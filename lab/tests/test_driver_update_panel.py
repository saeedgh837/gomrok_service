"""The launch page's driver-update button and its /driver-update route.

The button is the manual repair for a Chrome update that left the bundled
ChromeDriver a major behind: the operator asks explicitly, so the request must
reach captcha_collector's manual entry point (which ignores the automatic
attempt's latch and cooldown) and the verdict must come back to the page. Runs
the real handler over HTTP with isolated stores; the updater itself is stubbed.
"""
import json
import os
import re
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

import captcha_collector
import panel


class DriverUpdateHarness(unittest.TestCase):
    """Real handler on an ephemeral port, isolated stores, authenticated."""

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

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
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

    def _post(self, path):
        request = urllib.request.Request(self._url(path), data=b"{}",
                                         method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))


class LaunchPageButtonTests(DriverUpdateHarness):
    def test_the_page_offers_the_button_and_a_place_for_the_verdict(self):
        html = self._page("/launch")
        self.assertIn('id="btn-driver"', html)
        self.assertIn("به‌روزرسانی درایور مرورگر", html)
        self.assertIn('id="drv-note"', html)
        self.assertIn("/driver-update", html)

    def test_the_button_sits_before_the_system_close_button(self):
        html = self._page("/launch")
        self.assertLess(html.index('id="btn-driver"'), html.index('id="btn-stop"'))

    def test_the_page_can_render_every_verdict(self):
        html = self._page("/launch")
        for status in ("updated", "up-to-date", "disabled", "no-browser-version",
                       "busy", "driver-in-use"):
            # Each status must be a real row of the verdict table with a
            # non-empty message and a colour, not merely a word that happens to
            # occur on the page. A bare assertIn would pass if the row were
            # deleted and the token still appeared somewhere incidental, and
            # the button would then report a status it has no text for.
            self.assertRegex(
                html,
                "'" + re.escape(status)
                + r"':\['[^']+','var\(--[a-z]+\)'\]")
        # Anything else (failed, an unexpected status) must still say something
        # useful rather than leaving an empty line behind.
        self.assertIn("به‌روزرسانی درایور ناموفق بود؛ اتصال پروکسی را بررسی کنید",
                      html)
        self.assertIn("ارتباط با سرویس برقرار نشد", html)


class DriverUpdateRouteTests(DriverUpdateHarness):
    def test_the_route_reports_the_collector_verdict(self):
        verdict = {"status": "updated", "detail": "installed chromedriver 154",
                   "target": "C:\\x\\chromedriver.exe", "version": "154.0.8037.92"}
        with patch.object(captcha_collector, "update_chromedriver_now",
                          return_value=verdict) as call:
            status, body = self._post("/driver-update")
        self.assertEqual(status, 200)
        self.assertEqual(body, verdict)
        call.assert_called_once_with()

    def test_a_failed_verdict_still_answers_200_so_the_page_can_report_it(self):
        with patch.object(captcha_collector, "update_chromedriver_now",
                          return_value={"status": "failed",
                                        "detail": "proxy 127.0.0.1:18081 is not "
                                                  "available",
                                        "target": "t", "version": ""}):
            status, body = self._post("/driver-update")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "failed")

    def test_a_raising_collector_still_answers_json(self):
        """The page reads the verdict: an exception must still arrive as a
        status it understands, never as an HTML error body it cannot parse."""
        with patch.object(captcha_collector, "update_chromedriver_now",
                          side_effect=RuntimeError("boom")):
            status, body = self._post("/driver-update")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "failed")
        self.assertIn("boom", body["detail"])

    def test_an_unreadable_store_does_not_hide_the_verdict(self):
        """Repairing the driver has nothing to do with profiles.json. Were the
        route behind load_store(), a store fault would answer 500 and the page
        would tell the operator to check the proxy — a wrong diagnosis."""
        verdict = {"status": "updated", "detail": "installed chromedriver 154",
                   "target": "C:\\x\\chromedriver.exe", "version": "154.0.8037.92"}
        with patch.object(panel, "load_store",
                          side_effect=panel.StoreUnreadableError("torn")), \
                patch.object(captcha_collector, "update_chromedriver_now",
                             return_value=verdict):
            status, body = self._post("/driver-update")
        self.assertEqual(status, 200)
        self.assertEqual(body, verdict)

    def test_the_route_is_not_open_to_anonymous_callers(self):
        # `self._original` is the un-wrapped urlopen the auth harness handed
        # back: this request carries no session cookie.
        request = urllib.request.Request(self._url("/driver-update"),
                                         data=b"{}", method="POST")
        with patch.object(captcha_collector, "update_chromedriver_now") as call:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self._original(request, timeout=10)
            self.assertEqual(caught.exception.code, 401)
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
