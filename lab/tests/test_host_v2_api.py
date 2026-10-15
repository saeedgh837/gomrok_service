"""Phase B Task 7 — the exact `/v2` HTTP contract, proved over loopback only.

Runs as `--suite api` through lab/scripts/run_host_v2_tests.py. This suite:

  * never spawns a server (rulings 43/44) — the fixture front must already be
    listening, and a refused connection is a PREREQUISITE failure (exit 2),
    never a pass and never a skip;
  * talks ONLY to the loopback fixture origin `IRICA_V2_ORIGIN`
    (default `http://127.0.0.1:8766`), rejecting any other target outright;
  * disables ambient proxies and refuses every redirect, so a credential can
    never be replayed to another host;
  * keeps tokens and passwords in transient request memory only — they are
    never printed and never written into an assertion message.

The pure PHP cases (transport gate, `base_path` collision, `Codes::ALL`
reconciliation, version-path classification) need no socket at all; the
`create-account.php` cases drive the real CLI against the disposable fixture
database and purge every row they create.

Engine note: MariaDB 10.3.39 is the only provisioned engine. MySQL 5.7 is
BLOCKED and UNRUN — no MySQL support, compatibility or PASS is claimed.
"""

from __future__ import annotations

import binascii
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "host" / "src"
FIXTURE_FRONT = ROOT / "lab" / "fixtures" / "host_v2_front.php"
V2_API = SRC / "V2Api.php"
CREATE_ACCOUNT = ROOT / "host" / "bin" / "create-account.php"

DEFAULT_ORIGIN = "http://127.0.0.1:8766"
ORIGIN = os.environ.get("IRICA_V2_ORIGIN") or DEFAULT_ORIGIN

MAX_BODY_BYTES = 16384
MAX_RESPONSE_BYTES = 65536
LOGIN_PREFIX = "v2api."

V2_ROUTES = {
    "/v2/auth/login": ["POST"],
    "/v2/auth/me": ["GET"],
    "/v2/auth/logout": ["POST"],
    "/v2/auth/change-password": ["POST"],
    "/v2/workspaces": ["GET"],
}

# The complete set of texts this API is allowed to send. Anything else leaks.
GENERIC_MESSAGES = {
    "login or password is incorrect",
    "the session credential is not valid for this request",
    "this session was issued for a different purpose",
    "the password must be changed before this request can be served",
    "request content-type must be application/json",
    "request body is empty or malformed",
    "request body is not valid JSON",
    "request body must be a JSON object",
    "request body exceeds the configured limit",
    "this API is reachable only over HTTPS",
    "the fixture adapter serves an exact loopback peer only",
    "the configured legacy base_path collides with the /v2 routes",
    "no such endpoint",
    "this endpoint does not accept the request method",
    "a session credential is required for this endpoint",
    "the page size must be between 1 and 100",
    "the page cursor must be identified by a UUID",
    "the request body carries a field this endpoint does not accept",
    "login and password are required",
    "old_password and new_password are required",
    "password is invalid",
}

KNOWN_CODES = {
    "BAD_PAYLOAD", "HTTP_404", "HTTP_405", "HTTP_413", "HTTP_415",
    "PAYLOAD_TOO_LARGE", "AUTH_REQUIRED", "AUTH_EXPIRED", "AUTH_REVOKED",
    "PASSWORD_CHANGE_REQUIRED", "SESSION_PURPOSE_MISMATCH", "RATE_LIMITED",
    "VALIDATION_FAILED", "HTTPS_REQUIRED", "CONFIG_INVALID", "DB_UNAVAILABLE",
    "SERVER_ERROR",
}


class FixtureUnavailable(Exception):
    """A PREREQUISITE failure: the loopback fixture front is not listening."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise ValueError("FIXTURE_REDIRECT_REJECTED")


def _assert_target(origin, path):
    """The only approved target: exact loopback fixture origin, bounded path."""
    if (origin != DEFAULT_ORIGIN or not path.startswith("/")
            or path.startswith("//") or len(path) > 2048
            or any(ord(c) < 32 for c in path)):
        raise ValueError("FIXTURE_HTTP_TARGET_REJECTED")


def request_json(origin, path, method="GET", body=None, token=None):
    """One request to the fixture front. Raises FixtureUnavailable on refusal."""
    _assert_target(origin, path)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(origin + path, data=payload,
                                     headers=headers, method=method)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect())
    try:
        resp = opener.open(request, timeout=5)
    except urllib.error.HTTPError as err:
        resp = err
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as err:
        raise FixtureUnavailable(type(err).__name__) from None
    with resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("FIXTURE_RESPONSE_TOO_LARGE")
        return resp.status, resp.headers, json.loads(raw)


def raw_request(origin, path, method="GET", headers=None, body=None):
    """Send an exact request line over a socket: no client-side normalisation.

    `urllib` rewrites some targets (`..`, percent-encoding), so the path
    confusion and size-boundary cases control the bytes on the wire themselves.
    """
    _assert_target(origin, path)
    host, port = "127.0.0.1", 8766
    lines = ["%s %s HTTP/1.1" % (method, path), "Host: %s:%d" % (host, port),
             "Connection: close"]
    for name, value in (headers or {}).items():
        lines.append("%s: %s" % (name, value))
    wire = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
    if body is not None:
        wire += body
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as err:
        raise FixtureUnavailable(type(err).__name__) from None
    try:
        sock.sendall(wire)
        chunks, total = [], 0
        while total <= MAX_RESPONSE_BYTES:
            chunk = sock.recv(65536)
            if not chunk:
                break
            total += len(chunk)
            chunks.append(chunk)
    finally:
        sock.close()
    raw = b"".join(chunks)
    if total > MAX_RESPONSE_BYTES:
        raise ValueError("FIXTURE_RESPONSE_TOO_LARGE")
    head, _, payload = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
    match = re.match(r"\AHTTP/\d\.\d (\d{3})", status_line)
    names = []
    for line in head.split(b"\r\n")[1:]:
        name = line.partition(b":")[0].decode("ascii", "replace").lower()
        if name:
            names.append(name)
    document = None
    if payload.strip():
        try:
            document = json.loads(payload.decode("utf-8"))
        except ValueError:
            document = None
    return (int(match.group(1)) if match else 0), names, document


def _php():
    binary = shutil.which("php")
    if binary is None:
        raise AssertionError("FIXTURE_PHP_RUNTIME_MISSING")
    return binary


def _child_env(extra=None):
    env = {name: os.environ[name] for name in
           ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
    env.update(extra or {})
    return env


def php_run(args, env, timeout=90, stdin=b""):
    return subprocess.run([_php(), "-d", "display_errors=0", "-d", "log_errors=0"]
                          + list(args), cwd=ROOT, env=env, timeout=timeout,
                          input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)


def php_pure(code, timeout=60):
    """Execute a pure PHP snippet with product classes loaded, no database."""
    handle, path = tempfile.mkstemp(suffix=".php", prefix="irica-v2-pure-")
    os.close(handle)
    try:
        Path(path).write_text("<?php\ndeclare(strict_types=1);\n" + code,
                              encoding="utf-8")
        return php_run([path], _child_env(), timeout)
    finally:
        os.unlink(path)


def _product_boot():
    """The require-list every pure snippet shares; mirrors index.php's set."""
    return ("foreach (['Schema','Database','Http','Ids','Log','Tokens','Roles',"
            "'RateLimit','Auth','Accounts','Workspaces','AccountSessions'] as $f)"
            " { require_once %s . '/host/src/' . $f . '.php'; }" % _quote(str(ROOT)))


def _quote(value):
    return json.dumps(value)


def _require_fixture_front():
    """Ruling 44: a refused loopback connection is exit 2, never a skip.

    A front that ANSWERS with something unusable is not a missing prerequisite:
    that is a behavioural failure the cases report individually, so it must not
    be collapsed into a prerequisite exit.
    """
    try:
        request_json(ORIGIN, "/v2/auth/me")
    except FixtureUnavailable:
        print("FIXTURE_PREREQUISITE_FAILED")
        raise SystemExit(2) from None
    except Exception:  # noqa: BLE001 - answered, badly: the cases will report it
        pass


_require_fixture_front()


class ApiFixture(unittest.TestCase):
    """Every HTTP case shares one synthetic account set, purged on teardown."""

    logins = {}
    passwords = {}
    tokens = {}

    # ------------------------------------------------------------------
    # class lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def setUpClass(cls):
        cls.origin = ORIGIN
        config_path = os.environ.get("IRICA_B_FIXTURE_CONFIG")
        if not config_path:
            raise AssertionError("FIXTURE_CONFIG_MISSING")
        cls._prepare(config_path)
        cls._helper_run("state-snapshot", str(cls._state_snapshot))
        cls._create_accounts()

    @classmethod
    def _prepare(cls, config_path):
        with Path(config_path).open("r", encoding="utf-8") as source:
            fixture = json.load(source)
        runtime = fixture["runtime"]
        cls._log_path = Path(tempfile.gettempdir()) / "irica-v2-fixture-access.log"
        secret = binascii.hexlify(os.urandom(32)).decode("ascii")
        handle, config_file = tempfile.mkstemp(suffix=".php",
                                               prefix="irica-v2-host-config-")
        os.close(handle)
        Path(config_file).write_text(
            "<?php\nreturn ['db' => ['host' => %s, 'port' => %d, 'dbname' => %s,"
            " 'user' => %s, 'password' => %s], 'jwt_secret' => %s];\n"
            % (_quote(runtime["host"]), int(runtime["port"]),
               _quote(runtime["dbname"]), _quote(runtime["user"]),
               _quote(runtime["password"]), _quote(secret)), encoding="utf-8")
        cls._config_path = config_file
        cls._workdir = Path(tempfile.mkdtemp(prefix="irica-v2-helper-"))
        cls._state_snapshot = cls._workdir / "identity-state.json"
        cls._reseed_snapshot = cls._workdir / "reseed-state.json"
        helper = cls._workdir / "helper.php"
        helper.write_text(HELPER_PHP.replace("__BOOTSTRAP__",
                                             _quote(str(ROOT / "lab" / "fixtures" / "host_v2" / "bootstrap.php"))),
                          encoding="utf-8")
        cls._helper = helper
        cls._env = _child_env({"IRICA_B_FIXTURE_CONFIG": config_path})

    @classmethod
    def tearDownClass(cls):
        problems = []
        try:
            if getattr(cls, "_reseed_snapshot", None) and cls._reseed_snapshot.is_file():
                cls._helper_run("state-restore", str(cls._reseed_snapshot))
        except Exception as error:  # noqa: BLE001 - teardown must keep going
            problems.append(error)
        try:
            if getattr(cls, "_state_snapshot", None) and cls._state_snapshot.is_file():
                cls._helper_run("state-restore", str(cls._state_snapshot))
        except Exception as error:  # noqa: BLE001
            problems.append(error)
        try:
            cls._helper_run("purge", LOGIN_PREFIX)
        except Exception as error:  # noqa: BLE001
            problems.append(error)
        for path in (getattr(cls, "_config_path", None),):
            if path and Path(path).is_file():
                try:
                    os.unlink(path)
                except OSError as error:
                    problems.append(error)
        workdir = getattr(cls, "_workdir", None)
        if workdir and workdir.is_dir():
            shutil.rmtree(workdir, ignore_errors=True)
        if problems:
            raise AssertionError("FIXTURE_TEARDOWN_FAILED")

    @classmethod
    def _helper_run(cls, *args):
        result = php_run([str(cls._helper), *args], cls._env)
        if result.returncode != 0:
            raise AssertionError("FIXTURE_HELPER_FAILED")
        return result.stdout.decode("utf-8").strip()

    # ------------------------------------------------------------------
    # synthetic fixtures
    # ------------------------------------------------------------------

    @staticmethod
    def _login_for(tag):
        return LOGIN_PREFIX + tag + "." + binascii.hexlify(os.urandom(5)).decode()

    @staticmethod
    def _password():
        return "v2api-Passw0rd-" + binascii.hexlify(os.urandom(10)).decode()

    @classmethod
    def _create_accounts(cls):
        for tag in ("alpha", "bravo", "charlie", "delta", "disabled", "forced"):
            cls.logins[tag] = cls._login_for(tag)
            password = cls._password()
            cls.passwords[tag] = password
            cls._create_member(cls.logins[tag], password)
        # `forced` deliberately keeps must_change_password = 1.
        for tag in ("alpha", "bravo", "charlie", "delta"):
            cls._clear_forced_change(tag)
        cls._helper_run("disable", cls.logins["disabled"])

    @classmethod
    def _create_member(cls, login, password):
        result = cls._cli({"kind": "member", "login": login, "password": password})
        if result.returncode != 0:
            raise AssertionError("FIXTURE_CLI_FAILED")
        receipt = json.loads(result.stdout.decode("utf-8"))
        if set(receipt) != {"account_id", "workspace_id", "must_change_password"}:
            raise AssertionError("FIXTURE_CLI_RECEIPT_SHAPE")
        if receipt["must_change_password"] is not True:
            raise AssertionError("FIXTURE_CLI_FORCED_CHANGE_DEFAULT")
        if login in result.stdout.decode("utf-8") or password in result.stdout.decode("utf-8"):
            raise AssertionError("FIXTURE_CLI_RECEIPT_LEAK")
        return receipt

    @classmethod
    def _cli(cls, payload, argv=()):
        return php_run([str(CREATE_ACCOUNT), *argv],
                       _child_env({"IRICA_B_FIXTURE_CONFIG":
                                   cls._env["IRICA_B_FIXTURE_CONFIG"],
                                   "IRICA_HOST_CONFIG": cls._config_path}),
                       stdin=json.dumps(payload).encode("utf-8"))

    @classmethod
    def _clear_forced_change(cls, tag):
        """A synthetic password change through the product, not raw SQL."""
        password = cls.passwords[tag]
        status, _headers, login = request_json(
            cls.origin, "/v2/auth/login", "POST",
            {"login": cls.logins[tag], "password": password})
        if status != 200:
            raise AssertionError("FIXTURE_SETUP_LOGIN_FAILED")
        rotated = password + "-rot"
        status, _headers, _body = request_json(
            cls.origin, "/v2/auth/change-password", "POST",
            {"old_password": password, "new_password": rotated},
            login["token"])
        if status != 200:
            raise AssertionError("FIXTURE_SETUP_PASSWORD_CHANGE_FAILED")
        cls.passwords[tag] = rotated

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def login(self, tag, password=None):
        key = self.logins[tag]
        status, _headers, body = request_json(
            self.origin, "/v2/auth/login", "POST",
            {"login": key, "password": password or self.passwords[tag]})
        if status != 200:
            raise AssertionError("FIXTURE_SETUP_LOGIN_FAILED")
        return body["token"]

    def call(self, path, method="GET", body=None, token=None):
        status, headers, document = request_json(self.origin, path, method,
                                                 body, token)
        self.assert_envelope(status, headers, document)
        return status, headers, document

    def assert_envelope(self, status, headers, document, check_headers=True):
        self.assertIsInstance(document, dict, "every v2 answer is a JSON object")
        self.assertIn("ok", document, "every v2 answer carries ok")
        if document["ok"]:
            self.assertEqual(200, status)
        else:
            error = document.get("error")
            self.assertIsInstance(error, dict, "a failure carries error")
            self.assertEqual({"code", "message", "request_id"}, set(error))
            self.assertIn(error["code"], KNOWN_CODES,
                          "an error code must be one of the declared Codes")
            self.assertRegex(error["code"], r"\A[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\Z")
            self.assertLessEqual(len(error["code"]), 40)
            self.assertIn(error["message"], GENERIC_MESSAGES,
                          "an error message must be one of the fixed texts")
        if not check_headers:
            return
        self.assertEqual("no-store", headers.get("Cache-Control"))
        self.assertIsNone(headers.get("Set-Cookie"), "v2 never sets a cookie")
        self.assertIsNone(headers.get("set-cookie"))
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"),
                          "v2 keeps CORS closed")
        self.assertIsNone(headers.get("Access-Control-Allow-Credentials"))

    def assert_code(self, expected, status, document):
        self.assertFalse(document["ok"])
        self.assertEqual(expected, document["error"]["code"])

    # ------------------------------------------------------------------
    # path classification and routing
    # ------------------------------------------------------------------

    def test_a01_unknown_v2_route_is_404_on_every_verb(self):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            status, _headers, body = self.call("/v2/auth/refresh", method)
            self.assertEqual(404, status, "/v2/auth/refresh must never exist")
            self.assert_code("HTTP_404", status, body)

    def test_a02_known_wrong_methods_are_405(self):
        for path, verbs in V2_ROUTES.items():
            for method in ("GET", "POST", "PUT", "DELETE"):
                if method in verbs:
                    continue
                status, _headers, body = self.call(path, method)
                self.assertEqual(405, status, path + " " + method)
                self.assert_code("HTTP_405", status, body)

    def test_a03_version_path_confusion_is_never_served(self):
        # An authority-form target is refused by the client itself: it would
        # make `Host` mean something other than the approved fixture origin.
        with self.assertRaises(ValueError) as refused:
            raw_request(self.origin, "//v2/auth/login")
        self.assertEqual("FIXTURE_HTTP_TARGET_REJECTED", str(refused.exception))
        for target in ("/v2/../v1", "/v2%2Fauth%2Flogin", "/v2//auth/login",
                       "/V2/auth/me", "/v2/", "/v2%zz", "/v2%2e%2e/v1",
                       "/v2x", "/v2x/auth/login", "/v20/auth/login",
                       "/v1/auth/me", "/v2%00/auth/me"):
            status, _names, body = raw_request(self.origin, target)
            self.assertEqual(404, status, target + " must be refused")
            if isinstance(body, dict) and body:
                self.assert_envelope(status, {}, body, check_headers=False)

    def test_a04_known_v2_routes_survive_a_raw_request_line(self):
        status, names, body = raw_request(self.origin, "/v2/auth/me")
        self.assertEqual(401, status)
        self.assertIn("cache-control", names)
        self.assert_code("AUTH_REQUIRED", status, body)
        self.assertNotIn("set-cookie", names)
        self.assertNotIn("access-control-allow-origin", names)

    # ------------------------------------------------------------------
    # body, media-type and credential gates
    # ------------------------------------------------------------------

    def test_b01_empty_malformed_and_non_object_bodies_are_400(self):
        status, _names, body = raw_request(
            self.origin, "/v2/auth/login", "POST",
            {"Content-Type": "application/json"})
        self.assertEqual(400, status)
        self.assert_code("BAD_PAYLOAD", status, body)
        for payload in (b"not-json", b"[]", b'"text"', b"7"):
            status, _names, body = raw_request(
                self.origin, "/v2/auth/login", "POST",
                {"Content-Type": "application/json",
                 "Content-Length": str(len(payload))}, payload)
            self.assertEqual(400, status, payload)
            self.assert_code("BAD_PAYLOAD", status, body)

    def test_b02_non_json_content_type_is_415(self):
        raw = b'{"login":"nobody","password":"xxxxxxxxxxxx"}'
        status, _names, body = raw_request(
            self.origin, "/v2/auth/login", "POST",
            {"Content-Type": "text/plain", "Content-Length": str(len(raw))},
            raw)
        self.assertEqual(415, status)
        self.assert_code("UNSUPPORTED_MEDIA_TYPE", status, body)

    def test_b03_unknown_login_fields_are_rejected(self):
        for extra in ({"purpose": "admin_web"}, {"role": "operator"},
                      {"workspace": "x"}, {"tenant_id": "x"},
                      {"platform_role": "operator"}, {"token": "x" * 64}):
            document = {"login": "nobody", "password": "x" * 12}
            document.update(extra)
            status, _headers, body = self.call("/v2/auth/login", "POST", document)
            self.assertEqual(400, status, str(sorted(extra)))
            self.assert_code("BAD_PAYLOAD", status, body)

    def test_b04_unknown_change_password_fields_are_rejected(self):
        token = self.login("charlie")
        for extra in ({"purpose": "desktop"}, {"token": "x" * 64},
                      {"security_version": 2}):
            document = {"old_password": "x" * 12, "new_password": "y" * 12}
            document.update(extra)
            status, _headers, body = self.call("/v2/auth/change-password",
                                               "POST", document, token)
            self.assertEqual(400, status, str(sorted(extra)))
            self.assert_code("BAD_PAYLOAD", status, body)

    def test_b05_missing_bearer_is_auth_required(self):
        for path, method in (("/v2/auth/me", "GET"),
                             ("/v2/auth/logout", "POST"),
                             ("/v2/auth/change-password", "POST"),
                             ("/v2/workspaces", "GET")):
            status, _headers, body = self.call(path, method, {})
            self.assertEqual(401, status, path)
            self.assert_code("AUTH_REQUIRED", status, body)

    def test_b06_malformed_and_legacy_credentials_are_auth_required(self):
        for credential in ("not-a-token", "x" * 63, "x" * 65, "A" * 64,
                           "eyJhbGciOiJIUzI1NiJ9.e30.signature", "abc123"):
            status, _headers, body = self.call("/v2/auth/me", token=credential)
            self.assertEqual(401, status, "a non-v2 credential shape")
            self.assert_code("AUTH_REQUIRED", status, body)

    def test_b07_the_body_cap_is_exactly_16_kib(self):
        def padded(size):
            filler = size - len(b'{"login":"nobody","padding":""}')
            return b'{"login":"nobody","padding":"' + b"x" * filler + b'"}'

        inside = padded(MAX_BODY_BYTES)
        self.assertEqual(MAX_BODY_BYTES, len(inside))
        status, _names, body = raw_request(
            self.origin, "/v2/auth/login", "POST",
            {"Content-Type": "application/json",
             "Content-Length": str(len(inside))}, inside)
        self.assertNotEqual(413, status, "16 KiB exactly is still accepted")
        self.assertFalse(body["ok"])

        over = padded(MAX_BODY_BYTES + 1)
        self.assertEqual(MAX_BODY_BYTES + 1, len(over))
        status, _names, body = raw_request(
            self.origin, "/v2/auth/login", "POST",
            {"Content-Type": "application/json",
             "Content-Length": str(len(over))}, over)
        self.assertEqual(413, status, "the v2 cap is 16 KiB, not the legacy 16 MiB")
        self.assert_code("PAYLOAD_TOO_LARGE", status, body)

    # ------------------------------------------------------------------
    # the five routes
    # ------------------------------------------------------------------

    def test_c01_login_returns_a_token_and_a_minimal_receipt(self):
        status, _headers, body = self.call(
            "/v2/auth/login", "POST",
            {"login": self.logins["alpha"], "password": self.passwords["alpha"]})
        self.assertEqual(200, status)
        self.assertEqual({"ok", "token", "expires_at", "idle_expires_at",
                          "account"}, set(body))
        self.assertEqual({"id", "display_name", "platform_role",
                          "must_change_password"}, set(body["account"]))
        self.assertRegex(body["token"], r"\A[0-9a-f]{64}\Z")
        self.assertIs(body["account"]["must_change_password"], False)

    def test_c02_login_failure_is_generic(self):
        status, _headers, body = self.call(
            "/v2/auth/login", "POST",
            {"login": self.logins["alpha"], "password": self._password()})
        self.assertEqual(401, status)
        self.assert_code("AUTH_REQUIRED", status, body)
        self.assertEqual("login or password is incorrect",
                         body["error"]["message"])

    def test_c03_disabled_member_cannot_log_in(self):
        status, _headers, body = self.call(
            "/v2/auth/login", "POST",
            {"login": self.logins["disabled"],
             "password": self.passwords["disabled"]})
        self.assertEqual(401, status)
        self.assert_code("AUTH_REQUIRED", status, body)

    def test_c04_disabled_member_loses_an_existing_session(self):
        token = self.login("delta")
        status, _headers, _body = self.call("/v2/auth/me", token=token)
        self.assertEqual(200, status)
        self._helper_run("disable", self.logins["delta"])
        try:
            status, _headers, body = self.call("/v2/auth/me", token=token)
            self.assertEqual(401, status)
            self.assert_code("AUTH_REVOKED", status, body)
        finally:
            self._helper_run("enable", self.logins["delta"])

    def test_c05_me_is_passive_and_never_renews_idle(self):
        token = self.login("bravo")
        before = self._helper_run("session-window", self.logins["bravo"])
        for _ in range(3):
            status, _headers, body = self.call("/v2/auth/me", token=token)
            self.assertEqual(200, status)
            self.assertEqual({"ok", "account"}, set(body))
        after = self._helper_run("session-window", self.logins["bravo"])
        self.assertEqual(before, after, "me must never move the idle window")

    def test_c06_forced_change_blocks_workspaces_and_allows_self_service(self):
        token = self.login("forced")
        status, _headers, body = self.call("/v2/auth/me", token=token)
        self.assertEqual(200, status)
        self.assertIs(body["account"]["must_change_password"], True)
        status, _headers, body = self.call("/v2/workspaces", token=token)
        self.assertEqual(403, status)
        self.assert_code("PASSWORD_CHANGE_REQUIRED", status, body)
        old = self.passwords["forced"]
        new = old + "-rot"
        status, _headers, body = self.call(
            "/v2/auth/change-password", "POST",
            {"old_password": old, "new_password": new}, token)
        self.assertEqual(200, status)
        self.assertEqual({"ok", "reauthentication_required"}, set(body))
        self.assertIs(body["reauthentication_required"], True)
        self.passwords["forced"] = new
        status, _headers, body = self.call("/v2/auth/me", token=token)
        self.assertEqual(401, status, "the change invalidates every session")
        self.assert_code("AUTH_REVOKED", status, body)

    def test_c07_logout_revokes_exactly_one_session_and_never_echoes_it(self):
        token = self.login("bravo")
        sibling = self.login("bravo")
        status, _headers, body = self.call("/v2/auth/logout", "POST", {}, token)
        self.assertEqual(200, status)
        self.assertEqual({"ok", "revoked", "already_revoked"}, set(body))
        self.assertNotIn("token", json.dumps(body))
        status, _headers, body = self.call("/v2/auth/logout", "POST", {}, token)
        self.assertEqual(200, status)
        self.assertIs(body["already_revoked"], True)
        status, _headers, _body = self.call("/v2/auth/me", token=token)
        self.assertEqual(401, status)
        status, _headers, _body = self.call("/v2/auth/me", token=sibling)
        self.assertEqual(200, status, "the sibling session must survive")

    def test_c08_wrong_purpose_answers_session_purpose_mismatch(self):
        desktop = self.login("charlie")
        wrong = self._helper_run("issue", self.logins["charlie"], "admin_web")
        status, _headers, body = self.call("/v2/auth/me", token=wrong)
        self.assertEqual(401, status)
        self.assert_code("SESSION_PURPOSE_MISMATCH", status, body)
        for path, method, payload in (
                ("/v2/workspaces", "GET", None),
                ("/v2/auth/logout", "POST", {}),
                ("/v2/auth/change-password", "POST",
                 {"old_password": "x" * 12, "new_password": "y" * 12})):
            status, _headers, body = self.call(path, method, payload, wrong)
            self.assertEqual(401, status, path)
            self.assert_code("SESSION_PURPOSE_MISMATCH", status, body)
        status, _headers, _body = self.call("/v2/auth/me", token=desktop)
        self.assertEqual(200, status, "the desktop session must be unaffected")

    def test_c09_cross_tenant_selection_is_impossible(self):
        mine_token = self.login("alpha")
        theirs_token = self.login("bravo")
        status, _h, mine = self.call("/v2/workspaces", token=mine_token)
        self.assertEqual(200, status)
        status, _h, theirs = self.call("/v2/workspaces", token=theirs_token)
        self.assertEqual(200, status)
        mine_ids = {item["id"] for item in mine["items"]}
        their_ids = {item["id"] for item in theirs["items"]}
        self.assertEqual(set(), mine_ids & their_ids,
                         "two private workspaces must never overlap")
        foreign = sorted(their_ids or mine_ids)
        for cursor in foreign:
            status, _h, page = self.call(
                "/v2/workspaces?cursor=" + cursor, token=mine_token)
            self.assertEqual(200, status)
            for item in page["items"]:
                self.assertNotIn(item["id"], their_ids,
                                 "a foreign cursor must never reveal a tenant")

    def test_c10_workspaces_pages_with_an_opaque_cursor(self):
        token = self.login("delta")
        status, _h, body = self.call("/v2/workspaces?limit=1", token=token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(body["items"]))
        self.assertEqual({"id", "slug", "name", "context_version", "role"},
                         set(body["items"][0]))
        self.assertIsInstance(body["items"][0]["context_version"], int)
        cursor = body.get("next_cursor")
        if cursor is not None:
            status, _h, page = self.call(
                "/v2/workspaces?limit=1&cursor=" + cursor, token=token)
            self.assertEqual(200, status)
            self.assertNotEqual(body["items"][0]["id"], page["items"][0]["id"])

    def test_c11_workspaces_rejects_a_page_size_above_100(self):
        token = self.login("delta")
        for value in ("101", "1000", "0", "abc", "-1"):
            status, _headers, body = self.call(
                "/v2/workspaces?limit=" + value, token=token)
            self.assertEqual(400, status, value)
            self.assert_code("BAD_PAYLOAD", status, body)

    def test_c12_operator_only_services_are_unreachable_over_http(self):
        token = self.login("alpha")
        for path in ("/v2/accounts", "/v2/admin/accounts", "/v2/accounts/create",
                     "/v2/operators", "/v2/admin", "/v2/workspaces/assign",
                     "/v2/workspaces/freeze", "/v2/migrate", "/v2/setup"):
            for method in ("GET", "POST"):
                status, _headers, body = self.call(path, method,
                                                   {} if method == "POST" else None,
                                                   token)
                self.assertEqual(404, status, path + " " + method)
                self.assert_code("HTTP_404", status, body)
        source = V2_API.read_text(encoding="utf-8")
        for forbidden in ("seedFirstOperator", "linkLegacy", "setPlatformRole",
                          "createPrivateWithin", "->assign(", "->remove(",
                          "->freeze(", "run_database_backup", "setStatus"):
            self.assertNotIn(forbidden, source,
                             "no operator service may be reachable from /v2")

    def test_c13_every_v2_response_is_no_store_with_closed_cors(self):
        token = self.login("alpha")
        for path, method, payload, credential in (
                ("/v2/auth/login", "POST",
                 {"login": self.logins["alpha"],
                  "password": self.passwords["alpha"]}, None),
                ("/v2/auth/me", "GET", None, token),
                ("/v2/auth/logout", "POST", {}, token),
                ("/v2/auth/change-password", "POST",
                 {"old_password": "x" * 12, "new_password": "y" * 12}, token),
                ("/v2/workspaces", "GET", None, token),
                ("/v2/auth/refresh", "POST", {}, None)):
            status, headers, document = request_json(self.origin, path, method,
                                                     payload, credential)
            self.assert_envelope(status, headers, document)

    def test_c14_no_credential_reaches_the_log(self):
        marker = "v2api-Leak-Probe-" + binascii.hexlify(os.urandom(8)).decode()
        if self._log_path.is_file():
            self._log_path.unlink()
        token = self.login("bravo")
        self.call("/v2/workspaces?limit=1&cursor=not-a-cursor", token=token)
        status, _h, _body = self.call(
            "/v2/auth/login", "POST", {"login": self.logins["bravo"],
                                       "password": marker})
        self.assertEqual(401, status)
        self.call("/v2/auth/me", token=token)
        text = (self._log_path.read_text(encoding="utf-8", errors="replace")
                if self._log_path.is_file() else "")
        self.assertNotEqual("", text, "the fixture front must write a log line")
        for secret in (token, marker, self.logins["bravo"],
                       self.passwords["bravo"], "not-a-cursor",
                       self.logins["bravo"].split(".")[-1]):
            self.assertNotIn(secret, text)
        for line in text.splitlines():
            record = json.loads(line)
            route = record.get("route", "")
            self.assertNotIn("?", route, "a query string must never be logged")
            self.assertNotIn("Authorization", json.dumps(record))
            self.assertLessEqual(len(route), 64)

    # ------------------------------------------------------------------
    # the private create-account CLI
    # ------------------------------------------------------------------

    def test_e01_create_account_is_cli_only_and_refuses_argv(self):
        result = php_run([str(CREATE_ACCOUNT), "--login=x"],
                         _child_env({"IRICA_HOST_CONFIG": self._config_path}),
                         stdin=b"{}")
        self.assertEqual(2, result.returncode)
        self.assertNotIn(b"login", result.stderr)

    def test_e02_create_account_rejects_unknown_fields(self):
        login = self._login_for("unknown-field")
        password = self._password()
        result = self._cli({"kind": "member", "login": login,
                            "password": password, "role": "operator"})
        self.assertEqual(1, result.returncode)
        text = result.stdout.decode("utf-8")
        self.assertEqual("", text.strip())
        self.assertNotIn(login, text)
        self.assertNotIn(password, text)

    def test_e03_create_account_rejects_an_oversized_stdin(self):
        result = self._cli({"kind": "member", "login": self._login_for("huge"),
                            "password": self._password(),
                            "padding": "z" * (MAX_BODY_BYTES + 64)})
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout.decode("utf-8").strip())

    def test_e04_create_account_emits_ids_and_the_forced_change_flag_only(self):
        login = self._login_for("receipt")
        password = self._password()
        result = self._cli({"kind": "member", "login": login,
                            "password": password})
        self.assertEqual(0, result.returncode,
                         result.stderr.decode("utf-8", "replace"))
        text = result.stdout.decode("utf-8")
        receipt = json.loads(text)
        self.assertEqual({"account_id", "workspace_id",
                          "must_change_password"}, set(receipt))
        self.assertRegex(receipt["account_id"], r"\A[0-9a-f]{8}-[0-9a-f-]{27}\Z")
        self.assertRegex(receipt["workspace_id"], r"\A[0-9a-f]{8}-[0-9a-f-]{27}\Z")
        self.assertIs(receipt["must_change_password"], True)
        self.assertNotIn(login, text)
        self.assertNotIn(password, text)

    def test_e05_missing_identity_state_row_is_an_actionable_cli_error(self):
        """Ruling 55, re-graded: the CLI must diagnose, never stack-trace."""
        self._helper_run("state-delete", str(self._reseed_snapshot))
        try:
            self.assertEqual("0", self._helper_run("state-present"),
                             "the fixture must have removed the row")
            result = self._cli({"kind": "first_operator",
                                "login": self._login_for("no-state"),
                                "password": self._password()})
        finally:
            self._helper_run("state-restore", str(self._reseed_snapshot))
        self.assertEqual("1", self._helper_run("state-present"),
                         "the fixture must restore the row")
        self.assertNotEqual(0, result.returncode)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertIn("identity state", stderr.lower())
        self.assertIn("restore", stderr.lower())
        self.assertNotIn("Stack", stderr)
        self.assertNotIn("#0 ", stderr)
        self.assertEqual("", result.stdout.decode("utf-8").strip(),
                         "a failure never prints a receipt")

    def test_e06_first_operator_reseed_is_refused(self):
        login = self._login_for("reseed")
        receipt = self._create_member(login, self._password())
        self._helper_run("state-point-at-account", receipt["account_id"],
                         str(self._reseed_snapshot))
        try:
            result = self._cli({"kind": "first_operator", "login": login,
                                "password": self._password()})
        finally:
            self._helper_run("state-restore", str(self._reseed_snapshot))
        self.assertEqual(1, result.returncode)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertIn("VERSION_CONFLICT", stderr)
        self.assertNotIn("Stack", stderr)


class PurePhpGates(unittest.TestCase):
    """Transport, configuration, classification and Codes gates.

    No socket and no database: these run even when the fixture front is
    not listening, which is what makes them useful as standalone proof.
    """


    def test_d01_transport_gate_never_trusts_a_forwarded_header(self):
        code = ("require %s;\n%s\nrequire_once %s;\n"
                "$names = [];\n"
                "foreach ((new ReflectionMethod('V2Api','assertTransport'))"
                "->getParameters() as $p) { $names[] = $p->getName(); }\n"
                "if ($names !== ['remoteAddr','serverTls','fixture'])"
                " { print(\"PARAMS\\n\"); exit(1); }\n"
                "$cases = [\n"
                "  ['203.0.113.44', false, false, false],\n"
                "  ['203.0.113.44', true,  false, true],\n"
                "  ['127.0.0.1',    false, false, false],\n"
                "  ['127.0.0.1',    true,  false, true],\n"
                "  ['127.0.0.1',    false, true,  true],\n"
                "  ['::1',          false, true,  true],\n"
                "  ['127.0.0.2',    false, true,  false],\n"
                "  ['10.1.2.3',     false, true,  false],\n"
                "];\n"
                "foreach ($cases as $case) {\n"
                "  $threw = false;\n"
                "  try { V2Api::assertTransport($case[0], $case[1], $case[2]); }\n"
                "  catch (ApiException $e) {\n"
                "    $threw = true;\n"
                "    if ($e->errorCode !== 'HTTPS_REQUIRED' || $e->status !== 403)"
                " { print(\"WRONG_CODE\\n\"); exit(1); }\n"
                "  }\n"
                "  if ($threw === $case[3]) { print(\"MATRIX\\n\"); exit(1); }\n"
                "}\n"
                "print(\"OK\\n\");\n"
                % (_quote(str(SRC / 'Http.php')), _product_boot(),
                   _quote(str(V2_API))))
        result = php_pure(code)
        self.assertEqual(0, result.returncode,
                         result.stdout.decode("utf-8", "replace"))
        self.assertEqual("OK", result.stdout.decode("utf-8").strip())

    def test_d02_legacy_base_path_colliding_with_v2_is_config_invalid(self):
        code = ("require %s;\n%s\nrequire_once %s;\n"
                "$db = (new ReflectionClass('Database'))->newInstanceWithoutConstructor();\n"
                "$log = new Log(null);\n"
                "try { new V2Api($db, $log, []); }\n"
                "catch (ApiException $e) { print(\"EMPTY\\n\"); exit(1); }\n"
                "foreach (['/v2','/v2/','/V2','/v2/tenants','/V2/x','/v1',"
                "'/v2x','/api/v2','/v20'] as $base) {\n"
                "  $threw = false;\n"
                "  try { new V2Api($db, $log, ['base_path' => $base]); }\n"
                "  catch (ApiException $e) {\n"
                "    $threw = true;\n"
                "    if ($e->errorCode !== 'CONFIG_INVALID')"
                " { print(\"WRONG_CODE\\n\"); exit(1); }\n"
                "  }\n"
                "  $b = strtolower(rtrim($base, '/'));\n"
                "  $collision = ($b === '/v2' || str_starts_with($b, '/v2/'));\n"
                "  if ($threw !== $collision) { print(\"MATRIX \" . $base . \"\\n\");"
                " exit(1); }\n"
                "}\nprint(\"OK\\n\");\n"
                % (_quote(str(SRC / 'Http.php')), _product_boot(), _quote(str(V2_API))))
        result = php_pure(code)
        self.assertEqual(0, result.returncode,
                         result.stdout.decode("utf-8", "replace"))
        self.assertEqual("OK", result.stdout.decode("utf-8").strip())

    def test_d03_codes_all_equals_the_declared_constant_set(self):
        code = ("require %s;\n"
                "if (!class_exists('Codes', false)) { print(\"MISSING\\n\"); exit(1); }\n"
                "$declared = [];\n"
                "foreach ((new ReflectionClass('Codes'))->getConstants()"
                " as $name => $value) {\n"
                "  if (is_string($value) && Codes::isValid($value))"
                " { $declared[] = $value; }\n"
                "}\n"
                "$all = Codes::ALL;\nsort($declared);\nsort($all);\n"
                "if (count($declared) !== count(array_unique($declared)))"
                " { print(\"DUPLICATE\\n\"); exit(1); }\n"
                "if ($declared !== $all) {\n"
                "  printf(\"MISMATCH declared=%%d all=%%d missing=%%d extra=%%d\\n\",\n"
                "    count($declared), count($all),\n"
                "    count(array_diff($declared, $all)), count(array_diff($all, $declared)));\n"
                "  exit(1);\n}\n"
                "if (!in_array('SESSION_PURPOSE_MISMATCH', $all, true))"
                " { print(\"NO_MISMATCH\\n\"); exit(1); }\n"
                "printf(\"OK declared=%%d\\n\", count($declared));\n"
                % _quote(str(SRC / "Http.php")))
        result = php_pure(code)
        self.assertEqual(0, result.returncode,
                         result.stdout.decode("utf-8", "replace"))
        self.assertRegex(result.stdout.decode("utf-8").strip(),
                         r"\AOK declared=[0-9]+\Z")

    def test_d04_version2_classification_and_log_labels_are_exact(self):
        code = ("require %s;\n%s\nrequire_once %s;\n"
                "$true = ['/v2','/v2/','/v2/auth/me','/V2/auth/me',"
                "'/v2%%2Fauth%%2Flogin'];\n"
                "$false = ['/v2x','/v20/auth/login','/v1/auth/me','//v2/auth/me',"
                "'/','/apiv2/auth/me'];\n"
                "foreach ($true as $p) { if (!V2Api::isVersion2Request($p))"
                " { print(\"FALSE \" . $p . \"\\n\"); exit(1); } }\n"
                "foreach ($false as $p) { if (V2Api::isVersion2Request($p))"
                " { print(\"TRUE \" . $p . \"\\n\"); exit(1); } }\n"
                "if (!V2Api::isVersion2Request('/v2/auth/me?token=abc'))"
                " { print(\"QUERY\\n\"); exit(1); }\n"
                "if (V2Api::logLabel('GET','/v2/auth/me?cursor=secret')"
                " !== 'GET /v2/auth/me') { print(\"LABEL_QUERY\\n\"); exit(1); }\n"
                "if (V2Api::logLabel('GET','/v2/secret-route')"
                " !== 'GET <unknown>') { print(\"LABEL_UNKNOWN\\n\"); exit(1); }\n"
                "print(\"OK\\n\");\n"
                % (_quote(str(SRC / 'Http.php')), _product_boot(), _quote(str(V2_API))))
        result = php_pure(code)
        self.assertEqual(0, result.returncode,
                         result.stdout.decode("utf-8", "replace"))
        self.assertEqual("OK", result.stdout.decode("utf-8").strip())


HELPER_PHP = r'''<?php
/** Fixture-only helper for the /v2 HTTP suite. Never reads product config. */
declare(strict_types=1);
ini_set('display_errors', '0');
ini_set('log_errors', '0');

if (PHP_SAPI !== 'cli') { fwrite(STDERR, "FAIL\n"); exit(2); }

require __BOOTSTRAP__;

$db = BFixture::db();
foreach (['Schema', 'Http', 'Ids', 'Roles', 'Workspaces', 'Auth', 'Accounts',
          'Tokens', 'RateLimit', 'AccountSessions'] as $classFile) {
    require_once BFixture::repoRoot() . '/host/src/' . $classFile . '.php';
}

$accounts = new Accounts($db);
$sessions = new AccountSessions($db);

function accountFor(Database $db, string $login): array
{
    $row = $db->queryOne('SELECT id, login, status, display_name,'
        . ' must_change_password, security_version, platform_role, created_at,'
        . ' updated_at FROM accounts WHERE login = ? LIMIT 2', [$login]);
    if ($row === null) {
        fwrite(STDERR, "FAIL\n");
        exit(3);
    }
    return $row;
}

function saveState(Database $db, string $path): void
{
    $row = $db->queryOne('SELECT id, first_operator_id, row_version'
        . ' FROM identity_state WHERE id = 1 LIMIT 2');
    if ($row === null) {
        file_put_contents($path, json_encode(['present' => false]));
        return;
    }
    file_put_contents($path, json_encode([
        'present' => true,
        'first_operator_id' => $row['first_operator_id'],
        'row_version' => (int) $row['row_version'],
    ]));
}

function restoreState(Database $db, string $path): void
{
    $raw = @file_get_contents($path);
    $saved = $raw === false ? null : json_decode($raw, true);
    $db->execute('DELETE FROM identity_state WHERE id = 1');
    if (is_array($saved) && ($saved['present'] ?? false) === true) {
        $db->insert('identity_state', [
            'id' => 1,
            'first_operator_id' => $saved['first_operator_id'] ?? null,
            'row_version' => (int) ($saved['row_version'] ?? 1),
        ]);
    }
    @unlink($path);
}

/** Delete every synthetic row this suite owns, in dependency order. */
function purge(Database $db, string $prefix): int
{
    $rows = $db->query('SELECT id, login FROM accounts WHERE login LIKE ?',
        [$prefix . '%']);
    if ($rows === []) {
        return 0;
    }
    $ids = [];
    $hashes = [];
    foreach ($rows as $row) {
        $ids[] = (string) $row['id'];
        $hashes[] = 'v2:' . hash('sha256', (string) $row['login']);
    }
    $in = implode(',', array_fill(0, count($ids), '?'));
    $tenants = $db->query('SELECT DISTINCT tenant_id AS id'
        . ' FROM account_workspace_memberships WHERE account_id IN (' . $in . ')',
        $ids);
    $db->execute('UPDATE identity_state SET first_operator_id = NULL'
        . ' WHERE first_operator_id IN (' . $in . ')', $ids);
    $db->execute('DELETE FROM account_sessions WHERE account_id IN (' . $in . ')',
        $ids);
    $db->execute('DELETE FROM account_security_events WHERE actor_account_id'
        . ' IN (' . $in . ') OR subject_account_id IN (' . $in . ')',
        array_merge($ids, $ids));
    $db->execute('DELETE FROM legacy_account_map WHERE account_id IN (' . $in . ')',
        $ids);
    $db->execute('DELETE FROM login_attempts WHERE account IN ('
        . implode(',', array_fill(0, count($hashes), '?')) . ')', $hashes);
    $db->execute('DELETE FROM account_workspace_memberships WHERE account_id'
        . ' IN (' . $in . ')', $ids);
    foreach ($tenants as $tenant) {
        $tenantId = (string) $tenant['id'];
        $db->execute('DELETE FROM audit_events WHERE tenant_id = ?', [$tenantId]);
        $db->execute('DELETE FROM login_attempts WHERE tenant_id = ?', [$tenantId]);
        $db->execute('DELETE FROM tenants WHERE id = ?', [$tenantId]);
    }
    return $db->execute('DELETE FROM accounts WHERE id IN (' . $in . ')', $ids);
}

$command = $argv[1] ?? '';
try {
    switch ($command) {
        case 'disable':
        case 'enable':
            $status = $command === 'disable'
                ? Accounts::STATUS_DISABLED : Accounts::STATUS_ACTIVE;
            $accounts->setStatus((string) accountFor($db, $argv[2])['id'],
                $status, time());
            print("OK\n");
            break;

        case 'issue':
            $account = accountFor($db, $argv[2]);
            $token = $db->transaction(static function (Database $tx) use (
                $sessions, $account, $argv
            ): string {
                $live = $tx->queryOne('SELECT * FROM accounts WHERE id = ?'
                    . ' FOR UPDATE', [(string) $account['id']]);
                if ($live === null) {
                    throw new RuntimeException('FIXTURE_SCENARIO_FAILED');
                }
                return $sessions->issueWithin($tx, $live, (string) $argv[3],
                    time())['token'];
            });
            print($token);
            break;

        case 'session-window':
            $account = accountFor($db, $argv[2]);
            $row = $db->queryOne('SELECT idle_expires_at, last_activity_at'
                . ' FROM account_sessions WHERE account_id = ? AND purpose = ?'
                . ' ORDER BY issued_at DESC, id DESC LIMIT 1',
                [(string) $account['id'], AccountSessions::PURPOSE_DESKTOP]);
            if ($row === null) {
                print("none");
                break;
            }
            print('idle=' . (string) $row['idle_expires_at']
                . ' last=' . (string) $row['last_activity_at']);
            break;

        case 'purge':
            print((string) purge($db, (string) $argv[2]));
            break;

        case 'state-snapshot':
            saveState($db, (string) $argv[2]);
            print("OK\n");
            break;

        case 'state-delete':
            saveState($db, (string) $argv[2]);
            $db->execute('DELETE FROM identity_state WHERE id = 1');
            print("OK\n");
            break;

        case 'state-point-at-account':
            saveState($db, (string) $argv[3]);
            $db->execute('UPDATE identity_state SET first_operator_id = ?,'
                . ' row_version = row_version + 1 WHERE id = 1', [$argv[2]]);
            print("OK\n");
            break;

        case 'state-restore':
            restoreState($db, (string) $argv[2]);
            print("OK\n");
            break;

        case 'state-present':
            $present = (int) $db->queryValue('SELECT COUNT(*) AS n'
                . ' FROM identity_state WHERE id = 1');
            print($present === 0 ? "0" : "1");
            break;

        default:
            fwrite(STDERR, "FAIL\n");
            exit(2);
    }
} catch (Throwable $error) {
    fwrite(STDERR, 'FAIL ' . get_class($error) . "\n");
    exit(1);
}
'''


if __name__ == "__main__":  # pragma: no cover - the runner loads this as a module
    unittest.main()
