"""Tests for the auto-update hook in captcha_collector.

The hook decides WHICH driver copy to refresh, and must never let a failed
repair break a robot launch. All offline: the updater itself is patched, the
cooldown marker is redirected to a temp file, and nothing touches the live
data dir or the network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import captcha_collector as cc
import chromedriver_updater


# A separate interpreter that holds the byte-range lock exactly the way the hook
# does. In a subprocess so the test proves real cross-process exclusion rather
# than asserting against a mock of it. It announces itself with a sentinel file
# and not with stdout: a child's stdout reaches the parent only when the child
# exits, so a pipe handshake would return after the holder's sleep, by which
# time the lock is already released and the assertion proves nothing.
_LOCK_HOLDER = """
import os, sys, time, msvcrt
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)
msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
with open(sys.argv[3], "w", encoding="utf-8") as ready:
    ready.write("locked")
time.sleep(float(sys.argv[2]))
os.close(fd)
"""


def _hold_lock_in_another_process(test, seconds=30):
    """Start a second process that holds the lock, and return only once that
    process is demonstrably holding it; the caller kills it."""
    ready = os.path.join(test.tmp, "holder_ready")
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", _LOCK_HOLDER,
         cc._driver_update_lock_path(), str(seconds), ready],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    test.addCleanup(child.kill)
    deadline = time.monotonic() + 20
    while not os.path.exists(ready):
        if child.poll() is not None:
            test.fail("the holder process exited before taking the lock: "
                      + child.stderr.read())
        if time.monotonic() > deadline:
            test.fail("the holder process never took the lock")
        time.sleep(0.02)
    return child


class TargetTests(unittest.TestCase):
    def test_extras_cover_bundled_and_existing_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = os.path.join(tmp, ".tools")
            os.makedirs(tools)
            bundled = os.path.join(tools, "chromedriver.exe")
            override = os.path.join(tmp, "root", "chromedriver.exe")
            os.makedirs(os.path.dirname(override))
            for path in (bundled, override):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")

            with patch.object(cc, "HERE", tmp), \
                    patch.object(cc, "CHROMEDRIVER_PATH", override):
                primary, extras = cc._driver_update_targets(override)
            self.assertEqual(primary, override)
            self.assertEqual(extras, (bundled,))

    def test_missing_override_is_never_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = os.path.join(tmp, ".tools")
            os.makedirs(tools)
            bundled = os.path.join(tools, "chromedriver.exe")
            with open(bundled, "w", encoding="utf-8") as handle:
                handle.write("x")
            missing = os.path.join(tmp, "root", "chromedriver.exe")

            with patch.object(cc, "HERE", tmp), \
                    patch.object(cc, "CHROMEDRIVER_PATH", missing):
                primary, extras = cc._driver_update_targets(bundled)
            self.assertEqual(primary, bundled)
            self.assertEqual(extras, ())
            self.assertFalse(os.path.exists(missing))


class HookTests(unittest.TestCase):
    def setUp(self):
        cc._DRIVER_UPDATE_LATCH["done"] = False
        self.tmp = tempfile.mkdtemp(prefix="driver_hook_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.marker = os.path.join(self.tmp, "driver_update_last.json")
        self._patch_marker = patch.object(
            cc, "_driver_update_marker_path", return_value=self.marker)
        self._patch_marker.start()
        self.addCleanup(self._patch_marker.stop)

    def test_attempt_is_latched_to_one_per_process(self):
        calls = []

        def fake_update(**kwargs):
            calls.append(kwargs)
            return {"status": "failed", "detail": "nope", "target": "t",
                    "version": ""}

        with patch.object(chromedriver_updater, "update_chromedriver",
                          side_effect=fake_update):
            cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
            cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(len(calls), 1)

    def test_cooldown_blocks_a_later_attempt(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}' % time.time())
        self.assertTrue(cc._driver_update_cooling_down())
        with patch.object(chromedriver_updater, "update_chromedriver") as upd:
            self.assertIsNone(
                cc.maybe_update_chromedriver("/x/chromedriver.exe", 154))
        upd.assert_not_called()

    def test_a_cooling_down_process_keeps_its_one_attempt(self):
        """Cooling down is not an attempt. If it consumed the per-process latch
        a long-lived caller would never repair the driver once the cooldown
        expired, which is the exact case the latch exists to allow."""
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}' % time.time())
        self.assertIsNone(cc.maybe_update_chromedriver("/x/chromedriver.exe", 154))
        self.assertFalse(cc._DRIVER_UPDATE_LATCH["done"])

        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}'
                         % (time.time() - cc.DRIVER_UPDATE_COOLDOWN_SECONDS - 10))
        with patch.object(chromedriver_updater, "update_chromedriver",
                          return_value={"status": "up-to-date", "detail": "",
                                        "target": "t", "version": "154"}) as upd:
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        upd.assert_called_once()
        self.assertEqual(result["status"], "up-to-date")

    def test_stale_marker_does_not_cool_down(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}'
                         % (time.time() - cc.DRIVER_UPDATE_COOLDOWN_SECONDS - 10))
        self.assertFalse(cc._driver_update_cooling_down())

    def test_no_attempt_without_a_path_or_a_major(self):
        with patch.object(chromedriver_updater, "update_chromedriver") as upd:
            self.assertIsNone(cc.maybe_update_chromedriver("", 154))
            self.assertIsNone(cc.maybe_update_chromedriver("/x/chromedriver.exe", None))
        upd.assert_not_called()

    def test_a_raising_updater_is_reported_not_propagated(self):
        with patch.object(chromedriver_updater, "update_chromedriver",
                          side_effect=RuntimeError("boom")):
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(result["status"], "failed")
        self.assertIn("boom", result["detail"])

    def test_the_automatic_attempt_gets_a_tight_budget(self):
        """The hook runs on the launch path: it must ask the updater for a
        budget well under the updater's own default, or a slow tunnel would
        hold the robot start."""
        calls = []

        def fake_update(**kwargs):
            calls.append(kwargs)
            return {"status": "failed", "detail": "no", "target": "t",
                    "version": ""}

        with patch.object(chromedriver_updater, "update_chromedriver",
                          side_effect=fake_update):
            cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        budget = calls[0]["total"]
        self.assertEqual(budget, cc.DRIVER_UPDATE_AUTO_BUDGET_SECONDS)
        self.assertLess(budget, chromedriver_updater.RETRY_TOTAL_SECONDS)

    def test_the_marker_is_written_atomically(self):
        cc._mark_driver_update_attempt()
        with open(self.marker, encoding="utf-8") as handle:
            self.assertGreater(json.load(handle)["at"], 0)
        # Exactly the marker: the temp file the write stages through is named
        # with the marker as its prefix, so a name-based filter would pass even
        # if the staged file leaked. The directory holds nothing else.
        self.assertEqual(os.listdir(self.tmp), [cc.DRIVER_UPDATE_MARKER])

    def test_a_torn_marker_still_cools_down(self):
        """Half a JSON document must not read as "no attempt ever happened"."""
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": ')
        self.assertTrue(cc._driver_update_cooling_down())

    def test_an_undated_marker_still_cools_down(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.assertTrue(cc._driver_update_cooling_down())

    def test_a_non_object_marker_cools_down_without_raising(self):
        """Valid JSON that is not an object would make .get raise
        AttributeError, and this runs on the robot launch path, which is
        documented never to raise."""
        for body in ("5", "true", '"x"', "[1]"):
            with open(self.marker, "w", encoding="utf-8") as handle:
                handle.write(body)
            self.assertTrue(cc._driver_update_cooling_down(), body)

    def test_a_marker_write_that_fails_leaves_the_old_marker_intact(self):
        """Staging through a temp file is what keeps the next process from
        reading half a document. An in-place truncating write would satisfy the
        listdir check below and still destroy the marker here."""
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": 1.0}')

        def replace_failing(src, dst, *args, **kwargs):
            raise OSError("denied")

        with patch.object(cc.os, "replace", side_effect=replace_failing):
            cc._mark_driver_update_attempt()  # must not raise

        with open(self.marker, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"at": 1.0})
        self.assertEqual(os.listdir(self.tmp), [cc.DRIVER_UPDATE_MARKER])

    def test_an_unwritable_marker_does_not_break_the_attempt(self):
        with patch.object(cc, "_driver_update_marker_path",
                          return_value=os.path.join(self.tmp, "no-such-dir",
                                                    "m.json")):
            cc._mark_driver_update_attempt()  # must not raise


class CheckCompatIntegrationTests(unittest.TestCase):
    def setUp(self):
        cc._DRIVER_UPDATE_LATCH["done"] = False

    def test_warning_is_cleared_when_the_update_succeeds(self):
        majors = iter([153, 154])  # before the swap, then after

        with patch.object(cc, "_chrome_major_from_registry_or_path",
                          return_value=154), \
                patch.object(cc, "_read_major_version",
                             side_effect=lambda _p: next(majors)), \
                patch.object(cc, "maybe_update_chromedriver",
                             return_value={"status": "updated",
                                           "detail": "installed 154",
                                           "target": "t", "version": "154"}):
            problem = cc.check_driver_compat("/x/chromedriver.exe")
        self.assertEqual(problem, "")

    def test_warning_stands_when_the_update_fails(self):
        with patch.object(cc, "_chrome_major_from_registry_or_path",
                          return_value=154), \
                patch.object(cc, "_read_major_version",
                             return_value=153), \
                patch.object(cc, "maybe_update_chromedriver",
                             return_value={"status": "failed",
                                           "detail": "no proxy",
                                           "target": "t", "version": ""}):
            problem = cc.check_driver_compat("/x/chromedriver.exe")
        self.assertIn("154", problem)
        self.assertIn("153", problem)

    def test_matching_driver_never_triggers_an_attempt(self):
        with patch.object(cc, "_chrome_major_from_registry_or_path",
                          return_value=154), \
                patch.object(cc, "_read_major_version", return_value=154), \
                patch.object(cc, "maybe_update_chromedriver") as upd:
            self.assertEqual(cc.check_driver_compat("/x/chromedriver.exe"), "")
        upd.assert_not_called()


class ManualUpdateTests(unittest.TestCase):
    """update_chromedriver_now: the launch page's button, not the launch path.

    A person clicked it and is waiting, so it must not be latched or cooled
    down the way an automatic attempt is, and it must not consume the cooldown
    a later robot start needs.
    """

    def setUp(self):
        cc._DRIVER_UPDATE_LATCH["done"] = False
        self.tmp = tempfile.mkdtemp(prefix="driver_manual_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.marker = os.path.join(self.tmp, "driver_update_last.json")
        self._patch_marker = patch.object(
            cc, "_driver_update_marker_path", return_value=self.marker)
        self._patch_marker.start()
        self.addCleanup(self._patch_marker.stop)
        for name, value in (("_chrome_major_from_registry_or_path", 154),
                            ("resolve_chromedriver", "/x/chromedriver.exe")):
            item = patch.object(cc, name, return_value=value)
            item.start()
            self.addCleanup(item.stop)

    def _call(self, **kwargs):
        with patch.object(chromedriver_updater, "update_chromedriver",
                          **kwargs) as upd:
            result = cc.update_chromedriver_now()
        return result, upd

    def test_manual_update_ignores_the_process_latch(self):
        cc._DRIVER_UPDATE_LATCH["done"] = True
        result, upd = self._call(
            return_value={"status": "updated", "detail": "d", "target": "t",
                          "version": "154.0.1.2"})
        upd.assert_called_once()
        self.assertEqual(result["status"], "updated")

    def test_manual_update_ignores_the_cooldown_and_leaves_its_marker_alone(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}' % time.time())
        before = open(self.marker, encoding="utf-8").read()

        _, upd = self._call(
            return_value={"status": "failed", "detail": "d", "target": "t",
                          "version": ""})

        upd.assert_called_once()
        self.assertEqual(open(self.marker, encoding="utf-8").read(), before)

    def test_manual_update_asks_for_the_installed_chrome_major(self):
        # The resolved path is the override here; the bundled copy is the extra.
        with tempfile.TemporaryDirectory() as tmp:
            tools = os.path.join(tmp, ".tools")
            os.makedirs(tools)
            bundled = os.path.join(tools, "chromedriver.exe")
            override = os.path.join(tmp, "chromedriver.exe")
            for path in (bundled, override):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            with patch.object(cc, "HERE", tmp), \
                    patch.object(cc, "resolve_chromedriver",
                                 return_value=override):
                _, upd = self._call(
                    return_value={"status": "up-to-date", "detail": "",
                                  "target": override, "version": "154.0.1.2"})
        kwargs = upd.call_args.kwargs
        self.assertEqual(kwargs["expected_major"], 154)
        self.assertEqual(kwargs["target_exe"], override)
        self.assertEqual(kwargs["extra_targets"], (bundled,))

    def test_manual_update_defaults_to_the_bundled_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cc, "HERE", tmp), \
                    patch.object(cc, "resolve_chromedriver", return_value=""):
                _, upd = self._call(
                    return_value={"status": "up-to-date", "detail": "",
                                  "target": "", "version": ""})
        self.assertEqual(upd.call_args.kwargs["target_exe"],
                         os.path.join(tmp, ".tools", "chromedriver.exe"))

    def test_manual_update_reports_an_unreadable_browser_version(self):
        with patch.object(cc, "_chrome_major_from_registry_or_path",
                          return_value=None):
            result, upd = self._call()
        upd.assert_not_called()
        self.assertEqual(result["status"], "no-browser-version")

    def test_manual_update_survives_a_raising_updater(self):
        result, _ = self._call(side_effect=RuntimeError("boom"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("boom", result["detail"])

    def test_manual_update_fills_the_version_when_there_was_nothing_to_do(self):
        with patch.object(chromedriver_updater, "update_chromedriver",
                          return_value={"status": "up-to-date", "detail": "",
                                        "target": "t", "version": ""}), \
                patch.object(chromedriver_updater, "read_exe_version",
                             return_value="154.0.8037.92"):
            result = cc.update_chromedriver_now()
        self.assertEqual(result["status"], "up-to-date")
        self.assertEqual(result["version"], "154.0.8037.92")

    @unittest.skipUnless(os.name == "nt", "the lock is an msvcrt byte range")
    def test_the_manual_path_is_refused_while_another_process_holds_the_lock(self):
        """Bypassing the cooldown is about not being refused for having tried
        recently; it is not licence to download alongside a robot that is
        already downloading through the one tunnel."""
        child = _hold_lock_in_another_process(self)
        try:
            with patch.object(chromedriver_updater, "update_chromedriver") as upd:
                result = cc.update_chromedriver_now()
            upd.assert_not_called()
            self.assertEqual(result["status"], "busy")
            self.assertFalse(cc._DRIVER_UPDATE_BUSY.locked())
        finally:
            child.kill()

    def test_a_second_manual_update_is_told_busy(self):
        """Two clicks (or two tabs) must not both pull 11 MB through the one
        tunnel; the second caller is told what is happening, not joined to it."""
        self.assertFalse(cc._DRIVER_UPDATE_BUSY.locked())
        finished = {}
        running = threading.Event()
        release = threading.Event()

        def slow_update(**kwargs):
            running.set()
            release.wait(10)
            return {"status": "updated", "detail": "d", "target": "t",
                    "version": "154.0.1.2"}

        with patch.object(chromedriver_updater, "update_chromedriver",
                          side_effect=slow_update):
            worker = threading.Thread(
                target=lambda: finished.update(cc.update_chromedriver_now()))
            worker.start()
            try:
                self.assertTrue(running.wait(10), "the first update never ran")
                second = cc.update_chromedriver_now()
            finally:
                release.set()
                worker.join(10)
        self.assertEqual(second["status"], "busy")
        self.assertEqual(finished["status"], "updated")
        self.assertFalse(cc._DRIVER_UPDATE_BUSY.locked())


class CrossProcessLockTests(unittest.TestCase):
    """The cooldown marker makes two simultaneous attempts unlikely; this lock
    makes them impossible. It is an OS-held byte range, released the instant the
    holder exits, so a robot killed mid-update can never leave the updater
    wedged the way a lock file with a staleness rule could."""

    def setUp(self):
        cc._DRIVER_UPDATE_LATCH["done"] = False
        self.tmp = tempfile.mkdtemp(prefix="driver_lock_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.marker = os.path.join(self.tmp, "driver_update_last.json")
        self._patch_marker = patch.object(
            cc, "_driver_update_marker_path", return_value=self.marker)
        self._patch_marker.start()
        self.addCleanup(self._patch_marker.stop)

    def test_the_lock_file_lives_beside_the_marker(self):
        """Deriving the lock from the marker path is what keeps a test run from
        creating a lock in the live data directory: redirect the marker and the
        lock follows it."""
        self.assertEqual(cc._driver_update_lock_path(),
                         os.path.join(self.tmp, cc.DRIVER_UPDATE_LOCK))

    @unittest.skipUnless(os.name == "nt", "the lock is an msvcrt byte range")
    def test_a_process_holding_the_lock_blocks_the_attempt(self):
        child = _hold_lock_in_another_process(self)
        try:
            with patch.object(chromedriver_updater, "update_chromedriver") as upd:
                result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
            upd.assert_not_called()
            self.assertIsNone(result)
            # losing the race is not an attempt, so the latch stays free
            self.assertFalse(cc._DRIVER_UPDATE_LATCH["done"])
        finally:
            child.kill()

    @unittest.skipUnless(os.name == "nt", "the lock is an msvcrt byte range")
    def test_the_lock_is_released_when_the_holder_is_killed(self):
        """A robot killed mid-update must not wedge the updater, which is why
        this is a kernel-held byte range rather than a lock file with a
        staleness rule."""
        child = _hold_lock_in_another_process(self)
        child.kill()
        child.wait(10)
        with patch.object(chromedriver_updater, "update_chromedriver",
                          return_value={"status": "up-to-date", "detail": "",
                                        "target": "t", "version": "154"}) as upd:
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        upd.assert_called_once()
        self.assertEqual(result["status"], "up-to-date")

    def test_an_unopenable_lock_file_still_allows_the_attempt(self):
        """An unopenable lock file means no process can hold it. Refusing would
        permanently disable self-repair on a machine whose data directory is
        read-only, and concurrency is safe anyway because every install is
        verified and atomically replaced."""
        unopenable = os.path.join(self.tmp, "no_such_dir", "driver_update.lock")
        with patch.object(cc, "_driver_update_lock_path",
                          return_value=unopenable), \
                patch.object(chromedriver_updater, "update_chromedriver",
                             return_value={"status": "up-to-date", "detail": "",
                                           "target": "t", "version": "154"}) as upd:
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        upd.assert_called_once()
        self.assertEqual(result["status"], "up-to-date")


if __name__ == "__main__":
    unittest.main()
