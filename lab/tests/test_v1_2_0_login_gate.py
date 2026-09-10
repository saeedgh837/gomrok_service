"""v1.2.0 login gate — panel_auth unit tests (RED first)."""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import panel_auth  # noqa: E402
from unittest.mock import patch
import threading
import urllib.error
import urllib.request


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "panel_users.json")
        panel_auth.seed_default_users(self.path)

    def test_seed_creates_three_users(self):
        users = panel_auth.load_users(self.path)
        self.assertEqual(sorted(u["username"] for u in users),
                         ["arsalan", "hasan", "saeed"])

    def test_seed_file_has_no_plaintext_passwords(self):
        with open(self.path, encoding="utf-8") as fh:
            raw = fh.read()
        for secret in ("Sgh@1994@", "8080", "Aa1111"):
            self.assertNotIn(secret, raw)

    def test_seed_file_format(self):
        users = panel_auth.load_users(self.path)
        for u in users:
            self.assertIn("salt", u)
            self.assertIn("hash", u)
            self.assertIn("iters", u)
            int(u["salt"], 16)
            int(u["hash"], 16)

    def test_seed_is_idempotent(self):
        before = panel_auth.load_users(self.path)
        panel_auth.seed_default_users(self.path)
        after = panel_auth.load_users(self.path)
        self.assertEqual(before, after)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "panel_users.json")
        panel_auth.seed_default_users(self.path)

    def test_correct_passwords_verify(self):
        for user, pw in (("saeed", "Sgh@1994@"), ("hasan", "8080"),
                         ("arsalan", "Aa1111")):
            self.assertTrue(panel_auth.verify_password(self.path, user, pw),
                            user)

    def test_wrong_password_fails(self):
        self.assertFalse(panel_auth.verify_password(self.path, "saeed", "wrong"))

    def test_unknown_user_fails_without_exception(self):
        self.assertFalse(panel_auth.verify_password(self.path, "ghost", "x"))

    def test_unknown_user_timing_close_to_known(self):
        # uniform timing: hash a dummy hash for unknown users
        t0 = time.perf_counter()
        panel_auth.verify_password(self.path, "saeed", "wrong")
        t1 = time.perf_counter()
        panel_auth.verify_password(self.path, "ghost", "wrong")
        t2 = time.perf_counter()
        known, unknown = t1 - t0, t2 - t1
        self.assertGreater(unknown, known * 0.3, "unknown-user path too fast")
        self.assertLess(unknown, known * 3 + 0.05, "unknown-user path too slow")


class SessionTests(unittest.TestCase):
    def setUp(self):
        panel_auth.reset_sessions_for_tests()

    def test_login_creates_valid_session(self):
        token = panel_auth.create_session("saeed")
        self.assertTrue(panel_auth.session_user(token) == "saeed")

    def test_token_entropy(self):
        tokens = {panel_auth.create_session("saeed") for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        for t in tokens:
            self.assertGreaterEqual(len(t), 32)

    def test_logout_kills_session(self):
        token = panel_auth.create_session("saeed")
        panel_auth.destroy_session(token)
        self.assertIsNone(panel_auth.session_user(token))

    def test_garbage_token_safe(self):
        self.assertIsNone(panel_auth.session_user(""))
        self.assertIsNone(panel_auth.session_user(None))
        self.assertIsNone(panel_auth.session_user("x" * 500))

    def test_expiry(self):
        token = panel_auth.create_session("saeed", ttl_seconds=-1)
        self.assertIsNone(panel_auth.session_user(token))

    def test_validate_username_shape(self):
        self.assertTrue(panel_auth.valid_username("saeed"))
        self.assertFalse(panel_auth.valid_username("a"))
        self.assertFalse(panel_auth.valid_username("bad name!"))
        self.assertFalse(panel_auth.valid_username("<script>"))


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        panel_auth.reset_throttle_for_tests()

    def test_lockout_after_five_fails(self):
        for _ in range(5):
            panel_auth.register_failed_login("saeed")
        self.assertTrue(panel_auth.is_locked_out("saeed"))
        self.assertFalse(panel_auth.is_locked_out("hasan"))

    def test_lockout_expires(self):
        for _ in range(5):
            panel_auth.register_failed_login("saeed")
        panel_auth._THROTTLE["saeed"]["until"] = time.time() - 1
        self.assertFalse(panel_auth.is_locked_out("saeed"))

    def test_success_resets(self):
        for _ in range(4):
            panel_auth.register_failed_login("saeed")
        panel_auth.register_success("saeed")
        self.assertFalse(panel_auth.is_locked_out("saeed"))


class _GateHarness(unittest.TestCase):
    """Boots the real panel.Handler on an ephemeral port with a throwaway
    users file; the harness helper logs in and carries the cookie."""

    def setUp(self):
        import panel
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        users_path = os.path.join(self.tmp.name, "panel_users.json")
        panel_auth.seed_default_users(users_path)
        profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(profiles, "w", encoding="utf-8") as fh:
            json.dump({"active_id": "", "profiles": []}, fh)
        self._patches = [
            patch.object(panel_auth, "USERS_PATH", users_path),
            patch.object(panel, "PROFILES_PATH", profiles),
            patch.object(panel, "INPUTS_PATH", os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", os.path.join(self.tmp.name, "estelam_store.json")),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        panel_auth.reset_sessions_for_tests()
        panel_auth.reset_throttle_for_tests()
        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._server_thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self._server_thread.start()

        def _stop():
            self.httpd.shutdown()
            self.httpd.server_close()
            self._server_thread.join(timeout=5)
        self.addCleanup(_stop)
        self.base = f"http://127.0.0.1:{self.port}"

    # -- helpers -------------------------------------------------------
    def _op(self, path, data=None, headers=None, method=None, follow=False):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers or {}, method=method)
        try:
            resp = opener.open(req, timeout=10)
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")

    def login(self, username="saeed", password="Sgh@1994@"):
        """Returns the Set-Cookie header value from a successful login."""
        status, headers, body = self._op("/login", data={
            "username": username, "password": password})
        self.assertEqual(status, 200, body)
        return headers.get("Set-Cookie", "")

    def authed(self, path, cookie, data=None):
        return self._op(path, data=data, headers={"Cookie": cookie})


class GateTests(_GateHarness):
    def test_root_redirects_to_login_when_unauthenticated(self):
        status, headers, body = self._op("/")
        self.assertEqual(status, 302)
        self.assertTrue(headers.get("Location", "").startswith("/login"))

    def test_login_page_served_unauthenticated(self):
        status, headers, body = self._op("/login")
        self.assertEqual(status, 200)
        self.assertIn("ورود", body)

    def test_favicon_whitelisted(self):
        status, _h, _b = self._op("/favicon.ico")
        self.assertEqual(status, 200)

    def test_font_whitelisted(self):
        status, _h, _b = self._op("/fonts/Vazirmatn-var.woff2")
        self.assertEqual(status, 200)

    def test_api_unauthenticated_gets_401_json(self):
        status, headers, body = self._op("/state")
        self.assertEqual(status, 401)
        self.assertIn("ok", json.loads(body))

    def test_shutdown_is_gated(self):
        status, _h, _b = self._op("/shutdown", data={})
        self.assertIn(status, (302, 401))

    def test_wrong_password_gets_generic_error(self):
        status, _h, body = self._op("/login", data={
            "username": "saeed", "password": "nope"})
        self.assertEqual(status, 401)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"],
                         ["نام کاربری یا رمز عبور اشتباه است"])

    def test_unknown_user_same_generic_error(self):
        status, _h, body = self._op("/login", data={
            "username": "ghost", "password": "nope"})
        self.assertEqual(status, 401)
        self.assertIn("اشتباه است", body)

    def test_lockout_returns_429(self):
        for _ in range(5):
            self._op("/login", data={"username": "hasan", "password": "bad"})
        status, _h, body = self._op("/login", data={
            "username": "hasan", "password": "8080"})
        self.assertEqual(status, 429)


class AuthenticatedFlowTests(_GateHarness):
    def test_login_sets_helthy_cookie(self):
        cookie = self.login()
        self.assertIn("irica_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_authed_request_passes_gate(self):
        cookie = self.login()
        status, _h, _b = self.authed("/state", cookie)
        self.assertEqual(status, 200)

    def test_logout_kills_session_server_side(self):
        cookie = self.login()
        status, _h, _b = self.authed("/logout", cookie, data={})
        self.assertEqual(status, 302)
        status, _h, _b = self.authed("/state", cookie)
        self.assertEqual(status, 401)

    def test_forged_cookie_rejected(self):
        status, _h, _b = self.authed("/state", "irica_session=forged")
        self.assertEqual(status, 401)


class TelegramActorTests(unittest.TestCase):
    """telegram_notify prepends the acting user on every message."""

    def test_actor_prefix_from_param(self):
        import telegram_notify as tg
        self.assertEqual(
            tg.actor_prefix("saeed"), "<b>کاربر: saeed</b>\n")

    def test_actor_prefix_escapes_html(self):
        import telegram_notify as tg
        self.assertEqual(
            tg.actor_prefix('<b>x</b>'), "<b>کاربر: &lt;b&gt;x&lt;/b&gt;</b>\n")

    def test_env_fallback(self):
        import telegram_notify as tg
        with patch.dict(os.environ, {"IRICA_ACTOR": "hasan"}):
            self.assertIn("hasan", tg.actor_prefix(None))

    def test_system_fallback(self):
        import telegram_notify as tg
        with patch.dict(os.environ, {"IRICA_ACTOR": ""}):
            self.assertIn("سیستم", tg.actor_prefix(None))


class ActorEnvTests(unittest.TestCase):
    """Robots inherit IRICA_ACTOR so their telegram sends attribute correctly."""

    def test_robot_child_env_sets_actor(self):
        import panel
        env = panel._robot_child_env("saeed")
        self.assertEqual(env["IRICA_ACTOR"], "saeed")

    def test_robot_child_env_defaults_to_system(self):
        import panel
        env = panel._robot_child_env(None)
        self.assertEqual(env["IRICA_ACTOR"], "سیستم")


class ReportActorTests(unittest.TestCase):
    """The report sender must pass the acting user explicitly."""

    def test_report_send_passes_actor(self):
        import panel
        src = open(panel.__file__, encoding="utf-8").read()
        self.assertIn("tg.send_message(chunk, timeout=20, actor=self.actor)",
                      src)


class ReleaseHygieneTests(unittest.TestCase):
    """panel_users.json (hashes only) lives in the DATA dir, never the bundle."""

    def test_verify_forbids_panel_users_json_in_bundle(self):
        v = open(os.path.join(ROOT, "lab", "scripts",
                              "verify_windows_release.py"),
                 encoding="utf-8").read()
        self.assertIn('"panel_users.json"', v)

    def test_users_file_lands_outside_the_repo(self):
        import app_paths
        self.assertFalse(panel_auth.USERS_PATH.startswith(ROOT))


if __name__ == "__main__":
    unittest.main()

    unittest.main()
