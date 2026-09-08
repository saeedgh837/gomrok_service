"""Design-quality gates from the ui-ux-pro-max + frontend-design checklists,
applied to the redesigned CRM page (v1.1.1 polish round)."""
import unittest

import panel


class CrmDesignGates(unittest.TestCase):
    def setUp(self):
        self.page = panel.CRM_PAGE

    def test_font_is_bundled_not_remote(self):
        """Offline rule: @font-face points at the panel's own /fonts route."""
        self.assertIn("@font-face", self.page)
        self.assertIn("url('/fonts/Vazirmatn-var.woff2')", self.page)
        self.assertNotIn("fonts.googleapis.com", self.page)

    def test_no_emoji_in_metadata_chips(self):
        """ux-guidelines: icons/text instead of emoji glyphs."""
        for emoji in ("\U0001F464", "\U0001FAAA"):  # 👤 🪪
            self.assertNotIn(emoji, self.page)
        self.assertIn("var ICONS", self.page)
        self.assertIn('<svg class="ic"', self.page)

    def test_stage_rail_is_numbered(self):
        """Signature element: 9 numbered stage nodes via CSS counters."""
        self.assertIn("counter-reset:stage", self.page)
        self.assertIn("counter-increment:stage", self.page)
        self.assertIn("content:counter(stage)", self.page)

    def test_kpi_is_ledger_strip_not_clone_cards(self):
        self.assertIn("border-inline-start:1px solid var(--line)", self.page)
        self.assertIn(".kpi:first-child{border-inline-start:none}", self.page)

    def test_form_errors_mark_fields_aria_invalid(self):
        """Forms/Error Placement: specific error below the input + aria."""
        self.assertIn("aria-invalid", self.page)
        self.assertIn("showFieldErrors", self.page)

    def test_focus_visible_and_reduced_motion(self):
        self.assertIn(":focus-visible{outline:none;box-shadow:var(--ring)}", self.page)
        self.assertIn("prefers-reduced-motion:reduce", self.page)

    def test_numeric_inputs_declare_inputmode(self):
        self.assertIn('inputmode="numeric"', self.page)
        self.assertIn('inputmode="tel"', self.page)


class CrmFontRoute(unittest.TestCase):
    def test_font_route_registered(self):
        src = open(panel.__file__, encoding="utf-8").read()
        self.assertIn('"/fonts/Vazirmatn-var.woff2"', src)
        self.assertIn('"/fonts/Vazirmatn-Bold.woff2"', src)


if __name__ == "__main__":
    unittest.main()
