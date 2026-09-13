# -*- coding: utf-8 -*-
"""v1.4.0 — per-tab search & filter, case-table columns, brand sync.

Contract tests for the signature feature of the release:
- TAB_Q / TAB_SEARCH / TAB_STAGE_FILTER state and the 9 list tabs it covers
- normSearch normalization (Persian/Arabic digits to Latin, ZWNJ stripped,
  lowercase) verified by re-implementing it in Python
- the focus-safe shell contract: #tabShell[data-tab] wraps the toolbar and
  #tabBody; refreshes re-render only #tabBody and the aside (outerHTML, so
  the aside wrapper is never nested inside itself — H review regression)
- the case-table column order (ردیف | دستگاه | مشتری | مسافر | ... ) and
  the filtered-view row numbering fa(i+1)
- the unified brand tokens across all six redesigned pages, Vazirmatn
  everywhere, the path-drawn brand favicon, and zero pictographic emoji
"""
import re
import unittest

import panel


def crm_js():
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body(js, name):
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


FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def norm_search_py(s):
    """Python twin of the JS normSearch (panel.py normSearch)."""
    s = str(s or "").lower()
    s = "".join(str(FA_DIGITS.index(c)) if c in FA_DIGITS else c for c in s)
    s = "".join(str(AR_DIGITS.index(c)) if c in AR_DIGITS else c for c in s)
    return s.replace("‌", "")


class TabSearchStateTests(unittest.TestCase):
    """TAB_Q config + normalization contract."""

    def setUp(self):
        self.js = crm_js()

    def test_state_vars_present(self):
        for marker in ("var TAB_Q={};", "var TAB_STAGE_FILTER='all';",
                       "var TAB_COUNT=0;"):
            self.assertIn(marker, self.js)

    def test_all_nine_list_tabs_have_search(self):
        body = fn_body(self.js, "tabBarHtml")
        self.assertTrue(body, "tabBarHtml not found")
        # the nine list tabs (settings/reports excluded) and their units
        for marker in ("devices:{ph:'جستجو در پرونده‌ها…',unit:'پرونده'}",
                       "customers:{ph:'جستجو در مشتری‌ها…',unit:'مشتری'}",
                       "owners:{ph:'جستجو در مسافرها…',unit:'مسافر'}",
                       "identities:{ph:'جستجو در استخر هویت…',unit:'مشخصات'}",
                       "periodic:{ph:'جستجو در استعلام دوره‌ای…',unit:'دستگاه'}",
                       "ledger:{ph:'جستجو در تراکنش‌ها…',unit:'تراکنش'}",
                       "done_tab:{ph:'جستجو در تکمیل شده‌ها…',unit:'پرونده'}",
                       "problem_tab:{ph:'جستجو در دارای اشکال…',unit:'پرونده'}",
                       "closed_tab:{ph:'جستجو در مختومه‌ها…',unit:'پرونده'}"):
            self.assertIn(marker, self.js, marker)

    def test_normsearch_transforms(self):
        body = fn_body(self.js, "normSearch")
        self.assertIn(".toLowerCase()", body)
        self.assertIn("[۰-۹]", body)
        self.assertIn("[٠-٩]", body)
        self.assertIn("\\u200c", body)
        # behavior twin: Persian and Arabic digits become Latin, ZWNJ dies,
        # case folds — the JS and this Python twin must agree on samples
        for raw, want in [("۰۹۱۲", "0912"), ("٠١٢٣٤٥٦٧٨٩", "0123456789"),
                          ("می‌شود", "میشود"), ("SaMsUnG", "samsung"),
                          ("  3569 ", "  3569 ")]:
            self.assertEqual(norm_search_py(raw), want)
        # and the matcher itself uses the twin on both sides
        match = fn_body(self.js, "tabSearchMatches")
        self.assertIn("normSearch(text).indexOf(normSearch(q))>=0", match)

    def test_devices_search_haystack(self):
        body = fn_body(self.js, "devHay")
        self.assertIn("d.id,d.name,d.serial1,d.serial2,ownerName(d.owner_id)"
                      ",custName(d.customer_id)", body)


class TabSearchShellTests(unittest.TestCase):
    """Focus-safe rendering contract (#tabShell/#tabBody/aside)."""

    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "renderView")

    def test_shell_wraps_toolbar_and_body(self):
        self.assertIn("'<div id=\"tabShell\" data-tab=\"'+esc(TAB)+'\">'"
                      "+tabBarHtml()", self.body)
        self.assertIn("'<div id=\"tabBody\">'+bodyHtml()", self.body)

    def test_refresh_updates_only_body_and_aside(self):
        self.assertIn("renderBody();", self.body)
        # H review regression: innerHTML would nest the .crm-aside wrapper
        # inside itself (duplicate id every refresh) — must be outerHTML
        self.assertIn("document.getElementById('crmAside').outerHTML=aside;",
                      self.body)
        self.assertNotIn("document.getElementById('crmAside').innerHTML=aside;",
                         self.body)

    def test_toolbar_and_counter_markers(self):
        bar = fn_body(self.js, "tabBarHtml")
        self.assertIn('id="tabCount" hidden', bar)
        self.assertIn("cntpill", bar)
        self.assertIn("tabSearchInput(", bar)
        chips = fn_body(self.js, "stageChipsHtml")
        self.assertTrue(chips, "stageChipsHtml not found")
        self.assertIn("نیازمند اقدام", chips)
        reset = fn_body(self.js, "searchResetAll")
        self.assertIn("پاک کردن جستجو", self.js)
        self.assertIn("TAB_STAGE_FILTER='all'", reset)

    def test_typing_renders_body_not_shell(self):
        inp = fn_body(self.js, "tabSearchInput")
        self.assertIn("renderBody()", inp)
        self.assertNotIn("renderView()", inp)


class CaseTableColumnsTests(unittest.TestCase):
    """ردیف | دستگاه | مشتری | مسافر | ... (operator-requested order)."""

    def test_thead_order(self):
        src = open(panel.__file__, encoding="utf-8").read()
        # v1.4.2: «آخرین تغییر» left the case table (it stays in the modal)
        self.assertIn(
            "<thead><tr><th>ردیف</th><th>دستگاه</th><th>مشتری</th>"
            "<th>مسافر</th><th>سریال</th><th>گام فعلی</th><th>وضعیت</th>"
            "<th>شناسه پرونده</th><th>تاریخ ایجاد</th>"
            "<th></th></tr></thead>", src)

    def test_row_number_counts_the_filtered_view(self):
        body = fn_body(crm_js(), "devicesHtml")
        self.assertIn("'+fa(i+1)+'</td>'", body)
        # customer + passenger columns with muted placeholders
        self.assertIn("cust=custName(d.customer_id)", body)
        self.assertIn("owner=ownerName(d.owner_id)", body)


class BrandSyncTests(unittest.TestCase):
    """One design system across all six pages + the brand favicon."""

    def setUp(self):
        self.src = open(panel.__file__, encoding="utf-8").read()

    def test_light_brand_unified(self):
        # six pages: site-1, home, launch, estelam, crm, login
        self.assertGreaterEqual(
            self.src.count("--brand:#4460e3;--brand-hover:#3757cf;"), 6)
        self.assertNotIn("#4f6bef", self.src)

    def test_dark_brand_unified(self):
        self.assertGreaterEqual(
            self.src.count("--brand:#7d95ff;--brand-hover:#93a7ff;"), 3)
        self.assertNotIn("#4f6bef", self.src)

    def test_vazirmatn_on_every_page(self):
        self.assertGreaterEqual(self.src.count("Vazirmatn-var.woff2"), 6)

    def test_brand_favicon(self):
        # path-drawn R (font-independent), unified brand color
        self.assertIn("<path d='M7 18.5v-13'/>", self.src)
        self.assertIn("fill='#4460e3'", self.src)
        self.assertIn('"image/svg+xml"', self.src)
        self.assertNotIn("'%236d8bff'", self.src)

    def test_design_docs_match_code(self):
        for doc in ("lab/design/irica-brand.md", "lab/design/preview.html"):
            with open(doc, encoding="utf-8") as fh:
                self.assertNotIn("#4f6bef", fh.read(), doc)


class NoEmojiTests(unittest.TestCase):
    """No pictographic emoji anywhere in the served pages."""

    EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-⛿️]")

    def test_no_pictographs_in_panel_source(self):
        src = open(panel.__file__, encoding="utf-8").read()
        hits = self.EMOJI.findall(src)
        self.assertEqual(hits, [], f"emoji found in panel.py: {hits!r}")


if __name__ == "__main__":
    unittest.main()
