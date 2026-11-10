# -*- coding: utf-8 -*-
"""Registration-profile write retries, safe failure recovery and serial dedupe.

Atomic profile writes retry bounded transient failures. Payment preparation
retries ensure three times, starts only with canonical CRM identity, and exposes
sanitized diagnostics without marking a failed attempt as paid. Profile adoption
matches device serials, never the passenger's national identity alone.
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

DEVICE_ERR = "دستگاه پیدا نشد"


def _customer(cid, name, txs=None):
    return {"id": cid, "full_name": name, "address": "", "mobile":
            "09121234567", "telegram_id": "", "instagram_id": "",
            "notes": "", "transactions": txs or [],
            "created": "2026-01-01 00:00:00",
            "updated": "2026-01-01 00:00:00"}


def _owner(oid, national="0012345679"):
    return {"id": oid, "first_name": "ن", "last_name": "م",
            "national": national, "birth": "1370/01/01",
            "mobile": "09120000000", "card_no": "", "passport": "PP0000001",
            "created": "2026-01-01 00:00:00", "updated": ""}


def _device(did, customer_id="c1", owner_id="o1", stage="to_payment",
            serial1="356938035640001", profile_id=""):
    return {"id": did, "name": "گوشی", "serial1": serial1, "serial2": "",
            "customer_id": customer_id, "owner_id": owner_id, "stage": stage,
            "auto_advance": False, "profile_id": profile_id,
            "handed_at": "2026-09-14 10:00:00", "registered_at": "",
            "payment_confirmed_at": "", "code_delivered_at": "",
            "done_at": "", "last_inquiry_at": "", "notes": "",
            "advance_attempts": 0, "created": "2026-09-12 09:30:00",
            "updated": "2026-09-15 09:00:00"}


class EnsureFixCase(unittest.TestCase):
    """Isolated stores + auth'd HTTP harness (pattern: test_v1_6_accounting)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        os.environ["IRICA_CRM_STORE"] = self.crm_store
        self.addCleanup(os.environ.pop, "IRICA_CRM_STORE", None)
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH",
                         os.path.join(self.tmp.name, "estelam.json")),
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
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        # Stop the accept loop before closing its socket on Windows.
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=5)
        self.assertFalse(self.server_thread.is_alive())

    def _seed(self, devices=None):
        crm.store_write({"version": 1,
                         "customers": [_customer("c1", "مشتری")],
                         "owners": [_owner("o1")],
                         "identities": [],
                         "devices": devices or [_device("d1")],
                         "settings": {}})

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _post(self, path, payload):
        request = urllib.request.Request(
            self._url(path), data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def _log_events(self):
        path = crm.log_path()
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


class SaveStoreRetryTests(EnsureFixCase):
    def test_transient_replace_failure_retries_to_success(self):
        store = {"profiles": [{"id": "p1", "title": "ت",
                               "national": "001", "imeis": ["35"]}],
                 "active_id": "p1"}
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(5, "Access is denied", dst)
            return real_replace(src, dst)

        with patch.object(panel.os, "replace", side_effect=flaky):
            panel.save_store(store)
        self.assertEqual(calls["n"], 3)
        saved = panel.load_store()
        self.assertEqual([p["id"] for p in saved["profiles"]], ["p1"])

    def test_persistent_failure_raises_after_three_attempts(self):
        store = {"profiles": [], "active_id": ""}
        with patch.object(panel.os, "replace",
                          side_effect=PermissionError(5, "locked")) as rp:
            with self.assertRaises(PermissionError):
                panel.save_store(store)
        self.assertEqual(rp.call_count, 3)


class PaymentHealTests(EnsureFixCase):
    def _start_payment(self):
        return self._post("/crm-start-payment", {"device_id": "d1"})

    def test_persistent_ensure_failure_is_sanitized_and_logs_payment_phase(self):
        self._seed()
        boom = RuntimeError("profiles.json ghabel-e neveshtan nist")
        with patch.object(panel, "crm_ensure_registration_profile", side_effect=boom) as ensure, \
                patch.object(panel, "start_profile_process") as spawn:
            status, body = self._start_payment()
        self.assertEqual(status, 503)
        self.assertTrue(body.get("errors"), body)
        self.assertEqual(body["errors"][0],
                         "Registration profile storage is unavailable; close conflicting operations and retry")
        self.assertTrue(body["errors"][0].isascii())
        self.assertEqual(ensure.call_count, 3)
        spawn.assert_not_called()
        events = [e for e in self._log_events()
                  if e.get("event") == "profile_ensure_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("event"), "profile_ensure_failed")
        self.assertEqual(events[0].get("phase"), "payment")
        self.assertEqual(events[0].get("error_type"), "RuntimeError")
        self.assertNotIn(str(boom), json.dumps({"response": body, "events": events}))
        device = next(d for d in crm.store_read()["devices"] if d["id"] == "d1")
        self.assertEqual(device["stage"], "to_payment")
        self.assertFalse(device.get("payment_confirmed_at"))

    def test_transient_ensure_failure_heals_within_retries(self):
        self._seed()
        real = panel.crm_ensure_registration_profile
        state = {"n": 0}

        def flaky(device_id):
            state["n"] += 1
            if state["n"] < 3:
                raise OSError("transient lock")
            return real(device_id)

        with patch.object(panel, "crm_ensure_registration_profile",
                          side_effect=flaky):
            with patch.object(panel, "start_profile_process",
                              return_value={"pid": 4321}) as spawn:
                status, body = self._start_payment()
        self.assertEqual(status, 200, body)
        self.assertEqual(state["n"], 3)
        spawn.assert_called_once()
        device = next(d for d in crm.store_read()["devices"]
                      if d["id"] == "d1")
        self.assertTrue(device["profile_id"])
        self.assertEqual(device["stage"], "payment")
        self.assertFalse(device.get("payment_confirmed_at"))
        self.assertEqual(spawn.call_args.args, (device["profile_id"],))
        self.assertTrue(spawn.call_args.kwargs["runner_id"])

    def test_no_passenger_still_gets_the_actionable_error(self):
        self._seed(devices=[_device("d1", owner_id="")])
        status, body = self._start_payment()
        self.assertEqual(status, 400)
        self.assertIn("attach", body["errors"][0].lower())
        self.assertIn("passenger", body["errors"][0].lower())
        self.assertEqual(panel.load_store()["profiles"], [])
        device = next(d for d in crm.store_read()["devices"] if d["id"] == "d1")
        self.assertEqual(device["stage"], "to_payment")
        self.assertFalse(device.get("payment_confirmed_at"))


class AttachHookDiagnosticsTests(EnsureFixCase):
    def test_attach_failure_notice_carries_reason_and_logs_detail(self):
        self._seed(devices=[dict(_device("d1", stage="ready_owner"),
                                 owner_id="")])
        boom = OSError("disk full")
        with patch.object(panel, "crm_ensure_registration_profile",
                          side_effect=boom):
            status, body = self._post("/crm-tick", {
                "device_id": "d1", "action": "attach_owner",
                "owner_id": "o1"})
        self.assertEqual(status, 200, body)      # binding still committed
        self.assertIn("profile_notice", body)
        self.assertIn("علت:", body["profile_notice"])
        self.assertIn("تلاش می‌شود", body["profile_notice"])
        events = [e for e in self._log_events()
                  if e.get("event") == "profile_ensure_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("phase"), "attach")
        self.assertIn("disk full", str(events[0].get("detail")))


class SerialOnlyDedupeTests(EnsureFixCase):
    def test_same_national_different_imei_creates_fresh_profile(self):
        # The dead national clause must stay dead: linking a profile whose
        # imeis belong to a different device would register the wrong IMEI.
        self._seed(devices=[dict(_device("d1", stage="ready_owner"),
                                 serial1="111111111111111", owner_id="")])
        panel.save_store({"profiles": [{
            "id": "pOld", "title": "قدیم", "national": "0012345679",
            "birth": "1370/01/01", "mobile": "09120000000",
            "imeis": ["999999999999999"]}], "active_id": "pOld"})
        status, body = self._post("/crm-tick", {
            "device_id": "d1", "action": "attach_owner",
            "owner_id": "o1"})
        self.assertEqual(status, 200, body)
        device = next(d for d in crm.store_read()["devices"]
                      if d["id"] == "d1")
        self.assertNotEqual(device["profile_id"], "pOld")
        profile = next(p for p in panel.load_store()["profiles"]
                       if p["id"] == device["profile_id"])
        self.assertIn("111111111111111", profile["imeis"])

    def test_same_serial_links_existing_profile(self):
        self._seed(devices=[dict(_device("d1", stage="ready_owner"),
                                 serial1="111111111111111", owner_id="")])
        panel.save_store({"profiles": [{
            "id": "pOld", "title": "قدیم", "national": "0012345679",
            "birth": "1370/01/01", "mobile": "09120000000",
            "imeis": ["111111111111111"]}], "active_id": "pOld"})
        status, body = self._post("/crm-tick", {
            "device_id": "d1", "action": "attach_owner",
            "owner_id": "o1"})
        self.assertEqual(status, 200, body)
        device = next(d for d in crm.store_read()["devices"]
                      if d["id"] == "d1")
        self.assertEqual(device["profile_id"], "pOld")
        self.assertEqual(len(panel.load_store()["profiles"]), 1)


if __name__ == "__main__":
    unittest.main()
