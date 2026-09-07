"""Phase B — /crm-* HTTP API tests. A real ThreadingHTTPServer on an ephemeral
port serves panel.Handler with throwaway stores; requests go through urllib.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

import panel


class CrmApiHarness(unittest.TestCase):
    """Boots the real handler; each test gets isolated profile + CRM stores."""

    def setUp(self):
        import crm
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH", os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # -- helpers ---------------------------------------------------------
    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _post(self, path, payload):
        request = urllib.request.Request(
            self._url(path), data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def _get(self, path):
        try:
            with urllib.request.urlopen(self._url(path), timeout=10) as response:
                raw = response.read().decode("utf-8")
                ctype = response.headers.get("Content-Type", "")
                return response.status, (json.loads(raw) if "json" in ctype else raw), ctype
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), ""


class TestCrmRoutes(CrmApiHarness):
    def test_crm_page_served(self):
        status, body, ctype = self._get("/crm")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn('dir="rtl"', body)

    def test_crm_state_returns_empty_structure(self):
        status, body, _ = self._get("/crm-state")
        self.assertEqual(status, 200)
        for key in ("customers", "owners", "identities", "devices", "settings"):
            self.assertIn(key, body)

    def test_customer_crud_roundtrip(self):
        status, body = self._post("/crm-customer", {"action": "add", "full_name": "علی رضایی"})
        self.assertEqual(status, 200)
        customer_id = body["record"]["id"]
        status, body = self._post("/crm-customer", {
            "action": "update", "id": customer_id, "address": "تهران"})
        self.assertEqual(body["record"]["address"], "تهران")
        status, body = self._post("/crm-customer", {
            "action": "delete", "id": customer_id, "force": True})
        self.assertEqual(status, 200)

    def test_customer_validation_error_400(self):
        status, body = self._post("/crm-customer", {"action": "add", "full_name": " "})
        self.assertEqual(status, 400)
        self.assertTrue(body["errors"])

    def test_owner_add_validates_card(self):
        status, body = self._post("/crm-owner", {
            "action": "add", "first_name": "رضا", "last_name": "محمدی",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "card_no": "1234"})
        self.assertEqual(status, 400)
        self.assertTrue(any("کارت" in e for e in body["errors"]))

    def test_device_add_with_inline_customer(self):
        status, body = self._post("/crm-device", {
            "action": "add", "name": "iPhone 13", "serial1": "356938035643809",
            "customer": {"full_name": "مریم احمدی"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["device"]["customer_id"])

    def test_tick_transitions_and_rejects_invalid(self):
        _, body = self._post("/crm-device", {
            "action": "add", "name": "x", "serial1": "356938035643809"})
        device_id = body["device"]["id"]
        status, body = self._post("/crm-tick", {"device_id": device_id, "action": "confirm_payment"})
        self.assertEqual(status, 400)
        status, body = self._post("/crm-tick", {"device_id": device_id, "action": "queue_inquiry"})
        self.assertEqual(status, 200)
        self.assertEqual(body["device"]["stage"], "inquiry")

    def test_start_payment_requires_profile_id(self):
        """Review F18/F9: never pool-claim; no profile linked -> 400."""
        _, body = self._post("/crm-device", {
            "action": "add", "name": "x", "serial1": "356938035643809"})
        device_id = body["device"]["id"]
        # walk to to_payment
        for action in ("queue_inquiry", "mark_ready"):
            self._post("/crm-tick", {"device_id": device_id, "action": action})
        status, body = self._post("/crm-start-payment", {"device_id": device_id})
        self.assertEqual(status, 400)

    def test_settings_roundtrip(self):
        status, body = self._post("/crm-settings", {"watch_interval_hours": 5})
        self.assertEqual(status, 200)
        _, body, _ = self._get("/crm-state")
        self.assertEqual(body["settings"]["watch_interval_hours"], 5)

    def test_crm_logs_route(self):
        status, body, _ = self._get("/crm-logs")
        self.assertEqual(status, 200)
        self.assertIn("logs", body)


if __name__ == "__main__":
    unittest.main()
