"""Current explicit consent is required for every executable installation.

All versions and driver processes are synthetic; no network, proxy, browser or
real driver is started by these regression tests.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import captcha_collector as cc
import chromedriver_updater as upd


class ConsentBoundaryTests(unittest.TestCase):
    def test_manual_update_without_consent_has_no_side_effects(self):
        with patch.object(upd, "update_chromedriver") as install, \
                patch.object(cc, "_driver_update_single_flight") as lock, \
                patch.object(cc, "resolve_chromedriver") as resolve, \
                patch.object(upd, "_spawn_proxy_process") as spawn:
            result = cc.update_chromedriver_now()
        self.assertEqual(result["status"], "consent-required")
        self.assertIn("confirm", result["detail"].lower())
        install.assert_not_called()
        lock.assert_not_called()
        resolve.assert_not_called()
        spawn.assert_not_called()

    def test_truthy_values_do_not_replace_explicit_consent(self):
        for consent in (False, None, "true", "false", 1):
            with self.subTest(consent=consent), \
                    patch.object(upd, "update_chromedriver") as install:
                result = cc.update_chromedriver_now(consent=consent)
                self.assertEqual(result["status"], "consent-required")
                install.assert_not_called()

    def test_low_level_update_without_consent_never_probes_or_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "chromedriver.exe")
            with patch.object(upd, "proxy_ready") as probe, \
                    patch.object(upd, "_spawn_proxy_process") as spawn, \
                    patch.object(upd, "http_get_bytes") as download:
                result = upd.update_chromedriver(target, 154, env={})
            self.assertEqual(result["status"], "consent-required")
            self.assertFalse(os.path.exists(target))
            probe.assert_not_called()
            spawn.assert_not_called()
            download.assert_not_called()

    def test_direct_mode_and_proxy_override_do_not_imply_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            for options in ({"allow_direct": True},
                            {"proxy_override": "http://127.0.0.1:18081"}):
                with self.subTest(options=options), \
                        patch.object(upd, "http_get_bytes") as download, \
                        patch.object(upd, "_spawn_proxy_process") as spawn:
                    result = upd.update_chromedriver(
                        os.path.join(tmp, "chromedriver.exe"), 154,
                        env={}, **options)
                self.assertEqual(result["status"], "consent-required")
                download.assert_not_called()
                spawn.assert_not_called()

    def test_consent_is_not_reused_by_a_later_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "last.json")
            with patch.object(cc, "_driver_update_marker_path", return_value=marker), \
                    patch.object(cc, "resolve_chromedriver", return_value="driver.exe"), \
                    patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                    patch.object(upd, "update_chromedriver", return_value={
                        "status": "updated", "detail": "installed", "version": "154.0.1.2",
                        "target": "driver.exe"}) as install:
                first = cc.update_chromedriver_now(consent=True)
                second = cc.update_chromedriver_now()
            self.assertEqual(first["status"], "updated")
            self.assertEqual(second["status"], "consent-required")
            install.assert_called_once()
            self.assertIs(install.call_args.kwargs["consent"], True)

    def test_confirmed_manual_update_forwards_routing_and_exact_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cc, "_driver_update_marker_path",
                              return_value=os.path.join(tmp, "last.json")), \
                    patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                    patch.object(upd, "update_chromedriver", return_value={
                        "status": "updated", "detail": "installed", "version": "154.0.1.2",
                        "target": "driver.exe"}) as install:
                result = cc.update_chromedriver_now(
                    "driver.exe", consent=True, proxy_override="http://127.0.0.1:18081",
                    allow_direct=False, desired_version="154.0.1.2")
            self.assertEqual(result["status"], "updated")
            kwargs = install.call_args.kwargs
            self.assertIs(kwargs["consent"], True)
            self.assertEqual(kwargs["proxy_override"], "http://127.0.0.1:18081")
            self.assertFalse(kwargs["allow_direct"])
            self.assertEqual(kwargs["desired_version"], "154.0.1.2")


class ReadOnlyCompatibilityTests(unittest.TestCase):
    def test_missing_driver_requires_consent_without_importing_an_updater(self):
        with patch.object(upd, "update_chromedriver") as install, \
                patch.object(cc, "_driver_update_single_flight") as lock:
            result = cc.maybe_update_chromedriver("", 154)
        self.assertEqual(result["status"], "consent-required")
        self.assertIn("missing", result["detail"].lower())
        install.assert_not_called()
        lock.assert_not_called()

    def test_mismatched_driver_check_remains_read_only(self):
        with patch.object(cc, "_read_major_version", return_value=153), \
                patch.object(upd, "update_chromedriver") as install, \
                patch.object(upd, "_spawn_proxy_process") as spawn:
            result = cc.maybe_update_chromedriver("driver.exe", 154)
        self.assertEqual(result["status"], "consent-required")
        self.assertIn("153", result["detail"])
        self.assertIn("154", result["detail"])
        install.assert_not_called()
        spawn.assert_not_called()

    def test_compatible_driver_check_never_contacts_the_catalogue(self):
        with patch.object(cc, "_read_major_version", return_value=154), \
                patch.object(upd, "http_get_bytes") as download:
            result = cc.maybe_update_chromedriver("driver.exe", 154)
        self.assertEqual(result["status"], "up-to-date")
        download.assert_not_called()

    def test_check_driver_compat_reports_missing_local_driver_in_english(self):
        with patch.object(cc, "resolve_chromedriver", return_value=""), \
                patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154):
            problem = cc.check_driver_compat()
        self.assertIn("update", problem.lower())
        self.assertTrue(problem.isascii())


class SeleniumConstructionTests(unittest.TestCase):
    def test_missing_driver_never_reaches_selenium_manager(self):
        with patch.object(cc, "resolve_chromedriver", return_value=""), \
                patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                patch.object(cc.webdriver, "Chrome") as chrome:
            with self.assertRaisesRegex(RuntimeError, "(?i)update"):
                cc.build_chrome()
        chrome.assert_not_called()

    def test_mismatched_driver_is_blocked_before_selenium(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver = os.path.join(tmp, "chromedriver.exe")
            with open(driver, "wb") as handle:
                handle.write(b"SYNTHETIC")
            with patch.object(cc, "resolve_chromedriver", return_value=driver), \
                    patch.object(cc, "_read_major_version", return_value=153), \
                    patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                    patch.object(cc.webdriver, "Chrome") as chrome, \
                    patch.object(upd, "update_chromedriver") as install:
                with self.assertRaisesRegex(RuntimeError, "(?i)update"):
                    cc.build_chrome()
            chrome.assert_not_called()
            install.assert_not_called()

    def test_compatible_driver_is_passed_to_an_explicit_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver = os.path.join(tmp, "chromedriver.exe")
            with open(driver, "wb") as handle:
                handle.write(b"SYNTHETIC")
            with patch.object(cc, "resolve_chromedriver", return_value=driver), \
                    patch.object(cc, "_read_major_version", return_value=154), \
                    patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                    patch.object(cc.webdriver, "Chrome") as chrome, \
                    patch.object(cc, "ChromeService") as service:
                cc.build_chrome()
            service.assert_called_once_with(executable_path=driver)
            self.assertIs(chrome.call_args.kwargs["service"], service.return_value)

    def test_chrome_policy_failure_does_not_fall_back_to_edge_autofetch(self):
        with patch.object(cc, "resolve_chromedriver", return_value=""), \
                patch.object(cc, "USE_EDGE", False), \
                patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                patch.object(cc.webdriver, "Chrome") as chrome, \
                patch.object(cc.webdriver, "Edge") as edge:
            with self.assertRaisesRegex(RuntimeError, "(?i)update"):
                cc.build_driver()
        chrome.assert_not_called()
        edge.assert_not_called()

    def test_chrome_launch_failure_cannot_trigger_an_edge_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cc, "USE_EDGE", False), \
                    patch.object(cc, "HERE", tmp), \
                    patch.dict(os.environ, {"PATH": ""}), \
                    patch.object(cc, "build_chrome", side_effect=RuntimeError("browser failed")), \
                    patch.object(cc.webdriver, "Edge") as edge:
                with self.assertRaisesRegex(RuntimeError, "(?i)manual"):
                    cc.build_driver()
            edge.assert_not_called()

    def test_existing_edge_driver_uses_an_explicit_local_service(self):
        from selenium.webdriver.edge import service as edge_service
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "msedgedriver.exe")
            with open(path, "wb") as handle:
                handle.write(b"SYNTHETIC")
            with patch.object(cc, "EDGEDRIVER_PATH", path, create=True), \
                    patch.object(edge_service, "Service") as service, \
                    patch.object(cc.webdriver, "Edge") as edge:
                cc.build_edge()
            service.assert_called_once_with(executable_path=path)
            self.assertIs(edge.call_args.kwargs["service"], service.return_value)

    def test_missing_edge_driver_never_reaches_selenium_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cc, "HERE", tmp), \
                    patch.dict(os.environ, {"PATH": ""}), \
                    patch.object(cc.webdriver, "Edge") as edge:
                with self.assertRaisesRegex(RuntimeError, "(?i)driver"):
                    cc.build_edge()
            edge.assert_not_called()


class InquiryDriverConstructionTests(unittest.TestCase):
    def test_missing_inquiry_chrome_driver_never_uses_selenium_manager(self):
        import estelam_hamta as hamta
        with patch.object(hamta, "USE_EDGE", False), \
                patch.object(hamta, "cc_resolve_chromedriver", return_value=""), \
                patch.object(cc, "resolve_chromedriver", return_value=""), \
                patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                patch.object(hamta.webdriver, "Chrome") as chrome:
            with self.assertRaisesRegex(RuntimeError, "(?i)update"):
                hamta.build_driver()
        chrome.assert_not_called()

    def test_inquiry_mismatch_is_blocked_before_constructing_a_browser(self):
        import estelam_hamta as hamta
        with patch.object(hamta, "USE_EDGE", False), \
                patch.object(hamta, "cc_resolve_chromedriver", return_value="driver.exe"), \
                patch.object(cc, "_read_major_version", return_value=153), \
                patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                patch.object(hamta.webdriver, "Chrome") as chrome:
            with self.assertRaisesRegex(RuntimeError, "(?i)update"):
                hamta.build_driver()
        chrome.assert_not_called()

    def test_inquiry_compatible_driver_uses_an_explicit_service(self):
        import estelam_hamta as hamta
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chromedriver.exe")
            with open(path, "wb") as handle:
                handle.write(b"SYNTHETIC")
            with patch.object(hamta, "USE_EDGE", False), \
                    patch.object(hamta, "cc_resolve_chromedriver", return_value=path), \
                    patch.object(cc, "_read_major_version", return_value=154), \
                    patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
                    patch.object(hamta, "install_manual_scroll", side_effect=lambda driver: driver), \
                    patch.object(hamta, "ChromeService") as service, \
                    patch.object(hamta.webdriver, "Chrome") as chrome:
                hamta.build_driver()
            service.assert_called_once_with(executable_path=path)
            self.assertIs(chrome.call_args.kwargs["service"], service.return_value)

    def test_missing_inquiry_edge_driver_requires_manual_installation(self):
        import estelam_hamta as hamta
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(hamta, "USE_EDGE", True), \
                    patch.object(cc, "HERE", tmp), \
                    patch.dict(os.environ, {"PATH": ""}), \
                    patch.object(hamta.webdriver, "Edge") as edge:
                with self.assertRaisesRegex(RuntimeError, "(?i)manual"):
                    hamta.build_driver()
            edge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
