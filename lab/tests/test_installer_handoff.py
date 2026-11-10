"""Offline update handoff tests; no installer is executed or registry changed."""
from __future__ import annotations

from dataclasses import replace
import ctypes
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def _module(test):
    try:
        return importlib.import_module("installer_handoff")
    except ModuleNotFoundError:
        test.fail("The safe installed-update handoff is not implemented")


class _Child:
    def poll(self):
        return None


class _Helper:
    """A same-process fake launcher, using real Windows kernel events only."""
    def __init__(self, native, acknowledge=True):
        self.native = native
        self.acknowledge = acknowledge
        self.commands = []
        self.handles = []
        self.cancel_handle = None

    def __call__(self, command):
        self.commands.append(list(command))
        parameters = dict(value.split("=", 1) for value in command[1:] if "=" in value)
        if self.acknowledge:
            for name in ("/IRICAGATE", "/IRICACANCEL", "/IRICAREADY"):
                handle = self.native.open_event(parameters[name])
                self.handles.append(handle)
                if name == "/IRICACANCEL":
                    self.cancel_handle = handle
                if name == "/IRICAREADY":
                    self.native.set_event(handle)
        return _Child()

    def close(self):
        for handle in self.handles:
            self.native.close(handle)
        self.handles.clear()


@unittest.skipUnless(sys.platform == "win32", "Windows update handoff")
class InstallerHandoffTests(unittest.TestCase):
    def setUp(self):
        self.module = _module(self)
        self.temp = tempfile.TemporaryDirectory(prefix="irica-handoff-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "installed"
        (self.root / "python").mkdir(parents=True)
        for name in ("python.exe", "pythonw.exe"):
            (self.root / "python" / name).write_bytes(b"MZ-fixture-not-executable")
        (self.root / "VERSION").write_text("1.0.0\n", encoding="ascii")
        (self.root / "panel_tray.pyw").write_text("# Fixture only.\n", encoding="ascii")
        (self.root / "unins000.exe").write_bytes(b"MZ-fixture-not-executable")
        self.data = self.base / "data"
        self.data.mkdir()
        self.store = self.data / "fixture.json"
        self.store.write_bytes(b'{"preserve":true}\n')
        stage = self.base / "irica-update-fixture123"
        stage.mkdir()
        self.payload = stage / "IRICA-Setup-v1.1.0.exe"
        self.payload.write_bytes(b"MZ-fixture-never-executed")
        from app_updater import VerifiedOffer
        self.offer = VerifiedOffer(
            offer_id="a" * 43, release_id=12, asset_id=34, version="1.1.0",
            name=self.payload.name, size=self.payload.stat().st_size, sha256="a" * 64,
            url="https://github.com/saeedgh837/gomrok_service/releases/download/v1.1.0/"
                "IRICA-Setup-v1.1.0.exe",
        )
        self.registration = {
            "InstallLocation": str(self.root), "Inno Setup: App Path": str(self.root),
            "Inno Setup: Setup Version": "6.7.1", "DisplayName": "IRICA 1.0.0",
            "DisplayVersion": "1.0.0", "Publisher": "saeedgh837",
            "UninstallString": '"' + str(self.root / "unins000.exe") + '"',
        }
        self.native = self.module._Windows()
        identity = self.native.process_identity(os.getpid())
        self.identity = dict(identity, executable=str(self.root / "python" / "python.exe"))
        from maintenance_gate import MaintenanceGate
        self.gate = MaintenanceGate(install_dir=self.root,
                                    registry_path=self.data / "jobs.json")
        self.helper = _Helper(self.native)
        self.addCleanup(self.helper.close)
        self.shutdown_calls = []
        self.shutdown_result = True

        def guarded_shutdown():
            with self.assertRaisesRegex(RuntimeError, "update"):
                self.gate.assert_admission_open()
            self.shutdown_calls.append("graceful")
            return self.shutdown_result

        self.shutdown = guarded_shutdown
        for target, name, value in (
            (self.module, "__file__", str(self.root / "installer_handoff.py")),
            (self.module.sys, "executable", str(self.root / "python" / "python.exe")),
            (self.native, "process_identity", lambda pid: dict(self.identity)),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.handoff = self.make_handoff()

    def make_handoff(self, **overrides):
        values = dict(shutdown_callback=self.shutdown, gate=self.gate,
                      install_dir=self.root, data_dir=self.data,
                      registry_reader=lambda: dict(self.registration),
                      launcher=self.helper, native=self.native, ready_timeout=0.04)
        values.update(overrides)
        return self.module.InstallerHandoff(**values)

    def assert_refused(self, result):
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["status"], "failed", result)
        self.assertTrue(result["message"].isascii())
        self.assertEqual(self.helper.commands, [])
        self.assertEqual(self.shutdown_calls, [])
        self.assertEqual(self.store.read_bytes(), b'{"preserve":true}\n')

    def test_registered_bundled_install_is_eligible(self):
        self.assertTrue(self.handoff.eligible())
        self.assertEqual(self.helper.commands, [])

    def test_source_checkout_is_refused_despite_registration(self):
        (self.root / ".git").mkdir()
        self.assertFalse(self.handoff.eligible())
        self.assert_refused(self.handoff(self.payload, self.offer))

    def test_missing_or_wrong_registration_is_refused(self):
        for key, value in (("InstallLocation", str(self.base / "elsewhere")),
                           ("Inno Setup: App Path", str(self.base)),
                           ("Inno Setup: Setup Version", ""),
                           ("Publisher", "untrusted"),
                           ("UninstallString", '"C:\\outside\\unins000.exe"'),
                           ("DisplayVersion", "unknown")):
            with self.subTest(key=key):
                old = self.registration[key]
                self.registration[key] = value
                self.assert_refused(self.handoff(self.payload, self.offer))
                self.registration[key] = old
        self.assert_refused(self.make_handoff(registry_reader=lambda: None)(self.payload, self.offer))

    def test_relative_registry_paths_are_not_installed_identity(self):
        with patch.object(self.module.os, "getcwd", return_value=str(self.root)):
            self.registration["InstallLocation"] = "."
            self.registration["Inno Setup: App Path"] = "."
            self.assert_refused(self.handoff(self.payload, self.offer))

    def test_non_windows_and_unbundled_interpreter_are_refused(self):
        with patch.object(self.module.sys, "platform", "linux"):
            self.assertFalse(self.handoff.eligible())
            self.assert_refused(self.handoff(self.payload, self.offer))
        with patch.object(self.module.sys, "executable", str(self.base / "python.exe")):
            self.assert_refused(self.handoff(self.payload, self.offer))

    def test_bundled_directory_resolving_outside_installation_is_refused(self):
        # Model a junction's realpath result without requiring symlink privileges.
        canonical = self.module._canonical
        bundled = canonical(self.root / "python")
        outside = canonical(self.base / "outside-runtime")
        def junction_target(path):
            value = canonical(path)
            if value == bundled or value.startswith(bundled + os.sep):
                return outside + value[len(bundled):]
            return value
        with patch.object(self.module, "_canonical", side_effect=junction_target):
            self.assert_refused(self.handoff(self.payload, self.offer))

    def test_data_within_installation_is_refused_and_preserved(self):
        data = self.root / "data"
        data.mkdir()
        sentinel = data / "fixture.json"
        sentinel.write_bytes(b"keep")
        self.assert_refused(self.make_handoff(data_dir=data)(self.payload, self.offer))
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_other_module_root_and_missing_uninstaller_are_refused(self):
        self.assert_refused(self.make_handoff(install_dir=self.base)(self.payload, self.offer))
        (self.root / "unins000.exe").unlink()
        self.assert_refused(self.handoff(self.payload, self.offer))

    def test_active_admission_lease_waits_without_launch_or_shutdown(self):
        lease = self.gate.begin_install(exclude_pids={os.getpid()})
        try:
            result = self.handoff(self.payload, self.offer)
            self.assertEqual(result["state"], "waiting_for_idle", result)
            self.assertEqual(result["status"], "busy", result)
            self.assertEqual(self.helper.commands, [])
            self.assertEqual(self.shutdown_calls, [])
        finally:
            lease.close()
        self.gate.assert_admission_open()

    def test_active_inventory_waits_without_launch_or_shutdown(self):
        with patch.object(self.gate, "_running_jobs", return_value=[{"pid": 7}]):
            result = self.handoff(self.payload, self.offer)
        self.assertEqual(result["state"], "waiting_for_idle", result)
        self.assertEqual(self.helper.commands, [])
        self.assertEqual(self.shutdown_calls, [])
        self.gate.assert_admission_open()

    def test_payload_must_be_exact_existing_private_staged_exe(self):
        other = self.base / self.payload.name
        other.write_bytes(self.payload.read_bytes())
        for payload in (Path(self.payload.name), str(self.payload), other,
                        self.payload.parent / "missing.exe", self.payload.parent):
            with self.subTest(payload=str(payload)):
                self.assert_refused(self.handoff(payload, self.offer))

    def test_browser_dict_and_tampered_or_downgrade_offers_are_refused(self):
        self.assert_refused(self.handoff(self.payload, vars(self.offer)))
        for changes in ({"name": "arbitrary.exe"}, {"url": "https://example.invalid/update.exe"},
                        {"version": "0.9.0"}, {"version": "1.0.0"}, {"size": True},
                        {"size": self.offer.size + 1}, {"sha256": "not-a-digest"},
                        {"offer_id": "browser"}, {"asset_id": 0}):
            with self.subTest(changes=changes):
                self.assert_refused(self.handoff(self.payload, replace(self.offer, **changes)))

    def test_wrong_panel_pid_creation_or_executable_is_refused(self):
        for changes in ({"pid": os.getpid() + 1}, {"created": 0},
                        {"executable": str(self.base / "python.exe")}):
            with self.subTest(changes=changes):
                with patch.object(self.native, "process_identity",
                                  return_value=dict(self.identity, **changes)):
                    self.assert_refused(self.handoff(self.payload, self.offer))

    def test_ready_ack_transfers_gate_lifetime_before_graceful_shutdown(self):
        result = self.handoff(self.payload, self.offer)
        self.assertEqual(result["state"], "installing", result)
        self.assertEqual(result["status"], "launched", result)
        self.assertEqual(self.shutdown_calls, ["graceful"])
        command = self.helper.commands[0]
        self.assertEqual(command[0], str(self.payload))
        self.assertEqual(command[1:6], ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                      "/SP-", "/IRICASELFUPDATE"])
        parameters = dict(value.split("=", 1) for value in command[6:])
        self.assertEqual(parameters["/IRICAPANELPID"], str(os.getpid()))
        self.assertEqual(parameters["/IRICAPANELCREATED"], f'{self.identity["created"]:016x}')
        self.assertEqual(parameters["/IRICAGATE"], self.gate.event_name)
        self.assertEqual(parameters["/DIR"], str(self.root.resolve()))
        self.assertRegex(parameters["/IRICAREADY"], r"^Local\\IRICA_update_[0-9a-f]{64}_ready$")
        self.assertEqual(parameters["/IRICACANCEL"], parameters["/IRICAREADY"][:-5] + "cancel")
        with self.assertRaisesRegex(RuntimeError, "update"):
            self.gate.assert_admission_open()
        self.helper.close()
        self.gate.assert_admission_open()
        self.assertEqual(self.store.read_bytes(), b'{"preserve":true}\n')

    def test_readiness_timeout_does_not_shutdown_or_leave_python_lease(self):
        self.helper.acknowledge = False
        start = time.monotonic()
        result = self.handoff(self.payload, self.offer)
        self.assertEqual(result["state"], "failed", result)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(len(self.helper.commands), 1)
        self.assertEqual(self.shutdown_calls, [])
        self.gate.assert_admission_open()

    def test_failed_shutdown_requests_cancel_without_force_kill(self):
        self.shutdown_result = False
        result = self.handoff(self.payload, self.offer)
        self.assertEqual(result["state"], "failed", result)
        self.assertTrue(self.native.wait_event(self.helper.cancel_handle, 0))
        self.helper.close()
        self.gate.assert_admission_open()
        self.assertEqual(self.store.read_bytes(), b'{"preserve":true}\n')

    def test_shutdown_exception_cancels_and_preserves_user_data(self):
        def blocked_shutdown():
            raise RuntimeError("SECRET worker details")
        result = self.make_handoff(shutdown_callback=blocked_shutdown)(self.payload, self.offer)
        self.assertEqual(result["state"], "failed", result)
        self.assertNotIn("SECRET", result["message"])
        self.assertTrue(self.native.wait_event(self.helper.cancel_handle, 0))
        self.assertEqual(self.store.read_bytes(), b'{"preserve":true}\n')
        self.helper.close()
        self.gate.assert_admission_open()

    def test_acknowledgement_timeout_signals_an_opened_cancel_event(self):
        def opens_without_ready(command):
            parameters = dict(value.split("=", 1) for value in command[1:] if "=" in value)
            for name in ("/IRICAGATE", "/IRICACANCEL"):
                handle = self.native.open_event(parameters[name])
                self.helper.handles.append(handle)
                if name == "/IRICACANCEL":
                    self.helper.cancel_handle = handle
            return _Child()
        result = self.make_handoff(launcher=opens_without_ready)(self.payload, self.offer)
        self.assertEqual(result["state"], "failed", result)
        self.assertTrue(self.native.wait_event(self.helper.cancel_handle, 0))
        self.assertEqual(self.shutdown_calls, [])
        self.helper.close()
        self.gate.assert_admission_open()

    def test_launcher_error_is_sanitized_and_releases_gate(self):
        def broken(command):
            raise OSError("SECRET fixture path and internal details")
        result = self.make_handoff(launcher=broken)(self.payload, self.offer)
        self.assertEqual(result["state"], "failed", result)
        self.assertNotIn("SECRET", result["message"])
        self.assertEqual(self.shutdown_calls, [])
        self.gate.assert_admission_open()

    def test_early_helper_exit_never_requests_shutdown(self):
        class Exited:
            def poll(self):
                return 5
        def exited(command):
            self.helper(command)
            return Exited()
        result = self.make_handoff(launcher=exited)(self.payload, self.offer)
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(self.shutdown_calls, [])
        self.helper.close()
        self.gate.assert_admission_open()

    def test_events_are_fresh_and_timeout_cannot_exceed_ten_seconds(self):
        self.helper.acknowledge = False
        for _ in range(2):
            self.handoff(self.payload, self.offer)
        ready = [next(arg for arg in command if arg.startswith("/IRICAREADY="))
                 for command in self.helper.commands]
        self.assertNotEqual(ready[0], ready[1])
        for timeout in (0, 10.01, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.make_handoff(ready_timeout=timeout)


@unittest.skipUnless(sys.platform == "win32", "Native Windows handshake fixture")
class NativeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.module = _module(self)
        self.temp = tempfile.TemporaryDirectory(prefix="irica-native-handoff-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        from maintenance_gate import MaintenanceGate
        self.gate = MaintenanceGate(install_dir=self.root,
                                    registry_path=self.root / "jobs.json")
        self.native = self.module._Windows()
        self.handles = []
        self.addCleanup(self._close)

    def _close(self):
        for handle in self.handles:
            self.native.close(handle)

    def _worker(self, mode):
        import secrets
        token = secrets.token_hex(32)
        names = {kind: "Local\\IRICA_fixture_" + token + "_" + kind
                 for kind in ("ready", "release")}
        events = {}
        for kind, name in names.items():
            events[kind] = self.native.create_event(name)
            self.handles.append(events[kind])
        identity = self.native.process_identity(os.getpid())
        if mode == "reject-created":
            identity["created"] += 1
        elif mode == "reject-pid":
            identity["pid"] = 0
        control = self.root / "control.json"
        control.write_text(json.dumps(dict(identity, gate=self.gate.event_name,
                                           ready=names["ready"], release=names["release"])),
                           encoding="utf-8")
        command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                   "--handoff-worker", mode, str(self.root), str(control)]
        return command, events

    def test_native_helper_keeps_gate_alive_after_python_lease_closes(self):
        lease = self.gate.begin_install(exclude_pids={os.getpid()})
        command, events = self._worker("hold-event")
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(self.native.wait_event(events["ready"], 5000))
            lease.close()
            with self.assertRaisesRegex(RuntimeError, "update"):
                self.gate.assert_admission_open()
            self.native.set_event(events["release"])
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, (stdout, stderr))
            self.gate.assert_admission_open()
        finally:
            lease.close()
            self.native.set_event(events["release"])
            if child.poll() is None:
                child.wait(timeout=15)
            for stream in (child.stdout, child.stderr):
                stream.close()

    def test_native_wrong_pid_or_creation_never_acknowledges(self):
        for mode in ("reject-pid", "reject-created"):
            with self.subTest(mode=mode):
                lease = self.gate.begin_install(exclude_pids={os.getpid()})
                try:
                    command, events = self._worker(mode)
                    child = subprocess.run(command, capture_output=True, timeout=10)
                    self.assertNotEqual(child.returncode, 0)
                    self.assertFalse(self.native.wait_event(events["ready"], 0))
                finally:
                    lease.close()
                self.gate.assert_admission_open()


class RegistryContractTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows registry boundary")
    def test_native_reader_uses_only_expected_hkcu_read_view(self):
        module = _module(self)
        import winreg
        values = {
            "InstallLocation": "C:\\fixture\\IRICA",
            "Inno Setup: App Path": "C:\\fixture\\IRICA",
            "Inno Setup: Setup Version": "6.7.1", "DisplayName": "IRICA 1.0.0",
            "DisplayVersion": "1.0.0", "Publisher": "saeedgh837",
            "UninstallString": '"C:\\fixture\\IRICA\\unins000.exe"',
        }
        from unittest.mock import MagicMock
        key = MagicMock()
        key.__enter__.return_value = key
        with patch.object(winreg, "OpenKey", return_value=key) as opened, \
                patch.object(winreg, "QueryValueEx",
                             side_effect=lambda handle, name: (values[name], winreg.REG_SZ)):
            self.assertEqual(module._read_registration(), values)
        opened.assert_called_once_with(
            winreg.HKEY_CURRENT_USER,
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
            "{7C0F4A2E-9B1D-4E3A-8F5C-IRICA-PANEL-01}_is1", 0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
        with patch.object(winreg, "OpenKey", side_effect=PermissionError("fixture denied")):
            self.assertIsNone(module._read_registration())


class NativePathIdentityTests(unittest.TestCase):
    """Characterize the native path primitive required by the Inno contract."""
    @unittest.skipUnless(sys.platform == "win32", "Windows final-path handles")
    def test_junction_and_fresh_child_share_the_real_installation_identity(self):
        with tempfile.TemporaryDirectory(prefix="irica-path-identity-") as temporary:
            root = Path(temporary)
            actual = root / "Installed Application With Spaces"
            actual.mkdir()
            alias = root / "Installation Alias"
            self._junction(alias, actual)
            self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
            self.assertNotEqual(os.path.normcase(str(alias)), os.path.normcase(str(actual)))
            self.assertEqual(self._native_resolved(alias), os.path.normcase(os.path.realpath(actual)))
            self.assertEqual(self._native_resolved(alias / "New Folder" / "IRICA"),
                             os.path.normcase(os.path.realpath(actual / "New Folder" / "IRICA")))
            from maintenance_gate import MaintenanceGate
            gate = MaintenanceGate(install_dir=actual, registry_path=root / "jobs.json")
            import hashlib
            resolved = self._native_resolved(alias)
            self.assertEqual("Local\\IRICA_" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:24]
                             + "_install", gate.event_name)
            data = actual / "Data Storage"
            data.mkdir()
            self.assertTrue(Path(self._native_resolved(alias / "Data Storage" / "Future App"))
                            .is_relative_to(Path(self._native_resolved(data))))
            self.assertFalse((actual / "New Folder").exists())
            alias.rmdir()

    @staticmethod
    def _junction(path, target):
        # A scratch-only mount-point reparse fixture; no shell, elevation or app writes.
        import struct
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.DeviceIoControl.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        path.mkdir()
        substitute = "\\??\\" + str(target.resolve())
        printable = str(target.resolve())
        names = (substitute + "\0" + printable + "\0").encode("utf-16-le")
        data = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(names), 0,
                           0, len(substitute.encode("utf-16-le")),
                           len((substitute + "\0").encode("utf-16-le")),
                           len(printable.encode("utf-16-le"))) + names
        buffer = ctypes.create_string_buffer(data)
        handle = kernel.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            returned = wintypes.DWORD()
            if not kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(data), None, 0,
                                          ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)

    @staticmethod
    def _native_resolved(path):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                    wintypes.DWORD, wintypes.DWORD]
        kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        candidate = Path(os.path.abspath(path))
        suffix = []
        for _ in range(256):
            handle = kernel.CreateFileW(str(candidate), 0x80, 7, None, 3, 0x02000000, None)
            if handle != ctypes.c_void_p(-1).value:
                break
            error = ctypes.get_last_error()
            if error not in (2, 3) or candidate.parent == candidate:
                raise ctypes.WinError(error)
            suffix.insert(0, candidate.name)
            candidate = candidate.parent
        else:
            raise ValueError("The scratch path exceeded its ancestor limit")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if not 0 < size < len(buffer):
                raise ctypes.WinError(ctypes.get_last_error())
            resolved = buffer.value
        finally:
            kernel.CloseHandle(handle)
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
        return os.path.normcase(os.path.join(resolved, *suffix))


class InstallerPolicyTests(unittest.TestCase):
    def test_gate_and_containment_use_handle_resolved_path_identity(self):
        source = (ROOT / "lab" / "scripts" / "irica.iss").read_text(encoding="utf-8")
        canonical = source.split("function CanonicalDirectory(", 1)[1].split("function IsWithin(", 1)[0]
        self.assertIn("GetFinalPathNameByHandleW@kernel32.dll", source)
        self.assertIn("CreateFileW@kernel32.dll", source)
        self.assertIn("OpenNativePath(", canonical)
        self.assertIn("NativeFinalPath(", canonical)
        self.assertIn("ValidNewPathPart(", canonical)
        self.assertIn("Suffix", canonical)
        gate = source.split("function GateName(", 1)[1].split("function ParseVersion(", 1)[0]
        containment = source.split("function IsWithin(", 1)[1].split("function GateName(", 1)[0]
        self.assertIn("CanonicalDirectory(Directory)", gate)
        self.assertIn("CanonicalDirectory(Filename)", containment)
        self.assertIn("CanonicalDirectory(Directory)", containment)
        prepare = source.split("function PrepareToInstall(", 1)[1].split("function NormalPanelLaunch(", 1)[0]
        self.assertIn("SelectedPathMatchesResolved(Directory)", prepare)


    def test_installer_does_not_force_close_or_probe_authenticated_root(self):
        source = (ROOT / "lab" / "scripts" / "irica.iss").read_text(encoding="utf-8")
        lowered = source.lower()
        for unsafe in ("killpanelprocesses", "stop-process", "taskkill", "executionpolicy",
                       "winhttp", "http://127.0.0.1:8750/"):
            self.assertNotIn(unsafe, lowered)
        for directive in ("CloseApplications=no", "RestartApplications=no"):
            self.assertIn(directive, source)

    def test_normal_silent_install_keeps_skipifsilent_but_self_update_relaunches(self):
        source = (ROOT / "lab" / "scripts" / "irica.iss").read_text(encoding="utf-8")
        run = source.split("[Run]", 1)[1].split("[UninstallDelete]", 1)[0]
        records = [record for record in run.replace("\\\n", " ").splitlines()
                   if record.startswith("Filename:")]
        self.assertEqual(len(records), 2)
        normal = next(record for record in records if "Check: NormalPanelLaunch" in record)
        update = next(record for record in records if "Check: SelfUpdatePanelLaunch" in record)
        self.assertIn("skipifsilent", normal)
        self.assertNotIn("skipifsilent", update)
        self.assertIn("panel_tray.pyw", update)
        self.assertNotRegex(source, r"(?im)^\s*(?:Type:.*)?filesandordirs.*localappdata.*irica")


def _handoff_worker():
    """A bounded, trusted fixture: kernel handles only, never an installer."""
    if len(sys.argv) != 5 or sys.argv[2] not in {
            "hold-event", "reject-created", "reject-pid", "no-ready"}:
        raise SystemExit("Invalid handoff fixture arguments")
    root = Path(sys.argv[3]).resolve()
    control = Path(sys.argv[4]).resolve()
    if not root.is_dir() or not control.is_relative_to(root):
        raise SystemExit("The fixture control must be inside its scratch root")
    document = json.loads(control.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    from installer_handoff import _Windows
    native = _Windows()
    process = native.dll.OpenProcess(0x1000 | 0x100000, False, document["pid"])
    if not process:
        raise SystemExit("The expected fixture panel process cannot be opened")
    handles = [process]
    try:
        identity = native.process_identity(document["pid"])
        if not identity or any(identity[key] != document[key]
                               for key in ("pid", "created", "executable")):
            raise SystemExit("The expected panel process identity does not match")
        if native.wait_event(process, 0):
            raise SystemExit("The expected fixture panel process already exited")
        gate = native.open_event(document["gate"])
        handles.append(gate)
        if not native.wait_event(gate, 0):
            raise SystemExit("The fixture gate is not signaled")
        ready = native.open_event(document["ready"])
        handles.append(ready)
        release = native.open_event(document["release"])
        handles.append(release)
        if sys.argv[2] != "no-ready":
            native.set_event(ready)
        if not native.wait_event(release, 15000):
            raise SystemExit("The fixture helper exceeded its bounded wait")
    finally:
        for handle in handles:
            native.close(handle)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--handoff-worker":
        _handoff_worker()
    else:
        unittest.main()
