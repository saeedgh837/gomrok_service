# -*- coding: utf-8 -*-
"""v1.5 B5+B6 — consistent dialog close chrome + timestamps on every case surface.

- Dialog inventory gate: every `<dialog>` element block and every
  `class="modal"` block served by panel.py carries a close affordance
  (`aria-label="بستن"` or a closeDlg()/closeResModal() onclick). The CRM
  shared dialog (#dlg) owns a single x-close button in its chrome OUTSIDE
  #dlgBody, so every openDlg() body inherits it — bodies keep their own
  انصراف/بستن buttons and never embed a second x. The estelam result modal
  (site-2) was already compliant and must stay byte-untouched.
  site-1 (render_page), LAUNCH_PAGE, HOME_PAGE and LOGIN_PAGE use native
  confirm()/plain forms — no custom dialogs, nothing to add there.
- Timestamps: devRow (done/problem tabs) shows created/updated kbd chips,
  done rows add a «تاریخ تکمیل» chip, closed cards show created/updated
  next to the existing closed_at — caseDetail and the case table already
  showed them (B7).
- Restores the lost id="quickBox" marker on the quick-links box so the
  v1.3 UI module is fully green again.
- TX_KIND_FA labels the automatic registry-fee reversal (base_fee_refund).
"""
import os
import re
import unittest

import panel


def panel_src():
    """Full panel.py source (covers every page it serves)."""
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE (same trick as the rail test)."""
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


def dialog_blocks(src):
    """Every <dialog ...>...</dialog> element block in the source."""
    return re.findall(r"<dialog\b[^>]*>[\s\S]*?</dialog>", src)


def modal_blocks(src):
    """Every `class="modal"` element block (div-depth matched from its <div)."""
    blocks = []
    for match in re.finditer(r'class="modal"', src):
        start = src.rfind("<div", 0, match.start())
        if start < 0:
            continue
        depth, i = 0, start
        end = -1
        while i < len(src):
            if src.startswith("<div", i):
                depth += 1
                i += 4
            elif src.startswith("</div>", i):
                depth -= 1
                i += 6
                if depth == 0:
                    end = i
                    break
            else:
                i += 1
        if end > 0:
            blocks.append(src[start:end])
    return blocks


def has_close_affordance(block):
    if 'aria-label="بستن"' in block:
        return True
    return bool(re.search(r'onclick="[^"]*(?:closeDlg|closeResModal)\(\)"', block))


class DialogInventoryTests(unittest.TestCase):
    """No dialog/modal anywhere in panel.py lacks a close affordance."""

    def setUp(self):
        self.src = panel_src()
        self.dialogs = dialog_blocks(self.src)
        self.modals = modal_blocks(self.src)

    def test_inventory_is_nonempty_and_fully_closable(self):
        # the two known components: CRM shared #dlg + estelam #res-modal
        self.assertGreaterEqual(len(self.dialogs), 1, "no <dialog> found")
        self.assertGreaterEqual(len(self.modals), 1, "no class=modal found")
        for block in self.dialogs + self.modals:
            self.assertTrue(has_close_affordance(block),
                            "dialog/modal block lacks a close affordance: "
                            + block[:120])

    def test_crm_dlg_block_carries_the_chrome_x(self):
        dlg = [b for b in self.dialogs if 'id="dlg"' in b]
        self.assertEqual(len(dlg), 1, "expected exactly one id=dlg dialog")
        for marker in ('type="button"', 'class="dlg-x"',
                       'onclick="closeDlg()"', 'aria-label="بستن"'):
            self.assertIn(marker, dlg[0], marker)
        # the x glyph is the plain multiplication sign (no emoji — design gate)
        self.assertIn("</button>", dlg[0])
        self.assertNotIn("\U0001F464", dlg[0])
        # closeDlg() still defined
        self.assertIn("function closeDlg(){", self.src)

    def test_exactly_one_chrome_x_not_per_body(self):
        # one shared chrome button — openDlg bodies must never embed another
        self.assertEqual(panel.CRM_PAGE.count('class="dlg-x"'), 1)
        for fn in ("openDlg", "confirmDlg", "caseDetail", "txDetailModal"):
            body = fn_body(crm_js(), fn)
            self.assertTrue(body, fn + " not found")
            self.assertNotIn("dlg-x", body, fn + " must not embed a chrome x")

    def test_native_escape_path_untouched(self):
        # native cancel/ESC close: backdrop CSS present, plain opening tag
        # (no oncancel interception), showModal still opens #dlg
        # (v1.3.2: openDlg caches the element in `dlg` first)
        self.assertIn("dialog::backdrop{", self.src)
        self.assertIn("<dialog id=\"dlg\">", panel.CRM_PAGE)
        self.assertIn("dlg.showModal()", self.src)

    def test_estelam_modal_markers_survive(self):
        # site-2 result modal: untouched compliant chrome (.x button,
        # backdrop click, ESC keydown -> closeResModal)
        blocks = [b for b in self.modals if 'id="res-modal"' in b]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertIn('class="x"', block)
        self.assertIn('onclick="closeResModal()"', block)
        self.assertIn('aria-label="بستن"', block)
        self.assertIn(".modal .x{", self.src)
        self.assertIn("if(e.key==='Escape')closeResModal()", self.src)
        self.assertIn("function closeResModal(){", self.src)


class DialogChromeTests(unittest.TestCase):
    """CSS chrome for the shared x + openDlg still writes only #dlgBody."""

    def setUp(self):
        self.src = panel_src()
        self.js = crm_js()

    def test_dlg_x_css_pointer_and_hover(self):
        rule = re.search(r"\.dlg-x\{[^}]*\}", self.src)
        self.assertTrue(rule, ".dlg-x CSS rule not found")
        self.assertIn("cursor:pointer", rule.group(0))
        self.assertIn("position:absolute", rule.group(0))
        self.assertIn("inset-inline-end:10px", rule.group(0))
        hover = re.search(r"\.dlg-x:hover\{[^}]*\}", self.src)
        self.assertTrue(hover, ".dlg-x:hover rule not found")
        self.assertIn("color:var(--txt)", hover.group(0))

    def test_dialog_rule_is_positioned(self):
        rule = re.search(r"(?<![\w-])dialog\{[^}]*\}", self.src)
        self.assertTrue(rule, "dialog{...} rule not found")
        self.assertIn("position:relative", rule.group(0))

    def test_dialog_title_never_overlaps_the_x(self):
        rule = re.search(r"dialog h3\{[^}]*\}", self.src)
        self.assertTrue(rule, "dialog h3 rule not found")
        self.assertIn("padding-inline-end:34px", rule.group(0))

    def test_opendlg_still_sets_only_dlgbody(self):
        body = fn_body(self.js, "openDlg")
        self.assertTrue(body, "openDlg not found")
        self.assertIn("document.getElementById('dlgBody').innerHTML=html", body)
        self.assertEqual(body.count(".innerHTML="), 1,
                         "openDlg must set exactly one innerHTML (#dlgBody)")
        self.assertIn('<div id="dlgBody">', panel.CRM_PAGE)

    def test_toast_container_tracks_the_open_dialog(self):
        # v1.3.2: a showModal() dialog renders in the browser top layer,
        # above every z-index — toasts left in the body show dimmed
        # underneath it. The container must move INSIDE the dialog while it
        # is open (openDlg) and back to the body on every close — closeDlg
        # does it synchronously; the close listener is a stale-safe net.
        open_body = fn_body(self.js, "openDlg")
        self.assertIn(
            "dlg.appendChild(document.getElementById('toasts'))", open_body)
        listener = re.search(
            r"getElementById\('dlg'\)\.addEventListener\('close',"
            r"function\(\)\{[\s\S]*?\}\);", self.js)
        self.assertTrue(listener, "dlg close listener not found")
        self.assertIn(
            "document.body.appendChild(document.getElementById('toasts'))",
            listener.group(0))


class GuideAndGroupDesignTests(unittest.TestCase):
    """v1.3.5 — the step guide returns to the case modal, action clusters
    sit in framed groups, and case-modal buttons use the SVG icon language
    (no emoji)."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()
        self.src = panel_src()

    def test_guide_returns_to_the_case_modal(self):
        body = fn_body(self.js, "stageGuideModal")
        self.assertTrue(body, "stageGuideModal not found")
        self.assertIn("DLG_BACK=back", body)
        self.assertIn("بازگشت به پرونده", self.page)
        # v1.3.5 fix: the 'close' event only arrives asynchronously in
        # Chromium, so closeDlg does ALL the bookkeeping synchronously —
        # including the DLG_BACK restore.
        close_body = fn_body(self.js, "closeDlg")
        self.assertIn("var back=DLG_BACK;DLG_BACK='';", close_body)
        self.assertIn(
            "document.body.appendChild(document.getElementById('toasts'))",
            close_body)
        # H1 review: any dialog replacing content disarms a stale restore
        # (an in-flight action callback must not leave DLG_BACK armed) —
        # and any dialog opened over a LIVE case modal arms the return,
        # so انصراف/×/Esc on child dialogs comes back to the case.
        open_body = fn_body(self.js, "openDlg")
        self.assertIn("DLG_BACK=CASE_MODAL.live?CASE_MODAL.id:'';", open_body)
        # closing the case modal itself must NOT reopen it
        self.assertIn("var self=CASE_MODAL.live&&CASE_MODAL.id===back;",
                      close_body)
        self.assertIn("if(back&&!self)caseDetail(back);", close_body)
        # a confirmed action never auto-returns — its callback decides
        self.assertIn(
            "function confirmYes(){var cb=_confirmCb;_confirmCb=null;"
            "DLG_BACK='';closeDlg();if(cb)cb();}", self.js)
        # Esc path: native cancel is taken over and routed through closeDlg
        self.assertRegex(
            self.js,
            r"addEventListener\('cancel',function\(e\)\{"
            r"\s*e\.preventDefault\(\);\s*closeDlg\(\);\s*\}\);")

    def test_problem_rail_dot_opens_the_problem_guide(self):
        self.assertIn("(failed&&st==='inquiry'?'problem':st)", self.js)

    def test_action_groups_are_framed(self):
        body = fn_body(self.js, "caseDetail")
        self.assertIn('<div class="grp"><div class="grp-lbl">ابزار این گام</div>',
                      body)
        self.assertIn('<div class="grp"><div class="grp-lbl">پرداخت</div>', body)
        self.assertIn(
            '<div class="grp grp-quiet"><div class="grp-lbl">سایر اقدامات</div>',
            body)
        self.assertIn(".grp{", self.src)
        self.assertIn(".grp-quiet{background:transparent;border-style:dashed}",
                      self.src)

    def test_case_modal_buttons_carry_no_emoji(self):
        for emoji in ("🔗", "🧾", "💳", "🙈", "👁"):
            self.assertNotIn(emoji, self.page,
                             "emoji leaked back into the CRM page")

    def test_launch_page_defaults_to_light(self):
        # v1.3.5 (operator request): the launch card is light by default
        # like every other page — no hardcoded data-theme="dark".
        self.assertIn('LAUNCH_PAGE = r"""<!DOCTYPE html>\n<html lang="fa" dir="rtl">',
                      self.src)

    def test_guide_problem_content_and_labels(self):
        # the dedicated problem guide + the guide chrome labels
        self.assertIn("STAGE_GUIDE_PROBLEM:g.desc", self.js)
        self.assertIn("دارای اشکال در گام ۲ — استعلام ثبت‌نبودن", self.page)
        # the button label depends on whether a case modal is behind us
        self.assertIn("(back?'بازگشت به پرونده':'بستن')", self.js)
        self.assertIn("گام '+fa(g.n)+' از '+fa(RAIL_STEPS.length)", self.js)

    def test_quiet_row_renames(self):
        body = fn_body(self.js, "caseDetail")
        self.assertIn(">ویرایش پرونده</button>", body)
        self.assertIn(">حذف پرونده</button>", body)
        self.assertNotIn(">حذف</button>", body)

    def test_dev_delete_uses_styled_confirm(self):
        # v1.3.5 (H2 review): destructive actions use the styled
        # confirmDlg, never the native blocking confirm()
        body = fn_body(self.js, "devDelete")
        self.assertIn("confirmDlg('حذف پرونده'", body)
        self.assertNotIn("!confirm(", body)


class TimestampSurfaceTests(unittest.TestCase):
    """created/updated (+ «تاریخ تکمیل» for done) on every case surface."""

    def setUp(self):
        self.js = crm_js()

    def test_devrow_shows_created_and_updated(self):
        body = fn_body(self.js, "devRow")
        self.assertTrue(body, "devRow not found")
        self.assertIn("esc(d.created||'—')", body)
        self.assertIn("esc(d.updated||'—')", body)

    def test_done_tab_completion_date_chip(self):
        body = fn_body(self.js, "doneTabHtml")
        self.assertTrue(body, "doneTabHtml not found")
        self.assertIn("تاریخ تکمیل", body)
        self.assertIn("esc(d.done_at||'—')", body)

    def test_closed_tab_created_updated_and_preserved_markers(self):
        body = fn_body(self.js, "closedTabHtml")
        self.assertTrue(body, "closedTabHtml not found")
        self.assertIn("esc(d.created||'—')", body)
        self.assertIn("esc(d.updated||'—')", body)
        # v1.1.5 marker protection: reason/date chips survive byte-exact
        self.assertIn("esc(d.closed_reason||'—')", body)
        self.assertIn("esc(d.closed_at||'—')", body)


class QuickBoxMarkerTests(unittest.TestCase):
    """Lost id=quickBox marker restored (v1.3 UI contract)."""

    def test_quickbox_id_present(self):
        self.assertIn('id="quickBox"', panel.CRM_PAGE)
        self.assertIn('<div class="quickbox" id="quickBox">', panel.CRM_PAGE)


class TxKindLabelTests(unittest.TestCase):
    """base_fee_refund (auto registry-fee reversal on delete) is labeled."""

    def test_base_fee_refund_label(self):
        src = panel_src()
        match = re.search(r"var TX_KIND_FA=\{[^;]*\};", src)
        self.assertTrue(match, "TX_KIND_FA not found")
        self.assertIn("'base_fee_refund'", match.group(0))
        self.assertIn("'base_fee_refund':'بازگشت هزینه ثبت رجیستری'",
                      match.group(0))


if __name__ == "__main__":
    unittest.main()
