"""Security hardening for the panel gate (cloud-sync spec §10).

Four things are pinned here:

1. Nothing in the shipped source can ever be used as a password — the install
   generates its own, once, and the file holds hashes only.
2. Every state-changing request is checked against the panel's own origin and
   host, and carries the session's CSRF token in a header.
3. The session cookie is HttpOnly + SameSite=Strict; its CSRF companion is
   Strict but deliberately readable by the page.
4. The two CRM read-modify-write sites in panel.py run under crm.STORE_LOCK.

Everything is offline and stays inside a temp directory: no network, no
%LOCALAPPDATA%\\irica.
"""
import ast
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import app_paths  # noqa: E402
import panel_auth  # noqa: E402

# The shipped iteration count costs ~0.25 s per hash, which makes a scan over
# every source literal take minutes. WHICH strings are accepted depends only on
# the stored hash, never on the cost of producing it, so the scan below runs
# with a deliberately tiny count. The shipped default is never touched outside
# this `with`.
_FAST_ITERS = 1000

#: A credential could only be hidden in the two modules that own login.
_SOURCES = ("panel_auth.py", "panel.py")

#: How long a probe waits for a lock it must NOT be able to take. On the
#: passing path the lock really is held, so the acquire cannot succeed and the
#: wait always runs to completion — the timeout costs time, never correctness,
#: and nothing anywhere sleeps.
_PROBE_TIMEOUT = 1.0


def _source_text(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def _string_literals(name):
    """Every short string literal in a module: a published credential would be
    one of these. Newlines are excluded because no password is multi-line and
    docstrings dominate the count."""
    tree = ast.parse(_source_text(name), filename=name)
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and 0 < len(node.value) <= 64 and "\n" not in node.value}


_CREDENTIAL_NAME = re.compile(r"(?i)pass(word|wd)?|pwd|secret|credential")


def _credential_constants(name):
    """Module-level constants that LOOK like they hold a credential.

    The old panel_auth shipped `DEFAULT_USERS = [("saeed", "..."), ...]` — a
    name that says password and a value that is one. Both shapes are caught:
    a string under a credential-shaped name, and a table of string pairs.
    """
    tree = ast.parse(_source_text(name), filename=name)
    found = []

    def is_str(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    def is_pair_table(node):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            return False
        return all(isinstance(elt, (ast.List, ast.Tuple))
                   and len(elt.elts) == 2 and all(is_str(e) for e in elt.elts)
                   for elt in node.elts)

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        shaped = any(_CREDENTIAL_NAME.search(n) for n in names)
        if (shaped and is_str(node.value)) or is_pair_table(node.value):
            found.append("%s: %s" % (", ".join(names) or "?",
                                     ast.dump(node.value)[:120]))
    return found


class NoPublishedCredentialTests(unittest.TestCase):
    """No reusable password may exist anywhere in the shipped source."""

    def test_no_credential_shaped_constant(self):
        offenders = []
        for name in _SOURCES:
            for entry in _credential_constants(name):
                offenders.append("%s -> %s" % (name, entry))
        self.assertEqual(offenders, [])

    def test_the_default_users_are_usernames_only(self):
        """The replacement for the old password table carries no secret."""
        self.assertEqual(sorted(panel_auth.DEFAULT_USERNAMES),
                         ["arsalan", "hasan", "saeed"])
        for username in panel_auth.DEFAULT_USERNAMES:
            self.assertTrue(panel_auth.valid_username(username), username)

    def test_no_source_literal_is_accepted_as_a_password(self):
        """Behavioural, not just structural: build an install, then try every
        string literal the auth path could carry as a credential.

        Covers both routes a constant could take — the stored hash of a real
        account, and the uniform-timing dummy the unknown-user path compares
        against (which used to be a literal in this very file).
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "panel_users.json")
        records = []
        for username in panel_auth.DEFAULT_USERNAMES:
            salt = secrets.token_hex(16)
            records.append({
                "username": username,
                "salt": salt,
                "hash": panel_auth._hash_password(
                    panel_auth.generate_password(), salt, _FAST_ITERS),
                "iters": _FAST_ITERS,
                "must_change_password": True,
            })
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(records, handle)

        literals = set()
        for name in _SOURCES:
            literals |= _string_literals(name)
        self.assertTrue(literals)

        with patch.object(panel_auth, "PBKDF2_ITERATIONS", _FAST_ITERS):
            for literal in sorted(literals):
                for record in records:
                    digest = panel_auth._hash_password(
                        literal, record["salt"], _FAST_ITERS)
                    self.assertNotEqual(
                        digest, record["hash"],
                        "a source literal verifies for %s: %r"
                        % (record["username"], literal))
                self.assertFalse(
                    panel_auth.verify_password(path, "ghostuser", literal),
                    "a source constant authenticates a nonexistent account: %r"
                    % literal)


class GeneratedPasswordTests(unittest.TestCase):
    """The seeded password is per-install, random and never written down."""

    def _seed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "panel_users.json")
        return path, panel_auth.seed_default_users(path)

    def test_passwords_are_unique_across_installs(self):
        _path1, first = self._seed()
        _path2, second = self._seed()
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        generated = [pw for _n, pw in first + second]
        self.assertEqual(len(set(generated)), 6,
                         "a per-install password must never repeat")

    def test_password_is_never_persisted_anywhere(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "panel_users.json")
        seeded = panel_auth.seed_default_users(path)
        passwords = [pw for _n, pw in seeded]
        # Only the hashed store exists: no log, no temp file, no notice file.
        self.assertEqual(sorted(os.listdir(tmp.name)), ["panel_users.json"])
        for name in os.listdir(tmp.name):
            with open(os.path.join(tmp.name, name), "rb") as handle:
                raw = handle.read()
            for password in passwords:
                self.assertNotIn(password.encode("utf-8"), raw, name)
        # ...and nothing in the source tree holds one either.
        for module in _SOURCES:
            self.assertNotIn("".join(passwords), _source_text(module))

    def test_seeded_accounts_are_flagged_for_a_password_change(self):
        path, seeded = self._seed()
        self.assertIsNot(seeded, False)
        users = panel_auth.load_users(path)
        self.assertEqual(len(users), 3)
        for user in users:
            self.assertIs(user.get("must_change_password"), True)

    def test_an_existing_users_file_is_untouched(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "panel_users.json")
        panel_auth.seed_default_users(path)
        with open(path, "rb") as handle:
            before = handle.read()
        self.assertIs(panel_auth.seed_default_users(path), False)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)


class MigrationKeepsAccountsTests(unittest.TestCase):
    """A legacy-install migration must carry the operator's accounts over."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(app_paths.reset_cache)
        self.legacy = os.path.join(self.tmp.name, "irica-old-win64")
        self.data = os.path.join(self.tmp.name, "data")
        os.makedirs(self.legacy)
        # panel_users.json alone decides whether a folder "looks like an
        # install": without it in MIGRATED_FILES the accounts were dropped.
        self.payload = [{"username": "saeed", "salt": "aabb",
                         "hash": "ccdd", "iters": 390000,
                         "must_change_password": False}]
        with open(os.path.join(self.legacy, "panel_users.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(self.payload, handle)

    def test_panel_users_json_is_in_the_migration_list(self):
        self.assertIn("panel_users.json", app_paths.MIGRATED_FILES)

    def test_migration_carries_the_hashes_over_verbatim(self):
        moved = app_paths.migrate_legacy(self.legacy, self.data)
        self.assertIn("panel_users.json", moved)
        with open(os.path.join(self.data, "panel_users.json"),
                  encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), self.payload)

    def test_migration_never_replaces_existing_accounts(self):
        os.makedirs(self.data)
        mine = [{"username": "saeed", "salt": "1111", "hash": "2222",
                 "iters": 390000}]
        with open(os.path.join(self.data, "panel_users.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(mine, handle)
        moved = app_paths.migrate_legacy(self.legacy, self.data)
        self.assertNotIn("panel_users.json", moved)
        with open(os.path.join(self.data, "panel_users.json"),
                  encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), mine)


# --------------------------------------------------------------------------
# HTTP: origin / host / CSRF
# --------------------------------------------------------------------------
from test_v1_2_0_login_gate import _GateHarness  # noqa: E402


class RequestAuthenticityTests(_GateHarness):
    """A browser POST from anywhere but this panel must not land."""

    def _own_origin(self):
        return "http://127.0.0.1:%d" % self.port

    # -- host ----------------------------------------------------------
    def test_bad_host_is_rejected(self):
        status, _h, body = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"])

    def test_host_is_rejected_before_the_body_is_routed(self):
        """A rebound name must never reach a route, whatever it asks for."""
        cookie = self.login()
        status, _h, _b = self._op(
            "/shutdown", data={}, headers={
                "Host": "attacker.example", "Cookie": cookie})
        self.assertEqual(status, 403)

    def test_loopback_host_is_accepted(self):
        status, _h, _b = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Host": "127.0.0.1:%d" % self.port})
        # 401 = the host passed and the (wrong) credentials were judged.
        self.assertEqual(status, 401)

    def test_localhost_alias_is_accepted(self):
        """`localhost` can only ever mean this machine, so it is as local as
        HOST itself; an attacker's rebound domain can be neither."""
        status, _h, _b = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 401)

    # -- origin / referer ----------------------------------------------
    def test_cross_origin_post_is_rejected(self):
        status, _h, body = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIsInstance(payload["errors"], list)
        self.assertTrue(payload["errors"][0])

    def test_same_origin_post_is_accepted(self):
        status, _h, body = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Origin": self._own_origin()})
        # The origin passed; the credentials are simply wrong.
        self.assertEqual(status, 401)
        self.assertIn("اشتباه", body)

    def test_cross_origin_post_cannot_end_a_session(self):
        cookie = self.login()
        status, _h, body = self._op(
            "/logout", data={},
            headers={"Origin": "http://evil.example", "Cookie": cookie,
                     "X-CSRF-Token": panel_auth.session_csrf(
                         self._session_token(cookie)) or ""})
        self.assertEqual(status, 403)
        self.assertIn("منبع نامعتبر", body)
        # The session survived: the forged request changed nothing.
        self.assertEqual(self.authed("/state", cookie)[0], 200)

    def test_referer_is_checked_when_origin_is_absent(self):
        status, _h, _b = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Referer": "http://evil.example/attack.html"})
        self.assertEqual(status, 403)

    def test_same_origin_referer_is_accepted(self):
        status, _h, _b = self._op(
            "/login", data={"username": "x", "password": "y"},
            headers={"Referer": self._own_origin() + "/site-1"})
        self.assertEqual(status, 401)

    # -- CSRF ----------------------------------------------------------
    @staticmethod
    def _session_token(cookie):
        if "irica_session=" not in cookie:
            return ""
        return cookie.split("irica_session=", 1)[1].split(";", 1)[0]

    def test_post_without_the_csrf_header_is_rejected(self):
        cookie = self.login()
        token = self._session_token(cookie)
        self.assertTrue(panel_auth.session_csrf(token))
        status, _h, body = self._op(
            "/logout", data={}, headers={"Cookie": cookie,
                                         "Origin": self._own_origin()})
        self.assertEqual(status, 403)
        self.assertFalse(json.loads(body)["ok"])
        # Still signed in: the route never ran.
        self.assertEqual(self.authed("/state", cookie)[0], 200)

    def test_post_with_a_wrong_csrf_header_is_rejected(self):
        cookie = self.login()
        status, _h, _b = self._op(
            "/logout", data={}, headers={
                "Cookie": cookie, "Origin": self._own_origin(),
                "X-CSRF-Token": "not-the-token"})
        self.assertEqual(status, 403)
        self.assertEqual(self.authed("/state", cookie)[0], 200)

    def test_post_with_the_csrf_header_is_accepted(self):
        cookie = self.login()
        status, _h, _b = self.authed("/logout", cookie, data={})
        self.assertEqual(status, 302)
        self.assertEqual(self.authed("/state", cookie)[0], 401)

    def test_get_never_needs_the_csrf_header(self):
        cookie = self.login()
        status, _h, _b = self._op("/state", headers={"Cookie": cookie})
        self.assertEqual(status, 200)

    def test_an_unauthenticated_post_is_still_answered_with_401(self):
        """The token check is scoped to live sessions; a stranger still gets
        the ordinary gate answer rather than a CSRF error."""
        status, _h, _b = self._op("/shutdown", data={})
        self.assertIn(status, (401, 302))


class SessionCookieTests(_GateHarness):
    def test_session_cookie_is_httponly_and_strict(self):
        self.login()
        by_name = {c.split("=", 1)[0]: c for c in self.set_cookies}
        session = by_name["irica_session"]
        self.assertIn("HttpOnly", session)
        self.assertIn("SameSite=Strict", session)
        self.assertNotIn("SameSite=Lax", session)

    def test_csrf_cookie_is_strict_but_readable_by_the_page(self):
        self.login()
        by_name = {c.split("=", 1)[0]: c for c in self.set_cookies}
        companion = by_name["irica_csrf"]
        self.assertIn("SameSite=Strict", companion)
        self.assertNotIn("HttpOnly", companion)

    def test_served_pages_carry_the_csrf_client(self):
        cookie = self.login()
        status, _h, body = self._op("/site-1", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn('X-CSRF-Token', body)
        self.assertIn("irica_csrf", body)

    def test_the_injected_client_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed on this machine")
        script = panel_csrf_bootstrap()
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(script)
            path = handle.name
        try:
            proc = subprocess.run([node, "--check", path],
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr[:800])
        self.assertEqual(proc.stderr, "")


def panel_csrf_bootstrap():
    """The injected script body, minus its (typed) tags."""
    import panel
    tag = panel.CSRF_BOOTSTRAP
    start = tag.index(">", tag.index("<script")) + 1
    return tag[start:tag.rindex("</script>")]


# --------------------------------------------------------------------------
# The two unlocked read-modify-write sites
# --------------------------------------------------------------------------
class StoreLockTests(unittest.TestCase):
    """Both CRM read-modify-write windows must hold crm.STORE_LOCK.

    Deterministic interleaving, no sleeps: the site under test is parked at its
    crm.store_write — after its read, before its write, i.e. exactly inside the
    window — and a second thread then tries to take the lock. While the window
    is closed that acquire cannot succeed; when it is open it walks straight
    in and the test fails. The wait only ever costs time on the passing path.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = os.path.join(self.tmp.name, "crm_store.json")
        self._old_env = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = self.store
        self.addCleanup(self._restore_env)
        import panel
        self.panel = panel
        self._estelam = patch.object(
            panel, "ESTELAM_PATH", os.path.join(self.tmp.name, "estelam.json"))
        self._estelam.start()
        self.addCleanup(self._estelam.stop)
        import crm
        self.crm = crm
        crm.reset_cache()

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self._old_env
        self.crm.reset_cache()

    def _write_store(self, payload):
        with open(self.store, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    def _assert_window_is_locked(self, run_site):
        """Park `run_site` at its store_write and probe the lock from a second
        thread. Returns nothing; raises on a window that is not closed."""
        crm = self.crm
        armed = threading.Event()
        parked = threading.Event()
        probe_done = threading.Event()
        outcome = {"attempted": False, "acquired": False}
        real_write = crm.store_write

        def parking_write(data):
            if armed.is_set() and not parked.is_set():
                parked.set()
                probe_done.wait(30.0)
            return real_write(data)

        def probe():
            if not parked.wait(30.0):
                probe_done.set()
                return
            outcome["attempted"] = True
            got = crm.STORE_LOCK.acquire(timeout=_PROBE_TIMEOUT)
            outcome["acquired"] = bool(got)
            if got:
                # A real, competing mutation — exactly what the unlocked site
                # would have silently overwritten.
                data = crm.store_read()
                data["probe_marker"] = 1
                crm.store_write(data)
                crm.STORE_LOCK.release()
            probe_done.set()

        crm.store_write = parking_write
        thread = threading.Thread(target=probe, daemon=True)
        thread.start()
        armed.set()
        try:
            run_site()
        finally:
            armed.clear()
            parked.set()
            probe_done.set()
            crm.store_write = real_write
        thread.join(timeout=30.0)

        self.assertTrue(outcome["attempted"],
                        "the probe never reached the site's store_write")
        self.assertFalse(
            outcome["acquired"],
            "another writer entered the read-modify-write window: the site "
            "did not hold crm.STORE_LOCK")
        # ...and the probe itself is capable of taking the lock once the
        # window is over, so "blocked" cannot be a broken probe.
        self.assertTrue(crm.STORE_LOCK.acquire(timeout=_PROBE_TIMEOUT))
        crm.STORE_LOCK.release()

    def test_inquiry_history_reset_holds_the_lock(self):
        """panel.py _crm_inquiry_reset: store_read -> mutate -> store_write."""
        self._write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [{"id": "dev-1", "customer_id": "c1",
                         "stage": "inquiry", "last_inquiry_at": "yesterday"}],
            "periods": [], "settings": {},
        })

        replies = []

        class _Stub:
            actor = "tester"

            def _json(self, obj, code=200):
                replies.append((obj, code))

        def run_site():
            self.panel.Handler._crm_inquiry_reset(
                _Stub(), {"device_ids": ["dev-1"], "restart": False})

        self._assert_window_is_locked(run_site)
        self.assertEqual(replies[-1][0]["ok"], True, replies)
        device = self.crm.store_read()["devices"][0]
        self.assertEqual(device["last_inquiry_at"], "")

    def test_update_settings_holds_the_lock(self):
        """panel.py crm_update_settings: store_read -> validate -> store_write."""
        self._write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [], "periods": [], "settings": {},
        })

        def run_site():
            settings, errors = self.panel.crm_update_settings(
                {"watch_interval_hours": 6})
            self.assertEqual(errors, [])

        self._assert_window_is_locked(run_site)
        self.assertEqual(
            self.crm.store_read()["settings"]["watch_interval_hours"], 6)

    def test_the_lock_is_reentrant_for_the_nested_store_calls(self):
        """crm.store_read/store_write take the same RLock; wrapping must not
        deadlock with them."""
        self._write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [], "periods": [], "settings": {},
        })
        settings, errors = self.panel.crm_update_settings(
            {"scheduler_enabled": True})
        self.assertEqual(errors, [])
        self.assertTrue(settings["scheduler_enabled"])


if __name__ == "__main__":
    unittest.main()
