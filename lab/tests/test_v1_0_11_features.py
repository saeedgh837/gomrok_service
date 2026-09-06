import json
import os
import tempfile
import unittest
from unittest.mock import patch

import panel


class PanelVersionTests(unittest.TestCase):
    def test_version_text_helper_matches_version_file(self):
        version_path = os.path.join(os.path.dirname(panel.__file__), "VERSION")
        with open(version_path, encoding="utf-8") as f:
            expected = f.read().strip()
        self.assertEqual(panel.app_version_text(), expected)

    def test_all_pages_embed_version(self):
        version = panel.app_version_text()
        for page in (panel.render_home_page(),
                     panel.render_page(),
                     panel.render_estelam_page()):
            html = page.decode("utf-8")
            self.assertIn("irica-version", html)
            self.assertIn(version, html)


class InquiryProfileNameTests(unittest.TestCase):
    def test_service_results_show_profile_name(self):
        source = panel.PAGE_JS
        self.assertIn("profileInquiryLabel", source)
        self.assertIn("device-result-profile", source)

    def test_inquiry_panel_heading_carries_profile_name(self):
        source = panel.PAGE_JS
        # buildDeviceInquiryPanel must append the profile name to the heading
        self.assertIn("استعلام وضعیت دستگاه\u200cها'+", source)

    def test_rendered_page_has_inquiry_label(self):
        html = panel.render_page().decode("utf-8")
        self.assertIn("profileInquiryLabel", html)


class ReleaseBlockedProfileTests(unittest.TestCase):
    def _store(self, path, **profile):
        base = {"id": "p1", "title": "Leila", "national": "0012345678",
                "registration_status": "blocked", "enabled": False,
                "run_status": "idle", "runner_pid": 0,
                "runtime_error": "این پروفایل دیگر قابل ورود نیست: دو اسلات"}
        base.update(profile)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "p1", "profiles": [base]}, handle)

    def _paths(self, tmp):
        path = os.path.join(tmp, "profiles.json")
        return path, patch.object(panel, "PROFILES_PATH", path), \
            patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
            patch.object(panel, "_STORE_LOCK_PATH", path + ".lock")

    def test_release_blocked_profile_resets_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path)
            with p1, p2, p3:
                ok, message = panel.release_blocked_profile_with_reason("p1")
                self.assertTrue(ok, message)
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["registration_status"], "pending")
                self.assertEqual(profile["runtime_error"], "")
                self.assertEqual(profile["run_status"], "idle")
                self.assertEqual(profile["runner_pid"], 0)
                self.assertTrue(profile["enabled"])

    def test_release_blocked_profile_rejects_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path, run_status="busy", runner_pid=99999)
            with p1, p2, p3:
                ok, message = panel.release_blocked_profile_with_reason("p1")
                self.assertFalse(ok)
                self.assertIn("در حال اجرا", message)
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["registration_status"], "blocked")

    def test_release_blocked_profile_rejects_non_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path, registration_status="pending")
            with p1, p2, p3:
                ok, message = panel.release_blocked_profile_with_reason("p1")
                self.assertFalse(ok)
                self.assertIn("مسدود", message)

    def test_release_blocked_missing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path)
            with p1, p2, p3:
                ok, message = panel.release_blocked_profile_with_reason("nope")
                self.assertFalse(ok)
                self.assertTrue(message)

    def test_release_resets_inquiry_state_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path, device_inquiry_pid=4242,
                        device_inquiry_service="gomrok",
                        gomrok_inquiry_status="running",
                        hamta_inquiry_status="running")
            with p1, p2, p3:
                self.assertTrue(panel.release_blocked_profile_with_reason("p1")[0])
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["device_inquiry_pid"], 0)
                self.assertEqual(profile["gomrok_inquiry_status"], "not_checked")
                self.assertEqual(profile["hamta_inquiry_status"], "not_checked")

    def test_ui_has_release_button_on_blocked_edit(self):
        source = panel.PAGE_JS
        self.assertIn("releaseBlockedProfile", source)
        self.assertIn("آزادسازی و شروع مجدد", source)
        self.assertIn("/release-blocked", source)
        # The button appears in the edit form, guarded by blocked status
        self.assertIn("p.registration_status==='blocked'", source)


if __name__ == "__main__":
    unittest.main()
