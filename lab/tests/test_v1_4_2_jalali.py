# -*- coding: utf-8 -*-
"""v1.4.2 (W3) — Jalali (Shamsi) dates in report output.

Stored data stays ISO Gregorian; conversion happens only when report text is
built (txt/html/pdf + the report Telegram message). Reference values below
were computed with the shipped algorithm and cross-checked against known
Nowruz boundaries (equinox times, Tehran).

Layout note: the report builder is crm.build_report — panel.py's
/crm-report and /crm-report-telegram handlers only route to it, and no date
is formatted inside panel.py itself. The jalali import + the to_fa call
therefore live in crm.py; this suite locks that marker plus the panel.py
routing, so the whole pipeline is covered.
"""
import datetime
import re
import unittest

import jalali


class ConversionTests(unittest.TestCase):

    def test_reference_dates(self):
        cases = {
            (2026, 9, 13): (1405, 6, 22),   # «۲۲ شهریور ۱۴۰۵»
            (2026, 3, 20): (1404, 12, 29),  # day before Nowruz 1405
            (2026, 3, 21): (1405, 1, 1),    # Nowruz 1405
            (2025, 9, 13): (1404, 6, 22),
        }
        for gregorian, jalali_expected in cases.items():
            self.assertEqual(jalali.gregorian_to_jalali(*gregorian),
                             jalali_expected)

    def test_jalali_leap_years(self):
        # The leap years in this window are 1403 and 1408 (Esfand 30 exists).
        # The draft brief guessed 2028-03-20 -> 1406/12/30; that is wrong:
        # 1406 is NOT a Jalali leap year. The algorithm — and the astronomical
        # calendar (the 2028 equinox falls before noon Tehran) — put
        # 2028-03-20 at 1407/01/01. Locked as computed and cross-checked.
        self.assertEqual(jalali.gregorian_to_jalali(2025, 3, 20), (1403, 12, 30))
        self.assertEqual(jalali.gregorian_to_jalali(2028, 3, 20), (1407, 1, 1))
        self.assertEqual(jalali.gregorian_to_jalali(2030, 3, 20), (1408, 12, 30))

    def test_months(self):
        self.assertEqual(len(jalali.MONTHS), 12)
        self.assertEqual(jalali.MONTHS[0], "فروردین")
        self.assertEqual(jalali.MONTHS[5], "شهریور")
        self.assertEqual(jalali.MONTHS[11], "اسفند")

    def test_no_skipped_or_repeated_days(self):
        # 2024-2031 spans Nowruz 1403..1409 including both leap years: every
        # consecutive Gregorian day must map to the exact Jalali successor
        # (next day, next month's 1st, or next year's 1 Farvardin).
        day = datetime.date(2024, 1, 1)
        prev = None
        while day < datetime.date(2031, 1, 1):
            current = jalali.gregorian_to_jalali(day.year, day.month, day.day)
            if prev is not None:
                jy, jm, jd = prev
                self.assertIn(current, ((jy, jm, jd + 1), (jy, jm + 1, 1),
                                        (jy + 1, 1, 1)), day.isoformat())
            prev = current
            day += datetime.timedelta(days=1)


class FormattingTests(unittest.TestCase):

    def test_to_fa(self):
        self.assertEqual(jalali.to_fa("2026-09-13"), "۲۲ شهریور ۱۴۰۵")
        self.assertEqual(jalali.to_fa("2026-03-21"), "۱ فروردین ۱۴۰۵")
        # time in the input is ignored
        self.assertEqual(jalali.to_fa("2026-09-13 17:30:00"), "۲۲ شهریور ۱۴۰۵")

    def test_to_fa_datetime(self):
        self.assertEqual(jalali.to_fa_datetime("2026-09-13 17:30:00"),
                         "۲۲ شهریور ۱۴۰۵، ۱۷:۳۰")
        self.assertEqual(jalali.to_fa_datetime("2026-09-13 17:30"),
                         "۲۲ شهریور ۱۴۰۵، ۱۷:۳۰")
        self.assertEqual(jalali.to_fa_datetime("2026-09-13T17:30:00"),
                         "۲۲ شهریور ۱۴۰۵، ۱۷:۳۰")
        # date-only input -> date-only output
        self.assertEqual(jalali.to_fa_datetime("2026-09-13"), "۲۲ شهریور ۱۴۰۵")

    def test_datetime_objects(self):
        self.assertEqual(jalali.to_fa(datetime.date(2026, 9, 13)),
                         "۲۲ شهریور ۱۴۰۵")
        self.assertEqual(
            jalali.to_fa_datetime(datetime.datetime(2026, 9, 13, 17, 30)),
            "۲۲ شهریور ۱۴۰۵، ۱۷:۳۰")

    def test_empty_and_invalid(self):
        for empty in (None, "", "   "):
            self.assertEqual(jalali.to_fa(empty), "—")
            self.assertEqual(jalali.to_fa_datetime(empty), "—")
        # Non-empty garbage comes back unchanged (data is never destroyed).
        # Already-Jalali strings — like the stored birth dates — pass through.
        for bad in ("not a date", "2026-13-01", "2026-02-30", "2026-09-31",
                    "2026-09-13 25:00", "2026-09-13 17:60", "1405/06/22"):
            self.assertEqual(jalali.to_fa(bad), bad)
            self.assertEqual(jalali.to_fa_datetime(bad), bad)


class ReportPipelineTests(unittest.TestCase):
    """The report pipeline (txt/html/pdf + telegram) must render Jalali."""

    @classmethod
    def setUpClass(cls):
        import crm
        import panel
        cls.crm = crm
        cls.crm_src = open(crm.__file__, encoding="utf-8").read()
        cls.panel_src = open(panel.__file__, encoding="utf-8").read()

    def test_build_report_header_is_jalali(self):
        state = {"devices": [], "customers": [], "owners": []}
        for fmt in ("txt", "html"):
            text = self.crm.build_report(state, fmt=fmt)
            self.assertTrue(any(m in text for m in jalali.MONTHS), fmt)
            # no bare Gregorian ISO date leaks into report output
            self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", text), fmt)

    def test_report_builder_uses_jalali_module(self):
        # crm.py owns build_report (the txt/html/pdf/telegram report text);
        # its header timestamp must go through jalali.to_fa_datetime.
        self.assertIn("import jalali", self.crm_src)
        self.assertIn("jalali.to_fa_datetime(", self.crm_src)

    def test_panel_report_routes_reach_build_report(self):
        # panel.py only routes: /crm-report and /crm-report-telegram call
        # crm.build_report — no date is formatted in panel.py itself.
        self.assertIn('"/crm-report"', self.panel_src)
        self.assertIn("self._crm_report(query)", self.panel_src)
        self.assertIn("self._crm_report_telegram(", self.panel_src)
        self.assertIn("build_report", self.panel_src)


if __name__ == "__main__":
    unittest.main()
