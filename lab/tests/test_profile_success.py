import json
import os
import tempfile
import unittest
from unittest.mock import patch

import gomrok
import panel


class FakeDriver:
    def __init__(self):
        self.cdp_calls = []
        self.scripts = []

    def execute_cdp_cmd(self, name, payload):
        self.cdp_calls.append((name, payload))

    def execute_script(self, script):
        self.scripts.append(script)


class ProfileSuccessTests(unittest.TestCase):
    def test_exact_claimed_attempt_persists_payment_status_and_rejects_other_owners(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            inputs_path = os.path.join(tmp, "inputs.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({
                    "active_id": "p1",
                    "profiles": [{
                        "id": "p1", "title": "Ali", "national": "0012345679",
                        "birth": "1375/04/29", "mobile": "09121234567",
                        "passport": "PP0000001", "imeis": ["111111111111111"],
                    }],
                }, f)

            with patch.object(panel, "PROFILES_PATH", profiles_path), \
                    patch.object(panel, "INPUTS_PATH", inputs_path), \
                    patch.object(panel, "_STORE_LOCK_PATH", profiles_path + ".lock"):
                claimed = panel.claim_profile("p1")
                self.assertIsNotNone(claimed)
                runner_id = claimed["runner_id"]
                fingerprint = claimed["claimed_identity_fingerprint"]
                self.assertTrue(runner_id)
                self.assertTrue(fingerprint)
                evidence = {"link": "https://pay.test/1"}
                before = panel.load_store()
                for profile_id, runner, expected in (
                        ("missing", runner_id, fingerprint),
                        ("p1", "unrelated-runner", fingerprint),
                        ("p1", runner_id, "unrelated-fingerprint")):
                    with self.subTest(profile_id=profile_id, runner=runner, fingerprint=expected):
                        rejected = panel.publish_profile_result(profile_id, runner, expected, evidence)
                        self.assertFalse(rejected["ok"])
                        self.assertEqual(panel.load_store(), before)
                self.assertFalse(hasattr(panel, "mark_profile_success"))
                self.assertEqual(panel.load_store(), before)
                published = panel.publish_profile_result("p1", runner_id, fingerprint, evidence)
                store = panel.load_store()

            self.assertTrue(published["ok"], published)
            profile = store["profiles"][0]
            self.assertEqual(profile["registration_status"], "success")
            self.assertEqual(profile["payment_link"], "https://pay.test/1")
            self.assertTrue(profile["success_at"])

    def test_profile_banner_is_blue_and_persistent_above_manual_banner(self):
        driver = FakeDriver()
        gomrok.install_profile_banner(driver, 'Ali "A"')

        self.assertEqual(driver.cdp_calls[0][0], "Page.addScriptToEvaluateOnNewDocument")
        source = driver.cdp_calls[0][1]["source"]
        self.assertIn("Ali", source)
        self.assertIn("#1677ff", source)
        self.assertIn("hermes-profile-banner", source)
        self.assertIn("hermes-profile-banner-close", source)
        self.assertIn("window.__hermes_profile_banner_closed", source)
        self.assertIn("top:0", source)
        self.assertIn("setInterval(watch", source)
        self.assertIn("if(document.documentElement && !window.__hermes_profile_banner_observer)", source)
        self.assertNotIn("new MutationObserver(show).observe(document.documentElement", source)
        self.assertIn("until closed", source)
        self.assertIn("left:0;right:0", source)
        self.assertIn("data-hermes-hide-in-screenshot", source)
        self.assertEqual(driver.scripts, [source])

    def test_manual_banner_is_positioned_below_profile_banner(self):
        source = gomrok.ManualManageController._BANNER_JS
        self.assertIn("__kman_position", source)
        self.assertIn("hermes-profile-banner", source)
        self.assertIn("offsetHeight", source)
        self.assertIn("setInterval(window.__kman_position", source)

    def test_profile_edit_preserves_success_metadata(self):
        current = {
            "id": "p1", "title": "Ali", "registration_status": "success",
            "success_at": "2026-01-01 12:00:00", "payment_link": "https://pay.test/1",
        }
        edited = {"id": "p1", "title": "Ali edited"}

        merged = panel.merge_profile_update(current, edited)

        self.assertEqual(merged["title"], "Ali edited")
        self.assertEqual(merged["registration_status"], "success")
        self.assertEqual(merged["success_at"], "2026-01-01 12:00:00")
        self.assertEqual(merged["payment_link"], "https://pay.test/1")


if __name__ == "__main__":
    unittest.main()
