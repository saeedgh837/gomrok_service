import unittest
from unittest.mock import patch

import panel


class UnusableProfileTests(unittest.TestCase):
    def test_mark_profile_unusable_persists_error_and_disables_profile(self):
        import json
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            profiles = os.path.join(tmp, "profiles.json")
            inputs = os.path.join(tmp, "inputs.json")
            with open(profiles, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "Ali", "enabled": True,
                    "run_status": "busy", "runner_pid": 123,
                }]}, f)
            with patch.object(panel, "PROFILES_PATH", profiles), \
                    patch.object(panel, "INPUTS_PATH", inputs):
                changed = panel.mark_profile_unusable(
                    "p1", "خطا در استعلام IMEI از سامانه همتا: شناسه قبلا ثبت و فعال شده")
                profile = panel.load_store()["profiles"][0]

        self.assertTrue(changed)
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["run_status"], "idle")
        self.assertEqual(profile["registration_status"], "blocked")
        self.assertIn("دیگر قابل ورود نیست", profile["runtime_error"])

    def test_start_rejects_unusable_profile(self):
        with patch.object(panel, "load_store", return_value={"profiles": [{
            "id": "p1", "registration_status": "blocked", "run_status": "idle",
        }]}):
            status, payload = panel.start_profile("p1")
        self.assertEqual(status, 409)
        self.assertIn("قابل استفاده نیست", payload["error"])

    def test_ui_disables_start_and_shows_block_reason(self):
        source = panel.PAGE_JS
        self.assertIn("registration_status==='blocked'", source)
        self.assertIn("غیرقابل ورود", source)
        self.assertIn("runtime_error", source)


if __name__ == "__main__":
    unittest.main()
