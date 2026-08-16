import unittest

import panel


class SiteSelectorTests(unittest.TestCase):
    def test_home_has_two_site_choices(self):
        html = panel.render_home_page().decode("utf-8")
        self.assertIn('data-site="1"', html)
        self.assertIn('data-site="2"', html)
        self.assertIn('href="/site-1"', html)
        self.assertIn('href="/site-2"', html)

    def test_site_two_is_full_profile_dashboard(self):
        html = panel.render_site_two_page().decode("utf-8")
        for marker in (
            'id="profile-form"',
            'id="profile-list"',
            'id="profile-search"',
            'name="manual_manage"',
            "async function createProfile",
            "async function startProfile",
            "async function stopProfile",
        ):
            self.assertIn(marker, html)
        self.assertNotIn("if(res.ok)", html)

    def test_routes_keep_current_panel_separate(self):
        with open(panel.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertIn('route == "/site-1"', source)
        self.assertIn('route == "/site-2"', source)
        self.assertIn('route in ("/", "/index.html")', source)


if __name__ == "__main__":
    unittest.main()
