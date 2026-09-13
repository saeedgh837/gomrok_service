import unittest

import panel


class DashboardRewriteTests(unittest.TestCase):
    def test_dashboard_contains_required_sections_and_actions(self):
        html = panel.render_page().decode("utf-8")
        for text in (
            "لاگ پروفایل", "لاگ کلی", "تنظیمات کلی", "استعلامات",
                "استعلام ممنوع‌الخروجی", "استعلام اقساط و وام پرداخت‌نشده",
                "شروع به کار", "/start", "/stop", "لاگ پروفایل", "لاگ کلی",
        ):
            self.assertIn(text, html)

    def test_profile_editor_starts_empty_and_omits_customer_telegram_chat_id(self):
        source = panel.PAGE_JS
        html = panel.render_page().decode("utf-8")
        self.assertNotIn('name="telegram_chat_id"', source)
        self.assertNotIn("f.telegram_chat_id", source)
        self.assertIn("برای ویرایش، یک پروفایل را از منوی سمت راست انتخاب کنید", source)
        self.assertIn("profile-editor-empty", html)
        self.assertNotIn("document.getElementById('view-form').innerHTML=buildForm(initProf)", source)

    def test_logs_refresh_every_ten_seconds(self):
        source = panel.PAGE_JS
        html = panel.render_page().decode("utf-8")
        self.assertIn("function refreshLogs()", source)
        self.assertIn("fetch('/logs'", source)
        self.assertIn("setInterval(refreshLogs,10000)", source)
        self.assertIn("/logs", panel.Handler.do_GET.__code__.co_consts)
        self.assertIn("refreshLogs", html)

    def test_profile_list_is_compact_numbered_and_newest_first(self):
        source = panel.PAGE_JS
        self.assertIn("STORE.profiles.slice().reverse()", source)
        self.assertIn("profile-number", source)
        self.assertIn("STORE.profiles.indexOf(p)+1", source)
        html = panel.render_page().decode("utf-8")
        self.assertLess(html.index("+ پروفایل جدید"), html.index('id="prof-list"'))
        self.assertIn(".prof-item {", html)
        self.assertIn(".mini-btn.del {", html)
        self.assertIn("profile-run-primary", html)
        self.assertIn("profileRunButton(p,'profile-run-primary')", source)

    def test_dark_theme_header_active_banner_and_artifact_icons_exist(self):
        # site-1 redesign: emoji glyphs were replaced by the IRICA inline SVG
        # icon language, so the markers follow the icon/text pairs instead.
        html = panel.render_page().decode("utf-8")
        for marker in (
            'data-theme="dark"', "toggleTheme", "localStorage.getItem('irica-theme')",
            "panel-title", "active-profile-banner", 'id="theme-toggle"', 'class="ic"',
            "artifact-links", "رسید</a>", "پرداخت</a>", "لینک پرداخت</a>",
            "استعلام آنلاین خروج از کشور",
        ):
            self.assertIn(marker, html)

    def test_profile_search_is_below_new_profile_and_matches_all_identity_fields(self):
        source = panel.PAGE_JS
        html = panel.render_page().decode("utf-8")
        self.assertIn('id="profile-search"', html)
        self.assertLess(html.index("+ پروفایل جدید"), html.index('id="profile-search"'))
        self.assertIn("filterProfiles", source)
        self.assertIn("p.title", source)
        self.assertIn("p.national", source)
        self.assertIn("p.mobile", source)
        self.assertIn("p.imeis", source)


class JalaliDateDisplayTests(unittest.TestCase):
    """v1.4.2 — site-1 timestamps render as Jalali dates (render-time only).

    Storage stays ISO Gregorian; every visible timestamp goes through the
    faDateTime helper defined once in PAGE_JS.
    """

    def test_helpers_are_defined_once(self):
        source = panel.PAGE_JS
        self.assertIn("function faDate(iso){", source)
        self.assertIn("function faDateTime(iso){", source)
        self.assertIn("toLocaleDateString('fa-IR-u-ca-persian'", source)
        self.assertEqual(source.count("function faDateTime(iso)"), 1)

    def test_every_site1_timestamp_routes_through_faDateTime(self):
        source = panel.PAGE_JS
        for marker in (
            "escHtml(faDateTime(log.ts))",               # renderLogs
            "escHtml(faDateTime(l.ts))",                 # log notifications
            "escHtml(faDateTime(p.exitban_checked_at))",  # exitban result card
            "escHtml(faDateTime(row.checked_at))",        # device inquiry results
            "faDateTime(h.ts)",                           # telegram api-health meta
            "escHtml(faDateTime(item.modified))",         # system log files
        ):
            self.assertIn(marker, source)

    def test_no_raw_timestamp_passthrough_remains(self):
        source = panel.PAGE_JS
        for marker in ("escHtml(log.ts||'')", "escHtml(l.ts||'')",
                       "escHtml(p.exitban_checked_at)", "escHtml(row.checked_at)",
                       "escHtml(item.modified)"):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
