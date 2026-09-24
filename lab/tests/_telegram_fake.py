"""Offline stand-in for the Telegram Bot API, shared by the telegram tests.

Every telegram test this round talks to this local fake instead of the
operator's bot: nothing here ever resolves a real host. The server binds
("127.0.0.1", 0) -- an ephemeral port, loopback only -- and the only token any
test may use is the fake one ("TEST:token"). Importing this module opens no
socket and writes no file; `FakeTelegram.start()` is what listens.

The fake speaks the two endpoints the production notifier calls
(`/bot<token>/sendPhoto` and `/bot<token>/sendMessage`), accepts both body
shapes requests produces (multipart/form-data for photos, urlencoded for text),
and records per request the chat_id the body carried, the method taken from the
request path, and the byte size of the `photo` part. A request is recorded as
soon as its destination is known, so an upload the caller abandons halfway
(because its budget ran out) still counts as one attempt -- with
`photo_bytes == 0` and `finished is None`.

Timing knobs exist to reproduce the production bug where one slow destination
eats the whole caller deadline:

* ``delay={chat_id: seconds}`` -- the server sleeps before it consumes the
  upload. It reads only the small multipart prefix needed to learn the chat_id
  and then waits, so a client still writing its 1.4 MB receipt really blocks
  (its socket buffer fills up); the 150 KB payment PNG cannot do that. This is
  the cheap way to burn a caller's whole budget on one destination.
* ``slow_read={chat_id: chunk_bytes}`` -- the rest of the body is read that
  many bytes at a time with ``FakeTelegram.SLOW_READ_SLEEP_SEC`` (20 ms)
  between reads, so a large upload takes a predictable, non-buffer-dependent
  time.

``mode`` decides what the fake answers: "ok" (a normal reply), "chat_not_found"
(HTTP 400 with the exact `Bad Request: chat not found` description the
production code treats as permanent), "server_error" (HTTP 500), "blackhole"
(never answers -- the client must hit its own timeout), or a callable
``(chat_id, method, seen_index) -> (status, payload)`` for per-request
scripting. ``seen_index`` is the 0-based index of the request in this fake's
call log since the last ``start()``/``reset()``. A callable may return ``None``
as the payload to send the status with an empty body, and ``(None, None)`` to
leave the request unanswered.

Typical test module:

    import os
    import tempfile

    import _telegram_fake

    DATA_DIR = _telegram_fake.temp_data_dir()   # before app_paths/telegram_notify
    import telegram_notify

    fake = _telegram_fake.FakeTelegram().start()
    cfg = fake.write_config(os.path.join(tempdir, "telegram_config.json"),
                            ["111", "222"])
    restore = _telegram_fake.use_config(telegram_notify, cfg)
    try:
        telegram_notify.send_photo(receipt_png, timeout=25)
    finally:
        restore()
        fake.stop()
"""
import http.server
import json
import os
import random
import sys
import tempfile
import threading
import time
import urllib.parse

__all__ = ["FakeTelegram", "make_receipt_png", "temp_data_dir", "use_config"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Read this much at a time while hunting for the chat_id in a fresh body.
_PEEK_CHUNK = 4096
#: Give up peeking here; the remainder is read as the upload body.
_PEEK_LIMIT = 65536
#: Read chunk when `slow_read` does not cover the chat_id.
_DEFAULT_READ_CHUNK = 65536
#: A blackholed request is dropped no later than this (seconds).
_BLACKHOLE_MAX_WAIT = 300.0
#: Accepted `mode` strings; anything else is either a callable or a typo.
_MODES = ("ok", "chat_not_found", "server_error", "blackhole")
_MISSING = object()


def _boundary_of(content_type):
    """The multipart boundary as bytes, or b"" when there is none."""
    text = content_type or ""
    marker = "boundary="
    index = text.lower().find(marker)
    if index < 0:
        return b""
    value = text[index + len(marker):].strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        value = value[1:end] if end > 0 else value[1:]
    else:
        value = value.split(";")[0].strip()
    return value.encode("latin-1")


def _part_name(head):
    """The `name="..."` of a part's Content-Disposition header ("" if cut)."""
    marker = b'name="'
    index = head.find(marker)
    if index < 0:
        return ""
    rest = head[index + len(marker):]
    end = rest.find(b'"')
    return rest[:end].decode("utf-8", "replace") if end >= 0 else ""


def _multipart_parts(content_type, body):
    """[(name, content_bytes)] for a multipart/form-data body.

    `body` may be a truncated prefix of a large upload (the handler parses a
    prefix to learn the chat_id early): the final part then ends wherever the
    prefix did. Only a complete body may be used for byte accounting.
    """
    boundary = _boundary_of(content_type)
    if not boundary:
        return []
    parts = []
    for raw in body.split(b"--" + boundary)[1:]:
        if raw.startswith(b"--"):
            break                                   # closing delimiter
        raw = raw[2:] if raw.startswith(b"\r\n") else raw
        head, separator, content = raw.partition(b"\r\n\r\n")
        if not separator:
            continue                                # headers still incomplete
        if content.endswith(b"\r\n"):
            content = content[:-2]                  # CRLF before the next boundary
        parts.append((_part_name(head), content))
    return parts


def _chat_id_from_form(content_type, body):
    """The chat_id value in a (possibly truncated) form body, or None."""
    lowered = (content_type or "").lower()
    if "multipart/form-data" in lowered:
        for name, value in _multipart_parts(content_type, body):
            if name == "chat_id":
                return value.decode("utf-8", "replace").strip()
        return None
    if "application/x-www-form-urlencoded" in lowered:
        values = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        if values.get("chat_id"):
            return str(values["chat_id"][0]).strip()
    return None


def _refuse_live_dir(path):
    """This project's hard rule: no test writes into the live data directory."""
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not local:
        return
    live = os.path.normcase(os.path.join(os.path.abspath(local), "irica"))
    candidate = os.path.normcase(os.path.abspath(path))
    if candidate == live or candidate.startswith(live + os.sep):
        raise ValueError("refusing to write into the live data directory")


class _FakeServer(http.server.ThreadingHTTPServer):
    """Throwaway loopback server: one thread per connection, no logging."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """Silence the per-request traceback for a client that gave up first.

        A destination whose budget ran out aborts mid-upload; that is the case
        this fake exists to create, so it must not print a traceback into every
        test's output. Anything that is not a connection-level error is still
        reported by the stdlib.
        """
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    """Serves the two Bot API calls the production notifier makes."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):   # noqa: A003 - stdlib signature
        """Silence: the stdlib default writes one line per request to stderr."""
        return

    # -- timing ------------------------------------------------------------
    def _pause(self, fake, seconds):
        """Sleep in short slices so stop() never waits on a sleeping handler."""
        if not seconds:
            return
        end = time.monotonic() + float(seconds)
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0 or fake._stopping.is_set():
                return
            time.sleep(min(remaining, 0.05))

    def _read_prefix(self, content_type, length):
        """Read until the chat_id shows up (or the peek limit is reached).

        Returns (body_bytes, chat_id_or_None). The chat_id is read from the
        multipart prefix because `delay` must be spent before the upload body is
        consumed -- that is what keeps a client writing a large photo blocked.
        """
        body = b""
        chat_id = None
        while len(body) < min(length, _PEEK_LIMIT):
            chunk = self.rfile.read(min(_PEEK_CHUNK, length - len(body)))
            if not chunk:
                break
            body += chunk
            chat_id = _chat_id_from_form(content_type, body)
            if chat_id is not None:
                break
        return body, chat_id

    def _read_rest(self, body, length, chat_id, fake):
        """Consume the rest of the upload, paced by `slow_read` when it applies."""
        slow = fake._slow_chunk(chat_id)
        while len(body) < length:
            chunk = self.rfile.read(min(slow or _DEFAULT_READ_CHUNK, length - len(body)))
            if not chunk:
                break
            body += chunk
            if slow:
                self._pause(fake, fake.SLOW_READ_SLEEP_SEC)
        return body

    def _respond(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (ConnectionError, OSError):
            # The client gave up first (delay/blackhole past its own timeout):
            # the connection is dead, so do not try to read another request.
            self.close_connection = True

    def _wait_forever(self, fake):
        """Answer nothing: the client must reach its own timeout."""
        end = time.monotonic() + _BLACKHOLE_MAX_WAIT
        while time.monotonic() < end and not fake._stopping.is_set():
            time.sleep(0.05)
        self.close_connection = True

    # -- request -----------------------------------------------------------
    def do_POST(self):
        fake = self.server.fake
        started = time.monotonic()
        content_type = self.headers.get("Content-Type") or ""
        length = int(self.headers.get("Content-Length") or 0)
        path = self.path.split("?", 1)[0]
        method = path.rstrip("/").rsplit("/", 1)[-1] or "unknown"

        body, early = self._read_prefix(content_type, length)
        # Log the attempt as soon as the destination is known: a request whose
        # upload is cut short (the caller's budget spent) is still an attempt.
        record, index = fake._open_call(early or "", method, 0, started)
        if early:
            self._pause(fake, fake._delay_seconds(early))
        body = self._read_rest(body, length, early, fake)

        parts = _multipart_parts(content_type, body)
        chat_id = early
        if not chat_id:
            for name, value in parts:
                if name == "chat_id":
                    chat_id = value.decode("utf-8", "replace").strip()
                    break
            if chat_id:
                # Unusual part ordering (the photo before the chat_id): the
                # delay is still honoured, just after the upload was consumed.
                self._pause(fake, fake._delay_seconds(chat_id))
        photo_bytes = 0
        for name, value in parts:
            if name == "photo":
                photo_bytes = len(value)
                break
        chat_id = chat_id or ""
        fake._update_call(record, chat_id, photo_bytes)

        status, payload = fake._decide(chat_id, method, index)
        if status is None:
            self._wait_forever(fake)
        else:
            self._respond(status, payload)
        fake._close_call(record)


class FakeTelegram:
    """A local Bot API. Start it, point a config at `api_base`, then assert.

    See the module docstring for the timing knobs (`delay`, `slow_read`) and the
    `mode` values. Counters survive a stop()/start() pair; `reset()` clears
    them without dropping the server.
    """

    #: Seconds slept between `slow_read` chunks.
    SLOW_READ_SLEEP_SEC = 0.02

    def __init__(self, *, delay=None, mode="ok", slow_read=None):
        self._delay = {str(k): float(v) for k, v in (delay or {}).items()}
        self._slow_read = {str(k): int(v) for k, v in (slow_read or {}).items()}
        if isinstance(mode, str) and mode not in _MODES:
            raise ValueError(f"unknown mode: {mode!r} (expected one of {_MODES} or a callable)")
        self._mode = mode
        self._requests = []
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._server = None
        self._thread = None
        self._port = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Bind ("127.0.0.1", 0) and serve in a background thread."""
        if self._server is not None:
            return self
        self._stopping = threading.Event()
        server = _FakeServer(("127.0.0.1", 0), _FakeHandler)
        server.fake = self
        self._server = server
        self._port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever,
                                        name="fake-telegram", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Stop serving and close the listening socket (safe when not started)."""
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is None:
            return
        self._stopping.set()            # release blackholed/delaying handlers
        try:
            server.shutdown()
        finally:
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- addresses and recordings -----------------------------------------
    @property
    def port(self):
        """The ephemeral port actually bound (valid after stop() too)."""
        if self._port is None:
            raise RuntimeError("FakeTelegram.start() has not run yet")
        return self._port

    @property
    def api_base(self):
        """`http://127.0.0.1:<port>` -- no trailing slash."""
        return f"http://127.0.0.1:{self.port}"

    def counts(self):
        """{chat_id: number of requests served}.

        A request is counted as soon as its destination is known -- before its
        upload is consumed -- so a caller that gives up mid-upload (budget
        spent) still shows up as one attempt.
        """
        counts = {}
        for record in self.calls():
            counts[record["chat_id"]] = counts.get(record["chat_id"], 0) + 1
        return counts

    def calls(self):
        """[{"chat_id", "method", "photo_bytes", "started", "finished"}] (copies).

        `finished` is None while the request is still being served, and
        `photo_bytes` is 0 when the upload was cut short.
        """
        with self._lock:
            return [dict(record) for record in self._requests]

    def reset(self):
        """Clear the counters and the call log; the server stays up."""
        with self._lock:
            self._requests = []

    def write_config(self, path, chat_ids, token="TEST:token"):
        """Write a telegram_config.json pointing at this fake; return its path.

        `path` may be the file to write or an existing directory (the file is
        then `telegram_config.json` inside it). The live data directory is
        refused outright.
        """
        target = os.path.abspath(str(path))
        if os.path.isdir(target):
            target = os.path.join(target, "telegram_config.json")
        _refuse_live_dir(target)
        config = {"api_base": self.api_base,
                  "bot_token": token,
                  "chat_ids": [str(c).strip() for c in chat_ids if str(c).strip()]}
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        return target

    # -- internals ---------------------------------------------------------
    def _delay_seconds(self, chat_id):
        return self._delay.get(str(chat_id), 0.0) if chat_id else 0.0

    def _slow_chunk(self, chat_id):
        value = self._slow_read.get(str(chat_id)) if chat_id else None
        return int(value) if value else None

    def _open_call(self, chat_id, method, photo_bytes, started):
        record = {"chat_id": chat_id, "method": method, "photo_bytes": photo_bytes,
                  "started": started, "finished": None}
        with self._lock:
            self._requests.append(record)
            index = len(self._requests) - 1
        return record, index

    def _close_call(self, record):
        with self._lock:
            record["finished"] = time.monotonic()

    def _update_call(self, record, chat_id, photo_bytes):
        """Fill in what the body turned out to carry.

        `photo_bytes` stays 0 when the caller gave up before the photo part was
        read to the end; the attempt itself is already logged by then.
        """
        with self._lock:
            if chat_id:
                record["chat_id"] = chat_id
            record["photo_bytes"] = photo_bytes

    def _decide(self, chat_id, method, seen_index):
        """(status_code, payload) for one request; (None, ...) = answer nothing."""
        mode = self._mode
        if callable(mode):
            answer = mode(chat_id, method, seen_index)
            if not answer:
                return 200, None
            status, payload = answer
            return (None if status is None else int(status)), payload
        if mode == "chat_not_found":
            return 400, {"ok": False, "description": "Bad Request: chat not found"}
        if mode == "server_error":
            return 500, {"ok": False, "description": "Internal Server Error"}
        if mode == "blackhole":
            return None, None
        return 200, {"ok": True, "result": {"message_id": seen_index + 1}}


def make_receipt_png(path, size_bytes=1_415_000):
    """A deterministic stand-in for the ~1.4 MB receipt screenshot.

    Real bytes, no binary in the repository: the PNG signature followed by
    `size_bytes - 8` bytes from ``random.Random(1234)``, so two calls produce
    files of exactly `size_bytes` bytes with identical content. It is an upload
    payload, not a decodable image -- never hand it to an image library.
    """
    if size_bytes < len(_PNG_SIGNATURE):
        raise ValueError("size_bytes is smaller than the PNG signature")
    payload = random.Random(1234).randbytes(size_bytes - len(_PNG_SIGNATURE))
    with open(path, "wb") as handle:
        handle.write(_PNG_SIGNATURE)
        handle.write(payload)
    return path


def temp_data_dir():
    """A throwaway data directory, exported as IRICA_DATA_DIR unless already set.

    Call this at import time of a test module, BEFORE importing app_paths /
    telegram_notify / gomrok: ``app_paths.data_dir()`` caches its first result
    forever, so a late override would send the process into the operator's live
    directory. ``setdefault`` keeps whatever a test runner already exported.
    """
    path = tempfile.mkdtemp(prefix="irica_tg_")
    os.environ.setdefault("IRICA_DATA_DIR", path)
    return path


def use_config(module, cfg_path):
    """Point `module._CFG_PATH` at a test config; return a restore callable.

    `module` is telegram_notify (the global is read at call time). Pass the
    returned callable to `testcase.addCleanup(...)` so the previous value --
    or the absence of the attribute -- comes back.
    """
    previous = getattr(module, "_CFG_PATH", _MISSING)
    module._CFG_PATH = cfg_path

    def restore():
        if previous is _MISSING:
            try:
                del module._CFG_PATH
            except AttributeError:
                pass
        else:
            module._CFG_PATH = previous

    return restore


def _self_check():
    """The module's own acceptance run: `python lab/tests/_telegram_fake.py`."""
    import shutil
    import socket
    import types

    import requests

    scratch = tempfile.mkdtemp(prefix="irica_tg_selfcheck_")
    failures = []

    def check(name, fn):
        try:
            fn()
        except Exception as exc:        # noqa: BLE001 - report and keep going
            failures.append(name)
            print(f"FAIL {name}: {exc!r}")
        else:
            print(f"PASS {name}")

    def upload(fake, chat_id, photo=b"\x89PNG\r\n\x1a\n" + b"r" * 64,
               method="sendPhoto", timeout=10):
        """POST exactly the way the production notifier does."""
        url = f"{fake.api_base}/botTEST:token/{method}"
        if method == "sendMessage":
            return requests.post(url, data={"chat_id": chat_id, "text": "hi"},
                                 timeout=timeout)
        return requests.post(url, data={"chat_id": chat_id, "caption": "c"},
                             files={"photo": ("receipt.png", photo)}, timeout=timeout)

    def binds_loopback_ephemeral():
        with FakeTelegram() as fake:
            assert fake.api_base == f"http://127.0.0.1:{fake.port}", fake.api_base
            host, port = fake._server.server_address[:2]
            assert host == "127.0.0.1", host
            assert 0 < port < 65536, port
            with socket.create_connection(("127.0.0.1", fake.port), timeout=2):
                pass                    # something really listens there
            assert upload(fake, "111").status_code == 200

    def two_chat_ids_counted_separately():
        with FakeTelegram() as fake:
            for chat_id in ("111", "222"):
                response = upload(fake, chat_id)
                assert response.status_code == 200, response.status_code
                body = response.json()
                assert body["ok"] and body["result"]["message_id"], body
            assert fake.counts() == {"111": 1, "222": 1}, fake.counts()
            calls = fake.calls()
            assert [call["method"] for call in calls] == ["sendPhoto", "sendPhoto"], calls
            assert [call["photo_bytes"] for call in calls] == [72, 72], calls
            assert all(call["finished"] >= call["started"] for call in calls), calls

    def per_chat_delay_is_observed():
        with FakeTelegram(delay={"333": 0.5}) as fake:
            started = time.monotonic()
            assert upload(fake, "333").status_code == 200
            elapsed = time.monotonic() - started
            assert elapsed >= 0.5, elapsed
            started = time.monotonic()
            assert upload(fake, "444").status_code == 200
            assert time.monotonic() - started < 0.5, "an undelayed chat slept"

    def same_chat_twice_yields_two():
        with FakeTelegram() as fake:
            upload(fake, "555")
            upload(fake, "555")
            assert fake.counts() == {"555": 2}, fake.counts()

    def chat_not_found_answers_400():
        with FakeTelegram(mode="chat_not_found") as fake:
            response = upload(fake, "666")
            assert response.status_code == 400, response.status_code
            assert response.json() == {"ok": False,
                                       "description": "Bad Request: chat not found"}, \
                response.json()

    def server_error_answers_500():
        with FakeTelegram(mode="server_error") as fake:
            response = upload(fake, "667")
            assert response.status_code == 500, response.status_code
            assert response.json().get("ok") is False, response.json()

    def blackhole_answers_nothing():
        fake = FakeTelegram(mode="blackhole").start()
        started = time.monotonic()
        try:
            upload(fake, "777", timeout=(1, 1))
        except requests.exceptions.Timeout:
            assert time.monotonic() - started >= 1.0
        else:
            raise AssertionError("blackhole answered")
        finally:
            stopped = time.monotonic()
            fake.stop()
            assert time.monotonic() - stopped < 5.0, "stop() waited on the handler"
        assert fake.counts() == {"777": 1}, fake.counts()

    def two_start_stop_cycles():
        fake = FakeTelegram()
        for cycle in (1, 2):
            fake.start()
            try:
                assert upload(fake, "888").json()["ok"], cycle
            finally:
                fake.stop()
        assert fake.counts() == {"888": 2}, fake.counts()

    def message_bodies_are_counted():
        """sendMessage carries no files, so requests posts an urlencoded form."""
        with FakeTelegram() as fake:
            assert upload(fake, "999", method="sendMessage").status_code == 200
            assert fake.counts() == {"999": 1}, fake.counts()
            call = fake.calls()[0]
            assert call["method"] == "sendMessage", call
            assert call["photo_bytes"] == 0, call

    def callable_mode_scripts_requests():
        seen = []

        def mode(chat_id, method, index):
            seen.append((chat_id, method, index))
            return (429, {"ok": False, "description": "Too Many Requests"}) if index == 0 \
                else (200, {"ok": True, "result": {"message_id": index + 1}})

        with FakeTelegram(mode=mode) as fake:
            assert upload(fake, "1234").status_code == 429
            assert upload(fake, "1234").status_code == 200
            assert seen == [("1234", "sendPhoto", 0), ("1234", "sendPhoto", 1)], seen

    def receipt_png_is_deterministic():
        first = make_receipt_png(os.path.join(scratch, "a.png"))
        second = make_receipt_png(os.path.join(scratch, "b.png"))
        assert os.path.getsize(first) == 1_415_000, os.path.getsize(first)
        with open(first, "rb") as handle:
            assert handle.read(8) == b"\x89PNG\r\n\x1a\n"
        with open(first, "rb") as left, open(second, "rb") as right:
            assert left.read() == right.read(), "not deterministic"

    def slow_read_paces_a_large_upload():
        receipt = make_receipt_png(os.path.join(scratch, "receipt.png"))
        with open(receipt, "rb") as handle:
            photo = handle.read()
        with FakeTelegram(slow_read={"1010": 131072}) as fake:
            started = time.monotonic()
            assert upload(fake, "1010", photo=photo, timeout=60).status_code == 200
            elapsed = time.monotonic() - started
            assert fake.calls()[0]["photo_bytes"] == 1_415_000, fake.calls()
            # 1_415_000 / 131072 = 11 chunks, 20 ms each
            assert elapsed >= 0.2, elapsed

    def write_config_points_at_the_fake():
        with FakeTelegram() as fake:
            target = os.path.join(scratch, "telegram_config.json")
            assert fake.write_config(target, ["111", "222"]) == target
            with open(target, encoding="utf-8") as handle:
                config = json.load(handle)
            assert config["api_base"] == fake.api_base, config
            assert config["bot_token"] == "TEST:token", config
            assert config["chat_ids"] == ["111", "222"], config

    def use_config_restores_the_module_global():
        holder = types.SimpleNamespace()
        target = os.path.join(scratch, "cfg.json")
        restore = use_config(holder, target)
        assert holder._CFG_PATH == target
        restore()
        assert not hasattr(holder, "_CFG_PATH"), holder.__dict__

    def temp_data_dir_is_isolated():
        before = os.environ.get("IRICA_DATA_DIR")
        path = temp_data_dir()
        assert os.path.isdir(path), path
        assert os.path.basename(path).startswith("irica_tg_"), path
        assert os.environ["IRICA_DATA_DIR"] == (before or path), os.environ["IRICA_DATA_DIR"]

    checks = [
        ("binds 127.0.0.1 on an ephemeral port", binds_loopback_ephemeral),
        ("two chat_ids in one body counted separately", two_chat_ids_counted_separately),
        ("per-chat delay of 0.5 s is observed", per_chat_delay_is_observed),
        ("the same chat_id twice yields 2", same_chat_twice_yields_two),
        ("chat_not_found answers 400 chat not found", chat_not_found_answers_400),
        ("server_error answers 500", server_error_answers_500),
        ("blackhole answers nothing, client times out", blackhole_answers_nothing),
        ("two start()/stop() cycles both succeed", two_start_stop_cycles),
        ("urlencoded sendMessage bodies are counted", message_bodies_are_counted),
        ("callable mode scripts per-request statuses", callable_mode_scripts_requests),
        ("make_receipt_png is deterministic and sized", receipt_png_is_deterministic),
        ("slow_read paces a 1.4 MB upload", slow_read_paces_a_large_upload),
        ("write_config writes the fake endpoint", write_config_points_at_the_fake),
        ("use_config restores the module global", use_config_restores_the_module_global),
        ("temp_data_dir is a fresh irica_tg_ directory", temp_data_dir_is_isolated),
    ]
    try:
        for name, fn in checks:
            check(name, fn)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"{len(checks) - len(failures)} of {len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_self_check())
