import contextlib
import copy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import panel
import panel_auth


class MaintenanceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="irica-maintenance-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, document in (("profiles.json", {"active_id": "", "profiles": []}),
                               ("estelam_store.json", {"devices": []})):
            (self.root / name).write_text(json.dumps(document), encoding="utf-8")
        for key, value in {
            "PROFILES_PATH": str(self.root / "profiles.json"),
            "_STORE_LOCK_PATH": str(self.root / "profiles.json.lock"),
            "INPUTS_PATH": str(self.root / "inputs.json"),
            "ESTELAM_PATH": str(self.root / "estelam_store.json"),
        }.items():
            self.start_patch(panel, key, value)
        panel_auth.reset_sessions_for_tests()
        self.addCleanup(panel_auth.reset_sessions_for_tests)
        self.token = panel_auth.create_session("fixture_user")
        self.snapshot = {
            "ok": True, "checking": False,
            "app": {"state": "available", "current_version": "1.18.3",
                    "checked": True, "can_install": True,
                    "offer": {"offer_id": "application-offer", "version": "1.19.0"}},
            "driver": {"state": "available", "checked": True,
                       "installed_version": "154.0.1.1", "browser_version": "154.0.1.2",
                       "offer": {"offer_id": "driver-offer", "version": "154.0.1.2"}},
        }
        self.status_call = self.start_patch(
            panel, "maintenance_status", return_value=copy.deepcopy(self.snapshot), create=True)
        self.check_call = self.start_patch(
            panel, "maintenance_check", return_value=copy.deepcopy(self.snapshot), create=True)
        self.app_call = self.start_patch(
            panel, "confirm_application_update", create=True,
            return_value={"ok": True, "app": {"state": "downloading"}})
        self.driver_call = self.start_patch(
            panel, "run_chromedriver_update",
            return_value={"ok": True, "driver": {"state": "installing"}})

        request_done = self.request_done = threading.Event()

        class QuietHandler(panel.Handler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    super().do_POST()
                finally:
                    request_done.set()

        self.server = panel.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def start_patch(self, target, name, *args, **kwargs):
        operation = patch.object(target, name, *args, **kwargs)
        result = operation.start()
        self.addCleanup(operation.stop)
        return result

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, route, *, method="GET", body=None, authenticated=True,
                csrf=True, headers=None):
        request_headers = {"Accept": "*/*", "Content-Type": "application/json"}
        if authenticated:
            request_headers["Cookie"] = "irica_session=" + self.token
            if csrf:
                request_headers["X-CSRF-Token"] = panel_auth.session_csrf(self.token) or ""
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        if method == "POST":
            self.request_done.clear()
        try:
            connection.request(method, route,
                               body=json.dumps(body) if body is not None else None,
                               headers=request_headers)
            response = connection.getresponse()
            raw = response.read().decode("utf-8")
            if method == "POST":
                self.assertTrue(self.request_done.wait(4), "The fixture POST handler did not finish")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw
            return response.status, payload
        finally:
            connection.close()

    def test_status_requires_a_live_session(self):
        status, _ = self.request("/api/maintenance", authenticated=False)
        self.assertEqual(status, 401)
        self.status_call.assert_not_called()

    def test_status_reads_snapshots_without_checking_or_installing(self):
        status, result = self.request("/api/maintenance")
        self.assertEqual(status, 200)
        self.assertEqual(result, self.snapshot)
        self.check_call.assert_not_called()
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()

    def test_maintenance_exceptions_never_expose_service_details(self):
        marker = "fixture-maintenance-secret"
        cases = (
            (self.status_call, "/api/maintenance", "GET", None),
            (self.check_call, "/api/maintenance/check", "POST", {"force": False}),
            (self.app_call, "/api/update-app", "POST", {"offer_id": "application-offer"}),
            (self.driver_call, "/api/update-chromedriver", "POST", {"offer_id": "driver-offer", "consent": True}),
            (self.driver_call, "/driver-update", "POST", {"offer_id": "driver-offer", "consent": True}),
        )
        for operation, route, method, body in cases:
            with self.subTest(route=route):
                operation.side_effect = RuntimeError(marker)
                captured = io.StringIO()
                try:
                    with contextlib.redirect_stderr(captured):
                        status, result = self.request(route, method=method, body=body)
                finally:
                    operation.side_effect = None
                self.assertEqual(status, 500)
                self.assertNotIn(marker, json.dumps(result))
                self.assertNotIn(marker, captured.getvalue())
                self.assertEqual(result["errors"], ["Maintenance request failed. Refresh status before retrying."])

    def test_cached_and_manual_checks_are_explicit_check_only_requests(self):
        for force in (False, True):
            with self.subTest(force=force):
                status, _ = self.request("/api/maintenance/check", method="POST",
                                         body={"force": force})
                self.assertEqual(status, 202)
                self.assertIs(self.check_call.call_args.kwargs.get("force"), force)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()

    def test_driver_confirmation_requires_a_live_session(self):
        status, _ = self.request("/api/update-chromedriver", method="POST",
                                 body={"offer_id": "driver-offer", "consent": True},
                                 authenticated=False)
        self.assertEqual(status, 401)
        self.driver_call.assert_not_called()

    def test_expired_session_cannot_confirm_an_update(self):
        panel_auth.destroy_session(self.token)
        status, _ = self.request("/api/update-chromedriver", method="POST",
                                 body={"offer_id": "driver-offer", "consent": True})
        self.assertEqual(status, 401)
        self.driver_call.assert_not_called()

    def test_both_executable_routes_require_csrf(self):
        for route in ("/api/update-app", "/api/update-chromedriver"):
            with self.subTest(route=route):
                status, _ = self.request(route, method="POST", csrf=False,
                                         body={"offer_id": "fixture-offer", "consent": True})
                self.assertEqual(status, 403)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()

    def test_foreign_origin_cannot_confirm_an_update(self):
        for route in ("/api/update-app", "/api/update-chromedriver"):
            with self.subTest(route=route):
                status, _ = self.request(route, method="POST",
                                         body={"offer_id": "fixture-offer", "consent": True},
                                         headers={"Origin": "https://outside.example"})
                self.assertEqual(status, 403)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()

    def test_nonloopback_host_cannot_confirm_an_update(self):
        status, _ = self.request("/api/update-app", method="POST",
                                 body={"offer_id": "application-offer"},
                                 headers={"Host": "outside.example"})
        self.assertEqual(status, 403)
        self.app_call.assert_not_called()

    def test_nonloopback_peer_cannot_use_executable_routes(self):
        for route in ("/api/update-app", "/api/update-chromedriver"):
            with self.subTest(route=route):
                handler = object.__new__(panel.Handler)
                handler.command = "POST"
                handler.server = self.server
                handler.client_address = ("192.0.2.10", 42000)
                handler.headers = {
                    "Host": "127.0.0.1:" + str(self.server.server_address[1]),
                    "Cookie": "irica_session=" + self.token,
                    "X-CSRF-Token": panel_auth.session_csrf(self.token),
                }
                replies = []
                handler._json = lambda payload, code=200: replies.append((code, payload))
                self.assertFalse(handler._gate(route))
                self.assertEqual(replies[0][0], 403)

    def test_app_confirmation_forwards_only_the_current_offer_id(self):
        status, result = self.request("/api/update-app", method="POST",
                                      body={"offer_id": "application-offer"})
        self.assertEqual(status, 202)
        self.assertEqual(result["app"]["state"], "downloading")
        self.app_call.assert_called_once_with("application-offer")

    def test_driver_confirmation_forwards_exact_boolean_consent_and_offer(self):
        status, result = self.request("/api/update-chromedriver", method="POST",
                                      body={"offer_id": "driver-offer", "consent": True})
        self.assertEqual(status, 202)
        self.assertEqual(result["driver"]["state"], "installing")
        self.assertIs(self.driver_call.call_args.kwargs.get("consent"), True)
        self.assertEqual(self.driver_call.call_args.kwargs.get("offer_id"), "driver-offer")

    def test_query_string_does_not_hide_the_authenticated_driver_route(self):
        status, _ = self.request("/api/update-chromedriver?force_direct=1", method="POST",
                                 body={"offer_id": "driver-offer", "consent": True})
        self.assertEqual(status, 202)
        self.assertEqual(self.driver_call.call_args.kwargs.get("offer_id"), "driver-offer")

    def test_browser_cannot_choose_asset_paths_versions_or_installer_arguments(self):
        for route in ("/api/update-app", "/api/update-chromedriver"):
            for field in ("url", "path", "sha256", "desired_version", "arguments"):
                with self.subTest(route=route, field=field):
                    body = {"offer_id": "fixture-offer", field: "untrusted"}
                    if route.endswith("chromedriver"):
                        body["consent"] = True
                    status, _ = self.request(route, method="POST", body=body)
                    self.assertEqual(status, 400)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()

    def test_truthy_values_cannot_replace_explicit_driver_consent(self):
        for value in (False, None, 1, "true", [True]):
            with self.subTest(value=value):
                status, _ = self.request("/api/update-chromedriver", method="POST",
                                         body={"offer_id": "driver-offer", "consent": value})
                self.assertEqual(status, 400)
        self.driver_call.assert_not_called()

    def test_scalar_or_list_json_is_rejected_without_routing(self):
        for value in ([], True, "offer"):
            with self.subTest(value=value):
                status, _ = self.request("/api/update-chromedriver", method="POST", body=value)
                self.assertEqual(status, 400)
        self.driver_call.assert_not_called()

    def test_get_never_confirms_an_update(self):
        for route in ("/api/update-app", "/api/update-chromedriver"):
            with self.subTest(route=route):
                status, _ = self.request(route + "?offer_id=fixture&consent=true")
                self.assertEqual(status, 404)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()


    def test_successful_local_login_schedules_a_cached_check_only(self):
        with patch.object(panel_auth, "verify_password", return_value=True), \
                patch.object(panel_auth, "is_locked_out", return_value=False), \
                patch("crm.crm_log"):
            status, _ = self.request("/login", method="POST", authenticated=False,
                                     body={"username": "fixture_user", "password": "fixture-password",
                                           "mode": "standalone"})
        self.assertEqual(status, 200)
        self.check_call.assert_called_once_with(force=False)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()

    def test_successful_cloud_login_schedules_a_cached_check_only(self):
        import types
        user = "11111111-1111-4111-8111-111111111111"
        tenant = "22222222-2222-4222-8222-222222222222"
        client = Mock()
        client.login.return_value = {"user_id": user, "tenant_id": tenant,
                                     "user": {"id": user, "role": "admin"}}
        client.vault.has_device_credentials = True
        config = types.SimpleNamespace(api_base="https://cloud.example", tenant_id=tenant,
                                       sync_device_id="fixture-device", dataset_epoch=1, save=Mock())
        with contextlib.ExitStack() as patches:
            for target, name, value in (
                    (panel, "_cloud_runtime", {"client": client, "config": config}),
                    (panel_auth, "is_locked_out", False),
                    (panel.app_paths, "account_profile_key", "fixture-profile"),
                    (panel.app_paths, "account_profile_dir", str(self.root / "account")),
                    (panel.app_paths, "write_account_manifest", None),
                    (panel, "_cloud_apply_login_event", None),
                    (panel, "cloud_worker_start", None),
                    (panel, "cloud_worker_kick", None)):
                patches.enter_context(patch.object(target, name, return_value=value))
            status, _ = self.request("/login", method="POST", authenticated=False,
                                     body={"username": "fixture_user", "password": "fixture-password",
                                           "mode": "cloud"})
        self.assertEqual(status, 200)
        self.check_call.assert_called_once_with(force=False)
        self.app_call.assert_not_called()
        self.driver_call.assert_not_called()


class MaintenanceWiringTests(unittest.TestCase):
    def operation(self, name):
        result = getattr(panel, name, None)
        self.assertTrue(callable(result), "Missing maintenance integration: " + name)
        return result

    def test_source_factory_is_lazy_check_only_and_uses_the_verified_handoff(self):
        factory = self.operation("_maintenance_service")
        application, handoff, gate = Mock(), Mock(), Mock()
        application.status.return_value = {"state": "current"}
        handoff.eligible.return_value = False
        with patch.object(panel, "_MAINTENANCE_SERVICE", None, create=True), \
                patch("app_updater.AppUpdater", return_value=application) as constructor, \
                patch("installer_handoff.InstallerHandoff", return_value=handoff) as handoff_type, \
                patch("maintenance_gate.default_gate", return_value=gate), \
                patch.object(panel, "get_requests_proxies", return_value={}), \
                patch.object(panel, "check_system_updates") as check:
            service = factory()
            self.assertIs(service, factory())
            service.status()
        check.assert_not_called()
        application.check.assert_not_called()
        application.confirm.assert_not_called()
        self.assertIs(constructor.call_args.kwargs["installer"], handoff)
        self.assertIs(constructor.call_args.kwargs["installable"], False)
        self.assertEqual(constructor.call_args.kwargs["proxies"], {})
        self.assertIs(handoff_type.call_args.kwargs["gate"], gate)
        self.assertIs(handoff_type.call_args.kwargs["shutdown_callback"],
                      panel.prepare_application_update_shutdown)

    def test_application_proxy_policy_is_current_explicit_and_fail_closed(self):
        policy = self.operation("_maintenance_proxies")
        route = {"enabled": False, "proxy_url": ""}
        with patch.object(panel, "load_system_proxy_config", side_effect=lambda: dict(route)):
            self.assertEqual(policy(), {})
            route.update(enabled=True, proxy_url="http://127.0.0.1:18081")
            first = policy()
            self.assertEqual(first, {"http": route["proxy_url"], "https": route["proxy_url"]})
            route["proxy_url"] = "http://127.0.0.1:18082"
            self.assertEqual(policy()["https"], route["proxy_url"])
            self.assertEqual(first["https"], "http://127.0.0.1:18081")
            route["proxy_url"] = ""
            with self.assertRaises(ValueError):
                policy()

    def test_application_factory_passes_a_lazy_current_proxy_provider(self):
        application, handoff, gate = Mock(), Mock(), Mock()
        handoff.eligible.return_value = False
        with patch.object(panel, "_MAINTENANCE_SERVICE", None), \
                patch("app_updater.AppUpdater", return_value=application) as constructor, \
                patch("installer_handoff.InstallerHandoff", return_value=handoff), \
                patch("maintenance_gate.default_gate", return_value=gate), \
                patch.object(panel, "load_system_proxy_config") as config:
            panel._maintenance_service()
        self.assertIs(constructor.call_args.kwargs.get("proxy_provider"),
                      getattr(panel, "_maintenance_proxies", None))
        self.assertIsNotNone(constructor.call_args.kwargs.get("proxy_provider"))
        config.assert_not_called()

    def test_confirmed_driver_uses_only_the_locked_consent_entry_point(self):
        operation = self.operation("_install_confirmed_driver")
        for enabled in (False, True):
            with self.subTest(configured_proxy=enabled):
                config = {"enabled": enabled, "proxy_url": "http://fixture:fixture@127.0.0.1:18081"}
                with patch.object(panel, "load_system_proxy_config", return_value=config), \
                        patch("captcha_collector.update_chromedriver_now", return_value={"status": "updated"}) as install, \
                        patch("chromedriver_updater.update_chromedriver") as unguarded:
                    operation(consent=True, desired_version="154.0.1.2")
                install.assert_called_once_with(consent=True, desired_version="154.0.1.2",
                                                proxy_override=config["proxy_url"] if enabled else None,
                                                allow_direct=not enabled)
                unguarded.assert_not_called()

    def test_enabled_but_incomplete_proxy_never_falls_back_to_direct_install(self):
        operation = self.operation("_install_confirmed_driver")
        with patch.object(panel, "load_system_proxy_config", return_value={"enabled": True, "proxy_url": ""}), \
                patch("captcha_collector.update_chromedriver_now") as install:
            with self.assertRaises(ValueError):
                operation(consent=True, desired_version="154.0.1.2")
        install.assert_not_called()

    def test_driver_admission_stays_held_through_installation(self):
        operation = self.operation("_driver_install_admission")
        held = []
        gate = Mock()

        @contextlib.contextmanager
        def admitted():
            held.append(True)
            try:
                yield
            finally:
                held.pop()

        gate.admit.side_effect = admitted
        gate.running_jobs.side_effect = lambda **_: [] if held else self.fail("Inventory ran outside admission")
        with patch("maintenance_gate.default_gate", return_value=gate):
            with operation():
                self.assertTrue(held)
        self.assertFalse(held)

    def test_live_job_blocks_driver_changes(self):
        from maintenance_gate import MaintenanceBlocked
        operation = self.operation("_driver_install_admission")
        gate = Mock()
        gate.admit.return_value = contextlib.nullcontext()
        gate.running_jobs.return_value = [{"pid": 1234}]
        with patch("maintenance_gate.default_gate", return_value=gate):
            with self.assertRaises(MaintenanceBlocked):
                with operation():
                    self.fail("A driver update entered with an active job")


if __name__ == "__main__":
    unittest.main()
