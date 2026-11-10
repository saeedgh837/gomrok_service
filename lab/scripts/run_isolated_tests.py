"""Run unittest modules in separate, outbound-denied temporary environments.

Use the bundled interpreter with -I -B. No application module is imported by
this supervisor. Each child installs filesystem/network/process guards before
loading app_paths, disables its migration bootstrap, and then loads one module.
This is a cooperative test harness, not an OS sandbox for hostile/native code.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import weakref

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "lab" / "tests"
SCRIPT = Path(__file__).resolve()
DEFAULT = (
    "test_crm_owner_passport", "test_v1_6_profile_ensure_fix",
    "test_profile_success", "test_crm_stage_transitions",
    "test_crm_case_integrity", "test_crm_scheduler", "test_crm_watch_identity",
    "test_v1_5_profile_ready", "test_cloud_apply", "test_chromedriver_updater",
    "test_driver_autoupdate_hook", "test_driver_update_panel",
    "test_auth_hardening", "test_clerk_cloud_sync",
    "test_v1_18_mission_clerks_and_diagnostics",
)
_POLICY = None


def _within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _environment(scratch):
    """Replace inherited paths and proxy/credential overrides before imports."""
    keep = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS"}
    for key in list(os.environ):
        if key.upper() not in keep:
            os.environ.pop(key, None)
    paths = {name: scratch / name for name in ("data", "home", "tmp", "logs")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(paths["tmp"])
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        os.environ[key] = str(paths["home"])
    os.environ.update({
        "IRICA_DATA_DIR": str(paths["data"]),
        "IRICA_CRM_STORE": str(paths["data"] / "crm_store.json"),
        "IRICA_ESTELAM_STORE": str(paths["data"] / "estelam_store.json"),
        "IRICA_IMPORT_FROM": str(scratch / "empty-legacy"),
        "IRICA_SYSTEM_LOG_DIR": str(paths["logs"]),
        "IRICA_SYSTEM_DEBUG": "0", "IRICA_CLOUD_WORKER_OFF": "1",
        "IRICA_CRM_SCHEDULER_OFF": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "NO_PROXY": "*", "no_proxy": "*",
    })
    tempfile.tempdir = str(paths["tmp"])
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    os.chdir(scratch)


class IsolationPolicy:
    def __init__(self, scratch):
        self.scratch = scratch.resolve()
        self.runtime = Path(sys.base_prefix).resolve()
        self.listeners = weakref.WeakValueDictionary()
        self.bindings = weakref.WeakKeyDictionary()
        self.closed_fixtures = {}
        self.denials = Counter()
        self.expected_denials = Counter()
        self.local = threading.local()
        self.current_test = "module import"
        self.original_socket = socket.socket
        self.original_popen = subprocess.Popen
        self.node = shutil.which("node")
        self.processes = []
        self.process_errors = []

    def trusted_worker(self, command):
        if len(command) not in {8, 9} or command[:3] != [sys.executable, "-I", "-B"]:
            return None
        script = Path(command[3]).resolve()
        if (len(command) == 9 and script == TESTS / "test_registration_identity.py"
                and command[4] == "--crm-lock-worker"
                and command[6] in {"increment", "hold"}):
            paths, budget = (5, 7, 8), 5
        elif (len(command) == 9 and script == TESTS / "test_registration_attempts.py"
              and command[4] == "--attempt-worker"
              and command[6] in {"claim-publish", "claim-assert"}):
            paths, budget = (5, 7, 8), 6
        elif (len(command) == 8 and script == TESTS / "test_installer_handoff.py"
              and command[4] == "--handoff-worker"
              and command[5] in {"hold-event", "reject-created", "reject-pid", "no-ready"}):
            paths, budget = (6, 7), 15
        elif (len(command) == 8 and script == TESTS / "test_driver_autoupdate_hook.py"
              and command[4] == "--driver-lock-worker" and command[5] == "hold"):
            paths, budget = (6, 7), 15
        elif (len(command) == 8 and script == TESTS / "test_maintenance_gate.py"
              and command[4] == "--gate-worker"
              and command[5] in {"wait", "admit", "hold-event", "install"}):
            paths, budget = (6, 7), 15
        else:
            return None
        for index in paths:
            self.path(command[index], True)
        if script == TESTS / "test_driver_autoupdate_hook.py":
            self.driver_lock_manifest(command[6])
        return budget

    def driver_lock_manifest(self, value):
        manifest_path = Path(value)
        self.path(manifest_path)
        try:
            size = manifest_path.stat().st_size
        except OSError:
            self.deny("invalid-driver-lock-manifest")
        if size > 16384:
            self.deny("oversized-driver-lock-manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.deny("invalid-driver-lock-manifest")
        if (not isinstance(manifest, dict)
                or set(manifest) != {"primary", "extras", "data_root"}
                or not isinstance(manifest["extras"], list)
                or len(manifest["extras"]) > 2):
            self.deny("invalid-driver-lock-manifest")
        for path in (manifest["primary"], manifest["data_root"], *manifest["extras"]):
            if not isinstance(path, str) or not path:
                self.deny("invalid-driver-lock-manifest-path")
            self.path(path, True)

    @contextmanager
    def expect_denial(self):
        """Explicitly expected denials in the runner's own regression tests."""
        previous = getattr(self.local, "expected", False)
        self.local.expected = True
        try:
            yield
        finally:
            self.local.expected = previous

    @contextmanager
    def expect_closed_fixture_probe(self, address):
        """Simulate one refused connect, only to a fixture closed by this test.

        No packet is sent: a port could be reused by another process after the
        fixture closes it. Other hosts, ports and additional attempts still
        become unexpected denials and fail the module.
        """
        key = (self.loopback(address[0]), int(address[1]))
        if self.closed_fixtures.get(key) != self.current_test:
            raise AssertionError("closed-port probe requires this test's own fixture")
        previous = getattr(self.local, "closed_probe", None)
        probe = {"address": key, "test": self.current_test, "remaining": 1}
        self.local.closed_probe = probe
        try:
            yield
            if probe["remaining"]:
                raise AssertionError("expected closed-fixture probe did not occur")
        finally:
            self.local.closed_probe = previous

    def deny(self, operation):
        counter = (self.expected_denials if getattr(self.local, "expected", False)
                   else self.denials)
        counter[(self.current_test, operation)] += 1
        raise PermissionError("isolated tests denied " + operation)

    def path(self, value, write=False):
        if isinstance(value, int) or value is None:
            return
        path = Path(os.fsdecode(value)).resolve()
        if _within(path, self.scratch):
            return
        if not write and _within(path, self.runtime):
            return
        # Only source/static text is readable from the checkout. In particular,
        # real settings, stores, logs, screenshots and release archives are not.
        if not write and _within(path, ROOT) and (
            path.suffix.lower() in {".py", ".pyc", ".pyi", ".html", ".js", ".css"}
            or path.name in {"VERSION", "requirements.txt"}
            or path == ROOT / "lab" / "scripts" / "irica.iss"
            or path == ROOT / "panel_tray.pyw"
            or path == ROOT / "start_telegram_relay_chain.bat"
            or path == ROOT / "lab" / "design" / "irica-brand.md"
            or path in {ROOT / "host" / "src" / "Schema.php",
                        ROOT / "host" / "src" / "Http.php",
                        ROOT / "host" / "migrations" / "001_initial.sql"}
            or path.name.endswith(".example.json")
        ):
            return
        self.deny("filesystem-write" if write else "filesystem-read")

    @staticmethod
    def loopback(host):
        if host == "localhost":
            return "127.0.0.1"
        try:
            value = ipaddress.ip_address(host)
            return str(value) if value.is_loopback else None
        except (ValueError, TypeError):
            return None

    def endpoint(self, address):
        if not isinstance(address, tuple) or len(address) < 2:
            self.deny("non-IP-socket")
        host = self.loopback(address[0])
        if host is None:
            self.deny("external-network")
        key = (host, int(address[1]))
        listener = self.listeners.get(key)
        if listener is None or listener.fileno() < 0:
            probe = getattr(self.local, "closed_probe", None)
            if (probe and probe["address"] == key and probe["remaining"] == 1
                    and probe["test"] == self.current_test
                    and self.closed_fixtures.get(key) == self.current_test):
                probe["remaining"] = 0
                self.expected_denials[(self.current_test, "closed-fixture-probe")] += 1
                raise ConnectionRefusedError("isolated fixture-owned closed port")
            self.deny("unregistered-loopback-port")
        return host

    def audit(self, event, args):
        if event == "open":
            mode, flags = args[1], args[2]
            write = (bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
                     if flags is not None else any(c in (mode or "") for c in "wax+"))
            self.path(args[0], write)
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod",
                       "os.truncate", "shutil.rmtree"}:
            self.path(args[0], True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.path(args[0], True)
            self.path(args[1], True)
        elif event == "shutil.copyfile":
            self.path(args[0])
            self.path(args[1], True)
        elif event == "socket.bind":
            if not isinstance(args[1], tuple) or self.loopback(args[1][0]) is None:
                self.deny("non-loopback-bind")
        elif event == "socket.connect":
            self.endpoint(args[1])
        elif event == "socket.sendto":
            self.endpoint(args[-1])
        elif event.startswith("socket.get") and event != "socket.gethostname":
            # Numeric loopback resolution is handled without DNS below.
            if not args or self.loopback(args[0]) is None:
                self.deny("external-DNS")
        elif event in {"subprocess.Popen", "os.system", "os.spawn", "os.exec"}:
            if not getattr(self.local, "process_allowed", False):
                self.deny("subprocess")

    def install(self):
        policy = self

        class FixtureSocket(self.original_socket):
            def bind(self, address):
                result = super().bind(address)
                bound = self.getsockname()
                key = (policy.loopback(bound[0]), bound[1])
                policy.bindings[self] = (key, policy.current_test)
                policy.closed_fixtures.pop(key, None)
                return result

            def listen(self, backlog=128):
                # listen() can implicitly bind INADDR_ANY without bind().
                try:
                    host = self.getsockname()[0]
                except OSError:
                    host = None
                if policy.loopback(host) is None:
                    policy.deny("non-loopback-listen")
                result = super().listen(backlog)
                address = self.getsockname()
                policy.listeners[(policy.loopback(address[0]), address[1])] = self
                return result

            def close(self):
                binding = policy.bindings.pop(self, None)
                if binding:
                    key, owner = binding
                    policy.closed_fixtures[key] = owner
                for address, listener in list(policy.listeners.items()):
                    if listener is self:
                        policy.listeners.pop(address, None)
                return super().close()

        socket.socket = FixtureSocket
        original_addrinfo = socket.getaddrinfo

        def addrinfo(host, port, family=0, type=0, proto=0, flags=0):
            literal = policy.endpoint((host, port))
            return original_addrinfo(literal, port, family, type, proto,
                                     flags | socket.AI_NUMERICHOST)

        def hostname(host):
            literal = policy.loopback(host)
            if literal is None:
                policy.deny("external-DNS")
            return literal

        socket.getaddrinfo = addrinfo
        socket.gethostbyname = hostname
        socket.gethostbyname_ex = lambda host: (hostname(host), [], [hostname(host)])
        socket.gethostbyaddr = lambda host: (hostname(host), [], [hostname(host)])
        socket.getfqdn = hostname

        class FixturePopen(self.original_popen):
            # Keep Popen a class: asyncio.windows_utils subclasses it at import.
            def __init__(self, args, *positional, **kwargs):
                command = list(args) if isinstance(args, (tuple, list)) else []
                worker_budget = policy.trusted_worker(command)
                process_budget = 60
                self._isolation_timer = None
                if kwargs.get("shell") or kwargs.get("executable") is not None:
                    policy.deny("shell-or-executable-override")
                if worker_budget:
                    command = [sys.executable, "-I", "-B", str(SCRIPT),
                               "--scratch", str(policy.scratch), "--fixture-worker",
                               *command[3:]]
                    process_budget = worker_budget
                elif (len(command) == 3 and policy.node
                      and Path(command[0]).resolve() == Path(policy.node).resolve()
                      and command[1] == "--check"):
                    policy.path(command[2])
                    if not _within(Path(command[2]).resolve(), policy.scratch):
                        policy.deny("non-fixture-node-source")
                else:
                    policy.deny("subprocess")
                kwargs["cwd"] = str(policy.scratch)
                kwargs["env"] = dict(os.environ)
                policy.local.process_allowed = True
                try:
                    super().__init__(command, *positional, **kwargs)
                finally:
                    policy.local.process_allowed = False
                policy.processes.append(self)
                test_id = policy.current_test

                def terminate_overdue():
                    if self.poll() is None:
                        policy.process_errors.append({"test": test_id,
                                                      "timeout": process_budget})
                        try:
                            self.kill()
                        except ProcessLookupError:
                            pass

                self._isolation_timer = threading.Timer(process_budget, terminate_overdue)
                self._isolation_timer.daemon = True
                self._isolation_timer.start()

            def wait(self, *args, **kwargs):
                result = super().wait(*args, **kwargs)
                timer = getattr(self, "_isolation_timer", None)
                if timer is not None:
                    timer.cancel()
                return result

        subprocess.Popen = FixturePopen
        sys.addaudithook(self.audit)


def _denial_rows(counter):
    return [{"test": test, "operation": operation, "count": count}
            for (test, operation), count in sorted(counter.items())]


def _child(name, scratch, fixture_worker=None):
    global _POLICY
    _environment(scratch)
    _POLICY = IsolationPolicy(scratch)
    _POLICY.install()
    sys.modules.setdefault("run_isolated_tests", sys.modules[__name__])

    for path in (str(ROOT), str(TESTS)):
        sys.path.insert(0, path)
    # app_paths itself only defines helpers; it has no migration side effects.
    # Patch immediately, before any application caller or test can import it.
    import app_paths
    app_paths.bootstrap = lambda: []

    if fixture_worker:
        command = [sys.executable, "-I", "-B", *fixture_worker]
        if not _POLICY.trusted_worker(command):
            _POLICY.deny("unapproved-fixture-worker")
        import runpy
        sys.argv = list(fixture_worker)
        code = 0
        try:
            runpy.run_path(fixture_worker[0], run_name="__main__")
        except SystemExit as exit_status:
            code = (0 if exit_status.code is None else exit_status.code
                    if isinstance(exit_status.code, int) else 1)
        if _POLICY.denials:
            print("ISOLATED_WORKER_DENIALS " + json.dumps(_denial_rows(_POLICY.denials)),
                  flush=True)
            return 1
        return code

    thread_errors = []
    original_hook = threading.excepthook

    def thread_hook(args):
        thread_errors.append({"thread": args.thread.name,
                              "type": args.exc_type.__name__,
                              "error": str(args.exc_value)[:200]})
        original_hook(args)

    threading.excepthook = thread_hook

    class ReportingResult(unittest.TextTestResult):
        def startTest(self, test):
            _POLICY.current_test = test.id()
            super().startTest(test)

    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(name)
    except Exception as error:
        def import_failure(failure=error):
            raise failure
        suite = unittest.TestSuite([unittest.FunctionTestCase(
            import_failure, description="import " + name)])
    count = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportingResult).run(suite)
    for process in _POLICY.processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
    # Observe all fixture threads under one total budget, including daemons.
    deadline = time.monotonic() + 2
    for thread in threading.enumerate():
        if thread is not threading.current_thread():
            thread.join(timeout=max(0, deadline - time.monotonic()))
    # Custom thread names can contain credentials or fixture data. Retain only
    # conventional generated names; never serialize arbitrary thread metadata.
    unclosed_threads = [
        thread.name if re.fullmatch(r"Thread-\d+( \([A-Za-z_][A-Za-z0-9_]*\))?", thread.name)
        else "Thread (redacted)"
        for thread in threading.enumerate()
        if thread is not threading.current_thread() and thread.is_alive()
    ]
    sources = {}
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if file and str(file).endswith(".py"):
            path = Path(file).resolve()
            if _within(path, ROOT):
                sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    summary = {
        "module": name, "tests": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "failure_ids": [test.id() for test, _ in result.failures],
        "error_ids": [test.id() for test, _ in result.errors],
        "thread_errors": thread_errors, "unclosed_threads": unclosed_threads,
        "denials": _denial_rows(_POLICY.denials),
        "expected_denials": _denial_rows(_POLICY.expected_denials),
        "process_errors": _POLICY.process_errors, "pid": os.getpid(),
        "source_sha256": sources, "interpreter": sys.executable,
    }
    summary["ok"] = (result.wasSuccessful() and count > 0 and not loader.errors
                     and not thread_errors and not unclosed_threads
                     and not _POLICY.denials and not _POLICY.process_errors)
    (scratch / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("ISOLATED_MODULE " + json.dumps({k: v for k, v in summary.items()
                                         if k != "source_sha256"}), flush=True)
    if unclosed_threads:
        # This is the disposable isolated child, never a foreign process.
        # A live non-daemon must not hang Python shutdown; daemons must not
        # continue mutating fixtures after their failing result was emitted.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.flush()
        os._exit(1)
    return 0 if summary["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", help="Bare test module names; default: release baseline")
    parser.add_argument("--timeout", type=float, default=180, help="Hard timeout per module, seconds")
    parser.add_argument("--report", type=Path, help="Optional sanitized JSON result outside app data")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--scratch", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--fixture-worker", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child or args.fixture_worker:
        if not args.scratch or not args.scratch.is_dir():
            parser.error("child requires an existing supervisor scratch directory")
        return _child(args.child, args.scratch.resolve(), args.fixture_worker)
    names = args.modules or list(DEFAULT)
    for name in names:
        if not re.fullmatch(r"test_[A-Za-z0-9_]+", name) or not (TESTS / (name + ".py")).is_file():
            parser.error("missing or invalid test module: " + name)
    if not 0 < args.timeout <= 600:
        parser.error("timeout must be greater than zero and at most 600 seconds")
    summaries = []
    with tempfile.TemporaryDirectory(prefix="irica_isolated_") as temporary:
        run_root = Path(temporary)
        for index, name in enumerate(names):
            scratch = run_root / (str(index) + "_" + name)
            scratch.mkdir()
            command = [sys.executable, "-I", "-B", str(SCRIPT), "--child", name,
                       "--scratch", str(scratch)]
            print("ISOLATED_START " + name, flush=True)
            try:
                completed = subprocess.run(command, cwd=scratch, timeout=args.timeout)
                result_file = scratch / "result.json"
                summary = (json.loads(result_file.read_text(encoding="utf-8"))
                           if result_file.is_file() else {"module": name, "ok": False,
                                                         "process_exit": completed.returncode})
                if completed.returncode:
                    summary["ok"] = False
            except subprocess.TimeoutExpired:
                summary = {"module": name, "ok": False, "timeout": args.timeout}
            summaries.append(summary)
    report = {"modules": summaries, "tests": sum(s.get("tests", 0) for s in summaries),
              "failures": sum(s.get("failures", 0) for s in summaries),
              "errors": sum(s.get("errors", 0) for s in summaries),
              "skipped": sum(s.get("skipped", 0) for s in summaries),
              "ok": all(s["ok"] for s in summaries)}
    if args.report:
        args.report.resolve().write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ISOLATED_TOTAL " + json.dumps({k: v for k, v in report.items() if k != "modules"}), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
