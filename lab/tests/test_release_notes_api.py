import contextlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_paths
import panel
import panel_auth
import release_notes


class ReleaseNotesApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="irica-release-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.start_patch(app_paths, "data_dir", return_value=str(self.root))
        self.start_patch(panel, "app_version_text", return_value="1.20.0")
        self.start_patch(panel, "load_store", side_effect=AssertionError("Business store must not be loaded"))
        self.start_patch(panel, "maintenance_status", side_effect=AssertionError("Maintenance must not run"))
        self.start_patch(panel, "check_services_health", side_effect=AssertionError("Health check must not run"))
        panel_auth.reset_sessions_for_tests()
        self.addCleanup(panel_auth.reset_sessions_for_tests)
        self.token = panel_auth.create_session("fixture_user")

        class QuietHandler(panel.Handler):
            def log_message(self, *_):
                pass

        self.server = panel.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def start_patch(self, target, name, **kwargs):
        operation = patch.object(target, name, **kwargs)
        result = operation.start()
        self.addCleanup(operation.stop)
        return result

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, route="/api/release-notes", *, method="GET", payload=None,
                token=True, csrf=True, headers=None):
        token = self.token if token is True else token
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            request_headers["Cookie"] = "irica_session=" + token
            if csrf:
                request_headers["X-CSRF-Token"] = panel_auth.session_csrf(token) or ""
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        try:
            connection.request(method, route, body=json.dumps(payload) if payload is not None else None,
                               headers=request_headers)
            response = connection.getresponse()
            raw = response.read().decode("utf-8")
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            return response.status, body, dict(response.getheaders())
        finally:
            connection.close()

    def notice(self):
        status, body, _ = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        return {"version": body["notes"]["version"], "notice_id": body["notice_id"]}

    def ack(self, payload=None, **kwargs):
        return self.request("/api/release-notes/seen", method="POST", payload=payload, **kwargs)

    def test_summary_reads_without_loading_business_or_maintenance_state(self):
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertFalse(body["seen"])
        self.assertEqual(body["notes"]["version"], "1.20.0")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_acknowledgment_persists_and_responses_are_not_cached(self):
        payload = self.notice()
        status, body, headers = self.ack(payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["seen"])
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertTrue(self.request()[1]["seen"])
        self.assertEqual(self.ack(payload)[0], 200)

    def test_both_routes_require_a_live_session(self):
        for token in (False, "invalid-fixture-session"):
            self.assertEqual(self.request(token=token)[0], 401)
            self.assertEqual(self.ack({}, token=token)[0], 401)
        panel_auth.destroy_session(self.token)
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.ack({})[0], 401)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_acknowledgment_requires_csrf_same_origin_and_loopback_host(self):
        payload = self.notice()
        cases = ({"csrf": False}, {"headers": {"Origin": "https://outside.example"}},
                 {"headers": {"Host": "outside.example"}})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.ack(payload, **kwargs)[0], 403)
        self.assertFalse(self.request()[1]["seen"])

    def test_old_tab_cannot_acknowledge_a_different_logged_in_account(self):
        payload = self.notice()
        other = panel_auth.create_session("other_user")
        self.assertEqual(self.ack(payload, token=other)[0], 409)
        self.assertFalse(self.request(token=other)[1]["seen"])
        self.assertFalse(self.request()[1]["seen"])

    def test_malformed_or_stale_payload_never_writes_a_marker(self):
        payload = self.notice()
        invalid = ([], {}, {"version": "1.20.0"}, {**payload, "version": "1.19.0"},
                   {**payload, "subject": "other_user"}, {**payload, "notice_id": "نادرست"},
                   {**payload, "notice_id": "x" * 2000})
        for body in invalid:
            with self.subTest(body=body):
                self.assertIn(self.ack(body)[0], (400, 409))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_query_strings_do_not_bypass_routing_or_account_binding(self):
        status, body, _ = self.request("/api/release-notes?user=other_user")
        self.assertEqual(status, 200)
        payload = {"version": body["notes"]["version"], "notice_id": body["notice_id"]}
        status, _, _ = self.request("/api/release-notes/seen?user=other_user", method="POST", payload=payload)
        self.assertEqual(status, 200)
        self.assertTrue(self.request()[1]["seen"])

    def test_write_failure_is_nonfatal_sanitized_and_remains_unseen(self):
        payload = self.notice()
        with patch.object(release_notes.tempfile, "mkstemp", side_effect=PermissionError("fixture-private-path")):
            status, body, _ = self.ack(payload)
        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])
        self.assertNotIn("fixture-private-path", json.dumps(body))
        self.assertFalse(self.request()[1]["seen"])

    def test_unexpected_release_error_does_not_log_account_or_path_details(self):
        captured = io.StringIO()
        with patch.object(release_notes, "snapshot", side_effect=RuntimeError("fixture-private-identity")):
            with contextlib.redirect_stderr(captured):
                status, body, _ = self.request()
        self.assertEqual(status, 503)
        self.assertNotIn("fixture-private-identity", json.dumps(body) + captured.getvalue())

    def test_no_curated_content_does_not_fall_back_to_a_latest_release(self):
        with patch.object(panel, "app_version_text", return_value="1.19.0"):
            status, body, _ = self.request()
            self.assertEqual(status, 200)
            self.assertIsNone(body["notes"])
            self.assertEqual(self.ack({"version": "1.20.0", "notice_id": "x" * 64})[0], 409)


if __name__ == "__main__":
    unittest.main()
