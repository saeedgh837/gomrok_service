import base64
import json
import unittest
from unittest.mock import mock_open, patch

import cloudflare_auth_relay as relay


class RelayAuthTests(unittest.TestCase):
    def test_expected_authorization_uses_config_credentials(self):
        config = json.dumps({"username": "user", "password": "pass"})
        with patch("builtins.open", mock_open(read_data=config)):
            expected = "Basic " + base64.b64encode(b"user:pass").decode()
            self.assertEqual(relay.expected_authorization(), expected)

    def test_missing_credentials_are_rejected(self):
        with patch("builtins.open", mock_open(read_data="{}")):
            with self.assertRaises(ValueError):
                relay.load_config()


if __name__ == "__main__":
    unittest.main()
