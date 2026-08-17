import unittest

import panel


class ProfileReadOnlyViewTests(unittest.TestCase):
    def test_top_level_inquiries_tab_is_removed(self):
        html = panel.render_page().decode("utf-8")
        top_tabs = html[html.index('id="main-tabs"'):html.index('id="view-form"')]
        self.assertNotIn("tab-inquiries", top_tabs)
        self.assertNotIn("showView('inquiries')", top_tabs)
        self.assertNotIn('id="view-inquiries"', html)
        self.assertIn("پروفایل انتخاب‌شده", top_tabs)

    def test_readonly_profile_view_is_distinct_from_edit_form(self):
        source = panel.PAGE_JS
        start = source.index("function buildProfileOverview")
        end = source.index("function buildForm", start)
        overview = source[start:end]
        self.assertIn("readonly-grid", overview)
        self.assertIn("readonly-field", overview)
        self.assertIn("ویرایش اطلاعات", overview)
        self.assertNotIn("<input", overview)
        self.assertNotIn("onsubmit", overview)
        self.assertIn("function viewProfile", overview)

    def test_inner_tabs_and_inquiries_remain_in_readonly_view(self):
        source = panel.PAGE_JS
        start = source.index("function buildProfileOverview")
        end = source.index("function buildForm", start)
        overview = source[start:end]
        for marker in ("اطلاعات پایه", "اطلاعات هویتی", "استعلامات",
                       "view-base", "view-identity", "view-profile-inquiries",
                       "buildDeviceInquiryPanel(p)", "buildExitbanResult(p)"):
            self.assertIn(marker, overview)

    def test_initial_and_activate_paths_show_readonly_profile(self):
        source = panel.PAGE_JS
        self.assertIn("if(getProf(activeId)) viewProfile(activeId)", source)
        self.assertIn("if(p) viewProfile(activeId)", source)
        self.assertIn("onclick=\"viewProfile(activeId)\"", panel.PAGE_HEAD)


if __name__ == "__main__":
    unittest.main()
