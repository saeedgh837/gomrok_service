"""v1.5 — finance report: aggregation, renderers, endpoints, Telegram.

Covers the frozen /crm-finance-report contract (the reports-tab UI builds
against it): daily/weekly aggregation over the per-customer ledgers, the
Persian renderers (self-contained RTL HTML print doc + plain text), query
validation with the canonical Persian errors, and the Telegram fanout with
<=3900-char chunks and the report_finance_sent audit event.
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

# Fixed Gregorian anchors (verified: 2026-09-12 is a Saturday).
REF = datetime.date(2026, 9, 15)        # سه‌شنبه, mid-week
SAT = datetime.date(2026, 9, 12)        # شنبه
FRI = datetime.date(2026, 9, 18)        # جمعه

BAD_MODE = "مقدار mode نامعتبر است"
BAD_REF = "تاریخ ref نامعتبر است؛ قالب درست: YYYY-MM-DD"
BAD_FMT = "مقدار fmt نامعتبر است"


def _tx(at, amount, kind="", note="", device_id=""):
    return {"at": at, "amount": amount, "note": note, "kind": kind,
            "image": "", "device_id": device_id}


def _customer(cid, name, txs):
    return {"id": cid, "full_name": name, "address": "", "mobile": "",
            "telegram_id": "", "instagram_id": "", "notes": "",
            "transactions": txs, "created": "2026-01-01 00:00:00",
            "updated": "2026-01-01 00:00:00"}


def _store(customers):
    return {"version": 1, "customers": customers, "owners": [],
            "identities": [], "devices": [], "settings": {}}


class FinanceAggregateTests(unittest.TestCase):
    """Pure aggregation over synthetic store dicts (no file I/O)."""

    def test_daily_math(self):
        data = _store([_customer("c1", "مشتری یک", [
            _tx("2026-09-10 08:00:00", -500000, "debt"),          # opening
            _tx("2026-09-15 00:00:00", -24000000, "base_fee"),    # in
            _tx("2026-09-15 10:00:00", 24000000, "receipt"),      # in
            _tx("2026-09-15 11:00:00", 100000),                   # in, bare
            _tx("2026-09-15 12:00:00", -50000),                   # in, bare
            _tx("2026-09-16 09:00:00", 999000),                   # future
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        self.assertEqual(agg["mode"], "daily")
        self.assertEqual((agg["from"], agg["to"]), ("2026-09-15",) * 2)
        row = agg["customers"][0]
        self.assertEqual(row["id"], "c1")
        self.assertEqual(row["name"], "مشتری یک")
        self.assertEqual(row["opening"], -500000)
        self.assertEqual(row["debits"], -24050000)
        self.assertEqual(row["credits"], 24100000)
        self.assertEqual(row["receipts"], 24000000)
        self.assertEqual(row["closing"], -450000)
        self.assertEqual(row["count"], 4)
        self.assertEqual(agg["totals"], {
            "opening": -500000, "debits": -24050000, "credits": 24100000,
            "receipts": 24000000, "closing": -450000, "count": 4,
            "debtors_total": -450000, "creditors_total": 0})
        self.assertEqual(agg["days"], [])      # daily never has days
        self.assertEqual(agg["anomalies"], 0)

    def test_base_fee_debit_refund_credit_not_receipt(self):
        data = _store([_customer("c1", "مشتری", [
            _tx("2026-09-15 08:00:00", -24000000, "base_fee"),
            _tx("2026-09-15 09:00:00", 24000000, "base_fee_refund"),
            _tx("2026-09-15 10:00:00", 500000, "receipt"),
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        row = agg["customers"][0]
        self.assertEqual(row["debits"], -24000000)      # base_fee is a debit
        self.assertEqual(row["credits"], 24500000)      # refund IS a credit
        self.assertEqual(row["receipts"], 500000)       # ...but NOT a receipt
        self.assertEqual(row["opening"], 0)
        self.assertEqual(row["closing"], 500000)
        kinds = {k["kind"]: k for k in agg["kinds"]}
        self.assertEqual(kinds["base_fee"]["label"], "هزینه ثبت رجیستری")
        self.assertEqual(kinds["base_fee"]["amount"], -24000000)
        self.assertEqual(kinds["base_fee"]["count"], 1)
        self.assertEqual(kinds["base_fee_refund"]["label"],
                         "بازگشت هزینه ثبت رجیستری")
        self.assertEqual(kinds["base_fee_refund"]["amount"], 24000000)
        self.assertEqual(kinds["receipt"]["label"], "دریافتی")
        # sorted by |amount| desc; the 24M pair leads, receipt trails
        self.assertEqual(agg["kinds"][-1]["kind"], "receipt")
        self.assertEqual(set(kinds), {"base_fee", "base_fee_refund", "receipt"})

    def test_bare_tx_buckets_by_sign(self):
        data = _store([_customer("c1", "مشتری", [
            _tx("2026-09-15 08:00:00", 700000),
            _tx("2026-09-15 09:00:00", -200000, "tx"),
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        row = agg["customers"][0]
        self.assertEqual(row["credits"], 700000)
        self.assertEqual(row["debits"], -200000)
        self.assertEqual(row["receipts"], 0)
        kinds = {k["kind"]: k for k in agg["kinds"]}
        self.assertEqual(kinds[""]["label"], "—")
        self.assertEqual(kinds[""]["amount"], 700000)
        self.assertEqual(kinds["tx"]["label"], "تراکنش")
        self.assertEqual(kinds["tx"]["amount"], -200000)

    def test_daily_boundary(self):
        data = _store([_customer("c1", "مشتری", [
            _tx("2026-09-15 00:00:00", 100000),       # first second: IN
            _tx("2026-09-14 23:59:59", -300000),      # last second before: OUT
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        row = agg["customers"][0]
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["credits"], 100000)
        self.assertEqual(row["debits"], 0)
        self.assertEqual(row["opening"], -300000)     # pre-period bucket
        self.assertEqual(row["closing"], -200000)

    def test_weekly_range_and_bucket_edges(self):
        # شنبه ref, mid-week ref and جمعه ref all map to the same week.
        for ref in (SAT, REF, FRI):
            agg = crm.finance_report_aggregate(_store([]), "weekly", ref)
            self.assertEqual((agg["from"], agg["to"]),
                             ("2026-09-12", "2026-09-18"), ref)
        # next week starts on the following شنبه
        agg = crm.finance_report_aggregate(_store([]), "weekly",
                                           datetime.date(2026, 9, 19))
        self.assertEqual((agg["from"], agg["to"]), ("2026-09-19", "2026-09-25"))
        data = _store([_customer("c1", "م", [
            _tx("2026-09-11 10:00:00", -100000),      # prior جمعه: opening
            _tx("2026-09-12 10:00:00", 50000),        # week start: in
            _tx("2026-09-18 23:59:59", 10000),        # week end: in
            _tx("2026-09-19 10:00:00", 70000),        # next شنبه: out
        ])])
        agg = crm.finance_report_aggregate(data, "weekly", SAT)
        row = agg["customers"][0]
        self.assertEqual(row["opening"], -100000)
        self.assertEqual(row["credits"], 60000)
        self.assertEqual(row["count"], 2)

    def test_weekly_days_table(self):
        data = _store([_customer("c1", "م", [
            _tx("2026-09-12 09:00:00", -100000, "debt"),    # شنبه
            _tx("2026-09-15 09:00:00", 50000, "receipt"),   # سه‌شنبه
            _tx("2026-09-18 23:00:00", -20000),             # جمعه
        ])])
        agg = crm.finance_report_aggregate(data, "weekly", REF)
        days = agg["days"]
        self.assertEqual(len(days), 7)
        self.assertEqual([d["date"] for d in days],
                         ["2026-09-12", "2026-09-13", "2026-09-14",
                          "2026-09-15", "2026-09-16", "2026-09-17",
                          "2026-09-18"])
        self.assertEqual(days[0]["day_fa"], "شنبه")
        self.assertEqual(days[0]["date_fa"], "۲۱ شهریور")
        self.assertEqual(days[3]["day_fa"], "سه‌شنبه")
        self.assertEqual(days[6]["day_fa"], "جمعه")
        self.assertEqual(days[0]["debits"], -100000)
        self.assertEqual(days[0]["count"], 1)
        self.assertEqual(days[3]["credits"], 50000)
        self.assertEqual(days[3]["receipts"], 50000)
        self.assertEqual(days[6]["debits"], -20000)
        # empty days stay zeroed, not missing
        self.assertEqual(days[1]["count"], 0)
        self.assertEqual(days[1]["credits"], 0)

    def test_totals_cover_all_customers_but_list_is_active_only(self):
        data = _store([
            _customer("c1", "فعال", [_tx("2026-09-15 09:00:00", 100000)]),
            _customer("c2", "غیرفعال", [_tx("2026-09-10 09:00:00", -400000)]),
        ])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        self.assertEqual([c["id"] for c in agg["customers"]], ["c1"])
        self.assertEqual(agg["totals"]["opening"], -400000)
        self.assertEqual(agg["totals"]["credits"], 100000)
        self.assertEqual(agg["totals"]["closing"], -300000)
        # split sums over per-customer closings (c1 +100k, c2 -400k)
        self.assertEqual(agg["totals"]["debtors_total"], -400000)
        self.assertEqual(agg["totals"]["creditors_total"], 100000)

    def test_debtors_creditors_split(self):
        data = _store([
            _customer("a", "الف", [_tx("2026-09-15 09:00:00", -100000)]),
            _customer("b", "ب", [_tx("2026-09-15 09:00:00", 50000)]),
            _customer("c", "ج", [_tx("2026-09-15 09:00:00", 50000),
                                 _tx("2026-09-15 10:00:00", -50000)]),
        ])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        self.assertEqual(agg["totals"]["debtors_total"], -100000)
        self.assertEqual(agg["totals"]["creditors_total"], 50000)
        self.assertEqual(agg["totals"]["closing"], -50000)
        # active customers sorted by gross activity (credits + |debits|)
        # desc — the same metric the dialog's activity bar renders; c's
        # net is zero yet its gross ties a's.
        self.assertEqual([c["id"] for c in agg["customers"]], ["a", "c", "b"])

    def test_empty_period(self):
        agg = crm.finance_report_aggregate(_store([]), "daily", REF)
        self.assertEqual(agg["totals"], {
            "opening": 0, "debits": 0, "credits": 0, "receipts": 0,
            "closing": 0, "count": 0, "debtors_total": 0, "creditors_total": 0})
        self.assertEqual(agg["kinds"], [])
        self.assertEqual(agg["customers"], [])
        self.assertEqual(agg["transactions"], [])
        self.assertEqual(agg["days"], [])
        self.assertEqual(agg["anomalies"], 0)
        agg = crm.finance_report_aggregate(_store([]), "weekly", REF)
        self.assertEqual(len(agg["days"]), 7)
        self.assertTrue(all(d["count"] == 0 for d in agg["days"]))

    def test_malformed_at_excluded_and_counted(self):
        data = _store([_customer("c1", "م", [
            dict(_tx("2026-09-15 12:00:00", 1000), at=20260915),  # non-str
            _tx("garbage", 2000),
            _tx("2026-9-15 10:00", 3000),     # non-padded date
            _tx("2026-09-15 11:00:00", 4000),  # valid
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        self.assertEqual(agg["anomalies"], 3)
        self.assertEqual(agg["totals"]["count"], 1)
        self.assertEqual(agg["totals"]["credits"], 4000)

    def test_transactions_newest_first_with_fields(self):
        data = _store([_customer("c1", "مشتری تست", [
            _tx("2026-09-15 08:00:00", -24000000, "base_fee",
                "هزینهٔ ثبت رجیستری — گوشی", "dev01"),
            _tx("2026-09-15 09:00:00", 500000, "receipt", "واریز نقدی"),
            _tx("2026-09-15 07:00:00", -1000, "debt"),
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        txs = agg["transactions"]
        self.assertEqual([t["at"] for t in txs],
                         ["2026-09-15 09:00:00", "2026-09-15 08:00:00",
                          "2026-09-15 07:00:00"])
        first = txs[0]
        self.assertEqual(first["customer"], "مشتری تست")
        self.assertEqual(first["customer_id"], "c1")
        self.assertEqual(first["kind"], "receipt")
        self.assertEqual(first["label"], "دریافتی")
        self.assertEqual(first["amount"], 500000)
        self.assertEqual(first["note"], "واریز نقدی")
        self.assertEqual(first["device_id"], "")
        self.assertEqual(txs[1]["device_id"], "dev01")
        self.assertEqual(txs[1]["label"], "هزینه ثبت رجیستری")

    def test_fa_dates(self):
        agg = crm.finance_report_aggregate(_store([]), "daily", REF)
        self.assertEqual(agg["from_fa"], jalali.to_fa("2026-09-15"))
        self.assertEqual(agg["to_fa"], jalali.to_fa("2026-09-15"))
        agg = crm.finance_report_aggregate(_store([]), "weekly", REF)
        self.assertEqual(agg["from_fa"], jalali.to_fa("2026-09-12"))
        self.assertEqual(agg["to_fa"], jalali.to_fa("2026-09-18"))
        # ISO strings are accepted for `ref` too
        agg = crm.finance_report_aggregate(_store([]), "daily", "2026-09-15")
        self.assertEqual(agg["from"], "2026-09-15")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            crm.finance_report_aggregate(_store([]), "monthly", REF)

    def test_fa_money(self):
        self.assertEqual(crm._fa_money(1234567), "۱٬۲۳۴٬۵۶۷ تومان")
        self.assertEqual(crm._fa_money(-500000), "−۵۰۰٬۰۰۰ تومان")
        self.assertEqual(crm._fa_money(0), "۰ تومان")
        self.assertEqual(crm._fa_money(None), "۰ تومان")

    def test_weekday_fa_index(self):
        expected = {datetime.date(2026, 9, 12): "شنبه",
                    datetime.date(2026, 9, 13): "یکشنبه",
                    datetime.date(2026, 9, 14): "دوشنبه",
                    datetime.date(2026, 9, 15): "سه‌شنبه",
                    datetime.date(2026, 9, 16): "چهارشنبه",
                    datetime.date(2026, 9, 17): "پنجشنبه",
                    datetime.date(2026, 9, 18): "جمعه"}
        for day, label in expected.items():
            self.assertEqual(crm.WEEKDAY_FA[(day.weekday() + 2) % 7],
                             label, day)
        self.assertEqual(crm.week_range(SAT),
                         (SAT, FRI))


class TxKindLabelPinTests(unittest.TestCase):
    """Divergence pin: crm.TX_KIND_LABELS == the JS TX_KIND_FA map."""

    def test_python_labels_match_js_map(self):
        import re
        src = open(panel.__file__, encoding="utf-8").read()
        match = re.search(r"var TX_KIND_FA\s*=\s*\{([^}]*)\};", src)
        self.assertTrue(match, "TX_KIND_FA map not found in panel.py")
        js_map = dict(re.findall(r"'([^']*)'\s*:\s*'([^']*)'", match.group(1)))
        self.assertTrue(js_map)
        self.assertEqual(js_map, dict(crm.TX_KIND_LABELS))


class FinanceRendererTests(unittest.TestCase):
    def _agg(self, mode="daily", ref=REF):
        data = _store([
            _customer("c1", "مشتری یک", [
                _tx("2026-09-14 09:00:00", -24000000, "base_fee"),
                _tx("2026-09-15 09:00:00", 24000000, "receipt", "واریز"),
            ]),
            _customer("c2", "مشتری دو", [
                _tx("2026-09-15 10:00:00", -300000, "debt"),
            ]),
        ])
        return crm.finance_report_aggregate(data, mode, ref)

    def test_html_markers(self):
        html = crm.finance_report_html(self._agg())
        self.assertIn('<html dir="rtl" lang="fa">', html)
        self.assertIn("@font-face", html)
        self.assertIn("/fonts/Vazirmatn-var.woff2", html)
        self.assertIn("Vazirmatn", html)
        self.assertIn('class="pos"', html)
        self.assertIn('class="neg"', html)
        # canonical daily title with the full Jalali date
        self.assertIn("گزارش مالی مشتریان — " + jalali.to_fa("2026-09-15"),
                      html)
        self.assertIn("روزانه", html)
        self.assertIn("دریافتی‌های نقدی (واریزی)", html)
        self.assertIn("بدهکاری‌های جدید", html)
        self.assertIn("ماندهٔ کل مشتریان", html)
        self.assertIn("بدهکار", html)
        self.assertIn("بستانکار", html)
        self.assertIn("انواع تراکنش", html)
        self.assertIn("مشتریان", html)
        self.assertIn("ماندهٔ اول", html)
        self.assertIn("بدهکاری", html)
        self.assertIn("بستانکاری", html)
        self.assertIn("ماندهٔ پایان", html)
        self.assertIn("تراکنش‌های این بازه", html)
        self.assertIn("مانده‌ها بر اساس مشتری‌های فعلی است.", html)
        # store data is escaped into the doc
        self.assertIn("مشتری یک", html)
        self.assertIn("واریز", html)

    def test_html_weekly_title_and_days_section(self):
        html = crm.finance_report_html(self._agg("weekly"))
        self.assertIn("هفتگی", html)
        self.assertIn("گزارش مالی مشتریان — " + jalali.to_fa("2026-09-12")
                      + " تا " + jalali.to_fa("2026-09-18"), html)
        self.assertIn("روزهای هفته", html)
        self.assertIn("شنبه", html)
        self.assertIn("جمعه", html)

    def test_anomalies_note_rendered_when_present(self):
        data = _store([_customer("c1", "م", [
            _tx("2026-09-15 09:00:00", 4000),
            _tx("garbage", 2000),
        ])])
        agg = crm.finance_report_aggregate(data, "daily", REF)
        self.assertEqual(agg["anomalies"], 1)
        note = "۱ تراکنش با تاریخ نامعتبر نادیده‌گرفته شد"
        self.assertIn(note, crm.finance_report_html(agg))
        self.assertIn(note, crm.finance_report_txt(agg))
        # the note disappears when every `at` parses
        self.assertNotIn("نادیده‌گرفته شد",
                         crm.finance_report_html(self._agg()))
        self.assertNotIn("نادیده‌گرفته شد",
                         crm.finance_report_txt(self._agg()))

    def test_html_empty_period(self):
        agg = crm.finance_report_aggregate(_store([]), "daily", REF)
        html = crm.finance_report_html(agg)
        self.assertIn("در این بازه تراکنشی ثبت نشده است.", html)
        self.assertIn("تسویه", html)      # zero closing balance

    def test_html_escapes_customer_data(self):
        data = _store([_customer("c1", "<b>نام</b>", [
            _tx("2026-09-15 09:00:00", 5, note="<i>یادداشت</i>")])])
        html = crm.finance_report_html(
            crm.finance_report_aggregate(data, "daily", REF))
        self.assertNotIn("<b>نام</b>", html)
        self.assertIn("&lt;b&gt;نام&lt;/b&gt;", html)
        self.assertIn("&lt;i&gt;یادداشت&lt;/i&gt;", html)

    def test_txt_sections(self):
        text = crm.finance_report_txt(self._agg())
        self.assertTrue(text.startswith("گزارش مالی مشتریان — "))
        self.assertIn("روزانه", text)
        self.assertIn("دریافتی‌های نقدی (واریزی): ", text)
        self.assertIn("بدهکاری‌های جدید: ", text)
        self.assertIn("ماندهٔ کل مشتریان: ", text)
        self.assertIn("انواع تراکنش:", text)
        self.assertIn("مشتریان:", text)
        self.assertIn("تراکنش‌های این بازه:", text)
        self.assertIn("مانده‌ها بر اساس مشتری‌های فعلی است.", text)
        self.assertIn("مشتری یک", text)
        self.assertIn("−۳۰۰٬۰۰۰ تومان", text)
        self.assertIn("تسویه", text)      # zero opening renders تسویه
        self.assertNotIn("<", text)       # plain text, no markup

    def test_txt_weekly(self):
        text = crm.finance_report_txt(self._agg("weekly"))
        self.assertIn("هفتگی", text)
        self.assertIn("تا", text)
        self.assertIn("روزهای هفته:", text)
        self.assertIn("شنبه", text)

    def test_txt_empty_period(self):
        text = crm.finance_report_txt(
            crm.finance_report_aggregate(_store([]), "daily", REF))
        self.assertIn("در این بازه تراکنشی ثبت نشده است.", text)
        self.assertIn("۰ تومان", text)


class FinanceApiTests(unittest.TestCase):
    """HTTP-level: /crm-finance-report + /crm-finance-report-telegram."""

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
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm_mod.reset_cache()
        self.addCleanup(crm_mod.reset_cache)

        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # -- helpers ---------------------------------------------------------
    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

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

    def _seed_store(self, customers):
        crm.store_write(_store(customers))

    def _log_events(self):
        path = crm.log_path()
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    # -- json contract ---------------------------------------------------
    def test_defaults_daily_today_json(self):
        import time
        status, raw, headers = self._get("/crm-finance-report")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        body = json.loads(raw)
        self.assertTrue(body["ok"])
        self.assertEqual(body["mode"], "daily")
        today = time.strftime("%Y-%m-%d")
        self.assertEqual(body["from"], today)
        self.assertEqual(body["to"], today)
        self.assertEqual(body["days"], [])
        for key in ("from_fa", "to_fa", "totals", "kinds", "customers",
                    "transactions", "anomalies"):
            self.assertIn(key, body)
        self.assertEqual(body["totals"]["count"], 0)

    def test_weekly_json_contract(self):
        self._seed_store([_customer("c1", "مشتری یک", [
            _tx("2026-09-12 09:00:00", 24000000, "receipt"),
            _tx("2026-09-15 10:00:00", -24000000, "base_fee"),
            _tx("2026-09-14 09:00:00", -100000, "debt"),
        ])])
        status, raw, _ = self._get(
            "/crm-finance-report?mode=weekly&ref=2026-09-15&fmt=json")
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertTrue(body["ok"])
        self.assertEqual((body["from"], body["to"]),
                         ("2026-09-12", "2026-09-18"))
        self.assertEqual(body["from_fa"], jalali.to_fa("2026-09-12"))
        self.assertEqual(body["to_fa"], jalali.to_fa("2026-09-18"))
        self.assertEqual(len(body["days"]), 7)
        self.assertEqual(body["days"][0]["day_fa"], "شنبه")
        self.assertEqual(body["totals"]["credits"], 24000000)
        self.assertEqual(body["totals"]["debits"], -24100000)
        self.assertEqual(body["totals"]["receipts"], 24000000)
        self.assertEqual(body["totals"]["count"], 3)
        self.assertEqual([t["at"] for t in body["transactions"]],
                         ["2026-09-15 10:00:00", "2026-09-14 09:00:00",
                          "2026-09-12 09:00:00"])

    # -- validation ------------------------------------------------------
    def test_get_rejects_bad_mode(self):
        status, raw, _ = self._get(
            "/crm-finance-report?mode=monthly&ref=2026-09-15")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw),
                         {"ok": False, "errors": [BAD_MODE]})

    def test_get_rejects_bad_ref(self):
        for ref in ("2026-9-5", "15-09-2026", "2026-02-30", "garbage"):
            status, raw, _ = self._get(
                f"/crm-finance-report?mode=daily&ref={ref}")
            self.assertEqual(status, 400, ref)
            self.assertEqual(json.loads(raw)["errors"], [BAD_REF], ref)

    def test_get_rejects_bad_fmt(self):
        status, raw, _ = self._get(
            "/crm-finance-report?mode=daily&ref=2026-09-15&fmt=xml")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw),
                         {"ok": False, "errors": [BAD_FMT]})

    def test_get_accumulates_all_validation_errors(self):
        status, raw, _ = self._get("/crm-finance-report?mode=x&ref=bad&fmt=y")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["errors"],
                         [BAD_MODE, BAD_REF, BAD_FMT])

    # -- formats ---------------------------------------------------------
    def test_html_fmt(self):
        self._seed_store([_customer("c1", "مشتری یک", [
            _tx("2026-09-15 09:00:00", 24000000, "receipt")])])
        status, raw, headers = self._get(
            "/crm-finance-report?mode=daily&ref=2026-09-15&fmt=html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = raw.decode("utf-8")
        self.assertIn('dir="rtl"', html)
        self.assertIn("@font-face", html)
        self.assertIn("Vazirmatn", html)
        self.assertIn("گزارش مالی مشتریان — " + jalali.to_fa("2026-09-15"),
                      html)

    def test_txt_fmt_is_attachment_download(self):
        status, raw, headers = self._get(
            "/crm-finance-report?mode=daily&ref=2026-09-15&fmt=txt")
        self.assertEqual(status, 200)
        self.assertIn("application/octet-stream",
                      headers.get("Content-Type", ""))
        self.assertIn("crm-finance-report.txt",
                      headers.get("Content-Disposition", ""))
        text = raw.decode("utf-8")
        self.assertTrue(text.startswith("گزارش مالی مشتریان — "))
        self.assertIn("مانده‌ها بر اساس مشتری‌های فعلی است.", text)

    def test_pdf_fmt_serves_pdf_or_html_fallback(self):
        self._seed_store([_customer("c1", "مشتری یک", [
            _tx("2026-09-15 09:00:00", 24000000, "receipt")])])
        status, raw, headers = self._get(
            "/crm-finance-report?mode=daily&ref=2026-09-15&fmt=pdf")
        self.assertEqual(status, 200)
        ctype = headers.get("Content-Type", "")
        if "pdf" in ctype:
            self.assertGreater(len(raw), 500)
            self.assertEqual(raw[:4], b"%PDF")
        else:
            self.assertIn("html", ctype)
            self.assertIn("print", raw.decode("utf-8"))

    def test_pdf_fmt_html_fallback_branch(self):
        # deterministic fallback: with the converter disabled the route must
        # serve the auto-print HTML document, not an error
        self._seed_store([_customer("c1", "مشتری یک", [
            _tx("2026-09-15 09:00:00", 24000000, "receipt")])])
        with patch.object(panel, "crm_html_to_pdf", return_value=None):
            status, raw, headers = self._get(
                "/crm-finance-report?mode=daily&ref=2026-09-15&fmt=pdf")
        self.assertEqual(status, 200)
        self.assertIn("html", headers.get("Content-Type", ""))
        text = raw.decode("utf-8")
        self.assertIn("print", text)
        self.assertIn("گزارش مالی مشتریان", text)

    # -- telegram --------------------------------------------------------
    def test_telegram_chunks_event_and_count(self):
        txs = [
            _tx("2026-09-15 %02d:%02d:00" % (i // 60, i % 60),
                1000000 + i, "receipt",
                "دریافتی شمارهٔ " + str(i) + " با توضیح نسبتاً بلند برای "
                "پرشدن پیام تلگرام و رسیدن به چند بخش")
            for i in range(80)]
        self._seed_store([_customer("c1", "مشتری با تراکنش‌های زیاد", txs)])
        sent = []
        fake = type("tg", (), {"send_message": staticmethod(
            lambda text, timeout=20, actor=None: sent.append(text) or True)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-finance-report-telegram",
                                      {"mode": "daily", "ref": "2026-09-15"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["sent"], len(sent))
        self.assertGreater(len(sent), 1)              # actually chunked
        for chunk in sent:
            self.assertLessEqual(len(chunk), 3900)
        # chunking is a pure split of the full text
        expected = crm.finance_report_txt(crm.finance_report_aggregate(
            crm.store_read(), "daily", datetime.date(2026, 9, 15)))
        self.assertEqual("".join(sent), expected)
        # exactly one audit event carrying the mode and chunk count
        events = [e for e in self._log_events()
                  if e.get("event") == "report_finance_sent"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("chunks"), len(sent))
        self.assertEqual(events[0].get("mode"), "daily")

    def test_telegram_rejects_bad_params(self):
        status, body = self._post("/crm-finance-report-telegram",
                                  {"mode": "monthly", "ref": "2026-09-15"})
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"], [BAD_MODE])
        status, body = self._post("/crm-finance-report-telegram",
                                  {"mode": "weekly", "ref": "2026-13-01"})
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"], [BAD_REF])

    def test_telegram_partial_failure_is_502(self):
        # a dropped middle chunk must never report success for a financial
        # digest — the route 502s with the sent/total counts
        txs = [_tx("2026-09-15 09:00:00", 1000000 + i, "receipt",
                   "توضیح بلند برای چند بخش شدن پیام " + "متن" * 30)
               for i in range(80)]
        self._seed_store([_customer("c1", "مشتری", txs)])
        calls = []

        def flaky(text, timeout=20, actor=None):
            calls.append(text)
            return len(calls) == 1        # first chunk lands, the rest drop

        fake = type("tg", (), {"send_message": staticmethod(flaky)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-finance-report-telegram",
                                      {"mode": "daily", "ref": "2026-09-15"})
        self.assertGreater(len(calls), 1)             # actually chunked
        self.assertEqual(status, 502)
        self.assertFalse(body["ok"])
        self.assertTrue(body["errors"][0].startswith("ارسال تلگرام کامل نشد: "))
        self.assertIn("بخش ارسال شد", body["errors"][0])
        self.assertIn(jalali.fa_digits(len(calls)), body["errors"][0])
        events = [e for e in self._log_events()
                  if e.get("event") == "report_finance_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("detail"), "1/%d" % len(calls))

    def test_telegram_exception_is_502_with_event(self):
        self._seed_store([_customer("c1", "مشتری", [
            _tx("2026-09-15 09:00:00", 100000, "receipt")])])

        def boom(text, timeout=20, actor=None):
            raise RuntimeError("proxy down")

        fake = type("tg", (), {"send_message": staticmethod(boom)})
        with patch.dict("sys.modules", {"telegram_notify": fake}):
            status, body = self._post("/crm-finance-report-telegram",
                                      {"mode": "daily", "ref": "2026-09-15"})
        self.assertEqual(status, 502)
        self.assertEqual(body["errors"], ["ارسال تلگرام ناموفق بود"])
        events = [e for e in self._log_events()
                  if e.get("event") == "report_finance_failed"]
        self.assertEqual(len(events), 1)
        self.assertIn("proxy down", str(events[0].get("detail")))


if __name__ == "__main__":
    unittest.main()
