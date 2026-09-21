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

        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # -- helpers ---------------------------------------------------------
    def _open_period(self):
        """v1.8.1: a case may not exist outside a reporting period, so every
        test that creates one opens the period first — the same first step
        the operator takes."""
        import crm
        period, errors = crm.open_period("")
        self.assertEqual(errors, [], errors)
        return period

    def _seed_green_estelam(self, device_id):
        """Seed a both-OK estelam row so the intake inquiry gate (v1.1.2)
        lets fixtures walk past `mark_ready`."""
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device_id), "status": "healthy",
                 "hamta_status": "good"}]}, handle)

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
        period = self._open_period()
        status, body = self._post("/crm-device", {
            "action": "add", "name": "iPhone 13", "serial1": "356938035643809",
            "customer": {"full_name": "مریم احمدی"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["device"]["customer_id"])
        # the new case joined the open period (the v1.7 registration rule)
        self.assertEqual(body["device"]["period_id"], period["id"])

    def test_device_add_requires_an_open_period(self):
        """v1.8.1 operator rule: no case outside a reporting period. The page
        disables «پروندهٔ جدید»; the route refuses a stale tab or a direct
        call, and a rejected intake must not leave the inline customer
        behind."""
        status, body = self._post("/crm-device", {
            "action": "add", "name": "iPhone 13", "serial1": "356938035643809",
            "customer": {"full_name": "مریم احمدی"}})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertTrue(any("دوره" in e for e in body["errors"]), body)
        _, state, _ = self._get("/crm-state")
        self.assertEqual(state["devices"], [])
        self.assertEqual(state["customers"], [])
        # opening a period makes the same intake succeed
        self._open_period()
        status, body = self._post("/crm-device", {
            "action": "add", "name": "iPhone 13", "serial1": "356938035643809"})
        self.assertEqual(status, 200)

    def test_tick_transitions_and_rejects_invalid(self):
        self._open_period()
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
        self._open_period()
        _, body = self._post("/crm-device", {
            "action": "add", "name": "x", "serial1": "356938035643809"})
        device_id = body["device"]["id"]
        # walk to to_payment
        for action in ("queue_inquiry", "mark_ready"):
            self._seed_green_estelam(device_id)
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


class TestCrmPageUi(CrmApiHarness):
    """Phase E: the full CRM page serves all six tabs and wires the actions."""

    def test_page_contains_six_tabs_and_key_markers(self):
        status, body, _ = self._get("/crm")
        self.assertEqual(status, 200)
        for marker in (
                "دستگاه‌ها", "مشتریان", "اطلاعات مسافر", "استخر هویت",
                "تراکنش‌ها", "گزارش‌ها",
                "/crm-tick", "/crm-start-payment", "/crm-state",
                "irica-theme", "پرداخت خودکار", "crm-report-telegram"):
            self.assertIn(marker, body)

    def test_transaction_action_roundtrip(self):
        _, body = self._post("/crm-customer", {"action": "add", "full_name": "مریم"})
        cid = body["record"]["id"]
        status, body = self._post("/crm-customer", {
            "action": "tx", "id": cid, "amount": 1500000, "note": "پیش‌پرداخت"})
        self.assertEqual(status, 200)
        status, body = self._post("/crm-customer", {
            "action": "tx", "id": cid, "amount": "-500000", "note": "بدهی"})
        self.assertEqual(status, 200)
        _, state, _ = self._get("/crm-state")
        cust = next(c for c in state["customers"] if c["id"] == cid)
        self.assertEqual(len(cust["transactions"]), 2)
        self.assertEqual(sum(t["amount"] for t in cust["transactions"]), 1000000)


class TestCrmReports(CrmApiHarness):
    """Phase F: PDF via headless Edge with HTML fallback + telegram gate."""

    def _seed_device(self):
        import crm
        rec, _ = crm.add_device({"name": "iPhone 13", "serial1": "356938035643809"})
        return rec

    def test_pdf_route_serves_pdf_or_html_fallback(self):
        self._seed_device()
        try:
            with urllib.request.urlopen(self._url("/crm-report?fmt=pdf"), timeout=90) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            raw, ctype = error.read(), error.headers.get("Content-Type", "")
        if "pdf" in ctype:
            self.assertGreater(len(raw), 500)
            self.assertEqual(raw[:4], b"%PDF")
        else:
            # Edge missing -> HTML fallback with the print note
            self.assertIn("html", ctype)
            self.assertIn("Print", raw.decode("utf-8"))

    def test_telegram_honors_full_data_setting(self):
        self._seed_device()
        import crm as crm_mod
        sent = []
        fake = type("tg", (), {"send_message": staticmethod(
            lambda text, timeout=20, actor=None: sent.append(text) or True)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-report-telegram", {"full_data": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["chunks"], len(sent))
        self.assertTrue(sent)

    def test_telegram_never_contains_card_number(self):
        """Plan section 6: logs and telegram NEVER contain card numbers."""
        import crm as crm_mod
        device, _ = crm_mod.add_device({"name": "x", "serial1": "356938035643809"})
        crm_mod.add_owner({"first_name": "رضا", "last_name": "محمدی",
                           "national": "0012345679", "birth": "1375/04/29",
                           "mobile": "09121234567", "card_no": "6219861012345678",
                           "passport": "PP0000001"})
        crm_mod.advance(device["id"], "queue_inquiry")
        self._seed_green_estelam(device["id"])
        crm_mod.advance(device["id"], "mark_ready")
        crm_mod.advance(device["id"], "attach_owner",
                        owner_id=crm_mod.store_read()["owners"][0]["id"])
        state = panel.crm_state_payload()
        text = crm_mod.build_report(state, fmt="txt", plain=True, full_data=True)
        self.assertNotIn("6219861012345678", text)
        self.assertIn("001***5679", text)   # first3 + *** + last4 (F16)
        # explicit artifacts keep the full identity
        full = crm_mod.build_report(state, fmt="txt", plain=False, full_data=True)
        self.assertIn("6219861012345678", full)


class TestCrmEntryPoints(CrmApiHarness):
    """The operator must find /crm from the home picker and both site pages."""

    def _get_text(self, path):
        with urllib.request.urlopen(self._url(path), timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")

    def test_home_picker_links_to_crm(self):
        import re
        body = self._get_text("/")
        self.assertIn('href="/crm"', body)
        # CRM card is first and carries the unified-registry name
        # (anchor the full card tag: "data-site" also appears in CSS rules)
        self.assertIn("پنل یکپارچه رجیستری", body)
        crm_card = re.search(r'<a class="site" data-site="crm"', body)
        site2_card = re.search(r'<a class="site" data-site="2"', body)
        self.assertIsNotNone(crm_card)
        self.assertIsNotNone(site2_card)
        self.assertLess(crm_card.start(), site2_card.start())

    def test_site1_page_links_to_crm(self):
        body = self._get_text("/site-1")
        self.assertIn('href="/crm"', body)

    def test_site2_page_links_to_crm(self):
        body = self._get_text("/site-2")
        self.assertIn('href="/crm"', body)
