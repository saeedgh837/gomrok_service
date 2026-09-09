"""v1.4 — customer finance + periodic-tools tests.

Covers:
  1. Base service fee (هزینه ثبت رجیستری, default 24_000_000 Toman) is
     auto-charged to the customer when a device is registered for them.
  2. The fee amount is configurable from the settings tab (named
     «هزینه ثبت رجیستری») via /crm-settings.
  3. Customers tab per-customer buttons «ثبت دریافتی» (receipt, optional
     attachment) and «ثبت بدهی جدید» (debt, optional attachment) with
     their own modal forms.
  4. Attachment upload endpoint accepts an image and stores it in the
     data dir; /tx-artifact serves it back; debit/receipt rows carry it.
  5. Periodic panel: multi-select checkboxes for grouped changes, per-row
     «ریست تاریخچهٔ استعلام» (reset history) and «استعلام مجدد»
     (re-inquiry = reset + immediate run).
"""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import crm
import panel


def crm_page_src():
    src = panel.render_crm_page()
    return src.decode("utf-8") if isinstance(src, bytes) else str(src)


class BaseFeeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.tmp.name, "crm.json")
        self.addCleanup(crm.reset_cache)
        crm.reset_cache()

    def tearDown(self):
        os.environ.pop("IRICA_CRM_STORE", None)

    def _customer(self):
        cust, _ = crm.add_customer({"full_name": "آزمون", "mobile": "09120000000"})
        return cust["id"]

    def test_settings_default_registry_fee_24m(self):
        data = crm.store_read()
        self.assertEqual(
            int(data["settings"].get("registry_fee_amount", -1)), 24_000_000)

    def test_add_device_charges_base_fee(self):
        cid = self._customer()
        device, errors = crm.add_device({"name": "d1",
                                         "serial1": "111111111111111",
                                         "customer_id": cid})
        self.assertEqual(errors, [])
        data = crm.store_read()
        customer = next(c for c in data["customers"] if c["id"] == cid)
        txs = customer.get("transactions", [])
        self.assertEqual(len(txs), 1)
        self.assertEqual(int(txs[0]["amount"]), -24_000_000)
        self.assertEqual(txs[0]["kind"], "base_fee")
        self.assertEqual(txs[0]["device_id"], device["id"])

    def test_add_device_without_customer_no_fee(self):
        device, errors = crm.add_device({"name": "d2", "serial1": "222222222222222"})
        self.assertEqual(errors, [])
        data = crm.store_read()
        self.assertEqual(data["customers"], [])

    def test_fee_uses_custom_configured_amount(self):
        data = crm.store_read()
        data["settings"]["registry_fee_amount"] = 5_000_000
        crm.store_write(data)
        cid = self._customer()
        crm.add_device({"name": "d3", "serial1": "333333333333333", "customer_id": cid})
        data = crm.store_read()
        tx = next(c for c in data["customers"] if c["id"] == cid
                  )["transactions"][0]
        self.assertEqual(int(tx["amount"]), -5_000_000)

    def test_fee_not_duplicated_on_device_update(self):
        cid = self._customer()
        device, _ = crm.add_device({"name": "d4", "serial1": "444444444444444",
                                    "customer_id": cid})
        crm.update_device(device["id"], {"name": "d4 renamed"})
        data = crm.store_read()
        customer = next(c for c in data["customers"] if c["id"] == cid)
        self.assertEqual(len(customer["transactions"]), 1)

    def test_two_devices_two_fees(self):
        cid = self._customer()
        dev_a, _ = crm.add_device({"name": "a", "serial1": "111111111111112",
                                   "customer_id": cid})
        dev_b, _ = crm.add_device({"name": "b", "serial1": "222222222222223",
                                   "customer_id": cid})
        data = crm.store_read()
        customer = next(c for c in data["customers"] if c["id"] == cid)
        self.assertEqual(len(customer["transactions"]), 2)
        self.assertEqual({t["device_id"] for t in customer["transactions"]},
                         {dev_a["id"], dev_b["id"]})


class RegistryFeeSettingsTests(unittest.TestCase):
    """crm_update_settings validates و ذخیرهٔ مبلغ هزینه ثبت رجیستری."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.tmp.name, "crm.json")
        self.addCleanup(crm.reset_cache)
        crm.reset_cache()

    def tearDown(self):
        os.environ.pop("IRICA_CRM_STORE", None)

    def test_update_settings_registry_fee(self):
        settings, errors = panel.crm_update_settings({"registry_fee_amount": "12,500,000"})
        self.assertEqual(errors, [])
        self.assertEqual(int(settings["registry_fee_amount"]), 12_500_000)

    def test_update_settings_rejects_negative_fee(self):
        settings, errors = panel.crm_update_settings({"registry_fee_amount": -5})
        self.assertTrue(errors)
        self.assertIsNone(settings)

    def test_update_settings_rejects_non_numeric_fee(self):
        settings, errors = panel.crm_update_settings({"registry_fee_amount": "abc"})
        self.assertTrue(errors)

    def test_settings_payload_exposes_fee_to_ui(self):
        cid_src = panel.crm_state_payload
        # crm_state_payload carries raw store dict incl. settings
        state = cid_src()
        self.assertIn("registry_fee_amount", state["settings"])


class FinanceApiTests(unittest.TestCase):
    """HTTP-level: typed transactions with optional image attachment."""

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
        cust, _ = crm.add_customer({"full_name": "آزمون", "mobile": "09120000000"})
        self.cid = cust["id"]

    def test_receipt_transaction_positive_with_kind(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/crm-customer",
            data=json.dumps({"action": "tx", "id": self.cid,
                             "amount": 24_000_000, "note": "واریز",
                             "kind": "receipt"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        data = crm.store_read()
        tx = next(c for c in data["customers"] if c["id"] == self.cid
                  )["transactions"][0]
        self.assertEqual(int(tx["amount"]), 24_000_000)
        self.assertEqual(tx["kind"], "receipt")

    def test_debt_transaction_negative_kind(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/crm-customer",
            data=json.dumps({"action": "tx", "id": self.cid,
                             "amount": -3_000_000, "note": "قطعه",
                             "kind": "debt"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        tx = crm.store_read()["customers"][0]["transactions"][0]
        self.assertEqual(int(tx["amount"]), -3_000_000)
        self.assertEqual(tx["kind"], "debt")

    def test_tx_with_image_attachment_saved_and_served(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
            "YPgPAAEDAQAIicL2AAAAAElFTkSuQmCC")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/crm-customer",
            data=json.dumps({"action": "tx", "id": self.cid,
                             "amount": 1_000, "kind": "receipt",
                             "image_b64": base64.b64encode(png).decode("ascii")
                             }).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        tx = crm.store_read()["customers"][0]["transactions"][0]
        self.assertTrue(tx.get("image", ""))
        import app_paths
        stored = os.path.join(app_paths.data_subdir("tx_artifacts"),
                              tx["image"])
        self.assertTrue(os.path.isfile(stored))
        # served via /tx-artifact
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/tx-artifact?name="
                + urllib.request.quote(tx["image"])) as resp:
            served = resp.read()
        self.assertEqual(served, png)
        self.assertEqual(resp.headers.get("Content-Type"), "image/png")

    def test_tx_artifact_rejects_path_traversal(self):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/tx-artifact?name=..%2F..%2Fprofiles.json")
            self.fail("expected 400/404")
        except urllib.error.HTTPError as exc:
            self.assertIn(exc.code, (400, 404))

    def test_ledger_row_kinds_survive(self):
        cust, _ = crm.add_transaction(self.cid, 100, "test")
        self.assertEqual(cust["kind"], "")


class PeriodicToolsApiTests(unittest.TestCase):
    """Reset-history + re-inquiry actions for the periodic panel."""

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

    def _device_at_handed(self):
        cust, _ = crm.add_customer({"full_name": "م", "mobile": "09120000000"})
        device, _ = crm.add_device({"name": "p1",
                                    "serial1": "111111111111111",
                                    "customer_id": cust["id"]})
        did = device["id"]
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": did, "status": "healthy", "hamta_status": "good"}]}, handle)
        device, _ = crm.advance(did, "queue_inquiry")
        device, _ = crm.advance(did, "mark_ready")
        owner, _ = crm.add_owner({"first_name": "ا", "last_name": "م",
                                  "national": "0123456789",
                                  "birth": "1375/04/29", "mobile": "09120000000",
                                  "card_no": "6037991111111111"})
        crm.attach_owner(did, owner["id"])
        device, _ = crm.advance(did, "handover")
        return did

    def test_reset_history_clears_estelam_row(self):
        did = self._device_at_handed()
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": did, "status": "advanced", "hamta_status": "good"}]}, handle)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/crm-inquiry-reset",
            data=json.dumps({"device_ids": [did]}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        est = json.load(open(self.estelam_store, encoding="utf-8"))
        row = next(d for d in est["devices"] if d["id"] == did)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["result"], "")
        device = next(d for d in crm.store_read()["devices"] if d["id"] == did)
        self.assertEqual(device["last_inquiry_at"], "")

    def test_reinquiry_starts_when_not_running(self):
        did = self._device_at_handed()
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": did, "status": "advanced", "hamta_status": "good"}]}, handle)
        with patch.object(panel, "estelam_start") as fake_start:
            fake_start.return_value = {"ok": True}
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/crm-inquiry-reset",
                data=json.dumps({"device_ids": [did], "restart": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["queued"], 1)
        fake_start.assert_called_once()

    def test_reset_unknown_device_fails_cleanly(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/crm-inquiry-reset",
            data=json.dumps({"device_ids": ["nope"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["queued"], 0)


class PeriodicUiTests(unittest.TestCase):
    """Periodic tab: checkbox per row + group tools + per-row tools."""

    def test_periodic_multi_select_ui(self):
        src = crm_page_src()
        self.assertIn("periodicSel", src)          # checkbox name per row
        self.assertIn("periodicSelAll", src)       # select-all checkbox
        self.assertIn("periodicSelIds()", src)     # collector helper
        self.assertIn("periodicRunSelected()", src)
        self.assertIn("periodicResetSelected()", src)

    def test_periodic_row_tools(self):
        src = crm_page_src()
        self.assertIn("periodicResetOne(", src)    # per-row reset history
        self.assertIn("periodicReInquire(", src)   # per-row re-inquiry
        self.assertIn("ریست تاریخچه", src)
        self.assertIn("استعلام مجدد", src)

    def test_settings_registry_fee_field(self):
        src = crm_page_src()
        self.assertIn("stFee", src)                # settings input id
        self.assertIn("هزینه ثبت رجیستری", src)
        self.assertIn("registry_fee_amount", src)

    def test_customer_finance_buttons_and_modals(self):
        src = crm_page_src()
        self.assertIn("dlgReceipt(", src)
        self.assertIn("dlgDebt(", src)
        self.assertIn("ثبت دریافتی", src)
        self.assertIn("ثبت بدهی", src)
        self.assertIn("txImage", src)              # file input id
        self.assertIn("readTxImage", src)          # base64 reader helper

    def test_device_add_fee_hint(self):
        src = crm_page_src()
        # operator-visible hint that the fee will be charged
        self.assertIn("هزینهٔ ثبت", src)


if __name__ == "__main__":
    unittest.main()
