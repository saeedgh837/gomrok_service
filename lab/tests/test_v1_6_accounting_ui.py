# -*- coding: utf-8 -*-
"""v1.6 accounting round (frontend workstream) — received-amount intake,
the per-customer statement dialog, the case invoice link, the transaction
void flow and the skip-voided balance predicate, locked as CRM_PAGE source
markers.

The backend endpoints (POST /crm-device add with received_amount, POST
/crm-customer void_tx, GET /crm-statement json/html/pdf, POST
/crm-statement-telegram, GET /crm-invoice, business_name/business_phone
settings) land in parallel; these tests pin the UI contract they are
consumed through: canonical copy, GET/POST wiring via api(), the
styled-confirm void (never native confirm, double-submit guarded), and the
single balance predicate every money surface flows through.
"""
import re
import unittest

import panel


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE (same trick as v1.5)."""
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


class DeviceReceivedTests(unittest.TestCase):
    """dlgDevice/doAddDevice: dvRecv field, client guard, payload key."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_dlg_device_has_received_amount_field(self):
        body = fn_body(self.js, "dlgDevice")
        self.assertTrue(body, "dlgDevice body not found")
        self.assertIn("مبلغ دریافتی (تومان)", body)
        self.assertIn('id="dvRecv"', body)
        self.assertIn('inputmode="numeric"', body)
        self.assertIn('value="0"', body)

    def test_note_appends_received_sentence_after_fee_sentence(self):
        body = fn_body(self.js, "dlgDevice")
        # v1.7: the note names the field «هزینه ثبت رجیستری» (one name for
        # one charge, matching the ledger) and states that the default is
        # read from settings and is editable per case.
        self.assertIn("«هزینه ثبت رجیستری»", body)
        self.assertIn("مبلغ پیش‌فرض از تب تنظیمات خوانده می‌شود و قابل تغییر است",
                      body)
        # the received-amount sentence still follows it in the same <p>
        self.assertIn(
            "مبلغ دریافتی نیز به‌صورت «دریافتی» در حساب همان مشتری ثبت می‌شود.",
            body)

    def test_fee_field_defaults_to_configured_amount(self):
        """v1.7: the new-case dialog carries an editable per-case fee whose
        default is the configured registry_fee_amount."""
        body = fn_body(self.js, "dlgDevice")
        self.assertIn("هزینه ثبت رجیستری (تومان)", body)
        self.assertIn('id="dvFee"', body)
        # the default value is wired to settings, not hard-coded
        self.assertIn("(STATE.settings||{}).registry_fee_amount", body)

    def test_do_add_device_parses_and_guards_fee(self):
        """v1.7: the per-case fee is parsed like the received amount — raw
        input validated first, then digitsFa; the server is the authority."""
        body = fn_body(self.js, "doAddDevice")
        self.assertTrue(body, "doAddDevice not found")
        self.assertIn("isNaN(fee)||fee<0", body)
        self.assertIn("هزینه ثبت رجیستری باید عدد نامنفی باشد (تومان)", body)
        # an EMPTY field means "no override" (the configured amount applies),
        # so the payload only carries the key when the operator typed digits;
        # a field holding separators only (the «٬» left behind after clearing
        # the pre-filled default) counts as empty, never as a silent zero.
        self.assertIn("var fee=null;", body)
        self.assertIn("var feeDigits=digitsFa(feeRaw);", body)
        self.assertIn("if(feeRaw!==''&&feeDigits!==''){fee=parseInt(feeDigits,10);}",
                      body)
        self.assertIn("if(fee!==null)payload.registry_fee_override=fee;", body)

    def test_do_add_device_parses_and_guards_received_amount(self):
        body = fn_body(self.js, "doAddDevice")
        self.assertTrue(body, "doAddDevice not found")
        self.assertIn("parseInt(digitsFa(recvRaw)||'0',10)", body)
        # client-side guard: NaN or negative stops the POST (server stays
        # the authority) with the canonical message in the dialog error box
        self.assertIn("isNaN(recv)||recv<0", body)
        self.assertIn("مبلغ دریافتی باید عدد نامنفی باشد (تومان)", body)
        self.assertIn("received_amount:recv", body)


class StatementDialogTests(unittest.TestCase):
    """dlgStatement/stmt*: open contract, GET wiring, chips, table, footer."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_customers_row_opens_statement_before_receipt(self):
        body = fn_body(self.js, "customersHtml")
        self.assertTrue(body, "customersHtml not found")
        self.assertIn("صورت‌حساب", body)
        self.assertIn("dlgStatement(", body)
        self.assertLess(body.index("dlgStatement("), body.index("dlgReceipt("),
                        "صورت‌حساب must precede ثبت دریافتی in the row actions")

    def test_dlg_statement_exists_and_resets_state_on_open(self):
        self.assertIn("function dlgStatement(", self.js)
        body = fn_body(self.js, "dlgStatement")
        self.assertTrue(body, "dlgStatement body not found")
        self.assertIn("STMT={cid:cid,from:'',to:'',range:'all'}", body)
        self.assertIn("STMT_SEQ++", body)
        self.assertIn("صورت‌حساب مشتری", body)
        self.assertIn("در حال دریافت صورت‌حساب…", body)

    def test_statement_fetch_uses_get_with_json_fmt(self):
        url = fn_body(self.js, "stmtUrl")
        self.assertTrue(url, "stmtUrl not found")
        self.assertIn("'/crm-statement?'", url)
        self.assertIn("'&fmt=json'", url)
        query = fn_body(self.js, "stmtQuery")
        self.assertTrue(query, "stmtQuery not found")
        self.assertIn("customer_id=", query)
        self.assertIn("(STMT.from?'&from='+STMT.from:'')", query)
        self.assertIn("(STMT.to?'&to='+STMT.to:'')", query)
        load = fn_body(self.js, "stmtLoad")
        self.assertTrue(load, "stmtLoad not found")
        self.assertIn("api(stmtUrl())", load)
        self.assertIn("seq!==STMT_SEQ", load)

    def test_range_chips_and_local_midnight_arithmetic(self):
        opening = fn_body(self.js, "dlgStatement")
        for marker in ("همه", "۳۰ روز اخیر", "۹۰ روز اخیر",
                       'data-r="all"', 'data-r="30"', 'data-r="90"'):
            self.assertIn(marker, opening, marker)
        rng = fn_body(self.js, "stmtRange")
        self.assertTrue(rng, "stmtRange not found")
        # 30/90 windows anchor on local midnight (established finShift
        # pattern); «همه» clears both bounds
        self.assertIn("'T00:00:00'", rng)
        self.assertIn("setDate", rng)
        self.assertIn("STMT.from='';STMT.to=''", rng)
        self.assertIn("classList.toggle('on',on)", rng)
        self.assertIn("aria-pressed", rng)

    def test_statement_body_renders_canonical_columns_and_voided_rows(self):
        body = fn_body(self.js, "stmtBodyHtml")
        self.assertTrue(body, "stmtBodyHtml not found")
        self.assertIn(
            "<th>تاریخ</th><th>شرح</th><th>بدهکار</th><th>بستانکار</th>"
            "<th>مانده</th>",
            body)
        # totals strip reuses the finance KPI cards + the v1.5 vocabulary
        for marker in ("ماندهٔ اول", "بدهکاری", "بستانکاری", "ماندهٔ پایان",
                       "finBal", "stmt-kpis"):
            self.assertIn(marker, body, marker)
        # voided / reversal rows: struck class + no running balance; the
        # balances[] array is read in parallel with rows[]
        self.assertIn('class="voidedtx"', body)
        self.assertIn("x.voided||x.kind==='void'", body)
        self.assertIn("(r.balances||[])[i]", body)
        # the voided count surfaces as a muted note
        self.assertIn("voided_count", body)
        self.assertIn(
            " ردیف ابطال‌شده در محاسبه‌ها لحاظ نشده است.", body)

    def test_statement_footer_wiring(self):
        opening = fn_body(self.js, "dlgStatement")
        self.assertIn('onclick="stmtOpen(\\\'html\\\')"', opening)
        self.assertIn('onclick="stmtOpen(\\\'pdf\\\')"', opening)
        self.assertIn('onclick="stmtTelegram()"', opening)
        self.assertIn('onclick="closeDlg()"', opening)
        opened = fn_body(self.js, "stmtOpen")
        self.assertTrue(opened, "stmtOpen not found")
        self.assertIn("window.open('/crm-statement?'", opened)
        self.assertIn("'&fmt='+fmt", opened)
        telegram = fn_body(self.js, "stmtTelegram")
        self.assertTrue(telegram, "stmtTelegram not found")
        self.assertIn("api('/crm-statement-telegram'", telegram)
        self.assertIn("customer_id:STMT.cid", telegram)
        self.assertIn("from:STMT.from", telegram)
        self.assertIn("to:STMT.to", telegram)
        self.assertIn("'ارسال شد ('+fa(r.sent)+' بخش)'", telegram)

    def test_voided_strike_css_exists(self):
        self.assertIn(
            ".voidedtx td{color:var(--muted);text-decoration:line-through}",
            self.page)


class InvoiceButtonTests(unittest.TestCase):
    """caseDetail: the «فاکتور» quiet-group button opens the print invoice."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_case_detail_has_invoice_button(self):
        body = fn_body(self.js, "caseDetail")
        self.assertTrue(body, "caseDetail not found")
        self.assertIn("فاکتور", body)
        # raw source carries the JS \' escapes inside the double-quoted
        # onclick attribute (esc() never escapes quotes there)
        self.assertIn("window.open(\\'/crm-invoice?device_id='", body)
        self.assertIn("'&fmt=html\\'", body)
        self.assertIn("_blank", body)
        # the button joins «سایر اقدامات» (quiet ghost style) and carries a
        # document icon from the house SVG language
        self.assertIn("سایر اقدامات", body)
        self.assertIn("ICONS.doc", body)
        self.assertIn("doc:'<svg", self.js)


class VoidFlowTests(unittest.TestCase):
    """txDetailModal/dlgVoidTx/doVoidTx: both detail states, styled confirm,
    POST contract, double-submit guard."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_tx_detail_modal_branches_on_void_state(self):
        body = fn_body(self.js, "txDetailModal")
        self.assertTrue(body, "txDetailModal not found")
        # active transaction: the danger void action (never on a reversal)
        self.assertIn("!t.voided_at&&t.kind!=='void'", body)
        self.assertIn("dlgVoidTx(", body)
        # voided transaction: when + why rows instead of the button
        self.assertIn("rows.push(['تاریخ ابطال'", body)
        self.assertIn("rows.push(['دلیل ابطال'", body)
        self.assertIn("t.void_reason", body)
        self.assertIn("faDateTime(t.voided_at)", body)

    def test_dlg_void_tx_summary_warning_and_reason(self):
        self.assertIn("function dlgVoidTx(", self.js)
        body = fn_body(self.js, "dlgVoidTx")
        self.assertTrue(body, "dlgVoidTx body not found")
        self.assertIn("ابطال تراکنش", body)
        self.assertIn(
            "هشدار: ابطال، تراکنش را از محاسبه‌ها خارج می‌کند و یک"
            " ردیف اصلاحیهٔ قرینه ثبت می‌شود؛ تاریخچه پاک نمی‌شود.",
            body)
        self.assertIn("دلیل ابطال *", body)
        self.assertIn('id="vtReason"', body)
        # summary rows name kind/amount/note/time before the warning
        self.assertIn("TX_KIND_FA[t.kind]", body)
        # never a native confirm inside the modal itself
        self.assertNotIn("confirm(", body)

    def test_do_void_tx_guards_reason_then_double_submit(self):
        body = fn_body(self.js, "doVoidTx")
        self.assertTrue(body, "doVoidTx not found")
        # empty reason stops before the confirm replaces the dialog (the
        # canonical message the server also answers with)
        self.assertIn("ثبت دلیل برای ابطال الزامی است", body)
        # double-submit guard mirrors doDetach/stageContinueClick
        self.assertIn("btn.disabled=true", body)

    def test_do_void_tx_confirms_then_posts_void_tx(self):
        body = fn_body(self.js, "doVoidTx")
        self.assertIn("confirmDlg('تأیید ابطال'", body)
        self.assertIn("این تراکنش ابطال شود؟ این کار قابل بازگشت نیست.", body)
        self.assertIn("بله، ابطال کن", body)
        self.assertIn(
            "api('/crm-customer',{action:'void_tx',id:cid,tx_index:idx,"
            "reason:reason})",
            body)
        self.assertIn("toastCtx(cn,'تراکنش ابطال شد.')", body)


class BalancePredicateTests(unittest.TestCase):
    """bal() skips voided/reversal rows; the ledger renders them struck;
    TX_KIND_FA labels the reversal rows."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_bal_skips_voided_and_reversal_rows(self):
        body = fn_body(self.js, "bal")
        self.assertTrue(body, "bal not found")
        self.assertIn("(t.voided_at||t.kind==='void'||!/^\d{4}-\d{2}-\d{2}/.test(String(t.at||'')))?s:s+Number(t.amount||0)",
                      body)

    def test_ledger_rows_render_struck_when_voided(self):
        body = fn_body(self.js, "ledgerHtml")
        self.assertTrue(body, "ledgerHtml not found")
        self.assertIn("t.voided_at||t.kind==='void'", body)
        self.assertIn('class="voidedtx"', body)

    def test_tx_kind_fa_labels_void(self):
        self.assertIn("'void':'ابطال'", self.js)


class SettingsBusinessTests(unittest.TestCase):
    """Settings tab: optional business identity fields + per-card save."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_settings_renders_business_fields(self):
        body = fn_body(self.js, "settingsHtml")
        self.assertTrue(body, "settingsHtml not found")
        self.assertIn('id="stBizName"', body)
        self.assertIn('id="stBizPhone"', body)
        self.assertIn("نام کسب‌وکار", body)
        self.assertIn("شمارهٔ تماس کسب‌وکار", body)

    def test_settings_save_biz_posts_both_keys(self):
        body = fn_body(self.js, "settingsSaveBiz")
        self.assertTrue(body, "settingsSaveBiz not found")
        self.assertIn(
            "api('/crm-settings',{business_name:v('stBizName'),"
            "business_phone:v('stBizPhone')})",
            body)
        # reuses the exact existing settings-save toast wording
        self.assertIn("toast('تنظیمات ذخیره شد')", body)


if __name__ == "__main__":
    unittest.main()
