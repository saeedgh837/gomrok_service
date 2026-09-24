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


class DialogArgumentGates(unittest.TestCase):
    """openDlg(html, silent): `silent` is the SECOND argument.

    A call of the shape `openDlg('<div>', silent +'<h3>...' + ...)` parses
    fine and silently renders an almost-empty modal: `html` receives only
    the opening tag, and the entire dialog body is concatenated onto the
    boolean `silent` and discarded. v1.9.1 shipped for a long time with
    both big dialogs (caseDetail, dlgPeriod) opening as a 54px box holding
    nothing but the close button, because nothing structural rejected this.
    """

    src = open(panel.__file__, encoding="utf-8").read()

    def _balanced_call(self, start):
        """Return the text from `openDlg(` through its matching close paren."""
        depth, i = 0, start
        while i < len(self.src):
            c = self.src[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return self.src[start:i + 1]
            i += 1
        raise AssertionError("unbalanced openDlg( at offset %d" % start)

    def test_silent_is_not_a_term_of_the_html_concatenation(self):
        import re
        bad = []
        for m in re.finditer(r"openDlg\(", self.src):
            call = self._balanced_call(m.start())
            # The html argument is the string up to the first top-level comma.
            depth, comma = 0, None
            for i, c in enumerate(call):
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                elif c == "," and depth == 1:
                    comma = i
                    break
            if comma is None:
                continue
            first = call[:comma]
            # A bare `, silent` (whitespace/newline only between comma and name)
            # is correct. `'<div>', silent +'<h3>'` is the bug: the + makes the
            # rest of the body part of the SECOND argument.
            rest = call[comma + 1:]
            if re.match(r"\s*silent\s*=[^,)]", rest) or re.match(r"\s*silent\s*\+", rest):
                bad.append(first[-60:])
        self.assertEqual(bad, [], "openDlg calls with `silent` inside the "
                         "html concatenation render an empty modal: %r" % bad)

    def test_both_main_dialogs_pass_their_body_to_html(self):
        for name in ("caseDetail", "dlgPeriod"):
            i = self.src.find("function %s(" % name)
            self.assertGreater(i, 0, "%s not found" % name)
            call_start = self.src.find("openDlg(", i)
            call = self._balanced_call(call_start)
            self.assertRegex(call, r",\s*silent\)$",
                             "%s must close its openDlg call with `, silent)`"
                             % name)


class PanelOutputEncoding(unittest.TestCase):
    """Startup diagnostics must not crash on Windows cp1252 streams."""

    def test_persian_diagnostic_survives_a_cp1252_stream(self):
        import io

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        self.addCleanup(stream.close)
        panel._configure_output_stream(stream)
        stream.write("پنل اجرا شد\n")
        stream.flush()
        self.assertIn("پنل اجرا شد", raw.getvalue().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
