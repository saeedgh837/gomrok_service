# -*- coding: utf-8 -*-
"""v1.7 regression tests: fee override, inquiry-order fix, owner archive,
reporting periods, and the owner-save diagnostics."""
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

import crm
import panel

REF = datetime.date(2026, 9, 15)


def _store(devices=None, customers=None, periods=None, settings=None):
    data = crm._empty_store()
    data["devices"] = devices or []
    data["customers"] = customers or []
    data["periods"] = periods or []
    data["settings"] = dict(crm.DEFAULT_SETTINGS)
    data["settings"].update(settings or {})
    return data


class FeeOverrideTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.dir, "crm.json")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.old

    def _valid_national(self, base="123456789"):
        digs = [int(c) for c in base]
        total = sum(d * (10 - i) for i, d in enumerate(digs)) % 11
        return base + str(total if total < 2 else 11 - total)

    def test_add_device_uses_configured_fee_by_default(self):
        cust, _ = crm.add_customer({"full_name": "c1"})
        dev, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        self.assertEqual(errs, [])
        fee = [t for t in crm.store_read()["customers"][0]["transactions"]
               if t.get("kind") == "base_fee"]
        self.assertEqual(len(fee), 1)
        self.assertEqual(fee[0]["amount"], -crm.DEFAULT_SETTINGS["registry_fee_amount"])

    def test_add_device_honors_per_case_fee_override(self):
        cust, _ = crm.add_customer({"full_name": "c1"})
        dev, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"], "registry_fee_override": "10,000,000"})
        self.assertEqual(errs, [])
        fee = [t for t in crm.store_read()["customers"][0]["transactions"]
               if t.get("kind") == "base_fee" and t["device_id"] == dev["id"]]
        self.assertEqual(fee[0]["amount"], -10_000_000)

    def test_negative_fee_rejected(self):
        cust, _ = crm.add_customer({"full_name": "c1"})
        _, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"], "registry_fee_override": "-5"})
        self.assertEqual(errs, ["هزینه ثبت رجیستری باید عدد نامنفی باشد (تومان)"])

    def test_fee_zero_skips_booking(self):
        cust, _ = crm.add_customer({"full_name": "c1"})
        dev, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"], "registry_fee_override": "0"})
        self.assertEqual(errs, [])
        fee = [t for t in crm.store_read()["customers"][0]["transactions"]
               if t.get("kind") == "base_fee" and t["device_id"] == dev["id"]]
        self.assertEqual(fee, [])

    def test_blank_fee_override_charges_the_configured_amount(self):
        """An empty field means «no override» — NOT «charge nothing»: the
        configured amount must still be booked (v1.7 review)."""
        cust, _ = crm.add_customer({"full_name": "c1"})
        dev, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"], "registry_fee_override": ""})
        self.assertEqual(errs, [])
        fee = [t for t in crm.store_read()["customers"][0]["transactions"]
               if t.get("kind") == "base_fee" and t["device_id"] == dev["id"]]
        self.assertEqual(len(fee), 1)
        self.assertEqual(fee[0]["amount"],
                         -crm.DEFAULT_SETTINGS["registry_fee_amount"])

    def test_absent_fee_override_charges_the_configured_amount(self):
        cust, _ = crm.add_customer({"full_name": "c1"})
        dev, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        self.assertEqual(errs, [])
        fee = [t for t in crm.store_read()["customers"][0]["transactions"]
               if t.get("kind") == "base_fee" and t["device_id"] == dev["id"]]
        self.assertEqual(fee[0]["amount"],
                         -crm.DEFAULT_SETTINGS["registry_fee_amount"])

    def test_persian_thousands_separator_accepted_in_fee(self):
        """fa() renders amounts through toLocaleString('fa-IR'), which groups
        with U+066C «٬» — so the field the operator sees already carries it.
        A parser that rejects it refuses every submit (the H1 blocker)."""
        cust, _ = crm.add_customer({"full_name": "c1"})
        dev, errs = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"],
             "registry_fee_override": "۳۰٬۰۰۰٬۰۰۰"})
        self.assertEqual(errs, [])
        fee = [t for t in crm.store_read()["customers"][0]["transactions"]
               if t.get("kind") == "base_fee" and t["device_id"] == dev["id"]]
        self.assertEqual(fee[0]["amount"], -30_000_000)

    def test_amount_digits_strips_every_separator_the_panel_emits(self):
        # U+066C (fa-IR grouping), ASCII comma, Arabic comma, space, nbsp
        self.assertEqual(crm._amount_digits("۲۴٬۰۰۰٬۰۰۰"), "24000000")
        self.assertEqual(crm._amount_digits("24,000,000"), "24000000")
        self.assertEqual(crm._amount_digits("24،000،000"), "24000000")
        self.assertEqual(crm._amount_digits("24 000 000"), "24000000")
        self.assertEqual(crm._amount_digits("24 000 000"), "24000000")
        self.assertEqual(crm._amount_digits(""), "")
        self.assertEqual(crm._amount_digits(None), "")

    def test_amount_digits_keeps_a_sign_for_the_caller_to_reject(self):
        # only separators leave; a sign stays so _parse_fee_amount sees it and
        # errors instead of booking a positive charge
        self.assertEqual(crm._amount_digits("-500"), "-500")
        self.assertEqual(crm._amount_digits("۱۲-۳"), "12-3")

    def test_ledger_note_carries_the_one_name_and_no_em_dash(self):
        """v1.7 review F1/F2: every operator-visible spelling of the charge
        is «هزینه ثبت رجیستری» (escape-built here so the bytes are explicit),
        and the note joins the device name with a Persian comma — an em dash
        is not Persian prose punctuation."""
        name = "هزینه ثبت رجیستری"
        ezafe = "هزینهٔ ثبت رجیستری"
        self.assertEqual(crm.REGISTRY_FEE_NOTE, name)
        cust, _ = crm.add_customer({"full_name": "c1"})
        crm.add_device({"name": "A", "serial1": "356938035643809",
                        "customer_id": cust["id"]})
        note = [t for t in crm.store_read()["customers"][0]["transactions"]
                if t["kind"] == "base_fee"][0]["note"]
        self.assertTrue(note.startswith(name))
        self.assertIn("A", note)
        self.assertNotIn("—", note)
        self.assertNotIn(ezafe, note)


class OwnerDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.dir, "crm.json")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.old

    def test_bad_check_digit_explains_itself(self):
        _, errs = crm.add_owner({
            "first_name": "a", "last_name": "b",
            "national": "1234567890",  # wrong check digit
            "birth": "1375/04/29", "mobile": "09120000000"})
        self.assertIn("رقم کنترلی کد ملی اشتباه است", " ".join(errs))

    def test_wrong_length_national_explains_itself(self):
        _, errs = crm.add_owner({
            "first_name": "a", "last_name": "b", "national": "1234",
            "birth": "1375/04/29", "mobile": "09120000000"})
        self.assertIn("۱۰ رقم", " ".join(errs))


class OwnerArchiveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.dir, "crm.json")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.old

    def _full_case(self, serial="356938035643809"):
        cust, _ = crm.add_customer({"full_name": "c"})
        own, _ = crm.add_owner({
            "first_name": "p", "last_name": "q",
            "national": "1234567891", "birth": "1375/04/29",
            "mobile": "09120000000"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": serial,
             "customer_id": cust["id"]})
        for action, payload in [("stage_skip", {}), ("stage_skip", {}),
                                ("attach_owner", {"owner_id": own["id"]}),
                                ("handover", {}), ("to_payment", {}),
                                ("start_payment", {}), ("confirm_payment", {}),
                                ("deliver_code", {})]:
            crm.advance(dev["id"], action, **payload)
        return cust, own, dev

    def test_done_case_archives_owner(self):
        _, own, dev = self._full_case()
        data = crm.store_read()
        self.assertEqual(crm.archived_owner_ids(data), {own["id"]})
        rows = crm.archived_owners(data)
        self.assertEqual(len(rows), 1)
        # the row is what the archive tab renders: the passenger, its case,
        # the customer name and the delivery stamp — not just a count
        row = rows[0]
        self.assertEqual(row["owner"]["id"], own["id"])
        self.assertEqual(row["device"]["id"], dev["id"])
        self.assertEqual(row["customer_name"], "c")
        self.assertTrue(row["done_at"])
        self.assertTrue(row["code_delivered_at"])

    def test_rollback_unarchives_owner(self):
        _, own, dev = self._full_case()
        crm.advance(dev["id"], "stage_back")  # done -> payment_done
        self.assertEqual(crm.archived_owner_ids(crm.store_read()), set())

    def test_archive_lists_one_row_per_passenger(self):
        """A legacy store can carry the same passenger on two delivered
        cases; the badge counts passengers, so the list must not show that
        passenger twice (v1.7 review finding 6)."""
        cust, _ = crm.add_customer({"full_name": "c"})
        own, _ = crm.add_owner({
            "first_name": "p", "last_name": "q",
            "national": "1234567891", "birth": "1375/04/29",
            "mobile": "09120000000"})
        first, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        second, _ = crm.add_device(
            {"name": "B", "serial1": "356938035643810",
             "customer_id": cust["id"]})
        data = crm.store_read()
        # hand both cases to the same passenger and stamp them delivered
        for device, done in ((first, "2026-09-01 10:00:00"),
                             (second, "2026-09-02 10:00:00")):
            row = next(r for r in data["devices"] if r["id"] == device["id"])
            row["owner_id"] = own["id"]
            row["stage"] = "done"
            row["done_at"] = done
        crm.store_write(data)
        rows = crm.archived_owners(crm.store_read())
        self.assertEqual(len(rows), 1)
        # the newest completed case is the one listed
        self.assertEqual(rows[0]["device"]["id"], second["id"])

    def test_closed_case_not_archived(self):
        cust, _ = crm.add_customer({"full_name": "c"})
        own, _ = crm.add_owner({
            "first_name": "p", "last_name": "q",
            "national": "1234567891", "birth": "1375/04/29",
            "mobile": "09120000000"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        # walk to the handover without delivering, then close
        for action, payload in [("stage_skip", {}), ("stage_skip", {}),
                                ("attach_owner", {"owner_id": own["id"]}),
                                ("handover", {}),
                                ("close_case", {"reason": "لغو مشتری"})]:
            crm.advance(dev["id"], action, **payload)
        self.assertEqual(crm.archived_owner_ids(crm.store_read()), set())


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.dir, "crm.json")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.old

    def test_open_close_and_numbering(self):
        p1, errs = crm.open_period()
        self.assertEqual(errs, [])
        self.assertEqual(p1["number"], 1)
        p2, errs = crm.open_period()
        self.assertIn("یک دورهٔ باز از قبل وجود دارد", " ".join(errs))
        summary, errs = crm.close_period(p1["id"], actor="saeed")
        self.assertEqual(errs, [])
        self.assertEqual(summary["devices"], 0)
        # closing twice fails
        _, errs = crm.close_period(p1["id"])
        self.assertEqual(errs, ["این دوره قبلاً بسته شده است"])
        p3, errs = crm.open_period()
        self.assertEqual(p3["number"], 2)

    def test_devices_join_open_period_and_no_period_after_close(self):
        p, _ = crm.open_period()
        cust, _ = crm.add_customer({"full_name": "c"})
        d, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        self.assertEqual(d["period_id"], p["id"])
        crm.close_period(p["id"])
        d2, _ = crm.add_device(
            {"name": "B", "serial1": "356938035643810",
             "customer_id": cust["id"]})
        self.assertEqual(d2["period_id"], "")

    def test_period_report_scopes_ledger_and_warns_while_open(self):
        p, _ = crm.open_period()
        cust, _ = crm.add_customer({"full_name": "c"})
        crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        agg = crm.finance_report_aggregate(crm.store_read(), "period", p["id"])
        self.assertEqual(agg["period"]["id"], p["id"])
        # the registry fee landed in the period totals
        self.assertEqual(agg["totals"]["debits"],
                         -crm.DEFAULT_SETTINGS["registry_fee_amount"])
        self.assertNotEqual(agg["open_warning"], "")
        crm.close_period(p["id"])
        agg2 = crm.finance_report_aggregate(crm.store_read(), "period", p["id"])
        self.assertEqual(agg2["open_warning"], "")

    def test_period_report_excludes_other_periods(self):
        p1, _ = crm.open_period()
        cust, _ = crm.add_customer({"full_name": "c"})
        crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        crm.close_period(p1["id"])
        p2, _ = crm.open_period()
        crm.add_device(
            {"name": "B", "serial1": "356938035643810",
             "customer_id": cust["id"]})
        agg1 = crm.finance_report_aggregate(crm.store_read(), "period", p1["id"])
        agg2 = crm.finance_report_aggregate(crm.store_read(), "period", p2["id"])
        # period 1 totals are frozen at its own fee; period 2 holds the new one
        self.assertEqual(agg1["totals"]["debits"],
                         -crm.DEFAULT_SETTINGS["registry_fee_amount"])
        self.assertEqual(agg2["totals"]["debits"],
                         -crm.DEFAULT_SETTINGS["registry_fee_amount"])
        self.assertEqual(agg1["period"]["devices"], 1)
        self.assertEqual(agg2["period"]["devices"], 1)

    def test_period_report_counts_unlinked_rows_inside_its_window(self):
        """A manual receipt/debt carries no device_id. Scoping the period
        report by case link alone would drop the cycle's hand-recorded cash,
        so an unlinked row counts when its date falls inside the window."""
        today = datetime.date.today()
        period = {"id": "per1", "number": 1, "label": "دورهٔ اول",
                  "opened_at": today.isoformat() + " 08:00:00",
                  "closed_at": ""}
        cust = {"id": "c1", "full_name": "مشتری", "transactions": [
            {"at": today.isoformat() + " 09:00:00", "amount": -1_000_000,
             "kind": "debt", "device_id": "d1"},
            {"at": today.isoformat() + " 10:00:00", "amount": 500_000,
             "kind": "receipt", "device_id": ""},
            {"at": (today - datetime.timedelta(days=10)).isoformat()
             + " 10:00:00", "amount": 700_000, "kind": "receipt",
             "device_id": ""},
            {"at": today.isoformat() + " 11:00:00", "amount": -2_000_000,
             "kind": "debt", "device_id": "d2"},
        ]}
        crm.store_write(_store(
            devices=[{"id": "d1", "period_id": "per1", "stage": "new"},
                     {"id": "d2", "period_id": "per2", "stage": "new"}],
            customers=[cust], periods=[period]))
        agg = crm.finance_report_aggregate(crm.store_read(), "period", "per1")
        # the case-linked row and the in-window manual receipt, nothing else
        self.assertEqual(agg["totals"]["debits"], -1_000_000)
        self.assertEqual(agg["totals"]["receipts"], 500_000)
        self.assertEqual(agg["totals"]["count"], 2)

    def test_period_report_names_its_cycle_and_drops_the_daily_label(self):
        p, _ = crm.open_period()
        cust, _ = crm.add_customer({"full_name": "c"})
        crm.add_device({"name": "A", "serial1": "356938035643809",
                        "customer_id": cust["id"]})
        agg = crm.finance_report_aggregate(crm.store_read(), "period", p["id"])
        html = crm.finance_report_html(agg)
        text = crm.finance_report_txt(agg)
        # the heading names the cycle, and the daily/weekly sub-label is gone
        self.assertIn("گزارش مالی مشتریان: " + p["label"], html)
        self.assertNotIn("روزانه", html)
        self.assertNotIn("هفتگی", html)
        self.assertTrue(text.startswith(
            "گزارش مالی مشتریان: " + p["label"] + " (باز)"))
        self.assertNotIn("روزانه", text)
        # the counting rule is disclosed in both renderers
        rule = "تراکنش‌های بدون پرونده بر اساس تاریخ شمرده شده‌اند."
        self.assertIn(rule, html)
        self.assertIn(rule, text)
        crm.close_period(p["id"])
        closed = crm.finance_report_aggregate(crm.store_read(), "period", p["id"])
        self.assertTrue(crm.finance_report_txt(closed).startswith(
            "گزارش مالی مشتریان: " + p["label"] + " (بسته)"))

    def test_unknown_period_id_is_rejected_not_a_crash(self):
        crm.open_period()
        with self.assertRaises(ValueError):
            crm.finance_report_aggregate(crm.store_read(), "period", "nope")

    def test_closed_period_never_takes_new_devices(self):
        """A stale current_period_id must not re-open a closed period."""
        p, _ = crm.open_period()
        crm.close_period(p["id"])
        cust, _ = crm.add_customer({"full_name": "c"})
        dev, _ = crm.add_device({"name": "A", "serial1": "356938035643809",
                                 "customer_id": cust["id"]})
        self.assertEqual(dev["period_id"], "")
        data = crm.store_read()
        # even if the pointer is restored by hand, current_period refuses it
        data["settings"]["current_period_id"] = p["id"]
        self.assertIsNone(crm.current_period(data))

    def test_no_open_period_yields_no_open_warning(self):
        self.assertEqual(crm.period_open_warning(crm.store_read(), None), "")

    def test_period_report_keeps_a_late_payment_on_its_own_case(self):
        """A case of this period paid after the books were closed still
        belongs to the cycle that owns the case (v1.7 review finding 1):
        dropping it made closing a period erase money the same report had
        already shown. Rows scoped by their own date keep the window rule."""
        today = datetime.date.today()
        opened = (today - datetime.timedelta(days=10)).isoformat()
        closed = (today - datetime.timedelta(days=3)).isoformat()
        late = (today - datetime.timedelta(days=1)).isoformat()
        period = {"id": "per1", "number": 1, "label": "p1",
                  "opened_at": opened + " 08:00:00",
                  "closed_at": closed + " 18:00:00"}
        cust = {"id": "c1", "full_name": "c", "transactions": [
            {"at": late + " 12:00:00", "amount": 5_000_000,
             "kind": "receipt", "device_id": "d1"},
            {"at": late + " 13:00:00", "amount": 9_000_000,
             "kind": "receipt", "device_id": "d2"},
            {"at": late + " 14:00:00", "amount": 7_000_000,
             "kind": "receipt", "device_id": ""},
        ]}
        crm.store_write(_store(
            devices=[{"id": "d1", "period_id": "per1", "stage": "new"},
                     {"id": "d2", "period_id": "per2", "stage": "new"}],
            customers=[cust], periods=[period]))
        agg = crm.finance_report_aggregate(crm.store_read(), "period", "per1")
        self.assertEqual(agg["totals"]["receipts"], 5_000_000)
        self.assertEqual(agg["totals"]["count"], 1)

    def test_period_anomaly_count_is_scoped_to_the_period(self):
        """A malformed row belonging to another cycle's case must not inflate
        this period's warning."""
        today = datetime.date.today().isoformat()
        period = {"id": "per1", "number": 1, "label": "p1",
                  "opened_at": today + " 08:00:00", "closed_at": ""}
        cust = {"id": "c1", "full_name": "c", "transactions": [
            {"at": "not-a-date", "amount": 1, "kind": "debt",
             "device_id": "d2"},
            {"at": "not-a-date", "amount": 2, "kind": "debt",
             "device_id": "d1"},
            {"at": "nope", "amount": 3, "kind": "debt", "device_id": ""},
            "a row that is not a dict at all",
        ]}
        crm.store_write(_store(
            devices=[{"id": "d1", "period_id": "per1", "stage": "new"},
                     {"id": "d2", "period_id": "per2", "stage": "new"}],
            customers=[cust], periods=[period]))
        agg = crm.finance_report_aggregate(crm.store_read(), "period", "per1")
        # Only the row on a case of this period is counted: d2's belongs to
        # another cycle, and an unlinked row (or a non-dict one) carries no
        # case to attribute the corruption to — daily/weekly reports, where
        # every row is in scope, still surface all four.
        self.assertEqual(agg["anomalies"], 1)
        daily = crm.finance_report_aggregate(
            crm.store_read(), "daily", datetime.date.today().isoformat())
        self.assertEqual(daily["anomalies"], 4)

    def test_open_period_adopts_an_unclosed_orphan(self):
        """A store whose open-period pointer was lost (a restored or
        hand-edited file) must re-adopt that period instead of opening a
        second one: two overlapping open periods would let a manual receipt
        count in both of their reports."""
        data = _store(periods=[{"id": "per1", "number": 1, "label": "p1",
                                "opened_at": "2026-09-01 08:00:00",
                                "closed_at": ""}])
        data["settings"]["current_period_id"] = ""
        crm.store_write(data)
        period, errs = crm.open_period(actor="saeed")
        self.assertEqual(errs, [])
        self.assertEqual(period["id"], "per1")
        after = crm.store_read()
        self.assertEqual(len(after["periods"]), 1)
        self.assertEqual(after["settings"]["current_period_id"], "per1")

    def test_a_bad_report_reference_is_its_own_error_class(self):
        """The panel answers 400 only for ReportRefError; any other
        ValueError is a broken store record and must surface as a 500."""
        crm.open_period()
        self.assertTrue(issubclass(crm.ReportRefError, ValueError))
        with self.assertRaises(crm.ReportRefError):
            crm.finance_report_aggregate(crm.store_read(), "period", "nope")
        with self.assertRaises(crm.ReportRefError):
            crm.finance_report_aggregate(crm.store_read(), "monthly", "")


class InquiryOrderTests(unittest.TestCase):
    """The v1.7 fix: starting a hamta run must not clobber a finished gomrok
    verdict (and vice versa)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old_store = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.dir, "crm.json")
        os.environ["IRICA_PROFILES_PATH"] = os.path.join(self.dir, "p.json")
        os.environ["IRICA_INPUTS_PATH"] = os.path.join(self.dir, "i.json")
        # hygiene: the queue fixtures must not touch the live estelam store
        estelam_patcher = patch.object(
            panel, "ESTELAM_PATH", os.path.join(self.dir, "est.json"))
        estelam_patcher.start()
        self.addCleanup(estelam_patcher.stop)

    def tearDown(self):
        for key, value in (("IRICA_CRM_STORE", self.old_store),
                           ("IRICA_PROFILES_PATH", None),
                           ("IRICA_INPUTS_PATH", None)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_hamta_start_preserves_gomrok_verdict(self):
        cust, _ = crm.add_customer({"full_name": "c"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        # gomrok finished healthy...
        panel.estelam_save({"devices": [{
            "id": dev["id"], "status": "pending", "result": "",
            "hamta_status": "pending", "hamta_result": ""}]})
        panel.estelam_set_device(dev["id"], status="healthy", result="ok")
        # ...then a hamta run is queued
        panel.crm_sync_queue(dev["id"], service="hamta")
        row = panel.estelam_load()["devices"][0]
        self.assertEqual(row["status"], "healthy")
        self.assertEqual(row["hamta_status"], "pending")

    def test_gomrok_start_resets_only_gomrok_slot(self):
        cust, _ = crm.add_customer({"full_name": "c"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        panel.estelam_save({"devices": [{
            "id": dev["id"], "status": "healthy", "result": "ok",
            "hamta_status": "good", "hamta_result": "x"}]})
        panel.crm_sync_queue(dev["id"], service="gomrok")
        row = panel.estelam_load()["devices"][0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["hamta_status"], "good")

    def test_fresh_hamta_row_does_not_invent_gomrok_result(self):
        cust, _ = crm.add_customer({"full_name": "c"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        panel.estelam_save({"devices": []})
        panel.crm_sync_queue(dev["id"], service="hamta")
        row = panel.estelam_load()["devices"][0]
        self.assertEqual(row["status"], "")
        self.assertEqual(row["hamta_status"], "pending")

    def test_service_all_requeues_both_slots(self):
        """«استعلام گمرک و همتا» runs both robots, so both finished verdicts
        are legitimately stale and must be re-queued — resetting only one
        would make the other robot skip the device."""
        cust, _ = crm.add_customer({"full_name": "c"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        panel.estelam_save({"devices": [{
            "id": dev["id"], "status": "healthy", "result": "ok",
            "hamta_status": "good", "hamta_result": "x"}]})
        panel.crm_sync_queue(dev["id"], service="all")
        row = panel.estelam_load()["devices"][0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["result"], "")
        self.assertEqual(row["hamta_status"], "pending")
        self.assertEqual(row["hamta_result"], "")

    def test_unknown_service_falls_back_to_the_gomrok_slot(self):
        cust, _ = crm.add_customer({"full_name": "c"})
        dev, _ = crm.add_device(
            {"name": "A", "serial1": "356938035643809",
             "customer_id": cust["id"]})
        panel.estelam_save({"devices": [{
            "id": dev["id"], "status": "healthy", "result": "ok",
            "hamta_status": "good", "hamta_result": "x"}]})
        panel.crm_sync_queue(dev["id"], service="")
        row = panel.estelam_load()["devices"][0]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["hamta_status"], "good")


class FinanceReportApiTests(unittest.TestCase):
    """HTTP level for the v1.7 routes: the period report, the period
    open/close endpoints, and the do_GET safety net (a route exception used
    to abort the socket and surface as «TypeError: Failed to fetch»)."""

    def setUp(self):
        import json
        import threading
        from unittest.mock import patch
        import panel
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.store = os.path.join(self.tmp.name, "crm_store.json")
        self._patches = [
            patch.object(panel, "PROFILES_PATH", profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH",
                         os.path.join(self.tmp.name, "estelam.json")),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)
        from lab.tests import _auth_harness
        _auth_harness.install_auth_cookie(self, _auth_harness.seed_test_users())
        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        # registered after server_close so it runs first (LIFO): closing the
        # socket under a live serve_forever loop prints a WinError 10038 from
        # the selector thread into the test output.
        self.addCleanup(self.httpd.shutdown)

    def _get(self, path):
        import json
        import urllib.error
        import urllib.request
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _post(self, path, payload):
        import json
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_period_report_route_serves_the_cycle(self):
        status, opened = self._post("/crm-period-open", {"label": ""})
        self.assertEqual(status, 200)
        period_id = opened["period"]["id"]
        status, body = self._get(
            "/crm-finance-report?mode=period&ref=%s&fmt=json" % period_id)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["period"]["id"], period_id)
        self.assertNotEqual(body["open_warning"], "")

    def test_unknown_period_id_answers_400_instead_of_dropping(self):
        status, body = self._get(
            "/crm-finance-report?mode=period&ref=nope&fmt=json")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("پیدا نشد", " ".join(body["errors"]))

    def test_route_exception_is_answered_not_dropped(self):
        """The GET safety net: an unexpected exception must come back as a
        JSON error, never as a closed connection."""
        import contextlib
        import io
        from unittest.mock import patch
        with patch.object(crm, "finance_report_aggregate",
                          side_effect=RuntimeError("boom")):
            with contextlib.redirect_stderr(io.StringIO()):
                status, body = self._get(
                    "/crm-finance-report?mode=daily&fmt=json")
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertIn("خطای سرور", " ".join(body["errors"]))

    def test_period_close_route_reports_open_cases(self):
        status, opened = self._post("/crm-period-open", {"label": ""})
        period_id = opened["period"]["id"]
        cust, _ = crm.add_customer({"full_name": "c"})
        crm.add_device({"name": "A", "serial1": "356938035643809",
                        "customer_id": cust["id"]})
        status, body = self._post("/crm-period-close",
                                  {"period_id": period_id})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["devices"], 1)
        self.assertEqual(body["summary"]["open_cases"], 1)
        # closing twice is rejected, not fatal
        status, body = self._post("/crm-period-close",
                                  {"period_id": period_id})
        self.assertEqual(status, 400)
        self.assertIn("قبلاً بسته شده است", " ".join(body["errors"]))

    def test_settings_saves_a_fee_carrying_the_persian_separator(self):
        """The settings field is pre-filled by fa(), so a re-save arrives as
        «۳۰٬۰۰۰٬۰۰۰»; the old strip-list omitted U+066C and rejected it."""
        status, body = self._post("/crm-settings",
                                  {"registry_fee_amount": "۳۰٬۰۰۰٬۰۰۰"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["settings"]["registry_fee_amount"], 30_000_000)
        self.assertEqual(crm.store_read()["settings"]["registry_fee_amount"],
                         30_000_000)

    def test_settings_rejects_a_negative_fee(self):
        status, body = self._post("/crm-settings",
                                  {"registry_fee_amount": "-1"})
        self.assertEqual(status, 400)
        self.assertIn("عدد نامنفی", " ".join(body["errors"]))

    def test_broken_report_record_answers_500_not_400(self):
        """Only a stale *reference* is the caller's mistake (400). A
        ValueError out of a broken store record is a 500, so the operator is
        not told to re-read a list that is itself unreadable (review F3)."""
        import contextlib
        import io
        from unittest.mock import patch
        with patch.object(crm, "finance_report_aggregate",
                          side_effect=ValueError("bad record")), \
                patch.object(crm, "crm_log"), \
                contextlib.redirect_stderr(io.StringIO()):
            status, body = self._get("/crm-finance-report?mode=daily&fmt=json")
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertIn("خطای سرور", " ".join(body["errors"]))


class PanelMarkupTests(unittest.TestCase):
    """Page-level invariants the operator sees (v1.7 review findings 2/7)."""

    @classmethod
    def setUpClass(cls):
        import panel
        cls.page = panel.CRM_PAGE

    def test_both_amount_fields_share_one_input_class(self):
        """The received amount and the per-case fee are the same kind of
        input, so they must accept the same separators: a field the panel
        pre-fills through fa() carries U+066C, a paste can carry NBSP."""
        self.assertEqual(self.page.count("var AMOUNT_RAW_RE="), 1)
        self.assertIn("if(!AMOUNT_RAW_RE.test(recvRaw)){", self.page)
        self.assertIn("if(!AMOUNT_RAW_RE.test(feeRaw)){", self.page)
        # no field keeps its own private copy of the class
        self.assertEqual(self.page.count("}/.test(recvRaw)"), 0)
        self.assertEqual(self.page.count("}/.test(feeRaw)"), 0)

    def test_period_bar_ignores_a_closed_period(self):
        """A stored pointer can outlive its period; the bar must not offer
        «بستن دوره» for one that is already closed."""
        self.assertIn("&&!p.closed_at", self.page)

    def test_the_charge_has_one_name_in_the_page(self):
        ezafe = "هزینهٔ"
        self.assertNotIn(ezafe, self.page)
        self.assertIn("هزینه ثبت رجیستری", self.page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
