"""v1.5 — detach_owner: unlink a passenger from a case.

Covers: stage gating (allowed ONLY at ready_owner/problem/closed and the
stage never moves), the ownerless error, the mid-robot guard (same check as
delete_device), the registration-profile handling mirrored from
delete_device (device link cleared, profile kept but disabled), the audit
event (detach_owner carrying device_id + owner_id), re-attach semantics
(the profile ends linked to the NEW device only), the freed-owner /
freed-case swap, and the concurrent races under STORE_LOCK.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crm      # noqa: E402
import panel    # noqa: E402


def synth_serial(n):
    """Unique synthetic 15-digit serial (pattern shared across modules)."""
    return ("35693803564" + str(n).zfill(4))[:15]


def synth_national(n):
    """Checksum-valid synthetic national code (mod-11 rule as crm.py)."""
    base = str(n).zfill(9)
    total = sum(int(d) * (10 - i) for i, d in enumerate(base)) % 11
    check = total if total < 2 else 11 - total
    return base + str(check)


class DetachHarness(unittest.TestCase):
    """Isolated CRM + estelam + profile stores (pattern: test_crm_api)."""

    # Walk paths: stage -> actions that legally reach it from `new`. Every
    # path through handover carries an attached owner (the handover gate).
    _PATHS = {
        "new": [],
        "inquiry": ["queue_inquiry"],
        "ready_owner": ["queue_inquiry", "mark_ready", "attach_owner"],
        "problem": ["queue_inquiry", "mark_ready", "attach_owner",
                    "handover", "mark_problem"],
        "handed": ["queue_inquiry", "mark_ready", "attach_owner", "handover"],
        "to_payment": ["queue_inquiry", "mark_ready", "attach_owner",
                       "handover", "to_payment"],
        "payment": ["queue_inquiry", "mark_ready", "attach_owner",
                    "handover", "to_payment", "start_payment"],
        "payment_done": ["queue_inquiry", "mark_ready", "attach_owner",
                         "handover", "to_payment", "start_payment",
                         "confirm_payment"],
        "done": ["queue_inquiry", "mark_ready", "attach_owner", "handover",
                 "to_payment", "start_payment", "confirm_payment",
                 "deliver_code"],
        "closed": ["queue_inquiry", "mark_ready", "attach_owner",
                   "handover", "mark_problem", "close_case"],
    }

    def setUp(self):
        import crm as crm_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm_mod.reset_cache()
        self.addCleanup(crm_mod.reset_cache)
        self._serial_seq = 0
        self._owner_seq = 0

    # -- fixtures ---------------------------------------------------------
    def _serial(self):
        self._serial_seq += 1
        return synth_serial(self._serial_seq)

    def _add_owner(self):
        self._owner_seq += 1
        owner, errors = crm.add_owner({
            "first_name": "مسافر",
            "last_name": "آزمون " + str(self._owner_seq),
            "national": synth_national(900000000 + self._owner_seq),
            "birth": "1375/04/29",
            "mobile": "09121234567",
            "card_no": "6219861012345678",
            "passport": "PP0000001",
        })
        self.assertEqual(errors, [], errors)
        return owner

    def _seed_green(self, device_id):
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device_id), "status": "healthy",
                 "hamta_status": "good"}]}, handle)

    def _walk_to(self, stage, with_owner=True):
        """Fresh device walked to `stage`; returns (device, owner)."""
        record, errors = crm.add_device({"name": "D", "serial1": self._serial()})
        self.assertEqual(errors, [], errors)
        self._seed_green(record["id"])
        owner = self._add_owner()
        actions = list(self._PATHS[stage])
        if not with_owner:
            actions = [a for a in actions if a != "attach_owner"]
        for action in actions:
            if action == "attach_owner":
                _device, errors = crm.advance(record["id"], "attach_owner",
                                              owner_id=owner["id"])
            elif action == "close_case":
                _device, errors = crm.advance(record["id"], "close_case",
                                              reason="انصراف آزمون")
            else:
                _device, errors = crm.advance(record["id"], action)
            self.assertEqual(errors, [], (stage, action, errors))
        row = self._device(record["id"])
        self.assertEqual(row["stage"], stage)
        return record, owner

    def _device(self, device_id):
        data = crm.store_read()
        return next(d for d in data["devices"] if d["id"] == device_id)

    def _log_events(self):
        path = crm.log_path()
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


class DetachStageGatingTests(DetachHarness):
    def test_detach_allowed_from_ready_owner_problem_closed(self):
        for stage in ("ready_owner", "problem", "closed"):
            with self.subTest(stage=stage):
                device, _owner = self._walk_to(stage)
                record, errors = crm.advance(device["id"], "detach_owner")
                self.assertEqual(errors, [], (stage, errors))
                self.assertEqual(record["stage"], stage)   # stage unchanged
                row = self._device(device["id"])
                self.assertEqual(row["stage"], stage)
                self.assertEqual(row["owner_id"], "")

    def test_detach_rejected_from_every_other_stage(self):
        for stage in ("new", "inquiry", "handed", "to_payment", "payment",
                      "payment_done", "done"):
            with self.subTest(stage=stage):
                device, _owner = self._walk_to(stage)
                before = self._device(device["id"])["owner_id"]
                _record, errors = crm.advance(device["id"], "detach_owner")
                self.assertTrue(errors, stage)
                self.assertTrue(
                    any("این کنش در مرحلهٔ فعلی مجاز نیست" in e
                        for e in errors), (stage, errors))
                row = self._device(device["id"])
                self.assertEqual(row["stage"], stage)
                self.assertEqual(row["owner_id"], before)   # link untouched

    def test_detach_without_owner(self):
        device, _owner = self._walk_to("ready_owner", with_owner=False)
        _record, errors = crm.advance(device["id"], "detach_owner")
        self.assertEqual(errors, ["این مسافر به پرونده‌ای متصل نیست"])
        self.assertEqual(self._device(device["id"])["owner_id"], "")

    def test_detach_mid_robot_rejected(self):
        device, owner = self._walk_to("ready_owner")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": device["id"], "status": "running",
                 "hamta_status": "good"}]}, handle)
        _record, errors = crm.advance(device["id"], "detach_owner")
        self.assertEqual(
            errors, ["دستگاه در حال اجرای ربات است — ابتدا آن را متوقف کنید"])
        row = self._device(device["id"])
        self.assertEqual(row["owner_id"], owner["id"])
        self.assertEqual(row["stage"], "ready_owner")

    def test_detach_stamps_updated(self):
        device, _owner = self._walk_to("ready_owner")
        data = crm.store_read()
        for row in data["devices"]:
            row["updated"] = "2000-01-01 00:00:00"
        crm.store_write(data)
        _record, errors = crm.advance(device["id"], "detach_owner")
        self.assertEqual(errors, [])
        row = self._device(device["id"])
        self.assertTrue(row["updated"])
        self.assertNotEqual(row["updated"], "2000-01-01 00:00:00")


class DetachProfileTests(DetachHarness):
    """Profile handling mirrors delete_device (F13): the site-1 profile
    survives but is disabled; the device link is cleared."""

    def test_detach_clears_link_and_disables_profile(self):
        device, _owner = self._walk_to("ready_owner")
        profile_id = panel.crm_ensure_registration_profile(device["id"])
        self.assertTrue(profile_id)
        self.assertEqual(self._device(device["id"])["profile_id"], profile_id)
        # enable it so the disable after detach is observable
        self.assertTrue(panel.set_profile_enabled(profile_id, True))
        profile = next(p for p in panel.load_store()["profiles"]
                       if p["id"] == profile_id)
        self.assertTrue(profile["enabled"])
        _record, errors = crm.advance(device["id"], "detach_owner")
        self.assertEqual(errors, [])
        row = self._device(device["id"])
        self.assertEqual(row["owner_id"], "")
        self.assertEqual(row["profile_id"], "")     # device link cleared
        profile = next(p for p in panel.load_store()["profiles"]
                       if p["id"] == profile_id)
        self.assertFalse(profile["enabled"])        # kept, but disabled
        # the estelam row survives (the device itself is not deleted)
        with open(self.estelam_store, encoding="utf-8") as handle:
            est = json.load(handle)
        self.assertTrue(any(str(d.get("id")) == device["id"]
                            for d in est.get("devices", [])))


class DetachLogTests(DetachHarness):
    def test_detach_logs_event_with_device_and_owner_ids(self):
        device, owner = self._walk_to("ready_owner")
        _record, errors = crm.advance(device["id"], "detach_owner")
        self.assertEqual(errors, [])
        events = [e for e in self._log_events()
                  if e.get("event") == "detach_owner"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("device_id"), device["id"])
        self.assertEqual(events[0].get("owner_id"), owner["id"])

    def test_rejected_detach_logs_no_event(self):
        device, _owner = self._walk_to("ready_owner", with_owner=False)
        _record, errors = crm.advance(device["id"], "detach_owner")
        self.assertTrue(errors)
        self.assertEqual([e for e in self._log_events()
                          if e.get("event") == "detach_owner"], [])


class DetachSwapTests(DetachHarness):
    def test_freed_owner_and_freed_case_both_move_on(self):
        device_a, owner1 = self._walk_to("ready_owner")
        device_b, _unused = self._walk_to("ready_owner", with_owner=False)
        _record, errors = crm.advance(device_a["id"], "detach_owner")
        self.assertEqual(errors, [])
        # the freed owner binds a NEW case
        _record, errors = crm.advance(device_b["id"], "attach_owner",
                                      owner_id=owner1["id"])
        self.assertEqual(errors, [], errors)
        # the freed case takes a DIFFERENT passenger
        owner2 = self._add_owner()
        _record, errors = crm.advance(device_a["id"], "attach_owner",
                                      owner_id=owner2["id"])
        self.assertEqual(errors, [], errors)
        self.assertEqual(self._device(device_b["id"])["owner_id"], owner1["id"])
        self.assertEqual(self._device(device_a["id"])["owner_id"], owner2["id"])
        # 1:1 holds across the whole store
        owners = [d["owner_id"] for d in crm.store_read()["devices"]]
        self.assertEqual(len(owners), len(set(owners)) + owners.count(""))


class DetachRaceTests(DetachHarness):
    def _run_threads(self, targets):
        barrier = threading.Barrier(len(targets))
        results = {}

        def wrap(key, fn):
            def run():
                barrier.wait()
                results[key] = fn()
            return run

        threads = [threading.Thread(target=wrap(key, fn))
                   for key, fn in targets.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads),
                         "race threads did not finish")
        return results

    def test_concurrent_detach_exactly_one_wins(self):
        device, _owner = self._walk_to("ready_owner")
        results = self._run_threads({
            "first": lambda: crm.advance(device["id"], "detach_owner"),
            "second": lambda: crm.advance(device["id"], "detach_owner"),
        })
        winners = [key for key, (_d, errors) in results.items()
                   if errors == []]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(self._device(device["id"])["owner_id"], "")
        # exactly one audit event for the single successful detach
        events = [e for e in self._log_events()
                  if e.get("event") == "detach_owner"]
        self.assertEqual(len(events), 1)

    def test_concurrent_detach_and_attach_keeps_one_to_one(self):
        device_a, owner = self._walk_to("ready_owner")
        device_b, _unused = self._walk_to("ready_owner", with_owner=False)
        results = self._run_threads({
            "detach": lambda: crm.advance(device_a["id"], "detach_owner"),
            "attach": lambda: crm.advance(device_b["id"], "attach_owner",
                                          owner_id=owner["id"]),
        })
        detach_errors = results["detach"][1]
        attach_errors = results["attach"][1]
        self.assertEqual(detach_errors, [], results)
        data = crm.store_read()
        bound = [d for d in data["devices"] if d["owner_id"] == owner["id"]]
        self.assertLessEqual(len(bound), 1)          # 1:1 never torn
        if attach_errors == []:
            # detach committed first: the owner moved to B, A is clear
            self.assertEqual(len(bound), 1)
            self.assertEqual(bound[0]["id"], device_b["id"])
            self.assertEqual(self._device(device_a["id"])["owner_id"], "")
        else:
            # attach lost while the owner was still bound to A; the detach
            # then freed them -> bound to nobody
            self.assertEqual(bound, [])


class DetachApiTests(DetachHarness):
    """Endpoint level: POST /crm-tick {action: detach_owner}."""

    def setUp(self):
        super().setUp()
        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)
        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    def _post(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def test_detach_via_crm_tick_route(self):
        device, owner = self._walk_to("ready_owner")
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "detach_owner"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        # response shape mirrors attach_owner: {"ok", "device"}
        self.assertEqual(body["device"]["owner_id"], "")
        self.assertEqual(body["device"]["stage"], "ready_owner")
        events = [e for e in self._log_events()
                  if e.get("event") == "detach_owner"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("device_id"), device["id"])
        self.assertEqual(events[0].get("owner_id"), owner["id"])

    def test_detach_route_error_shape(self):
        device, _owner = self._walk_to("ready_owner", with_owner=False)
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "detach_owner"})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"ok": False,
                                "errors": ["این مسافر به پرونده‌ای متصل نیست"]})

    def test_reattach_same_owner_to_other_device_profile_links_once(self):
        # Two cases, one passenger: A owns the passenger + the registration
        # profile; after detaching A, re-attaching to B via the route (which
        # auto-ensures the profile) must end with B linked to a profile that
        # NO OTHER device references — never a profile shared by two devices
        # (the required end-state). Note: ensure's owner-national dedupe
        # clause is dead code (misplaced parenthesis, panel.py ~1564), so
        # the serial dedupe is the only live path — B gets its own profile
        # carrying B's serial; A's disabled profile stays unreferenced.
        device_a, owner = self._walk_to("ready_owner")
        profile_id_a = panel.crm_ensure_registration_profile(device_a["id"])
        self.assertTrue(profile_id_a)
        device_b, _unused = self._walk_to("ready_owner", with_owner=False)

        status, body = self._post("/crm-tick", {
            "device_id": device_a["id"], "action": "detach_owner"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

        status, body = self._post("/crm-tick", {
            "device_id": device_b["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["device"]["owner_id"], owner["id"])

        data = crm.store_read()
        # the old case holds NO profile link anymore
        self.assertEqual(self._device(device_a["id"])["profile_id"], "")
        # the new case holds exactly one link, shared with nobody
        profile_id_b = self._device(device_b["id"])["profile_id"]
        self.assertTrue(profile_id_b)
        linked = [d for d in data["devices"]
                  if d.get("profile_id") == profile_id_b]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["id"], device_b["id"])
        # global invariant: no profile is referenced by two devices
        seen = [d.get("profile_id") for d in data["devices"]
                if d.get("profile_id")]
        self.assertEqual(len(seen), len(set(seen)))
        # A's profile survives, disabled (delete_device handling) and now
        # referenced by no device at all
        profile_a = next(p for p in panel.load_store()["profiles"]
                         if p["id"] == profile_id_a)
        self.assertFalse(profile_a["enabled"])
        self.assertNotEqual(profile_id_a, profile_id_b)


if __name__ == "__main__":
    unittest.main()
