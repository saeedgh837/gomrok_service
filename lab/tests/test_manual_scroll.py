"""Manual review must be possible in the robot's browser window.

The v1.0.4 field report: on the gomrok login page part of the form sits outside
the window and nothing can be scrolled into view.

Measured cause (lab/scripts/diagnose_mobile_scroll.py):

    window 430x932 requested -> Chrome gives 516x932, of which 151 px is window
    chrome, so only 781 CSS px of page are ever visible.
    deviceMetrics.height = 932 forces a layout viewport 932 CSS px tall.
    => the bottom 151 CSS px are permanently outside the window, AND a page
       shorter than 932 CSS px has scrollHeight == clientHeight, so there is no
       scroll range at all -- wheel, keyboard and drag have nothing to move.

Fix, in two parts:

  1. `deviceMetrics.height = 0` -- CDP treats 0 as "do not override this axis",
     so the layout viewport equals the real window content area. Nothing is
     clipped, the scroll range is correct, and it follows a window resize.
     (Proven: lab/scripts/probe_devicemetrics_auto.py)
  2. `Emulation.setEmitTouchEventsForMouse(mobile)` -- mouse drag scrolls the
     page like a finger on a phone. Mobile emulation uses overlay scrollbars
     that occupy 0 px and cannot be grabbed, and no CSS or Chrome flag brings a
     classic one back (all four attempts measured 0 px in
     lab/scripts/probe_focus_and_scrollbar.py), so drag is the replacement.
     (Proven 3/3, and survives navigation: lab/scripts/probe_dragscroll_safety.py)

Invariants this must NOT break, all asserted here:
  * layout viewport width stays 430 CSS px, pixelRatio 3.0, mobile UA -> the site
    keeps serving its mobile page;
  * `--window-size` still receives a REAL height (a 0 there would be a broken
    window);
  * every robot click path is unaffected (verified live in
    lab/scripts/probe_click_with_dragscroll.py).

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_manual_scroll
"""
import unittest
from unittest.mock import MagicMock, patch

import captcha_collector as cc
import estelam_hamta as eh


class ViewportMetricsTests(unittest.TestCase):
    """The emulated viewport must track the window, not a fixed phone height."""

    def test_collector_metrics_use_auto_height(self):
        self.assertEqual(cc.MOBILE_METRICS["height"], 0,
                         "height must be 0 (auto) so the viewport tracks the window")
        self.assertEqual(cc.MOBILE_METRICS["width"], 430)
        self.assertEqual(cc.MOBILE_METRICS["pixelRatio"], 3.0)
        self.assertTrue(cc.MOBILE_METRICS["mobile"])

    def test_hamta_metrics_use_auto_height(self):
        self.assertEqual(eh.MOBILE_METRICS["height"], 0)
        self.assertEqual(eh.MOBILE_METRICS["width"], 430)
        self.assertEqual(eh.MOBILE_METRICS["pixelRatio"], 3.0)
        self.assertTrue(eh.MOBILE_METRICS["mobile"])

    def test_window_size_never_inherits_the_auto_height(self):
        """`--window-size=430,0` would create an unusable window."""
        for module in (cc, eh):
            with self.subTest(module=module.__name__):
                self.assertGreater(module.WINDOW_SIZE[0], 0)
                self.assertGreater(module.WINDOW_SIZE[1], 400)
                self.assertEqual(module.WINDOW_SIZE[0], module.MOBILE_METRICS["width"])

    def test_window_size_flag_carries_real_numbers(self):
        for module in (cc, eh):
            with self.subTest(module=module.__name__):
                flag = module.window_size_arg()
                self.assertTrue(flag.startswith("--window-size="), flag)
                w, h = (int(v) for v in flag.split("=", 1)[1].split(","))
                self.assertEqual((w, h), tuple(module.WINDOW_SIZE))
                self.assertGreater(h, 0)


class ManualScrollInstallTests(unittest.TestCase):
    """Drag-to-scroll must be installed on every robot driver, and never raise."""

    def test_install_enables_touch_emulation_for_mouse(self):
        driver = MagicMock()
        self.assertIs(cc.install_manual_scroll(driver), driver)
        driver.execute_cdp_cmd.assert_any_call(
            "Emulation.setEmitTouchEventsForMouse",
            {"enabled": True, "configuration": "mobile"})

    def test_install_is_best_effort(self):
        driver = MagicMock()
        driver.execute_cdp_cmd.side_effect = RuntimeError("no cdp here")
        self.assertIs(cc.install_manual_scroll(driver), driver)

    def test_collector_build_driver_installs_manual_scroll(self):
        driver = MagicMock()
        with patch.object(cc, "build_chrome", return_value=driver), \
                patch.object(cc, "install_site_zoom", side_effect=lambda d: d), \
                patch.object(cc, "install_manual_scroll",
                             side_effect=lambda d: d) as install:
            self.assertIs(cc.build_driver("ua"), driver)
        install.assert_called_once_with(driver)

    def test_hamta_build_driver_installs_manual_scroll(self):
        driver = MagicMock()
        with patch.object(eh.webdriver, "Chrome", return_value=driver), \
                patch.object(eh, "install_manual_scroll",
                             side_effect=lambda d: d) as install:
            self.assertIs(eh.build_driver(), driver)
        install.assert_called_once_with(driver)


class DriverOptionsTests(unittest.TestCase):
    """The capability handed to chromedriver must carry the auto height."""

    def _capability(self, build, **kwargs):
        captured = {}

        def fake_chrome(*args, **kw):
            options = kw.get("options") or args[-1]
            captured.update(options.experimental_options)
            captured["arguments"] = list(options.arguments)
            return MagicMock()

        with patch.object(cc.webdriver, "Chrome", side_effect=fake_chrome), \
                patch.object(eh.webdriver, "Chrome", side_effect=fake_chrome):
            build(**kwargs)
        return captured

    def test_collector_chrome_capability(self):
        cap = self._capability(cc.build_chrome, user_agent="test-ua")
        metrics = cap["mobileEmulation"]["deviceMetrics"]
        self.assertEqual(metrics["height"], 0)
        self.assertEqual(metrics["width"], 430)
        self.assertEqual(cap["mobileEmulation"]["userAgent"], "test-ua")
        self.assertIn(cc.window_size_arg(), cap["arguments"])
        self.assertNotIn("--window-size=430,0", cap["arguments"])

    def test_hamta_chrome_capability(self):
        cap = self._capability(eh.build_driver)
        metrics = cap["mobileEmulation"]["deviceMetrics"]
        self.assertEqual(metrics["height"], 0)
        self.assertEqual(metrics["width"], 430)
        self.assertIn(eh.window_size_arg(), cap["arguments"])
        self.assertNotIn("--window-size=430,0", cap["arguments"])


class ScrollHelperTests(unittest.TestCase):
    """A keyboard/JS fallback for review, for when the mouse is awkward."""

    def test_scroll_page_moves_by_a_viewport_fraction(self):
        driver = MagicMock()
        cc.scroll_page(driver, "down")
        script = driver.execute_script.call_args.args[0]
        self.assertIn("scrollBy", script)
        self.assertIn("innerHeight", script)

    def test_scroll_page_directions_are_signed(self):
        driver = MagicMock()
        cc.scroll_page(driver, "down")
        down = driver.execute_script.call_args.args[1]
        cc.scroll_page(driver, "up")
        up = driver.execute_script.call_args.args[1]
        self.assertGreater(down, 0)
        self.assertLess(up, 0)

    def test_scroll_page_supports_top_and_bottom(self):
        driver = MagicMock()
        cc.scroll_page(driver, "bottom")
        self.assertIn("scrollHeight", driver.execute_script.call_args.args[0])
        cc.scroll_page(driver, "top")
        self.assertIn("scrollTo", driver.execute_script.call_args.args[0])

    def test_scroll_page_rejects_unknown_direction(self):
        with self.assertRaises(ValueError):
            cc.scroll_page(MagicMock(), "sideways")

    def test_scroll_page_is_best_effort_on_driver_failure(self):
        driver = MagicMock()
        driver.execute_script.side_effect = RuntimeError("session gone")
        self.assertFalse(cc.scroll_page(driver, "down"))


if __name__ == "__main__":
    unittest.main()
