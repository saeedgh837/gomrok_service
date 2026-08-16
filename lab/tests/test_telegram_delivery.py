import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import panel
import telegram_notify


class TelegramDeliveryTests(unittest.TestCase):
    def test_send_photo_retries_until_telegram_confirms_ok(self):
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = {"ok": False, "description": "temporary"}
        second = Mock()
        second.raise_for_status.return_value = None
        second.json.return_value = {"ok": True, "result": {"message_id": 12}}
        fd, photo_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(photo_path) and os.remove(photo_path))
        with patch.object(telegram_notify, "ensure_config", return_value={
                    "api_base": "https://api.telegram.org", "bot_token": "x", "chat_ids": ["1"]}), \
                patch("requests.post", side_effect=[first, second]) as post, \
                patch("telegram_notify.time.sleep"):
            result = telegram_notify.send_photo(photo_path, "caption")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result[0]["result"]["message_id"], 12)

    def test_customer_chat_id_is_added_without_duplicates(self):
        self.assertEqual(
            telegram_notify._target_chat_ids({"chat_ids": ["1", "2"]}, ["2", "3"]),
            ["1", "2", "3"],
        )

    def test_profile_preserves_customer_chat_id(self):
        profile = panel._clean_profile({"title": "A", "telegram_chat_id": " 12345 "})
        self.assertEqual(profile["telegram_chat_id"], "12345")


if __name__ == "__main__":
    unittest.main()
