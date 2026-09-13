# -*- coding: utf-8 -*-
"""v1.5 B1 — customs-inquiry fail-closed: error surfacing + identity gate.

- Gomrok inquiry affordances (device-card «استعلام گمرک» button, periodic
  «اجرای فوری» / «استعلام مجدد» / «شروع فوری استعلام» /
  «استعلام انتخاب‌شده‌ها») render disabled with a hint tooltip while the
  identity pool holds no approximately-valid entry; hamta buttons stay
  enabled.
- devInquire refuses a gomrok start from a stale UI when the pool is empty.
- The previously catch-less api('/crm-state') call (stage_back branch of
  devTick) now toasts transport/backend errors and reports a missing device
  instead of returning silently.
- Bulk /crm-inquiry toasts surface the first per-device backend error
  instead of a bare «شروع نشد».
- HTTP (fail-closed proof): with an empty pool, POST /crm-inquire gomrok
  returns 400 with Persian errors and spawns nothing (device untouched),
  hamta still runs for the same device, an invalid service is rejected,
  and the bulk /crm-inquiry route answers 200 with per-device errors —
  exactly the payload the periodic tab now surfaces.
"""
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import crm
import panel


def crm_page_src():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE (same trick as the rail test)."""
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


class InquiryGateSourceTests(unittest.TestCase):
    """CRM_PAGE JS markers: identity-pool gate + fail-closed error surfacing."""

    def test_identity_pool_helper_and_hint(self):
        js = crm_js()
        self.assertIn("function identityPoolReady()", js)
        self.assertIn("IDENTITY_POOL_HINT", js)
        self.assertIn("ابتدا در بخش استخر هویت حداقل یک مشخصات معتبر اضافه کنید", js)
        body = fn_body_js(js, "identityPoolReady")
        self.assertIn("STATE.identities", body)
        self.assertIn("digitsFa", body)
        self.assertIn(r"/^\d{10}$/", body)              # national code shape
        self.assertIn(r"/^09\d{9}$/", body)             # mobile shape
        self.assertIn(r"/\d{4}\/\d{2}\/\d{2}/", body)   # birth shape (approx.)

    def test_all_five_gomrok_affordances_gated(self):
        src = crm_page_src()
        for marker in (
            # device card «استعلام گمرک»
            "gomrokGateAttrs()+' onclick=\"devInquire(",
            # periodic tab «اجرای فوری»
            "gomrokGateAttrs()+' onclick=\"periodicRunOne(",
            # periodic tab «استعلام مجدد»
            "gomrokGateAttrs()+' onclick=\"periodicReInquire(",
            # periodic tab «شروع فوری استعلام»
            "gomrokGateAttrs()+' onclick=\"periodicRunAll(",
            # periodic tab «استعلام انتخاب‌شده‌ها»
            "gomrokGateAttrs()+' onclick=\"periodicRunSelected(",
        ):
            self.assertIn(marker, src)
        gate = fn_body_js(crm_js(), "gomrokGateAttrs")
        self.assertIn("identityPoolReady()", gate)
        self.assertIn("disabled", gate)
        self.assertIn("IDENTITY_POOL_HINT", gate)

    def test_hamta_button_not_gated(self):
        lines = [line for line in panel.CRM_PAGE.splitlines()
                 if "استعلام همتا</button>" in line]
        self.assertTrue(lines, "hamta button markup not found in CRM_PAGE")
        for line in lines:
            self.assertNotIn("gomrokGateAttrs", line)
            self.assertNotIn("identityPoolReady", line)

    def test_dev_inquire_guards_gomrok_only(self):
        body = fn_body_js(crm_js(), "devInquire")
        self.assertIn("service!=='hamta'&&!identityPoolReady()", body)
        self.assertIn("IDENTITY_POOL_HINT", body)

    def test_stage_back_surfaces_errors_and_missing_device(self):
        src = crm_page_src()
        start = src.index("action==='__stage_back'")
        chunk = src[start:src.index("__start_payment", start)]
        self.assertIn("api('/crm-state')", chunk)
        self.assertIn("پرونده پیدا نشد", chunk)
        # the /crm-state chain is fail-closed: its own catch precedes return
        # (v1.3.1: the catch is a named contextual toast)
        self.assertRegex(
            chunk, r"\}\)\.catch\(e=>toastCtx\(cn,String\(e\),false\)\);\s*return;")

    def test_bulk_run_toasts_first_backend_error(self):
        js = crm_js()
        helper = fn_body_js(js, "inquiryBulkToast")
        self.assertIn("results", helper)
        self.assertIn("errors", helper)
        self.assertIn(",false)", helper)      # failure toast renders red
        for name in ("periodicRunAll", "periodicRunSelected"):
            self.assertIn("inquiryBulkToast(r)", fn_body_js(js, name))


class InquiryGateHttpHarness(unittest.TestCase):
    """Boots the real handler with isolated stores (same as test_crm_api)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
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

    def _intake_device(self):
        """A device at an intake stage (identity comes from the pool)."""
        record, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": "356938035643809"})
        assert errs == [], errs
        return record


class TestGomrokInquiryFailsClosed(InquiryGateHttpHarness):
    """Empty identity pool: gomrok fails closed at HTTP level; hamta runs."""

    def test_empty_pool_gomrok_rejected_hamta_runs(self):
        device = self._intake_device()
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running",
                             return_value=False):
            spawn.return_value = {"pid": 4321}
            # gomrok: fail-closed — Persian errors, no spawn, device untouched
            status, body = self._post("/crm-inquire", {
                "device_id": device["id"], "service": "gomrok"})
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertTrue(body["errors"])
            self.assertTrue(any("استخر" in str(e) for e in body["errors"]),
                            body["errors"])
            self.assertTrue(any(re.search(r"[؀-ۿ]", str(e))
                                for e in body["errors"]), body["errors"])
            spawn.assert_not_called()
            data = crm.store_read()
            stored = next(d for d in data["devices"]
                          if d["id"] == device["id"])
            self.assertFalse(stored.get("last_inquiry_at"))
            # same device, hamta: NOT rejected for pool reasons
            status, body = self._post("/crm-inquire", {
                "device_id": device["id"], "service": "hamta"})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"], body)
            self.assertFalse(body.get("errors"))
            spawn.assert_called_once()

    def test_invalid_service_rejected(self):
        status, body = self._post("/crm-inquire", {
            "device_id": "x", "service": "wat"})
        self.assertEqual(status, 400)
        self.assertIn("نوع استعلام نامعتبر است", body["errors"])


class TestBulkInquiryPerDeviceErrors(InquiryGateHttpHarness):
    """Bulk route keeps HTTP 200 but reports each device's failure — the
    payload the periodic tab now surfaces via inquiryBulkToast."""

    def test_empty_pool_bulk_reports_per_device_errors(self):
        device = self._intake_device()
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running",
                             return_value=False):
            spawn.return_value = {"pid": 99}
            status, body = self._post("/crm-inquiry", {
                "service": "gomrok", "device_ids": [device["id"]]})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(body["queued"], 0)
        self.assertEqual(len(body["results"]), 1)
        entry = body["results"][0]
        self.assertEqual(entry["device_id"], str(device["id"]))
        self.assertFalse(entry["ok"])
        self.assertTrue(entry["errors"])
        self.assertTrue(any("استخر" in str(e) for e in entry["errors"]),
                        entry["errors"])
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
