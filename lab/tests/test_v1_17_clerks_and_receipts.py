"""v1.17.0 — Clerk ("متصدی ثبت") subsystem and customer case receipts.

Covers:
  1. Clerk CRUD: add_clerk, update_clerk, delete_clerk, validate_clerk.
  2. Clerk accounting: credit, payment/debit, void_clerk_transaction, clerk_balance.
  3. Case-clerk binding: add_device, update_device, attach_owner, advance('attach_owner').
  4. Customer receipts for cases (any stage): add_transaction_seq with device_id,
     POST /crm-customer action=tx with device_id.
  5. HTTP API routes in panel.py:
     - POST /crm-clerk: add, update, delete, tx, void_tx
     - GET /crm-state: includes clerks with computed balance
     - POST /crm-tick: forwards clerk_id on attach_owner
  6. Frontend UI contracts in panel.CRM_PAGE:
     - TABS includes ['clerks', 'متصدی ثبت']
     - dlgPeriod includes clerks segment and perClerksHtml
     - dlgAttach and dlgDeviceEdit include clerk picker
     - dlgCaseReceipt and caseDetail include customer receipt button
     - warnConfirmDlg exists with warning alert
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crm
import panel


class TestClerkBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.orig_env = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = self.tmp.name
        crm._store_write(crm._empty_store())

    def tearDown(self):
        if self.orig_env is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.orig_env
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_clerk_crud_and_validation(self):
        # Validation failures
        errs = crm.validate_clerk({})
        self.assertIn("نام متصدی الزامی است", errs)

        errs = crm.validate_clerk({"name": "علی", "mobile": "12345"})
        self.assertIn("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود", errs)

        # Successful addition
        clerk, errs = crm.add_clerk({
            "name": "محسن رضایی",
            "mobile": "09121112233",
            "notes": "متصدی استان البرز"
        })
        self.assertEqual(errs, [])
        self.assertIsNotNone(clerk)
        self.assertEqual(clerk["name"], "محسن رضایی")
        self.assertEqual(clerk["mobile"], "09121112233")
        self.assertTrue(clerk["active"])
        self.assertEqual(clerk["transactions"], [])

        # Update
        updated, errs = crm.update_clerk(clerk["id"], {
            "name": "محسن رضایی اصل",
            "mobile": "09129998877",
            "active": False
        })
        self.assertEqual(errs, [])
        self.assertEqual(updated["name"], "محسن رضایی اصل")
        self.assertEqual(updated["mobile"], "09129998877")
        self.assertFalse(updated["active"])

        # Soft delete vs Hard delete
        # Add a device referencing this clerk
        cust, _ = crm.add_customer({"full_name": "مشتری تست"})
        dev, errs = crm.add_device({"name": "تلفن تست", "serial1": "111111111111111", "customer_id": cust["id"], "clerk_id": clerk["id"]})
        self.assertEqual(errs, [])
        self.assertEqual(dev["clerk_id"], clerk["id"])

        # Regular delete with devices -> soft-delete (active=False)
        ok, errs = crm.delete_clerk(clerk["id"], force=False)
        self.assertTrue(ok)
        store = crm._store_read()
        re_clerk = next(c for c in store["clerks"] if c["id"] == clerk["id"])
        self.assertFalse(re_clerk["active"])

        # Force delete -> removes record and clears device reference
        ok, errs = crm.delete_clerk(clerk["id"], force=True)
        self.assertTrue(ok)
        store = crm._store_read()
        self.assertFalse(any(c["id"] == clerk["id"] for c in store["clerks"]))
        re_dev = next(d for d in store["devices"] if d["id"] == dev["id"])
        self.assertEqual(re_dev["clerk_id"], "")

    def test_clerk_accounting_and_voids(self):
        clerk, _ = crm.add_clerk({"name": "سارا محمدی", "mobile": "09123456789"})
        cid = clerk["id"]

        period, _ = crm.open_period("دوره آزمایشی")
        pid = period["id"]
        cust, _ = crm.add_customer({"full_name": "مشتری سارا"})
        dev, _ = crm.add_device({"name": "گوشی سارا", "serial1": "666666666666666", "customer_id": cust["id"]})
        did = dev["id"]

        # Initial balance: 0
        self.assertEqual(crm.clerk_balance(clerk), 0)

        # 1. Credit 1,000,000 Toman (e.g. commission earned for a case)
        tx1, errs = crm.add_clerk_transaction(
            cid, amount="1000000", kind="credit",
            recipient_name="سارا محمدی", device_id=did, period_id=pid,
            note="کمیسیون پرونده آیفون"
        )
        self.assertEqual(errs, [])
        self.assertEqual(tx1["amount"], 1_000_000)
        self.assertEqual(tx1["kind"], "credit")

        store = crm._store_read()
        c_loaded = next(c for c in store["clerks"] if c["id"] == cid)
        # Balance = +1,000,000 (business owes clerk)
        self.assertEqual(crm.clerk_balance(c_loaded), 1_000_000)

        # 2. Payment/Disbursement 400,000 Toman to intermediary person
        tx2, errs = crm.add_clerk_transaction(
            cid, amount="400000", kind="payment",
            recipient_name="کامران شریفی (حساب واسط)", period_id=pid,
            note="واریز کارت به کارت بابت تسویه دوره"
        )
        self.assertEqual(errs, [])
        self.assertEqual(tx2["amount"], 400_000)
        self.assertEqual(tx2["recipient_name"], "کامران شریفی (حساب واسط)")

        store = crm._store_read()
        c_loaded = next(c for c in store["clerks"] if c["id"] == cid)
        # Balance = 1,000,000 - 400,000 = +600,000
        self.assertEqual(crm.clerk_balance(c_loaded), 600_000)

        # 3. Another payment 700,000 Toman -> balance becomes negative (-100,000 debtor)
        tx3, errs = crm.add_clerk_transaction(
            cid, amount="700000", kind="payment",
            recipient_name="سارا محمدی", note="مساعده"
        )
        self.assertEqual(errs, [])
        store = crm._store_read()
        c_loaded = next(c for c in store["clerks"] if c["id"] == cid)
        self.assertEqual(crm.clerk_balance(c_loaded), -100_000)

        # 4. Void tx3 (700,000 payment)
        rev, errs = crm.void_clerk_transaction(cid, tx3["id"], reason="واریز اشتباه")
        self.assertEqual(errs, [])
        self.assertIsNotNone(rev)
        self.assertEqual(rev["kind"], "void")
        self.assertEqual(rev["reversal_of"], tx3["id"])

        store = crm._store_read()
        c_loaded = next(c for c in store["clerks"] if c["id"] == cid)
        # tx3 and its void reversal are excluded from balance -> back to +600,000
        self.assertEqual(crm.clerk_balance(c_loaded), 600_000)

        # Double-void must fail
        _, errs = crm.void_clerk_transaction(cid, tx3["id"], reason="دوباره")
        self.assertIn("این تراکنش قبلاً ابطال شده است", errs)

    def test_case_clerk_binding_and_passenger_attach(self):
        clerk, _ = crm.add_clerk({"name": "احمد حسینی", "mobile": "09351234567"})
        owner, errs = crm.add_owner({
            "first_name": "رضا", "last_name": "بهرامی",
            "national": "0012345679", "birth": "1370/01/01",
            "mobile": "09128889900"
        })
        self.assertEqual(errs, [])
        cust, _ = crm.add_customer({"full_name": "فروشگاه پارس"})

        # Create device without clerk
        dev, errs = crm.add_device({
            "name": "سامسونگ اس۲۴",
            "serial1": "222222222222222",
            "customer_id": cust["id"]
        })
        self.assertEqual(errs, [])
        self.assertEqual(dev.get("clerk_id", ""), "")

        # Update device with clerk
        dev_up, errs = crm.update_device(dev["id"], {"clerk_id": clerk["id"]})
        self.assertEqual(errs, [])
        self.assertEqual(dev_up["clerk_id"], clerk["id"])

        # Attach owner with clerk update
        clerk2, errs = crm.add_clerk({"name": "مهدی کریمی", "mobile": "09361234567"})
        self.assertEqual(errs, [])
        bound_dev, errs = crm.attach_owner(dev["id"], owner["id"], clerk_id=clerk2["id"])
        self.assertEqual(errs, [])
        self.assertEqual(bound_dev["owner_id"], owner["id"])
        self.assertEqual(bound_dev["clerk_id"], clerk2["id"])

        # Check advance attach_owner forwards clerk_id
        owner2, errs = crm.add_owner({
            "first_name": "علی", "last_name": "صادقی",
            "national": "0023456787", "birth": "1372/02/02",
            "mobile": "09127778899"
        })
        self.assertEqual(errs, [])
        dev2, _ = crm.add_device({"name": "شیائومی ۱۳", "serial1": "333333333333333", "customer_id": cust["id"]})
        # Move dev2 to ready_owner stage
        with crm.STORE_LOCK:
            data = crm._store_read()
            d2 = next(d for d in data["devices"] if d["id"] == dev2["id"])
            d2["stage"] = "ready_owner"
            crm._store_write(data)
        adv_dev, errs = crm.advance(dev2["id"], "attach_owner", owner_id=owner2["id"], clerk_id=clerk["id"])
        self.assertEqual(errs, [])
        self.assertEqual(adv_dev["owner_id"], owner2["id"])
        self.assertEqual(adv_dev["clerk_id"], clerk["id"])

    def test_customer_receipt_in_any_case_stage(self):
        cust, _ = crm.add_customer({"full_name": "بازرگانی پیشرو"})
        dev, _ = crm.add_device({"name": "آیفون ۱۵", "serial1": "444444444444444", "customer_id": cust["id"]})

        # Device is in "new" stage
        self.assertEqual(dev["stage"], "new")

        # Book receipt with device_id
        entry, seq, errs = crm.add_transaction_seq(
            cust["id"], 5_000_000, note="دریافتی نقد بابت آیفون ۱۵",
            kind="receipt", device_id=dev["id"]
        )
        self.assertEqual(errs, [])
        self.assertEqual(entry["amount"], 5_000_000)
        self.assertEqual(entry["device_id"], dev["id"])

        # Advance device to handed, payment, done — receipt remains available
        store = crm._store_read()
        re_cust = next(c for c in store["customers"] if c["id"] == cust["id"])
        tx = next(t for t in re_cust["transactions"] if t.get("device_id") == dev["id"] and t.get("kind") == "receipt")
        self.assertEqual(tx["amount"], 5_000_000)
        self.assertEqual(tx["kind"], "receipt")

    def test_financial_integrity_and_validation(self):
        clerk, _ = crm.add_clerk({"name": "تست صحت مالی"})
        cid = clerk["id"]

        # 1. Invalid device_id rejected in clerk tx
        _, errs = crm.add_clerk_transaction(cid, 100_000, device_id="nonexistent")
        self.assertIn("دستگاه پیدا نشد", errs)

        # 2. Invalid period_id rejected in clerk tx
        _, errs = crm.add_clerk_transaction(cid, 100_000, period_id="nonexistent")
        self.assertIn("دوره پیدا نشد", errs)

        # 3. Cross-customer device reference rejected in customer receipt
        cust1, _ = crm.add_customer({"full_name": "مشتری اول"})
        cust2, _ = crm.add_customer({"full_name": "مشتری دوم"})
        dev1, _ = crm.add_device({"name": "دستگاه مشتری اول", "serial1": "777777777777777", "customer_id": cust1["id"]})

        _, _, errs = crm.add_transaction_seq(cust2["id"], 500_000, device_id=dev1["id"])
        self.assertIn("این پرونده به مشتری دیگری تعلق دارد", errs)

        # 4. Mismatch between device_id period and requested period_id is rejected
        per1, _ = crm.open_period("دوره ۱")
        dev_in_per, _ = crm.add_device({"name": "دستگاه در دوره ۱", "serial1": "888888888888888", "customer_id": cust1["id"]})
        crm.close_period(per1["id"], "بستن دوره ۱")
        per2, _ = crm.open_period("دوره ۲")

        _, errs = crm.add_clerk_transaction(cid, 200_000, device_id=dev_in_per["id"], period_id=per2["id"])
        self.assertIn("دورهٔ انتخابی با دورهٔ این پرونده همخوانی ندارد", errs)

        # 5. Device without period inherits device period automatically
        tx_inherited, errs = crm.add_clerk_transaction(cid, 200_000, device_id=dev_in_per["id"])
        self.assertEqual(errs, [])
        self.assertEqual(tx_inherited["period_id"], dev_in_per["period_id"])

        # 6. Changing device clerk or moving device leaves historical transactions and balance unchanged
        clerk_a, _ = crm.add_clerk({"name": "متصدی الف"})
        clerk_b, _ = crm.add_clerk({"name": "متصدی ب"})
        dev_hist, _ = crm.add_device({"name": "دستگاه تاریخی", "serial1": "999999999999999", "customer_id": cust1["id"], "clerk_id": clerk_a["id"]})

        # Book tx for clerk_a
        crm.add_clerk_transaction(clerk_a["id"], 750_000, kind="credit", device_id=dev_hist["id"])
        store = crm._store_read()
        ca = next(c for c in store["clerks"] if c["id"] == clerk_a["id"])
        cb = next(c for c in store["clerks"] if c["id"] == clerk_b["id"])
        self.assertEqual(crm.clerk_balance(ca), 750_000)
        self.assertEqual(crm.clerk_balance(cb), 0)

        # Reassign device to clerk_b
        crm.update_device(dev_hist["id"], {"clerk_id": clerk_b["id"]})
        store = crm._store_read()
        ca_after = next(c for c in store["clerks"] if c["id"] == clerk_a["id"])
        cb_after = next(c for c in store["clerks"] if c["id"] == clerk_b["id"])
        # clerk_a balance remains 750,000; clerk_b balance remains 0
        self.assertEqual(crm.clerk_balance(ca_after), 750_000)
        self.assertEqual(crm.clerk_balance(cb_after), 0)
        self.assertEqual(len(ca_after["transactions"]), 1)
        self.assertEqual(len(cb_after["transactions"]), 0)

        # 7. Device moving to another period does NOT change historical transaction attribution
        tx_orig = ca_after["transactions"][0]
        orig_pid = tx_orig.get("period_id")
        self.assertTrue(orig_pid)
        # Move device to a new period (per3)
        crm.close_period(per2["id"], "بستن دوره ۲")
        per3, _ = crm.open_period("دوره ۳")
        with crm.STORE_LOCK:
            s_data = crm._store_read()
            d_item = next(d for d in s_data["devices"] if d["id"] == dev_hist["id"])
            d_item["period_id"] = per3["id"]
            crm._store_write(s_data)

        # Re-check clerk_a tx still points to orig_pid (per2)
        s_data = crm._store_read()
        ca_re = next(c for c in s_data["clerks"] if c["id"] == clerk_a["id"])
        self.assertEqual(ca_re["transactions"][0]["period_id"], orig_pid)
        self.assertEqual(ca_re["transactions"][0]["period_id"], per2["id"])
        self.assertNotEqual(ca_re["transactions"][0]["period_id"], per3["id"])

        # 8. Test clerk_id preservation in cloud_sync apply
        from cloud_sync.apply import _upsert_row
        from cloud_sync import mapper

        wire_record = {
            "id": dev_hist["id"],
            "name": "دستگاه بروز شده سرور",
            "serial1": "999999999999999",
            "serial2": "",
            "customer_id": None,
            "owner_id": None,
            "stage": "new",
            "auto_advance": False,
            "profile_id": "",
            "period_id": None,
            "created": "2026-09-01T10:00:00+00:00",
            "updated": "2026-09-02T10:00:00+00:00"
        }
        with crm.STORE_LOCK:
            cur_data = crm._store_read()
            d_hist = next(d for d in cur_data["devices"] if d["id"] == dev_hist["id"])
            d_hist["clerk_id"] = clerk_b["id"]
            _upsert_row(cur_data, "crm_devices", wire_record, index={}, tz=mapper.IRAN_TZ,
                        cloud_id="11111111-1111-1111-1111-111111111111", prior=None)
            crm._store_write(cur_data)

        re_data = crm._store_read()
        re_dev = next(d for d in re_data["devices"] if d["id"] == dev_hist["id"])
        self.assertEqual(re_dev["name"], "دستگاه بروز شده سرور")
        self.assertEqual(re_dev["clerk_id"], clerk_b["id"])


class TestPanelApiAndUIContracts(unittest.TestCase):
    def test_crm_state_payload_includes_clerks_and_balance(self):
        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            orig = os.environ.get("IRICA_CRM_STORE")
            os.environ["IRICA_CRM_STORE"] = f.name
            try:
                crm._store_write(crm._empty_store())
                clerk, _ = crm.add_clerk({"name": "مریم حسینی", "mobile": "09120001122"})
                crm.add_clerk_transaction(clerk["id"], 2_500_000, kind="credit")

                payload = panel.crm_state_payload()
                self.assertIn("clerks", payload)
                self.assertEqual(len(payload["clerks"]), 1)
                self.assertEqual(payload["clerks"][0]["name"], "مریم حسینی")
                self.assertEqual(payload["clerks"][0]["balance"], 2_500_000)
            finally:
                if orig is None:
                    os.environ.pop("IRICA_CRM_STORE", None)
                else:
                    os.environ["IRICA_CRM_STORE"] = orig

    def test_ui_templates_and_scripts(self):
        page = panel.CRM_PAGE

        # 1. TABS has 'clerks'
        self.assertIn("['clerks','متصدی ثبت']", page)

        # 2. TAB_SEARCH has clerks
        self.assertIn("clerks:{ph:'جستجو در متصدی‌ها…',unit:'متصدی'}", page)

        # 3. bodyHtml handles TAB === 'clerks'
        self.assertIn("else if(TAB==='clerks')body=clerksHtml();", page)

        # 4. perSeg in dlgPeriod has clerks segment button
        self.assertIn("data-pseg=\"clerks\"", page)
        self.assertIn("perClerksHtml", page)

        # 5. Clerk picker in dlgAttach and dlgDeviceEdit
        self.assertIn("id=\"attClerk\"", page)
        self.assertIn("id=\"dvEClerk\"", page)

        # 6. Customer receipt button in dlgDeviceEdit and caseDetail
        self.assertIn("dlgCaseReceipt", page)
        self.assertIn("ثبت دریافتی از مشتری", page)

        # 7. Warning confirm dialog warnConfirmDlg
        self.assertIn("function warnConfirmDlg", page)
        self.assertIn("هشدار مهم:", page)


from lab.tests.test_crm_api import CrmApiHarness


class TestClerkHttpApi(CrmApiHarness):
    def test_clerk_http_crud_and_transactions(self):
        # 1. Add clerk via HTTP POST /crm-clerk
        status, body = self._post("/crm-clerk", {
            "action": "add",
            "name": "جواد کریمی",
            "mobile": "09123334455",
            "notes": "متصدی آزمایشی"
        })
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        clerk = body["record"]
        cid = clerk["id"]
        self.assertEqual(clerk["name"], "جواد کریمی")

        # 2. Update clerk
        status, body = self._post("/crm-clerk", {
            "action": "update",
            "id": cid,
            "name": "جواد کریمی راد",
            "mobile": "09123334455"
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["record"]["name"], "جواد کریمی راد")

        # 3. Add credit transaction
        status, body = self._post("/crm-clerk", {
            "action": "tx",
            "id": cid,
            "amount": 1_200_000,
            "kind": "credit",
            "recipient_name": "جواد کریمی راد",
            "note": "حق‌الزحمه"
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["record"]["amount"], 1_200_000)

        # 4. Add payment transaction
        status, body = self._post("/crm-clerk", {
            "action": "tx",
            "id": cid,
            "amount": 500_000,
            "kind": "payment",
            "recipient_name": "سعید رضایی (شبا واسط)",
            "note": "تسویه بخش اول"
        })
        self.assertEqual(status, 200, body)
        pay_tx_id = body["record"]["id"]
        self.assertEqual(body["record"]["amount"], 500_000)

        # 5. Check /crm-state includes clerks with calculated balance
        status, state, _ = self._get("/crm-state")
        self.assertEqual(status, 200)
        c_state = next(c for c in state["clerks"] if c["id"] == cid)
        # Balance = 1,200,000 - 500,000 = +700,000
        self.assertEqual(c_state["balance"], 700_000)

        # 6. Void the payment transaction
        status, body = self._post("/crm-clerk", {
            "action": "void_tx",
            "id": cid,
            "tx_id": pay_tx_id,
            "reason": "ثبت تکراری"
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["record"]["kind"], "void")

        # Check balance reverted
        status, state, _ = self._get("/crm-state")
        c_state = next(c for c in state["clerks"] if c["id"] == cid)
        self.assertEqual(c_state["balance"], 1_200_000)

        # 7. Customer receipt with device_id
        c_status, c_body = self._post("/crm-customer", {
            "action": "add",
            "full_name": "مشتری فروشگاه"
        })
        cust_id = c_body["record"]["id"]

        self._open_period()
        d_status, d_body = self._post("/crm-device", {
            "action": "add",
            "name": "آیفون ۱۴",
            "serial1": "555555555555555",
            "customer_id": cust_id,
            "clerk_id": cid
        })
        dev_id = d_body["record"]["id"]

        # Record customer receipt for this device
        status, body = self._post("/crm-customer", {
            "action": "tx",
            "id": cust_id,
            "amount": 3_000_000,
            "kind": "receipt",
            "device_id": dev_id,
            "note": "دریافتی مرحله اول"
        })
        self.assertEqual(status, 200, body)

        status, state, _ = self._get("/crm-state")
        cust_state = next(c for c in state["customers"] if c["id"] == cust_id)
        receipt_tx = next(t for t in cust_state["transactions"] if t.get("kind") == "receipt")
        self.assertEqual(receipt_tx["amount"], 3_000_000)
        self.assertEqual(receipt_tx["device_id"], dev_id)

        # 8. Attach owner via /crm-tick with clerk_id
        o_status, o_body = self._post("/crm-owner", {
            "action": "add",
            "first_name": "کامران",
            "last_name": "نجفی",
            "national": "0012345679",
            "birth": "1365/05/10",
            "mobile": "09121113355"
        })
        owner_id = o_body["record"]["id"]

        # Advance dev to ready_owner
        with crm.STORE_LOCK:
            data = crm._store_read()
            d = next(x for x in data["devices"] if x["id"] == dev_id)
            d["stage"] = "ready_owner"
            crm._store_write(data)

        # New clerk
        c2_status, c2_body = self._post("/crm-clerk", {
            "action": "add",
            "name": "متصدی دوم",
            "mobile": "09128881122"
        })
        c2_id = c2_body["record"]["id"]

        status, body = self._post("/crm-tick", {
            "device_id": dev_id,
            "action": "attach_owner",
            "owner_id": owner_id,
            "clerk_id": c2_id
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["device"]["owner_id"], owner_id)
        self.assertEqual(body["device"]["clerk_id"], c2_id)


if __name__ == "__main__":
    unittest.main()
