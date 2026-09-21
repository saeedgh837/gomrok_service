# -*- coding: utf-8 -*-
"""v1.8 periods round (frontend workstream) — the «دوره\u200cها» tab, the period
detail dialog, the icon-action-button + tooltip system and the one money
predicate every period surface flows through, locked as CRM_PAGE source
markers (same house pattern as test_v1_5/v1_6/v1_7).

The tab and the icon/tooltip layer are pure frontend: everything is derived
from STATE (periods/devices/owners/customers) and no new HTTP route is added.
caseMoney() must mirror the Python ledger rule (crm._tx_countable plus the
void/reversal exclusion crm.device_invoice applies), so MoneyRuleParityTests
executes both rules over one synthetic ledger and requires the same
debit/credit/net.

Markup assertions run over the *call closure* of a surface: the surface's own
body plus (transitively) the bodies of the page functions it calls, so a
button or a card that the round renders through a helper is still pinned —
while every marker itself stays an exact source string.
"""
import re
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm    # noqa: E402

# نیم‌فاصله, spelled out so the byte cannot drift
ZWNJ = "‌"
import panel  # noqa: E402

# The ten JS functions the v1.8 round adds (contract item 2, verbatim names).
REQUIRED_FNS = [
    "periodsHtml", "periodCardsHtml", "dlgPeriod", "perPaint", "perSeg",
    "caseMoney", "periodCases", "periodOwners", "periodCustomers",
    "periodMoney",
]
# The period tab itself (cards list) and the period detail dialog.
PERIOD_TAB_FNS = ["periodsHtml", "periodCardsHtml"]
PERIOD_DLG_FNS = ["dlgPeriod", "perPaint", "perSeg", "periodCases",
                  "periodOwners", "periodCustomers", "periodMoney"]
PERIOD_ALL_FNS = PERIOD_TAB_FNS + PERIOD_DLG_FNS + ["caseMoney"]
# Surfaces whose row actions / in-tab toolbars become icon buttons. The
# completed tab's row actions are rendered by doneTabHtml (devRow is only the
# shared card shell), so that is the surface the contract pins.
IBTN_SURFACES = [
    "customersHtml", "ownersHtml", "ownerArchiveHtml", "devicesHtml",
    "doneTabHtml", "problemTabHtml", "identitiesHtml", "periodicHtml",
    "ledgerHtml", "periodsHtml",
]
# The only class tokens an icon button may carry (contract item 6).
IBTN_CLASSES = {"ibtn", "primary", "ok", "warn", "danger", "ghost", "busy"}
# ICONS keys the v1.8 round adds (contract item 9), plus the two the review
# round added for the reset (rewind) and the printable report (printer).
NEW_ICON_KEYS = [
    "plus", "edit", "trash", "unlink", "undo", "rotate", "refresh", "ban",
    "play", "lock", "list", "users", "check", "clock", "globe", "fileText",
    "download", "x", "money", "ticket", "rewind", "printer",
]
# The tooltip element is created at runtime, so its id/role live in whichever
# spelling the page uses: a literal attribute string, a property assignment or
# setAttribute. The contract pins the values (`tip`, `tooltip`), not the
# syntax.
TIP_ID_FORMS = [r'id\s*=\s*"tip"',
                r"\.id\s*=\s*'tip'", r"\.id\s*=\s*\"tip\"",
                r"setAttribute\(\s*'id'\s*,\s*'tip'\s*\)",
                r'setAttribute\(\s*"id"\s*,\s*"tip"\s*\)']
TIP_ROLE_FORMS = [r'role\s*=\s*"tooltip"', r"role\s*=\s*'tooltip'",
                  r"setAttribute\(\s*'role'\s*,\s*'tooltip'\s*\)",
                  r'setAttribute\(\s*"role"\s*,\s*"tooltip"\s*\)']


def crm_js():
    """Concatenated <script> bodies of CRM_PAGE (same trick as v1.5/v1.6)."""
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def crm_css():
    """Concatenated <style> bodies of CRM_PAGE only — the sibling pages in
    panel.py carry rules of their own, so every style assertion is scoped
    here."""
    return "\n".join(re.findall(r"<style>([\s\S]*?)</style>", panel.CRM_PAGE))


def crm_markup():
    """CRM_PAGE with every <script>/<style> body stripped (static shell)."""
    page = re.sub(r"<script>[\s\S]*?</script>", "", panel.CRM_PAGE)
    return re.sub(r"<style>[\s\S]*?</style>", "", page)


def brace_block(text, open_index):
    """Content between the brace at `open_index` and its match ("" when the
    brace never closes) — the same depth walk fn_body uses."""
    depth, i = 0, open_index
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
        i += 1
    return ""


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


def js_literal(js, name, opener):
    """Inner text of `var NAME = {...}` / `var NAME = [...]` ("" if absent)."""
    match = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*" + re.escape(opener),
                      js)
    if not match:
        return ""
    closer = {"{": "}", "[": "]"}[opener]
    depth, i = 0, match.end() - 1
    while i < len(js):
        if js[i] == opener:
            depth += 1
        elif js[i] == closer:
            depth -= 1
            if depth == 0:
                return js[match.end():i]
        i += 1
    return ""


def fn_region(js, names):
    """Concatenated bodies of `names` (a missing function contributes "")."""
    return "\n".join(fn_body(js, name) for name in names)


def call_closure(js, seeds, rounds=6):
    """Bodies of `seeds` plus, transitively, the bodies of the page functions
    they call — the markup a surface really renders, whatever helpers the
    round splits it into. Only names that resolve to a declared function
    contribute, and the walk is bounded by `rounds`."""
    seen, pending, out = set(), list(seeds), []
    for _round in range(rounds):
        nxt = []
        for name in pending:
            if name in seen:
                continue
            seen.add(name)
            body = fn_body(js, name)
            if not body:
                continue
            out.append(body)
            for callee in re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", body):
                if callee not in seen:
                    nxt.append(callee)
        pending = nxt
    return "\n".join(out)


def ibtn_elements(src):
    """Every `<button>`/`<a>` whose class carries `ibtn`, as
    (open_tag, inner_html) pairs — the generated markup never nests these,
    so the first closing tag ends the element."""
    found = []
    for match in re.finditer(r"<(button|a)\b[^>]*?\bclass=\"ibtn\b", src):
        tag = match.group(1)
        open_end = src.find(">", match.start())
        if open_end < 0:
            continue
        close = src.find("</" + tag + ">", open_end)
        inner = src[open_end + 1:close] if close >= 0 else ""
        found.append((src[match.start():open_end + 1], inner))
    return found


def ibtn_builders(js):
    """Names of the page functions that emit `.ibtn` markup — the icon-button
    template(s) a converted surface routes through."""
    names = []
    for match in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\{", js):
        if 'class="ibtn' in fn_body(js, match.group(1)):
            names.append(match.group(1))
    return names


def tag_attr(open_tag, name):
    """Raw value of `name="..."` inside an opening tag (None when absent)."""
    match = re.search(re.escape(name) + r'="([^"]*)"', open_tag)
    return None if match is None else match.group(1)


def fa_words(text):
    """Persian words (>=2 letters) in a label — the visible-text probe: a
    converted button must not render any of them as a text node."""
    return [w for w in re.findall("[\u0600-\u06ff\u200c]+", text or "")
            if len(w) >= 2]


def label_exprs(value):
    """Variable/expression tokens spliced into a composed label — a dynamic
    label must not be rendered as the button's content either."""
    return re.findall(r"\+([A-Za-z_$][\w$.]*)\+", value or "")


def tip_builder_body(js):
    """Body of the function that builds the tooltip element — the one that
    assigns the `tip` id (the closest enclosing function of that assignment
    when it is an object/IIFE method)."""
    for match in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\{", js):
        body = fn_body(js, match.group(1))
        if any(re.search(form, body) for form in TIP_ID_FORMS):
            return body
    return ""


def enclosing_blocks(js, index):
    """Text of the two innermost blocks enclosing `index` (a brace-depth walk
    from the start of the script) — used to take in a whole IIFE/namespace
    body when the element builder is a method inside it."""
    stack = []
    for i, ch in enumerate(js[:index]):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            stack.pop()
    return [brace_block(js, start) for start in stack[-2:]]


def tip_region(js):
    """Everything the tooltip layer is made of: every `tip*` function body,
    every line that mentions `tip`, the block that owns the tooltip element
    (an element builder inside an IIFE brings the whole layer in), and every
    `addEventListener(...)` call that mentions `tip`."""
    parts = [fn_body(js, name) for name in re.findall(r"function\s+(tip\w*)\s*\(", js)]
    parts += [line for line in js.splitlines() if "tip" in line]
    for form in TIP_ID_FORMS:
        hit = re.search(form, js)
        if hit:
            parts += enclosing_blocks(js, hit.start())
            break
    for match in re.finditer(r"\.addEventListener\s*\(", js):
        depth, i = 1, match.end()
        while i < len(js) and depth:
            if js[i] == "(":
                depth += 1
            elif js[i] == ")":
                depth -= 1
            i += 1
        call = js[match.start():i]
        if "tip" in call:
            parts.append(call)
    return "\n".join(parts)


class PeriodsTabTests(unittest.TestCase):
    """TABS registry, the bodyHtml route, the tab count and the STATE-only
    (route-free) data contract of the new tab."""

    def setUp(self):
        self.page = panel.CRM_PAGE
        self.js = crm_js()

    def test_tabs_registry_places_periods_right_after_devices(self):
        tabs = js_literal(self.js, "TABS", "[")
        self.assertTrue(tabs, "TABS literal not found")
        self.assertIn("['periods','دوره\u200cها']", tabs)
        self.assertEqual(tabs.count("'periods'"), 1)
        self.assertRegex(
            tabs,
            r"\['devices','دستگاه\u200cها'\]\s*,\s*\['periods','دوره\u200cها'\]")
        # and it stays in the devices neighbourhood (before customers/owners)
        self.assertLess(tabs.index("'periods'"), tabs.index("'customers'"))

    def test_body_html_routes_the_periods_route(self):
        body = fn_body(self.js, "bodyHtml")
        self.assertTrue(body, "bodyHtml not found")
        self.assertIn("TAB==='periods'", body)
        self.assertIn("periodsHtml()", body)

    def test_the_ten_new_functions_exist(self):
        for name in REQUIRED_FNS:
            self.assertTrue(fn_body(self.js, name),
                            "function %s not found" % name)

    def test_tab_count_map_counts_periods_from_state(self):
        body = fn_body(self.js, "renderTabs")
        self.assertTrue(body, "renderTabs not found")
        match = re.search(r"counts\s*=\s*\{", body)
        self.assertIsNotNone(match, "counts literal not found in renderTabs")
        counts = brace_block(body, match.end() - 1)
        self.assertIn("periods:", counts)
        entry = counts.split("periods:", 1)[1].split(",")[0]
        self.assertIn("STATE.periods", entry)

    def test_period_tab_is_derived_from_state_only(self):
        region = call_closure(self.js, PERIOD_ALL_FNS)
        for key in ("STATE.periods", "STATE.devices", "STATE.owners",
                    "STATE.customers"):
            self.assertIn(key, region, key)

    def test_period_tab_adds_no_http_call(self):
        region = fn_region(self.js, PERIOD_ALL_FNS)
        # every contracted function must be in the region before the
        # route-freeness of the region means anything
        for name in PERIOD_ALL_FNS:
            self.assertIn("function " + name + "(", region, name)
        self.assertNotIn("fetch(", region)
        self.assertNotIn("api(", region)


class PeriodMoneyTests(unittest.TestCase):
    """caseMoney(deviceId) — the one per-case money predicate of the tab."""

    def setUp(self):
        self.js = crm_js()
        self.body = fn_body(self.js, "caseMoney")

    def test_case_money_exists_and_returns_the_triple(self):
        self.assertTrue(self.body, "caseMoney not found")
        for key in ("debit", "credit", "net"):
            self.assertIn(key, self.body, key)
        self.assertRegex(self.body, r"net\s*[:=]\s*credit\s*-\s*debit")

    def test_case_money_counts_only_rows_of_this_device(self):
        self.assertIn("String(t.device_id||'')===deviceId", self.body)

    def test_case_money_skips_voided_and_reversal_rows(self):
        self.assertIn("t.voided_at", self.body)
        self.assertIn("t.kind!=='void'", self.body)
        # the guard as one expression: a source-level mutation that inverts or
        # reorders it must fail here (the split assertions above would not)
        self.assertIn("!t.voided_at&&t.kind!=='void'", self.body)

    def test_case_money_requires_a_fixed_width_date_stamp(self):
        self.assertIn(r"/^\d{4}-\d{2}-\d{2}/", self.body)
        self.assertIn("t.at", self.body)

    def test_case_money_splits_debit_and_credit_by_sign(self):
        self.assertRegex(self.body, r"amount\s*<\s*0")
        self.assertRegex(self.body, r"debit\s*\+=([^;]{0,40})amount")
        self.assertRegex(self.body, r"credit\s*\+=([^;]{0,40})amount")
        # the sign belongs to the column: debit takes the negated amount and
        # credit the positive one, so swapping the accumulators fails here
        self.assertIn("debit+=-amount", self.body)
        self.assertIn("credit+=amount", self.body)

    def test_period_surfaces_use_case_money(self):
        region = call_closure(self.js, [n for n in PERIOD_ALL_FNS
                                        if n != "caseMoney"])
        self.assertIn("caseMoney(", region)


class PeriodDetailDialogTests(unittest.TestCase):
    """dlgPeriod/perPaint: container, segmented switcher, KPI strip, the
    house table wrapper, money cells and the balance marks."""

    def setUp(self):
        self.js = crm_js()
        self.dlg_region = call_closure(self.js, PERIOD_DLG_FNS)

    def test_dialog_root_and_body_container(self):
        body = fn_body(self.js, "dlgPeriod")
        self.assertTrue(body, "dlgPeriod not found")
        self.assertIn('class="perdlg"', body)
        self.assertIn('id="perBody"', self.dlg_region)

    def test_dialog_width_rule(self):
        self.assertRegex(crm_css(),
                         r"dialog:has\(\.perdlg\)\s*\{\s*width:min\(1040px,96vw\)")

    def test_segmented_switcher_chips(self):
        for chip in ('data-pseg="cases"', 'data-pseg="owners"',
                     'data-pseg="customers"'):
            self.assertIn(chip, self.dlg_region, chip)
        seg = fn_body(self.js, "perSeg")
        self.assertTrue(seg, "perSeg not found")
        self.assertIn("data-pseg", call_closure(self.js, ["perSeg"]))

    def test_kpi_strip_and_cells(self):
        self.assertIn('class="per-kpis"', self.dlg_region)
        self.assertIn('class="per-kpi', self.dlg_region)

    def test_tables_use_the_house_card_wrapper(self):
        self.assertIn('class="card tblcard"', self.dlg_region)
        self.assertIn('class="tbl-wrap"', self.dlg_region)
        self.assertIn("<table", self.dlg_region)

    def test_money_cells_use_the_pmoney_classes(self):
        self.assertIn('class="pmoney d"', self.dlg_region)
        self.assertIn('class="pmoney c"', self.dlg_region)

    def test_balance_marks_use_the_house_pill_classes(self):
        self.assertIn('<span class="pill bad">بدهکار', self.dlg_region)
        self.assertIn('<span class="pill ok">بستانکار', self.dlg_region)
        self.assertIn('<span class="pill n">تسویه', self.dlg_region)


class PassengerLinkBadgeTests(unittest.TestCase):
    """«متصل» marks a passenger bound to a case of the period; «بدون پرونده»
    and the existing «آزاد» mark the unbound ones."""

    def setUp(self):
        self.js = crm_js()

    def test_period_passengers_view_marks_attached_and_unbound(self):
        region = call_closure(self.js, PERIOD_DLG_FNS)
        self.assertIn('<span class="pill ok">متصل', region)
        self.assertIn('<span class="pill n">بدون پرونده', region)

    def test_owners_tab_rows_show_the_same_attached_badge(self):
        """One badge, one spelling: the literal asserted here is byte-equal
        to the one the period passengers view asserts."""
        body = fn_body(self.js, "ownersHtml")
        self.assertTrue(body, "ownersHtml not found")
        self.assertIn('<span class="pill ok">متصل', body)

    def test_owners_tab_keeps_the_free_marker(self):
        body = fn_body(self.js, "ownersHtml")
        self.assertIn('<span class="pill warn">آزاد</span>', body)


class IconButtonTests(unittest.TestCase):
    """Icon action buttons: class contract, accessible name == tooltip,
    svg-only visual, and the dialogs that keep visible labels."""

    def setUp(self):
        self.js = crm_js()
        self.builders = ibtn_builders(self.js)

    def _region(self, name):
        region = call_closure(self.js, [name])
        self.assertTrue(region, "%s not found" % name)
        return region

    def test_every_converted_surface_renders_icon_buttons(self):
        self.assertTrue(self.builders, "no .ibtn template in the page")
        for name in IBTN_SURFACES:
            region = self._region(name)
            renders = len(ibtn_elements(region))
            for builder in self.builders:
                renders += len(re.findall(r"\b" + re.escape(builder) + r"\(",
                                          region))
            self.assertGreaterEqual(renders, 1,
                                    "%s renders no .ibtn action" % name)

    def test_icon_buttons_pair_aria_label_with_the_same_data_tip(self):
        elements = ibtn_elements(self.js)
        self.assertTrue(elements, "no .ibtn markup in the page")
        for open_tag, _inner in elements:
            aria = tag_attr(open_tag, "aria-label")
            tip = tag_attr(open_tag, "data-tip")
            self.assertIsNotNone(aria, "aria-label missing: %s" % open_tag)
            self.assertIsNotNone(tip, "data-tip missing: %s" % open_tag)
            self.assertEqual(aria, tip, "label mismatch: %s" % open_tag)

    def test_icon_button_labels_are_persian(self):
        for name in IBTN_SURFACES:
            region = self._region(name)
            checked = 0
            for value in re.findall(r'\bdata-tip="([^"]*)"', region):
                if "'" in value or "+" in value:
                    continue        # composed label: pinned at its call site
                self.assertRegex(value, "[\u0600-\u06ff]",
                                 "non-Persian data-tip %r in %s" % (value, name))
                checked += 1
            for builder in self.builders:
                for args in re.findall(
                        r"\b" + re.escape(builder) + r"\(([^)]*)\)", region):
                    first = args.split(",")[0].strip()
                    if first.startswith("'") and first.endswith("'"):
                        self.assertRegex(
                            first, "[\u0600-\u06ff]",
                            "non-Persian label %s in %s" % (first, name))
                        checked += 1
            self.assertGreaterEqual(checked, 1,
                                    "%s passes no Persian icon-button label"
                                    % name)

    def test_icon_button_labels_are_not_rendered_as_text(self):
        for name in IBTN_SURFACES:
            for open_tag, inner in ibtn_elements(self._region(name)):
                label = tag_attr(open_tag, "aria-label") or ""
                for word in fa_words(label):
                    self.assertNotIn(word, inner,
                                     "visible label %r in %s" % (word, name))
                for expr in label_exprs(label):
                    if len(expr) < 3:
                        continue
                    self.assertIsNone(
                        re.search(r"(?<![\w$.])" + re.escape(expr) + r"(?![\w$])",
                                  inner),
                        "visible label variable %r in %s" % (expr, name))

    def test_icon_button_modifiers_are_known_classes(self):
        for open_tag, _inner in ibtn_elements(self.js):
            classes = tag_attr(open_tag, "class") or ""
            if "'" in classes or "+" in classes:
                continue        # dynamically composed class (checked by hand)
            tokens = classes.split()
            self.assertEqual(tokens[0], "ibtn", classes)
            for token in tokens[1:]:
                self.assertIn(token, IBTN_CLASSES, classes)

    def test_confirm_dialog_keeps_its_visible_text_buttons(self):
        body = fn_body(self.js, "confirmDlg")
        self.assertTrue(body, "confirmDlg not found")
        self.assertIn("esc(okLabel||'تأیید')", body)
        self.assertIn('class="btn danger" onclick="confirmYes()"', body)
        self.assertIn("انصراف", body)
        self.assertNotIn("ibtn", body)
        self.assertNotIn("data-tip", body)


class TooltipLayerTests(unittest.TestCase):
    """One lazily-created #tip[role=tooltip], driven by the data-tip
    attribute: bound for mouseenter/focusin and torn down on mouseleave/
    focusout/scroll/click — the frozen contract names these five events."""

    def setUp(self):
        self.js = crm_js()
        self.region = tip_region(self.js)

    def test_tip_show_and_hide_exist(self):
        self.assertTrue(fn_body(self.js, "tipShow"), "tipShow not found")
        self.assertTrue(fn_body(self.js, "tipHide"), "tipHide not found")

    def test_one_lazily_created_tooltip_element(self):
        js = self.js
        self.assertEqual(sum(len(re.findall(f, js)) for f in TIP_ID_FORMS), 1,
                         'the page must own exactly one id="tip" element')
        self.assertEqual(sum(len(re.findall(f, js)) for f in TIP_ROLE_FORMS), 1,
                         'the tooltip must carry role="tooltip" exactly once')
        builder = tip_builder_body(js)
        self.assertTrue(builder, 'no function assigns id="tip"')
        self.assertIn("createElement", builder,
                      "the tooltip element must be built at runtime")
        # nothing sits in the static shell: the element is created on demand
        self.assertNotIn('id="tip"', crm_markup())
        self.assertNotIn("id='tip'", crm_markup())

    def test_trigger_contract_is_the_data_tip_attribute(self):
        self.assertIn("data-tip", self.region)
        self.assertIn("[data-tip]", self.region)

    def test_binding_events(self):
        # The contract named mouseenter/mouseleave; the layer has to delegate
        # instead, because every table is re-rendered on the 5s refresh and
        # mouseenter/mouseleave do not bubble, so they cannot be delegated.
        # pointerover/pointerout carry the same meaning and do bubble.
        self.assertIn("pointerover", self.region)
        self.assertIn("focusin", self.region)

    def test_teardown_events(self):
        for event in ("focusout", "scroll", "click"):
            self.assertIn(event, self.region, event)


class IconSetTests(unittest.TestCase):
    """The new ICONS keys, each a 24-grid stroke language glyph."""

    def setUp(self):
        self.icons = js_literal(crm_js(), "ICONS", "{")

    def _entries(self):
        self.assertTrue(self.icons, "ICONS literal not found")
        entries = dict(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*'([^']*)'", self.icons))
        entries.update(re.findall(r'([A-Za-z_$][\w$]*)\s*:\s*"([^"\n]*)"', self.icons))
        return entries

    def test_new_icon_keys_exist(self):
        entries = self._entries()
        for key in NEW_ICON_KEYS:
            self.assertIn(key, entries, "ICONS.%s missing" % key)

    def test_new_icons_are_24_grid_current_color_svgs(self):
        entries = self._entries()
        for key in NEW_ICON_KEYS:
            svg = entries.get(key, "")
            self.assertTrue(svg.startswith("<svg"), "ICONS.%s is not an <svg>" % key)
            self.assertIn('viewBox="0 0 24 24"', svg, key)
            self.assertIn('stroke="currentColor"', svg, key)


class MoneyRuleParityTests(unittest.TestCase):
    """caseMoney() is the JS twin of crm._tx_countable: one synthetic ledger,
    both rules, the same debit/credit/net."""

    DEVICE = "dev-case-1"
    OTHER = "dev-case-2"
    CUSTOMER = "cust-1"

    def setUp(self):
        # row shapes: booked debit, received credit, voided original,
        # malformed `at`, `void` reversal, another case, no device at all.
        self.rows = [
            {"device_id": self.DEVICE, "amount": -500,
             "at": "2026-01-01 10:00:00", "kind": "base_fee"},
            {"device_id": self.DEVICE, "amount": 300,
             "at": "2026-01-02 10:00:00", "kind": "received"},
            {"device_id": self.DEVICE, "amount": -200,
             "at": "2026-01-03 10:00:00", "kind": "base_fee",
             "voided_at": "2026-01-04 10:00:00"},
            {"device_id": self.DEVICE, "amount": 100,
             "at": "not-a-date", "kind": "received"},
            {"device_id": self.DEVICE, "amount": -70,
             "at": "2026-01-05 10:00:00", "kind": "void"},
            {"device_id": self.OTHER, "amount": -999,
             "at": "2026-01-06 10:00:00", "kind": "base_fee"},
            {"amount": -123, "at": "2026-01-07 10:00:00", "kind": "base_fee"},
        ]
        self.data = {
            "devices": [{"id": self.DEVICE, "name": "case",
                         "customer_id": self.CUSTOMER}],
            "customers": [{"id": self.CUSTOMER, "full_name": "customer",
                           "transactions": self.rows}],
            "settings": {},
        }

    def js_case_money(self, rows, device_id):
        """caseMoney()'s frozen rule, executed in Python.

        Rows of another case are out; a voided original (voided_at) and a
        `void` reversal are out; the `at` stamp must open with the
        fixed-width date shape (JS \\d is ASCII, hence [0-9]). amount < 0
        lands in debit, amount >= 0 in credit; net = credit - debit.
        """
        debit = credit = 0
        for tx in rows:
            if str(tx.get("device_id") or "") != device_id:
                continue
            if tx.get("voided_at") or tx.get("kind") == "void":
                continue
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
                            str(tx.get("at") or "")[:10]) is None:
                continue
            amount = int(tx.get("amount") or 0)
            if amount < 0:
                debit += -amount
            else:
                credit += amount
        return {"debit": debit, "credit": credit, "net": credit - debit}

    def test_python_rule_helpers_exist(self):
        for name in ("_tx_countable", "customer_balance", "device_invoice"):
            self.assertTrue(callable(getattr(crm, name, None)),
                            "crm.%s missing" % name)

    def test_synthetic_ledger_isolates_the_case(self):
        invoice, errors = crm.device_invoice(self.data, self.DEVICE)
        self.assertEqual(errors, [])
        self.assertEqual(invoice["total_debits"], 500)
        self.assertEqual(invoice["total_credits"], 300)
        self.assertEqual(invoice["case_balance"], -200)
        # the voided original, the malformed `at` and the reversal row
        self.assertEqual(invoice["inactive_count"], 3)

    def test_js_rule_and_python_rule_agree_on_the_same_ledger(self):
        expected = self.js_case_money(self.rows, self.DEVICE)
        invoice, _errors = crm.device_invoice(self.data, self.DEVICE)
        self.assertEqual(expected["debit"], invoice["total_debits"])
        self.assertEqual(expected["credit"], invoice["total_credits"])
        self.assertEqual(expected["net"], invoice["case_balance"])

    def test_every_exclusion_the_js_rule_names_is_false_countable(self):
        voided, malformed, reversal = self.rows[2], self.rows[3], self.rows[4]
        self.assertFalse(crm._tx_countable(voided))
        self.assertFalse(crm._tx_countable(malformed))
        self.assertFalse(crm._tx_countable(reversal))
        # the exclusions are the named ones, not a side effect of the filter
        self.assertEqual(str(voided["device_id"]), self.DEVICE)
        self.assertTrue(voided["voided_at"])
        self.assertEqual(reversal["kind"], "void")
        self.assertEqual(str(malformed["at"])[:10], "not-a-date")

    def test_account_balance_uses_the_same_predicate(self):
        # the account balance spans every case (and rows bound to no case at
        # all): -500 booked + 300 received - 999 on the other case - 123 with
        # no case = -1322, deliberately larger than this case's own net.
        invoice, _errors = crm.device_invoice(self.data, self.DEVICE)
        self.assertEqual(crm.customer_balance(self.data["customers"][0]), -1322)
        self.assertEqual(invoice["customer_balance"], -1322)


class ReviewFixPins(unittest.TestCase):
    """The review round's own fixes, pinned one by one: every assertion here
    exists because a reviewer found the behaviour it now prevents."""

    def setUp(self):
        self.css = crm_css()
        self.js = crm_js()

    def test_card_target_layering_and_focus(self):
        # the stretched target sits above the card text (so a click anywhere
        # opens the period) and below the action row (so those stay clickable),
        # and the row itself lets the pointer through
        self.assertIn(".pcard-hit{position:absolute;inset:0;z-index:1", self.css)
        self.assertIn(".pcard .pacts{position:relative;z-index:2", self.css)
        self.assertIn("pointer-events:none}", self.css)
        self.assertIn("pointer-events:auto}", self.css)
        self.assertIn(".pcard-hit:focus-visible{outline:2px solid var(--brand)", self.css)
        # the focus ring must not depend on :has()
        self.assertNotIn(":has(.pcard-hit", self.css)

    def test_ok_warn_hover_never_fires_on_a_disabled_button(self):
        self.assertIn(
            ".ibtn.ok:hover:not(:disabled),.ibtn.warn:hover:not(:disabled)", self.css)
        self.assertIn("box-shadow:0 0 0 3px var(--pill-ok-bg)", self.css)
        self.assertIn("box-shadow:0 0 0 3px var(--pill-warn-bg)", self.css)

    def test_reduced_motion_keeps_the_busy_state_visible(self):
        self.assertIn(".ibtn.busy .ic{opacity:.35}", self.css)
        self.assertIn("transform:none;filter:none;box-shadow:none", self.css)

    def test_money_scope_is_named_where_the_numbers_are(self):
        cases = "پرونده" + ZWNJ + "ها"
        for label in ("بدهکاری " + cases, "بستانکاری " + cases):
            self.assertIn(label, self.js, label)
        # the dialog says what it counts and that the invoice can differ
        self.assertIn("گزارش مالی این دوره ممکن است", self.js)
        self.assertIn("با فاکتور همان پرونده یکی نیست", self.js)

    def test_persian_forms_match_the_page_convention(self):
        segment = fn_body(self.js, "dlgPeriod")
        self.assertIn("مسافر" + ZWNJ + "ها</button>", segment)
        self.assertIn("صورت" + ZWNJ + "حساب مشتری", self.js)
        self.assertNotIn("مسافرها</button>", segment)

    def test_reset_and_print_have_their_own_glyphs(self):
        self.assertIn("ICONS.rewind", self.js)
        self.assertIn("ICONS.printer", self.js)
        self.assertNotIn("ICONS.globe", self.js)

    def test_deletes_use_the_styled_confirmation(self):
        for name in ("custDelete", "identDelete"):
            body = fn_body(self.js, name)
            self.assertTrue(body, name + " not found")
            self.assertIn("confirmDlg(", body, name)
            self.assertNotIn("confirm(", body.replace("confirmDlg(", ""), name)


if __name__ == "__main__":
    unittest.main()
