# -*- coding: utf-8 -*-
"""v1.4.2 — Jalali (Persian calendar) display dates across the CRM page.

Stored timestamps stay ISO Gregorian ("2026-09-13 17:30:20"); conversion to
«روز ماه سال» happens only at render time through two page helpers:

- faDate(iso)      plain dates (created, done_at, closed_at)
- faDateTime(iso)  dates where the clock matters (updated, last_inquiry_at,
                   ledger/tx timestamps, history and periodic log lines)

Empty or unparsable values render as «—». The devices case table loses its
«آخرین تغییر» column (the caseDetail modal keeps the timestamp row).
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


class HelperContractTests(unittest.TestCase):
    """The two helpers exist with the exact rendering contract."""

    def setUp(self):
        self.js = crm_js()

    def test_helpers_defined(self):
        self.assertIn("function faDate(iso){", self.js)
        self.assertIn("function faDateTime(iso){", self.js)

    def test_persian_calendar_locale_and_fallback(self):
        for name in ("faDate", "faDateTime"):
            body = fn_body(self.js, name)
            self.assertTrue(body, name + " not found")
            # Persian calendar, long month, Persian digits come from the
            # fa-IR-u-ca-persian locale; ISO space becomes T for Date()
            self.assertIn(".replace(' ','T')", body)
            self.assertIn(
                "toLocaleDateString('fa-IR-u-ca-persian',"
                "{day:'numeric',month:'long',year:'numeric'})", body)
            # empty or unparsable input renders as the em dash marker
            self.assertIn("String(iso||'—')", body)
        # faDateTime appends the clock with the Persian comma separator
        self.assertIn(
            "+'، '+d.toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'})",
            fn_body(self.js, "faDateTime"))


class HelperUsageTests(unittest.TestCase):
    """Every CRM surface that shows a date goes through the helpers."""

    def setUp(self):
        self.js = crm_js()

    def test_devices_table_created_column(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertTrue(body, "devicesHtml not found")
        self.assertIn("faDate(d.created)", body)

    def test_case_detail_modal_dates(self):
        body = fn_body(self.js, "caseDetail")
        self.assertTrue(body, "caseDetail not found")
        self.assertIn("faDate(d.created)", body)
        self.assertIn("faDateTime(d.updated)", body)
        self.assertIn("faDate(d.done_at)", body)
        self.assertIn("faDateTime(d.last_inquiry_at)", body)

    def test_tab_rows_use_helpers(self):
        for fn, markers in (
            ("devRow", ("faDate(d.created)", "faDateTime(d.updated)")),
            ("doneTabHtml", ("faDate(d.done_at)",)),
            ("closedTabHtml", ("faDate(d.closed_at)", "faDate(d.created)",
                               "faDateTime(d.updated)")),
        ):
            body = fn_body(self.js, fn)
            self.assertTrue(body, fn + " not found")
            for marker in markers:
                self.assertIn(marker, body, fn + ": " + marker)

    def test_history_and_logs(self):
        for fn in ("historyHtml", "renderLogs"):
            body = fn_body(self.js, fn)
            self.assertTrue(body, fn + " not found")
            self.assertIn("faDateTime(l.at)", body, fn)

    def test_ledger_and_tx_detail(self):
        ledger = fn_body(self.js, "ledgerHtml")
        self.assertTrue(ledger, "ledgerHtml not found")
        self.assertIn("faDateTime(t.at)", ledger)
        tx = fn_body(self.js, "txDetailModal")
        self.assertTrue(tx, "txDetailModal not found")
        self.assertIn("faDateTime(t.at)", tx)

    def test_periodic_surfaces(self):
        periodic = fn_body(self.js, "periodicHtml")
        self.assertTrue(periodic, "periodicHtml not found")
        self.assertIn("faDateTime(d.last_inquiry_at)", periodic)
        self.assertIn("faDateTime(l.ts)", periodic)
        history = fn_body(self.js, "periodicHistory")
        self.assertTrue(history, "periodicHistory not found")
        self.assertIn("faDateTime(l.ts)", history)
        self.assertIn("faDateTime(d.last_inquiry_at)", history)

    def test_no_raw_iso_date_rendering_left(self):
        """The migration is complete: no esc() directly on a date field."""
        for marker in ("esc(d.created", "esc(d.updated", "esc(d.done_at",
                       "esc(d.closed_at", "esc(d.last_inquiry_at",
                       "esc(l.at)", "esc(t.at)", "esc(l.ts)"):
            self.assertNotIn(marker, self.js, marker + " still rendered raw")


class DevicesColumnRemovalTests(unittest.TestCase):
    """«آخرین تغییر» left the case table; the modal keeps it (v1.4.2)."""

    def setUp(self):
        self.src = panel.CRM_PAGE
        self.js = crm_js()

    def test_thead_has_no_last_change_column(self):
        self.assertNotIn("<th>آخرین تغییر</th>", self.js)

    def test_devices_rows_have_no_updated_cell(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertTrue(body, "devicesHtml not found")
        self.assertNotIn("d.updated", body)

    def test_modal_keeps_last_change_row(self):
        body = fn_body(self.js, "caseDetail")
        self.assertIn("آخرین تغییر: '+esc(faDateTime(d.updated))", body)


if __name__ == "__main__":
    unittest.main()
