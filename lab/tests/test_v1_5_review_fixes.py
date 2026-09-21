# -*- coding: utf-8 -*-
"""v1.5 review round fixes (consolidated gate-H list + manager items 10-11).

- item 1 (priority): a rollback to `handed` must restart auto-advance
  attempts from zero — the scan used the stale pre-rollback counter and
  logged auto_advance_gave_up on the very first post-rollback move.
- item 2: site-2 'advanced' is the SUCCESS signal under pool semantics;
  status/badge text and tone no longer read as a warning/danger.
- item 3: the periodic tab no longer implies scheduled runs for
  payment/payment_done (manual-only badge + an explicit header note).
- item 4: per-device in-flight guard in estelam_start — an inquiry reset
  followed by the scheduler or a manual click can no longer double-start
  the SAME device; other devices still start in parallel.
- items 6+7: copy sweep — no CRM copy may claim watch inquiries run under
  the passenger's identity or that only intake inquiries need the pool.
- item 8: crm_start_inquiry docstring states the pool-only reality.
- item 9: the last_inquiry_at read-modify-write is wrapped in STORE_LOCK.
- item 10: /save never drops the response on a cp1252 console print of a
  Persian profile title.
- item 11: a stale device.profile_id (profile deleted from the store)
  self-heals instead of surfacing an English 404 from start_profile.
"""
import inspect
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import crm
import panel

OWNER_FIELDS = {
    "first_name": "رضا", "last_name": "محمدی",
    "national": "0012345679", "birth": "1375/04/29",
    "mobile": "09121234567", "card_no": "6219861012345678",
    "passport": "PP0000001",
}
SERIAL1 = "356938035643809"
SERIAL2 = "356938035643810"


def panel_source():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def fn_body_js(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
    import re
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[m.start():i]


# ---------------------------------------------------------------------------
# Direct-call harness (test_crm_scheduler pattern)
# ---------------------------------------------------------------------------
class ReviewFixUnitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estelam = os.path.join(self.tmp.name, "estelam_store.json")
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self._patches = [
            patch.object(panel, "ESTELAM_PATH", self.estelam),
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    def _device_at_handed(self, auto_advance=True):
        """Walk the real gates to `handed` (owner attached, profile linked)."""
        device, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": SERIAL1,
                                       "auto_advance": auto_advance})
        assert errs == [], errs
        owner, errs = crm.add_owner(dict(OWNER_FIELDS))
        assert errs == [], errs
        crm.advance(device["id"], "queue_inquiry")
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device["id"]), "status": "healthy",
                 "hamta_status": "good"}]}, handle)
        crm.advance(device["id"], "mark_ready")
        crm.advance(device["id"], "attach_owner", owner_id=owner["id"])
        device, errs = crm.advance(device["id"], "handover")
        assert errs == [], errs
        return device

    def _stored_device(self, device_id):
        data = crm.store_read()
        return next(d for d in data["devices"] if d["id"] == device_id)

    def _patch_device(self, device_id, **fields):
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device_id)
        dev.update(fields)
        crm.store_write(data)
        return dev

    def _seed_advanced_row(self, device):
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device["id"]), "name": device["name"],
                 "serial1": device["serial1"], "status": "advanced",
                 "result": "شناسه در گمرک ورودی ثبت است"}]}, handle)


class TestAutoAdvanceRollback(ReviewFixUnitCase):
    """Item 1 — the money test: 6 failed attempts, rollback to handed, then
    an 'advanced' scan must START payment, not give up on the stale counter."""

    def test_rollback_to_handed_restarts_attempts_and_pays(self):
        device = self._device_at_handed(auto_advance=True)
        # The traced shape: six failed start_profile attempts, then the
        # operator rolls the case BACK to handed (stage_back keeps
        # advance_attempts — only the stage moves).
        self._patch_device(device["id"], stage="handed", advance_attempts=6)
        data = crm.store_read()
        data["settings"]["scheduler_enabled"] = True
        crm.store_write(data)
        self._seed_advanced_row(device)
        log_file = os.path.join(self.tmp.name, "crm_events.jsonl")
        with patch.object(panel, "estelam_is_running", return_value=False), \
                patch.object(panel, "start_profile",
                             return_value=(200, {"pid": 4321})), \
                patch.object(crm, "log_path", return_value=log_file):
            result = panel.crm_auto_advance_scan()
        self.assertIn(device["id"], result["moved"])
        self.assertIn(device["id"], result["started"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(self._stored_device(device["id"])["stage"], "payment")
        # No give-up may be logged: the stale pre-rollback counter is dead.
        with open(log_file, encoding="utf-8") as handle:
            events = [json.loads(line)["event"] for line in handle
                      if line.strip()]
        self.assertNotIn("auto_advance_gave_up", events)
        self.assertIn("auto_payment_started", events)


# ---------------------------------------------------------------------------
# Item 4 — per-device in-flight guard (unit level, estelam store only)
# ---------------------------------------------------------------------------
class PerDeviceGuardUnitCase(unittest.TestCase):
    """The registry entry's pid is the LIVE test process: estelam_live_runs
    really reports it, so the guard is exercised against the true helper."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estelam = os.path.join(self.tmp.name, "estelam_store.json")
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({
                "person": {"national": "0012345679", "birth": "1375/04/29",
                           "mobile": "09121234567"},
                "devices": [
                    {"id": "dev-x", "name": "X", "serial1": "1" * 15,
                     "status": "pending"},
                    {"id": "dev-y", "name": "Y", "serial1": "2" * 15,
                     "status": "pending"},
                ],
                "runs": [{"pid": os.getpid(), "device_id": "dev-x",
                          "service": "gomrok", "started": ""}],
            }, handle)
        patcher = patch.object(panel, "ESTELAM_PATH", self.estelam)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_live_run_refuses_same_device_without_spawning(self):
        with patch.object(panel, "start_estelam_process") as spawn:
            result = panel.estelam_start(device_id="dev-x", service="gomrok")
        self.assertFalse(result["ok"])
        self.assertTrue(any("در حال اجراست" in e
                            for e in result["errors"]), result)
        spawn.assert_not_called()

    def test_different_device_still_starts(self):
        """Per-device only: a second device starts fine under the cap."""
        with patch.object(panel, "start_estelam_process",
                          return_value={"pid": 555}) as spawn:
            result = panel.estelam_start(device_id="dev-y", service="gomrok")
        self.assertTrue(result["ok"], result)
        spawn.assert_called_once()


# ---------------------------------------------------------------------------
# HTTP harness (test_crm_api pattern: real server + auth cookie)
# ---------------------------------------------------------------------------
class ReviewFixHttpCase(unittest.TestCase):
    def setUp(self):
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
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # -- helpers ---------------------------------------------------------
    def _post(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def _add_device(self, **extra):
        record, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": SERIAL1,
                                       "serial2": SERIAL2, **extra})
        assert errs == [], errs
        return record

    def _add_owner(self, **override):
        record, errs = crm.add_owner({**OWNER_FIELDS, **override})
        assert errs == [], errs
        return record

    def _patch_device(self, device_id, **fields):
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device_id)
        dev.update(fields)
        crm.store_write(data)
        return dev

    def _stored_device(self, device_id):
        data = crm.store_read()
        return next(d for d in data["devices"] if d["id"] == device_id)

    def _read_profiles(self):
        with open(self.profiles, encoding="utf-8") as handle:
            return json.load(handle)["profiles"]

    def _write_profiles(self, profiles):
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": profiles}, handle)

    def _seed_estelam(self, devices, runs=()):
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": list(devices),
                       "runs": list(runs)}, handle)

    def _pool(self):
        record, errs = crm.add_identity({
            "label": "pool", "national": "0012345679",
            "birth": "1375/04/29", "mobile": "09121234567"})
        assert errs == [], errs
        return record


class TestPerDeviceGuardHttp(ReviewFixHttpCase):
    """Item 4 at the HTTP level: /crm-inquire refuses the in-flight device
    with a Persian error, a different device still starts."""

    def test_inflight_device_refused_other_device_starts(self):
        device_x = self._add_device()
        device_y = self._add_device(name="iPhone 14",
                                    serial1="356938035643819",
                                    serial2="356938035643820")
        self._patch_device(device_x["id"], stage="handed")
        self._patch_device(device_y["id"], stage="handed")
        self._pool()
        live = [{"pid": os.getpid(), "device_id": str(device_x["id"]),
                 "service": "gomrok", "started": ""}]
        self._seed_estelam([
            {"id": str(device_x["id"]), "name": "X", "serial1": SERIAL1,
             "status": "pending"},
            {"id": str(device_y["id"]), "name": "Y",
             "serial1": "356938035643819", "status": "pending"},
        ], runs=live)
        with patch.object(panel, "start_estelam_process",
                          return_value={"pid": 4242}) as spawn:
            status_x, body_x = self._post("/crm-inquire", {
                "device_id": device_x["id"], "service": "gomrok"})
            status_y, body_y = self._post("/crm-inquire", {
                "device_id": device_y["id"], "service": "gomrok"})
        self.assertEqual(status_x, 400, body_x)
        self.assertFalse(body_x["ok"])
        self.assertTrue(any("در حال اجراست" in e
                            for e in body_x["errors"]), body_x)
        self.assertEqual(status_y, 200, body_y)
        self.assertTrue(body_y["ok"], body_y)
        self.assertIn("pid", body_y)
        spawn.assert_called_once()          # only the different device ran
        # the locked RMW still ran for the successful start (item 9)
        self.assertTrue(self._stored_device(device_y["id"])
                        .get("last_inquiry_at"))


class TestStaleLinkSelfHeal(ReviewFixHttpCase):
    """Item 11 — stale device.profile_id must self-heal, never 404 English."""

    def _stale_link(self, device_id):
        self._write_profiles([{
            "id": "p-stale", "title": "deleted later",
            "national": OWNER_FIELDS["national"], "birth": "1375/04/29",
            "mobile": "09121234567", "imeis": [SERIAL1], "enabled": True}])
        self._patch_device(device_id, stage="to_payment",
                           profile_id="p-stale")
        # the linked profile disappears from the store (operator delete);
        # the device keeps pointing at the dead id
        self._write_profiles([])

    def test_stale_link_with_owner_self_heals_200(self):
        device = self._add_device()
        owner = self._add_owner()
        self._patch_device(device["id"], owner_id=owner["id"])
        self._stale_link(device["id"])
        with patch.object(panel, "start_profile_process",
                          return_value={"pid": 4321, "profile_id": "x"}):
            status, body = self._post("/crm-start-payment",
                                      {"device_id": device["id"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        profiles = self._read_profiles()
        self.assertEqual(len(profiles), 1)          # re-created by ensure
        stored = self._stored_device(device["id"])
        self.assertEqual(stored["stage"], "payment")
        self.assertEqual(stored["profile_id"], profiles[0]["id"])
        self.assertNotEqual(stored["profile_id"], "p-stale")
        self.assertEqual(body["pid"], 4321)

    def test_stale_link_without_owner_actionable_400(self):
        device = self._add_device()
        self._stale_link(device["id"])
        with patch.object(panel, "start_profile_process") as spawn:
            status, body = self._post("/crm-start-payment",
                                      {"device_id": device["id"]})
        self.assertEqual(status, 400, body)
        errors = body.get("errors") or []
        self.assertTrue(errors, body)
        self.assertIn("اتصال مسافر", errors[0])     # actionable Persian
        self.assertNotIn("profile not found", json.dumps(body, ensure_ascii=False))
        spawn.assert_not_called()                   # never tried the dead id


class _Cp1252LikeStdout:
    """Simulates a cp1252 console: any non-ASCII write raises
    UnicodeEncodeError — exactly what printing a Persian title there does."""

    def __init__(self, real):
        self._real = real

    def write(self, text):
        text = str(text)
        if any(ord(ch) > 127 for ch in text):
            raise UnicodeEncodeError("cp1252", text, 0, 1,
                                     "illegal multibyte sequence")
        return self._real.write(text)

    def flush(self):
        self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestSavePersianTitle(ReviewFixHttpCase):
    """Item 10 — a Persian title on a cp1252 console must not drop the
    response: the route's print is guarded, the client still gets the store."""

    def test_persian_title_survives_cp1252_console(self):
        payload = {"title": "پروفایل تست فارسی",
                   "national": "0012345679", "birth": "1375/04/29",
                   "mobile": "09121234567", "first_name": "Reza",
                   "last_name": "Mohammadi", "father_name": "Mohsen",
                   "birth_certificate_no": "1234", "imeis": [SERIAL1]}
        with patch.object(sys, "stdout", _Cp1252LikeStdout(sys.stdout)):
            status, body = self._post("/save", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["active_id"],
                         self._read_profiles()[0]["id"])
        self.assertEqual(self._read_profiles()[0]["title"],
                         "پروفایل تست فارسی")


# ---------------------------------------------------------------------------
# Source markers
# ---------------------------------------------------------------------------
class TestSourceMarkers(unittest.TestCase):

    def _src(self):
        return panel_source()

    def _fn_chunk(self, src, name):
        start = src.index("def " + name + "(")
        return src[start:src.index("\ndef ", start + 10)]

    def _method_chunk(self, src, name):
        start = src.index("def " + name + "(")
        return src[start:src.index("\n    def ", start + 10)]

    # -- item 1: the local counter resets inside the is_new branch --------
    def test_attempts_reset_inside_is_new_branch(self):
        src = self._src()
        scan = self._fn_chunk(src, "crm_auto_advance_scan")
        branch = scan[scan.index("if is_new:"):scan.index("elif not is_retry:")]
        self.assertIn("attempts = 0", branch)

    # -- item 9: the last_inquiry_at RMW runs under STORE_LOCK ------------
    def test_last_inquiry_at_mutation_is_lock_guarded(self):
        chunk = self._fn_chunk(self._src(), "crm_start_inquiry")
        self.assertRegex(
            chunk, r"(?s)with crm_mod\.STORE_LOCK:.{0,400}last_inquiry_at")

    # -- item 8: pool-only docstring ---------------------------------------
    def test_crm_start_inquiry_docstring_is_pool_only(self):
        doc = inspect.getdoc(panel.crm_start_inquiry)
        self.assertIn("pool", doc.lower())
        self.assertIn("intake", doc.lower())
        self.assertIn("watch", doc.lower())
        self.assertNotIn("OWNER for the watch stages", doc)

    # -- item 2: site-2 advanced reads as success ---------------------------
    def test_advanced_labels_are_success_tone(self):
        for text in (panel.ESTELAM_STATUS_FA["advanced"],
                     panel.ESTELAM_BADGE_FA["advanced"]):
            self.assertNotIn("مسدود", text)
            self.assertNotIn("هشدار", text)
            self.assertIn("ثبت‌شده", text)
        src = self._src()
        # the site-2 badge + runtime chip paint the success tone
        self.assertIn(".b-advanced{background:var(--pill-ok-bg);"
                      "color:var(--pill-ok-fg)}", src)
        self.assertIn(".chip.advanced{background:var(--pill-ok-bg);"
                      "color:var(--pill-ok-fg)}", src)

    # -- item 3: manual-only badge + header note + dropped done claim ------
    def test_periodic_tab_manual_only_badge_and_note(self):
        body = fn_body_js(panel.CRM_PAGE, "periodicHtml")
        self.assertIn("فقط دستی", body)
        # the due countdown is suppressed exactly for payment/payment_done
        self.assertIn("d.stage==='payment'||d.stage==='payment_done'", body)
        self.assertIn("?'<span class=\"pill n\">فقط دستی</span>':dueIn(d,hours)",
                      body)
        page = panel.CRM_PAGE
        self.assertIn("استعلام خودکار فقط برای مراحل «تحویل به مسئول ثبت» "
                      "و «استعلام ثبت‌بودن» اجرا می‌شود", page)
        # STAGE_GUIDE['done'] no longer claims periodic inquiries continue
        self.assertNotIn("استعلام دوره‌ای برای اطمینان ادامه دارد", page)

    # -- item 4 (UI belt-and-braces): reset button disabled while running --
    def test_periodic_reset_button_disable_pattern(self):
        body = fn_body_js(panel.CRM_PAGE, "periodicHtml")
        start = body.index("periodicResetOne(\\'")
        seg = body[start:body.index("+ICONS.rewind+'</button>", start)]
        self.assertIn("STATE.estelam_running?'disabled':''", seg)

    # -- items 6+7: corrected copy present, old claims gone ----------------
    def test_identity_pool_copy_matches_pool_semantics(self):
        page = panel.CRM_PAGE
        # corrected strings
        self.assertIn("برای هر استعلام — ورودی و نظارتی — یک مشخصات تصادفی "
                      "معتبر از همین استخر انتخاب می‌شود", page)
        self.assertIn("هیچ استعلامی — ورودی و نظارتی — اجرا نمی‌شود", page)
        self.assertIn("مشخصات مسافر برای ثبت پرونده، تحویل و ثبت‌نام لازم است",
                      page)
        self.assertIn("مشخصات مسافر برای ثبت پرونده و تحویل لازم است، "
                      "نه برای هویت استعلام", page)
        # old, contradictory strings
        self.assertNotIn("با هویت مسافر اجرا می‌شود", page)
        self.assertNotIn("استعلام‌های نظارتی اجرا نمی‌شوند", page)
        self.assertNotIn("استعلام ورودی اجرا نمی‌شود", page)
        self.assertNotIn("استعلام نظارتی و ثبت‌نام لازم است", page)

    # -- item 10: the /save print can never break the response -------------
    def test_save_route_print_is_encoding_safe(self):
        src = self._src()
        start = src.index('self.path == "/save"')
        chunk = src[start:src.index('self.path == "/activate"')]
        self.assertIn("print(", chunk)                     # kept as diagnostic
        self.assertIn("except UnicodeEncodeError", chunk)  # but guarded

    # -- item 11: the self-heal also covers a stale (missing) link ---------
    def test_start_payment_heal_covers_stale_link(self):
        src = self._src()
        chunk = self._method_chunk(src, "_crm_start_payment")
        self.assertIn("crm_ensure_registration_profile(device_id)", chunk)
        # the heal triggers when the linked profile is gone from the store
        self.assertIn('if not next((p for p in store.get("profiles", [])',
                      chunk)
        self.assertIn('profile_id = ""', chunk)


if __name__ == "__main__":
    unittest.main()
