"""UI fixes requested 2026-09-08:
1. Launcher page (/launch): first button = CRM ("پنل یکپارچه رجیستری").
2. Home picker (/): CRM card first, site-1 renamed "پنل قدیمی رجیستری" third.
3. CRM devices empty state must still offer the "پروندهٔ جدید" dialog entry.
"""
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

import panel


class EntryPointHarness(unittest.TestCase):
    """Boots the real handler with isolated stores (same shape as CrmApiHarness)."""

    def setUp(self):
        import crm
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH", os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", os.path.join(self.tmp.name, "estelam_store.json")),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    def _page(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
            return response.read().decode("utf-8")


class TestEntryPointNaming(EntryPointHarness):
    def test_launch_page_crm_first(self):
        html = self._page("/launch")
        self.assertIn("پنل یکپارچه رجیستری", html)
        self.assertIn('id="btn-crm"', html)
        self.assertLess(html.index('id="btn-crm"'), html.index('id="btn-estelam"'))
        self.assertLess(html.index('id="btn-estelam"'), html.index('id="btn-open"'))

    def test_home_picker_order_and_names(self):
        html = self._page("/")
        # anchor the full card tag, not the CSS selector occurrences
        m_crm = re.search(r'<a class="site" data-site="crm"', html)
        m_2 = re.search(r'<a class="site" data-site="2"', html)
        m_1 = re.search(r'<a class="site" data-site="1"', html)
        self.assertIsNotNone(m_crm)
        self.assertIsNotNone(m_2)
        self.assertIsNotNone(m_1)
        self.assertLess(m_crm.start(), m_2.start())
        self.assertLess(m_2.start(), m_1.start())
        self.assertIn("پنل یکپارچه رجیستری", html)
        self.assertIn("پنل قدیمی رجیستری", html)
        self.assertNotIn("سایت اول گمرک", html)


class TestCrmEmptyState(unittest.TestCase):
    """Static-source checks: CRM_PAGE is a Python string constant, not a route."""

    def setUp(self):
        self.page = panel.CRM_PAGE

    def test_dialog_entry_exists(self):
        self.assertIn("dlgDevice", self.page)

    def test_empty_state_offers_new_case(self):

        match = re.search(r"if\(!ds\.length\)\s*return\s*([^;]+);", self.page)
        self.assertIsNotNone(match, "devicesHtml empty branch not found")
        branch = match.group(1)
        self.assertIn("پرونده‌ای ثبت نشده", branch)
        self.assertIn("dlgDevice()", branch)
        self.assertIn("+ پروندهٔ جدید", branch)


if __name__ == "__main__":
    unittest.main()
