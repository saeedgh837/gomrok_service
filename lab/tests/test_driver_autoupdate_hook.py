"""Read-only compatibility hooks and explicitly confirmed manual updates.

All updater calls are patched, marker/lock files are temporary, and no test
starts a real driver, proxy or external connection.
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
from unittest.mock import Mock, patch

import captcha_collector as cc
import chromedriver_updater


# This fixed scratch-only fixture runs the public updater with a synthetic
# blocked download, proving cross-process exclusion without installing a driver.
def _hold_lock_in_another_process(test, primary=None, extras=()):
    primary = primary or test.target
    ready = os.path.join(test.tmp, "holder_ready_" + str(time.monotonic_ns()))
    manifest = ready + ".json"
    data_root = os.path.join(test.tmp, "other_data_root")
    os.makedirs(data_root, exist_ok=True)
    with open(manifest, "w", encoding="utf-8") as stream:
        json.dump({"primary": primary, "extras": list(extras),
                   "data_root": data_root}, stream)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", os.path.abspath(__file__),
         "--driver-lock-worker", "hold", manifest, ready],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    test.addCleanup(child.kill)
    deadline = time.monotonic() + 10
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

    def test_read_only_checks_never_consume_the_process_latch(self):
        with patch.object(cc, "_read_major_version", return_value=153), \
                patch.object(chromedriver_updater, "update_chromedriver") as upd:
            first = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
            second = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(first["status"], "consent-required")
        self.assertEqual(second["status"], "consent-required")
        self.assertFalse(cc._DRIVER_UPDATE_LATCH["done"])
        upd.assert_not_called()

    def test_cooldown_does_not_hide_the_consent_requirement(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}' % time.time())
        self.assertTrue(cc._driver_update_cooling_down())
        with patch.object(chromedriver_updater, "update_chromedriver") as upd:
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(result["status"], "consent-required")
        upd.assert_not_called()

    def test_cooldown_expiry_never_authorizes_a_later_install(self):
        with patch.object(chromedriver_updater, "update_chromedriver") as upd:
            for age in (0, cc.DRIVER_UPDATE_COOLDOWN_SECONDS + 10):
                with open(self.marker, "w", encoding="utf-8") as handle:
                    handle.write('{"at": %f}' % (time.time() - age))
                result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
                self.assertEqual(result["status"], "consent-required")
                self.assertFalse(cc._DRIVER_UPDATE_LATCH["done"])
        upd.assert_not_called()

    def test_stale_marker_does_not_cool_down(self):
        with open(self.marker, "w", encoding="utf-8") as handle:
            handle.write('{"at": %f}'
                         % (time.time() - cc.DRIVER_UPDATE_COOLDOWN_SECONDS - 10))
        self.assertFalse(cc._driver_update_cooling_down())

    def test_no_install_without_a_path_or_a_major(self):
        with patch.object(chromedriver_updater, "update_chromedriver") as upd, \
                patch.object(cc, "_read_major_version", return_value=154):
            missing = cc.maybe_update_chromedriver("", 154)
            unknown = cc.maybe_update_chromedriver("/x/chromedriver.exe", None)
        self.assertEqual(missing["status"], "consent-required")
        self.assertEqual(unknown["status"], "no-browser-version")
        upd.assert_not_called()

    def test_a_raising_updater_is_never_called_from_the_hook(self):
        with patch.object(chromedriver_updater, "update_chromedriver",
                          side_effect=RuntimeError("boom")) as upd:
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(result["status"], "consent-required")
        upd.assert_not_called()

    def test_a_missing_updater_module_does_not_affect_read_only_checks(self):
        with patch.dict(sys.modules, {"chromedriver_updater": None}):
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(result["status"], "consent-required")
        self.assertIn("confirm", result["detail"].lower())
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(cc._driver_update_cooling_down())

    def test_the_launch_hook_never_spends_a_download_budget(self):
        with patch.object(chromedriver_updater, "update_chromedriver") as upd, \
                patch.object(chromedriver_updater, "_spawn_proxy_process") as spawn:
            result = cc.maybe_update_chromedriver("/x/chromedriver.exe", 154)
        self.assertEqual(result["status"], "consent-required")
        upd.assert_not_called()
        spawn.assert_not_called()
        self.assertEqual(os.listdir(self.tmp), [])

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

    def test_warning_cannot_be_cleared_by_an_unapproved_update(self):
        with patch.object(cc, "_chrome_major_from_registry_or_path",
                          return_value=154), \
                patch.object(cc, "_read_major_version", return_value=153) as read, \
                patch.object(chromedriver_updater, "update_chromedriver") as upd:
            problem = cc.check_driver_compat("/x/chromedriver.exe")
        self.assertIn("153", problem)
        self.assertIn("154", problem)
        self.assertIn("confirm", problem.lower())
        self.assertTrue(problem.isascii())
        upd.assert_not_called()
        self.assertLessEqual(read.call_count, 2)

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
    """The confirmed manual action ignores obsolete automatic cooldown/latches.

    Each call carries its own explicit consent and shares one installation lock.
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
        self.target = os.path.join(self.tmp, "chromedriver.exe")
        for name, value in (("_chrome_major_from_registry_or_path", 154),
                            ("resolve_chromedriver", self.target),
                            ("HERE", self.tmp)):
            item = (patch.object(cc, name, value) if name == "HERE"
                    else patch.object(cc, name, return_value=value))
            item.start()
            self.addCleanup(item.stop)

    def _call(self, **kwargs):
        with patch.object(chromedriver_updater, "update_chromedriver",
                          **kwargs) as upd:
            result = cc.update_chromedriver_now(consent=True)
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
            result = cc.update_chromedriver_now(consent=True)
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
                result = cc.update_chromedriver_now(consent=True)
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
                target=lambda: finished.update(cc.update_chromedriver_now(consent=True)))
            worker.start()
            try:
                self.assertTrue(running.wait(10), "the first update never ran")
                second = cc.update_chromedriver_now(consent=True)
            finally:
                release.set()
                worker.join(10)
        self.assertEqual(second["status"], "busy")
        self.assertEqual(finished["status"], "updated")
        self.assertFalse(cc._DRIVER_UPDATE_BUSY.locked())


class CrossProcessLockTests(unittest.TestCase):
    """Resource-scoped kernel exclusion spans independent application data roots."""

    def setUp(self):
        cc._DRIVER_UPDATE_LATCH["done"] = False
        self.tmp = tempfile.mkdtemp(prefix="driver_lock_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = os.path.join(self.tmp, "chromedriver.exe")
        self.extra = os.path.join(self.tmp, "shared_chromedriver.exe")
        data_root = os.path.join(self.tmp, "parent_data_root")
        os.makedirs(data_root)
        environment = patch.dict(os.environ, {"IRICA_DATA_DIR": data_root})
        environment.start()
        self.addCleanup(environment.stop)
        self.marker = os.path.join(data_root, "driver_update_last.json")
        for name, options in (
                ("_driver_update_marker_path", {"return_value": self.marker}),
                ("_chrome_major_from_registry_or_path", {"return_value": 154}),
                ("resolve_chromedriver", {"return_value": self.target})):
            item = patch.object(cc, name, **options)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(cc, "HERE", self.tmp)
        item.start()
        self.addCleanup(item.stop)

    def test_lock_names_are_canonical_deduplicated_and_ordered(self):
        alias = os.path.join(self.tmp, ".", "CHROMEDRIVER.EXE")
        first = cc._driver_update_mutex_names(self.target, (self.extra, alias))
        second = cc._driver_update_mutex_names(self.extra, (self.target,))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual(first, tuple(sorted(first)))
        self.assertTrue(all(name.startswith("Local\\IRICA_ChromeDriver_") for name in first))
        self.assertNotIn(self.tmp, " ".join(first))

    @unittest.skipUnless(os.name == "nt", "Windows named-mutex test")
    def test_a_process_in_another_data_root_blocks_the_same_target(self):
        child = _hold_lock_in_another_process(self)
        try:
            with patch.object(chromedriver_updater, "update_chromedriver") as upd:
                result = cc.update_chromedriver_now(self.target, consent=True)
            upd.assert_not_called()
            self.assertEqual(result["status"], "busy")
            self.assertFalse(cc._DRIVER_UPDATE_LATCH["done"])
            self.assertFalse(cc._DRIVER_UPDATE_BUSY.locked())
        finally:
            child.kill()
            child.wait(10)

    @unittest.skipUnless(os.name == "nt", "Windows named-mutex test")
    def test_a_shared_secondary_target_blocks_another_primary(self):
        other = os.path.join(self.tmp, "other_primary.exe")
        child = _hold_lock_in_another_process(self, other, (self.extra,))
        try:
            with patch.object(cc, "_driver_update_targets",
                              return_value=(self.target, (self.extra,))), \
                    patch.object(chromedriver_updater, "update_chromedriver") as upd:
                result = cc.update_chromedriver_now(self.target, consent=True)
            upd.assert_not_called()
            self.assertEqual(result["status"], "busy")
        finally:
            child.kill()
            child.wait(10)
        # Refusal after acquiring a primary must release that partial lock set.
        with patch.object(chromedriver_updater, "update_chromedriver", return_value={
                "status": "updated", "detail": "synthetic", "version": "154.0.1.2",
                "target": self.target}) as upd:
            result = cc.update_chromedriver_now(self.target, consent=True)
        upd.assert_called_once()
        self.assertEqual(result["status"], "updated")

    @unittest.skipUnless(os.name == "nt", "Windows named-mutex test")
    def test_the_lock_is_released_when_the_holder_is_killed(self):
        child = _hold_lock_in_another_process(self)
        child.kill()
        child.wait(10)
        with patch.object(chromedriver_updater, "update_chromedriver",
                          return_value={"status": "up-to-date", "detail": "",
                                        "target": "t", "version": "154"}) as upd:
            result = cc.update_chromedriver_now(self.target, consent=True)
        upd.assert_called_once()
        self.assertEqual(result["status"], "up-to-date")

    def test_unavailable_kernel_lock_refuses_the_confirmed_install(self):
        with patch.object(cc, "_driver_update_mutex_api", side_effect=OSError("denied")), \
                patch.object(chromedriver_updater, "update_chromedriver") as upd:
            result = cc.update_chromedriver_now(self.target, consent=True)
        upd.assert_not_called()
        self.assertEqual(result["status"], "busy")
        self.assertFalse(cc._DRIVER_UPDATE_BUSY.locked())

    def test_partial_acquisition_failure_releases_every_handle(self):
        api = Mock()
        api.CreateMutexW.side_effect = [101, 202]
        api.WaitForSingleObject.side_effect = [0, 0x102]
        with patch.object(cc, "_driver_update_mutex_api", return_value=api):
            with cc._driver_update_single_flight(self.target, (self.extra,)) as acquired:
                self.assertFalse(acquired)
        api.ReleaseMutex.assert_called_once_with(101)
        self.assertEqual([call.args[0] for call in api.CloseHandle.call_args_list], [202, 101])

    def test_a_fixed_target_snapshot_is_used_for_locking_and_installation(self):
        targets = (self.target, (self.extra,))
        with patch.object(cc, "_driver_update_targets", return_value=targets) as resolve, \
                patch.object(cc, "_driver_update_single_flight") as lock, \
                patch.object(chromedriver_updater, "update_chromedriver", return_value={
                    "status": "updated", "detail": "synthetic", "version": "154.0.1.2",
                    "target": self.target}) as upd:
            lock.return_value.__enter__.return_value = True
            result = cc.update_chromedriver_now(self.target, consent=True)
        self.assertEqual(result["status"], "updated")
        resolve.assert_called_once_with(self.target)
        lock.assert_called_once_with(*targets)
        self.assertEqual(upd.call_args.kwargs["target_exe"], targets[0])
        self.assertEqual(upd.call_args.kwargs["extra_targets"], targets[1])


def _driver_lock_worker():
    """A fixed isolated fixture: no network, executable launch or installation."""
    if len(sys.argv) != 5 or sys.argv[2] != "hold":
        raise SystemExit("Invalid driver-lock fixture arguments")
    manifest_path, ready = map(os.path.abspath, sys.argv[3:5])
    scratch = os.path.dirname(manifest_path)

    def fixture_path(path):
        path = os.path.abspath(path)
        if os.path.commonpath((scratch, path)) != scratch:
            raise SystemExit("Driver-lock fixtures must remain in their scratch directory")
        return path

    ready = fixture_path(ready)
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    primary = fixture_path(manifest["primary"])
    extras = tuple(fixture_path(path) for path in manifest["extras"])
    data_root = fixture_path(manifest["data_root"])
    if len(extras) > 2:
        raise SystemExit("Too many driver-lock fixture targets")

    def hold(**kwargs):
        with open(ready, "w", encoding="utf-8") as stream:
            stream.write("locked")
        sys.stdin.read(1)
        return {"status": "updated", "detail": "synthetic", "target": primary,
                "version": "154.0.1.2"}

    with patch.dict(os.environ, {"IRICA_DATA_DIR": data_root}), \
            patch.object(cc, "_driver_update_marker_path",
                         return_value=os.path.join(data_root, "driver_update_last.json")), \
            patch.object(cc, "_chrome_major_from_registry_or_path", return_value=154), \
            patch.object(cc, "_driver_update_targets", return_value=(primary, extras)), \
            patch.object(chromedriver_updater, "update_chromedriver", side_effect=hold):
        result = cc.update_chromedriver_now(primary, consent=True)
    if result["status"] != "updated":
        raise SystemExit("The synthetic driver update did not acquire its resource locks")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--driver-lock-worker":
        _driver_lock_worker()
    else:
        unittest.main()
