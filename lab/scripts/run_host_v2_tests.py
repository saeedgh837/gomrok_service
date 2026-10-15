"""Fail-closed entry point for the separately provisioned Phase B fixture."""

import argparse
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_REJECTED = "FIXTURE_TARGET_REJECTED"
_UNSET = object()
ROOT = Path(__file__).resolve().parents[2]
SUITES = ("fixture-guard", "migrations", "integrity", "workspaces", "accounts", "sessions", "api", "all")
_INTEGRATION = SUITES[1:-1]
_MAX_CONFIG = 16 * 1024


def _text(value):
    if type(value) is not str or not value.strip() or "\x00" in value:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


def validate_fixture(config, marker=_UNSET):
    """Pure target/marker validation, with no file, process or socket access."""
    top_keys = {"fixture", "run_id", "instance_id", "php_path", "migrate", "runtime"}
    connection_keys = {"host", "port", "dbname", "user", "password"}
    if type(config) is not dict or set(config) != top_keys:
        raise ValueError(_REJECTED)
    if (config["fixture"] is not True
            or type(config["run_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{16}", config["run_id"]) is None
            or not _text(config["instance_id"]) or not _text(config["php_path"])):
        raise ValueError(_REJECTED)
    dbname = "irica_b_test_" + config["run_id"]
    for role in ("migrate", "runtime"):
        connection = config[role]
        if type(connection) is not dict or set(connection) != connection_keys:
            raise ValueError(_REJECTED)
        if (connection["host"] != "127.0.0.1"
                or type(connection["port"]) is not int or connection["port"] != 3307
                or connection["dbname"] != dbname
                or not _text(connection["user"]) or not _text(connection["password"])):
            raise ValueError(_REJECTED)
    if config["migrate"]["user"] == config["runtime"]["user"]:
        raise ValueError(_REJECTED)
    if marker is not _UNSET:
        expected = {"run_id": config["run_id"], "instance_id": config["instance_id"],
                    "dbname": dbname}
        if type(marker) is not dict or marker != expected:
            raise ValueError(_REJECTED)
    return config


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_REJECTED)
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError(_REJECTED)


def is_local_path(text):
    """Ruling 11: never read the credential file over SMB or via a device path."""
    return isinstance(text, str) and not (text.startswith("\\") or text.startswith("//"))


def read_fixture(path):
    """Read an explicit private JSON file; never fall back to product config."""
    if not isinstance(path, (str, Path)):
        raise ValueError(_REJECTED)
    raw_path = str(path)
    if not _text(raw_path) or not is_local_path(raw_path):
        raise ValueError(_REJECTED)
    try:
        path = Path(raw_path)
        if path.suffix.lower() != ".json":
            raise ValueError(_REJECTED)
        resolved = path.resolve()
        if not path.is_absolute() or not is_local_path(str(resolved)) \
                or resolved.is_relative_to(ROOT):
            raise ValueError(_REJECTED)
        with resolved.open("rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CONFIG:
                raise ValueError(_REJECTED)
            raw = source.read(_MAX_CONFIG)
    except (OSError, RuntimeError):
        raise ValueError("FIXTURE_CONFIG_MISSING") from None
    if len(raw) > _MAX_CONFIG:
        raise ValueError(_REJECTED)
    try:
        config = json.loads(raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(_REJECTED) from None
    return validate_fixture(config)


def suite_path(suite):
    if suite == "api":
        return ROOT / "lab" / "tests" / "test_host_v2_api.py"
    if suite in _INTEGRATION:
        return ROOT / "lab" / "fixtures" / "host_v2" / ("test_" + suite + ".php")
    raise ValueError("FIXTURE_ARGUMENT_REJECTED")


_READY = re.compile(
    r"FIXTURE_READY driver=[a-z]{1,16} server=[0-9A-Za-z.+:~/\-]{1,96}"
    r" tz=\+[0-9]{2}:[0-9]{2} tables=[0-9]{1,18}")
_LABEL = re.compile(r"FIXTURE_[A-Z0-9_]{1,60}")


def prerequisite_lines(data):
    """Keep only fixed fixture labels; a PDO diagnostic must never be relayed."""
    if type(data) is not bytes:
        return []
    kept = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if _READY.fullmatch(line) or _LABEL.fullmatch(line):
            kept.append(line)
    return kept


def pid_alive(pid):
    """Prove liveness. os.kill(pid, 0) is destructive on Windows (measured), so
    Windows uses OpenProcess/GetExitCodeProcess instead of a signal."""
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetLastError.restype = wintypes.DWORD
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # ERROR_INVALID_PARAMETER (87) is the only error that proves "no such
            # process"; anything else means we cannot prove death, so stay alive.
            return kernel32.GetLastError() != 87
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lock_path(config):
    return Path(tempfile.gettempdir()) / ("irica-b-fixture-" + config["run_id"] + ".lock")


def lock_body(pid):
    token = binascii.hexlify(os.urandom(16)).decode("ascii")
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ("pid=%d token=%s started=%s\n" % (pid, token, started)).encode("ascii")


_LOCK_BODY = re.compile(
    rb"\Apid=([0-9]{1,10}) token=([0-9a-f]{32}) started=[0-9]{4}-[0-9]{2}-[0-9]{2}"
    rb"T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\r?\n\Z")


def lock_state(path):
    """'absent' | 'live' | 'stale' | 'unreadable'; only 'stale' may be taken over."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if len(raw) > 256:
        return "unreadable"
    match = _LOCK_BODY.match(raw)
    if match is None:
        return "unreadable"
    return "live" if pid_alive(int(match.group(1))) else "stale"


def break_lock(config):
    """Ruling 6 escape hatch: clear a lock only when no live owner holds it."""
    path = lock_path(config)
    state = lock_state(path)
    if state == "absent":
        print("FIXTURE_LOCK_ABSENT")
        return 0
    if state == "live":
        print("FIXTURE_LOCK_LIVE")
        return 2
    try:
        path.unlink()
    except OSError:
        print("FIXTURE_LOCK_BUSY")
        return 2
    print("FIXTURE_LOCK_CLEARED")
    return 0


@contextmanager
def fixture_lock(config):
    """One approved fixture instance cannot host two overlapping runners."""
    path = lock_path(config)
    state = lock_state(path)
    if state in ("live", "unreadable"):
        raise ValueError("FIXTURE_BUSY")
    if state == "stale":
        try:
            path.unlink()
        except OSError:
            raise ValueError("FIXTURE_BUSY") from None
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        handle = os.open(path, flags)
    except FileExistsError:
        raise ValueError("FIXTURE_BUSY") from None
    except OSError:
        raise ValueError("FIXTURE_CONFIG_MISSING") from None
    body = lock_body(os.getpid())
    token = _LOCK_BODY.match(body).group(2)
    try:
        try:
            os.write(handle, body)
        finally:
            os.close(handle)
        yield path
    finally:
        try:
            if _LOCK_BODY.match(path.read_bytes()).group(2) == token:
                path.unlink()
        except (OSError, ValueError, AttributeError, TypeError):
            pass


def run_scenario(command, env=None, capture=False):
    """Return (exit_status, ready_attestations).

    Child output is never relayed verbatim: with capture=True only allowlisted
    lines are printed, and the caller learns how many of them were a full
    FIXTURE_READY record so a gate cannot be read as green without one.
    """
    attestations = 0
    try:
        if capture:
            result = subprocess.run(command, cwd=ROOT, env=env, timeout=120,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    check=False, shell=False)
            for line in prerequisite_lines(result.stdout):
                print(line)
                if _READY.fullmatch(line):
                    attestations += 1
        else:
            result = subprocess.run(command, cwd=ROOT, env=env, timeout=120,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    check=False, shell=False)
    except OSError:
        return 2, 0
    except subprocess.TimeoutExpired:
        return 1, 0
    status = 0 if result.returncode == 0 else 2 if result.returncode == 2 else 1
    return status, attestations


def _run_test_module(module):
    """Never count an empty or skipped gate as green, matching _API_LOADER."""
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    if suite.countTestCases() == 0:
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        return 1
    return 0 if result.wasSuccessful() else 1


def _guard_suite():
    # A machine without PHP is a missing prerequisite, never a behavioural RED.
    if shutil.which("php") is None:
        raise ValueError("FIXTURE_RUNTIME_MISSING")
    sys.modules["run_host_v2_tests"] = sys.modules[__name__]
    test_path = ROOT / "lab" / "tests" / "test_host_v2_fixture.py"
    if not test_path.is_file():
        raise ValueError("FIXTURE_SUITE_MISSING")
    spec = importlib.util.spec_from_file_location("test_host_v2_fixture", test_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _run_test_module(module)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("FIXTURE_ARGUMENT_REJECTED")


# Load only the allowlisted Python API module under isolated Python. No path injection.
_API_LOADER = """
import importlib.util, sys, unittest
spec = importlib.util.spec_from_file_location('test_host_v2_api', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.defaultTestLoader.loadTestsFromModule(module)
if suite.countTestCases() == 0:
    sys.exit(2)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
"""


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--fixture-config")
    parser.add_argument("--break-lock", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.suite == "fixture-guard":
            if args.fixture_config is not None or args.break_lock:
                raise ValueError("FIXTURE_ARGUMENT_REJECTED")
            return _guard_suite()
        path = args.fixture_config or os.environ.get("IRICA_B_FIXTURE_CONFIG")
        if not path:
            raise ValueError("FIXTURE_CONFIG_MISSING")
        config = read_fixture(path)
        if args.break_lock:
            return break_lock(config)
        bootstrap = ROOT / "lab" / "fixtures" / "host_v2" / "bootstrap.php"
        if not bootstrap.is_file():
            raise ValueError("FIXTURE_SUITE_MISSING")
        php = shutil.which(config["php_path"])
        if php is None:
            raise ValueError("FIXTURE_RUNTIME_MISSING")
        selected = _INTEGRATION if args.suite == "all" else (args.suite,)
        # Child processes receive the private file path, never credential values.
        env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
               if key in os.environ}
        env["IRICA_B_FIXTURE_CONFIG"] = str(Path(path).resolve())
        php_command = [php, "-d", "display_errors=0", "-d", "log_errors=0",
                       "-d", "auto_prepend_file=", "-d", "auto_append_file="]
        with fixture_lock(config):
            # The real gate always runs before any suite is resolved, so a
            # missing scenario can never hide an unverified fixture.
            status, attestations = run_scenario(
                php_command + [str(bootstrap), "--check-prerequisites"], env, capture=True)
            # Green needs a positive attestation, not just a zero exit: a child
            # that performed no check (or reported twice) must never read as a
            # passed gate.
            if status or attestations != 1:
                print("FIXTURE_PREREQUISITE_FAILED")
                return 2
            if args.suite == "all":
                status = _guard_suite()
                if status:
                    return status
            # Deliberately sequential: migration/integrity cannot race one instance.
            for suite in selected:
                target = suite_path(suite)
                if not target.is_file():
                    raise ValueError("FIXTURE_SUITE_MISSING")
                command = ([sys.executable, "-I", "-B", "-c", _API_LOADER, str(target)]
                           if suite == "api" else php_command + [str(target)])
                status, _attestations = run_scenario(command, env)
                print("FIXTURE_" + suite.upper()
                      + ("_PASS" if status == 0
                         else "_PREREQUISITE_FAILED" if status == 2 else "_FAIL"))
                if status:
                    return status
        return 0
    except ValueError as error:
        # Every raised error above is a fixed label, never input/credential content.
        print(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
