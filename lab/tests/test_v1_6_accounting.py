"""v1.6 — accounting backend: received-on-register, void_tx, void-aware
aggregation, customer statement, per-case invoice, business settings.

Covers the frozen contracts:
  * add_device(received_amount): optional non-negative receipt booked in the
    same store write as the registry fee («دریافتی هنگام ثبت پرونده»).
  * POST /crm-customer action=void_tx: original marked voided_at/void_reason,
    a `void` reversal appended, event void_tx; every error branch; concurrent
    double-void resolves to exactly one winner.
  * The accounting rule — a row is inactive iff voided_at or kind == "void"
    and ONLY active rows move balances/aggregates — applied to
    customer_balance, the finance aggregate (and the refund side of device
    deletion, which must never refund an already-voided fee).
  * GET /crm-statement (+ -telegram POST): period math with optional bounds,
    voided rows visible with null balances, canonical Persian validation
    errors, RTL HTML/txt renderers, chunked Telegram fanout.
  * GET /crm-invoice: per-case sections over device-linked ACTIVE rows,
    «ماندهٔ این پرونده» vs «ماندهٔ کل حساب مشتری», the voided-count note.
  * business_name/business_phone settings (trim, 80-char cap, empty clears).
"""
import datetime
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
import jalali   # noqa: E402
import panel    # noqa: E402

REF = datetime.date(2026, 9, 15)
FEE = 24_000_000

BAD_RECEIVED = "مبلغ دریافتی باید عدد نامنفی باشد (تومان)"
RECEIVED_NOTE = "دریافتی هنگام ثبت پرونده"


def _tx(at, amount, kind="", note="", device_id="", voided_at="",
        void_reason=""):
    row = {"at": at, "amount": amount, "note": note, "kind": kind,
           "image": "", "device_id": device_id}
    if voided_at:
        row["voided_at"] = voided_at
        row["void_reason"] = void_reason
    return row


def _customer(cid, name, txs, mobile="09121234567", address="تهران، خیابان نمونه"):
    return {"id": cid, "full_name": name, "address": address, "mobile": mobile,
            "telegram_id": "", "instagram_id": "", "notes": "",
            "transactions": txs, "created": "2026-01-01 00:00:00",
            "updated": "2026-01-01 00:00:00"}


def _device(did, name, customer_id="", serial1="356938035640001",
            serial2="", created="2026-09-12 10:00:00"):
    return {"id": did, "name": name, "serial1": serial1, "serial2": serial2,
            "customer_id": customer_id, "owner_id": "", "stage": "new",
            "auto_advance": False, "profile_id": "",
            "handed_at": "", "registered_at": "", "payment_confirmed_at": "",
            "code_delivered_at": "", "done_at": "", "last_inquiry_at": "",
            "notes": "", "advance_attempts": 0,
            "created": created, "updated": created}


def _store(customers, devices=None, settings=None):
    return {"version": 1, "customers": customers, "owners": [],
            "identities": [], "devices": devices or [],
            "settings": settings or {}}


def synth_serial(n):
    return ("35693803564" + str(n).zfill(4))[:15]


class StoreCase(unittest.TestCase):
    """Isolated CRM + estelam stores (pattern: test_crm_ledger_integrity)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        os.environ["IRICA_CRM_STORE"] = self.crm_store
        self.addCleanup(os.environ.pop, "IRICA_CRM_STORE", None)
        estelam_patcher = patch.object(panel, "ESTELAM_PATH",
                                       self.estelam_store)
        estelam_patcher.start()
        self.addCleanup(estelam_patcher.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    def add_customer(self, n):
        customer, errors = crm.add_customer(
            {"full_name": "مشتری آزمون " + str(n),
             "mobile": "09120000000"})
        self.assertEqual(errors, [], errors)
        return customer

    def customer_by_id(self, customer_id):
        return next(c for c in crm.store_read()["customers"]
                    if c["id"] == customer_id)

    def balance(self, customer_id):
        return crm.customer_balance(self.customer_by_id(customer_id))

    def _log_events(self):
        path = crm.log_path()
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


class ApiCase(StoreCase):
    """HTTP-level harness (pattern: test_v1_5_finance_report)."""

    def setUp(self):
        super().setUp()
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)
        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _open_period(self):
        """v1.8.1: a case may not exist outside a reporting period — call this
        before any intake, the same first step the operator takes."""
        period, errors = crm.open_period("")
        self.assertEqual(errors, [], errors)
        return period

    def _get(self, path):
        try:
            with urllib.request.urlopen(self._url(path), timeout=90) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

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


# ---------------------------------------------------------------------------
# received_amount on add_device
# ---------------------------------------------------------------------------

class ReceivedOnRegisterTests(StoreCase):
    def _txs_of(self, customer_id):
        return self.customer_by_id(customer_id).get("transactions", [])

    def test_absent_and_zero_book_no_receipt(self):
        customer = self.add_customer(1)
        for i, empty in enumerate((None, 0, "", "0")):
            device, errors = crm.add_device(
                {"name": "D", "serial1": synth_serial(i + 1),
                 "customer_id": customer["id"], "received_amount": empty})
            self.assertEqual(errors, [], empty)
            self.assertEqual(len(self._txs_of(customer["id"])), i + 1)
            self.assertEqual(self._txs_of(customer["id"])[-1]["kind"],
                             "base_fee")

    def test_received_books_receipt_alongside_fee(self):
        customer = self.add_customer(1)
        device, errors = crm.add_device(
            {"name": "Device 1", "serial1": synth_serial(1),
             "customer_id": customer["id"], "received_amount": 5_000_000})
        self.assertEqual(errors, [], errors)
        txs = self._txs_of(customer["id"])
        self.assertEqual(len(txs), 2)                    # fee + receipt
        fee, receipt = txs
        self.assertEqual(fee["kind"], "base_fee")
        self.assertEqual(int(fee["amount"]), -FEE)
        self.assertEqual(receipt["kind"], "receipt")
        self.assertEqual(int(receipt["amount"]), 5_000_000)
        self.assertEqual(receipt["note"], RECEIVED_NOTE)
        self.assertEqual(receipt["device_id"], device["id"])
        self.assertEqual(receipt["image"], "")
        self.assertEqual(self.balance(customer["id"]), 5_000_000 - FEE)

    def test_received_with_fee_disabled_books_only_receipt(self):
        data = crm.store_read()
        data["settings"]["registry_fee_amount"] = 0
        crm.store_write(data)
        customer = self.add_customer(1)
        device, errors = crm.add_device(
            {"name": "Device 1", "serial1": synth_serial(1),
             "customer_id": customer["id"], "received_amount": 700000})
        self.assertEqual(errors, [], errors)
        txs = self._txs_of(customer["id"])
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["kind"], "receipt")
        self.assertEqual(txs[0]["device_id"], device["id"])
        self.assertEqual(self.balance(customer["id"]), 700000)

    def test_inline_new_customer_gets_the_receipt(self):
        device, errors = crm.add_device(
            {"name": "Device 1", "serial1": synth_serial(1),
             "received_amount": 3_000_000},
            customer={"full_name": "مشتری همراه ثبت", "mobile": "09121234568"})
        self.assertEqual(errors, [], errors)
        customer = crm.store_read()["customers"][0]
        txs = customer.get("transactions", [])
        self.assertEqual(len(txs), 2)
        self.assertEqual([t["kind"] for t in txs], ["base_fee", "receipt"])
        self.assertEqual(txs[1]["note"], RECEIVED_NOTE)
        self.assertEqual(txs[1]["device_id"], device["id"])
        self.assertEqual(crm.customer_balance(customer), 3_000_000 - FEE)

    def test_received_without_customer_books_nothing(self):
        device, errors = crm.add_device(
            {"name": "Device 1", "serial1": synth_serial(1),
             "received_amount": 5_000_000})
        self.assertEqual(errors, [], errors)
        self.assertTrue(device["id"])
        self.assertEqual(crm.store_read()["customers"], [])

    def test_negative_and_non_int_rejected(self):
        customer = self.add_customer(1)
        for bad in (-5, "-5", "abc", "12.5", True):
            _device, errors = crm.add_device(
                {"name": "Device 1", "serial1": synth_serial(1),
                 "customer_id": customer["id"], "received_amount": bad})
            self.assertEqual(errors, [BAD_RECEIVED], bad)
        self.assertEqual(self._txs_of(customer["id"]), [])

    def test_string_and_persian_digit_forms_accepted(self):
        customer = self.add_customer(1)
        data = crm.store_read()
        data["settings"]["registry_fee_amount"] = 0
        crm.store_write(data)
        for i, good in enumerate(("500000", "۵۰۰۰۰۰", "500,000")):
            _device, errors = crm.add_device(
                {"name": "Device 1", "serial1": synth_serial(i + 1),
                 "customer_id": customer["id"], "received_amount": good})
            self.assertEqual(errors, [], good)
        receipts = [t for t in self.customer_by_id(customer["id"])
                    .get("transactions", []) if t["kind"] == "receipt"]
        self.assertEqual([int(t["amount"]) for t in receipts],
                         [500000] * 3)


class ReceivedOnRegisterApiTests(ApiCase):
    def test_add_device_with_received_amount(self):
        self._open_period()
        customer, errors = crm.add_customer(
            {"full_name": "مشتری دریافتی", "mobile": "09120000001"})
        self.assertEqual(errors, [], errors)
        status, body = self._post("/crm-device", {
            "action": "add", "name": "Device 1",
            "serial1": synth_serial(1), "customer_id": customer["id"],
            "received_amount": 2_000_000})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        txs = self.customer_by_id(customer["id"])["transactions"]
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[1]["kind"], "receipt")
        self.assertEqual(txs[1]["note"], RECEIVED_NOTE)

    def test_add_device_rejects_negative_received_amount(self):
        self._open_period()
        status, body = self._post("/crm-device", {
            "action": "add", "name": "Device 1",
            "serial1": synth_serial(1), "received_amount": -1})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"ok": False, "errors": [BAD_RECEIVED]})


# ---------------------------------------------------------------------------
# void_tx
# ---------------------------------------------------------------------------

class VoidTxTests(StoreCase):
    def _seed_receipt(self, amount=100000):
        customer = self.add_customer(1)
        _entry, errors = crm.add_transaction(customer["id"], amount,
                                             "واریز نقدی", kind="receipt")
        self.assertEqual(errors, [], errors)
        return customer

    def test_happy_path_marks_original_and_appends_reversal(self):
        customer = self._seed_receipt(100000)
        device, errors = crm.add_device(
            {"name": "D", "serial1": synth_serial(1),
             "customer_id": customer["id"]})
        self.assertEqual(errors, [], errors)
        data = crm.store_read()
        txs = data["customers"][0]["transactions"]
        txs[0]["device_id"] = device["id"]       # mark the receipt's case
        crm.store_write(data)

        reversal, errors = crm.void_transaction(customer["id"], 0,
                                                "ثبت اشتباه")
        self.assertEqual(errors, [], errors)
        self.assertEqual(reversal["kind"], "void")
        self.assertEqual(int(reversal["amount"]), -100000)
        self.assertEqual(reversal["note"], "ابطال، دلیل: ثبت اشتباه")
        self.assertEqual(reversal["image"], "")
        self.assertEqual(reversal["device_id"], device["id"])

        txs = self.customer_by_id(customer["id"])["transactions"]
        self.assertEqual(len(txs), 3)            # fee + original + reversal
        original = txs[0]
        self.assertTrue(original.get("voided_at"))
        self.assertEqual(original.get("void_reason"), "ثبت اشتباه")
        self.assertEqual(txs[2]["kind"], "void")
        self.assertEqual(original["amount"], 100000)   # untouched magnitude

        events = [e for e in self._log_events()
                  if e.get("event") == "void_tx"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("customer_id"), customer["id"])
        # the index rides as a string so 0 survives crm_log's falsy filter
        self.assertEqual(events[0].get("tx_index"), "0")

    def test_error_branches(self):
        customer = self._seed_receipt()
        _rev, errors = crm.void_transaction("missing", 0, "دلیل")
        self.assertEqual(errors, ["مشتری پیدا نشد"])
        for bad_index in (-1, 1, 99):
            _rev, errors = crm.void_transaction(customer["id"], bad_index,
                                                "دلیل")
            self.assertEqual(errors, ["تراکنش پیدا نشد"], bad_index)
        _rev, errors = crm.void_transaction(customer["id"], 0, "   ")
        self.assertEqual(errors, ["ثبت دلیل برای ابطال الزامی است"])
        _rev, errors = crm.void_transaction(customer["id"], 0, "")
        self.assertEqual(errors, ["ثبت دلیل برای ابطال الزامی است"])
        # nothing was written by the rejected attempts
        self.assertEqual(len(self.customer_by_id(customer["id"])
                             ["transactions"]), 1)

    def test_already_voided_and_voiding_a_void(self):
        customer = self._seed_receipt()
        _rev, errors = crm.void_transaction(customer["id"], 0, "دلیل اول")
        self.assertEqual(errors, [], errors)
        _rev, errors = crm.void_transaction(customer["id"], 0, "دلیل دوم")
        self.assertEqual(errors, ["این تراکنش قبلاً ابطال شده است"])
        _rev, errors = crm.void_transaction(customer["id"], 1, "دلیل سوم")
        self.assertEqual(errors, ["ردیف ابطال قابل ابطال مجدد نیست"])
        txs = self.customer_by_id(customer["id"])["transactions"]
        self.assertEqual(len(txs), 2)            # exactly one reversal

    def test_balance_after_void_equals_balance_without_the_pair(self):
        with_pair = self._seed_receipt(400000)
        without_pair = self.add_customer(2)
        _rev, errors = crm.void_transaction(with_pair["id"], 0, "ابطال تست")
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.balance(with_pair["id"]),
                         self.balance(without_pair["id"]))
        self.assertEqual(self.balance(with_pair["id"]), 0)
        # an active neighbour row still counts
        _entry, errors = crm.add_transaction(with_pair["id"], -50000,
                                             "هزینه", kind="debt")
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.balance(with_pair["id"]), -50000)

    def test_concurrent_double_void_exactly_one_wins(self):
        customer = self._seed_receipt(100000)
        barrier = threading.Barrier(2)
        results = {}

        def wrap(key):
            def run():
                barrier.wait()
                results[key] = crm.void_transaction(customer["id"], 0,
                                                    "دلیل " + key)
            return run

        threads = [threading.Thread(target=wrap(key))
                   for key in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        winners = [key for key, (_rev, errors) in results.items()
                   if errors == []]
        self.assertEqual(len(winners), 1, results)
        txs = self.customer_by_id(customer["id"])["transactions"]
        self.assertEqual(len(txs), 2)
        self.assertEqual(len([t for t in txs if t["kind"] == "void"]), 1)
        self.assertTrue(txs[0].get("voided_at"))
        events = [e for e in self._log_events()
                  if e.get("event") == "void_tx"]
        self.assertEqual(len(events), 1)


class VoidTxApiTests(ApiCase):
    def _seeded(self):
        customer, errors = crm.add_customer(
            {"full_name": "مشتری ابطال", "mobile": "09120000002"})
        self.assertEqual(errors, [], errors)
        _entry, errors = crm.add_transaction(customer["id"], 250000,
                                             "واریز", kind="receipt")
        self.assertEqual(errors, [], errors)
        return customer

    def test_void_tx_via_crm_customer_route(self):
        customer = self._seeded()
        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": customer["id"], "tx_index": "0",
            "reason": "ثبت اشتباه"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        # response shape mirrors the tx action: {"ok", "record"}
        self.assertEqual(body["record"]["kind"], "void")
        self.assertEqual(int(body["record"]["amount"]), -250000)
        txs = self.customer_by_id(customer["id"])["transactions"]
        self.assertEqual(len(txs), 2)
        self.assertTrue(txs[0].get("voided_at"))
        events = [e for e in self._log_events()
                  if e.get("event") == "void_tx"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("customer_id"), customer["id"])

    def test_void_tx_error_shape(self):
        customer = self._seeded()
        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": customer["id"], "tx_index": 7,
            "reason": "دلیل"})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"ok": False, "errors": ["تراکنش پیدا نشد"]})
        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": "nope", "tx_index": 0,
            "reason": "دلیل"})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"ok": False, "errors": ["مشتری پیدا نشد"]})


# ---------------------------------------------------------------------------
# void-aware aggregation + the label map
# ---------------------------------------------------------------------------

class VoidAwareAggregateTests(unittest.TestCase):
    def test_customer_balance_skips_inactive(self):
        customer = _customer("c1", "مشتری", [
            _tx("2026-09-10 09:00:00", -500000, "debt"),
            _tx("2026-09-11 09:00:00", 2000000, "receipt",
                voided_at="2026-09-12 09:00:00", void_reason="اشتباه"),
            _tx("2026-09-12 09:00:00", -2000000, "void"),
            _tx("2026-09-13 09:00:00", 300000, "receipt"),
        ])
        self.assertEqual(crm.customer_balance(customer), -200000)

    def test_voided_pair_changes_nothing_in_the_finance_report(self):
        txs = [
            _tx("2026-09-14 09:00:00", -500000, "debt"),
            _tx("2026-09-15 09:00:00", 24000000, "receipt"),
        ]
        voided = txs + [
            _tx("2026-09-15 10:00:00", 1000000, "receipt",
                voided_at="2026-09-15 11:00:00", void_reason="تکراری"),
            _tx("2026-09-15 11:00:00", -1000000, "void"),
        ]
        base = crm.finance_report_aggregate(
            _store([_customer("c1", "مشتری", txs)]), "daily", REF)
        with_pair = crm.finance_report_aggregate(
            _store([_customer("c1", "مشتری", voided)]), "daily", REF)
        self.assertEqual(with_pair["totals"], base["totals"])
        self.assertEqual(with_pair["kinds"], base["kinds"])
        self.assertEqual(with_pair["customers"], base["customers"])
        self.assertEqual(with_pair["days"], base["days"])
        # the transactions list is the real-money view: the in-range pair
        # (both rows land on 09-15) disappears; the pre-period debt stays
        # out of the list either way (it only feeds opening)
        self.assertEqual([t["at"] for t in with_pair["transactions"]],
                         ["2026-09-15 09:00:00"])
        self.assertEqual([int(t["amount"])
                          for t in with_pair["transactions"]], [24000000])
        self.assertNotIn("void", {t["kind"] for t in
                                  with_pair["transactions"]})

    def test_void_kind_label(self):
        self.assertEqual(crm.TX_KIND_LABELS["void"], "ابطال")

    def test_refund_skips_voided_fee_on_device_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,
                            {"IRICA_CRM_STORE":
                             os.path.join(tmp, "crm_store.json")}):
                crm.reset_cache()
                customer, errors = crm.add_customer(
                    {"full_name": "مشتری هزینه", "mobile": "09120000003"})
                self.assertEqual(errors, [], errors)
                device, errors = crm.add_device(
                    {"name": "Device 1", "serial1": synth_serial(1),
                     "customer_id": customer["id"]})
                self.assertEqual(errors, [], errors)
                data = crm.store_read()
                fee_index = next(
                    i for i, t in enumerate(
                        data["customers"][0]["transactions"])
                    if t["kind"] == "base_fee")
                _rev, errors = crm.void_transaction(customer["id"], fee_index,
                                                    "هزینه اشتباه")
                self.assertEqual(errors, [], errors)
                self.assertEqual(crm.customer_balance(
                    crm.store_read()["customers"][0]), 0)
                ok, errors = crm.delete_device(device["id"])
                self.assertEqual(errors, [], errors)
                self.assertTrue(ok)
                txs = crm.store_read()["customers"][0]["transactions"]
                self.assertEqual(
                    [t for t in txs if t["kind"] == "base_fee_refund"], [])
                self.assertEqual(crm.customer_balance(
                    crm.store_read()["customers"][0]), 0)


class FinanceBusinessHeaderTests(unittest.TestCase):
    def test_business_line_in_html_and_txt_when_set(self):
        data = _store([_customer("c1", "مشتری", [
            _tx("2026-09-15 09:00:00", 100000, "receipt")])],
            settings={"business_name": "فروشگاه نمونه",
                      "business_phone": "02112345678"})
        agg = crm.finance_report_aggregate(data, "daily", REF)
        self.assertEqual(agg["business"], {"name": "فروشگاه نمونه",
                                           "phone": "02112345678"})
        line = "فروشگاه نمونه؛ 02112345678"
        self.assertIn(line, crm.finance_report_html(agg))
        self.assertIn(line, crm.finance_report_txt(agg))

    def test_business_line_absent_when_unset(self):
        agg = crm.finance_report_aggregate(_store([]), "daily", REF)
        self.assertEqual(agg["business"], {"name": "", "phone": ""})
        self.assertNotIn("فروشگاه نمونه", crm.finance_report_html(agg))
        self.assertNotIn("فروشگاه نمونه", crm.finance_report_txt(agg))


# ---------------------------------------------------------------------------
# statement: pure builder
# ---------------------------------------------------------------------------

class StatementBuildTests(unittest.TestCase):
    LEDGER = [
        _tx("2026-09-10 08:00:00", -500000, "debt"),
        _tx("2026-09-12 09:00:00", -FEE, "base_fee",
            "هزینه ثبت رجیستری، گوشی", "d1"),
        _tx("2026-09-13 10:00:00", 10000000, "receipt", "واریز", "d1"),
        _tx("2026-09-14 09:00:00", 2000000, "receipt", "واریز تکراری",
            voided_at="2026-09-14 09:30:00", void_reason="تکراری"),
        _tx("2026-09-14 09:30:00", -2000000, "void",
            "ابطال — دلیل: تکراری"),
        _tx("2026-09-15 00:00:00", -50000),
        _tx("2026-09-16 09:00:00", 999000),
    ]

    def _statement(self, from_date=None, to_date=None, customer_id="c1"):
        return crm.customer_statement(
            _store([_customer("c1", "مشتری یک", self.LEDGER)],
                   settings={"business_name": "فروشگاه نمونه",
                             "business_phone": "02112345678"}),
            customer_id, from_date, to_date)[0]

    def test_math_with_both_bounds(self):
        stmt = self._statement(datetime.date(2026, 9, 12),
                               datetime.date(2026, 9, 15))
        self.assertEqual(stmt["from"], "2026-09-12")
        self.assertEqual(stmt["to"], "2026-09-15")
        self.assertEqual(stmt["from_fa"], jalali.to_fa("2026-09-12"))
        self.assertEqual(stmt["to_fa"], jalali.to_fa("2026-09-15"))
        self.assertEqual(stmt["customer"], {
            "id": "c1", "name": "مشتری یک", "mobile": "09121234567",
            "address": "تهران، خیابان نمونه"})
        self.assertEqual(stmt["business"], {"name": "فروشگاه نمونه",
                                            "phone": "02112345678"})
        self.assertEqual(stmt["opening"], -500000)
        self.assertEqual([r["at"] for r in stmt["rows"]], [
            "2026-09-12 09:00:00", "2026-09-13 10:00:00",
            "2026-09-14 09:00:00", "2026-09-14 09:30:00",
            "2026-09-15 00:00:00"])
        self.assertEqual(stmt["rows"][0]["debit"], FEE)
        self.assertEqual(stmt["rows"][0]["credit"], 0)
        self.assertEqual(stmt["rows"][0]["label"], "هزینه ثبت رجیستری")
        self.assertEqual(stmt["rows"][0]["voided"], False)
        self.assertEqual(stmt["rows"][1]["debit"], 0)
        self.assertEqual(stmt["rows"][1]["credit"], 10000000)
        # voided original + reversal: visible, flagged, null balance
        self.assertEqual(stmt["rows"][2]["voided"], True)
        self.assertEqual(stmt["rows"][2]["credit"], 2000000)
        self.assertEqual(stmt["rows"][3]["kind"], "void")
        self.assertEqual(stmt["rows"][3]["label"], "ابطال")
        self.assertEqual(stmt["rows"][3]["voided"], True)
        self.assertEqual(stmt["rows"][3]["debit"], 2000000)
        # running balances over ACTIVE rows only
        self.assertEqual(stmt["balances"],
                         [-500000 - FEE, -500000 - FEE + 10000000,
                          None, None, -500000 - FEE + 10000000 - 50000])
        self.assertEqual(stmt["totals"], {
            "debits": FEE + 50000, "credits": 10000000,
            "opening": -500000,
            "closing": -500000 + 10000000 - FEE - 50000})
        self.assertEqual(stmt["voided_count"], 2)

    def test_no_bounds(self):
        stmt = self._statement()
        self.assertEqual(stmt["from"], "")
        self.assertEqual(stmt["to"], "")
        self.assertEqual(stmt["opening"], 0)
        self.assertEqual(len(stmt["rows"]), 7)
        self.assertEqual(stmt["totals"]["closing"], -13551000)
        self.assertEqual(stmt["balances"][-1], -13551000)

    def test_only_from(self):
        stmt = self._statement(from_date=datetime.date(2026, 9, 12))
        self.assertEqual(stmt["opening"], -500000)
        self.assertEqual(len(stmt["rows"]), 6)
        self.assertEqual(stmt["totals"]["closing"], -13551000)

    def test_only_to(self):
        stmt = self._statement(to_date=datetime.date(2026, 9, 15))
        self.assertEqual(stmt["opening"], 0)
        self.assertEqual(len(stmt["rows"]), 6)
        self.assertEqual(stmt["totals"]["closing"], -14550000)

    def test_malformed_at_rows_never_render(self):
        ledger = [_tx("garbage", 700000), _tx("2026-09-15 09:00:00", 1000)]
        stmt = crm.customer_statement(
            _store([_customer("c1", "مشتری", ledger)]), "c1")[0]
        self.assertEqual([r["at"] for r in stmt["rows"]],
                         ["2026-09-15 09:00:00"])
        self.assertEqual(stmt["totals"]["credits"], 1000)

    def test_missing_customer(self):
        stmt, errors = crm.customer_statement(_store([]), "nope")
        self.assertIsNone(stmt)
        self.assertEqual(errors, ["مشتری پیدا نشد"])

    def test_generated_at_fields(self):
        stmt = self._statement()
        self.assertTrue(stmt["generated_at"])
        self.assertEqual(stmt["generated_at_fa"],
                         jalali.to_fa_datetime(stmt["generated_at"]))


class StatementRendererTests(unittest.TestCase):
    def _stmt(self):
        return crm.customer_statement(
            _store([_customer("c1", "مشتری <یک>", [
                _tx("2026-09-14 09:00:00", -FEE, "base_fee",
                    "هزینه ثبت رجیستری، گوشی", "d1"),
                _tx("2026-09-15 09:00:00", 10000000, "receipt", "واریز"),
                _tx("2026-09-15 10:00:00", 2000000, "receipt", "تکراری",
                    voided_at="2026-09-15 11:00:00", void_reason="تکراری"),
                _tx("2026-09-15 11:00:00", -2000000, "void",
                    "ابطال — دلیل: تکراری"),
            ])], settings={"business_name": "فروشگاه نمونه",
                           "business_phone": "02112345678"}),
            "c1", datetime.date(2026, 9, 14), datetime.date(2026, 9, 15))[0]

    def test_html_markers(self):
        html = crm.customer_statement_html(self._stmt())
        self.assertIn('<html dir="rtl" lang="fa">', html)
        self.assertIn("@font-face", html)
        self.assertIn("/fonts/Vazirmatn-var.woff2", html)
        self.assertIn("Vazirmatn", html)
        self.assertIn("صورت‌حساب مشتری", html)
        self.assertIn("فروشگاه نمونه؛ 02112345678", html)
        self.assertIn("نام: ", html)
        self.assertIn("موبایل: ", html)
        self.assertIn("آدرس: ", html)
        self.assertIn("از " + jalali.to_fa("2026-09-14"), html)
        self.assertIn("تا " + jalali.to_fa("2026-09-15"), html)
        for header in ("تاریخ", "شرح", "بدهکار", "بستانکار", "مانده"):
            self.assertIn("<th>" + header + "</th>", html)
        self.assertIn("جمع بدهکاری", html)
        self.assertIn("جمع دریافتی", html)
        self.assertIn("ماندهٔ اول", html)
        self.assertIn("ماندهٔ پایان", html)
        self.assertIn("تاریخ صدور: ", html)
        self.assertIn('class="void"', html)       # struck rows
        self.assertIn("text-decoration:line-through", html)
        # store data is escaped
        self.assertNotIn("مشتری <یک>", html)
        self.assertIn("مشتری &lt;یک&gt;", html)

    def test_html_without_business_header(self):
        stmt = crm.customer_statement(
            _store([_customer("c1", "مشتری", [])]), "c1")[0]
        html = crm.customer_statement_html(stmt)
        self.assertNotIn("فروشگاه نمونه", html)

    def test_txt_markers(self):
        text = crm.customer_statement_txt(self._stmt())
        self.assertTrue(text.startswith("صورت‌حساب مشتری"))
        self.assertIn("فروشگاه نمونه؛ 02112345678", text)
        self.assertIn("نام: مشتری <یک>", text)      # plain text: no escaping
        self.assertIn("ماندهٔ اول: ", text)
        self.assertIn("جمع بدهکاری: ", text)
        self.assertIn("جمع دریافتی: ", text)
        self.assertIn("ماندهٔ پایان: ", text)
        self.assertIn("تاریخ صدور: ", text)
        self.assertIn("(ابطال‌شده)", text)
        # the reversal row renders its ابطال label via the note
        self.assertIn("ابطال — دلیل: تکراری", text)


# ---------------------------------------------------------------------------
# statement: HTTP + Telegram
# ---------------------------------------------------------------------------

class StatementApiTests(ApiCase):
    def _seed(self):
        crm.store_write(_store(
            [_customer("c1", "مشتری صورت‌حساب", [
                _tx("2026-09-14 09:00:00", -FEE, "base_fee", "", "d1"),
                _tx("2026-09-15 09:00:00", 10000000, "receipt", "واریز"),
                _tx("2026-09-15 10:00:00", 2000000, "receipt", "تکراری",
                    voided_at="2026-09-15 11:00:00", void_reason="تکراری"),
                _tx("2026-09-15 11:00:00", -2000000, "void",
                    "ابطال — دلیل: تکراری"),
            ])],
            settings={"business_name": "فروشگاه نمونه",
                      "business_phone": "02112345678"}))

    def test_json_contract(self):
        self._seed()
        status, raw, headers = self._get(
            "/crm-statement?customer_id=c1&from=2026-09-14&to=2026-09-15")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        body = json.loads(raw)
        self.assertTrue(body["ok"])
        self.assertEqual(body["customer"]["id"], "c1")
        self.assertEqual(body["from"], "2026-09-14")
        self.assertEqual(body["to"], "2026-09-15")
        self.assertEqual(body["business"], {"name": "فروشگاه نمونه",
                                            "phone": "02112345678"})
        self.assertEqual(body["opening"], 0)
        self.assertEqual(len(body["rows"]), 4)
        self.assertEqual(body["rows"][2]["voided"], True)
        self.assertIsNone(body["balances"][2])
        self.assertEqual(body["rows"][3]["label"], "ابطال")
        self.assertIsNone(body["balances"][3])
        self.assertEqual(body["balances"][1], 10000000 - FEE)
        self.assertEqual(body["totals"], {"debits": FEE, "credits": 10000000,
                                          "opening": 0,
                                          "closing": 10000000 - FEE})
        self.assertEqual(body["voided_count"], 2)
        for key in ("generated_at", "generated_at_fa", "from_fa", "to_fa"):
            self.assertIn(key, body)

    def test_validation_errors(self):
        self._seed()
        status, raw, _ = self._get("/crm-statement")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw),
                         {"ok": False, "errors": ["مشتری پیدا نشد"]})
        status, raw, _ = self._get("/crm-statement?customer_id=nope")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"], ["مشتری پیدا نشد"])
        status, raw, _ = self._get("/crm-statement?customer_id=c1&from=bad")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"],
                         ["تاریخ from نامعتبر است؛ قالب درست: YYYY-MM-DD"])
        status, raw, _ = self._get(
            "/crm-statement?customer_id=c1&to=2026-02-30")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"],
                         ["تاریخ to نامعتبر است؛ قالب درست: YYYY-MM-DD"])
        status, raw, _ = self._get("/crm-statement?customer_id=c1&fmt=xml")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"],
                         ["مقدار fmt نامعتبر است"])

    def test_html_fmt(self):
        self._seed()
        status, raw, headers = self._get(
            "/crm-statement?customer_id=c1&from=2026-09-14&fmt=html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = raw.decode("utf-8")
        self.assertIn('dir="rtl"', html)
        self.assertIn("Vazirmatn", html)
        self.assertIn("صورت‌حساب مشتری", html)
        self.assertIn("فروشگاه نمونه؛ 02112345678", html)

    def test_txt_fmt_is_attachment_download(self):
        self._seed()
        status, raw, headers = self._get(
            "/crm-statement?customer_id=c1&fmt=txt")
        self.assertEqual(status, 200)
        self.assertIn("application/octet-stream",
                      headers.get("Content-Type", ""))
        self.assertIn("crm-statement.txt",
                      headers.get("Content-Disposition", ""))
        text = raw.decode("utf-8")
        self.assertTrue(text.startswith("صورت‌حساب مشتری"))
        self.assertIn("ماندهٔ پایان: ", text)

    def test_pdf_fmt_html_fallback_branch(self):
        self._seed()
        with patch.object(panel, "crm_html_to_pdf", return_value=None):
            status, raw, headers = self._get(
                "/crm-statement?customer_id=c1&fmt=pdf")
        self.assertEqual(status, 200)
        self.assertIn("html", headers.get("Content-Type", ""))
        text = raw.decode("utf-8")
        self.assertIn("print", text)
        self.assertIn("صورت‌حساب مشتری", text)

    def test_telegram_chunks_event_and_count(self):
        txs = [
            _tx("2026-09-15 %02d:%02d:00" % (i // 60, i % 60),
                1000000 + i, "receipt",
                "دریافتی شمارهٔ " + str(i) + " با توضیح نسبتاً بلند برای "
                "پرشدن پیام تلگرام و رسیدن به چند بخش")
            for i in range(80)]
        crm.store_write(_store([_customer("c1", "مشتری پرتراکنش", txs)]))
        sent = []
        # v1.10: the send path gained the optional extra destinations, so a
        # double has to mirror the real module's surface, not just the old one
        fake = type("tg", (), {
            "send_message": staticmethod(
                lambda text, timeout=20, actor=None, extra_chat_ids=None:
                sent.append(text) or True),
            "report_extra_chat_ids": staticmethod(lambda: None)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-statement-telegram",
                                      {"customer_id": "c1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["sent"], len(sent))
        self.assertGreater(len(sent), 1)
        for chunk in sent:
            self.assertLessEqual(len(chunk), 3900)
        expected = crm.customer_statement_txt(
            crm.customer_statement(crm.store_read(), "c1")[0])
        self.assertEqual("".join(sent), expected)
        events = [e for e in self._log_events()
                  if e.get("event") == "report_statement_sent"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("customer_id"), "c1")
        self.assertEqual(events[0].get("chunks"), len(sent))

    def test_telegram_partial_failure_is_502(self):
        txs = [_tx("2026-09-15 09:00:00", 1000000 + i, "receipt",
                   "توضیح بلند برای چند بخش شدن پیام " + "متن" * 30)
               for i in range(80)]
        crm.store_write(_store([_customer("c1", "مشتری", txs)]))
        calls = []

        def flaky(text, timeout=20, actor=None, extra_chat_ids=None):
            calls.append(text)
            return len(calls) == 1

        fake = type("tg", (), {
            "send_message": staticmethod(flaky),
            "report_extra_chat_ids": staticmethod(lambda: None)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-statement-telegram",
                                      {"customer_id": "c1"})
        self.assertGreater(len(calls), 1)
        self.assertEqual(status, 502)
        self.assertFalse(body["ok"])
        self.assertTrue(body["errors"][0].startswith("ارسال تلگرام کامل نشد: "))
        self.assertIn("بخش ارسال شد", body["errors"][0])
        self.assertIn(jalali.fa_digits(1), body["errors"][0])
        events = [e for e in self._log_events()
                  if e.get("event") == "report_statement_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("detail"), "1/%d" % len(calls))

    def test_telegram_exception_is_502_with_event(self):
        crm.store_write(_store([_customer("c1", "مشتری", [
            _tx("2026-09-15 09:00:00", 100000, "receipt")])]))

        def boom(text, timeout=20, actor=None, extra_chat_ids=None):
            raise RuntimeError("proxy down")

        fake = type("tg", (), {
            "send_message": staticmethod(boom),
            "report_extra_chat_ids": staticmethod(lambda: None)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-statement-telegram",
                                      {"customer_id": "c1"})
        self.assertEqual(status, 502)
        self.assertEqual(body["errors"], ["ارسال تلگرام ناموفق بود"])
        events = [e for e in self._log_events()
                  if e.get("event") == "report_statement_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("proxy down", str(events[0].get("detail")))

    def test_telegram_rejects_bad_params(self):
        status, body = self._post("/crm-statement-telegram", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"], ["مشتری پیدا نشد"])
        status, body = self._post("/crm-statement-telegram",
                                  {"customer_id": "c1", "from": "x"})
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"],
                         ["تاریخ from نامعتبر است؛ قالب درست: YYYY-MM-DD"])
        status, body = self._post("/crm-statement-telegram",
                                  {"customer_id": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"], ["مشتری پیدا نشد"])


# ---------------------------------------------------------------------------
# per-case invoice
# ---------------------------------------------------------------------------

class InvoiceTests(unittest.TestCase):
    def _invoice(self, device_id):
        customer = _customer("c1", "مشتری فاکتور", [
            _tx("2026-09-12 09:00:00", -FEE, "base_fee", "", "d1"),
            _tx("2026-09-13 09:00:00", 10000000, "receipt", "واریز", "d1"),
            _tx("2026-09-13 10:00:00", 2000000, "receipt", "تکراری", "d1",
                voided_at="2026-09-13 11:00:00", void_reason="تکراری"),
            _tx("2026-09-13 11:00:00", -2000000, "void",
                "ابطال — دلیل: تکراری", "d1"),
            _tx("2026-09-14 09:00:00", -FEE, "base_fee", "", "d2"),
            _tx("2026-09-15 09:00:00", 1000000, "receipt", "واریز آزاد"),
        ])
        devices = [_device("d1", "Device One", "c1", synth_serial(1)),
                   _device("d2", "Device Two", "c1", synth_serial(2),
                           serial2=synth_serial(3))]
        return crm.device_invoice(
            _store([customer], devices,
                   settings={"business_name": "فروشگاه نمونه",
                             "business_phone": "02112345678"}),
            device_id)

    def test_sections_math_and_balances(self):
        invoice, errors = self._invoice("d1")
        self.assertEqual(errors, [], errors)
        self.assertEqual([int(r["debit"]) for r in invoice["debits"]],
                         [FEE])
        self.assertEqual([int(r["credit"]) for r in invoice["credits"]],
                         [10000000])
        self.assertEqual(invoice["total_debits"], FEE)
        self.assertEqual(invoice["total_credits"], 10000000)
        self.assertEqual(invoice["case_balance"], 10000000 - FEE)
        # distinct: the whole-customer active balance includes d2 + free row
        self.assertEqual(invoice["customer_balance"],
                         10000000 - FEE - FEE + 1000000)
        self.assertEqual(invoice["inactive_count"], 2)
        self.assertEqual(invoice["device"]["id"], "d1")
        self.assertEqual(invoice["customer"]["id"], "c1")

    def test_second_device_sections(self):
        invoice, errors = self._invoice("d2")
        self.assertEqual(errors, [], errors)
        self.assertEqual(len(invoice["debits"]), 1)
        self.assertEqual(invoice["total_credits"], 0)
        self.assertEqual(invoice["case_balance"], -FEE)
        self.assertEqual(invoice["inactive_count"], 0)

    def test_missing_device(self):
        invoice, errors = self._invoice("nope")
        self.assertIsNone(invoice)
        self.assertEqual(errors, ["دستگاه پیدا نشد"])

    def test_device_without_customer_omits_customer_block(self):
        invoice, errors = crm.device_invoice(
            _store([_customer("c1", "مشتری",
                              [_tx("2026-09-15 09:00:00", 1000)])],
                   [_device("d9", "Lonely", "")]), "d9")
        self.assertEqual(errors, [], errors)
        self.assertIsNone(invoice["customer"])
        self.assertEqual(invoice["debits"], [])
        self.assertEqual(invoice["credits"], [])
        self.assertEqual(invoice["case_balance"], 0)
        self.assertEqual(invoice["customer_balance"], 0)
        html = crm.device_invoice_html(invoice)
        self.assertIn("فاکتور پرونده", html)
        self.assertNotIn("ماندهٔ کل حساب مشتری", html)
        self.assertNotIn("نام: ", html)

    def test_html_markers(self):
        invoice, _errors = self._invoice("d1")
        html = crm.device_invoice_html(invoice)
        self.assertIn('<html dir="rtl" lang="fa">', html)
        self.assertIn("Vazirmatn", html)
        self.assertIn("فاکتور پرونده", html)
        self.assertIn("شمارهٔ فاکتور: d1", html)
        self.assertIn("تاریخ صدور: ", html)
        self.assertIn("تاریخ ثبت پرونده: ", html)
        self.assertIn(jalali.to_fa("2026-09-12"), html)
        self.assertIn("نام: مشتری فاکتور", html)
        self.assertIn("موبایل: ", html)
        self.assertIn("آدرس: ", html)
        self.assertIn("نام دستگاه: Device One", html)
        self.assertIn("سریال‌ها: " + synth_serial(1), html)
        self.assertIn("بدهکاری‌های این پرونده", html)
        self.assertIn("دریافتی‌های این پرونده", html)
        self.assertIn("جمع بدهکاری", html)
        self.assertIn("جمع دریافتی", html)
        self.assertIn("ماندهٔ این پرونده", html)
        self.assertIn("ماندهٔ کل حساب مشتری", html)
        self.assertIn(jalali.fa_digits(2) +
                      " ردیف ابطال‌شده در محاسبه‌ها لحاظ نشده است.", html)
        # typo guard: the canonical label, never the mis-spelled variant
        self.assertIn("شمارهٔ فاکتور", html)
        self.assertNotIn("شمارهٔ فافتور", html)
        self.assertNotIn("فاختور", html)

    def test_html_without_business_header(self):
        invoice, _errors = self._invoice("d2")
        html = crm.device_invoice_html(invoice)
        self.assertIn("فاکتور پرونده", html)

    def test_serial2_rendered(self):
        invoice, _errors = self._invoice("d2")
        html = crm.device_invoice_html(invoice)
        self.assertIn(synth_serial(2) + " / " + synth_serial(3), html)


class InvoiceApiTests(ApiCase):
    def _seed(self):
        customer = _customer("c1", "مشتری فاکتور", [
            _tx("2026-09-12 09:00:00", -FEE, "base_fee", "", "d1"),
            _tx("2026-09-13 09:00:00", 5000000, "receipt", "واریز", "d1"),
        ])
        crm.store_write(_store([customer],
                               [_device("d1", "Device One", "c1",
                                        synth_serial(1))]))

    def test_html_fmt(self):
        self._seed()
        status, raw, headers = self._get("/crm-invoice?device_id=d1&fmt=html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = raw.decode("utf-8")
        self.assertIn("فاکتور پرونده", html)
        self.assertIn("شمارهٔ فاکتور: d1", html)
        self.assertIn("ماندهٔ کل حساب مشتری", html)

    def test_device_id_required(self):
        status, raw, _ = self._get("/crm-invoice")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw),
                         {"ok": False, "errors": ["دستگاه پیدا نشد"]})
        status, raw, _ = self._get("/crm-invoice?device_id=nope")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"], ["دستگاه پیدا نشد"])

    def test_bad_fmt_rejected(self):
        self._seed()
        status, raw, _ = self._get("/crm-invoice?device_id=d1&fmt=json")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"],
                         ["مقدار fmt نامعتبر است"])

    def test_pdf_fmt_html_fallback_branch(self):
        self._seed()
        with patch.object(panel, "crm_html_to_pdf", return_value=None):
            status, raw, headers = self._get("/crm-invoice?device_id=d1&fmt=pdf")
        self.assertEqual(status, 200)
        self.assertIn("html", headers.get("Content-Type", ""))
        text = raw.decode("utf-8")
        self.assertIn("print", text)
        self.assertIn("فاکتور پرونده", text)


# ---------------------------------------------------------------------------
# business settings
# ---------------------------------------------------------------------------

class BusinessSettingsTests(StoreCase):
    def test_defaults_are_empty_strings(self):
        settings = crm.store_read()["settings"]
        self.assertEqual(settings["business_name"], "")
        self.assertEqual(settings["business_phone"], "")

    def test_save_trim_and_readback(self):
        settings, errors = panel.crm_update_settings(
            {"business_name": "  فروشگاه الف  ",
             "business_phone": "02112345678"})
        self.assertEqual(errors, [], errors)
        self.assertEqual(settings["business_name"], "فروشگاه الف")
        self.assertEqual(settings["business_phone"], "02112345678")
        stored = crm.store_read()["settings"]
        self.assertEqual(stored["business_name"], "فروشگاه الف")
        self.assertEqual(stored["business_phone"], "02112345678")

    def test_length_cap(self):
        settings, errors = panel.crm_update_settings(
            {"business_name": "ن" * 81})
        self.assertIsNone(settings)
        self.assertEqual(errors, ["نام کسب‌وکار حداکثر ۸۰ کاراکتر باشد"])
        settings, errors = panel.crm_update_settings(
            {"business_phone": "0" * 81})
        self.assertEqual(errors, ["شمارهٔ تماس کسب‌وکار حداکثر ۸۰ کاراکتر باشد"])

    def test_empty_clears_and_absent_untouched(self):
        _settings, errors = panel.crm_update_settings(
            {"business_name": "فروشگاه الف", "business_phone": "0211"})
        self.assertEqual(errors, [], errors)
        settings, errors = panel.crm_update_settings({"business_name": ""})
        self.assertEqual(errors, [], errors)
        self.assertEqual(settings["business_name"], "")
        self.assertEqual(settings["business_phone"], "0211")
        settings, errors = panel.crm_update_settings({"watch_interval_hours": 3})
        self.assertEqual(errors, [], errors)
        self.assertEqual(settings["business_phone"], "0211")


class BusinessSettingsApiTests(ApiCase):
    def test_settings_post_and_state_readback(self):
        status, body = self._post("/crm-settings", {
            "business_name": "فروشگاه الف", "business_phone": "02112345678"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["settings"]["business_name"], "فروشگاه الف")
        self.assertEqual(body["settings"]["business_phone"], "02112345678")
        # the CRM state payload carries the same keys to the client
        status, raw, _ = self._get("/crm-state")
        self.assertEqual(status, 200)
        state = json.loads(raw)
        self.assertEqual(state["settings"]["business_name"], "فروشگاه الف")
        self.assertEqual(state["settings"]["business_phone"], "02112345678")

    def test_settings_rejects_overlong_value(self):
        status, body = self._post("/crm-settings", {
            "business_name": "ن" * 81})
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"],
                         ["نام کسب‌وکار حداکثر ۸۰ کاراکتر باشد"])


class ReviewFixTests(StoreCase):
    """Manager fixes after the H1/H2 review gates (v1.6.0)."""

    def test_void_fee_refund_interlock(self):
        # A live fee/refund pair nets zero; voiding ONE side would mint a
        # phantom credit/debit. Both directions are rejected while the
        # counterpart is active.
        customer = self.add_customer(1)
        cid = customer["id"]
        crm.store_write({"version": 1,
                         "customers": [_customer(cid, "م", [
                             _tx("2026-09-12 09:00:00", -FEE, "base_fee",
                                 "fee", "devX"),
                             _tx("2026-09-13 09:00:00", FEE,
                                 "base_fee_refund", "refund", "devX"),
                         ])], "owners": [], "identities": [],
                         "devices": [], "settings": {}})
        _, errs = crm.void_transaction(cid, 0, "اشتباه")
        self.assertTrue(errs and "جفت است" in errs[0], errs)
        _, errs = crm.void_transaction(cid, 1, "اشتباه")
        self.assertTrue(errs and "جفت است" in errs[0], errs)
        self.assertEqual(self.balance(cid), 0)

    def test_void_fee_without_live_refund_allowed(self):
        customer = self.add_customer(1)
        cid = customer["id"]
        crm.store_write({"version": 1,
                         "customers": [_customer(cid, "م", [
                             _tx("2026-09-12 09:00:00", -FEE, "base_fee",
                                 "fee", "devX"),
                         ])], "owners": [], "identities": [],
                         "devices": [], "settings": {}})
        _, errs = crm.void_transaction(cid, 0, "اشتباه")
        self.assertEqual(errs, [], errs)
        self.assertEqual(self.balance(cid), 0)

    def test_statement_html_shows_opening_value(self):
        # H1: the printed tfoot carried the «ماندهٔ اول» label but never its
        # value — the number must appear in the artifact.
        stmt, errs = crm.customer_statement(
            _store([_customer("c1", "م", [
                _tx("2026-09-01 09:00:00", -500000, "debt"),
                _tx("2026-09-15 09:00:00", 100000, "receipt"),
            ])]), "c1", from_date=REF)
        self.assertEqual(errs, [], errs)
        self.assertEqual(stmt["opening"], -500000)
        html = crm.customer_statement_html(stmt)
        self.assertIn("ماندهٔ اول: ", html)
        self.assertIn(crm._fa_money(-500000), html)

    def test_statement_counts_anomalies_and_notes_renderers(self):
        stmt, errs = crm.customer_statement(
            _store([_customer("c1", "م", [
                _tx("garbage", 7000),
                _tx("2026-09-15 09:00:00", 100000, "receipt"),
            ])]), "c1")
        self.assertEqual(errs, [], errs)
        self.assertEqual(stmt["anomalies"], 1)
        self.assertIn("۱ تراکنش با تاریخ نامعتبر نادیده‌گرفته شد",
                      crm.customer_statement_html(stmt))
        self.assertIn("۱ تراکنش با تاریخ نامعتبر نادیده‌گرفته شد",
                      crm.customer_statement_txt(stmt))
        clean, _ = crm.customer_statement(
            _store([_customer("c1", "م", [
                _tx("2026-09-15 09:00:00", 100000, "receipt"),
            ])]), "c1")
        self.assertEqual(clean["anomalies"], 0)
        self.assertNotIn("نادیده‌گرفته شد",
                         crm.customer_statement_html(clean))

    def test_voided_pair_straddling_period_bounds(self):
        # Original in-period (voided, struck, null balance), reversal lands
        # after `to`: no total may move, the original still renders.
        data = _store([_customer("c1", "م", [
            _tx("2026-09-14 09:00:00", 2000000, "receipt", "x", "devD",
                voided_at="2026-09-16 10:00:00",
                void_reason="تکراری"),
            _tx("2026-09-16 10:00:00", -2000000, "void",
                "ابطال، دلیل: تکراری", "devD"),
            _tx("2026-09-15 09:00:00", 300000, "receipt"),
        ])])
        stmt, errs = crm.customer_statement(
            data, "c1", from_date=datetime.date(2026, 9, 12),
            to_date=datetime.date(2026, 9, 15))
        self.assertEqual(errs, [], errs)
        self.assertEqual([r["voided"] for r in stmt["rows"]],
                         [True, False])
        self.assertEqual(stmt["balances"], [None, 300000])
        self.assertEqual(stmt["totals"]["credits"], 300000)
        self.assertEqual(stmt["totals"]["closing"], 300000)

    def test_customer_balance_skips_malformed_at_rows(self):
        # H2: customer_balance used to count unplaceable rows the statement
        # skipped, so the two surfaces disagreed. Both skip them now.
        customer = _customer("c1", "م", [
            _tx("garbage", 999999),
            _tx("2026-09-15 09:00:00", 1000, "receipt"),
        ])
        self.assertEqual(crm.customer_balance(customer), 1000)

    def test_invoice_skips_malformed_at_rows(self):
        device = _device("d1", "گوشی", customer_id="c1")
        data = _store([_customer("c1", "م", [
            _tx("garbage", -24000000, "base_fee", "fee", "d1"),
            _tx("2026-09-15 09:00:00", -24000000, "base_fee", "fee2", "d1"),
        ])], devices=[device])
        invoice, errs = crm.device_invoice(data, "d1")
        self.assertEqual(errs, [], errs)
        self.assertEqual(invoice["total_debits"], 24000000)
        self.assertEqual(len(invoice["debits"]), 1)
        self.assertEqual(invoice["inactive_count"], 1)


class ReviewFixApiTests(ApiCase):
    def _seed(self):
        crm.store_write(_store([_customer("c1", "مشتری", [
            _tx("2026-09-15 09:00:00", 1000000, "receipt"),
        ])]))

    def test_received_then_void_then_invoice(self):
        # H2 composition probe: the on-register receipt, once voided, must
        # leave the case balance without it and flag the inactive rows.
        self._seed()
        self._open_period()
        status, body = self._post("/crm-device", {
            "action": "add", "name": "گوشی ترکیبی",
            "serial1": synth_serial(1), "customer_id": "c1",
            "received_amount": 5000000})
        self.assertEqual(status, 200, body)
        device_id = body["record"]["id"]
        # rows: [0] seed receipt, [1] auto fee, [2] on-register receipt
        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": "c1", "tx_index": 2,
            "reason": "ثبت اشتباه"})
        self.assertEqual(status, 200, body)
        status, raw, _ = self._get(
            "/crm-invoice?device_id=" + device_id + "&fmt=html")
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        self.assertIn("فاکتور پرونده", html)
        status, raw, _ = self._get("/crm-statement?customer_id=c1&fmt=json")
        stmt = json.loads(raw)
        self.assertEqual(stmt["voided_count"], 2)
        self.assertEqual(stmt["totals"]["credits"], 1000000)

    def test_state_carries_void_fields_after_api_void(self):
        self._seed()
        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": "c1", "tx_index": 0,
            "reason": "تکراری"})
        self.assertEqual(status, 200, body)
        status, raw, _ = self._get("/crm-state")
        self.assertEqual(status, 200)
        state = json.loads(raw)
        cust = next(c for c in state["customers"] if c["id"] == "c1")
        txs = cust["transactions"]
        self.assertTrue(txs[0].get("voided_at"))
        self.assertEqual(txs[0].get("void_reason"), "تکراری")
        self.assertEqual(txs[1].get("kind"), "void")

    def test_pdf_success_branch_statement_and_invoice(self):
        self._seed()
        crm.store_write(_store(
            [_customer("c1", "مشتری", [])],
            devices=[dict(_device("d1", "گوشی", customer_id="c1"),
                          **{"stage": "to_payment"})]))
        fake_pdf = b"%PDF-fake-statement"
        with patch.object(panel, "crm_html_to_pdf",
                          return_value=fake_pdf) as converter:
            status, raw, headers = self._get(
                "/crm-statement?customer_id=c1&fmt=pdf")
            self.assertEqual(status, 200)
            self.assertIn("application/pdf", headers.get("Content-Type", ""))
            self.assertEqual(raw, fake_pdf)
            self.assertEqual(converter.call_count, 1)
            status, raw, headers = self._get(
                "/crm-invoice?device_id=d1&fmt=pdf")
            self.assertEqual(status, 200)
            self.assertIn("application/pdf", headers.get("Content-Type", ""))
            self.assertEqual(raw, fake_pdf)
            self.assertEqual(converter.call_count, 2)


if __name__ == "__main__":
    unittest.main()
