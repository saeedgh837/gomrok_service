"""Review-only launch navigation and authenticated consent-bound driver routes.

Both executable route names require a live session, CSRF and an exact current
opaque offer with boolean consent. The real HTTP/body/auth path is exercised
with synthetic stores; only the maintenance service boundary is stubbed.
"""
import json
import os
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

# Serving the real handler means the real app_paths: data_dir() is cached on
# its first call and its fallback is the operator's live %LOCALAPPDATA%\irica.
# The runner sets IRICA_DATA_DIR to a scratch directory; refusing to import
# without it keeps a direct `python lab/tests/test_driver_update_panel.py`
# from writing into live data.
if not os.environ.get("IRICA_DATA_DIR"):
    raise RuntimeError(
        "test_driver_update_panel serves the live handler: run it through "
        "lab/scripts/run_panel_tests.py, which points IRICA_DATA_DIR at a "
        "throwaway directory")

import panel


class _BodyRecordingHandler(panel.Handler):
    """panel.Handler that remembers what each stage saw of the request body.

    The drain happens in do_POST before any route runs, which makes _gate the
    first place a body can be observed at all. Reading it there is how the test
    proves the ORDER instead of trusting it.
    """
    recorded = {}

    def _gate(self, route):
        self.recorded["at_gate"] = self._request_body
        return super()._gate(route)

    def _do_post_body(self):
        self.recorded["at_route"] = self._request_body
        self.recorded["parsed"] = self._read_json()
        return super()._do_post_body()


def _raw_post(port, head, tail=b""):
    """POST over a bare socket and return (status line, response body).

    urllib sends the body it declared, and both cases that need this helper are
    about a body the client does not deliver: one declared too large to accept
    and one that stops partway. Only a raw socket can make either claim.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw:
        raw.sendall(head + tail)
        stream = raw.makefile("rb")
        status = stream.readline()
        length = 0
        while True:
            line = stream.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip() or 0)
        return status, stream.read(length)


class DriverUpdateHarness(unittest.TestCase):
    """Real handler on an ephemeral port, isolated stores, authenticated."""

    HANDLER = panel.Handler

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        for name, value in (
                ("PROFILES_PATH", self.profiles),
                ("INPUTS_PATH", os.path.join(self.tmp.name, "inputs.json")),
                ("_STORE_LOCK_PATH", self.profiles + ".lock")):
            item = patch.object(panel, name, value)
            item.start()
            self.addCleanup(item.stop)

        # /launch and /driver-update both sit behind the v1.2.0 login gate.
        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        self._original = _auth_harness.install_auth_cookie(self, self._token)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), self.HANDLER)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        # shutdown() first: closing the socket under a live serve_forever makes
        # the loop thread raise WinError 10038 on Windows.
        self.httpd.shutdown()
        self.httpd.server_close()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _page(self, path):
        with urllib.request.urlopen(self._url(path), timeout=10) as response:
            return response.read().decode("utf-8")

    def _post(self, path, payload=b"{}"):
        request = urllib.request.Request(self._url(path), data=payload,
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read().decode("utf-8"))


class LaunchPageButtonTests(DriverUpdateHarness):
    def test_the_page_offers_browser_update_review_without_installing(self):
        html = self._page("/launch")
        self.assertIn('id="btn-driver"', html)
        self.assertIn("بررسی به‌روزرسانی‌ها", html)
        button = re.search(r'<button\b[^>]*id="btn-driver"[^>]*>', html)
        self.assertIn('lang="fa"', button.group(0))
        self.assertIn('dir="rtl"', button.group(0))
        self.assertNotIn("/driver-update", html)
        self.assertNotIn("/api/update-chromedriver", html)

    def test_the_button_sits_before_the_system_close_button(self):
        html = self._page("/launch")
        self.assertLess(html.index('id="btn-driver"'), html.index('id="btn-stop"'))

    def test_the_review_button_navigates_to_the_authenticated_registration_panel(self):
        html = self._page("/launch")
        button = re.search(r'<button\b[^>]*id="btn-driver"[^>]*>', html)
        self.assertIsNotNone(button)
        handler = re.search(
            r"document\.getElementById\('btn-driver'\)\.onclick\s*=\s*function\(\)\s*\{(.*?)\};",
            html, re.DOTALL)
        navigation = button.group(0) + (handler.group(1) if handler else "")
        self.assertRegex(navigation, r"location\.(?:href\s*=|assign\s*\(|replace\s*\()\s*['\"]/site-1['\"]")
        self.assertNotIn("var msgs=", navigation)
        self.assertNotIn("fetch(", navigation)


class DriverUpdateRouteTests(DriverUpdateHarness):
    OFFER = {"offer_id": "opaque-driver-offer", "consent": True}

    def test_both_routes_submit_exact_offer_and_consent_to_the_service(self):
        accepted = {"ok": True, "driver": {"state": "installing"}}
        for route in ("/driver-update", "/api/update-chromedriver"):
            with self.subTest(route=route), \
                    patch.object(panel, "run_chromedriver_update", return_value=accepted) as install:
                status, body = self._post(route, json.dumps(self.OFFER).encode("utf-8"))
            self.assertEqual(status, 202)
            self.assertEqual(body, accepted)
            install.assert_called_once_with(offer_id=self.OFFER["offer_id"], consent=True)

    def test_a_rejected_or_stale_offer_answers_409_with_service_state(self):
        rejected = {"ok": False, "error": "Check again and confirm the current offer.",
                    "driver": {"state": "not_checked"}}
        with patch.object(panel, "run_chromedriver_update", return_value=rejected) as install:
            status, body = self._post("/driver-update", json.dumps(self.OFFER).encode("utf-8"))
        self.assertEqual(status, 409)
        self.assertEqual(body, rejected)
        install.assert_called_once_with(offer_id=self.OFFER["offer_id"], consent=True)

    def test_a_raising_service_still_answers_sanitized_json(self):
        with patch.object(panel, "run_chromedriver_update",
                          side_effect=RuntimeError("private transport detail")) as install:
            status, body = self._post("/driver-update", json.dumps(self.OFFER).encode("utf-8"))
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertTrue(body["errors"])
        self.assertNotIn("private transport detail", json.dumps(body))
        install.assert_called_once_with(offer_id=self.OFFER["offer_id"], consent=True)

    def test_an_unreadable_profiles_store_does_not_hide_the_update_response(self):
        accepted = {"ok": True, "driver": {"state": "installing"}}
        with patch.object(panel, "load_store", side_effect=panel.StoreUnreadableError("torn")) as store, \
                patch.object(panel, "run_chromedriver_update", return_value=accepted) as install:
            status, body = self._post("/driver-update", json.dumps(self.OFFER).encode("utf-8"))
        self.assertEqual(status, 202)
        self.assertEqual(body, accepted)
        store.assert_not_called()
        install.assert_called_once_with(offer_id=self.OFFER["offer_id"], consent=True)

    def test_the_route_is_not_open_to_anonymous_callers(self):
        request = urllib.request.Request(self._url("/driver-update"),
                                         data=json.dumps(self.OFFER).encode("utf-8"), method="POST")
        with patch.object(panel, "run_chromedriver_update") as install:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._original(request, timeout=10)
            self.assertEqual(caught.exception.code, 401)
        install.assert_not_called()

    def test_invalid_request_shapes_never_reach_the_install_service(self):
        invalid = (b"", b"{}", b"[]", b"{", b'{"probe":true}',
                   b'{"offer_id":"opaque-driver-offer"}',
                   b'{"offer_id":"opaque-driver-offer","consent":false}',
                   b'{"offer_id":"opaque-driver-offer","consent":1}',
                   b'{"offer_id":"","consent":true}',
                   b'{"offer_id":"opaque-driver-offer","consent":true,"url":"https://example.test/driver"}')
        with patch.object(panel, "run_chromedriver_update") as install:
            for route in ("/driver-update", "/api/update-chromedriver"):
                for payload in invalid:
                    with self.subTest(route=route, payload=payload):
                        status, body = self._post(route, payload)
                        self.assertEqual(status, 400, body)
                        self.assertFalse(body["ok"])
                        self.assertTrue(body["errors"])
        install.assert_not_called()

    def test_exact_consent_does_not_bypass_csrf(self):
        request = urllib.request.Request(self._url("/driver-update"),
                                         data=json.dumps(self.OFFER).encode("utf-8"),
                                         headers={"Cookie": "irica_session=" + self._token}, method="POST")
        with patch.object(panel, "run_chromedriver_update") as install:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._original(request, timeout=10)
            self.assertEqual(caught.exception.code, 403)
        install.assert_not_called()

    def test_exact_consent_does_not_bypass_foreign_origin_rejection(self):
        request = urllib.request.Request(self._url("/driver-update"),
                                         data=json.dumps(self.OFFER).encode("utf-8"),
                                         headers={"Origin": "https://foreign.example.test"}, method="POST")
        with patch.object(panel, "run_chromedriver_update") as install:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 403)
        install.assert_not_called()


class BodyDrainTests(DriverUpdateHarness):
    """The POST body is read before any route runs, on every exit.

    A socket closed while it still holds unread request bytes makes Windows send
    RST instead of FIN, and the client's in-flight read dies with
    WSAECONNABORTED -- the browser then shows a raw network error where the
    route had sent a real status. Three exits used to leave those bytes behind
    (the login gate's 401, the /driver-update reply, do_POST's exception
    handler), and the symptom was a flaky test rather than a caught defect.
    """

    HANDLER = _BodyRecordingHandler

    def setUp(self):
        super().setUp()
        _BodyRecordingHandler.recorded = {}

    def test_the_body_is_read_before_the_route_ever_sees_it(self):
        with patch.object(panel, "run_chromedriver_update") as install:
            status, body = self._post("/driver-update", payload=b'{"probe": true}')
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        install.assert_not_called()
        # Already drained when the gate ran, i.e. before any route had a chance
        # to return early and leave the bytes sitting in the socket.
        self.assertEqual(_BodyRecordingHandler.recorded["at_gate"],
                         b'{"probe": true}')
        # ...and the route still gets the body, and gets it parsed: one read
        # shared, not a read that the first consumer swallows.
        self.assertEqual(_BodyRecordingHandler.recorded["at_route"],
                         b'{"probe": true}')
        self.assertEqual(_BodyRecordingHandler.recorded["parsed"],
                         {"probe": True})

    def test_a_rejected_post_is_drained_too(self):
        """The 401 is the path the browser actually reported a network error on:
        the gate answers and returns without ever looking at the body."""
        request = urllib.request.Request(
            self._url("/driver-update"), data=b'{"probe": true}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._original(request, timeout=10)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(_BodyRecordingHandler.recorded["at_gate"],
                         b'{"probe": true}')

    def test_a_bodyless_post_reaches_validation_without_installing(self):
        with patch.object(panel, "run_chromedriver_update") as install:
            status, body = self._post("/driver-update", payload=b"")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        install.assert_not_called()
        self.assertEqual(_BodyRecordingHandler.recorded["at_gate"], b"")
        self.assertEqual(_BodyRecordingHandler.recorded["at_route"], b"")
        self.assertEqual(_BodyRecordingHandler.recorded["parsed"], {})

    def test_an_oversized_body_is_refused_without_being_read(self):
        """Content-Length is the client's claim about its body, not evidence of
        it, and the panel acts on the claim instead of on the bytes: reading
        first is what would let one request declaring a gigabyte pin a worker
        thread and spend the memory to match. Nothing follows the headers here,
        so the answer arriving at all is the proof that nothing was read."""
        status, body = _raw_post(
            self.port,
            b"POST /driver-update HTTP/1.0\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: %d\r\n\r\n" % (panel.MAX_POST_BODY_BYTES + 1))
        self.assertTrue(status.startswith(b"HTTP/1.0 413 "), status)
        self.assertIn("بیش از حد بزرگ", json.loads(body)["errors"][0])

    def test_a_body_that_stops_arriving_is_given_up_on(self):
        """The same claim, taken at its word: a length only partly delivered
        must not hold the thread until the client closes. Refused, not served
        an empty body -- a route that ran on a half-read body would act on
        fields it never received."""
        # The real deadline is 10s; the test's own version of "short" is 0.3s
        # so this stays a unit test rather than a ten-second wait.
        with patch.object(panel, "POST_BODY_READ_TIMEOUT", 0.3):
            status, body = _raw_post(
                self.port,
                b"POST /driver-update HTTP/1.0\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 100\r\n\r\n",
                tail=b'{"probe":')
        self.assertTrue(status.startswith(b"HTTP/1.0 408 "), status)
        self.assertIn("ناتمام ماند", json.loads(body)["errors"][0])


if __name__ == "__main__":
    unittest.main()
