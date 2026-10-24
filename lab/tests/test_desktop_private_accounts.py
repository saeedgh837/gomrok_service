# -*- coding: utf-8 -*-
"""Desktop client private-account integration tests (TDD RED first).

Verifies the desktop application requirements from the approved design:
1. One login in desktop app: customer signs in with username/password.
2. Dispatcher logic: cloud mode vs standalone mode; no fallback chain.
3. Cloud mode is online-only: server auth (/auth/login) creates local UI session;
   host unreachable fails closed without local fallback.
4. Successful /auth/login validates immutable user_id and tenant_id, binds tenant,
   self-enrols sync device, bootstraps account state, and creates session.
5. Account profile isolation: accounts/v1/<profile_key> namespace per account.
6. Logout cleanly tears down session and clears credentials.
7. Offline lease is retired for cloud accounts (online-only).
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_paths
import panel
import panel_auth
from cloud_sync.config import CloudConfig
from cloud_sync.errors import AuthRequiredError, UnreachableError, BadResponseError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _headers(message):
    out = dict(message)
    cookies = message.get_all("Set-Cookie")
    if cookies:
        out["_set_cookies"] = cookies
    return out


class DesktopPrivateAccountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig_data_dir = os.environ.get("IRICA_DATA_DIR")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["IRICA_DATA_DIR"] = self.tmp.name
        app_paths.reset_cache()

        # Seed local panel users
        self.users_path = os.path.join(self.tmp.name, "panel_users.json")
        self.seeded = panel_auth.seed_default_users(self.users_path)
        self.passwords = dict(self.seeded)

        # Basic profiles / stores
        self.profiles_path = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles_path, "w", encoding="utf-8") as fh:
            json.dump({"active_id": "", "profiles": []}, fh)

        self.inputs_path = os.path.join(self.tmp.name, "inputs.json")
        with open(self.inputs_path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)

        # Cloud config with loopback test api_base
        self.api_base = "http://127.0.0.1:8999"
        self.cloud_config_path = os.path.join(self.tmp.name, "cloud_config.json")
        self.cfg = CloudConfig(api_base=self.api_base, path=self.cloud_config_path)
        self.cfg.save()

        self._patches = [
            patch.object(panel_auth, "USERS_PATH", self.users_path),
            patch.object(panel, "PROFILES_PATH", self.profiles_path),
            patch.object(panel, "INPUTS_PATH", self.inputs_path),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles_path + ".lock"),
            patch.object(panel, "ESTELAM_PATH", os.path.join(self.tmp.name, "estelam_store.json")),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

        panel_auth.reset_sessions_for_tests()
        panel_auth.reset_throttle_for_tests()

        # Start loopback panel server
        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._server_thread.start()

        def _stop():
            self.httpd.shutdown()
            self.httpd.server_close()
            self._server_thread.join(timeout=5)
        self.addCleanup(_stop)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        panel.cloud_worker_stop()
        try:
            self.tmp.cleanup()
        except OSError:
            import shutil
            shutil.rmtree(self.tmp.name, ignore_errors=True)
        if self.orig_data_dir is not None:
            os.environ["IRICA_DATA_DIR"] = self.orig_data_dir
        else:
            os.environ.pop("IRICA_DATA_DIR", None)
        app_paths.reset_cache()

    def _op(self, path, data=None, headers=None, method=None):
        opener = urllib.request.build_opener(_NoRedirect)
        base_headers = {"Accept": "*/*"}
        base_headers.update(headers or {})
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        if payload is not None and "Content-Type" not in base_headers:
            base_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers=base_headers,
            method=method,
        )
        try:
            resp = opener.open(req, timeout=10)
            return resp.status, _headers(resp.headers), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, _headers(exc.headers), exc.read().decode("utf-8", "replace")

    def test_cloud_mode_login_success_enrols_device_and_activates_account(self):
        """Valid server credentials call /auth/login, bind IDs, enroll device, and return 200."""
        user_uuid = "11111111-2222-4333-8444-555555555555"
        tenant_uuid = "66666666-7777-4888-8999-aaaaaaaaaaaa"
        mock_login_payload = {
            "access_token": "valid-access-token",
            "refresh_token": "valid-refresh-token",
            "expires_in": 900,
            "must_change_password": False,
            "user": {"id": user_uuid, "username": "customer_acme", "role": "admin"},
            "user_id": user_uuid,
            "tenant_id": tenant_uuid,
        }
        mock_enroll_payload = {
            "sync_device_id": "device-uuid-1234",
            "device_refresh_token": "dev-refresh-token-5678",
        }
        mock_bootstrap_payload = {
            "dataset_epoch": 1,
            "cursor": 0,
            "records": [],
        }

        mock_client = MagicMock()
        mock_client.config = self.cfg
        mock_client.login.return_value = mock_login_payload
        mock_client.enroll.return_value = mock_enroll_payload
        mock_client.bootstrap.return_value = mock_bootstrap_payload
        mock_client.commit_bootstrap.return_value = 0
        mock_client.vault = MagicMock()
        mock_client.vault.has_device_credentials = False

        runtime = {
            "config": self.cfg,
            "client": mock_client,
            "machine": MagicMock(),
            "lease": MagicMock(),
            "outbox": MagicMock(),
        }

        with patch("panel._cloud_runtime", return_value=runtime), \
             patch("panel.cloud_worker_start") as mock_start:
            status, headers, body = self._op("/login", data={
                "username": "customer_acme",
                "password": "Password123!",
            })
            self.assertEqual(status, 200, f"Expected 200 but got {status}: {body}")
            parsed = json.loads(body)
            self.assertTrue(parsed.get("ok"))
            self.assertEqual(parsed.get("mode"), "cloud")
            self.assertEqual(parsed.get("tenant_id"), tenant_uuid)
            self.assertEqual(parsed.get("user_id"), user_uuid)

            # Assert server client login called with exact credentials
            mock_client.login.assert_called_once_with("customer_acme", "Password123!")

            # Assert self-enroll was invoked
            mock_client.enroll.assert_called_once()

            # Assert session was created and cookie issued
            cookies = headers.get("_set_cookies", [])
            self.assertTrue(any("irica_session=" in c for c in cookies))

            # Verify profile directory created under accounts/v1/
            profile_key = hashlib.sha256(
                f"{self.api_base}:{tenant_uuid}:{user_uuid}".encode("utf-8")
            ).hexdigest()
            account_dir = os.path.join(self.tmp.name, "accounts", "v1", profile_key)
            self.assertTrue(os.path.isdir(account_dir), f"Account dir {account_dir} not created")

            manifest_path = os.path.join(account_dir, "manifest.json")
            self.assertTrue(os.path.isfile(manifest_path))
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
            self.assertEqual(manifest["tenant_id"], tenant_uuid)
            self.assertEqual(manifest["user_id"], user_uuid)
            self.assertEqual(manifest["username"], "customer_acme")

    def test_cloud_mode_bad_credentials_fails_closed_no_local_fallback(self):
        """Server 401 returns 401, does not check panel_users.json, leaves session unauthenticated."""
        mock_client = MagicMock()
        mock_client.config = self.cfg
        mock_client.login.side_effect = AuthRequiredError(route="/auth/login", status=401)

        runtime = {"config": self.cfg, "client": mock_client}

        with patch("panel._cloud_runtime", return_value=runtime), \
             patch("panel_auth.verify_password") as mock_verify:
            status, headers, body = self._op("/login", data={
                "username": "saeed",
                "password": "WrongPassword",
            })
            self.assertEqual(status, 401)
            parsed = json.loads(body)
            self.assertFalse(parsed.get("ok"))
            self.assertNotIn("_set_cookies", headers)
            # Must NOT fall back to local password verification
            mock_verify.assert_not_called()

    def test_cloud_mode_unreachable_server_fails_closed_no_local_fallback(self):
        """When server is unreachable, cloud login returns 503/error and does NOT fall back."""
        mock_client = MagicMock()
        mock_client.config = self.cfg
        mock_client.login.side_effect = UnreachableError(route="/auth/login")

        runtime = {"config": self.cfg, "client": mock_client}

        # User "saeed" has a valid password in local panel_users.json
        local_pass = self.passwords["saeed"]

        with patch("panel._cloud_runtime", return_value=runtime), \
             patch("panel_auth.verify_password") as mock_verify:
            status, headers, body = self._op("/login", data={
                "username": "saeed",
                "password": local_pass,
            })
            # Must fail closed — NOT succeed via local password!
            self.assertIn(status, (502, 503, 401))
            parsed = json.loads(body)
            self.assertFalse(parsed.get("ok"))
            self.assertNotIn("_set_cookies", headers)
            mock_verify.assert_not_called()

    def test_cloud_mode_malformed_identity_fails_closed(self):
        """Server response missing tenant_id or user_id fails closed before profile activation."""
        mock_client = MagicMock()
        mock_client.config = self.cfg
        # Missing tenant_id and user_id
        mock_client.login.return_value = {
            "access_token": "token",
            "user": {"username": "cust"},
        }
        runtime = {"config": self.cfg, "client": mock_client}

        with patch("panel._cloud_runtime", return_value=runtime):
            status, headers, body = self._op("/login", data={
                "username": "cust",
                "password": "Password123!",
            })
            self.assertIn(status, (400, 401, 500, 502))
            parsed = json.loads(body)
            self.assertFalse(parsed.get("ok"))
            self.assertNotIn("_set_cookies", headers)

    def test_explicit_standalone_mode_authenticates_locally(self):
        """Explicit mode='standalone' checks panel_users.json and does not contact server."""
        mock_client = MagicMock()
        runtime = {"config": self.cfg, "client": mock_client}

        local_pass = self.passwords["saeed"]

        with patch("panel._cloud_runtime", return_value=runtime):
            status, headers, body = self._op("/login", data={
                "username": "saeed",
                "password": local_pass,
                "mode": "standalone",
            })
            self.assertEqual(status, 200, f"Expected 200: {body}")
            parsed = json.loads(body)
            self.assertTrue(parsed.get("ok"))
            self.assertEqual(parsed.get("mode"), "standalone")
            # Client.login must NOT have been called
            mock_client.login.assert_not_called()

            cookies = headers.get("_set_cookies", [])
            self.assertTrue(any("irica_session=" in c for c in cookies))

    def test_explicit_standalone_mode_wrong_password_fails(self):
        """Wrong password in standalone mode returns 401."""
        status, headers, body = self._op("/login", data={
            "username": "saeed",
            "password": "wrong-local-password",
            "mode": "standalone",
        })
        self.assertEqual(status, 401)
        parsed = json.loads(body)
        self.assertFalse(parsed.get("ok"))

    def test_logout_clears_session_and_server_tokens(self):
        """Logout destroys session cookie and invokes server logout."""
        # Create a cloud session first
        token = panel_auth.create_session("customer_acme", mode="cloud")
        csrf = panel_auth.session_csrf(token)

        mock_client = MagicMock()
        runtime = {"config": self.cfg, "client": mock_client}

        with patch("panel._cloud_runtime", return_value=runtime):
            status, headers, body = self._op(
                "/logout",
                data={},
                headers={
                    "Cookie": f"irica_session={token}",
                    "X-CSRF-Token": csrf,
                },
                method="POST",
            )
            # Should redirect to /login or return ok
            self.assertIn(status, (200, 302))
            # Session must be destroyed in memory
            self.assertIsNone(panel_auth.session_user(token))
            # Set-Cookie must expire the cookie
            cookies = headers.get("_set_cookies", [])
            self.assertTrue(any("Max-Age=0" in c for c in cookies))
            # Server logout was called
            mock_client.logout.assert_called_once()

    def test_account_profile_isolation_across_different_customers(self):
        """Account A and Account B get completely isolated namespaces and folders."""
        user_a = "aaaaaaaa-1111-4111-8111-111111111111"
        tenant_a = "aaaaaaaa-2222-4222-8222-222222222222"
        user_b = "bbbbbbbb-1111-4111-8111-111111111111"
        tenant_b = "bbbbbbbb-2222-4222-8222-222222222222"

        key_a = app_paths.account_profile_key(self.api_base, tenant_a, user_a)
        key_b = app_paths.account_profile_key(self.api_base, tenant_b, user_b)
        self.assertNotEqual(key_a, key_b)

        dir_a = app_paths.account_profile_dir(self.api_base, tenant_a, user_a, base_dir=self.tmp.name)
        dir_b = app_paths.account_profile_dir(self.api_base, tenant_b, user_b, base_dir=self.tmp.name)
        self.assertNotEqual(dir_a, dir_b)

        # Write A's manifest and a sample store
        app_paths.write_account_manifest(dir_a, api_base=self.api_base, tenant_id=tenant_a,
                                         user_id=user_a, username="customer_a")
        store_a = os.path.join(dir_a, "crm_store.json")
        with open(store_a, "w", encoding="utf-8") as fh:
            json.dump({"customers": [{"id": "c1", "name": "Client A"}]}, fh)

        # Write B's manifest
        app_paths.write_account_manifest(dir_b, api_base=self.api_base, tenant_id=tenant_b,
                                         user_id=user_b, username="customer_b")

        # B cannot see A's crm_store.json
        store_b = os.path.join(dir_b, "crm_store.json")
        self.assertFalse(os.path.exists(store_b))

        with open(os.path.join(dir_a, "manifest.json"), encoding="utf-8") as fh:
            m_a = json.load(fh)
        with open(os.path.join(dir_b, "manifest.json"), encoding="utf-8") as fh:
            m_b = json.load(fh)

        self.assertEqual(m_a["username"], "customer_a")
        self.assertEqual(m_b["username"], "customer_b")
        self.assertEqual(m_a["tenant_id"], tenant_a)
        self.assertEqual(m_b["tenant_id"], tenant_b)

    def test_offline_lease_retired_for_cloud_mode(self):
        """In cloud mode, an unauthenticated session cannot sync or write even if a lease file exists."""
        from cloud_sync.lease import OfflineLease, LeaseExpiredError

        # Create a lease that claims to have been granted right now
        lease_path = os.path.join(self.tmp.name, "cloud_sync_lease.json")
        lease = OfflineLease(path=lease_path)
        lease.grant()

        # In cloud mode, if the session has no active authenticated cloud actor,
        # offline lease does not authorize cloud data mutations
        panel._CLOUD_ACTOR = None
        mock_client = MagicMock()
        mock_client.vault.user_access_token = None
        mock_client.vault.has_device_credentials = False

        runtime = {
            "config": self.cfg,
            "client": mock_client,
            "lease": lease,
            "machine": MagicMock(),
            "outbox": MagicMock(),
        }

        with patch("panel._cloud_runtime", return_value=runtime):
            # Attempting cloud sync with no live session returns 401 or auth error
            status, headers, body = self._op("/cloud-sync", data={})
            self.assertIn(status, (401, 403, 409))


if __name__ == "__main__":
    unittest.main()
