"""Synthetic, connection-free checks for the Phase B fixture boundary."""

import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import run_host_v2_tests as runner
from run_host_v2_tests import validate_fixture

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "lab" / "fixtures" / "host_v2" / "bootstrap.php"


def fixture_config():
    return {
        "fixture": True,
        "run_id": "0123456789abcdef",
        "instance_id": "synthetic-instance",
        "php_path": "php",
        "migrate": {"host": "127.0.0.1", "port": 3307,
                    "dbname": "irica_b_test_0123456789abcdef",
                    "user": "fixture_ddl", "password": "synthetic-secret"},
        "runtime": {"host": "127.0.0.1", "port": 3307,
                    "dbname": "irica_b_test_0123456789abcdef",
                    "user": "fixture_dml", "password": "synthetic-secret"},
    }


_READY_LINE = "FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00 tables=0"
# The secret-shaped token never appears in this source: the synthetic child
# rebuilds it from character codes at run time (Phase A packaging-scanner style)
# so neither argv nor the assertion literal carries a credential-shaped literal.
_SECRET_CODES = (115, 121, 116, 104, 101, 116, 105, 99, 45, 115, 101, 99, 114, 101, 116)


def synthetic_child(lines):
    """Command whose argv carries only char codes and @SECRET@ templates."""
    script = ("import json, sys\n"
              "secret = ''.join(chr(c) for c in json.loads(sys.argv[1]))\n"
              "for template in json.loads(sys.argv[2]):\n"
              "    sys.stdout.write(template.replace('@SECRET@', secret) + chr(10))\n")
    return [sys.executable, "-I", "-B", "-c", script,
            json.dumps(list(_SECRET_CODES)), json.dumps(list(lines))]


def invalid_configs():
    """Hand-derived targets; accepting any crosses the fixture fence."""
    cases = []
    for role in ("migrate", "runtime"):
        for field, values in {
            "host": ["example.invalid", "localhost", "127.0.0.2", "127.0.0.1;port=3306", None],
            "port": [3306, "3307", 3307.0, True, None],
            "dbname": ["irica", "irica_b_test_0123456789abcdee", "irica_b_test_0123456789ABCDEF", None],
            "user": ["", " ", "bad\x00user", 123, None],
            "password": ["", "bad\x00password", 123, None],
        }.items():
            for value in values:
                cfg = fixture_config()
                cfg[role][field] = value
                cases.append(cfg)
        cfg = fixture_config()
        cfg[role]["unknown"] = "synthetic-secret"
        cases.append(cfg)
        for field in fixture_config()[role]:
            cfg = fixture_config()
            del cfg[role][field]
            cases.append(cfg)
        cfg = fixture_config()
        cfg[role] = []
        cases.append(cfg)
    for field, values in {
        "fixture": [False, 1, "true", None],
        "run_id": ["", "0123456789ABCDEf", "0123456789abcde", "0123456789abcdef\n", 123],
        "instance_id": ["", " ", "bad\x00id", 123, None],
        "php_path": ["", " ", "bad\x00path", 123, None],
    }.items():
        for value in values:
            cfg = fixture_config()
            cfg[field] = value
            cases.append(cfg)
    for field in fixture_config():
        cfg = fixture_config()
        del cfg[field]
        cases.append(cfg)
    cfg = fixture_config()
    cfg["unknown"] = "synthetic-secret"
    cases.append(cfg)
    cfg = fixture_config()
    cfg["runtime"]["user"] = cfg["migrate"]["user"]
    cases.append(cfg)
    cases.extend([None, [], "synthetic-secret", True])
    return cases


class FixtureGuardTests(unittest.TestCase):
    def test_accepts_exact_synthetic_target_without_mutation(self):
        cfg = fixture_config()
        original = copy.deepcopy(cfg)
        self.assertEqual(cfg, validate_fixture(cfg))
        self.assertEqual(original, cfg)

    def test_rejects_production_targets_types_and_unknown_fields(self):
        for index, cfg in enumerate(invalid_configs()):
            with self.subTest(case=index):
                with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                    validate_fixture(cfg)

    def test_requires_both_principals_to_match_the_marker(self):
        cfg = fixture_config()
        marker = {"run_id": "0123456789abcdef", "instance_id": "synthetic-instance",
                  "dbname": "irica_b_test_0123456789abcdef"}
        self.assertEqual(cfg, validate_fixture(cfg, marker))
        for field in marker:
            bad = dict(marker)
            bad[field] = "wrong"
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                    validate_fixture(cfg, bad)
        for bad in (None, {}, [], "synthetic-secret", dict(marker, unknown="secret")):
            with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                validate_fixture(cfg, bad)

    def test_validation_has_no_io_even_on_rejected_targets(self):
        with mock.patch("builtins.open", side_effect=AssertionError("FILE_ACCESS")), \
                mock.patch("socket.socket", side_effect=AssertionError("SOCKET_ACCESS")), \
                mock.patch("subprocess.run", side_effect=AssertionError("PROCESS_ACCESS")):
            validate_fixture(fixture_config())
            for cfg in invalid_configs():
                with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                    validate_fixture(cfg)

    def test_every_rejection_is_an_allowlisted_label_and_never_a_credential(self):
        allowed = {"FIXTURE_TARGET_REJECTED", "FIXTURE_CONFIG_MISSING",
                   "FIXTURE_ARGUMENT_REJECTED", "FIXTURE_SUITE_MISSING",
                   "FIXTURE_RUNTIME_MISSING", "FIXTURE_PREREQUISITE_FAILED",
                   "FIXTURE_BUSY"}
        secrets = ("synthetic-secret", "fixture_ddl", "fixture_dml")
        errors = []
        for cfg in invalid_configs() + [None, [], "synthetic-secret", True]:
            with self.assertRaises(ValueError) as caught:
                validate_fixture(cfg)
            errors.append(caught.exception)
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic.json"
            for raw in (b"", b"{}", b"[]", b"\xff", b'{"fixture":true}',
                        json.dumps(fixture_config()).encode()[:-1]):
                path.write_bytes(raw)
                with self.assertRaises(ValueError) as caught:
                    runner.read_fixture(path)
                errors.append(caught.exception)
        self.assertTrue(errors)
        for error in errors:
            self.assertIn(str(error), allowed)
            for secret in secrets:
                self.assertNotIn(secret, repr(error.args))

    def test_rejects_surrogates_and_unicode_whitespace(self):
        for field in ("instance_id", "php_path"):
            for value in ("\ud800", "\u0085 ", "\x1c\x1f"):
                cfg = fixture_config()
                cfg[field] = value
                with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                    validate_fixture(cfg)


class FixtureFileTests(unittest.TestCase):
    def test_reads_only_bounded_strict_json_outside_the_repository(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(fixture_config()), encoding="utf-8")
            self.assertEqual(fixture_config(), runner.read_fixture(path))
            for raw in (b"[]", b"null", b"{", b"\xff", b" " * 16385,
                        b'{"fixture":true,"fixture":false}', b'{"x":NaN}'):
                path.write_bytes(raw)
                with self.subTest(kind=raw[:1]):
                    with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                        runner.read_fixture(path)
            with self.assertRaisesRegex(ValueError, "^FIXTURE_CONFIG_MISSING$"):
                runner.read_fixture(Path(directory) / "absent.json")
        with mock.patch("pathlib.Path.open", side_effect=AssertionError("READ_FORBIDDEN")):
            for path in (ROOT / "host/config/config.php", ROOT / "synthetic.json"):
                with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                    runner.read_fixture(path)


class FixtureRunnerTests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                mock.patch.dict(os.environ, {}, clear=True):
            status = runner.main(args)
        return status, output.getvalue()

    def test_all_without_an_explicit_private_config_fails_before_processes(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("PROCESS_ACCESS")):
            status, output = self.invoke(["--suite", "all"])
        self.assertEqual(2, status)
        self.assertEqual("FIXTURE_CONFIG_MISSING\n", output)

    def test_cli_rejects_arbitrary_suites_files_sql_urls_and_abbreviations(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("PROCESS_ACCESS")):
            for args in (["--suite", "synthetic-secret.php"], ["--sql", "synthetic-secret"],
                         ["--url", "https://example.invalid/synthetic-secret"],
                         ["--file", "synthetic-secret"], ["--s", "fixture-guard"],
                         ["--suite", "fixture-guard", "--fixture-config", "synthetic.json"],
                         ["--suite", "fixture-guard", "--break-lock"],
                         ["--break-lock"]):
                status, output = self.invoke(args)
                self.assertEqual(2, status)
                self.assertEqual("FIXTURE_ARGUMENT_REJECTED\n", output)

    def test_rejected_config_never_starts_php_or_api(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic.json"
            cfg = fixture_config()
            cfg["runtime"]["host"] = "example.invalid"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with mock.patch("subprocess.run", side_effect=AssertionError("PROCESS_ACCESS")):
                status, output = self.invoke(["--suite", "all", "--fixture-config", str(path)])
            self.assertEqual(2, status)
            self.assertEqual("FIXTURE_TARGET_REJECTED\n", output)

    def test_suite_bindings_are_exact_and_a_missing_bootstrap_fails_closed_without_a_process(self):
        expected = {
            "migrations": ROOT / "lab/fixtures/host_v2/test_migrations.php",
            "integrity": ROOT / "lab/fixtures/host_v2/test_integrity.php",
            "workspaces": ROOT / "lab/fixtures/host_v2/test_workspaces.php",
            "accounts": ROOT / "lab/fixtures/host_v2/test_accounts.php",
            "sessions": ROOT / "lab/fixtures/host_v2/test_sessions.php",
            "api": ROOT / "lab/tests/test_host_v2_api.py",
        }
        for suite, path in expected.items():
            self.assertEqual(path, runner.suite_path(suite))
            # is_file() is False everywhere here, so bootstrap presence fails
            # first; per-suite existence after the gate is covered by
            # test_missing_scenario_is_detected_only_after_the_real_prerequisite_gate.
            with mock.patch.object(runner, "read_fixture", return_value=fixture_config()), \
                    mock.patch("pathlib.Path.is_file", return_value=False), \
                    mock.patch("subprocess.run", side_effect=AssertionError("PROCESS_ACCESS")):
                status, output = self.invoke(["--suite", suite, "--fixture-config", "synthetic.json"])
            self.assertEqual(2, status)
            self.assertEqual("FIXTURE_SUITE_MISSING\n", output)

    def test_invalid_private_path_is_a_fixed_label_not_a_raw_path_error(self):
        status, output = self.invoke(["--suite", "all", "--fixture-config",
                                      str(ROOT.parent / "synthetic-secret\x00.json")])
        self.assertEqual(2, status)
        self.assertEqual("FIXTURE_TARGET_REJECTED\n", output)

    def test_fixture_lock_prevents_overlapping_runs_and_releases_on_error(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory, \
                mock.patch("tempfile.gettempdir", return_value=directory):
            with runner.fixture_lock(fixture_config()):
                with self.assertRaisesRegex(ValueError, "^FIXTURE_BUSY$"):
                    with runner.fixture_lock(fixture_config()):
                        self.fail("CONCURRENT_FIXTURE_RUN")
            with self.assertRaisesRegex(RuntimeError, "^SYNTHETIC_FAILURE$"):
                with runner.fixture_lock(fixture_config()):
                    raise RuntimeError("SYNTHETIC_FAILURE")
            with runner.fixture_lock(fixture_config()):
                pass
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_pid_liveness_is_provable_without_touching_the_probe(self):
        self.assertTrue(runner.pid_alive(os.getpid()))
        for pid in (0, -1, "0", None):
            with self.subTest(pid=pid):
                self.assertFalse(runner.pid_alive(pid))
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
        child.wait(timeout=30)
        self.assertFalse(runner.pid_alive(child.pid))

    def test_stale_lock_is_taken_over_while_live_and_unreadable_locks_refuse(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory, \
                mock.patch("tempfile.gettempdir", return_value=directory):
            cfg = fixture_config()
            path = runner.lock_path(cfg)
            self.assertEqual("absent", runner.lock_state(path))
            child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
            child.wait(timeout=30)
            path.write_bytes(runner.lock_body(child.pid))
            self.assertEqual("stale", runner.lock_state(path))
            with runner.fixture_lock(cfg):
                self.assertTrue(path.is_file())
            self.assertFalse(path.exists())

            path.write_bytes(runner.lock_body(os.getpid()))
            self.assertEqual("live", runner.lock_state(path))
            with self.assertRaisesRegex(ValueError, "^FIXTURE_BUSY$"):
                with runner.fixture_lock(cfg):
                    self.fail("LIVE_LOCK_TAKEN_OVER")

            path.write_bytes(b"garbage\n")
            self.assertEqual("unreadable", runner.lock_state(path))
            with self.assertRaisesRegex(ValueError, "^FIXTURE_BUSY$"):
                with runner.fixture_lock(cfg):
                    self.fail("UNREADABLE_LOCK_TAKEN_OVER")
            path.unlink()

    def test_break_lock_clears_stale_or_unreadable_but_never_a_live_owner(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory, \
                mock.patch("tempfile.gettempdir", return_value=directory):
            cfg = fixture_config()
            path = runner.lock_path(cfg)

            def break_lock():
                with mock.patch.object(runner, "read_fixture", return_value=cfg), \
                        mock.patch("subprocess.run", side_effect=AssertionError("PROCESS_ACCESS")):
                    return self.invoke(["--suite", "migrations", "--break-lock",
                                        "--fixture-config", "synthetic.json"])

            self.assertEqual((0, "FIXTURE_LOCK_ABSENT\n"), break_lock())
            child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
            child.wait(timeout=30)
            path.write_bytes(runner.lock_body(child.pid))
            self.assertEqual((0, "FIXTURE_LOCK_CLEARED\n"), break_lock())
            self.assertFalse(path.exists())
            path.write_bytes(b"garbage\n")
            self.assertEqual((0, "FIXTURE_LOCK_CLEARED\n"), break_lock())
            self.assertFalse(path.exists())
            path.write_bytes(runner.lock_body(os.getpid()))
            self.assertEqual((2, "FIXTURE_LOCK_LIVE\n"), break_lock())
            self.assertTrue(path.is_file())

    def test_guard_loader_refuses_empty_failing_and_skipped_suites(self):
        cases = (("import unittest\n", 2),
                 ("import unittest\nclass Gate(unittest.TestCase):\n"
                  " @unittest.skip('synthetic')\n def test_gate(self): pass\n", 1),
                 ("import unittest\nclass Gate(unittest.TestCase):\n"
                  " def test_ok(self): self.assertTrue(True)\n"
                  " def test_bad(self): self.assertTrue(False)\n", 1),
                 ("import unittest\nclass Gate(unittest.TestCase):\n"
                  " def test_ok(self): self.assertTrue(True)\n", 0))
        for source, expected in cases:
            with self.subTest(expected=expected), \
                    tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory, \
                    contextlib.redirect_stderr(io.StringIO()):
                path = Path(directory) / "synthetic_guard.py"
                path.write_text(source, encoding="utf-8")
                spec = importlib.util.spec_from_file_location("synthetic_guard", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(expected, runner._run_test_module(module))

    def test_guard_suite_refuses_when_the_php_binary_is_absent(self):
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("importlib.util.spec_from_file_location",
                           side_effect=AssertionError("GUARD_MUST_NOT_LOAD")):
            status, output = self.invoke(["--suite", "fixture-guard"])
        self.assertEqual(2, status)
        self.assertEqual("FIXTURE_RUNTIME_MISSING\n", output)

    def test_missing_scenario_is_detected_only_after_the_real_prerequisite_gate(self):
        missing = ROOT / "lab" / "fixtures" / "host_v2" / "absent_scenario.php"
        self.assertFalse(missing.is_file())
        for suite in ("migrations", "integrity", "workspaces", "accounts", "sessions", "api"):
            with self.subTest(suite=suite):
                with mock.patch.object(runner, "read_fixture", return_value=fixture_config()), \
                        mock.patch.object(runner, "suite_path", return_value=missing), \
                        mock.patch("shutil.which", return_value="synthetic-php"), \
                        mock.patch.object(runner, "run_scenario", return_value=(0, 1)) as scenario:
                    status, output = self.invoke(
                        ["--suite", suite, "--fixture-config", "synthetic.json"])
                self.assertEqual(2, status)
                self.assertEqual("FIXTURE_SUITE_MISSING\n", output)
                self.assertEqual(1, scenario.call_count)
                self.assertIn("--check-prerequisites", scenario.call_args.args[0])

    def test_all_exercises_the_real_prerequisite_gate_before_failing_closed(self):
        missing = ROOT / "lab" / "fixtures" / "host_v2" / "absent_scenario.php"
        with mock.patch.object(runner, "read_fixture", return_value=fixture_config()), \
                mock.patch.object(runner, "suite_path", return_value=missing), \
                mock.patch("shutil.which", return_value="synthetic-php"), \
                mock.patch.object(runner, "_guard_suite", return_value=0), \
                mock.patch.object(runner, "run_scenario", return_value=(0, 1)) as scenario:
            status, output = self.invoke(["--suite", "all", "--fixture-config", "synthetic.json"])
        self.assertEqual(2, status)
        self.assertEqual("FIXTURE_SUITE_MISSING\n", output)
        self.assertEqual(1, scenario.call_count)
        self.assertIn("--check-prerequisites", scenario.call_args.args[0])

    def test_prerequisite_child_output_is_allowlisted_before_it_is_printed(self):
        ready = b"FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00 tables=17\n"
        self.assertEqual(["FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00 tables=17"],
                         runner.prerequisite_lines(ready))
        noisy = (b"PHP Warning: something\n"
                 b"SQLSTATE[HY000] [1045] Access denied for user synthetic-secret\n"
                 b"password=synthetic-secret\n"
                 b"  FIXTURE_DDL_CHECK_FAILED  \n"
                 b"FIXTURE_DDL_CHECK_FAILED\n")
        self.assertEqual(["FIXTURE_DDL_CHECK_FAILED"], runner.prerequisite_lines(noisy))
        self.assertEqual([], runner.prerequisite_lines(b""))
        # A READY line without the sanitized table count is not the record we print.
        self.assertEqual([], runner.prerequisite_lines(
            b"FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00\n"))
        self.assertEqual([], runner.prerequisite_lines(
            b"FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00 tables=17; rm -rf\n"))
        self.assertEqual([], runner.prerequisite_lines(
            b"FIXTURE_READY password=synthetic-secret\n"))

    def test_gate_green_requires_exactly_one_ready_attestation(self):
        cases = [
            ("no output at all", [], 0),
            ("one valid record", [_READY_LINE], 1),
            ("two identical records", [_READY_LINE, _READY_LINE], 2),
            ("two different records",
             [_READY_LINE, "FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00 tables=1"], 2),
            ("bare FIXTURE_READY is not the record", ["FIXTURE_READY"], 0),
            ("READY without the table count",
             ["FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00"], 0),
            ("only a failure label", ["FIXTURE_DDL_CHECK_FAILED"], 0),
        ]
        for label, lines, expected in cases:
            with self.subTest(case=label), contextlib.redirect_stdout(io.StringIO()):
                status, attestations = runner.run_scenario(synthetic_child(lines), capture=True)
            self.assertEqual((0, expected), (status, attestations), label)

    def test_gate_refuses_zero_or_multiple_ready_attestations(self):
        missing = ROOT / "lab" / "fixtures" / "host_v2" / "absent_scenario.php"

        def run(attestations):
            with mock.patch.object(runner, "read_fixture", return_value=fixture_config()), \
                    mock.patch.object(runner, "suite_path", return_value=missing), \
                    mock.patch("shutil.which", return_value="synthetic-php"), \
                    mock.patch.object(runner, "run_scenario",
                                      return_value=(0, attestations)) as scenario:
                status, output = self.invoke(
                    ["--suite", "integrity", "--fixture-config", "synthetic.json"])
            return status, output, scenario.call_count

        for attestations, case in ((0, "zero"), (2, "multiple")):
            with self.subTest(case=case):
                status, output, calls = run(attestations)
                self.assertEqual(2, status)
                self.assertEqual("FIXTURE_PREREQUISITE_FAILED\n", output)
                self.assertEqual(1, calls)
        # Exactly one attestation lets the run continue past the gate.
        status, output, calls = run(1)
        self.assertEqual(2, status)
        self.assertEqual("FIXTURE_SUITE_MISSING\n", output)
        self.assertEqual(1, calls)

    def test_captured_child_output_is_redacted_end_to_end(self):
        secret = "".join(chr(code) for code in _SECRET_CODES)
        noisy = ["SQLSTATE[HY000] [1045] Access denied for user `synthetic` password=@SECRET@",
                 "password=@SECRET@",
                 "PHP Warning: @SECRET@"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = runner.run_scenario(synthetic_child(noisy + [_READY_LINE]), capture=True)
        self.assertEqual((0, 1), result)
        printed = output.getvalue()
        self.assertIn(_READY_LINE, printed)
        for template in noisy:
            self.assertNotIn(template.replace("@SECRET@", secret), printed)
        self.assertNotIn(secret, printed)

    def test_remote_and_device_config_paths_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            local = Path(directory) / "synthetic.json"
            local.write_text(json.dumps(fixture_config()), encoding="utf-8")
            # An ordinary absolute local path still passes.
            self.assertEqual(fixture_config(), runner.read_fixture(local))
            self.assertTrue(runner.is_local_path(str(local)))
            for raw in (r"\\server\share\x.json", r"\\?\C:\x.json", r"\\.\pipe\x",
                        r"\\.\C:\x.json", "//server/share/x.json", "//?/C:/x.json"):
                with self.subTest(path=raw):
                    self.assertFalse(runner.is_local_path(raw))
                    with self.assertRaisesRegex(ValueError, "^FIXTURE_TARGET_REJECTED$"):
                        runner.read_fixture(raw)

    def test_prerequisite_refusal_stops_before_any_scenario_and_redacts_output(self):
        with mock.patch.object(runner, "read_fixture", return_value=fixture_config()), \
                mock.patch("pathlib.Path.is_file", return_value=True), \
                mock.patch("shutil.which", return_value="synthetic-php"), \
                mock.patch.object(runner, "run_scenario", return_value=(2, 0)) as scenario:
            status, output = self.invoke(["--suite", "integrity", "--fixture-config", "synthetic.json"])
        self.assertEqual(2, status)
        self.assertEqual("FIXTURE_PREREQUISITE_FAILED\n", output)
        self.assertEqual(1, scenario.call_count)
        self.assertIn("--check-prerequisites", scenario.call_args.args[0])
        self.assertNotIn("synthetic-secret", repr(scenario.call_args))

    def test_api_uses_isolated_python_and_only_the_private_path_environment(self):
        calls = []
        def completed(command, env=None, capture=False):
            calls.append((command, env, capture))
            # The gate must be able to attest; a scenario never attests.
            return (0, 1) if capture else (0, 0)
        with mock.patch.object(runner, "read_fixture", return_value=fixture_config()), \
                mock.patch("pathlib.Path.is_file", return_value=True), \
                mock.patch("shutil.which", return_value="synthetic-php"), \
                mock.patch.object(runner, "run_scenario", side_effect=completed):
            status, output = self.invoke(["--suite", "api", "--fixture-config", "synthetic.json"])
        self.assertEqual((0, "FIXTURE_API_PASS\n"), (status, output))
        # First call is the PHP prerequisite gate; only it may capture output.
        self.assertEqual([True, False], [call[2] for call in calls])
        self.assertIn("--check-prerequisites", calls[0][0])
        self.assertEqual([sys.executable, "-I", "-B", "-c"], calls[1][0][:4])
        self.assertEqual(str(ROOT / "lab/tests/test_host_v2_api.py"), calls[1][0][-1])
        self.assertEqual({"IRICA_B_FIXTURE_CONFIG": str(Path("synthetic.json").resolve())}, calls[1][1])
        self.assertNotIn("synthetic-secret", repr(calls))

    def test_api_loader_refuses_empty_or_skipped_test_gates(self):
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic_api.py"
            for source, expected in (("import unittest\n", 2),
                                     ("import unittest\nclass Gate(unittest.TestCase):\n"
                                      " @unittest.skip('synthetic')\n def test_gate(self): pass\n", 1),
                                     ("import unittest\nclass Gate(unittest.TestCase):\n"
                                      " def test_gate(self): self.assertTrue(True)\n", 0)):
                path.write_text(source, encoding="utf-8")
                result = subprocess.run([sys.executable, "-I", "-B", "-c", runner._API_LOADER, str(path)],
                                        capture_output=True, timeout=10, check=False)
                self.assertEqual(expected, result.returncode)

    def test_scenario_results_are_bounded_sanitized_and_not_skipped(self):
        command = ["synthetic-php", "synthetic-scenario.php"]
        for returncode, expected in ((0, 0), (1, 1), (2, 2)):
            completed = subprocess.CompletedProcess(command, returncode, b"synthetic-secret", b"synthetic-secret")
            with mock.patch("subprocess.run", return_value=completed) as process:
                self.assertEqual((expected, 0), runner.run_scenario(command))
            self.assertEqual(120, process.call_args.kwargs["timeout"])
            self.assertFalse(process.call_args.kwargs.get("shell", False))
            self.assertIs(process.call_args.kwargs.get("stdout"), subprocess.DEVNULL)
        for error, expected in ((OSError("synthetic-secret"), 2),
                                (subprocess.TimeoutExpired(command, 120, output=b"synthetic-secret"), 1)):
            with mock.patch("subprocess.run", side_effect=error):
                self.assertEqual((expected, 0), runner.run_scenario(command))


class PhpFixtureParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.php = shutil.which("php")
        if cls.php is None:
            raise AssertionError("FIXTURE_PHP_PARITY_RUNTIME_MISSING")

    def php_guard(self, payload):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        result = subprocess.run([self.php, "-n", str(BOOTSTRAP), "--validate-json"],
                                input=json.dumps(payload).encode("utf-8"),
                                capture_output=True, timeout=10, check=False)
        return result.returncode, result.stdout, result.stderr

    def test_php_and_python_agree_on_all_synthetic_target_guards(self):
        self.assertEqual((0, b"FIXTURE_VALID\n", b""),
                         self.php_guard({"config": fixture_config()}))
        for index, cfg in enumerate(invalid_configs()):
            with self.subTest(case=index):
                self.assertEqual((2, b"FIXTURE_TARGET_REJECTED\n", b""),
                                 self.php_guard({"config": cfg}))

    def test_php_and_python_agree_on_marker_identity_and_null_rejection(self):
        marker = {"run_id": "0123456789abcdef", "instance_id": "synthetic-instance",
                  "dbname": "irica_b_test_0123456789abcdef"}
        self.assertEqual((0, b"FIXTURE_VALID\n", b""),
                         self.php_guard({"config": fixture_config(), "marker": marker}))
        for bad in (None, {}, [], dict(marker, instance_id="wrong"), dict(marker, unknown="secret")):
            self.assertEqual((2, b"FIXTURE_TARGET_REJECTED\n", b""),
                             self.php_guard({"config": fixture_config(), "marker": bad}))

    def test_php_rejects_invalid_json_utf8_and_oversize_input_without_pdo(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        for raw in (b"{", b"[]", b"\xff", b" " * 16385):
            result = subprocess.run([self.php, "-n", str(BOOTSTRAP), "--validate-json"],
                                    input=raw, capture_output=True, timeout=10, check=False)
            self.assertEqual((2, b"FIXTURE_TARGET_REJECTED\n", b""),
                             (result.returncode, result.stdout, result.stderr))

    def test_php_db_rejects_unsafe_config_before_loading_product_or_pdo(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic.json"
            cfg = fixture_config()
            cfg["migrate"]["port"] = 3306
            path.write_text(json.dumps(cfg), encoding="utf-8")
            result = subprocess.run([self.php, "-n", str(BOOTSTRAP), "--check-prerequisites",
                                     "--fixture-config", str(path)],
                                    capture_output=True, timeout=10, check=False)
            self.assertEqual((2, b"FIXTURE_TARGET_REJECTED\n", b""),
                             (result.returncode, result.stdout, result.stderr))

    def test_php_refuses_remote_and_device_config_paths(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        probe = r'''
require $argv[1];
if (!BFixture::isLocalPath('C:/Users/Saeed/AppData/Local/Temp/irica-b-guard/synthetic.json')) exit(1);
foreach (['\\\\server\\share\\x.json', '\\\\?\\C:\\x.json', '\\\\.\\pipe\\x',
          '\\\\.\\C:\\x.json', '//server/share/x.json', '//?/C:/x.json'] as $remote) {
    if (BFixture::isLocalPath($remote)) exit(1);
}
print("FIXTURE_LOCAL_PATH_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", probe, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_LOCAL_PATH_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))
        env = dict(os.environ)
        env.pop("IRICA_B_FIXTURE_CONFIG", None)
        for raw in (r"\\server\share\x.json", r"\\?\C:\x.json", r"\\.\pipe\x",
                    r"\\.\C:\x.json", "//server/share/x.json"):
            with self.subTest(path=raw):
                result = subprocess.run(
                    [self.php, "-n", str(BOOTSTRAP), "--check-prerequisites",
                     "--fixture-config", raw],
                    env=env, capture_output=True, timeout=10, check=False)
                self.assertEqual((2, b"FIXTURE_TARGET_REJECTED\n", b""),
                                 (result.returncode, result.stdout, result.stderr))
        # An ordinary absolute local path must get past the reader and reach the
        # extension gate; under php -n that is FIXTURE_RUNTIME_MISSING, which
        # proves the path was accepted rather than refused.
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(fixture_config()), encoding="utf-8")
            self.assertTrue(str(path).startswith(str(directory)))
            result = subprocess.run(
                [self.php, "-n", str(BOOTSTRAP), "--check-prerequisites",
                 "--fixture-config", str(path)],
                env=env, capture_output=True, timeout=10, check=False)
            self.assertEqual((2, b"FIXTURE_RUNTIME_MISSING\n", b""),
                             (result.returncode, result.stdout, result.stderr))

    def test_php_requires_config_and_explicit_mysql_mbstring_runtime(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        env = dict(os.environ)
        env.pop("IRICA_B_FIXTURE_CONFIG", None)
        result = subprocess.run([self.php, "-n", str(BOOTSTRAP), "--check-prerequisites"],
                                env=env, capture_output=True, timeout=10, check=False)
        self.assertEqual((2, b"FIXTURE_CONFIG_MISSING\n", b""),
                         (result.returncode, result.stdout, result.stderr))
        with tempfile.TemporaryDirectory(prefix="irica-b-guard-") as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(fixture_config()), encoding="utf-8")
            result = subprocess.run([self.php, "-n", str(BOOTSTRAP), "--check-prerequisites",
                                     "--fixture-config", str(path)],
                                    env=env, capture_output=True, timeout=10, check=False)
            self.assertEqual((2, b"FIXTURE_RUNTIME_MISSING\n", b""),
                             (result.returncode, result.stdout, result.stderr))

    def test_php_server_and_ddl_permission_error_classification_is_fail_closed(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
BFixture::validateServer('mysql', '8.0.36', '+00:00');
BFixture::validateServer('mysql', '10.6.20-MariaDB', '+00:00');
foreach ([['sqlite','8.0.36','+00:00'], ['mysql','5.6.51','+00:00'],
          ['mysql','10.2.44-MariaDB','+00:00'], ['mysql','unknown','+00:00'],
          ['mysql','8.0.36','SYSTEM']] as $bad) {
    try { BFixture::validateServer(...$bad); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_SERVER_REJECTED') exit(1); }
}
BFixture::assertDdlDenied(['42000', 1142, 'synthetic-secret']);
BFixture::assertDdlDenied(['42000', 1044, 'synthetic-secret']);
foreach ([null, [], ['HY000',2002,'synthetic-secret'], ['42000',1064,'synthetic-secret'],
          ['23000',1142,'synthetic-secret'], ['42000','1142','synthetic-secret']] as $bad) {
    try { BFixture::assertDdlDenied($bad); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_DDL_CHECK_FAILED') exit(1); }
}
print("FIXTURE_SERVER_GUARDS_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_SERVER_GUARDS_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_grant_validation_covers_both_principals_without_a_role_lookup(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
$db = 'irica_b_test_0123456789abcdef';
$usage = 'GRANT USAGE ON *.* TO `synthetic`@`127.0.0.1`';
$marker = 'GRANT SELECT ON `irica_b_fixture_control`.`runs` TO `synthetic`@`127.0.0.1`';
$target = function (string $privileges, string $user = 'synthetic') use ($db) {
    return "GRANT $privileges ON `$db`.* TO `$user`@`127.0.0.1`";
};
$dml = 'SELECT, INSERT, UPDATE, DELETE';
$ddl = $dml . ', CREATE, DROP, REFERENCES, INDEX, ALTER';
$runtime = [$usage, $target($dml), $marker];
$migrate = [$usage, $target($ddl, 'synthetic_ddl'), $marker];
// The provisioned scope spells its underscores escaped; only `\_` is canonicalised.
$escaped = [$usage, 'GRANT ' . $ddl . ' ON `irica\_b\_test\_0123456789abcdef`.*'
            . ' TO `synthetic_ddl`@`127.0.0.1`', $marker];
BFixture::validatePrincipalGrants($runtime, $db, false);
BFixture::validatePrincipalGrants($migrate, $db, true);
BFixture::validatePrincipalGrants($escaped, $db, true);
foreach ([
    ['GRANT ALL PRIVILEGES ON *.* TO `synthetic`@`%`'],
    array_merge($runtime, ['GRANT SELECT ON `irica`.* TO `synthetic`@`%`']),
    array_merge($runtime, ['GRANT SELECT ON `synthetic`.* TO `synthetic`@`%` WITH GRANT OPTION']),
    array_merge($runtime, ['GRANT `some_role` TO `synthetic`@`%`']),
    [$usage, $target($dml)],
    [$usage, $target('SELECT, INSERT, UPDATE, DELETE, CREATE'), $marker],
    [$usage, $target('SELECT, INSERT, UPDATE'), $marker],
    [],
    ['synthetic-secret'],
] as $bad) {
    try { BFixture::validatePrincipalGrants($bad, $db, false); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_GRANT_REJECTED') exit(1); }
}
foreach ([
    ['GRANT ALL PRIVILEGES ON *.* TO `synthetic_ddl`@`%`'],
    array_merge($migrate, ['GRANT SELECT ON `irica`.* TO `synthetic_ddl`@`%`']),
    array_merge($migrate, ['GRANT SELECT ON `synthetic_ddl`.* TO `synthetic_ddl`@`%` WITH GRANT OPTION']),
    [$usage, $target($dml, 'synthetic_ddl'), $marker],
    [$usage, $target($dml . ', CREATE, DROP, ALTER, INDEX, EXECUTE', 'synthetic_ddl'), $marker],
    [$usage, $target($ddl, 'synthetic_ddl')],
    [],
] as $bad) {
    try { BFixture::validatePrincipalGrants($bad, $db, true); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_GRANT_REJECTED') exit(1); }
}
print("FIXTURE_GRANT_GUARDS_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_GRANT_GUARDS_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_probe_objects_are_rejected_without_demanding_an_empty_target(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
BFixture::assertNoProbeObjects(0);
foreach ([1, 2, 17] as $bad) {
    try { BFixture::assertNoProbeObjects($bad); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_TARGET_NOT_EMPTY') exit(1); }
}
foreach ([-1, '1', null, 0.0] as $bad) {
    try { BFixture::assertNoProbeObjects($bad); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_TARGET_REJECTED') exit(1); }
}
print("FIXTURE_PROBE_GUARDS_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_PROBE_GUARDS_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_ready_line_records_a_sanitized_table_count_and_distro_versions(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
$line = BFixture::readyLine('mysql', '10.3.39-MariaDB', '+00:00', 17);
if ($line !== 'FIXTURE_READY driver=mysql server=10.3.39-MariaDB tz=+00:00 tables=17') exit(1);
if (BFixture::readyLine('mysql', '10.3.39-MariaDB-1:10.3.39+maria~bullseye', '+00:00', 0)
      !== 'FIXTURE_READY driver=mysql server=10.3.39-MariaDB-1:10.3.39+maria~bullseye'
          . ' tz=+00:00 tables=0') exit(1);
foreach ([['sqlite', '10.3.39-MariaDB', '+00:00', 0],
          ['mysql', "10.3.39-MariaDB\nEVIL", '+00:00', 0],
          ['mysql', '10.3.39-MariaDB extra', '+00:00', 0],
          ['mysql', '', '+00:00', 0],
          ['mysql', '10.3.39-MariaDB', '+05:30', 0]] as $bad) {
    try { BFixture::readyLine(...$bad); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_SERVER_REJECTED') exit(1); }
}
print("FIXTURE_READY_RECORD_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_READY_RECORD_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_code_expectation_fails_closed_when_api_exception_is_not_loaded(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
$ran = false;
try {
    BFixture::code('SYNTHETIC_CODE', function () use (&$ran) { $ran = true; });
    exit(1);
} catch (RuntimeException $e) {
    if ($e->getMessage() !== 'FIXTURE_EXCEPTION_CLASS_MISSING') exit(1);
}
if ($ran) exit(1);
if (class_exists('ApiException', false)) exit(1);
print("FIXTURE_CODE_FAIL_CLOSED\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_CODE_FAIL_CLOSED\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_failure_labels_stay_distinct_and_pick_the_right_exit_code(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
if (BFixture::failureLabel(new RuntimeException('FIXTURE_ASSERTION_FAILED'))
      !== 'FIXTURE_ASSERTION_FAILED') exit(1);
if (BFixture::failureLabel(new RuntimeException('FIXTURE_EXPECTED_REJECTION'))
      !== 'FIXTURE_EXPECTED_REJECTION') exit(1);
if (BFixture::failureLabel(new RuntimeException('FIXTURE_EXCEPTION_CLASS_MISSING'))
      !== 'FIXTURE_EXCEPTION_CLASS_MISSING') exit(1);
if (BFixture::failureLabel(new RuntimeException('synthetic unexpected text'))
      !== 'FIXTURE_SCENARIO_FAILED') exit(1);
if (BFixture::failureExit('FIXTURE_ASSERTION_FAILED') !== 1) exit(1);
if (BFixture::failureExit('FIXTURE_EXPECTED_REJECTION') !== 1) exit(1);
if (BFixture::failureExit('FIXTURE_SCENARIO_FAILED') !== 1) exit(1);
if (BFixture::failureExit('FIXTURE_TARGET_REJECTED') !== 2) exit(1);
if (BFixture::failureExit('FIXTURE_TARGET_NOT_EMPTY') !== 2) exit(1);
if (BFixture::failureExit('FIXTURE_EXCEPTION_CLASS_MISSING') !== 2) exit(1);
if (BFixture::failureExit('FIXTURE_DDL_CHECK_FAILED') !== 2) exit(1);
print("FIXTURE_LABELS_DISTINCT\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_LABELS_DISTINCT\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_assertion_helpers_password_and_repo_root_without_product_imports(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        code = r'''
require $argv[1];
final class ApiException extends RuntimeException {
    public function __construct(public readonly string $errorCode) { parent::__construct('synthetic'); }
}
BFixture::same(3, 3);
try { BFixture::same(3, '3'); exit(1); }
catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_ASSERTION_FAILED') exit(1); }
BFixture::code('SYNTHETIC_CODE', fn() => throw new ApiException('SYNTHETIC_CODE'));
try { BFixture::code('SYNTHETIC_CODE', fn() => null); exit(1); }
catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_EXPECTED_REJECTION') exit(1); }
try { BFixture::code('SYNTHETIC_CODE', fn() => throw new ApiException('WRONG_CODE')); exit(1); }
catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_ASSERTION_FAILED') exit(1); }
try { BFixture::code('SYNTHETIC_CODE', fn() => throw new LogicException('synthetic')); exit(1); }
catch (LogicException $e) {}
$a = BFixture::password(); $b = BFixture::password();
if (!preg_match('/\A[0-9a-f]{32}\z/', $a) || $a === $b) exit(1);
if (realpath(BFixture::repoRoot()) !== realpath($argv[2])) exit(1);
if (class_exists('Database', false)) exit(1);
print("FIXTURE_HELPERS_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP), str(ROOT)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_HELPERS_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))

    def test_php_engine_floor_matches_documented_mysql_5_7_and_mariadb_10_3(self):
        self.assertTrue(BOOTSTRAP.is_file(), "FIXTURE_BOOTSTRAP_MISSING")
        # php -n only exercises the static validator; the dynamic path must not
        # issue a statement the documented floor cannot answer either.
        source = BOOTSTRAP.read_text(encoding="utf-8")
        for forbidden in ("CURRENT_ROLE", "SET PERSIST", "JSON_TABLE", "CLONE", "ROLE_ADMIN"):
            self.assertNotIn(forbidden, source, "MYSQL8_ONLY_STATEMENT")
        code = r'''
require $argv[1];
foreach ([['mysql', '5.7.44', '+00:00'], ['mysql', '5.5.5-5.7.44', '+00:00'],
          ['mysql', '10.3.39-MariaDB', '+00:00'], ['mysql', '5.5.5-10.3.39-MariaDB', '+00:00'],
          ['mysql', '10.6.20-MariaDB', '+00:00'], ['mysql', '8.0.36', '+00:00']] as $ok) {
    try { BFixture::validateServer(...$ok); }
    catch (RuntimeException $e) { fwrite(STDERR, $e->getMessage()); exit(1); }
}
foreach ([['mysql', '5.6.51', '+00:00'], ['mysql', '5.5.51', '+00:00'],
          ['mysql', '10.2.44-MariaDB', '+00:00'], ['mysql', 'unknown', '+00:00'],
          ['sqlite', '8.0.36', '+00:00'], ['mysql', '5.7.44', 'SYSTEM']] as $bad) {
    try { BFixture::validateServer(...$bad); exit(1); }
    catch (RuntimeException $e) { if ($e->getMessage() !== 'FIXTURE_SERVER_REJECTED') exit(1); }
}
print("FIXTURE_ENGINE_FLOOR_VALID\n");
'''
        result = subprocess.run([self.php, "-n", "-r", code, str(BOOTSTRAP)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual((0, b"FIXTURE_ENGINE_FLOOR_VALID\n", b""),
                         (result.returncode, result.stdout, result.stderr))

