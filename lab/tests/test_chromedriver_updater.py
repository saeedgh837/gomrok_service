"""Tests for chromedriver_updater — the auto-updater that fetches the driver
matching the installed Chrome from Chrome-for-Testing THROUGH the project's
Cloudflare proxy.

Every test is offline: the zip is built here, the CfT payload is a literal,
and the HTTP layer is an injected fake. No test may touch the network or the
live data dir.
"""
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

import chromedriver_updater as upd


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _make_zip(path, member="chromedriver-win64/chromedriver.exe", payload=b"NEW"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, payload)
    return path


def _zip_bytes(member="chromedriver-win64/chromedriver.exe", payload=b"NEW"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(member, payload)
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, body=b"", status=200, headers=None):
        self.content = body
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise upd.DriverUpdateError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        # http_get_bytes streams the body so it can enforce its deadline
        # between chunks; the double yields the same bytes in chunks.
        for start in range(0, len(self.content), size):
            yield self.content[start:start + size]

    def close(self):
        pass


class FetchRecorder:
    """Stands in for the module's HTTP layer; records every call."""

    def __init__(self, mapping=None, fail_times=0):
        self.mapping = mapping or {}
        self.calls = []
        self.fail_times = fail_times

    def __call__(self, url, proxy, timeout, **kw):
        self.calls.append({"url": url, "proxy": proxy, "timeout": timeout})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("connection reset")
        for key, value in self.mapping.items():
            if url.endswith(key) or key in url:
                # the real fetch returns the body bytes, not a response object
                return value.content if isinstance(value, _FakeResponse) else value
        raise AssertionError(f"unexpected fetch: {url}")


# --------------------------------------------------------------------------
# version parsing / URL construction (pure)
# --------------------------------------------------------------------------

class VersionAndUrlTests(unittest.TestCase):
    def test_major_of_parses_leading_number(self):
        self.assertEqual(upd.major_of("154.0.8037.92"), 154)
        self.assertEqual(upd.major_of("ChromeDriver 153.0.8010.52 (78e5e4)"), 153)
        self.assertEqual(upd.major_of("Chrome 154.0.8037.58"), 154)

    def test_major_of_returns_none_on_junk(self):
        for junk in ("", None, "no digits here", "chrome"):
            self.assertIsNone(upd.major_of(junk))

    def test_version_of_returns_the_full_build(self):
        self.assertEqual(upd.version_of("ChromeDriver 154.0.8037.92 (78e5e4)"),
                         "154.0.8037.92")
        self.assertIsNone(upd.version_of("no version here"))
        self.assertIsNone(upd.version_of(None))

    def test_cft_zip_url_is_deterministic(self):
        self.assertEqual(
            upd.cft_zip_url("154.0.8037.92"),
            "https://storage.googleapis.com/chrome-for-testing-public/"
            "154.0.8037.92/win64/chromedriver-win64.zip")
        self.assertEqual(
            upd.cft_zip_url("153.0.8010.52", platform="win32"),
            "https://storage.googleapis.com/chrome-for-testing-public/"
            "153.0.8010.52/win32/chromedriver-win32.zip")

    def test_cft_zip_url_rejects_empty_version(self):
        with self.assertRaises(ValueError):
            upd.cft_zip_url("")


# --------------------------------------------------------------------------
# reading a driver's own --version (the only unstubbed version source)
# --------------------------------------------------------------------------

class ExeVersionTests(unittest.TestCase):
    """read_exe_major / read_exe_version against the real subprocess call.

    Everything else in this file injects read_major, so these are the tests
    that would catch the parse drifting away from what chromedriver prints —
    and the ones that pin the "never execute something that is not there" and
    "a hung binary must not hang the launch path" behaviours.
    """

    def _exe(self, tmp):
        path = os.path.join(tmp, "chromedriver.exe")
        with open(path, "wb") as handle:
            handle.write(b"binary")
        return path

    def test_version_is_parsed_from_the_binary_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = self._exe(tmp)
            completed = subprocess.CompletedProcess(
                args=[exe, "--version"], returncode=0,
                stdout="ChromeDriver 154.0.8037.92 (78e5e4c9a0)\n", stderr="")
            with patch.object(upd.subprocess, "run",
                              return_value=completed) as run:
                self.assertEqual(upd.read_exe_major(exe), 154)
                self.assertEqual(upd.read_exe_version(exe), "154.0.8037.92")
            run.assert_called_with([exe, "--version"], capture_output=True,
                                   text=True, timeout=upd.VERSION_TIMEOUT)

    def test_a_driver_printing_on_stderr_is_still_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = self._exe(tmp)
            completed = subprocess.CompletedProcess(
                args=[exe, "--version"], returncode=0, stdout="",
                stderr="ChromeDriver 153.0.8010.52 (abc)\n")
            with patch.object(upd.subprocess, "run", return_value=completed):
                self.assertEqual(upd.read_exe_major(exe), 153)

    def test_output_without_a_version_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = self._exe(tmp)
            completed = subprocess.CompletedProcess(
                args=[exe, "--version"], returncode=1, stdout="usage: ...",
                stderr="")
            with patch.object(upd.subprocess, "run", return_value=completed):
                self.assertIsNone(upd.read_exe_major(exe))
                self.assertIsNone(upd.read_exe_version(exe))

    def test_a_binary_that_cannot_run_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = self._exe(tmp)
            with patch.object(upd.subprocess, "run",
                              side_effect=OSError("not executable")):
                self.assertIsNone(upd.read_exe_major(exe))

    def test_a_hung_binary_times_out_instead_of_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = self._exe(tmp)
            with patch.object(
                    upd.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(exe,
                                                          upd.VERSION_TIMEOUT)):
                self.assertIsNone(upd.read_exe_major(exe))

    def test_a_missing_file_is_never_executed(self):
        missing = os.path.join("C:\\does-not-exist", "chromedriver.exe")
        with patch.object(upd.subprocess, "run") as run:
            self.assertIsNone(upd.read_exe_major(missing))
        run.assert_not_called()


# --------------------------------------------------------------------------
# choosing the right CfT version for a given browser major
# --------------------------------------------------------------------------

LAST_KNOWN_GOOD = {
    "timestamp": "2026-09-30T00:00:00Z",
    "channels": {
        "Stable": {"channel": "Stable", "version": "154.0.8037.92"},
        "Canary": {"channel": "Canary", "version": "157.0.8100.1"},
    },
}

MILESTONES = {
    "timestamp": "2026-09-30T00:00:00Z",
    "milestones": {
        "152": {"milestone": "152", "version": "152.0.7977.75"},
        "153": {"milestone": "153", "version": "153.0.8010.52"},
        "154": {"milestone": "154", "version": "154.0.8037.92"},
    },
}


class VersionPickTests(unittest.TestCase):
    def test_last_known_good_matches_stable_major(self):
        self.assertEqual(
            upd.pick_from_last_known_good(LAST_KNOWN_GOOD, 154), "154.0.8037.92")

    def test_last_known_good_rejects_when_major_differs(self):
        # Stable is 154; a machine on Chrome 153 must NOT be handed 154.
        self.assertIsNone(upd.pick_from_last_known_good(LAST_KNOWN_GOOD, 153))

    def test_milestones_picks_the_build_for_that_major(self):
        self.assertEqual(upd.pick_from_milestones(MILESTONES, 153),
                         "153.0.8010.52")

    def test_milestones_returns_none_for_unknown_major(self):
        self.assertIsNone(upd.pick_from_milestones(MILESTONES, 999))

    def test_milestones_ignores_malformed_entries(self):
        payload = {"milestones": {"152": {"version": "152.0.9.9"},
                                  "153": "garbage", "154": None}}
        self.assertEqual(upd.pick_from_milestones(payload, 152), "152.0.9.9")
        self.assertIsNone(upd.pick_from_milestones(payload, 153))
        self.assertIsNone(upd.pick_from_milestones(payload, 154))

    def test_milestones_never_hands_over_another_major(self):
        payload = {"milestones": {"153": {"version": "154.0.1.1"}}}
        self.assertIsNone(upd.pick_from_milestones(payload, 153))


# --------------------------------------------------------------------------
# the proxy is mandatory — there is no silent direct fallback
# --------------------------------------------------------------------------

class ProxyMandatoryTests(unittest.TestCase):
    def test_fetch_refuses_without_a_proxy(self):
        for bad in (None, "", "   "):
            with self.assertRaises(upd.DriverUpdateError) as ctx:
                upd.http_get_bytes("https://example.test/x", proxy=bad,
                                   timeout=5, session=False)
            self.assertIn("proxy", str(ctx.exception).lower())

    def test_proxy_url_shape(self):
        self.assertEqual(upd.proxy_url(), "http://127.0.0.1:18081")
        self.assertEqual(upd.proxy_url(port=9090), "http://127.0.0.1:9090")

    def test_the_fetch_pins_the_proxy_and_ignores_the_environment(self):
        """The proxy is the only route that can work from this host (a direct
        request gets 403 from CfT), so the call must name it explicitly and
        must not let requests widen it from the ambient environment: an
        HTTP(S)_PROXY in the operator's shell could otherwise point the
        download somewhere else entirely. ``stream=True`` is pinned too --
        without it requests buffers the whole body before hand-off and the
        per-chunk deadline check becomes decorative."""
        response = _FakeResponse(b"BODY")
        with patch("requests.Session") as session_cls:
            session = session_cls.return_value
            session.get.return_value = response
            body = upd.http_get_bytes("https://x.test/payload",
                                      proxy="http://127.0.0.1:18081", timeout=5)
        self.assertEqual(body, b"BODY")
        self.assertFalse(session.trust_env,
                         "the session must not honour the ambient proxy env")
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["proxies"],
                         {"http": "http://127.0.0.1:18081",
                          "https": "http://127.0.0.1:18081"})
        self.assertTrue(kwargs["stream"])


# --------------------------------------------------------------------------
# proxy probing
# --------------------------------------------------------------------------

class _FakeConnectProxy:
    """Answers CONNECT requests with a canned status line, and keeps serving.

    The point of the readiness probe is that an open port is not enough, so the
    tests need a socket that listens and yet refuses the tunnel. It must survive
    more than one connection too: the probe connects once just to see the port
    is up before it sends the CONNECT."""

    def __init__(self, reply):
        self.reply = reply
        self.received = b""
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(3.0)
                try:
                    request = conn.recv(256)
                    if request:
                        self.received = request
                        conn.sendall(self.reply)
                except OSError:
                    pass

    def close(self):
        self.srv.close()
        self.thread.join(3.0)


class ProxyProbeTests(unittest.TestCase):
    def test_listening_port_is_detected(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(upd.is_port_listening("127.0.0.1", port,
                                                  timeout=1.0))
        finally:
            srv.close()

    def test_closed_port_is_reported_down(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        self.assertFalse(upd.is_port_listening("127.0.0.1", port, timeout=0.5))

    def test_ready_needs_a_tunnel_not_just_an_open_port(self):
        proxy = _FakeConnectProxy(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        try:
            self.assertTrue(upd.proxy_ready("127.0.0.1", proxy.port, timeout=2.0))
            self.assertTrue(
                proxy.received.startswith(
                    b"CONNECT " + upd.CFT_CATALOGUE_HOST.encode()),
                proxy.received)
        finally:
            proxy.close()

    def test_a_refused_tunnel_is_not_ready(self):
        proxy = _FakeConnectProxy(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        try:
            self.assertFalse(upd.proxy_ready("127.0.0.1", proxy.port, timeout=2.0))
        finally:
            proxy.close()

    def test_a_dead_port_is_not_ready(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        self.assertFalse(upd.proxy_ready("127.0.0.1", port, timeout=0.5))


# --------------------------------------------------------------------------
# bounded retry with backoff
# --------------------------------------------------------------------------

class RetryTests(unittest.TestCase):
    def test_transient_failure_is_retried_then_succeeds(self):
        rec = FetchRecorder(mapping={"payload": _FakeResponse(b"OK")},
                            fail_times=2)
        body = upd.retrying_fetch(rec, "https://x.test/payload",
                                  proxy="http://127.0.0.1:18081",
                                  timeout=5, attempts=4, backoff=0)
        self.assertEqual(body, b"OK")
        self.assertEqual(len(rec.calls), 3)

    def test_retry_is_bounded_and_then_raises(self):
        rec = FetchRecorder(mapping={}, fail_times=99)
        with self.assertRaises(upd.DriverUpdateError):
            upd.retrying_fetch(rec, "https://x.test/payload",
                               proxy="http://127.0.0.1:18081",
                               timeout=5, attempts=3, backoff=0)
        self.assertEqual(len(rec.calls), 3)

    def test_backoff_grows_exponentially_and_is_jittered(self):
        pauses = []
        rec = FetchRecorder(mapping={}, fail_times=99)
        with self.assertRaises(upd.DriverUpdateError):
            upd.retrying_fetch(rec, "https://x.test/payload", proxy="http://p",
                               timeout=5, attempts=4, backoff=2.0, total=0,
                               sleep=pauses.append)
        self.assertEqual(len(pauses), 3)
        for pause, base in zip(pauses, (2.0, 4.0, 8.0)):
            self.assertGreaterEqual(pause, base * 0.5)
            self.assertLessEqual(pause, base * 1.5)
        # jitter must actually vary, or every robot retries in lockstep
        self.assertNotEqual(pauses, [2.0, 4.0, 8.0])

    def test_one_pause_never_exceeds_the_cap(self):
        pauses = []
        rec = FetchRecorder(mapping={}, fail_times=99)
        with self.assertRaises(upd.DriverUpdateError):
            upd.retrying_fetch(rec, "https://x.test/payload", proxy="http://p",
                               timeout=5, attempts=6, backoff=100.0, total=0,
                               sleep=pauses.append)
        self.assertTrue(pauses)
        self.assertTrue(all(p <= upd.BACKOFF_CAP * 1.5 for p in pauses))

    def test_the_whole_sequence_honours_the_elapsed_cap(self):
        clock = {"now": 0.0}
        pauses = []

        def fake_sleep(seconds):
            pauses.append(seconds)
            clock["now"] += seconds

        rec = FetchRecorder(mapping={}, fail_times=99)
        with patch.object(upd.time, "monotonic",
                          side_effect=lambda: clock["now"]):
            with self.assertRaises(upd.DriverUpdateError):
                upd.retrying_fetch(rec, "https://x.test/payload",
                                   proxy="http://p", timeout=5, attempts=5,
                                   backoff=10.0, total=12.0, sleep=fake_sleep)
        # the cap stops the sequence before all five attempts are spent
        self.assertLessEqual(clock["now"], 12.0 + 1e-6)
        self.assertLess(len(rec.calls), 5)

    def test_a_single_request_never_outlives_the_budget(self):
        """A request may not use the nominal timeout once the budget is nearly
        spent, or one slow tunnel call would outlive the launch path's cap."""
        seen = []
        clock = {"now": 0.0}

        def fetch(url, proxy, timeout, **kw):
            seen.append(timeout)
            clock["now"] += 70.0  # this call eats most of the 90s budget
            raise OSError("connection reset")

        with patch.object(upd.time, "monotonic",
                          side_effect=lambda: clock["now"]):
            with self.assertRaises(upd.DriverUpdateError):
                upd.retrying_fetch(fetch, "https://x.test/payload",
                                   proxy="http://p", timeout=30, attempts=4,
                                   backoff=0, total=90, sleep=lambda s: None)
        self.assertEqual(seen, [30, 20])

    def test_a_permanent_status_is_not_retried(self):
        """403 is what this host gets from Chrome-for-Testing without the
        proxy: asking four more times only burns the tunnel."""
        calls = []

        def fetch(url, proxy, timeout, **kw):
            calls.append(url)
            raise upd.DriverUpdateError("HTTP 403 for " + url, status=403)

        with self.assertRaises(upd.DriverUpdateError):
            upd.retrying_fetch(fetch, "https://x.test/payload", proxy="http://p",
                               timeout=5, attempts=5, backoff=0, total=0,
                               sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_the_http_layer_marks_a_non_200_as_permanent(self):
        """The retry loop can only honour the rule above if http_get_bytes
        attaches the status to the error it raises."""
        response = _FakeResponse(b"", status=403,
                                 headers={"Retry-After": "5"})
        with patch("requests.Session") as session:
            session.return_value.get.return_value = response
            with self.assertRaises(upd.DriverUpdateError) as ctx:
                upd.http_get_bytes("https://x.test/payload",
                                   proxy="http://127.0.0.1:18081", timeout=5)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.retry_after, "5")

    def test_a_transient_status_is_retried(self):
        calls = []

        def fetch(url, proxy, timeout, **kw):
            calls.append(url)
            if len(calls) < 3:
                raise upd.DriverUpdateError("HTTP 502 for " + url, status=502)
            return b"OK"

        body = upd.retrying_fetch(fetch, "https://x.test/payload",
                                  proxy="http://p", timeout=5, attempts=5,
                                  backoff=0, total=0, sleep=lambda s: None)
        self.assertEqual(body, b"OK")
        self.assertEqual(len(calls), 3)

    def test_retry_after_pushes_the_pause_out(self):
        pauses = []
        calls = []

        def fetch(url, proxy, timeout, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise upd.DriverUpdateError("HTTP 429 for " + url, status=429,
                                             retry_after="7")
            return b"OK"

        upd.retrying_fetch(fetch, "https://x.test/payload", proxy="http://p",
                           timeout=5, attempts=3, backoff=0.1, total=0,
                           sleep=pauses.append)
        self.assertEqual([round(p, 6) for p in pauses], [7.0])

    def test_an_unparseable_retry_after_falls_back_to_the_backoff(self):
        self.assertIsNone(upd._retry_after_seconds("Wed, 21 Oct 2026 07:28:00"))
        self.assertIsNone(upd._retry_after_seconds(None))
        self.assertEqual(upd._retry_after_seconds(" 12 "), 12.0)


# --------------------------------------------------------------------------
# install: verify the zip, verify the binary, replace atomically
# --------------------------------------------------------------------------

class InstallTests(unittest.TestCase):
    def _target(self, tmp, content=b"OLD"):
        target = os.path.join(tmp, ".tools", "chromedriver.exe")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        return target

    def test_install_replaces_target_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            seen = {}

            def read_major(path):
                seen["path"] = path
                return 154

            upd.install_from_zip(zp, target, expected_major=154,
                                 read_major=read_major)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"NEW")
            self.assertTrue(os.path.isfile(target + ".bak"))
            with open(target + ".bak", "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_install_refuses_wrong_major_and_leaves_target_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            with self.assertRaises(upd.DriverUpdateError):
                upd.install_from_zip(zp, target, expected_major=154,
                                     read_major=lambda p: 153)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")
            self.assertFalse(os.path.isfile(target + ".bak"))

    def test_install_refuses_zip_without_the_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "d.zip"), member="readme.txt")
            with self.assertRaises(upd.DriverUpdateError):
                upd.install_from_zip(zp, target, expected_major=154,
                                     read_major=lambda p: 154)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_install_rejects_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            bad = os.path.join(tmp, "bad.zip")
            with open(bad, "wb") as f:
                f.write(b"this is not a zip")
            with self.assertRaises(upd.DriverUpdateError):
                upd.install_from_zip(bad, target, expected_major=154,
                                     read_major=lambda p: 154)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_install_rejects_an_archive_member_that_escapes_the_target_dir(self):
        """A crafted archive must not be able to write outside the staging
        directory it is unpacked into."""
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "evil.zip"),
                           member="../../chromedriver.exe")
            with self.assertRaises(upd.DriverUpdateError) as ctx:
                upd.install_from_zip(zp, target, expected_major=154,
                                     read_major=lambda p: 154)
            self.assertIn("unsafe", str(ctx.exception))
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")
            self.assertFalse(os.path.isfile(
                os.path.join(os.path.dirname(tmp), "chromedriver.exe")))

    def test_install_rejects_an_absolute_archive_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "evil.zip"),
                           member="C:/Windows/chromedriver.exe")
            with self.assertRaises(upd.DriverUpdateError) as ctx:
                upd.install_from_zip(zp, target, expected_major=154,
                                     read_major=lambda p: 154)
            self.assertIn("unsafe", str(ctx.exception))
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_install_leaves_no_temp_files_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            upd.install_from_zip(zp, target, expected_major=154,
                                 read_major=lambda p: 154)
            leftovers = [n for n in os.listdir(os.path.dirname(target))
                         if n not in ("chromedriver.exe",
                                      "chromedriver.exe.bak")]
            self.assertEqual(leftovers, [])

    def test_install_creates_target_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            upd.install_from_zip(zp, target, expected_major=154,
                                 read_major=lambda p: 154)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"NEW")
            self.assertFalse(os.path.isfile(target + ".bak"))

    def test_install_refuses_another_build_of_the_right_major(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            with self.assertRaises(upd.DriverUpdateError) as ctx:
                upd.install_from_zip(zp, target, expected_major=154,
                                     read_major=lambda p: 154,
                                     expected_version="154.0.8037.92",
                                     read_version=lambda p: "154.0.8037.50")
            self.assertIn("154.0.8037.50", str(ctx.exception))
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")
            self.assertFalse(os.path.isfile(target + ".bak"))

    def test_install_accepts_the_exact_build_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            upd.install_from_zip(zp, target, expected_major=154,
                                 read_major=lambda p: 154,
                                 expected_version="154.0.8037.92",
                                 read_version=lambda p: "154.0.8037.92")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"NEW")

    def test_backup_is_refreshed_with_the_driver_just_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            with open(target + ".bak", "wb") as handle:
                handle.write(b"ANCIENT")
            zp = _make_zip(os.path.join(tmp, "d.zip"))
            upd.install_from_zip(zp, target, expected_major=154,
                                 read_major=lambda p: 154)
            with open(target + ".bak", "rb") as handle:
                self.assertEqual(handle.read(), b"OLD")


# --------------------------------------------------------------------------
# kill switch + full orchestration
# --------------------------------------------------------------------------

class OrchestrationTests(unittest.TestCase):
    def test_kill_switch_disables_the_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")

            def boom(*a, **k):
                raise AssertionError("must not touch the network")

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154,
                fetcher=boom, probe=lambda *a: True, env={"IRICA_NO_DRIVER_UPDATE": "1"})
            self.assertEqual(res["status"], "disabled")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_no_update_when_driver_already_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")

            def boom(*a, **k):
                raise AssertionError("must not touch the network")

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154,
                read_major=lambda p: 154,
                fetcher=boom, probe=lambda *a: True, env={})
            self.assertEqual(res["status"], "up-to-date")

    def test_full_update_succeeds_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            zip_bytes = io.BytesIO()
            with zipfile.ZipFile(zip_bytes, "w") as zf:
                zf.writestr("chromedriver-win64/chromedriver.exe", b"NEW")

            rec = FetchRecorder(mapping={
                upd.CFT_LAST_KNOWN_GOOD_URL: _FakeResponse(
                    json.dumps(LAST_KNOWN_GOOD).encode()),
                "chromedriver-win64.zip": _FakeResponse(zip_bytes.getvalue()),
            })

            def read_major(path):
                # mirrors the real reader: the installed file is the old 153
                # driver, the freshly staged one reports 154.
                with open(path, "rb") as stream:
                    return 154 if stream.read() == b"NEW" else 153

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154,
                read_major=read_major,
                read_version=lambda path: "154.0.8037.92",
                fetcher=rec, probe=lambda *a: True, env={})
            self.assertEqual(res["status"], "updated", res)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"NEW")

    def test_falls_back_to_the_milestone_document(self):
        """Stable is 154, this machine is on 153: the per-milestone document
        must answer, and the big catalogue must never be fetched."""
        rec = FetchRecorder(mapping={
            upd.CFT_LAST_KNOWN_GOOD_URL: _FakeResponse(
                json.dumps(LAST_KNOWN_GOOD).encode()),
            upd.CFT_MILESTONE_URL: _FakeResponse(
                json.dumps(MILESTONES).encode()),
        })
        version = upd._resolve_version(rec, "http://127.0.0.1:18081", 153, 5, 1, 0)
        self.assertEqual(version, "153.0.8010.52")
        self.assertEqual([c["url"] for c in rec.calls],
                         [upd.CFT_LAST_KNOWN_GOOD_URL, upd.CFT_MILESTONE_URL])

    def test_update_reports_failure_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154,
                read_major=lambda p: 153,
                fetcher=FetchRecorder(fail_times=99), probe=lambda *a: True,
                backoff=0, env={})
            self.assertEqual(res["status"], "failed")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_update_reports_no_proxy_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154,
                read_major=lambda p: 153,
                fetcher=FetchRecorder(), probe=lambda *a: False,
                spawn_proxy=lambda *a, **k: False, env={})
            self.assertEqual(res["status"], "failed")
            self.assertIn("proxy", res["detail"].lower())

    def test_a_catalogue_with_only_another_major_is_refused(self):
        """Chrome 153 must never be handed the 154 build just because it is the
        newest thing the catalogue offers."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            rec = FetchRecorder(mapping={
                upd.CFT_LAST_KNOWN_GOOD_URL: _FakeResponse(json.dumps(
                    {"channels": {"Stable": {"version": "154.0.1.1"}}}
                ).encode()),
                upd.CFT_MILESTONE_URL: _FakeResponse(json.dumps(
                    {"milestones": {"152": {"version": "152.0.1.1"}}}
                ).encode()),
            })

            res = upd.update_chromedriver(
                target_exe=target, expected_major=153,
                read_major=lambda p: 152, fetcher=rec,
                probe=lambda *a: True, env={})
            self.assertEqual(res["status"], "failed", res)
            self.assertIn("153", res["detail"])
            self.assertFalse([c for c in rec.calls if "chromedriver-win" in c["url"]])
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")

    def test_every_request_goes_through_the_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            rec = FetchRecorder(mapping={
                upd.CFT_LAST_KNOWN_GOOD_URL: _FakeResponse(
                    json.dumps(LAST_KNOWN_GOOD).encode()),
                "chromedriver-win64.zip": _FakeResponse(_zip_bytes()),
            })

            def read_major(path):
                with open(path, "rb") as stream:
                    return 154 if stream.read() == b"NEW" else 153

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154, read_major=read_major,
                read_version=lambda path: "154.0.8037.92",
                fetcher=rec, probe=lambda *a: True, env={})
            self.assertEqual(res["status"], "updated", res)
            self.assertTrue(rec.calls)
            self.assertEqual({c["proxy"] for c in rec.calls}, {upd.proxy_url()})

    def test_a_locked_driver_is_reported_as_in_use(self):
        """The robots run this very file, and Windows will not let it be
        replaced while they do. That is not a failure the operator can fix by
        retrying the download, so it gets its own status."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            rec = FetchRecorder(mapping={
                upd.CFT_LAST_KNOWN_GOOD_URL: _FakeResponse(
                    json.dumps(LAST_KNOWN_GOOD).encode()),
                "chromedriver-win64.zip": _FakeResponse(_zip_bytes()),
            })

            with patch.object(upd, "install_from_zip",
                              side_effect=PermissionError(
                                  "the process cannot access the file")):
                res = upd.update_chromedriver(
                    target_exe=target, expected_major=154,
                    read_major=lambda p: 153,
                    read_version=lambda p: "153.0.8010.52",
                    fetcher=rec, probe=lambda *a: True, env={})
            self.assertEqual(res["status"], "driver-in-use", res)

    def test_a_read_only_destination_is_not_a_locked_driver(self):
        """WinError 5 (access denied) and WinError 32/33 (sharing or lock
        violation) both surface as PermissionError, and only the second means a
        robot is holding the file. Answering "close the robots" for a read-only
        folder sends the operator after a phantom, so the two are separated
        where the error code is still legible."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "chromedriver.exe")
            with open(target, "wb") as handle:
                handle.write(b"OLD")
            zpath = _make_zip(os.path.join(tmp, "driver.zip"))
            real_replace = os.replace

            def denied(code):
                exc = PermissionError("denied")
                exc.winerror = code
                return exc

            def replace_failing_on_the_live_file(src, dst, *args, **kwargs):
                # Only the replacement of the live driver is made to fail; the
                # .bak staging call before it must still work, or the test would
                # pass on a backup failure instead of the one under test.
                if os.path.abspath(dst) == os.path.abspath(target):
                    raise denied(code)
                return real_replace(src, dst, *args, **kwargs)

            for code, expected in ((5, upd.DriverUpdateError),
                                   (32, PermissionError),
                                   (33, PermissionError)):
                with patch.object(upd.os, "replace",
                                  side_effect=replace_failing_on_the_live_file):
                    with self.assertRaises(expected):
                        upd.install_from_zip(zpath, target, 154, lambda p: 154)

    def test_a_failed_download_is_not_reported_as_a_failed_install(self):
        """The archive never arrived, so naming the install as the failing step
        sends the operator to look at the driver file for a problem that is
        really in the tunnel."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")

            def fetch(url, proxy, timeout, **kw):
                if url.endswith(".zip"):
                    raise OSError("connection reset")
                return json.dumps(LAST_KNOWN_GOOD).encode()

            res = upd.update_chromedriver(
                target_exe=target, expected_major=154,
                read_major=lambda p: 153, fetcher=fetch,
                probe=lambda *a: True, env={}, backoff=0)
            self.assertEqual(res["status"], "failed", res)
            self.assertIn("could not download", res["detail"])

    def test_a_disk_error_is_not_mistaken_for_a_locked_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            rec = FetchRecorder(mapping={
                upd.CFT_LAST_KNOWN_GOOD_URL: _FakeResponse(
                    json.dumps(LAST_KNOWN_GOOD).encode()),
                "chromedriver-win64.zip": _FakeResponse(_zip_bytes()),
            })

            with patch.object(upd, "install_from_zip",
                              side_effect=OSError("no space left on device")):
                res = upd.update_chromedriver(
                    target_exe=target, expected_major=154,
                    read_major=lambda p: 153, fetcher=rec,
                    probe=lambda *a: True, env={})
            self.assertEqual(res["status"], "failed", res)
            self.assertIn("no space", res["detail"])

    def test_a_slow_tunnel_is_cut_off_at_the_budget(self):
        """The launch path passes a small budget; the download must give up
        inside it rather than hold a robot start hostage."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            clock = {"now": 0.0}

            def fetch(url, proxy, timeout, **kw):
                clock["now"] += 60.0
                raise OSError("connection reset")

            with patch.object(upd.time, "monotonic",
                              side_effect=lambda: clock["now"]):
                res = upd.update_chromedriver(
                    target_exe=target, expected_major=154,
                    read_major=lambda p: 153, fetcher=fetch,
                    probe=lambda *a: True, env={}, total=90.0, backoff=0)
            self.assertEqual(res["status"], "failed", res)
            # two 60s requests fit the 90s budget; the loop stops there rather
            # than starting a third one it cannot pay for
            self.assertLessEqual(clock["now"], 120.0)
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"OLD")


    def test_the_download_allowance_is_clamped_to_the_launch_budget(self):
        """The download call site asks for DOWNLOAD_TIMEOUT (180 s), twice the
        launch path's whole 90 s budget. The socket must actually be given
        min(nominal, budget left): without that clamp one stalled read could
        hold a robot start for the nominal allowance on top of the budget it
        was supposed to respect."""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".tools", "chromedriver.exe")
            os.makedirs(os.path.dirname(target))
            with open(target, "wb") as f:
                f.write(b"OLD")
            clock = {"now": 0.0}
            seen = []

            def read_major(path):
                with open(path, "rb") as stream:
                    return 154 if stream.read() == b"NEW" else 153

            def fetch(url, proxy, timeout, **kw):
                seen.append((url, timeout))
                clock["now"] += 1.0  # each call spends a slice of the budget
                if url == upd.CFT_LAST_KNOWN_GOOD_URL:
                    return json.dumps(LAST_KNOWN_GOOD).encode()
                return _zip_bytes()

            with patch.object(upd.time, "monotonic",
                              side_effect=lambda: clock["now"]):
                res = upd.update_chromedriver(
                    target_exe=target, expected_major=154,
                    read_major=read_major,
                    read_version=lambda p: "154.0.8037.92",
                    fetcher=fetch, probe=lambda *a: True, env={}, total=90.0)

            self.assertEqual(res["status"], "updated", res)
            downloads = [t for url, t in seen if url.endswith(".zip")]
            self.assertTrue(downloads, seen)
            for allowance in downloads:
                self.assertLessEqual(allowance, 90.0)
                self.assertLess(allowance, upd.DOWNLOAD_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
