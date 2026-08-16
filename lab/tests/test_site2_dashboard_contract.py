import re
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parents[2]
SOURCE = (HERE / "dashboard-new.html").read_text(encoding="utf-8")


class Site2DashboardContractTests(unittest.TestCase):
    def test_required_dom_contracts_remain_present(self):
        required_ids = {
            "btn-start-manual", "manual-msg", "stats", "s-total", "s-running",
            "s-ready", "s-success", "s-blocked", "profile-form", "f-title",
            "f-national", "f-birth", "f-mobile", "f-imei1", "f-imei2",
            "f-manual", "profile-search", "filters", "profile-list", "log-list",
            "live", "flash",
        }
        ids = set(re.findall(r'id="([^"]+)"', SOURCE))
        self.assertTrue(required_ids <= ids, required_ids - ids)
        self.assertEqual(len(ids), len(re.findall(r'id="([^"]+)"', SOURCE)))

    def test_existing_action_and_refresh_routes_remain_wired(self):
        for route in (
            "/state", "/save", "/start2", "/stop", "/toggle", "/clone",
            "/delete", "/start2-manual", "/site2-notif", "/logs",
        ):
            self.assertIn(route, SOURCE)
        self.assertIn("setInterval(refreshList,5000)", SOURCE)
        self.assertIn("checkNotif()", SOURCE)
        self.assertIn("loadLogs()", SOURCE)

    def test_status_search_filter_and_safe_rendering_contracts_remain(self):
        for marker in (
            "function statusOf", "function render", "const esc",
            "activeFilter", "data-filter", "profile-search",
            "STORE.profiles", "reverse()",
        ):
            self.assertIn(marker, SOURCE)
        self.assertIn("esc(p.title", SOURCE)
        self.assertIn("esc(error.message)", SOURCE)
        self.assertIn("esc(l.message", SOURCE)

    def test_responsive_and_accessibility_markers_exist(self):
        self.assertIn("@media(max-width:1000px)", SOURCE)
        self.assertIn("@media(max-width:700px)", SOURCE)
        self.assertIn('aria-live="polite"', SOURCE)
        self.assertIn('aria-label="جستجوی پروفایل"', SOURCE)
        self.assertIn(":focus-visible", SOURCE)


if __name__ == "__main__":
    unittest.main()
