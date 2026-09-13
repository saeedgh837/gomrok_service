"""v1.1.5 — CRM left-side summary rail on the devices view.

Three compact cards stacked in a narrow column visually LEFT of the device
cards area (RTL layout), collapsing below the cards on narrow widths:

- «جدیدترین مشتری‌ها» — newest customers (created desc), rows open the
  existing customer edit dialog dlgCustomer('edit', id). One click.
- «پرونده‌های اخیر» — devices sorted by d.updated desc, click scrolls to and
  briefly highlights the device card (stable anchor id="devcard-<id>").
- «بیشترین بدهکاری» — customers with bal(id) < 0, most negative first,
  top 5 + muted overflow line.

Pure client-side from STATE: no new HTTP routes, no polling changes,
no backend edits. All interpolated strings pass through esc().
"""
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import panel  # noqa: E402

T_SUM_CUSTOMERS = "جدیدترین مشتری‌ها"
T_SUM_CASES = "پرونده‌های اخیر"
T_SUM_DEBTORS = "بیشترین بدهکاری"

RAIL_FNS = ["devicesRailHtml", "sumCustomersHtml", "sumBalPill",
            "sumCasesHtml", "sumFocusDevice", "sumDebtorsHtml"]


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE."""
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body(js, name):
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


class SummaryRailMarkupTests(unittest.TestCase):
    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_rail_renders_in_the_aside_on_devices_tab(self):
        # v1.3.4 (operator request): the rail moved into the aside, above
        # the history card, so the case table owns the full main column.
        view = fn_body(self.js, "renderView")
        self.assertTrue(view, "renderView not found")
        self.assertIn(
            "(TAB==='devices'?'<div class=\"devrail\">'+devicesRailHtml()"
            "+'</div>':'')", view)
        self.assertIn("kpisHtml()", view)
        body = fn_body(self.js, "devicesHtml")
        self.assertNotIn("devicesRailHtml()", body,
                         "the rail must not narrow the case table")

    def test_three_card_headers_present(self):
        for title in (T_SUM_CUSTOMERS, T_SUM_CASES, T_SUM_DEBTORS):
            self.assertIn(title, self.js)

    def test_rail_functions_defined(self):
        for fn in RAIL_FNS:
            self.assertTrue(re.search(r"function\s+" + re.escape(fn) + r"\s*\(",
                                      self.js), "missing function " + fn)

    def test_css_two_column_layout(self):
        self.assertIn(".crm-cols{display:grid", self.page)
        self.assertIn("grid-template-columns:minmax(0,1fr)", self.page)

    def test_css_collapses_below_cards_on_narrow_widths(self):
        self.assertIn("@media (max-width:1100px){.crm-cols{grid-template-columns:1fr}",
                      self.page)

    def test_device_card_anchor_ids_added(self):
        self.assertIn('id="devcard-\'+d.id+\'"', self.js)

    def test_focus_highlight_css_present(self):
        self.assertIn(".dev.flash{animation:devflash", self.page)
        self.assertIn("@keyframes devflash", self.page)

    def test_reduced_motion_disables_flash(self):
        m = re.search(r"@media \(prefers-reduced-motion:reduce\)\{"
                      r"\.dev\.flash\{animation:none\}\}", self.page)
        self.assertTrue(m, "reduced-motion guard for .dev.flash missing")


class SummaryCustomersRailTests(unittest.TestCase):
    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "sumCustomersHtml")

    def test_sorted_by_created_desc(self):
        self.assertIn(
            "String(b.created||'').localeCompare(String(a.created||''))",
            self.body)

    def test_rows_capped_at_five(self):
        self.assertIn("slice(0,5)", self.body)

    def test_click_opens_existing_edit_dialog(self):
        self.assertIn("dlgCustomer(\\'edit\\'", self.body)

    def test_names_and_mobile_escaped(self):
        self.assertIn("esc(c.full_name)", self.body)
        self.assertIn("esc(c.mobile||'—')", self.body)

    def test_balance_pill_reuses_bal_semantics(self):
        pill = fn_body(self.js, "sumBalPill")
        self.assertIn("bal(id)<0", pill)
        self.assertIn("بدهکار", pill)
        self.assertIn("تسویه", pill)

    def test_empty_state_one_muted_line(self):
        self.assertIn('class="muted"', self.body)


class SummaryCasesRailTests(unittest.TestCase):
    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "sumCasesHtml")

    def test_sorted_by_updated_desc(self):
        self.assertIn(
            "String(b.updated||'').localeCompare(String(a.updated||''))",
            self.body)

    def test_rows_capped_at_five(self):
        self.assertIn("slice(0,5)", self.body)

    def test_rows_show_serial_stage_and_tone_dot(self):
        self.assertIn("esc(d.serial1", self.body)
        self.assertIn("STAGE_FA[d.stage]", self.body)
        self.assertIn("STAGE_TONE[d.stage]", self.body)

    def test_click_focuses_device_card(self):
        self.assertIn("sumFocusDevice(\\'", self.body)
        focus = fn_body(self.js, "sumFocusDevice")
        self.assertIn("getElementById('devcard-'+id)", focus)
        self.assertIn("scrollIntoView", focus)
        self.assertIn("classList.add('flash')", focus)

    def test_empty_state_one_muted_line(self):
        self.assertIn('class="muted"', self.body)


class SummaryDebtorsRailTests(unittest.TestCase):
    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "sumDebtorsHtml")

    def test_only_negative_balances_kept(self):
        self.assertIn("bal(c.id)<0", self.body)

    def test_sorted_most_negative_first(self):
        self.assertIn(".sort((a,b)=>bal(a.id)-bal(b.id))", self.body)

    def test_rows_capped_at_five_with_overflow_line(self):
        self.assertIn("slice(0,5)", self.body)
        self.assertIn("مورد دیگر", self.body)
        self.assertIn("fa(", self.body)

    def test_amount_in_red_pill(self):
        self.assertIn('pill bad', self.body)
        self.assertIn("money(bal(c.id))", self.body)

    def test_click_opens_existing_edit_dialog(self):
        self.assertIn("dlgCustomer(\\'edit\\'", self.body)

    def test_empty_state_one_muted_line(self):
        self.assertIn('class="muted"', self.body)


class SummaryRailPurityTests(unittest.TestCase):
    """Pure client-side rendering: no new HTTP calls from the rail."""

    def test_no_fetch_in_rail_functions(self):
        js = crm_js()
        for fn in RAIL_FNS:
            body = fn_body(js, fn)
            self.assertNotIn("fetch(", body, fn + " must not fetch")
            self.assertNotIn("XMLHttpRequest", body, fn + " must not XHR")


if __name__ == "__main__":
    unittest.main()
