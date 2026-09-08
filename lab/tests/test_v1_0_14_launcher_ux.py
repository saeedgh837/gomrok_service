"""v1.0.14: launcher UX — /launch page, favicon, panel_tray.pyw, iss wiring."""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PANEL = os.path.join(ROOT, "panel.py")
TRAY = os.path.join(ROOT, "panel_tray.pyw")
ISS = os.path.join(ROOT, "lab", "scripts", "irica.iss")
RELEASE = os.path.join(ROOT, "lab", "scripts", "build_windows_release.py")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class LaunchPageTests(unittest.TestCase):
    """The /launch page exists and serves the operator-facing card."""

    def setUp(self):
        self.src = read(PANEL)

    def test_panel_has_launch_route(self):
        self.assertIn('route == "/launch"', self.src)
        self.assertIn("def render_launch_page", self.src)

    def test_panel_has_favicon_route(self):
        self.assertIn('route == "/favicon.ico"', self.src)

    def test_launch_page_core_elements(self):
        page = self.src[self.src.index("LAUNCH_PAGE"):]
        for marker in (
            "سرویس رجیستری",
            "پنل یکپارچه رجیستری",
            "پنل استعلام",
            "بستن سرویس",
            "/site-1", "/site-2", "/shutdown", "/state",
            'dir="rtl"',
        ):
            self.assertIn(marker, page)

    def test_panel_syntax_ok(self):
        ast.parse(self.src)


class TrayLauncherTests(unittest.TestCase):
    """panel_tray.pyw: probe -> spawn -> toast -> open /launch chain."""

    def setUp(self):
        self.src = read(TRAY)
        ast.parse(self.src)

    def test_file_exists_and_syntax_ok(self):
        self.assertIn("def main()", self.src)

    def test_probe_uses_project_markup(self):
        # A non-panel service on the port must not be treated as "up".
        self.assertIn("site-1", self.src)
        self.assertIn("irica-version", self.src)

    def test_spawn_is_detached_pythonw(self):
        self.assertIn("panel_gui.pyw", self.src)
        self.assertIn("DETACHED_PROCESS", self.src)
        self.assertIn("pythonw", self.src)

    def test_bounded_start_poll(self):
        self.assertIn("START_TIMEOUT", self.src)
        self.assertIn("_alert", self.src)

    def test_toast_is_best_effort(self):
        self.assertIn("def _toast", self.src)
        # the except path must swallow, not raise
        self.assertIn("toast skipped", self.src)

    def test_launch_fallback_cross_version(self):
        # /launch first, then /site-1 fallback for panels older than 1.0.14
        self.assertIn('"/launch"', self.src)
        self.assertIn('"/site-1"', self.src)
        self.assertLess(self.src.index('"/launch"'), self.src.index('"/site-1"'))

    def test_log_written_on_crash(self):
        self.assertIn("panel_tray.log", self.src)


class InstallerWiringTests(unittest.TestCase):
    """Shortcuts and bundle must point at the new launcher."""

    def test_iss_shortcuts_use_tray(self):
        iss = read(ISS)
        icons = iss[iss.index("[Icons]"):iss.index("[Registry]")]
        self.assertIn("panel_tray.pyw", icons)
        # shortcuts must NOT still point at panel_gui directly
        self.assertNotIn("panel_gui.pyw", icons)

    def test_release_ships_tray(self):
        rel = read(RELEASE)
        self.assertIn('"panel_tray.pyw"', rel)
        self.assertIn('"panel_gui.pyw"', rel)


if __name__ == "__main__":
    unittest.main()
