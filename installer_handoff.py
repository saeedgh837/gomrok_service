"""Private, fail-closed handoff from AppUpdater to the registered Inno installer.

InstallerHandoff(shutdown_callback=..., gate=None, install_dir=None, data_dir=None,
                 registry_reader=None, launcher=None, native=None,
                 ready_timeout=10.0)

eligible() is a read-only installation check. __call__(payload: pathlib.Path,
 offer: app_updater.VerifiedOffer) returns status busy/blocked/launched/failed,
plus a UI state and a safe English message. This is NOT an HTTP/file-open API:
AppUpdater must prove the exact current confirmed immutable offer and retain its
verified payload/directory deny-write/delete handles throughout this callback.
The shutdown callback must return True only after background workers drain/join
and graceful panel shutdown is requested. It must never stop active jobs.
No application imports, registry access, threads or native calls run on import.
"""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys


_UNINSTALL_KEY = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                  "\\{7C0F4A2E-9B1D-4E3A-8F5C-IRICA-PANEL-01}_is1")
_REGISTRATION_VALUES = (
    "InstallLocation", "Inno Setup: App Path", "Inno Setup: Setup Version",
    "DisplayName", "DisplayVersion", "Publisher", "UninstallString",
)
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z", re.ASCII)
_MAX_EXE = 256 * 1024 * 1024


def _version(value):
    if not isinstance(value, str) or len(value) > 64 or not _VERSION.fullmatch(value):
        raise ValueError("Invalid installer version")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in parts):
        raise ValueError("Installer version component exceeds Inno limits")
    return parts


def _canonical(value):
    return os.path.normcase(os.path.realpath(value))


def _inside(path, root):
    try:
        return os.path.commonpath((_canonical(path), _canonical(root))) == _canonical(root)
    except ValueError:
        return False


def _read_registration():
    """Read only this application's per-user Inno registration, never HKLM."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            values = {}
            for name in _REGISTRATION_VALUES:
                value, kind = winreg.QueryValueEx(key, name)
                if kind != winreg.REG_SZ or not isinstance(value, str):
                    return None
                values[name] = value
            return values
    except OSError:
        return None


class _Windows:
    """Small Win32 handle boundary, also exercised by non-installing fixtures."""
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("The installer handoff requires Windows")
        self.dll = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateEventW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                              wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenEventW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetEvent": ([wintypes.HANDLE], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
                                wintypes.BOOL),
            "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
                                           wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, result

    def close(self, handle):
        if handle and not self.dll.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def create_event(self, name):
        ctypes.set_last_error(0)
        handle = self.dll.CreateEventW(None, True, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.close(handle)
            raise OSError("The private handoff event already exists")
        return handle

    def open_event(self, name):
        handle = self.dll.OpenEventW(0x100000 | 2, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def set_event(self, handle):
        if not self.dll.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_event(self, handle, timeout_ms):
        result = self.dll.WaitForSingleObject(handle, timeout_ms)
        if result not in (0, 258):
            raise OSError("Cannot verify the installer handoff event")
        return result == 0

    def process_identity(self, pid):
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            return None
        handle = self.dll.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self.wait_event(handle, 0):
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.dll.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
                raise ctypes.WinError(ctypes.get_last_error())
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not self.dll.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            return {"pid": pid, "created": (times[0].dwHighDateTime << 32)
                    | times[0].dwLowDateTime, "executable": image.value}
        finally:
            self.close(handle)


def _launch(command):
    return subprocess.Popen(
        command, cwd=str(Path(command[0]).parent), shell=False, close_fds=True,
        creationflags=0x00000008 | 0x00000200,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _result(status, message):
    state = {"busy": "waiting_for_idle", "blocked": "waiting_for_idle",
             "launched": "installing", "failed": "failed"}[status]
    return {"status": status, "state": state, "message": message}


class InstallerHandoff:
    def __init__(self, *, shutdown_callback, gate=None, install_dir=None, data_dir=None,
                 registry_reader=None, launcher=None, native=None, ready_timeout=10.0):
        if (not isinstance(ready_timeout, (int, float)) or isinstance(ready_timeout, bool)
                or not math.isfinite(ready_timeout) or not 0 < ready_timeout <= 10):
            raise ValueError("Installer readiness timeout must be between zero and ten seconds")
        if not callable(shutdown_callback):
            raise TypeError("A graceful shutdown callback is required")
        self._root = Path(install_dir or Path(__file__).resolve().parent).resolve()
        self._data_dir = Path(data_dir).resolve() if data_dir is not None else None
        self._gate = gate
        self._registry = registry_reader or _read_registration
        self._launcher = launcher or _launch
        self._native = native
        self._shutdown = shutdown_callback
        self._ready_ms = max(1, int(ready_timeout * 1000))

    def _installation(self):
        if sys.platform != "win32" or os.name != "nt":
            raise ValueError("Automatic installation requires Windows")
        root = self._root
        if (_canonical(root) != _canonical(Path(__file__).resolve().parent)
                or (root / ".git").exists()):
            raise ValueError("Automatic installation requires the installed module root")
        interpreter = Path(sys.executable)
        if (not interpreter.is_absolute() or not interpreter.is_file()
                or not _inside(interpreter, root)
                or _canonical(interpreter.parent) != _canonical(root / "python")
                or interpreter.name.lower() not in {"python.exe", "pythonw.exe"}
                or not (root / "python" / "python.exe").is_file()
                or not (root / "python" / "pythonw.exe").is_file()
                or not (root / "panel_tray.pyw").is_file()):
            raise ValueError("Automatic installation requires the bundled interpreter")
        data = self._data_dir
        if data is None:
            import app_paths
            data = Path(app_paths.data_dir()).resolve()
        if _inside(data, root) or _inside(root, data):
            raise ValueError("Application and user data directories must be separate")
        registration = self._registry()
        if not isinstance(registration, dict) or any(
                not isinstance(registration.get(key), str) for key in _REGISTRATION_VALUES):
            raise ValueError("The per-user Inno registration is missing")
        current = (root / "VERSION").read_text(encoding="ascii").strip()
        version = _version(current)
        if (any(not Path(registration[key]).is_absolute()
                or _canonical(registration[key]) != _canonical(root)
                for key in ("InstallLocation", "Inno Setup: App Path"))
                or registration["Publisher"] != "saeedgh837"
                or not registration["Inno Setup: Setup Version"].strip()
                or registration["DisplayName"] not in {"IRICA", "IRICA " + current}
                or _version(registration["DisplayVersion"]) != version):
            raise ValueError("The Inno registration does not match this installation")
        uninstall = re.fullmatch(r'"([^"\r\n]+)"', registration["UninstallString"])
        if uninstall is None:
            raise ValueError("The registered Inno uninstaller is invalid")
        uninstaller = Path(uninstall.group(1))
        if (not uninstaller.is_absolute() or _canonical(uninstaller.parent) != _canonical(root)
                or not uninstaller.is_file()
                or not re.fullmatch(r"unins[0-9]{3}\.exe", uninstaller.name, re.IGNORECASE)):
            raise ValueError("The registered Inno uninstaller is outside the installation")
        return root, data, interpreter, version

    def eligible(self):
        try:
            self._installation()
            return True
        except (OSError, ValueError, TypeError):
            return False

    @staticmethod
    def _validate_payload(payload, offer, root, data, current_version):
        # Only the service can supply its frozen offer; browser snapshots are dicts.
        from app_updater import VerifiedOffer
        if type(offer) is not VerifiedOffer or not isinstance(payload, Path):
            raise ValueError("An immutable verified service offer and Path are required")
        version = _version(offer.version)
        if (version <= current_version
                or offer.name != "IRICA-Setup-v" + offer.version + ".exe"
                or type(offer.release_id) is not int or offer.release_id <= 0
                or type(offer.asset_id) is not int or offer.asset_id <= 0
                or type(offer.size) is not int or not 0 < offer.size <= _MAX_EXE
                or not isinstance(offer.sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", offer.sha256, re.ASCII) is None
                or not isinstance(offer.offer_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", offer.offer_id, re.ASCII) is None
                or offer.url != "https://github.com/saeedgh837/gomrok_service/releases/download/v"
                + offer.version + "/" + offer.name):
            raise ValueError("The current official upgrade offer is invalid")
        if (not payload.is_absolute() or payload != payload.resolve(strict=True)
                or payload.name != offer.name or not payload.parent.name.startswith("irica-update-")
                or len(payload.parent.name) < len("irica-update-") + 6
                or _inside(payload, root) or _inside(payload, data)):
            raise ValueError("The verified installer is not in private external staging")
        info = payload.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size != offer.size or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("The staged installer does not match the verified offer")

    def __call__(self, payload, offer):
        try:
            root, data, interpreter, current = self._installation()
            self._validate_payload(payload, offer, root, data, current)
            native = self._native or _Windows()
            identity = native.process_identity(os.getpid())
            if (not isinstance(identity, dict) or identity.get("pid") != os.getpid()
                    or type(identity.get("created")) is not int or identity["created"] <= 0
                    or _canonical(identity.get("executable", "")) != _canonical(interpreter)):
                raise ValueError("The expected panel process identity could not be verified")
            from maintenance_gate import MaintenanceBlocked, default_gate
            gate = self._gate or default_gate()
            if _canonical(gate.install_dir) != _canonical(root):
                raise ValueError("The admission guard belongs to another installation")
            try:
                lease = gate.begin_install(exclude_pids={os.getpid()})
            except MaintenanceBlocked:
                return _result("busy", "The verified update is waiting for active work or a safe maintenance check.")
        except Exception:
            return _result("failed", "Automatic installation is not safe for this installation or verified offer.")

        cancel = None
        launched = False
        result = _result("failed", "The installer did not acknowledge a safe handoff. The panel remains open.")
        with ExitStack() as handles:
            handles.callback(lease.close)
            try:
                token = secrets.token_hex(32)
                prefix = "Local\\IRICA_update_" + token
                ready_name, cancel_name = prefix + "_ready", prefix + "_cancel"
                ready = native.create_event(ready_name)
                handles.callback(native.close, ready)
                cancel = native.create_event(cancel_name)
                handles.callback(native.close, cancel)
                command = [
                    str(payload), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                    "/IRICASELFUPDATE", "/IRICAPANELPID=" + str(os.getpid()),
                    "/IRICAPANELCREATED=" + format(identity["created"], "016x"),
                    "/IRICAGATE=" + lease.event_name, "/IRICAREADY=" + ready_name,
                    "/IRICACANCEL=" + cancel_name, "/DIR=" + str(root),
                ]
                helper = self._launcher(command)
                if native.wait_event(ready, self._ready_ms) and helper.poll() is None:
                    # Inno now holds the gate event and the original process HANDLE.
                    # Its cancellation/exit timeout prevents replacement if we cannot drain.
                    if self._shutdown() is True:
                        launched = True
                        result = _result("launched", "The verified installer is waiting for graceful panel exit, then will reopen it.")
                    else:
                        result = _result("failed", "Background workers did not drain. Installation was canceled; keep the panel open.")
            except Exception:
                result = _result("failed", "The safe installer handoff failed. No active job was stopped.")
            finally:
                if cancel is not None and not launched:
                    try:
                        native.set_event(cancel)
                    except OSError:
                        result = _result("failed", "Installer cancellation could not be confirmed. Keep the panel open.")
        return result
