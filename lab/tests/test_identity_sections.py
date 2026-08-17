import unittest

import panel


class IdentitySectionsTests(unittest.TestCase):
    def test_profile_has_identity_fields(self):
        profile = panel._clean_profile({
            "title": "A", "national": "0012345678", "birth": "1370/1/1",
            "first_name": "علی", "last_name": "رضایی", "father_name": "حسن",
            "birth_certificate_no": "1234",
        })
        self.assertEqual(profile["first_name"], "علی")
        self.assertEqual(profile["last_name"], "رضایی")
        self.assertEqual(profile["father_name"], "حسن")
        self.assertEqual(profile["birth_certificate_no"], "1234")
        self.assertEqual(profile["national"], "0012345678")
        self.assertEqual(profile["birth"], "1370/1/1")

    def test_panel_has_three_profile_tabs_and_separate_views(self):
        html = panel.render_page().decode("utf-8")
        for value in (
            "اطلاعات پایه", "اطلاعات هویتی", "استعلامات",
            "view-base", "view-identity", "view-profile-inquiries",
            "showProfileSection", "اطلاعات پایه", "اطلاعات هویتی", "استعلامات",
        ):
            self.assertIn(value, html)
    def test_identity_view_wraps_only_identity_fields(self):
        source = panel.PAGE_JS
        form_start = source.index("function buildForm")
        identity_start = source.index("'<div id=\"view-identity\"", form_start)
        identity_end = source.index("'<div id=\"view-profile-inquiries\"", identity_start)
        identity_view = source[identity_start:identity_end]
        self.assertIn("اطلاعات هویتی", identity_view)
        self.assertIn("name=\"first_name\"", identity_view)
        self.assertNotIn("class=\"switch\"", identity_view)
        base_start = source.index("'<div id=\"view-base\"", form_start)
        self.assertNotIn("name=\"first_name\"", source[base_start:identity_start])
        self.assertNotIn("name=\"first_name\"", source[identity_end:source.index("'<button type=\"submit\"", identity_end)])

    def test_readonly_identity_view_has_no_editable_inputs(self):
        source = panel.PAGE_JS
        overview_start = source.index("function buildProfileOverview")
        form_start = source.index("function buildForm", overview_start)
        overview = source[overview_start:form_start]
        identity_start = overview.index("'<div id=\"view-identity\"")
        identity_end = overview.index("'<div id=\"view-profile-inquiries\"", identity_start)
        identity_view = overview[identity_start:identity_end]
        self.assertIn("readonly-grid", identity_view)
        self.assertIn("p.first_name", identity_view)
        self.assertNotIn("<input", identity_view)

    def test_exitban_inquiry_sends_currently_selected_profile_id(self):
        source = panel.PAGE_JS
        self.assertIn("runExitbanInquiry(activeId)", source)
        self.assertIn("body:JSON.stringify({id:id})", source)
        self.assertIn("/exitban-inquiry", source)
        self.assertNotIn("استعلام ممنوع‌الخروجی</h3><span class=\"soon\">", source)


if __name__ == "__main__":
    unittest.main()
