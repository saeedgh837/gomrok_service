"""v1.5 (B2+B3) — device-edit modal, owner passport/card form, attach-bound
owners, and an editable completed tab with a guarded done -> payment_done
rollback.

Two layers:
- Source markers lock the CRM_PAGE JS contract (edit modal ids, locked
  serial1, update payload without serial1, passport field + legacy hint,
  optional card, attach disabled options, doneRollback confirmation).
- HTTP tests exercise the real handler end to end: the handover walk, the
  stage_back round-trip from `done` (done_at cleared), and the device update
  contract incl. the immutable-serial1 rejection.
"""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import crm  # noqa: E402
import panel  # noqa: E402


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE (same trick as v1.1.5)."""
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body_js(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
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


SERIAL1_LOCK_TITLE = ("سریال اول قابل ویرایش نیست — برای تغییر، پرونده را حذف "
                      "و دوباره بسازید")
PASSPORT_HINT = "برای ارسال اطلاعات ثبت، شمارهٔ گذرنامه لازم است"


class FormSourceMarkerTests(unittest.TestCase):
    """CRM_PAGE source contract for the v1.5 forms and the done tab."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_dlg_device_edit_modal_with_locked_serial1(self):
        self.assertIn("function dlgDeviceEdit(id){", self.page)
        body = fn_body_js(self.js, "dlgDeviceEdit")
        self.assertTrue(body, "dlgDeviceEdit body not found")
        self.assertIn("disabled", body)
        self.assertIn(SERIAL1_LOCK_TITLE, body)
        for field_id in ("dvEName", "dvES2", "dvECust", "dvEErr"):
            self.assertIn('id="%s"' % field_id, body)

    def test_do_save_device_edit_payload_excludes_serial1(self):
        body = fn_body_js(self.js, "doSaveDeviceEdit")
        self.assertTrue(body, "doSaveDeviceEdit body not found")
        self.assertIn("action:'update'", body)
        self.assertIn("api('/crm-device'", body)
        self.assertNotIn("serial1", body,
                         "the update payload must never carry serial1")

    def test_dlg_device_add_keeps_editable_serial1(self):
        body = fn_body_js(self.js, "dlgDevice")
        self.assertTrue(body, "dlgDevice body not found")
        self.assertIn('id="dvS1"', body)
        self.assertNotIn("disabled", body,
                         "add-modal serial1 must stay editable (creation)")

    def test_owner_passport_required_card_optional(self):
        self.assertIn("گذرنامه *", self.page)
        self.assertIn('id="oPassport"', self.page)
        self.assertIn("شماره کارت ۱۶ رقمی", self.page)
        self.assertNotIn("شماره کارت ۱۶ رقمی *", self.page)
        self.assertIn(PASSPORT_HINT, self.page)
        owner = fn_body_js(self.js, "dlgOwner")
        self.assertTrue(owner, "dlgOwner body not found")
        # passport field renders before the card field
        self.assertLess(owner.find('id="oPassport"'), owner.find('id="oCard"'))

    def test_do_save_owner_sends_passport_and_blocks_empty(self):
        body = fn_body_js(self.js, "doSaveOwner")
        self.assertTrue(body, "doSaveOwner body not found")
        self.assertIn("passport:v('oPassport')", body)
        self.assertIn("card_no:v('oCard')", body)
        self.assertIn("oErr", body)
        self.assertIn("گذرنامه", body)   # client-side empty-passport error

    def test_dlg_attach_disables_bound_owners(self):
        body = fn_body_js(self.js, "dlgAttach")
        self.assertTrue(body, "dlgAttach body not found")
        self.assertIn("disabled", body)
        self.assertIn("متصل", body)
        self.assertIn("owner_id", body)

    def test_done_tab_rows_edit_and_rollback_buttons(self):
        self.assertIn('id="devrow-', self.page)
        body = fn_body_js(self.js, "doneTabHtml")
        self.assertTrue(body, "doneTabHtml body not found")
        self.assertIn("dlgDeviceEdit(", body)
        self.assertIn("doneRollback(", body)
        self.assertIn(">ویرایش</button>", body)

    def test_done_rollback_strong_confirm_and_stage_back(self):
        self.assertIn("function doneRollback(id){", self.page)
        body = fn_body_js(self.js, "doneRollback")
        self.assertTrue(body, "doneRollback body not found")
        self.assertIn("confirmDlg(", body)
        self.assertIn("پرداخت تأییدشده", body)   # names the target stage
        self.assertIn("action:'stage_back'", body)

    def test_stage_back_fa_has_no_done_entry(self):
        """The active-case __stage_back flow must stay unchanged: the done
        tab uses its own doneRollback surface (spec B3)."""
        match = re.search(r"var STAGE_BACK_FA = \{([\s\S]*?)\};", self.js)
        self.assertTrue(match, "STAGE_BACK_FA not found")
        self.assertNotIn("'done'", match.group(1))


class V15HttpHarness(unittest.TestCase):
    """Same boot pattern as test_crm_api.CrmApiHarness: real handler on an
    ephemeral port, throwaway stores, auth cookie installed."""

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

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # -- helpers ---------------------------------------------------------
    def _seed_green_estelam(self, device_id):
        """Seed a both-OK estelam row for the intake inquiry gate."""
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device_id), "status": "healthy",
                 "hamta_status": "good"}]}, handle)

    def _seed_store(self, devices, owners):
        """Write the CRM store directly: crm.add_owner does not persist
        passport yet (Domain A pending), so passport-carrying owner fixtures
        are seeded as synthetic store records."""
        with open(self.crm_store, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "customers": [], "owners": owners,
                       "identities": [], "devices": devices, "settings": {}},
                      handle)

    @staticmethod
    def _device(**over):
        fields = {"id": "dev1", "name": "iPhone 13",
                  "serial1": "356938035643809", "serial2": "",
                  "customer_id": "", "owner_id": "", "stage": "new",
                  "auto_advance": False, "profile_id": "",
                  "handed_at": "", "registered_at": "",
                  "payment_confirmed_at": "", "code_delivered_at": "",
                  "done_at": "", "last_inquiry_at": "", "notes": "",
                  "advance_attempts": 0,
                  "created": "2026-01-01 00:00:00",
                  "updated": "2026-01-01 00:00:00"}
        fields.update(over)
        return fields

    @staticmethod
    def _owner(**over):
        fields = {"id": "own1", "first_name": "سارا", "last_name": "کریمی",
                  "national": "0023456789", "birth": "1370/01/15",
                  "mobile": "09121112233", "card_no": "",
                  "passport": "A12345678",
                  "created": "2026-01-01 00:00:00"}
        fields.update(over)
        return fields

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


class HandoverAndRollbackTests(V15HttpHarness):
    def test_handover_walk_ready_owner_to_handed(self):
        """Proves the walk used by later tests: a ready_owner device with a
        bound (synthetic, passport-carrying) owner ticks to handed."""
        self._seed_store([self._device(stage="ready_owner", owner_id="own1")],
                         [self._owner()])
        self._seed_green_estelam("dev1")
        status, body = self._post("/crm-tick",
                                  {"device_id": "dev1", "action": "handover"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["device"]["stage"], "handed")

    def test_stage_back_from_done_returns_payment_done(self):
        """done -> payment_done via /crm-tick stage_back: done_at and the
        delivery timestamp are cleared; payment_confirmed_at survives."""
        device = self._device(stage="done", done_at="2026-01-02 10:00:00",
                              code_delivered_at="2026-01-02 10:00:00",
                              payment_confirmed_at="2026-01-02 09:00:00")
        self._seed_store([device], [])
        status, body = self._post("/crm-tick",
                                  {"device_id": "dev1", "action": "stage_back"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["device"]["stage"], "payment_done")
        self.assertEqual(body["device"]["done_at"], "")
        self.assertEqual(body["device"]["code_delivered_at"], "")
        self.assertEqual(body["device"]["payment_confirmed_at"],
                         "2026-01-02 09:00:00")


class DeviceUpdateTests(V15HttpHarness):
    def test_update_roundtrip_and_serial1_immutable(self):
        """UI contract end to end: editable fields update; a serial1 change
        is rejected with the backend's delete-and-recreate Persian error."""
        self._seed_store([self._device()], [])
        status, body = self._post("/crm-device", {
            "action": "update", "id": "dev1", "name": "X2",
            "serial2": "356938035643810", "notes": "یادداشت تست",
            "auto_advance": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        record = body["record"]
        self.assertEqual(record["name"], "X2")
        self.assertEqual(record["serial2"], "356938035643810")
        self.assertEqual(record["serial1"], "356938035643809")
        self.assertTrue(record["auto_advance"])
        # attempting to change serial1 surfaces the Persian error (mentions
        # deleting the case) instead of mutating the identity
        status, body = self._post("/crm-device", {
            "action": "update", "id": "dev1",
            "serial1": "111111111111111"})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertTrue(any("حذف" in e for e in body["errors"]),
                        f"expected immutable-serial1 error, got {body['errors']}")
        # the failed update must not have touched the record
        with urllib.request.urlopen(self._url("/crm-state"), timeout=10) as resp:
            state = json.loads(resp.read().decode("utf-8"))
        stored = next(d for d in state["devices"] if d["id"] == "dev1")
        self.assertEqual(stored["serial1"], "356938035643809")
        self.assertEqual(stored["name"], "X2")


if __name__ == "__main__":
    unittest.main()
