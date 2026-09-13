# -*- coding: utf-8 -*-
"""v1.5 B7 — devices tab as a case table + case DETAIL modal.

The devices tab becomes a clean case list: a responsive table (id, passenger,
device, masked serial, stage pill, estelam/hamta status, created/updated)
whose only controls are «+ پروندهٔ جدید», a per-row «جزئیات» button and the
global stop-robot banner. Every per-case action that used to sit on the card
(stage actions, continue, inquiries, periodic opt-in/out, pay controls,
edit, delete, close-case via stageActions) moves into the caseDetail modal —
reusing the existing handlers verbatim, adding zero new action literals.
sumFocusDevice routes to the tab owning the case (done/problem/closed) and
retries the lookup once instead of looping forever.
"""
import re
import unittest

import crm
import panel
import lab.tests.test_crm_api as _crm_api


def crm_page_src():
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


TABLE_HEADERS = ("شناسه پرونده", "مسافر", "دستگاه", "سریال",
                 "گام فعلی", "وضعیت", "تاریخ ایجاد", "آخرین تغییر")


class CaseListSourceTests(unittest.TestCase):
    """B7 markers on CRM_PAGE source (static checks, no browser)."""

    def setUp(self):
        self.src = crm_page_src()
        self.js = crm_js()

    def test_case_detail_modal_and_mask_serial_defined(self):
        self.assertIn("function caseDetail(id){", self.js)
        self.assertIn("function maskSerial(s){", self.js)
        self.assertIn("s.length<=8?s:s.slice(0,4)+'…'+s.slice(-3)", self.js)

    def test_case_table_headers_present(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertTrue(body, "devicesHtml not found")
        for header in TABLE_HEADERS:
            self.assertIn("<th>" + header + "</th>", body, header)

    def test_rows_are_detail_opens_with_devcard_anchor(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertIn("caseDetail(\\''+d.id+'\\')", body)   # row + button click
        self.assertIn("id=\"devcard-'+d.id+'\"", self.js)   # rail focus anchor
        self.assertIn(">جزئیات</button>", body)
        self.assertIn("maskSerial(d.serial1)", body)
        self.assertIn("ownerName(d.owner_id)", body)
        self.assertIn("esc(d.created||'—')", body)
        self.assertIn("esc(d.updated||'—')", body)

    def test_table_wrapper_css(self):
        self.assertIn(".tbl-wrap{overflow-x:auto}", self.src)
        self.assertIn("tbl-wrap", fn_body(self.js, "devicesHtml"))

    def test_actions_moved_into_modal_not_the_list(self):
        body = fn_body(self.js, "caseDetail")
        self.assertTrue(body, "caseDetail not found")
        for marker in ("stageActions(d)", "stageContinueClick(", "devInquire(",
                       "dlgDeviceEdit(", "devDelete(", "railHtml(d)",
                       # v1.3.1 round 3: every tool lives on its own step only
                       "d.stage==='inquiry'",
                       "d.stage==='to_payment'",
                       "d.stage==='handed'",
                       "st==='problem'",
                       "devRequeue(\\''+d.id+'\\')"):
            self.assertIn(marker, body, marker)
        # passenger block shows the passport (v1.5 owner contract)
        self.assertIn("esc(o.passport||'—')", body)
        # the gomrok inquiry button keeps the identity-pool gate (T1)
        self.assertIn("gomrokGateAttrs()+' onclick=\"devInquire(", self.src)


class CaseListStructureTests(unittest.TestCase):
    """The devices list itself carries no per-case actions (B7)."""

    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "devicesHtml")
        self.assertTrue(self.body, "devicesHtml not found")

    def test_no_per_case_actions_in_the_list(self):
        for marker in ("devDelete(", "dlgDeviceEdit(", "__close_case",
                       "devInquire(", "payStop(", "payBrowser(",
                       "periodicRemove(", "periodicReAdd(", "dlgAttach(",
                       "doneRollback("):
            self.assertNotIn(marker, self.body,
                             marker + " leaked into the case list")

    def test_case_detail_opens_from_the_list(self):
        self.assertIn("caseDetail(", self.body)

    def test_list_area_only_global_buttons(self):
        # new-case button, per-row details button, global stop-robot banner
        self.assertIn("dlgDevice()", self.body)
        self.assertIn("+ پروندهٔ جدید", self.body)
        self.assertIn("crmStopRun()", self.body)
        self.assertIn("توقف ربات استعلام", self.body)
        # empty-state branch (B4) stays: points at the section tabs
        self.assertIn("emptyHtml(", self.body)
        for tab_label in ("تکمیل شده‌ها", "دارای اشکال", "مختومه‌ها"):
            self.assertIn(tab_label, self.body)


class SumFocusRoutingTests(unittest.TestCase):
    """sumFocusDevice routes to the owning tab and retries once (B7)."""

    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "sumFocusDevice")
        self.assertTrue(self.body, "sumFocusDevice not found")

    def test_keeps_flash_and_anchor_contract(self):
        self.assertIn("getElementById('devcard-'+id)", self.body)
        self.assertIn("scrollIntoView", self.body)
        self.assertIn("classList.add('flash')", self.body)

    def test_routes_by_stage_to_owning_tab(self):
        self.assertIn("setTab('done_tab')", self.body)
        self.assertIn("setTab('problem_tab')", self.body)
        self.assertIn("setTab('closed_tab')", self.body)
        self.assertIn("setTab('devices')", self.body)

    def test_devrow_ids_tried_and_retry_is_bounded(self):
        self.assertIn("devrow-'+id", self.body)
        self.assertIn("setTimeout(flashRow,250)", self.body)
        # the old infinite self-retry loop is gone (only the definition remains)
        self.assertEqual(self.body.count("sumFocusDevice"), 1)


class PreservedMarkerTests(unittest.TestCase):
    """Every byte-exact marker the B7 rebuild had to keep (v1.1.5 rail,
    v1.3 payment row, T1 gate, design system, stage maps)."""

    def setUp(self):
        self.src = crm_page_src()
        self.js = crm_js()

    def test_layout_contract_with_summary_rail(self):
        # v1.3.4: the rail renders in the renderView aside (above the
        # history card) instead of narrowing the case table.
        view = fn_body(self.js, "renderView")
        self.assertIn(
            "(TAB==='devices'?'<div class=\"devrail\">'+devicesRailHtml()"
            "+'</div>':'')", view)
        body = fn_body(self.js, "devicesHtml")
        self.assertNotIn("devicesRailHtml()", body)
        self.assertIn(".crm-cols{display:grid", self.src)
        self.assertIn("grid-template-columns:minmax(0,1fr)", self.src)
        self.assertIn("@media (max-width:1100px){.crm-cols{grid-template-columns:1fr}",
                      self.src)

    def test_focus_flash_css(self):
        self.assertIn(".dev.flash{animation:devflash", self.src)
        self.assertIn("@keyframes devflash", self.src)
        self.assertRegex(
            self.src,
            r"@media \(prefers-reduced-motion:reduce\)\{"
            r"\.dev\.flash\{animation:none\}\}")

    def test_rail_summary_bodies(self):
        for fn, markers in (
            ("sumCustomersHtml", ("slice(0,5)", "dlgCustomer(\\'edit\\'",
                                  "esc(c.mobile||'—')")),
            ("sumCasesHtml", ("slice(0,5)", "esc(d.serial1",
                              "STAGE_FA[d.stage]", "STAGE_TONE[d.stage]",
                              "sumFocusDevice(\\'")),
            ("sumDebtorsHtml", ("bal(c.id)<0", "مورد دیگر",
                                ".sort((a,b)=>bal(a.id)-bal(b.id))")),
            ("sumBalPill", ("bal(id)<0", "بدهکار", "تسویه")),
        ):
            body = fn_body(self.js, fn)
            self.assertTrue(body, fn + " not found")
            for marker in markers:
                self.assertIn(marker, body, fn + ": " + marker)

    def test_rail_functions_stay_pure_client_side(self):
        for fn in ("devicesRailHtml", "sumCustomersHtml", "sumBalPill",
                   "sumCasesHtml", "sumFocusDevice", "sumDebtorsHtml"):
            body = fn_body(self.js, fn)
            self.assertNotIn("fetch(", body, fn + " must not fetch")
            self.assertNotIn("XMLHttpRequest", body, fn + " must not XHR")

    def test_payment_row_markers_survive_in_modal(self):
        for marker in ("btn-continue", "payRow", "بستن ربات", "نمایش مرورگر",
                       "لینک پرداخت", "روبات پرداخت خودکار در جریان است",
                       "devInquire", "crmStopRun",
                       "استعلام گمرک", "استعلام همتا"):
            self.assertIn(marker, self.src, marker)

    def test_stage_order_and_skip_maps_unchanged(self):
        self.assertIn("var RAIL_STEPS = ['new','inquiry','ready_owner','handed',"
                      "'to_payment','payment','payment_done','done']", self.src)
        skip_next = re.search(r"var STAGE_SKIP_NEXT = \{([\s\S]*?)\};", self.js)
        self.assertTrue(skip_next, "STAGE_SKIP_NEXT not found")
        self.assertNotIn("'payment_done':", skip_next.group(1))
        self.assertIn("'problem':'inquiry'", skip_next.group(1))

    def test_design_system_css_markers(self):
        for marker in ("counter-reset:stage", "counter-increment:stage",
                       "content:counter(stage)",
                       ".kpi:first-child{border-inline-start:none}",
                       "border-inline-start:1px solid var(--line)"):
            self.assertIn(marker, self.src, marker)

    def test_no_emoji_metadata_chips(self):
        # design-gate scope is the CRM page (other panel pages may differ)
        page = panel.CRM_PAGE
        for emoji in ("\U0001F464", "\U0001FAAA"):  # 👤 🪪
            self.assertNotIn(emoji, page)
        self.assertIn("var ICONS", page)
        self.assertIn('<svg class="ic"', page)


class MarkerCountSafetyTests(unittest.TestCase):
    """B8 guard rails: exactly one deliver_code, no skip-to-done pairing."""

    def setUp(self):
        self.src = crm_page_src()

    def test_deliver_code_literal_appears_exactly_once(self):
        self.assertEqual(self.src.count("deliver_code"), 1)

    def test_no_payment_done_to_done_skip_pairing(self):
        self.assertNotIn("'payment_done':'done'", self.src)


class CaseListHttpSmoke(_crm_api.CrmApiHarness):
    """GET /crm serves the new case-table page (auth-gated route)."""

    def test_crm_page_serves_case_table(self):
        device, errors = crm.add_device({"name": "iPhone 13",
                                         "serial1": "356938035643809"})
        self.assertEqual(errors, [])
        # v1.5 owner contract: passport required on save (card optional)
        owner, errors = crm.add_owner({
            "first_name": "نگار", "last_name": "کریمی",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "card_no": "6219861012345678",
            "passport": "AB1234567"})
        self.assertEqual(errors, [])
        status, page, _ctype = self._get("/crm")
        self.assertEqual(status, 200)
        for marker in ("function caseDetail(id){", "maskSerial",
                       ".tbl-wrap{overflow-x:auto}", ">جزئیات</button>",
                       "caseDetail(\\''+d.id+'\\')") + TABLE_HEADERS:
            self.assertIn(marker, page, marker)
        self.assertIn('id="devcard-', page)


if __name__ == "__main__":
    unittest.main()
