# -*- coding: utf-8 -*-
"""Unit tests for Telegram host relay, proxy options, and CRM panel bug fixes.

Covers:
1. Mode resolution and URL generation in telegram_notify (host, proxy, direct).
2. Secret token redaction in telegram_notify._redact.
3. Header and proxy propagation in telegram_notify._post_confirmed.
4. Telegram config persistence in panel.py (load, save, public view without secrets).
5. Connectivity testing in panel.py (test_telegram_api, run_telegram_test).
6. CRM stage transition fix for 'handed' (advances to 'to_payment').
7. Stage skip warning dictionary contains 'problem'.
8. Case detail navigation in done_tab, problem_tab, and closed_tab.
9. Signature stability and responsive refresh in dlgCaseSig.
10. PHP relay script (host/public/telegram.php) execution via CLI.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import panel
import telegram_notify


class TelegramNotifyModeTests(unittest.TestCase):
    def test_mode_resolution_explicit(self):
        self.assertEqual(telegram_notify._resolve_mode({"mode": "host"}), "host")
        self.assertEqual(telegram_notify._resolve_mode({"mode": "proxy"}), "proxy")
        self.assertEqual(telegram_notify._resolve_mode({"mode": "direct"}), "direct")

    def test_mode_resolution_inferred(self):
        self.assertEqual(telegram_notify._resolve_mode({"proxy_url": "socks5h://127.0.0.1:10808"}), "proxy")
        self.assertEqual(telegram_notify._resolve_mode({"relay_url": "https://api.oneserver.in/telegram.php"}), "host")
        self.assertEqual(telegram_notify._resolve_mode({"api_base": "https://worker.workers.dev"}), "direct")
        self.assertEqual(telegram_notify._resolve_mode({}), "host")

    def test_api_url_host_mode(self):
        cfg = {
            "mode": "host",
            "relay_url": "https://api.oneserver.in/telegram.php",
            "bot_token": "123:ABC",
        }
        url = telegram_notify._api("sendMessage", cfg)
        self.assertEqual(url, "https://api.oneserver.in/telegram.php?method=sendMessage&token=123:ABC")

    def test_api_url_direct_mode(self):
        cfg = {
            "mode": "direct",
            "api_base": "https://worker.workers.dev",
            "bot_token": "123:ABC",
        }
        url = telegram_notify._api("sendMessage", cfg)
        self.assertEqual(url, "https://worker.workers.dev/bot123:ABC/sendMessage")

    def test_redact_hides_both_token_and_secret(self):
        token = "987654:XYZ-SECRET_TOKEN"
        secret = "MY_RELAY_PASSWORD_123"
        raw = f"Error with token {token} and secret {secret} at url"
        redacted = telegram_notify._redact(raw, token, secret)
        self.assertNotIn(token, redacted)
        self.assertNotIn(secret, redacted)
        self.assertIn("***", redacted)

    def test_post_confirmed_sends_relay_headers_in_host_mode(self):
        cfg = {
            "mode": "host",
            "relay_url": "https://api.oneserver.in/telegram.php",
            "relay_secret": "my-secret",
            "bot_token": "123:ABC",
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 42}}
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            res = telegram_notify._post_confirmed("sendMessage", cfg, {"chat_id": "111", "text": "hi"}, attempts=1)
            self.assertTrue(res.get("ok"))
            mock_post.assert_called_once()
            _args, kwargs = mock_post.call_args
            headers = kwargs.get("headers") or {}
            self.assertEqual(headers.get("X-Relay-Secret"), "my-secret")
            self.assertEqual(headers.get("X-Telegram-Bot-Token"), "123:ABC")
            self.assertEqual(kwargs.get("proxies"), {"http": None, "https": None})

    def test_post_confirmed_passes_proxies_in_proxy_mode(self):
        cfg = {
            "mode": "proxy",
            "proxy_url": "socks5h://127.0.0.1:10808",
            "bot_token": "123:ABC",
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 43}}
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            res = telegram_notify._post_confirmed("sendMessage", cfg, {"chat_id": "111", "text": "hi"}, attempts=1)
            self.assertTrue(res.get("ok"))
            mock_post.assert_called_once()
            _args, kwargs = mock_post.call_args
            proxies = kwargs.get("proxies") or {}
            self.assertEqual(proxies.get("http"), "socks5h://127.0.0.1:10808")
            self.assertEqual(proxies.get("https"), "socks5h://127.0.0.1:10808")


class PanelTelegramConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = os.path.join(self.tmp.name, "telegram_config.json")
        self.orig_path = panel.TELEGRAM_PATH
        panel.TELEGRAM_PATH = self.cfg_path
        self.addCleanup(setattr, panel, "TELEGRAM_PATH", self.orig_path)

    def test_save_and_load_roundtrip_all_modes(self):
        saved = panel.save_telegram_config({
            "mode": "host",
            "relay_url": "https://custom.host/telegram.php",
            "relay_secret": "secret-999",
            "bot_token": "111:TOKEN",
            "chat_ids": ["123", "456"],
        })
        self.assertTrue(saved["token_set"])
        self.assertTrue(saved["relay_secret_set"])
        self.assertEqual(saved["mode"], "host")
        self.assertEqual(saved["relay_url"], "https://custom.host/telegram.php")
        self.assertEqual(saved["chat_ids"], ["123", "456"])
        self.assertNotIn("bot_token", saved)
        self.assertNotIn("relay_secret", saved)

        loaded = panel.load_telegram_config()
        self.assertEqual(loaded["bot_token"], "111:TOKEN")
        self.assertEqual(loaded["relay_secret"], "secret-999")
        self.assertEqual(loaded["relay_url"], "https://custom.host/telegram.php")

    def test_token_and_secret_kept_when_blank_on_update(self):
        panel.save_telegram_config({
            "bot_token": "KEEP_ME",
            "relay_secret": "KEEP_SECRET",
            "mode": "proxy",
            "proxy_url": "http://127.0.0.1:8080",
        })
        # Update with empty token and secret (as web UI does when unchanged)
        panel.save_telegram_config({
            "bot_token": "",
            "mode": "proxy",
            "proxy_url": "http://127.0.0.1:9090",
        })
        loaded = panel.load_telegram_config()
        self.assertEqual(loaded["bot_token"], "KEEP_ME")
        self.assertEqual(loaded["relay_secret"], "KEEP_SECRET")
        self.assertEqual(loaded["proxy_url"], "http://127.0.0.1:9090")

    def test_test_telegram_api_calls_host_relay(self):
        panel.save_telegram_config({
            "mode": "host",
            "relay_url": "https://api.oneserver.in/telegram.php",
            "relay_secret": "sec",
            "bot_token": "123:ABC",
        })
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.content = b'{"ok": true, "result": {"username": "my_bot"}}'
        mock_resp.json.return_value = {"ok": True, "result": {"username": "my_bot"}}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            res = panel.test_telegram_api(attempts=1)
            self.assertTrue(res["ok"])
            self.assertIn("@my_bot", res["message"])
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertIn("https://api.oneserver.in/telegram.php?method=getMe&token=123:ABC", args[0])
            self.assertEqual(kwargs["headers"]["X-Relay-Secret"], "sec")


class CRMPageAndStageFixTests(unittest.TestCase):
    def test_handed_stage_advance_returns_to_payment(self):
        # Regression test for critical handed stage bug:
        # Previously handed stage returned action '__start_payment', which crm.py rejected.
        self.assertIn("d.stage==='handed'?'to_payment':", panel.CRM_PAGE)
        self.assertNotIn("d.stage==='handed'?(d.auto_advance?'__noop':'__start_payment'):", panel.CRM_PAGE)

    def test_stage_skip_warn_includes_problem(self):
        self.assertIn("'problem':'پرونده بدون تکرار استعلام به گام استعلام برمی‌گردد.'", panel.CRM_PAGE)

    def test_case_detail_buttons_rendered_in_all_tabs(self):
        # done_tab and problem_tab and closed_tab
        self.assertIn(r"caseDetail(\''+d.id+'\')", panel.CRM_PAGE)
        # closed_tab cards have devrow id
        self.assertIn('id="devrow-\'+d.id+\'"', panel.CRM_PAGE)

    def test_dlg_case_sig_includes_customer_and_owner_fields(self):
        self.assertIn("c.name,d.owner_id,o.name,o.passport,o.national", panel.CRM_PAGE)

    def test_periodic_interval_input_uses_raw_ascii_hours(self):
        # Must not use fa(hours) inside input[type="number"] which crashes Chromium validation
        self.assertIn('id="periodicInterval" class="ltr" style="width:64px" type="number" min="1" max="72" value="\'+hours+\'"', panel.CRM_PAGE)

    def test_settings_tab_contains_telegram_and_auto_advance_controls(self):
        self.assertIn('id="stTgToken"', panel.CRM_PAGE)
        self.assertIn('id="stTgMode"', panel.CRM_PAGE)
        self.assertIn('id="stTgRelayUrl"', panel.CRM_PAGE)
        self.assertIn('id="stTgProxyUrl"', panel.CRM_PAGE)
        self.assertIn('id="stTgApiBase"', panel.CRM_PAGE)
        self.assertIn('id="stTgSaveBtn"', panel.CRM_PAGE)
        self.assertIn('id="stTgTestBtn"', panel.CRM_PAGE)
        self.assertIn('id="stAutoAdv"', panel.CRM_PAGE)
        self.assertIn('settingsLoadTelegram', panel.CRM_PAGE)
        self.assertIn('settingsTestTelegram', panel.CRM_PAGE)


class PHPTelegramRelayTests(unittest.TestCase):
    def test_php_script_syntax(self):
        php_file = os.path.join(os.path.dirname(__file__), "..", "..", "host", "public", "telegram.php")
        php_file = os.path.abspath(php_file)
        self.assertTrue(os.path.isfile(php_file))
        res = subprocess.run(["php", "-l", php_file], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_php_health_check(self):
        php_file = os.path.join(os.path.dirname(__file__), "..", "..", "host", "public", "telegram.php")
        php_file = os.path.abspath(php_file)
        code = f'$_SERVER["REQUEST_METHOD"] = "GET"; require "{php_file.replace(chr(92), "/")}";'
        res = subprocess.run(["php", "-r", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("service"), "irica-telegram-relay")

    def test_php_rejects_missing_token_on_post(self):
        php_file = os.path.join(os.path.dirname(__file__), "..", "..", "host", "public", "telegram.php")
        php_file = os.path.abspath(php_file)
        code = f'$_SERVER["REQUEST_METHOD"] = "POST"; require "{php_file.replace(chr(92), "/")}";'
        res = subprocess.run(["php", "-r", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error_code"), 400)


if __name__ == "__main__":
    unittest.main()
