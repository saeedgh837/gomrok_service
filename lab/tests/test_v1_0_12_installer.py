import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app_paths
import captcha_collector
import panel


class SingleInstanceTests(unittest.TestCase):
    def test_another_panel_on_port_refuses_to_start(self):
        # app_version_text() must exist for the port-probe User-Agent check
        self.assertTrue(callable(panel.app_version_text))
        # The gate must be a pure function we can test without sockets:
        # it receives (probe_ok, probe_version) and decides.
        self.assertEqual(panel.start_gate_decision(True, "1.0.10"),
                         ("already-running", "1.0.10"))
        self.assertEqual(panel.start_gate_decision(False, ""), ("proceed", ""))

    def test_start_gate_wraps_main(self):
        source = open(os.path.join(os.path.dirname(panel.__file__),
                                   "panel.py"), encoding="utf-8").read()
        self.assertIn("start_gate_decision", source)
        self.assertIn("_panel_already_running", source)


class ChromedriverResolverTests(unittest.TestCase):
    def test_resolver_order_tools_then_root_override_then_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = os.path.join(tmp, ".tools")
            os.makedirs(tools)
            bundled = os.path.join(tools, "chromedriver.exe")
            with open(bundled, "w", encoding="utf-8") as f:
                f.write("x")
            override = os.path.join(tmp, "override", "chromedriver.exe")
            os.makedirs(os.path.dirname(override))
            with open(override, "w", encoding="utf-8") as f:
                f.write("y")

            # 1) bundled .tools wins when no explicit override is set
            with patch.object(captcha_collector, "CHROMEDRIVER_PATH", ""), \
                    patch.object(captcha_collector, "HERE", tmp):
                self.assertEqual(captcha_collector.resolve_chromedriver(), bundled)
            # 2) explicit override (e.g. C:\chromedriver.exe) wins when set & real
            with patch.object(captcha_collector, "CHROMEDRIVER_PATH", override), \
                    patch.object(captcha_collector, "HERE", tmp):
                self.assertEqual(captcha_collector.resolve_chromedriver(), override)
            # 3) explicit override that does not exist falls back to bundled
            with patch.object(captcha_collector, "CHROMEDRIVER_PATH",
                              os.path.join(tmp, "missing", "chromedriver.exe")), \
                    patch.object(captcha_collector, "HERE", tmp):
                self.assertEqual(captcha_collector.resolve_chromedriver(), bundled)
            # 4) nothing anywhere -> empty string (selenium manager takes over)
            with patch.object(captcha_collector, "CHROMEDRIVER_PATH", ""), \
                    patch.object(captcha_collector, "HERE",
                                 os.path.join(tmp, "empty")):
                self.assertEqual(captcha_collector.resolve_chromedriver(), "")

    def test_version_compat_warns_on_mismatch(self):
        # major versions equal -> no warning; different -> warning returned
        self.assertEqual(captcha_collector.driver_compat_problem(
            "152.0.7977.75", "152.0.7977.83"), "")
        problem = captcha_collector.driver_compat_problem(
            "114.0.5735.90", "152.0.7977.83")
        self.assertIn("152", problem)
        self.assertIn("114", problem)
        # unparseable driver version -> cannot compare, no false alarm
        self.assertEqual(captcha_collector.driver_compat_problem(
            "", "152.0.7977.83"), "")
        # missing browser version -> unknown, no false alarm
        self.assertEqual(captcha_collector.driver_compat_problem(
            "152.0.7977.75", ""), "")

    def test_robots_use_shared_resolver(self):
        # exitban and estelam_hamta and gomrok2 must route through the
        # shared resolver instead of their own hardcoded paths.
        exitban_src = open(os.path.join(os.path.dirname(panel.__file__),
                                        "exitban.py"), encoding="utf-8").read()
        self.assertIn("resolve_chromedriver", exitban_src)
        hamta_src = open(os.path.join(os.path.dirname(panel.__file__),
                                      "estelam_hamta.py"), encoding="utf-8").read()
        self.assertIn("resolve_chromedriver", hamta_src)
        g2_src = open(os.path.join(os.path.dirname(panel.__file__),
                                   "gomrok2.py"), encoding="utf-8").read()
        self.assertIn("resolve_chromedriver", g2_src)


class VersionMismatchRobotTests(unittest.TestCase):
    def test_build_chrome_reports_mismatch_warning(self):
        # check_driver_compat must be consulted even when no driver resolves:
        # patch the webdriver so no browser/Selenium-Manager call happens.
        with patch.object(captcha_collector, "CHROMEDRIVER_PATH", ""), \
                patch.object(captcha_collector, "HERE", "nowhere"), \
                patch.object(captcha_collector, "check_driver_compat") as chk, \
                patch.object(captcha_collector.webdriver, "Chrome",
                             side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                captcha_collector.build_chrome()
        chk.assert_called_once()


if __name__ == "__main__":
    unittest.main()
