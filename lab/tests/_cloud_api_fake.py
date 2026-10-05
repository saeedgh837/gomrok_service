# -*- coding: utf-8 -*-
"""The executable copy of spec §6: a loopback fake of the cPanel API.

Offline, deterministic and stdlib-only. It binds ``127.0.0.1`` on an
ephemeral port, never sleeps to synchronise, and speaks every endpoint the
contract defines:

* ``GET  /v1/health/live``
* ``POST /v1/auth/login`` · ``/auth/refresh`` · ``/auth/logout``
* ``POST /v1/sync-devices/enroll``
* ``GET  /v1/sync/bootstrap``
* ``GET  /v1/sync/changes?cursor=&limit=``
* ``POST /v1/sync/mutations``
* ``POST /v1/bootstrap/import``  (spec §8, added by workstream K)

What it enforces, because a fake that is lax proves nothing:

* the ``{"ok":true,...}`` / ``{"ok":false,"error":{code,message,request_id}}``
  envelope, with a ``message`` carrying the ``SERVER_MESSAGE_CANARY`` marker so
  a test can prove the client never lets server text reach a log or an
  exception;
* bearer auth on every route that needs it, and ``401`` once a token is
  expired or revoked;
* refresh-token **rotation** and reuse detection (a replayed refresh token
  kills the family — spec §5);
* idempotent ``operation_id`` replay: the second send of an id returns the
  first result and applies nothing;
* the §8 import gate: the same ``import_id`` replays byte for byte, the same
  ``import_id`` with different bytes is ``409 IMPORT_CONFLICT``, and any other
  import of a tenant that already holds rows is ``409 DATASET_NOT_EMPTY``;
* compare-and-swap on ``row_version`` with ``conflicts[]`` carrying the
  server copy, and a batch that still applies the untouched operations;
* cursors, ``has_more``, tombstones (``record: null``);
* fault injection for ``429`` / ``5xx`` / malformed bodies.

**Swappable in exactly one place.** ``acquire()`` returns a handle whose
``base_url`` is ``IRICA_TEST_API_BASE`` when that variable is set, so a later
workstream can point the same tests at the real PHP server without editing a
single test body. When the variable is set, ``handle.fake`` is ``None`` and
scenarios that script faults skip themselves.

Nothing here is a real credential: the only password is
:data:`FAKE_PASSWORD`, and every token is prefixed with ``fake-``.
"""
import contextlib
import hashlib
import json
import os
import re
import threading
import uuid

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

__all__ = [
    "API_PREFIX", "EXTERNAL_ENV", "FAKE_PASSWORD", "FAKE_USERNAME",
    "SERVER_MESSAGE_CANARY", "FakeApi", "ApiHandle", "acquire", "start_api",
    "with_local_api", "error_envelope",
]

#: The base URL includes the version prefix (spec §6: ``https://api.<d>/v1``).
API_PREFIX = "/v1"

#: The one and only place this environment variable is honoured.
EXTERNAL_ENV = "IRICA_TEST_API_BASE"

#: Obviously-fake credentials. They authenticate against this process only.
FAKE_USERNAME = "admin"
FAKE_PASSWORD = "FAKE-admin-password-not-real"

#: Embedded in every server-side error message. If it ever shows up in a log
#: line or an exception string, the client leaked the server's text.
SERVER_MESSAGE_CANARY = "SERVER_MESSAGE_CANARY"

#: Mirrors spec §10.6: reject bodies that are plainly not one of ours.
MAX_BODY_BYTES = 1024 * 1024

_ENTITIES = ("customers", "customer_transactions", "owners", "identities",
             "crm_devices", "periods", "settings")
#: The admin role may enrol devices (spec §7).
_ENROL_ROLES = frozenset({"admin"})

_MISSING_BODY = "missing or malformed JSON body"

#: `host/src/Ids.php isUuid()` — the only id shape an import may carry.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_uuid(value):
    return isinstance(value, str) and _UUID_RE.match(value.lower()) is not None


def _bump_epoch(value):
    """Spec §8.5: ``dataset_epoch`` is incremented by an import.

    The fake seeds it as an opaque token so bootstrap snapshots stay
    distinguishable; an int grows by one, a token gains a generation suffix.
    Either way it must visibly change, because that is what a test asserts.
    """
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        head, marker, generation = value.partition("_gen")
        if marker and generation.isdigit():
            return "%s_gen%d" % (head, int(generation) + 1)
        return value + "_gen1"
    return 1


def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).isoformat()


def _now_iso():
    return _iso(__import__("time").time())


def error_envelope(status, code, message=None):
    """The §6 failure envelope: ``{ok:false, error:{code, message, request_id}}``.

    The ``message`` always carries :data:`SERVER_MESSAGE_CANARY` so a test can
    prove the client never lets server text through.
    """
    text = message or ("%s: %s (server text — never show this to the "
                       "operator)" % (SERVER_MESSAGE_CANARY, code))
    return status, {
        "ok": False,
        "error": {"code": code, "message": text,
                  "request_id": str(uuid.uuid4())},
    }


class FakeApi(object):
    """A complete, thread-safe in-process implementation of spec §6."""

    def __init__(self, *, tenant_id=None, dataset_epoch=None):
        self._lock = threading.RLock()
        self._server = None
        self.tenant_id = tenant_id or ("tenant-" + uuid.uuid4().hex[:8])
        self.dataset_epoch = dataset_epoch or ("epoch-" + uuid.uuid4().hex[:8])
        self.requests = []          # in-memory only, never written to disk
        self.apply_count = {}       # operation_id -> how often it was applied
        self._faults = {}
        self._reuse_next = False
        self.reset()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def reset(self):
        """Back to a pristine tenant. Keeps nothing between tests."""
        with self._lock:
            self.users = {
                FAKE_USERNAME: {"password": FAKE_PASSWORD, "role": "admin",
                                "must_change_password": False},
            }
            self.records = {name: {} for name in _ENTITIES}
            self.change_log = []
            self.seq = 0
            self.idempotency = {}
            # §8: import_id -> {"hash": sha256(canonical collections),
            #                   "response": the stored result to replay}.
            self.imports = {}
            self.access_tokens = {}      # token -> {kind, family, device_id}
            self.refresh_tokens = {}     # token -> {kind, family, used}
            self.families = {}           # family -> {revoked}
            self.sync_devices = {}       # device_id -> {status, family}
            self.requests = []
            self.apply_count = {}
            self._faults = {}
            self._reuse_next = False
            self._acting_user = FAKE_USERNAME
            self.dataset_epoch = ("epoch-" + uuid.uuid4().hex[:8])

    @property
    def base_url(self):
        if self._server is None:
            raise RuntimeError("FakeApi.start() has not been called")
        host, port = self._server.server_address[:2]
        return "http://%s:%d%s" % (host, port, API_PREFIX)

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        """Bind loopback:0 and serve in a daemon thread. Returns self."""
        if self._server is not None:
            return self
        api = self

        class Handler(_Handler):
            pass

        Handler.api = api

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever,
                                  name="irica-fake-api", daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def stop(self):
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------
    def seed(self, entity, record_id, record, *, row_version=1):
        """Put a row in place and append it to the change log."""
        if entity not in self.records:
            raise KeyError(entity)
        with self._lock:
            self.records[entity][record_id] = {
                "id": record_id,
                "tenant_id": self.tenant_id,
                "row_version": int(row_version),
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "deleted_at": None,
                "record": record,
            }
            self.seq += 1
            self.change_log.append({
                "entity": entity, "id": record_id, "seq": self.seq,
                "row_version": int(row_version), "record": record,
            })
            return self.seq

    def row(self, entity, record_id):
        with self._lock:
            return self.records.get(entity, {}).get(record_id)

    def collections(self):
        with self._lock:
            return {name: dict(rows) for name, rows in self.records.items()}

    def device_refresh_token(self, device_id=None):
        """The current device refresh token (tests enrol first)."""
        with self._lock:
            for token, meta in self.refresh_tokens.items():
                if meta["kind"] == "device" and not meta["used"] \
                        and (device_id is None or meta.get("device_id")
                             == device_id):
                    return token
        return None

    def device_access_token(self):
        with self._lock:
            for token, meta in self.access_tokens.items():
                if meta["kind"] == "device":
                    return token
        return None

    # ------------------------------------------------------------------
    # Fault injection (never sleeps, never threads)
    # ------------------------------------------------------------------
    def fail_next(self, route, status=500, code="HTTP_500", count=1,
                  *, headers=None, body=None, message=None):
        """Queue `count` canned failures for `route` ("POST /sync/mutations").

        ``body`` overrides the payload verbatim (used to fake a malformed
        response); otherwise the standard error envelope is built with `code`.
        """
        key = route
        with self._lock:
            bucket = self._faults.setdefault(key, [])
            for _ in range(count):
                bucket.append({"status": status, "code": code,
                               "headers": dict(headers or {}),
                               "body": body, "message": message})
        return self

    def expire_access_tokens(self):
        """Every access token stops working — the next call answers 401."""
        with self._lock:
            self.access_tokens.clear()
        return self

    def revoke_device_family(self):
        """Revoke the device family, so the refresh token is refused too."""
        with self._lock:
            for meta in self.families.values():
                meta["revoked"] = True
            self.access_tokens.clear()
            for meta in self.refresh_tokens.values():
                if meta["kind"] == "device":
                    meta["used"] = True
        return self

    def reuse_next_refresh(self):
        """Make the next /auth/refresh look like a replayed token (§5)."""
        with self._lock:
            self._reuse_next = True
        return self

    def clear_faults(self):
        with self._lock:
            self._faults.clear()
        return self

    def calls(self, route=None):
        """Requests matching `route`, which may be ``"/sync/changes"`` or the
        full ``"GET /sync/changes"`` key used for fault injection."""
        with self._lock:
            if route is None:
                return list(self.requests)
            return [r for r in self.requests
                    if r["route"] == route or r["key"] == route]

    def _take_fault(self, key):
        with self._lock:
            bucket = self._faults.get(key)
            if not bucket:
                return None
            return bucket.pop(0)

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------
    def _new_family(self, kind, *, device_id=None):
        family = uuid.uuid4().hex
        self.families[family] = {"revoked": False, "kind": kind,
                                 "device_id": device_id}
        return family

    def _issue(self, kind, family, *, device_id=None):
        access = "fake-%s-access-%s" % (kind, uuid.uuid4().hex)
        refresh = "fake-%s-refresh-%s" % (kind, uuid.uuid4().hex)
        self.access_tokens[access] = {"kind": kind, "family": family,
                                      "device_id": device_id}
        self.refresh_tokens[refresh] = {"kind": kind, "family": family,
                                        "used": False,
                                        "device_id": device_id}
        return access, refresh

    def _access_meta(self, token, kind):
        with self._lock:
            meta = self.access_tokens.get(token)
            if meta is None or meta["kind"] != kind:
                return None
            family = self.families.get(meta["family"])
            if family is None or family["revoked"]:
                return None
            return meta

    def _user_permission(self, meta, permission):
        if permission == "device:enroll":
            # Spec §7: admins by default.
            user = getattr(self, "_acting_user", None) or FAKE_USERNAME
            return self.users.get(user, {}).get("role") in _ENROL_ROLES
        return True

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _append_change(self, entity, record_id, row, record):
        self.seq += 1
        self.change_log.append({
            "entity": entity, "id": record_id, "seq": self.seq,
            "row_version": row["row_version"], "record": record,
        })
        return self.seq


class _Handler(BaseHTTPRequestHandler):
    """Minimal JSON HTTP handler. All work happens on ``self.api``."""

    server_version = "IricaFakeAPI"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):                 # keep stdout out of the run
        return

    # -- plumbing -------------------------------------------------------
    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            size = int(length)
        except ValueError:
            return None
        if size < 0 or size > MAX_BODY_BYTES:
            return None
        return self.rfile.read(size)

    def _send(self, status, payload, *, headers=None):
        # Record the status *before* the bytes go out: the client may read the
        # fake's log the instant its own request returns, and a record written
        # after that would race with it.
        record = getattr(self, "_record", None)
        if record is not None:
            record["status"] = status
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
        else:
            raw = json.dumps(payload, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    @staticmethod
    def _ok(payload):
        body = dict(payload)
        body["ok"] = True
        return body

    @staticmethod
    def _envelope(status, code, message=None):
        return error_envelope(status, code, message)

    def _bearer(self):
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return ""
        return header[len("Bearer "):].strip()

    def _route(self, path):
        """`/v1/sync/changes` -> (`/sync/changes`, {'cursor': …})."""
        split = urlsplit(path)
        raw = split.path
        if raw.startswith(API_PREFIX):
            raw = raw[len(API_PREFIX):]
        query = {key: values[-1] for key, values in
                 parse_qs(split.query, keep_blank_values=True).items()}
        return raw or "/", query

    # -- verbs ----------------------------------------------------------
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_HEAD(self):                             # pragma: no cover - unused
        self._handle("GET")

    def _handle(self, method):
        route, query = self._route(self.path)
        api = self.api
        status = 500
        key = "%s %s" % (method, route)
        # The entry exists before anything is answered, so a caller that reads
        # the log right after its request returns always sees it.
        record = {"method": method, "route": route, "key": key, "status": None,
                  "authorization": self.headers.get("Authorization") or ""}
        with api._lock:
            api.requests.append(record)
        self._record = record
        # Drain the body up front. A handler that answers without reading it
        # would leave those bytes in the socket, and the next pipelined
        # request on this connection would be parsed as garbage (HTTP 501).
        self._raw_body = self._read_body() if method == "POST" else b""
        if self._raw_body is None and method == "POST":
            # Too large to read: nothing sane can follow on this connection.
            self.close_connection = True
        try:
            status = self._dispatch(method, route, query)
        except _Stop as stopped:                   # the route already answered
            status = stopped.status
        except Exception:                          # noqa: BLE001 - the fake must
            # never take the test run down; answer with a real envelope.
            status, payload = self._envelope(500, "SERVER_ERROR")
            self._send(status, payload)
        record["status"] = status
        return

    # -- dispatch -------------------------------------------------------
    def _dispatch(self, method, route, query):
        api = self.api
        fault = api._take_fault("%s %s" % (method, route))
        if fault is not None:
            if fault["body"] is not None:
                self._send(fault["status"], fault["body"],
                           headers=fault["headers"])
                return fault["status"]
            status, payload = self._envelope(fault["status"], fault["code"],
                                             fault["message"])
            self._send(status, payload, headers=fault["headers"])
            return status

        handler = {
            "GET /health/live": self._health,
            "POST /auth/login": self._login,
            "POST /auth/refresh": self._refresh,
            "POST /auth/logout": self._logout,
            "POST /sync-devices/enroll": self._enroll,
            "GET /sync/bootstrap": self._bootstrap,
            "GET /sync/changes": self._changes,
            "POST /sync/mutations": self._mutations,
            "POST /bootstrap/import": self._bootstrap_import,
        }.get("%s %s" % (method, route))
        if handler is None:
            status, payload = self._envelope(404, "HTTP_404")
            self._send(status, payload)
            return status
        return handler(route, query)

    def _body(self, required=True):
        raw = self._raw_body
        if raw is None:
            status, payload = self._envelope(413, "HTTP_ERROR")
            self._send(status, payload)
            raise _Stop(status)
        if not raw:
            if required:
                status, payload = self._envelope(400, "BAD_PAYLOAD",
                                                 "%s: %s" % (SERVER_MESSAGE_CANARY,
                                                             _MISSING_BODY))
                self._send(status, payload)
                raise _Stop(status)
            return {}
        content_type = (self.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            # Spec §10.6: strict Content-Type check.
            status, payload = self._envelope(415, "HTTP_ERROR")
            self._send(status, payload)
            raise _Stop(status)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            status, payload = self._envelope(400, "BAD_PAYLOAD")
            self._send(status, payload)
            raise _Stop(status)
        if not isinstance(parsed, dict):
            status, payload = self._envelope(400, "BAD_PAYLOAD")
            self._send(status, payload)
            raise _Stop(status)
        return parsed

    # -- routes ---------------------------------------------------------
    def _health(self, route, query):
        self._send(200, self._ok({"time": _now_iso()}))
        return 200

    def _login(self, route, query):
        api = self.api
        body = self._body()
        username = body.get("username")
        password = body.get("password")
        with api._lock:
            user = api.users.get(username) if isinstance(username, str) \
                else None
            if user is None or not isinstance(password, str) \
                    or password != user["password"]:
                status, payload = self._envelope(401, "AUTH_REQUIRED")
                self._send(status, payload)
                return status
            family = api._new_family("user")
            api._acting_user = username
            access, refresh = api._issue("user", family)
        self._send(200, self._ok({
            "access_token": access, "refresh_token": refresh,
            "expires_in": 900,
            "must_change_password": bool(user["must_change_password"]),
            "user": {"username": username, "role": user["role"]},
        }))
        return 200

    def _refresh(self, route, query):
        api = self.api
        body = self._body()
        token = body.get("refresh_token")
        kind = body.get("kind")
        if kind not in ("device", "user") or not isinstance(token, str) \
                or not token:
            status, payload = self._envelope(400, "BAD_PAYLOAD")
            self._send(status, payload)
            return status
        with api._lock:
            meta = api.refresh_tokens.get(token)
            if meta is None or meta["kind"] != kind:
                status, payload = self._envelope(401, "AUTH_REQUIRED")
                self._send(status, payload)
                return status
            family = api.families.get(meta["family"])
            reuse = api._reuse_next or meta["used"] or meta.get("replayed")
            if api._reuse_next:
                api._reuse_next = False
                meta["replayed"] = True
            if reuse or (family is not None and family["revoked"]):
                # Spec §5: one-time-use family. Reuse invalidates all of it.
                if family is not None:
                    family["revoked"] = True
                api.access_tokens = {k: v for k, v in
                                     api.access_tokens.items()
                                     if v["family"] != meta["family"]}
                status, payload = self._envelope(401, "AUTH_REVOKED")
                self._send(status, payload)
                return status
            # Rotate: the presented token is dead the moment it is used.
            meta["used"] = True
            device_id = meta.get("device_id")
            access, refreshed = api._issue(kind, meta["family"],
                                           device_id=device_id)
        self._send(200, self._ok({"access_token": access,
                                  "refresh_token": refreshed,
                                  "expires_in": 900}))
        return 200

    def _logout(self, route, query):
        api = self.api
        self._body(required=False)
        token = self._bearer()
        with api._lock:
            meta = api._access_meta(token, "user")
            if meta is None:
                status, payload = self._envelope(401, "AUTH_REQUIRED")
                self._send(status, payload)
                return status
            api.families[meta["family"]]["revoked"] = True
            api.access_tokens.pop(token, None)
        self._send(200, self._ok({}))
        return 200

    def _enroll(self, route, query):
        api = self.api
        self._body(required=False)
        token = self._bearer()
        with api._lock:
            meta = api._access_meta(token, "user")
            if meta is None:
                status, payload = self._envelope(401, "AUTH_REQUIRED")
                self._send(status, payload)
                return status
            if not api._user_permission(meta, "device:enroll"):
                status, payload = self._envelope(403, "HTTP_403")
                self._send(status, payload)
                return status
            device_id = "dev-" + uuid.uuid4().hex
            family = api._new_family("device", device_id=device_id)
            _, refresh = api._issue("device", family, device_id=device_id)
            api.sync_devices[device_id] = {"status": "active",
                                           "family": family}
        self._send(200, self._ok({"sync_device_id": device_id,
                                  "device_refresh_token": refresh,
                                  "tenant_id": api.tenant_id}))
        return 200

    def _require_device(self):
        token = self._bearer()
        meta = self.api._access_meta(token, "device")
        if meta is None:
            status, payload = self._envelope(401, "AUTH_REQUIRED")
            self._send(status, payload)
            raise _Stop(status)
        return meta

    def _bootstrap(self, route, query):
        api = self.api
        with api._lock:
            self._require_device()
            collections = {
                name: [{"id": rid, "tenant_id": row["tenant_id"],
                        "row_version": row["row_version"],
                        "record": row["record"]}
                       for rid, row in rows.items()
                       if row["deleted_at"] is None]
                for name, rows in api.records.items()}
            payload = self._ok({
                "collections": collections,
                "cursor": api.seq,
                "dataset_epoch": api.dataset_epoch,
                "tenant_id": api.tenant_id,
            })
        self._send(200, payload)
        return 200

    def _changes(self, route, query):
        api = self.api
        raw_cursor = query.get("cursor", "0")
        raw_limit = query.get("limit", "500")
        try:
            cursor = int(raw_cursor)
            limit = int(raw_limit)
        except (TypeError, ValueError):
            status, payload = self._envelope(400, "BAD_PAYLOAD")
            self._send(status, payload)
            return status
        if cursor < 0 or limit < 1 or limit > 5000:
            status, payload = self._envelope(400, "BAD_PAYLOAD")
            self._send(status, payload)
            return status
        with api._lock:
            self._require_device()
            pending = [row for row in api.change_log if row["seq"] > cursor]
            page = pending[:limit]
            has_more = len(pending) > limit
            next_cursor = page[-1]["seq"] if page else cursor
            payload = self._ok({"changes": [dict(row) for row in page],
                                "cursor": next_cursor,
                                "has_more": has_more})
        self._send(200, payload)
        return 200

    def _mutations(self, route, query):
        api = self.api
        body = self._body()
        operations = body.get("operations")
        if not isinstance(operations, list) or not operations:
            status, payload = self._envelope(400, "BAD_PAYLOAD")
            self._send(status, payload)
            return status

        with api._lock:
            self._require_device()
            # Remember the order the batch arrived in: parents before children
            # is a contract requirement, and the test asserts it from here.
            if self._record is not None:
                self._record["entities"] = [
                    op.get("entity") for op in operations
                    if isinstance(op, dict)]
            # Validate the whole batch first: one bad row must not leave a
            # half-applied transaction behind (spec §6.1).
            for op in operations:
                if not isinstance(op, dict):
                    status, payload = self._envelope(400, "BAD_PAYLOAD")
                    self._send(status, payload)
                    return status
                if not isinstance(op.get("operation_id"), str) \
                        or not op["operation_id"] \
                        or op.get("kind") not in ("upsert", "delete") \
                        or op.get("entity") not in _ENTITIES \
                        or not isinstance(op.get("id"), str) or not op["id"] \
                        or isinstance(op.get("base_version"), bool) \
                        or not isinstance(op.get("base_version"), int) \
                        or op["base_version"] < 0:
                    status, payload = self._envelope(400, "BAD_PAYLOAD")
                    self._send(status, payload)
                    return status
                if op["kind"] == "upsert" and not isinstance(
                        op.get("record"), dict):
                    status, payload = self._envelope(400, "BAD_PAYLOAD")
                    self._send(status, payload)
                    return status

            applied = []
            conflicts = []
            for op in operations:
                operation_id = op["operation_id"]
                replay = api.idempotency.get(operation_id)
                if replay is not None:
                    # Spec §6.1: same id returns the first result, and the
                    # row is touched exactly once for all time.
                    if replay["status"] == "applied":
                        applied.append(operation_id)
                    else:
                        conflicts.append(dict(replay["conflict"]))
                    continue
                result = self._apply_one(op)
                api.idempotency[operation_id] = result
                if result["status"] == "applied":
                    applied.append(operation_id)
                else:
                    conflicts.append(dict(result["conflict"]))
            cursor = api.seq
        self._send(200, self._ok({"applied": applied, "conflicts": conflicts,
                                  "cursor": cursor}))
        return 200

    def _apply_one(self, op):
        api = self.api
        entity = op["entity"]
        record_id = op["id"]
        base = op["base_version"]
        rows = api.records[entity]
        row = rows.get(record_id)

        if row is not None and row["row_version"] != base:
            return {"status": "conflict", "conflict": {
                "operation_id": op["operation_id"],
                "entity": entity, "id": record_id,
                "server_version": row["row_version"],
                "server_record": row["record"],
            }}
        if row is None and base != 0:
            # CAS against a row this machine has never seen: the server has
            # nothing to compare with, which is a conflict, not a free create.
            return {"status": "conflict", "conflict": {
                "operation_id": op["operation_id"],
                "entity": entity, "id": record_id,
                "server_version": 0, "server_record": None,
            }}

        if row is None:
            row = {"id": record_id, "tenant_id": api.tenant_id,
                   "row_version": 1, "created_at": _now_iso(),
                   "updated_at": _now_iso(), "deleted_at": None,
                   "record": None}
            rows[record_id] = row
        else:
            row["row_version"] += 1
            row["updated_at"] = _now_iso()

        if op["kind"] == "delete":
            row["deleted_at"] = _now_iso()
            row["record"] = None
            stored = None
        else:
            row["record"] = op["record"]
            stored = op["record"]
        api.apply_count[op["operation_id"]] = \
            api.apply_count.get(op["operation_id"], 0) + 1
        api._append_change(entity, record_id, row, stored)
        return {"status": "applied"}

    # ------------------------------------------------------------------
    # §8: one-shot bootstrap import
    # ------------------------------------------------------------------
    def _x_acting_user(self):
        """The spec §6 ``X-Acting-User`` credential, or None when absent.

        The header is a bearer *user* access token; the device token in
        ``Authorization`` is checked separately by ``_require_device``. An
        import needs both (spec §6, §8.2), so a missing header is a 401 even
        when the device is perfectly healthy.
        """
        raw = self.headers.get("X-Acting-User") or ""
        token = raw[7:].strip() if raw.startswith("Bearer ") else raw.strip()
        if not token:
            return None
        return self.api._access_meta(token, "user")

    @staticmethod
    def _import_documents(collections):
        """`collections` → `{entity: [record, …]}`, or ValueError.

        Mirrors `host/src/Import.php normalize()`: every value a JSON array,
        `settings` also accepted as a single object and wrapped, one settings
        row at most, and every record carrying the id its entity needs.
        """
        out = {}
        for entity, value in collections.items():
            if entity not in _ENTITIES:
                raise ValueError(entity)
            if entity == "settings" and isinstance(value, dict):
                value = [value]
            if not isinstance(value, list):
                raise ValueError(entity)
            entries = []
            for record in value:
                if not isinstance(record, dict):
                    raise ValueError(entity)
                identifier = record.get("id")
                if entity in ("customer_transactions", "settings"):
                    # Minted by the client (spec §15.2): a real UUID or bust.
                    usable = _is_uuid(identifier)
                else:
                    usable = (isinstance(identifier, str) and identifier
                              and len(identifier) <= 64)
                if not usable:
                    raise ValueError(entity)
                entries.append(dict(record))
            if entity == "settings" and len(entries) > 1:
                raise ValueError(entity)
            out[entity] = entries
        return out

    @staticmethod
    def _import_row_id(entity, record, installation):
        """The §4 uuid5 the host would compute for this record."""
        if entity == "settings":
            legacy = "settings/settings"          # spec §15.2
        elif entity == "customer_transactions":
            return record["id"]                   # minted client-side
        else:
            legacy = record["id"]
        return str(uuid.uuid5(
            uuid.NAMESPACE_URL, "%s/%s/%s" % (installation, entity, legacy)))

    def _bootstrap_import(self, route, query):
        api = self.api
        body = self._body()
        with api._lock:
            self._require_device()
            if self._x_acting_user() is None:
                status, payload = self._envelope(401, "AUTH_REQUIRED")
                self._send(status, payload)
                return status
            actor = api._acting_user or FAKE_USERNAME
            if api.users.get(actor, {}).get("role") != "admin":
                # Spec §7: running an import is an admin capability.
                status, payload = self._envelope(403, "HTTP_403")
                self._send(status, payload)
                return status

            import_id = body.get("import_id")
            installation = body.get("source_installation_id")
            collections = body.get("collections")
            if not _is_uuid(import_id) or not _is_uuid(installation) \
                    or not isinstance(collections, dict):
                status, payload = self._envelope(400, "BAD_PAYLOAD")
                self._send(status, payload)
                return status
            try:
                documents = self._import_documents(collections)
            except ValueError:
                status, payload = self._envelope(400, "BAD_PAYLOAD")
                self._send(status, payload)
                return status
            digest = hashlib.sha256(
                json.dumps(documents, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")).hexdigest()

            previous = api.imports.get(import_id)
            if previous is not None:
                if previous["hash"] == digest:
                    # §8.4: the identical result, byte for byte.
                    self._send(200, self._ok(dict(previous["response"])))
                    return 200
                status, payload = self._envelope(409, "IMPORT_CONFLICT")
                self._send(status, payload)
                return status
            if api.imports or any(rows for rows in api.records.values()):
                status, payload = self._envelope(409, "DATASET_NOT_EMPTY")
                self._send(status, payload)
                return status

            counts = {}
            for entity in _ENTITIES:
                counts[entity] = 0
                for record in documents.get(entity, []):
                    api.seed(entity, self._import_row_id(entity, record,
                                                         installation), record)
                    counts[entity] += 1
            api.dataset_epoch = _bump_epoch(api.dataset_epoch)
            response = {
                "import_id": import_id,
                "tenant_id": api.tenant_id,
                "dataset_epoch": api.dataset_epoch,
                "source_installation_id": installation,
                "counts": counts,
                "rows": sum(counts.values()),
                "cursor": api.seq,
            }
            api.imports[import_id] = {"hash": digest, "response": response}
        self._send(200, self._ok(dict(response)))
        return 200


class _Stop(Exception):
    """The route already wrote its response; `status` is what it wrote."""

    def __init__(self, status):
        self.status = status
        super().__init__("answered %d" % status)


class ApiHandle(object):
    """What a test talks to: always a ``base_url``, a fake only when local."""

    __slots__ = ("base_url", "fake")

    def __init__(self, base_url, fake):
        self.base_url = base_url
        self.fake = fake

    @property
    def external(self):
        """True when IRICA_TEST_API_BASE pointed us at a real server."""
        return self.fake is None

    def close(self):
        if self.fake is not None:
            self.fake.stop()


def acquire():
    """**The single place** ``IRICA_TEST_API_BASE`` is read (spec §13).

    With the variable set, tests run against that real base URL and
    ``handle.fake`` is ``None``; otherwise a loopback fake is started. The
    fake is only stopped by :meth:`ApiHandle.close`.
    """
    external = (os.environ.get(EXTERNAL_ENV) or "").strip()
    if external:
        return ApiHandle(external.rstrip("/"), None)
    fake = FakeApi().start()
    return ApiHandle(fake.base_url, fake)


def start_api(testcase, *, needs_fake=True):
    """Acquire a handle, register its cleanup, and skip when scripted.

    Pass ``needs_fake=False`` for scenarios that only need a reachable base
    URL (health, a plain 404), so those still run against a real server.
    """
    handle = acquire()
    testcase.addCleanup(handle.close)
    if needs_fake and handle.external:
        testcase.skipTest(
            "%s points at a real server (%s); this scenario scripts the fake"
            % (EXTERNAL_ENV, handle.base_url))
    return handle


@contextlib.contextmanager
def _pushed_env(name, value):
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def with_local_api():
    """Context manager that clears IRICA_TEST_API_BASE for its body.

    Used by the test that proves the variable is honoured: inside the block
    ``acquire()`` must return the loopback fake, outside it the real base URL
    wins again.
    """
    return _pushed_env(EXTERNAL_ENV, "")
