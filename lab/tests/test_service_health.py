import unittest
from unittest.mock import Mock

import maintenance_service
import requests


class ServiceHealthTests(unittest.TestCase):
    def setUp(self):
        self.check_health = getattr(maintenance_service, "check_service_health", None)
        self.assertTrue(callable(self.check_health), "Read-only service diagnostics are not implemented")
        self.session = Mock()
        self.response = Mock(status_code=200)
        self.session.get.return_value = self.response
        self.telegram = {"mode": "host", "relay_url": "https://relay.example/telegram.php",
                         "bot_token": "synthetic-private-token", "relay_secret": "synthetic-private-secret"}
        self.proxy = {"enabled": False}

    def run_health(self, cloud="https://cloud.example", telegram=None, proxy=None):
        return self.check_health(cloud_url=cloud, telegram=self.telegram if telegram is None else telegram,
                                 proxy=self.proxy if proxy is None else proxy, session=self.session)

    def test_health_has_exactly_three_service_scopes_and_timestamp(self):
        result = self.run_health()
        self.assertTrue(result["ok"])
        self.assertEqual([row["id"] for row in result["steps"]], ["cloud", "telegram", "proxy"])
        self.assertTrue(result["checked_at"])
        for row in result["steps"]:
            self.assertTrue(row["scope"])
        self.assertEqual(result["steps"][2]["status"], "not_configured")

    def test_telegram_reachability_never_claims_delivery_or_bot_readiness(self):
        row = self.run_health()["steps"][1]
        self.assertEqual(row["status"], "reachable")
        self.assertEqual(row["readiness"], "unknown")
        self.assertIn("not tested", row["message"])

    def test_forbidden_telegram_is_auth_denied_not_healthy(self):
        self.session.get.side_effect = [Mock(status_code=200), Mock(status_code=403)]
        result = self.run_health()
        self.assertEqual(result["steps"][1]["status"], "auth_denied")
        self.assertNotEqual(result["overall"], "ok")

    def test_no_diagnostic_message_or_token_is_sent(self):
        result = self.run_health()
        self.assertEqual(self.session.get.call_count, 2)
        for call in self.session.get.call_args_list:
            self.assertNotIn("synthetic-private", str(call))
            self.assertNotIn("sendMessage", str(call))
            self.assertNotIn("params", call.kwargs)
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertTrue(call.kwargs["verify"])
            self.assertTrue(call.kwargs["stream"])
        self.assertNotIn("synthetic-private", str(result))
        self.session.post.assert_not_called()
        self.assertIs(self.session.trust_env, False)

    def test_error_details_and_proxy_credentials_do_not_escape(self):
        self.session.get.side_effect = requests.ConnectionError("https://user:private@proxy.example/secret-token")
        result = self.run_health(proxy={"enabled": True, "proxy_url": "http://user:private@127.0.0.1:18081"})
        self.assertNotIn("private", str(result))
        self.assertNotIn("secret-token", str(result))
        self.assertTrue(all(row["status"] == "error" for row in result["steps"]))

    def test_timeout_is_distinct_from_generic_failure(self):
        self.session.get.side_effect = requests.Timeout("synthetic timeout")
        result = self.run_health()
        self.assertEqual(result["steps"][0]["status"], "timeout")
        self.assertEqual(result["steps"][1]["status"], "timeout")

    def test_missing_configuration_sends_no_network_requests(self):
        result = self.run_health(cloud=None, telegram={})
        self.session.get.assert_not_called()
        self.assertTrue(all(row["status"] == "not_configured" for row in result["steps"]))

    def test_credential_or_query_bearing_endpoint_is_not_probed(self):
        result = self.run_health(cloud="https://user:secret@cloud.example",
                                 telegram={"mode": "host", "relay_url": "https://relay.example?token=secret"})
        self.session.get.assert_not_called()
        self.assertEqual(result["steps"][0]["status"], "unknown")
        self.assertEqual(result["steps"][1]["status"], "unknown")

    def test_proxy_transport_uses_only_the_explicit_configured_route(self):
        route = "http://fixture:fixture@127.0.0.1:18081"
        result = self.run_health(proxy={"enabled": True, "proxy_url": route})
        proxy_call = self.session.get.call_args_list[-1]
        self.assertEqual(proxy_call.kwargs["proxies"], {"http": route, "https": route})
        self.assertEqual(result["steps"][2]["status"], "ok")
        self.assertEqual(self.response.close.call_count, 3)


class PanelHealthIntegrationTests(unittest.TestCase):
    def test_panel_uses_read_only_health_with_minimum_configuration(self):
        import types
        from unittest.mock import patch
        import panel
        expected = {"ok": True, "steps": []}
        telegram = {"mode": "host", "relay_url": "https://relay.example/telegram.php",
                    "api_base": "https://api.telegram.org", "proxy_url": "",
                    "bot_token": "synthetic-private-token", "relay_secret": "synthetic-private-secret"}
        with patch("cloud_sync.config.load_config", return_value=types.SimpleNamespace(api_base="https://cloud.example")), \
                patch.object(panel, "load_telegram_config", return_value=telegram), \
                patch.object(panel, "load_system_proxy_config", return_value={"enabled": False, "proxy_url": ""}), \
                patch("maintenance_service.check_service_health", return_value=expected) as health, \
                patch("requests.get", return_value=Mock(status_code=200)) as legacy:
            self.assertIs(panel.check_services_health(), expected)
        health.assert_called_once_with(cloud_url="https://cloud.example",
                                       telegram={key: telegram[key] for key in ("mode", "relay_url", "api_base", "proxy_url")},
                                       proxy={"enabled": False, "proxy_url": ""})
        legacy.assert_not_called()


class DriverDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        import json
        from unittest.mock import patch
        import panel
        import chromedriver_updater
        self.panel = panel
        self.session = Mock()
        self.responses = []
        self.payloads = {
            chromedriver_updater.CFT_LAST_KNOWN_GOOD_URL: {"channels": {"Stable": {"version": "154.0.1.2"}}},
            chromedriver_updater.CFT_MILESTONE_URL: {"milestones": {"154": {"version": "154.0.1.2",
                "downloads": {"chromedriver": [{"platform": "win64", "url": "https://untrusted.example/driver.zip"}]}}}},
        }

        def get(url, **kwargs):
            self.assertIn(url, self.payloads)
            body = json.dumps(self.payloads[url]).encode("utf-8")
            response = Mock(status_code=200, headers={"Content-Length": str(len(body))})
            response.iter_content.return_value = [body]
            response.json.return_value = self.payloads[url]
            self.responses.append(response)
            return response

        self.session.get.side_effect = get
        for target, name, kwargs in (
                (panel, "get_installed_chrome_version", {"return_value": "154.0.1.1"}),
                (panel, "load_system_proxy_config", {"return_value": {"enabled": False, "proxy_url": ""}}),
                ("captcha_collector", "resolve_chromedriver", {"return_value": "fixture-driver.exe"}),
                ("captcha_collector", "_read_major_version", {"return_value": 154}),
                (chromedriver_updater, "read_exe_version", {"return_value": "154.0.1.1"}),
                (requests, "Session", {"return_value": self.session})):
            operation = (patch(target + "." + name, **kwargs) if isinstance(target, str) else
                         patch.object(target, name, **kwargs))
            operation.start()
            self.addCleanup(operation.stop)

    def test_check_uses_verified_explicit_transport_and_closes_responses(self):
        result = self.panel.check_system_updates()
        self.assertTrue(result["ok"])
        self.assertIs(self.session.trust_env, False)
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs["proxies"], {})
            self.assertTrue(call.kwargs.get("verify"))
            self.assertIs(call.kwargs.get("allow_redirects"), False)
            self.assertTrue(call.kwargs.get("stream"))
        self.assertTrue(self.responses)
        for response in self.responses:
            response.close.assert_called_once()
        self.session.close.assert_called_once()

    def test_catalogue_urls_cannot_become_installer_input(self):
        result = self.panel.check_system_updates()
        self.assertNotIn("untrusted.example", str(result))
        self.assertEqual(result["chromedriver"]["matched_version"], "154.0.1.2")

    def test_diagnostic_failure_never_exposes_proxy_or_exception_secrets(self):
        self.session.get.side_effect = requests.ConnectionError("http://fixture:private-secret@proxy.example")
        result = self.panel.check_system_updates()
        self.assertFalse(result["ok"])
        self.assertNotIn("private-secret", str(result))
        self.assertNotIn("fixture:", str(result))

    def test_incomplete_enabled_proxy_refuses_check_without_direct_fallback(self):
        from unittest.mock import patch
        with patch.object(self.panel, "load_system_proxy_config", return_value={"enabled": True, "proxy_url": ""}):
            result = self.panel.check_system_updates()
        self.assertFalse(result["ok"])
        self.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
