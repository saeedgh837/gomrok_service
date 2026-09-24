# -*- coding: utf-8 -*-
"""v1.9.1 — /estelam-shot: the shot_resolve containment contract and the
route's negative answers.

shot_resolve is the only containment gate between a store field (the one
capture input the panel never recomputes) and an open() call, so the resolve
contract is pinned class by class: an empty field, a real file inside the
root, a traversal that leaves the root from inside it, a sibling directory
whose name merely starts with the root name, the legacy in-release root
(panel.HERE), and a path on another root or drive.

The route half runs over real HTTP against a real ThreadingHTTPServer on an
ephemeral port, because the gate (302 vs 401 vs 404), the response headers
(nosniff) and the log records are all handler-level behaviour: an unknown
service, an unknown device, the two service slots not crossing, an unreadable
file, non-PNG bytes, an unauthenticated navigation and an unauthenticated XHR,
an empty field that must write no record at all, a path outside the roots that
may only log its basename, and the 32-character bound on the logged service
value.

Every test is isolated: temp IRICA_DATA_DIR for the screenshots root, a temp
estelam_store.json, a temp estelam_logs.jsonl (so the operator's live log is
never touched), a temp panel_users.json for the session, and panel.HERE
pointed at a temp directory so no resolve can reach the repository.
"""
import builtins
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_paths  # noqa: E402
import panel  # noqa: E402
import panel_auth  # noqa: E402

#: The 8-byte PNG signature; used to prove a body carries (or does not carry)
#: image bytes rather than merely checking a status code.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG = PNG_MAGIC + bytes(24)
#: A marker that exists only on the login page (its username input).
LOGIN_FORM_MARKER = 'name="username"'


def other_drive(anchor):
    """A drive letter different from `anchor`'s, for the mixed-drive case."""
    current = (os.path.splitdrive(anchor)[0] or "C:").upper()
    for candidate in ("D:", "E:", "Z:", "C:"):
        if candidate != current:
            return candidate
    return "C:"


class ResolveIsolation(unittest.TestCase):
    """Temp DATA_DIR + a temp legacy HERE, so shot_resolve can only ever see
    directories this test created."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.legacy = tempfile.TemporaryDirectory()
        self.addCleanup(self.legacy.cleanup)
        self.elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(self.elsewhere.cleanup)
        self._patches = [
            patch.dict(os.environ, {"IRICA_DATA_DIR": self.tmp.name}),
            # panel.HERE is the legacy in-release screenshots root and is read
            # at call time, so patching the module global is enough.
            patch.object(panel, "HERE", self.legacy.name),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        app_paths.reset_cache()
        self.addCleanup(app_paths.reset_cache)
        # data_subdir CREATES the directory, so it may only run once the
        # IRICA_DATA_DIR patch is active.
        self.shot_dir = app_paths.data_subdir("screenshots")

    def write(self, path, data=PNG):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path


class ShotResolveTests(ResolveIsolation):
    """The containment contract of shot_resolve itself (no HTTP)."""

    def test_empty_and_blank_fields_resolve_to_nothing(self):
        """None and "" are rejected before resolving: realpath("") is the CWD,
        which must never look like a resolvable capture."""
        for raw in ("", None, "   "):
            self.assertEqual(panel.shot_resolve(raw), "", repr(raw))

    def test_a_file_inside_the_data_screenshots_root_resolves(self):
        shot = self.write(os.path.join(self.shot_dir,
                                       "estelam_gomrok_fixture.png"))
        self.assertEqual(panel.shot_resolve(shot), os.path.realpath(shot))

    def test_a_traversal_out_of_the_root_is_refused_although_it_exists(self):
        """The escape target really is on disk, so the refusal is purely the
        containment check and not a missing file."""
        escape = self.write(os.path.join(self.tmp.name, "escape.png"))
        self.assertTrue(os.path.isfile(escape))
        inside = os.path.join(self.shot_dir, "..", "escape.png")
        self.assertEqual(panel.shot_resolve(inside), "")
        # ... and the same escape from a nested subdirectory
        nested = os.path.join(self.shot_dir, "sub", "..", "..", "escape.png")
        self.assertEqual(panel.shot_resolve(nested), "")

    def test_a_sibling_directory_sharing_the_root_prefix_is_refused(self):
        """The prefix-string trap: commonpath is component-aware, so a sibling
        named like the root plus a suffix is not "inside" it."""
        sibling = self.write(os.path.join(self.tmp.name, "screenshots_evil",
                                          "x.png"))
        self.assertTrue(os.path.isfile(sibling))
        self.assertEqual(panel.shot_resolve(sibling), "")
        self.assertFalse(panel.shot_resolve(sibling))

    def test_the_legacy_release_root_resolves_while_HERE_points_at_it(self):
        """Owners of an older release recorded paths inside the release
        folder; that root is accepted as long as panel.HERE says so."""
        legacy = self.write(os.path.join(self.legacy.name, "screenshots",
                                         "estelam_hamta_legacy.png"))
        self.assertEqual(panel.shot_resolve(legacy), os.path.realpath(legacy))
        with patch.object(panel, "HERE", self.elsewhere.name):
            self.assertEqual(panel.shot_resolve(legacy), "")

    def test_an_existing_path_on_another_root_is_refused(self):
        """A real system file is still outside both roots; no exception may
        escape and no path may be returned."""
        system_file = r"C:\Windows\win.ini"
        self.assertTrue(os.path.isfile(system_file))
        self.assertEqual(panel.shot_resolve(system_file), "")

    def test_a_path_on_another_drive_is_refused_without_raising(self):
        """commonpath raises ValueError for mixed drives; the resolver catches
        it and answers "", it does not propagate."""
        foreign = other_drive(self.tmp.name) + r"\irica_probe\nope.png"
        self.assertEqual(panel.shot_resolve(foreign), "")

    def test_a_directory_resolves_but_is_not_a_file(self):
        """Containment and file-ness are separate checks: the root directory
        is contained, so it resolves -- the route's os.path.isfile() rejection
        of it is pinned in the route class below."""
        self.assertEqual(panel.shot_resolve(self.shot_dir),
                         os.path.realpath(self.shot_dir))
        self.assertTrue(os.path.isdir(panel.shot_resolve(self.shot_dir)))
        self.assertFalse(os.path.isfile(panel.shot_resolve(self.shot_dir)))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urlopen at the first 3xx so the gate's raw answer is visible."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ShotRouteTests(unittest.TestCase):
    """Real HTTP against panel.Handler with throwaway stores."""

    @classmethod
    def setUpClass(cls):
        # Seed the session users once: seeding is PBKDF2-heavy (3 accounts).
        # USERS_PATH lives in the data directory, which is NOT redirectable by
        # IRICA_DATA_DIR alone (it is an import-time constant), so it is
        # patched explicitly to keep the operator's file untouched.
        cls._users_tmp = tempfile.TemporaryDirectory()
        cls._users_patch = patch.object(
            panel_auth, "USERS_PATH",
            os.path.join(cls._users_tmp.name, "panel_users.json"))
        cls._users_patch.start()
        from lab.tests import _auth_harness
        cls.token = _auth_harness.seed_test_users()
        cls.addClassCleanup(cls._users_patch.stop)
        cls.addClassCleanup(cls._users_tmp.cleanup)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = os.path.join(self.tmp.name, "estelam_store.json")
        self.logs = os.path.join(self.tmp.name, "estelam_logs.jsonl")
        with open(self.store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        self._patches = [
            patch.dict(os.environ, {"IRICA_DATA_DIR": self.tmp.name}),
            patch.object(panel, "ESTELAM_PATH", self.store),
            patch.object(panel, "ESTELAM_LOGS_PATH", self.logs),
            patch.object(panel, "HERE", self.tmp.name),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        app_paths.reset_cache()
        self.addCleanup(app_paths.reset_cache)
        self.shot_dir = app_paths.data_subdir("screenshots")

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # cleanup order is LIFO: register server_close first so shutdown()
        # stops serve_forever BEFORE the listening socket is closed (closing
        # it first makes the serving thread die with WinError 10038).
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    # -- helpers ---------------------------------------------------------
    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def get(self, path, headers=None, anonymous=False, follow=True):
        """One GET, returning (status, body, headers)."""
        request = urllib.request.Request(self.url(path))
        if not anonymous:
            request.add_header("Cookie", "irica_session=" + self.token)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        opener = urllib.request.build_opener(NoRedirect) if not follow \
            else urllib.request.build_opener()
        try:
            with opener.open(request, timeout=10) as response:
                return (response.status, response.read(),
                        dict(response.headers), response.url)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers), error.url

    def shot(self, device_id, service):
        return self.get("/estelam-shot?id=%s&svc=%s" % (device_id, service))

    def add_device(self, **fields):
        """An obvious fixture device; no real serial or identity data."""
        record = panel.estelam_add_device({"name": "fixture-phone",
                                           "serial1": "000000000000000"})
        if fields:
            panel.estelam_set_device(record["id"], **fields)
        return record

    def write_shot(self, name="estelam_gomrok_fixture.png", data=PNG):
        path = os.path.join(self.shot_dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def log_records(self):
        if not os.path.exists(self.logs):
            return []
        with open(self.logs, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    # -- negatives -------------------------------------------------------
    def test_an_unknown_service_is_a_404_and_one_log_record(self):
        record = self.add_device(gomrok_screenshot=self.write_shot())
        status, body, headers, _ = self.shot(record["id"], "other")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        records = self.log_records()
        self.assertEqual(len(records), 1)
        self.assertIn("unknown service", records[0]["message"])
        self.assertEqual(records[0]["status"], "shot")

    def test_a_missing_svc_is_a_404_and_one_log_record(self):
        record = self.add_device(gomrok_screenshot=self.write_shot())
        status, body, _, _ = self.get("/estelam-shot?id=" + record["id"])
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 1)
        self.assertIn("unknown service", records[0]["message"])

    def test_an_unknown_device_is_a_404_and_one_log_record(self):
        status, body, _, _ = self.shot("no-such-device", "hamta")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 1)
        self.assertIn("unknown device", records[0]["message"])

    def test_the_two_service_slots_do_not_cross(self):
        """A device whose ONLY capture is the hamta one must serve nothing
        when asked for gomrok: the slots are keyed independently, and the
        gomrok request must not even log about the hamta path."""
        hamta = self.write_shot("estelam_hamta_fixture.png")
        record = self.add_device(hamta_screenshot=hamta)

        status, body, _, _ = self.shot(record["id"], "gomrok")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        self.assertNotIn(PNG_MAGIC[:4], body)
        # an empty gomrok field is a normal state, not a diagnostic
        self.assertEqual(self.log_records(), [])

        status, body, headers, _ = self.shot(record["id"], "hamta")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(PNG_MAGIC))
        self.assertEqual(body, PNG)
        self.assertEqual(headers["Content-Type"], "image/png")

    def test_an_unreadable_file_is_a_404_and_the_server_stays_alive(self):
        target = self.write_shot("estelam_gomrok_unreadable.png")
        record = self.add_device(gomrok_screenshot=target)
        real_open = builtins.open

        def failing_open(file, *args, **kwargs):
            if os.path.abspath(str(file)) == os.path.realpath(target):
                raise OSError(13, "Permission denied")
            return real_open(file, *args, **kwargs)

        with patch.object(builtins, "open", failing_open):
            status, body, _, _ = self.shot(record["id"], "gomrok")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 1)
        self.assertIn("unreadable", records[0]["message"])
        self.assertIn(os.path.basename(target), records[0]["message"])

        # the handler's exception path must not have killed the connection
        status, body, _, _ = self.shot(record["id"], "hamta")
        self.assertEqual(status, 404)
        self.assertIn(b"not found", body)

    def test_a_directory_in_the_root_is_not_served(self):
        """shot_resolve returns the directory (it is contained), so the
        os.path.isfile() check is what refuses it here."""
        record = self.add_device(gomrok_screenshot=self.shot_dir)
        status, body, _, _ = self.shot(record["id"], "gomrok")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        self.assertEqual(body, b"not found")

    def test_non_png_bytes_are_served_as_png_with_nosniff(self):
        """The route does not sniff: bytes from the root are served as
        image/png whatever they contain. nosniff is exactly the mitigation --
        the browser must not re-interpret them as HTML."""
        target = self.write_shot("estelam_gomrok_notpng.png", b"not a png")
        record = self.add_device(gomrok_screenshot=target)
        status, body, headers, _ = self.shot(record["id"], "gomrok")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"not a png")
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_a_served_capture_carries_nosniff_and_the_png_type(self):
        target = self.write_shot("estelam_hamta_ok.png")
        record = self.add_device(hamta_screenshot=target)
        status, body, headers, _ = self.shot(record["id"], "hamta")
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        # no-store is for HTML only; an image may be cached by the browser
        self.assertNotIn("Cache-Control", headers)

    def test_an_empty_field_is_a_404_and_writes_no_log_record(self):
        """Log-eviction guard: a capture-less device is a normal state, and a
        browser retrying the missing image must not flood estelam_logs.jsonl
        (which keeps only its newest records)."""
        record = self.add_device()
        for service in ("gomrok", "hamta"):
            status, body, _, _ = self.shot(record["id"], service)
            self.assertEqual(status, 404)
            self.assertNotIn(PNG_MAGIC, body)
        self.assertEqual(self.log_records(), [])

    def test_a_path_outside_the_roots_logs_the_basename_only(self):
        outside = os.path.join(self.tmp.name, "outside.png")
        with open(outside, "wb") as handle:
            handle.write(PNG)
        record = self.add_device(gomrok_screenshot=outside)
        status, body, _, _ = self.shot(record["id"], "gomrok")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 1)
        message = records[0]["message"]
        self.assertIn(os.path.basename(outside), message)
        # the panel renders this log: no install or data path may be copied in
        self.assertNotIn(os.path.dirname(outside), message)
        self.assertNotIn(self.tmp.name, message)

    def test_a_long_junk_svc_value_is_bounded_in_the_log(self):
        """The service value comes straight from the query string, and the log
        keeps only its newest records, so it is truncated before logging."""
        junk = "z" * 500
        status, body, _, _ = self.get("/estelam-shot?id=x&svc=" + junk)
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 1)
        message = records[0]["message"]
        self.assertIn("unknown service", message)
        self.assertIn(repr(junk[:32]), message)
        self.assertNotIn(repr(junk), message)
        self.assertLess(len(message), 100)

    def test_a_long_junk_device_id_is_bounded_in_the_log(self):
        """The id comes from the query string too, and it is used IN FULL for
        the store lookup, so it is bounded on the way into the log. The panel
        renders estelam_logs.jsonl and the file keeps only its newest records,
        so one request carrying a huge id would otherwise evict the real robot
        diagnostics. The bound is asserted as a property -- the recorded text
        must not grow with the input -- rather than as a single magic length,
        so retuning the number deliberately still passes this test."""
        head = "junk-id-" + "9" * 56            # the bound: the first 64 chars
        junk_id = head + "x" * 444              # 508 characters
        longer_id = head + "y" * 2000           # 2064 characters, same prefix

        # 1. an unknown device: the id ends up inside the message
        status, body, _, _ = self.get(
            "/estelam-shot?id=" + junk_id + "&svc=gomrok")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 1)
        message = records[0]["message"]
        self.assertIn("unknown device", message)
        self.assertIn(repr(head), message)
        self.assertNotIn(repr(junk_id), message)

        # 2. the same request with a much longer id records exactly the same
        # text: the log cannot grow with the input past the bound
        status, body, _, _ = self.get(
            "/estelam-shot?id=" + longer_id + "&svc=gomrok")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["message"], message)
        self.assertLessEqual(len(message), len(head) + 64)
        self.assertLess(len(message), len(junk_id))

        # 3. the unknown-service record carries the id in its own field
        status, body, _, _ = self.get(
            "/estelam-shot?id=" + junk_id + "&svc=other")
        self.assertEqual(status, 404)
        self.assertNotIn(PNG_MAGIC, body)
        records = self.log_records()
        self.assertEqual(len(records), 3)
        carried = [r for r in records if r["device_id"]]
        self.assertEqual(len(carried), 1)
        record = carried[0]
        self.assertIn("unknown service", record["message"])
        self.assertEqual(record["device_id"], junk_id[:64])
        self.assertNotEqual(record["device_id"], junk_id)
        self.assertLessEqual(len(record["device_id"]), len(head))

    # -- the login gate --------------------------------------------------
    def test_an_unauthenticated_navigation_lands_on_the_login_page(self):
        """Plain urllib sends no Accept header, so the gate treats it as a
        browser navigation and 302s to /login, which urllib then follows: the
        body must be the login page and must carry no image bytes."""
        target = self.write_shot("estelam_gomrok_private.png")
        record = self.add_device(gomrok_screenshot=target)
        status, body, headers, final_url = self.get(
            "/estelam-shot?id=%s&svc=gomrok" % record["id"], anonymous=True)
        self.assertEqual(status, 200)
        self.assertTrue(final_url.endswith("/login"), final_url)
        self.assertIn(LOGIN_FORM_MARKER, body.decode("utf-8"))
        self.assertNotIn(PNG_MAGIC, body)
        self.assertNotEqual(body, PNG)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))

    def test_an_unauthenticated_navigation_raw_answer_is_a_302_to_login(self):
        """The same request with redirects disabled: the gate's own answer."""
        target = self.write_shot("estelam_gomrok_private.png")
        record = self.add_device(gomrok_screenshot=target)
        status, body, headers, _ = self.get(
            "/estelam-shot?id=%s&svc=gomrok" % record["id"], anonymous=True,
            follow=False)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/login")
        self.assertNotIn(PNG_MAGIC, body)

    def test_an_unauthenticated_xhr_gets_a_json_401(self):
        """Accept: */* is page-JS fetch, which must get a JSON 401 so the page
        can show its own message instead of a login page body."""
        target = self.write_shot("estelam_gomrok_private.png")
        record = self.add_device(gomrok_screenshot=target)
        status, body, headers, _ = self.get(
            "/estelam-shot?id=%s&svc=gomrok" % record["id"],
            headers={"Accept": "*/*"}, anonymous=True)
        self.assertEqual(status, 401)
        self.assertNotIn(PNG_MAGIC, body)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        payload = json.loads(body.decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"])
        # and nothing was served from the store on the way
        self.assertNotEqual(body, PNG)

    def test_a_capture_is_never_served_without_a_session(self):
        """A second, independent confirmation that the file bytes stay out of
        an unauthenticated response body."""
        target = self.write_shot("estelam_hamta_private.png")
        record = self.add_device(hamta_screenshot=target)
        for headers in ({}, {"Accept": "*/*"}):
            status, body, _, _ = self.get(
                "/estelam-shot?id=%s&svc=hamta" % record["id"],
                headers=headers, anonymous=True)
            self.assertIn(status, (200, 401))
            self.assertNotIn(PNG_MAGIC, body)
            self.assertNotIn(b"fixture", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
