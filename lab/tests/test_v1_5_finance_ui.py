# -*- coding: utf-8 -*-
"""v1.5 finance round (frontend workstream) — the customers-tab finance
report dialog and the passenger detach flow, locked as CRM_PAGE source
markers.

The backend endpoints (GET /crm-finance-report json/html/pdf, POST
/crm-finance-report-telegram, /crm-tick detach_owner) land in parallel;
these tests pin the UI contract they are consumed through: the dialog's
canonical copy, the GET/POST wiring via api(), the server-anchored period
navigation, the confirmDlg-based detach (never native confirm), and the
generic history filters that surface the new log events.
"""
import re
import unittest

import panel


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE (same trick as v1.1.5)."""
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not match:
        return ""
    depth, i = 1, match.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[match.start():i]


class FinanceDialogTests(unittest.TestCase):
    """dlgFinance: open/reset contract, GET wiring, canonical copy, footer."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_dlg_finance_exists_and_resets_ref_on_open(self):
        self.assertIn("function dlgFinance(){", self.js)
        body = fn_body(self.js, "dlgFinance")
        self.assertTrue(body, "dlgFinance body not found")
        # FIN_REF resets to "today" on every open: the first fetch omits ref
        # and adopts the server-returned from
        self.assertIn("FIN_REF=''", body)
        self.assertIn("FIN_SEQ++", body)
        self.assertIn("گزارش مالی مشتریان", body)

    def test_finance_fetch_uses_get_with_json_fmt(self):
        url = fn_body(self.js, "finUrl")
        self.assertTrue(url, "finUrl not found")
        self.assertIn("'/crm-finance-report?mode='+FIN_MODE", url)
        self.assertIn("'&fmt=json'", url)
        load = fn_body(self.js, "finLoad")
        self.assertTrue(load, "finLoad not found")
        # api() without a payload performs the GET; stale responses race-drop
        self.assertIn("api(finUrl())", load)
        self.assertIn("seq!==FIN_SEQ", load)

    def test_finance_period_nav_is_server_anchored(self):
        # only ±day/±week arithmetic on the adopted anchor (local parse
        # pattern 'T00:00:00'); display labels come from the response
        shift = fn_body(self.js, "finShift")
        self.assertTrue(shift, "finShift not found")
        self.assertIn("'T00:00:00'", shift)
        self.assertIn("FIN_MODE==='weekly'?7:1", shift)
        label = fn_body(self.js, "finLabelHtml")
        self.assertTrue(label, "finLabelHtml not found")
        self.assertIn("from_fa", label)
        self.assertIn("تا ", label)

    def test_finance_canonical_copy(self):
        for marker in (
            "روزانه", "هفتگی", "امروز", "این هفته",
            "بازهٔ قبلی", "بازهٔ بعدی",
            "دریافتی‌های نقدی (واریزی)", "بدهکاری‌های جدید",
            "ماندهٔ کل مشتریان", "بدهکار ", "بستانکار ", "تسویه",
            "ماندهٔ اول", "بدهکاری", "بستانکاری", "ماندهٔ پایان",
            "تراکنش‌های این بازه",
            "در این بازه تراکنشی ثبت نشده است.",
            "مانده‌ها بر اساس مشتری‌های فعلی است.",
            "چاپ", "PDF", "ارسال به تلگرام", "بستن",
        ):
            self.assertIn(marker, self.page, marker)
        body = fn_body(self.js, "finBodyHtml")
        self.assertTrue(body, "finBodyHtml not found")
        # both empty states reuse the canonical sentence
        self.assertEqual(body.count("در این بازه تراکنشی ثبت نشده است."), 2)
        # weekly day strip renders server-provided weekday/date labels only
        self.assertIn("day_fa", body)
        self.assertIn("date_fa", body)

    def test_finance_footer_wiring(self):
        opening = fn_body(self.js, "dlgFinance")
        self.assertIn('onclick="finOpen(\\\'html\\\')"', opening)
        self.assertIn('onclick="finOpen(\\\'pdf\\\')"', opening)
        self.assertIn('onclick="finTelegram()"', opening)
        self.assertIn('onclick="closeDlg()"', opening)
        opened = fn_body(self.js, "finOpen")
        self.assertTrue(opened, "finOpen not found")
        self.assertIn("window.open('/crm-finance-report?mode='", opened)
        self.assertIn("'+fmt", opened)
        telegram = fn_body(self.js, "finTelegram")
        self.assertTrue(telegram, "finTelegram not found")
        self.assertIn("api('/crm-finance-report-telegram'", telegram)
        self.assertIn("mode:FIN_MODE", telegram)
        self.assertIn("ref:FIN_REF", telegram)
        self.assertIn("ارسال شد", telegram)

    def test_seg_active_state_follows_fin_mode(self):
        # the segmented control derives class/aria-pressed from FIN_MODE so
        # a persisted weekly session reopens with the right highlight.
        # v1.7: the toolbar markup moved into finBarHtml() so finMode() can
        # rebuild it in place when the mode changes.
        opening = fn_body(self.js, "dlgFinance")
        self.assertIn("finBarHtml()", opening)
        bar = fn_body(self.js, "finBarHtml")
        self.assertIn("(FIN_MODE==='daily'?'on':'')", bar)
        self.assertIn("(FIN_MODE==='weekly'?'on':'')", bar)
        self.assertIn("(FIN_MODE==='daily'?'true':'false')", bar)

    def test_telegram_toast_counts_and_anomalies_note(self):
        telegram = fn_body(self.js, "finTelegram")
        # the toast names the chunk count and closes its parenthesis
        self.assertIn("'ارسال شد ('+fa(r.sent)+' بخش)'", telegram)
        body = fn_body(self.js, "finBodyHtml")
        # malformed-at transactions are surfaced, never silently dropped
        self.assertIn("r.anomalies", body)
        self.assertIn("تراکنش با تاریخ نامعتبر نادیده‌گرفته شد", body)


class DetachFlowTests(unittest.TestCase):
    """dlgDetach/doDetach: staged branches, styled confirm, POST contract."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_dlg_detach_branches(self):
        self.assertIn("function dlgDetach(", self.js)
        body = fn_body(self.js, "dlgDetach")
        self.assertTrue(body, "dlgDetach body not found")
        self.assertIn("قطع اتصال مسافر", body)
        # stale state guard (owner no longer bound when the modal opens)
        self.assertIn("toast('پرونده پیدا نشد',false)", body)
        # Branch A: allowed stages + canonical warning + danger button
        self.assertIn("['ready_owner','problem','closed'].includes(dev.stage)", body)
        self.assertIn("هشدار: هر مسافر فقط به یک پرونده متصل می‌ماند", body)
        # Branch B: blocked stages + guidance, single close button
        self.assertIn(
            "['handed','to_payment','payment','payment_done'].includes(dev.stage)",
            body)
        self.assertIn("این پرونده به مسئول ثبت تحویل شده است", body)
        # rolled-back-before-step-3 cases get the same remedy minus the
        # handover claim
        self.assertIn("برای قطع اتصال، ابتدا پرونده را به گام ۳ (اتصال مسافر) "
                      "برگردانید یا آن را مختومه کنید.", body)
        # serials are Latin data inside an RTL page — pinned direction
        self.assertIn('class="kbd" dir="ltr"', body)
        # the warning uses the product term for the registration profile
        self.assertIn("پروفایل ثبت‌نام", body)
        # never a native confirm in the modal itself
        self.assertNotIn("confirm(", body)

    def test_do_detach_posts_detach_owner_with_device_id(self):
        body = fn_body(self.js, "doDetach")
        self.assertTrue(body, "doDetach not found")
        self.assertIn("confirmDlg('تأیید قطع اتصال'", body)
        self.assertIn("بله، قطع اتصال کن", body)
        self.assertIn("api('/crm-tick',{device_id:id,action:'detach_owner'})",
                      body)
        self.assertIn("اتصال مسافر قطع شد.", body)
        # double-submit guard mirrors stageContinueClick
        self.assertIn("btn.disabled=true", body)


class HistorySurfacingTests(unittest.TestCase):
    """detach_owner / report_finance_sent events surface through the
    existing generic history filters (payload-key match + report* prefix),
    so no per-event wiring is needed — these assertions lock that."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_owner_keyed_events_surface_in_passengers_history(self):
        # detach_owner logs carry device_id + owner_id; the owners-tab key
        # list matches on owner_id, the devices tab on device_id
        self.assertIn("owners:['owner_id','entity_id']", self.js)
        self.assertIn("devices:['device_id','entity_id']", self.js)
        self.assertIn("action:'detach_owner'", self.page)

    def test_report_events_surface_in_reports_history(self):
        body = fn_body(self.js, "historyHtml")
        self.assertTrue(body, "historyHtml not found")
        self.assertIn("String(l.event||'').startsWith('report')", body)
        # the finance telegram event matches that prefix filter
        self.assertTrue("report_finance_sent".startswith("report"))


if __name__ == "__main__":
    unittest.main()
