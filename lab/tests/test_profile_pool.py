import json
import os
import tempfile
import unittest
from unittest.mock import patch

import panel
import telegram_notify


def _ready_profile(profile_id):
    return {"id": profile_id, "title": profile_id,
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "passport": "PP0000001",
            "imeis": ["352506099000001" if profile_id == "p1" else "352506099000002"]}


class ProfilePoolTests(unittest.TestCase):
    def test_claim_marks_profile_busy_and_release_restores_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [
                    _ready_profile("p1"), _ready_profile("p2"),
                ]}, f)
            with patch.object(panel, "PROFILES_PATH", path), patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")):
                with patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"):
                    claimed = panel.claim_profile()
                    self.assertEqual(claimed["id"], "p1")
                    second = panel.claim_profile()
                    self.assertEqual(second["id"], "p2")
                    self.assertEqual(panel.claim_profile(), None)
                    self.assertTrue(panel.release_profile("p1", claimed["runner_id"]))
                    self.assertEqual(panel.claim_profile()["id"], "p1")

    def test_output_is_stored_on_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [_ready_profile("p1")]}, f)
            with patch.object(panel, "PROFILES_PATH", path), patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")):
                with patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"):
                    claimed = panel.claim_profile("p1")
                    self.assertIsNotNone(claimed)
                    self.assertTrue(panel.record_profile_output(
                        "p1", claimed["runner_id"], "C:/receipt.png", "C:/payment.png", "https://pay",
                        expected_fingerprint=claimed["claimed_identity_fingerprint"]))
                    p = panel.load_store()["profiles"][0]
                    self.assertEqual(p["payment_link"], "https://pay")
                    self.assertEqual(p["receipt_screenshot"], "C:/receipt.png")


class TelegramConfigTests(unittest.TestCase):
    def test_chat_ids_support_legacy_and_multiple_values(self):
        self.assertEqual(telegram_notify._chat_ids({"chat_id": "1"}), ["1"])
        self.assertEqual(telegram_notify._chat_ids({"chat_ids": ["1", "2"]}), ["1", "2"])


if __name__ == "__main__":
    unittest.main()
