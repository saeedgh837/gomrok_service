"""Regression checks for the isolated runner; execute through that runner."""
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.request

import run_isolated_tests as runner


class IsolatedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.policy = runner._POLICY
        self.assertIsNotNone(self.policy, "run through lab/scripts/run_isolated_tests.py")

    def test_paths_and_worker_switches_are_set_before_application_imports(self):
        for key in ("IRICA_DATA_DIR", "IRICA_CRM_STORE", "IRICA_ESTELAM_STORE",
                    "IRICA_SYSTEM_LOG_DIR", "HOME", "USERPROFILE", "TEMP", "APPDATA",
                    "LOCALAPPDATA", "XDG_DATA_HOME"):
            self.assertTrue(runner._within(Path(os.environ[key]), self.policy.scratch), key)
        self.assertEqual(os.environ["IRICA_CLOUD_WORKER_OFF"], "1")
        self.assertEqual(os.environ["IRICA_CRM_SCHEDULER_OFF"], "1")
        self.assertTrue(sys.dont_write_bytecode)
        self.assertFalse(any(key.upper().endswith("_PROXY") and key.upper() != "NO_PROXY"
                             for key in os.environ))
        self.assertFalse(any(any(marker in key.upper() for marker in
                                 ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))
                             for key in os.environ))
        import app_paths
        with patch.object(app_paths, "migrate_legacy") as migrate:
            self.assertEqual(app_paths.bootstrap(), [])
            migrate.assert_not_called()
        import panel
        self.assertEqual(panel.DATA_DIR, os.environ["IRICA_DATA_DIR"])

    def test_external_dns_is_denied_without_resolving(self):
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            socket.getaddrinfo("example.invalid", 443)
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            socket.gethostbyaddr("203.0.113.1")

    def test_non_loopback_bind_is_denied(self):
        with socket.socket() as fixture:
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                fixture.bind(("0.0.0.0", 0))

    def test_implicit_wildcard_listen_is_denied(self):
        with socket.socket() as fixture:
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                fixture.listen()

    def test_unregistered_loopback_forwarder_is_denied(self):
        with self.policy.original_socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                socket.create_connection(("127.0.0.1", port), timeout=0.2)

    def test_closed_fixture_probe_is_exact_and_does_not_send_packets(self):
        fixture = socket.socket()
        fixture.bind(("127.0.0.1", 0))
        address = fixture.getsockname()
        fixture.close()
        with self.policy.expect_closed_fixture_probe(address):
            # Numeric lookup is intercepted before connect, so no other local
            # process can receive a packet even if the OS reuses the port.
            with self.assertRaises(ConnectionRefusedError):
                socket.create_connection(address, timeout=0.2)
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                socket.create_connection(address, timeout=0.2)
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                socket.create_connection(("203.0.113.1", 443), timeout=0.2)

    def test_closed_fixture_context_requires_provenance_and_an_attempt(self):
        with self.assertRaises(AssertionError):
            with self.policy.expect_closed_fixture_probe(("127.0.0.1", 1)):
                pass
        fixture = socket.socket()
        fixture.bind(("127.0.0.1", 0))
        address = fixture.getsockname()
        fixture.close()
        with self.assertRaises(AssertionError):
            with self.policy.expect_closed_fixture_probe(address):
                pass

    def test_direct_connect_ex_cannot_bypass_the_guard(self):
        with socket.socket() as client:
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                client.connect_ex(("203.0.113.1", 443))

    def test_only_a_live_in_process_fixture_can_receive_http(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"synthetic fixture")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2) as response:
                self.assertEqual(response.read(), b"synthetic fixture")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2)

    def test_real_requests_session_get_is_denied(self):
        import requests
        with self.policy.expect_denial(), self.assertRaises(requests.exceptions.ConnectionError):
            requests.Session().get("https://example.invalid/never-contact", timeout=0.2)

    def test_real_checkout_config_and_copy_are_denied_before_read(self):
        source = runner.ROOT / "profiles.json"
        for operation in (lambda: source.read_bytes(),
                          lambda: io.open(source, "rb"),
                          lambda: os.open(source, os.O_RDONLY),
                          lambda: shutil.copy2(source, self.policy.scratch / "forbidden.json")):
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                operation()
        self.assertFalse((self.policy.scratch / "forbidden.json").exists())

    def test_installed_runtime_and_checkout_writes_are_denied(self):
        for path in (runner.ROOT / "forbidden-isolated-test.tmp",
                     self.policy.runtime / "forbidden-isolated-test.tmp"):
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                path.write_text("must never be written", encoding="utf-8")
            self.assertFalse(path.exists())

    def test_existing_checkout_archives_cannot_be_read(self):
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            (runner.ROOT / "synthetic-denied-name.zip").read_bytes()

    def test_popen_remains_a_class_for_windows_asyncio(self):
        self.assertTrue(issubclass(subprocess.Popen, self.policy.original_popen))
        self.assertEqual(sys.stdout.encoding.lower(), "utf-8")

    def test_unapproved_child_processes_cannot_escape_network_guard(self):
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])

    def test_trusted_crm_worker_protocol_is_path_and_mode_bounded(self):
        command = [sys.executable, "-I", "-B",
                   str(runner.TESTS / "test_registration_identity.py"),
                   "--crm-lock-worker", str(self.policy.scratch / "store.json"),
                   "increment", str(self.policy.scratch / "ready"),
                   str(self.policy.scratch / "proceed")]
        self.assertEqual(self.policy.trusted_worker(command), 5)
        command[6] = "hold"
        self.assertEqual(self.policy.trusted_worker(command), 5)
        command[6] = "unapproved"
        self.assertIsNone(self.policy.trusted_worker(command))
        command[6] = "increment"
        self.assertIsNone(self.policy.trusted_worker(command + ["extra"]))
        command[5] = str(runner.ROOT / "crm_store.json")
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            self.policy.trusted_worker(command)

    def test_trusted_gate_worker_protocol_is_path_and_mode_bounded(self):
        command = [sys.executable, "-I", "-B",
                   str(runner.TESTS / "test_maintenance_gate.py"), "--gate-worker",
                   "wait", str(self.policy.scratch / "install"),
                   str(self.policy.scratch / "registry.json")]
        for mode in ("wait", "admit", "hold-event", "install"):
            command[5] = mode
            self.assertEqual(self.policy.trusted_worker(command), 15)
        command[5] = "unapproved"
        self.assertIsNone(self.policy.trusted_worker(command))
        command[5] = "wait"
        command[7] = str(runner.ROOT / "profiles.json")
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            self.policy.trusted_worker(command)

    def test_trusted_attempt_worker_protocol_is_path_and_mode_bounded(self):
        command = [sys.executable, "-I", "-B",
                   str(runner.TESTS / "test_registration_attempts.py"),
                   "--attempt-worker", str(self.policy.scratch / "attempt"),
                   "claim-publish", str(self.policy.scratch / "ready"),
                   str(self.policy.scratch / "proceed")]
        for mode in ("claim-publish", "claim-assert"):
            command[6] = mode
            self.assertEqual(self.policy.trusted_worker(command), 6)
        command[6] = "unapproved"
        self.assertIsNone(self.policy.trusted_worker(command))
        command[6] = "claim-publish"
        self.assertIsNone(self.policy.trusted_worker(command + ["extra"]))
        command[5] = str(runner.ROOT)
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            self.policy.trusted_worker(command)

    def test_trusted_handoff_worker_protocol_is_path_and_mode_bounded(self):
        command = [sys.executable, "-I", "-B",
                   str(runner.TESTS / "test_installer_handoff.py"), "--handoff-worker",
                   "hold-event", str(self.policy.scratch / "install"),
                   str(self.policy.scratch / "control.json")]
        for mode in ("hold-event", "reject-created", "reject-pid", "no-ready"):
            command[5] = mode
            self.assertEqual(self.policy.trusted_worker(command), 15)
        command[5] = "unapproved"
        self.assertIsNone(self.policy.trusted_worker(command))
        command[5] = "hold-event"
        self.assertIsNone(self.policy.trusted_worker(command + ["extra"]))
        command[7] = str(runner.ROOT / "control.json")
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            self.policy.trusted_worker(command)

    def test_trusted_driver_lock_worker_validates_nested_fixture_paths(self):
        import json
        manifest = self.policy.scratch / "driver-lock-manifest.json"
        payload = {"primary": str(self.policy.scratch / "driver.exe"),
                   "extras": [str(self.policy.scratch / "override.exe")],
                   "data_root": str(self.policy.scratch / "driver-data")}
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        command = [sys.executable, "-I", "-B",
                   str(runner.TESTS / "test_driver_autoupdate_hook.py"),
                   "--driver-lock-worker", "hold", str(manifest),
                   str(self.policy.scratch / "ready")]
        self.assertEqual(self.policy.trusted_worker(command), 15)
        command[5] = "unapproved"
        self.assertIsNone(self.policy.trusted_worker(command))
        command[5] = "hold"
        self.assertIsNone(self.policy.trusted_worker(command + ["extra"]))
        for key in ("primary", "data_root", "extras"):
            escaped = dict(payload)
            escaped[key] = [str(runner.ROOT / "driver.exe")] if key == "extras" else str(runner.ROOT)
            manifest.write_text(json.dumps(escaped), encoding="utf-8")
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                self.policy.trusted_worker(command)
        malformed = ["{}", "not-json", json.dumps({**payload, "extras": [payload["primary"]] * 3}),
                     json.dumps({**payload, "primary": None}), " " * 16385]
        for content in malformed:
            manifest.write_text(content, encoding="utf-8")
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                self.policy.trusted_worker(command)

    def test_worker_executable_and_shell_cannot_be_overridden(self):
        command = [sys.executable, "-I", "-B",
                   str(runner.TESTS / "test_maintenance_gate.py"), "--gate-worker",
                   "wait", str(self.policy.scratch / "install"),
                   str(self.policy.scratch / "registry.json")]
        for kwargs in ({"shell": True}, {"executable": sys.executable}):
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                subprocess.Popen(command, **kwargs)

    def test_tray_static_read_does_not_allow_private_data_write_or_execution(self):
        import ast
        source = runner.ROOT / "panel_tray.pyw"
        self.assertTrue(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)).body)
        for private_path in (runner.ROOT / "profiles.json", runner.ROOT / "private.pyw"):
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                private_path.read_bytes()
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            source.write_text("must never be written", encoding="utf-8")
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            subprocess.Popen([sys.executable, "-I", "-B", str(source)])

    def test_design_and_relay_sources_are_exact_read_only_exceptions(self):
        sources = (runner.ROOT / "lab" / "design" / "irica-brand.md",
                   runner.ROOT / "start_telegram_relay_chain.bat")
        for source in sources:
            self.assertTrue(source.read_text(encoding="utf-8"))
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                source.write_text("must never be written", encoding="utf-8")
        for private in (runner.ROOT / "lab" / "design" / "private.md",
                        runner.ROOT / "private.bat", runner.ROOT / "settings.json"):
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                private.read_bytes()
        with self.policy.expect_denial(), self.assertRaises(PermissionError):
            subprocess.Popen([str(sources[1])])

    def test_exact_host_schema_sources_are_read_only_and_private_data_stays_denied(self):
        sources = (runner.ROOT / "host" / "src" / "Schema.php",
                   runner.ROOT / "host" / "src" / "Http.php",
                   runner.ROOT / "host" / "migrations" / "001_initial.sql")
        for source in sources:
            self.assertTrue(source.read_text(encoding="utf-8"))
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                source.write_text("must never be written", encoding="utf-8")
        for private in (runner.ROOT / "host" / "config.php",
                        runner.ROOT / "host" / "config.json",
                        runner.ROOT / "host" / "migrations" / "private.sql"):
            with self.policy.expect_denial(), self.assertRaises(PermissionError):
                private.read_bytes()

    def test_source_text_remains_readable_for_contract_checks(self):
        self.assertIn("def main", (runner.ROOT / "panel.py").read_text(encoding="utf-8"))
        self.assertTrue((runner.ROOT / "lab" / "scripts" / "irica.iss").read_text(encoding="utf-8"))


class IsolatedChildLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runner._POLICY, "run through the isolated supervisor")
        self.saved_policy = runner._POLICY

    @contextmanager
    def child_report(self, *, daemon, leak, name="Thread-9001 (fixture_wait)",
                     expected_name="Thread-9001 (fixture_wait)", empty=False):
        """Exercise the real child/report flow and always release our own waiter."""
        import app_paths
        from types import ModuleType

        release = threading.Event()
        ready = threading.Event()
        worker = threading.Thread(
            target=lambda: (ready.set(), release.wait()), name=name, daemon=daemon)
        scratch = self.saved_policy.scratch / self.id().rsplit(".", 1)[-1] / str(daemon)
        scratch.mkdir(parents=True, exist_ok=True)
        fixture_name = "_isolated_thread_lifecycle_fixture"
        fixture = ModuleType(fixture_name)

        def run_fixture(_test):
            worker.start()
            self.assertTrue(ready.wait(timeout=2), "Synthetic waiter did not start")
            if not leak:
                release.set()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())

        if not empty:
            fixture.SyntheticThreadTest = type(
                "SyntheticThreadTest", (unittest.TestCase,),
                {"__module__": fixture_name, "test_waiter": run_fixture})
        policy = runner.IsolationPolicy(scratch)
        original_hook = threading.excepthook
        original_path = list(sys.path)
        original_bootstrap = app_paths.bootstrap
        before = {thread for thread in threading.enumerate()
                  if thread is not threading.current_thread()}

        # Restrict only infrastructure thread enumeration: no application test
        # receives a fake policy, path override, or network/process permission.
        real_enumerate = threading.enumerate

        def fixture_threads():
            return [thread for thread in real_enumerate()
                    if thread is worker or thread not in before]

        def exit_after_report(code):
            result = json.loads((scratch / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["unclosed_threads"], [expected_name])
            self.assertIn('"unclosed_threads": ' + json.dumps([expected_name]), emitted.getvalue())
            self.assertTrue(emitted.flushed, "Result was not flushed before forced exit")
            self.assertEqual(code, 1)
            raise SystemExit(code)

        class Output(io.StringIO):
            flushed = False

            def flush(self):
                self.flushed = True
                return super().flush()

        emitted = Output()
        missing = object()
        previous_module = sys.modules.get(fixture_name, missing)
        sys.modules[fixture_name] = fixture
        try:
            with patch.object(runner, "_environment"), \
                    patch.object(runner, "IsolationPolicy", return_value=policy), \
                    patch.object(policy, "install"), \
                    patch.object(threading, "enumerate", side_effect=fixture_threads), \
                    patch.object(sys, "stdout", emitted), \
                    patch.object(sys, "stderr", io.StringIO()), \
                    patch.object(runner.os, "_exit", side_effect=exit_after_report) as exit_process:
                if leak:
                    with self.assertRaises(SystemExit) as stopped:
                        runner._child(fixture_name, scratch)
                    self.assertEqual(stopped.exception.code, 1)
                    self.assertEqual(exit_process.call_count, 1)
                else:
                    self.assertEqual(runner._child(fixture_name, scratch), 1 if empty else 0)
                    exit_process.assert_not_called()
                yield json.loads((scratch / "result.json").read_text(encoding="utf-8"))
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(timeout=2)
            threading.excepthook = original_hook
            sys.path[:] = original_path
            app_paths.bootstrap = original_bootstrap
            runner._POLICY = self.saved_policy
            if previous_module is missing:
                sys.modules.pop(fixture_name, None)
            else:
                sys.modules[fixture_name] = previous_module
            self.assertFalse(worker.is_alive(), "The regression test leaked its own waiter")

    def test_waiting_daemon_cannot_produce_a_green_child_report(self):
        with self.child_report(daemon=True, leak=True) as report:
            self.assertEqual(report["tests"], 1)
            self.assertEqual(report["errors"], 0)
            self.assertEqual(report["thread_errors"], [])
            self.assertFalse(report["ok"])
            self.assertEqual(report["unclosed_threads"], ["Thread-9001 (fixture_wait)"])

    def test_waiting_nondaemon_reports_failure_before_child_exit(self):
        with self.child_report(daemon=False, leak=True) as report:
            self.assertFalse(report["ok"])
            self.assertEqual(report["unclosed_threads"], ["Thread-9001 (fixture_wait)"])

    def test_released_thread_is_not_reported_as_a_leak(self):
        with self.child_report(daemon=True, leak=False) as report:
            self.assertTrue(report["ok"])
            self.assertIn("unclosed_threads", report)
            self.assertEqual(report["unclosed_threads"], [])

    def test_empty_module_cannot_pass_even_when_unittest_is_successful(self):
        with self.child_report(daemon=True, leak=False, empty=True) as report:
            self.assertEqual(report["tests"], 0)
            self.assertEqual(report["errors"], 0)
            self.assertFalse(report["ok"])
            self.assertEqual(report["unclosed_threads"], [])

    def test_thread_names_are_redacted_in_the_child_result(self):
        with self.child_report(daemon=True, leak=True,
                               name="PRIVATE-CANARY-token-123\nunsafe",
                               expected_name="Thread (redacted)") as report:
            self.assertEqual(report["unclosed_threads"], ["Thread (redacted)"])
            self.assertNotIn("PRIVATE-CANARY", json.dumps(report))


class PanelRunnerFrontDoorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runner._POLICY, "run through the isolated supervisor")
        self.source = runner.ROOT / "lab" / "scripts" / "run_panel_tests.py"

    def load_front_door(self):
        spec = importlib.util.spec_from_file_location("_panel_runner_front_door_test", self.source)
        module = importlib.util.module_from_spec(spec)
        # Importing a CLI must not create scratch state or discover app tests.
        with patch.object(runner.tempfile, "mkdtemp",
                          side_effect=AssertionError("Front-door import created scratch state")), \
                patch.object(unittest.TestLoader, "loadTestsFromName",
                             side_effect=AssertionError("Front-door import loaded application tests")):
            spec.loader.exec_module(module)
        return module

    def stub_supervisor(self, *, status=0, raises=False):
        from types import SimpleNamespace
        observed = []

        def main():
            observed.append(list(sys.argv))
            if raises:
                raise SystemExit(status)
            return status

        return SimpleNamespace(SCRIPT=runner.SCRIPT, main=main), observed

    def test_import_is_inert_and_preserves_process_state(self):
        path_before, argv_before = list(sys.path), list(sys.argv)
        environment_before = dict(os.environ)
        front = self.load_front_door()
        self.assertTrue(callable(getattr(front, "main", None)))
        self.assertEqual(sys.path, path_before)
        self.assertEqual(sys.argv, argv_before)
        self.assertEqual(dict(os.environ), environment_before)

    def test_exact_supervisor_import_does_not_import_application_modules(self):
        import builtins
        front = self.load_front_door()
        original_import = builtins.__import__
        imports = []

        def observe(name, *args, **kwargs):
            imports.append(name)
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=observe):
            supervisor = front._load_supervisor()
        self.assertEqual(Path(supervisor.__file__).resolve(), runner.SCRIPT)
        self.assertIsNone(supervisor._POLICY)
        self.assertTrue(callable(supervisor.main))
        self.assertFalse({"panel", "app_paths", "crm"}.intersection(imports))

    def test_explicit_selection_and_options_delegate_without_default_expansion(self):
        front = self.load_front_door()
        supervisor, observed = self.stub_supervisor(status=1)
        saved_argv = sys.argv
        report = runner._POLICY.scratch / "selected-front-door.json"
        with patch.object(front, "_load_supervisor", return_value=supervisor):
            status = front.main(["test_profile_pool", "test_runtime_heartbeat",
                                 "--timeout", "9", "--report", str(report)])
        self.assertEqual(status, 1)
        self.assertEqual(observed, [[str(runner.SCRIPT), "test_profile_pool",
                                     "test_runtime_heartbeat", "--timeout", "9.0",
                                     "--report", str(report)]])
        self.assertIs(sys.argv, saved_argv)
        self.assertFalse(report.exists())

    def test_default_inventory_delegates_all_modules_with_or_without_options(self):
        front = self.load_front_door()
        self.assertIn("test_server_crm_v2_authority", front.DEFAULT)
        self.assertEqual(len(front.DEFAULT), 115)
        self.assertEqual(len(set(front.DEFAULT)), 115)
        for options, timeout in (([], "180.0"), (["--timeout", "7"], "7.0")):
            with self.subTest(options=options):
                supervisor, observed = self.stub_supervisor()
                with patch.object(front, "_load_supervisor", return_value=supervisor):
                    self.assertEqual(front.main(options), 0)
                self.assertEqual(observed, [[str(runner.SCRIPT), *front.DEFAULT,
                                             "--timeout", timeout]])

    def test_process_argv_is_restored_when_supervisor_exits(self):
        front = self.load_front_door()
        supervisor, observed = self.stub_supervisor(status=7, raises=True)
        original = sys.argv
        with patch.object(front, "_load_supervisor", return_value=supervisor), \
                patch.object(sys, "argv", [str(self.source), "test_profile_pool"]):
            active_argv = sys.argv
            with self.assertRaises(SystemExit) as stopped:
                front.main()
            self.assertEqual(stopped.exception.code, 7)
            self.assertIs(sys.argv, active_argv)
        self.assertIs(sys.argv, original)
        self.assertEqual(observed[0][1], "test_profile_pool")

    def test_missing_selection_fails_before_any_child_can_start(self):
        front = self.load_front_door()
        with patch.object(subprocess, "run", side_effect=AssertionError("Unexpected child")), \
                patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                front.main(["test_front_door_missing_synthetic_module"])
        self.assertEqual(stopped.exception.code, 2)

    def test_help_is_available_without_loading_the_supervisor(self):
        front = self.load_front_door()
        output = io.StringIO()
        with patch.object(front, "_load_supervisor", side_effect=AssertionError("Unexpected import")), \
                patch.object(sys, "stdout", output):
            with self.assertRaises(SystemExit) as stopped:
                front.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--timeout", output.getvalue())
        self.assertIn("--report", output.getvalue())

    def test_real_supervisor_builds_exact_isolated_commands_and_propagates_failure(self):
        front = self.load_front_door()
        supervisor = front._load_supervisor()
        fixtures = runner._POLICY.scratch / "front-door-synthetic-modules"
        fixtures.mkdir()
        names = ["test_front_door_synthetic_pass", "test_front_door_synthetic_hollow"]
        for name in names:
            (fixtures / (name + ".py")).write_text("SYNTHETIC = True\n", encoding="utf-8")
        supervisor.TESTS = fixtures
        report = runner._POLICY.scratch / "front-door-supervisor-result.json"
        commands, workspaces = [], []

        def finish_synthetic_child(command, *, cwd, timeout):
            commands.append(command)
            workspaces.append(Path(cwd))
            self.assertEqual(timeout, 5.0)
            module = command[5]
            clean = module == names[0]
            result = {"module": module, "tests": int(clean), "failures": 0,
                      "errors": 0, "skipped": 0, "ok": clean}
            (Path(cwd) / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0 if clean else 1)

        with patch.object(front, "_load_supervisor", return_value=supervisor), \
                patch.object(subprocess, "run", side_effect=finish_synthetic_child), \
                patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(front.main([*names, "--timeout", "5", "--report", str(report)]), 1)
        self.assertEqual(len(commands), 2)
        for name, command, workspace in zip(names, commands, workspaces):
            self.assertEqual(command, [sys.executable, "-I", "-B", str(runner.SCRIPT),
                                       "--child", name, "--scratch", str(workspace)])
            self.assertTrue(runner._within(workspace, runner._POLICY.scratch))
        self.assertNotEqual(workspaces[0], workspaces[1])
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual([item["module"] for item in result["modules"]], names)
        self.assertEqual(result["tests"], 1)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
