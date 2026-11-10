"""Release-probe contracts using synthetic paths and registered loopback only."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "lab" / "scripts" / "release_probe.py"


def probe_module(test):
    if not SOURCE.is_file():
        test.fail("The isolated bundled-release probe is not implemented")
    name = "_release_probe_test_module"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SOURCE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class ReleaseProbePathsTests(unittest.TestCase):
    def setUp(self):
        self.module = probe_module(self)
        self.temp = tempfile.TemporaryDirectory(prefix="release-probe-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.stage = self.base / "fresh-stage"
        (self.stage / "python").mkdir(parents=True)
        (self.stage / "python" / "python.exe").write_bytes(b"MZ-fixture-not-executable")
        self.scratch = self.base / "new-workspace"
        self.probe = self.stage / "_irica_release_probe_fixture.py"

    def test_paths_must_be_absolute_disjoint_and_scratch_new(self):
        stage, scratch = self.module.validate_paths(self.stage, self.scratch)
        self.assertEqual(stage, self.stage)
        self.assertEqual(scratch, self.scratch)
        self.assertFalse(scratch.exists())
        invalid = ((Path("relative-stage"), self.scratch),
                   (self.stage, Path("relative-scratch")),
                   (self.stage, self.stage / "workspace"),
                   (self.stage, self.base), (ROOT, self.scratch))
        for stage, scratch in invalid:
            with self.subTest(stage=str(stage), scratch=str(scratch)):
                with self.assertRaises(self.module.ProbeFailure):
                    self.module.validate_paths(stage, scratch)
        self.scratch.mkdir()
        with self.assertRaises(self.module.ProbeFailure):
            self.module.validate_paths(self.stage, self.scratch)

    def test_installed_or_state_carrying_stage_is_refused_without_reading_store(self):
        for name in ("profiles.json", "telegram_config.json", "unins000.exe", ".git"):
            with self.subTest(name=name):
                path = self.stage / name
                path.write_bytes(b"PRIVATE-CANARY-DO-NOT-READ")
                try:
                    with self.assertRaises(self.module.ProbeFailure):
                        self.module.validate_paths(self.stage, self.scratch)
                finally:
                    path.unlink()

    def test_network_stage_is_refused_before_any_filesystem_probe(self):
        with patch.object(Path, "is_dir", side_effect=AssertionError("No network filesystem probe")):
            with self.assertRaises(self.module.ProbeFailure):
                self.module.validate_paths(Path(r"\\unauthorized.invalid\share\stage"), self.scratch)

    def test_reparse_ancestors_are_refused_before_following_or_resolving_them(self):
        from types import SimpleNamespace
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0x400)), \
                patch.object(Path, "is_dir", side_effect=AssertionError("No following reparse paths")), \
                patch.object(Path, "resolve", side_effect=AssertionError("No resolving reparse paths")):
            with self.assertRaises(self.module.ProbeFailure):
                self.module.validate_paths(self.stage, self.scratch)

    def test_policy_rejects_unc_before_path_resolution_can_touch_network(self):
        self.scratch.mkdir()
        policy = self.module.make_policy(self.stage, self.scratch, self.probe)
        with patch.object(Path, "resolve", side_effect=AssertionError("No network path resolution")):
            with self.assertRaises(PermissionError):
                policy.path(r"\\unauthorized.invalid\share\secret.json")

    def test_cli_exits_its_own_probe_when_nonjoined_threads_would_hold_python_open(self):
        import io
        with patch.object(self.module, "run_probe", return_value={"ok": False, "unclosed_threads": 1}), \
                patch.object(self.module.os, "_exit", side_effect=SystemExit(2)), \
                patch.object(sys, "stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                self.module.main(["--stage", str(self.stage), "--scratch", str(self.scratch)])
            self.assertEqual(stopped.exception.code, 2)

    def test_cleanup_never_removes_a_probe_it_did_not_create(self):
        self.probe.write_bytes(b"existing coordinator file")
        self.module.cleanup_probe(self.probe, owned=False)
        self.assertEqual(self.probe.read_bytes(), b"existing coordinator file")
        self.module.cleanup_probe(self.probe, owned=True)
        self.assertFalse(self.probe.exists())

    def test_stage_is_read_only_except_the_single_owned_probe(self):
        self.scratch.mkdir()
        policy = self.module.make_policy(self.stage, self.scratch, self.probe)
        model = self.stage / "solver" / "fixture.onnx"
        policy.path(model)
        policy.path(self.probe, True)
        policy.path(self.scratch / "data" / "fixture.json", True)
        for path in (self.stage / "panel.py", model, self.stage / "another-probe.py"):
            with self.subTest(path=str(path)), self.assertRaises(PermissionError):
                policy.path(path, True)
        with self.assertRaises(PermissionError):
            policy.path(self.base / "private-settings.json")

    def test_probe_allowance_cannot_escape_stage_or_cover_another_script(self):
        self.scratch.mkdir()
        for probe in (self.base / "outside.py", self.stage / "panel.py",
                      self.stage / "nested" / "_irica_release_probe_fixture.py"):
            with self.subTest(probe=str(probe)), self.assertRaises(self.module.ProbeFailure):
                self.module.make_policy(self.stage, self.scratch, probe)

    def test_all_children_are_denied_even_when_base_worker_or_node_would_be_allowed(self):
        self.scratch.mkdir()
        policy = self.module.make_policy(self.stage, self.scratch, self.probe)
        policy.local.process_allowed = True
        for event in ("subprocess.Popen", "os.system", "os.spawn", "os.exec", "os.startfile",
                      "os.posix_spawn"):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                policy.audit(event, (sys.executable, []))
        self.assertIsNone(policy.trusted_worker([
            sys.executable, "-I", "-B", str(ROOT / "lab/tests/test_maintenance_gate.py"),
            "--gate-worker", "wait", str(self.scratch), str(self.scratch / "jobs.json")]))

    def test_external_and_unregistered_loopback_endpoints_are_denied(self):
        self.scratch.mkdir()
        policy = self.module.make_policy(self.stage, self.scratch, self.probe)
        for address in (("192.0.2.1", 443), ("127.0.0.1", 34567), ("::1", 34567)):
            with self.subTest(address=address), self.assertRaises(PermissionError):
                policy.endpoint(address)

    def test_environment_is_replaced_before_product_imports(self):
        self.scratch.mkdir()
        previous_cwd, previous_temp = os.getcwd(), tempfile.tempdir
        values = dict(os.environ)
        try:
            os.environ.update({"IRICA_IMPORT_FROM": "PRIVATE", "IRICA_CRM_STORE": "PRIVATE",
                               "IRICA_DATA_DIR": "PRIVATE", "HTTP_PROXY": "PRIVATE",
                               "HTTPS_PROXY": "PRIVATE", "ALL_PROXY": "PRIVATE",
                               "IRICA_VERIFY_USER": "PRIVATE", "IRICA_VERIFY_PASS": "PRIVATE",
                               "CUSTOM_API_SECRET": "PRIVATE", "PYTHONPATH": "PRIVATE"})
            self.module.configure_environment(self.scratch)
            self.assertNotIn("PRIVATE", os.environ.values())
            for key in ("IRICA_DATA_DIR", "IRICA_CRM_STORE", "IRICA_ESTELAM_STORE",
                        "IRICA_IMPORT_FROM", "IRICA_SYSTEM_LOG_DIR", "HOME", "LOCALAPPDATA",
                        "APPDATA", "USERPROFILE", "TEMP", "TMP"):
                self.assertTrue(Path(os.environ[key]).is_relative_to(self.scratch), key)
            self.assertEqual(os.environ["IRICA_CLOUD_WORKER_OFF"], "1")
            self.assertEqual(os.environ["IRICA_CRM_SCHEDULER_OFF"], "1")
            self.assertEqual(os.environ["IRICA_SYSTEM_DEBUG"], "0")
            self.assertEqual(Path.cwd(), self.scratch)
        finally:
            os.environ.clear()
            os.environ.update(values)
            os.chdir(previous_cwd)
            tempfile.tempdir = previous_temp

    def test_preloading_only_app_paths_disables_migration_and_restores_previous_binding(self):
        module_source = self.stage / "app_paths.py"
        module_source.write_text("called = False\ndef bootstrap():\n    global called\n    called = True\n    raise RuntimeError('Migration must never run')\n", encoding="ascii")
        previous = sys.modules.get("app_paths")
        before = list(sys.path)
        with self.module.staged_app_paths(self.stage) as app_paths:
            self.assertEqual(app_paths.bootstrap(), [])
            self.assertFalse(app_paths.called)
            self.assertEqual(Path(app_paths.__file__), module_source)
            self.assertIs(sys.modules["app_paths"], app_paths)
            self.assertEqual(sys.path, before)
        self.assertIs(sys.modules.get("app_paths"), previous)

    def test_stage_import_path_help_is_refused_before_real_launcher(self):
        with patch.object(sys, "path", [str(self.stage)]):
            with self.assertRaises(self.module.ProbeFailure):
                self.module.assert_launcher_isolation(self.stage)
        with patch.object(sys, "path", [str(self.stage / "python")]):
            self.module.assert_launcher_isolation(self.stage)

    def test_summary_reports_only_sanitized_counts_and_any_denial_fails(self):
        self.scratch.mkdir()
        policy = self.module.make_policy(self.stage, self.scratch, self.probe)
        policy.current_test = "PRIVATE session-cookie fixture"
        with self.assertRaises(PermissionError):
            policy.endpoint(("192.0.2.1", 443))
        summary = self.module.summarize(["imports", "http"], policy,
                                        thread_errors=["PRIVATE token"], live_threads=["PRIVATE worker"])
        import json
        rendered = json.dumps(summary)
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["checks"], 2)
        self.assertEqual(summary["denials"], 1)
        self.assertEqual(summary["thread_errors"], 1)
        self.assertEqual(summary["unclosed_threads"], 1)
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn(str(self.base), rendered)


class SolverConfigImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="solver-import-safety-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def load_actual_config(self, package, fixture):
        source = (ROOT / package / "config.py").read_text(encoding="utf-8")
        fixture.mkdir(parents=True, exist_ok=True)
        target = fixture / "config.py"
        target.write_text(source, encoding="utf-8")
        name = "_release_config_" + package.replace("-", "_") + "_" + fixture.name
        spec = importlib.util.spec_from_file_location(name, target)
        module = importlib.util.module_from_spec(spec)
        previous = sys.modules.get(name)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            return module
        finally:
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def test_existing_checkpoint_directory_is_read_only_at_config_import(self):
        for package in ("solver", "Hamta-solver"):
            with self.subTest(package=package):
                fixture = self.base / package
                checkpoint = fixture / "checkpoints"
                checkpoint.mkdir(parents=True)
                sentinel = checkpoint / "fixture.model"
                sentinel.write_bytes(b"synthetic-model-not-executed")
                # Copy before forbidding mkdir so only the actual import is tested.
                source = (ROOT / package / "config.py").read_text(encoding="utf-8")
                target = fixture / "config.py"
                target.write_text(source, encoding="utf-8")
                name = "_release_readonly_config_" + package.replace("-", "_")
                spec = importlib.util.spec_from_file_location(name, target)
                module = importlib.util.module_from_spec(spec)
                with patch.dict(sys.modules, {name: module}), \
                        patch.object(Path, "mkdir", side_effect=AssertionError("Existing checkpoint import attempted a write")):
                    spec.loader.exec_module(module)
                self.assertEqual(module.CKPT_DIR, checkpoint)
                self.assertEqual(sentinel.read_bytes(), b"synthetic-model-not-executed")

    def test_missing_checkpoint_directory_is_still_created_for_training(self):
        for package in ("solver", "Hamta-solver"):
            with self.subTest(package=package):
                fixture = self.base / (package + "_missing")
                module = self.load_actual_config(package, fixture)
                self.assertEqual(module.CKPT_DIR, fixture / "checkpoints")
                self.assertTrue(module.CKPT_DIR.is_dir())


class ReleaseProbePlatformTests(unittest.TestCase):
    def setUp(self):
        self.module = probe_module(self)

    @staticmethod
    def native_version(build=26100, *, major=10, minor=0, product_type=1):
        from types import SimpleNamespace
        return SimpleNamespace(major=major, minor=minor, build=build, platform=2,
                               service_pack="", service_pack_major=0, service_pack_minor=0,
                               suite_mask=0, product_type=product_type,
                               platform_version=(major, minor, build))

    @unittest.skipUnless(sys.platform == "win32" and os.name == "nt", "Windows runtime metadata")
    def test_truthful_metadata_context_never_calls_shell_and_restores_exact_functions(self):
        import platform
        import subprocess
        original = (platform.system, platform.release)
        unchanged = (platform.machine, platform.version)
        with patch.object(subprocess, "Popen", side_effect=AssertionError("No executable may run")), \
                patch.object(sys, "getwindowsversion", return_value=self.native_version()), \
                patch.object(platform, "system", side_effect=AssertionError("No stdlib shell-version fallback")) as previous_system, \
                patch.object(platform, "release", side_effect=AssertionError("No stdlib shell-version fallback")) as previous_release:
            with self.module.onnx_import_system():
                self.assertEqual(platform.system(), "Windows")
                self.assertEqual(platform.release(), "11")
                self.assertEqual((platform.machine, platform.version), unchanged)
            self.assertIs(platform.system, previous_system)
            self.assertIs(platform.release, previous_release)
        self.assertEqual((platform.system, platform.release), original)

    def test_native_client_build_determines_release_without_manifest_or_shell_fallback(self):
        import platform
        cases = ((self.native_version(19045), "10"),
                 (self.native_version(22000), "11"),
                 (self.native_version(26100), "11"))
        # Native platform_version, not manifest-affected compatibility fields, is authoritative.
        compatibility = self.native_version(26100)
        compatibility.major, compatibility.minor, compatibility.build = 6, 2, 9200
        for native, expected in (*cases, (compatibility, "11")):
            with self.subTest(build=native.platform_version), \
                    patch.object(sys, "getwindowsversion", return_value=native), \
                    patch.object(platform, "release", side_effect=AssertionError("No shell fallback")):
                with self.module.onnx_import_system():
                    self.assertEqual(platform.release(), expected)

    def test_unknown_native_release_is_refused_without_guessing_or_mutating_functions(self):
        import platform
        before = (platform.system, platform.release)
        versions = (self.native_version(product_type=3), self.native_version(major=6, minor=3),
                    self.native_version(10239))
        for native in versions:
            with self.subTest(version=native.platform_version, product_type=native.product_type), \
                    patch.object(sys, "getwindowsversion", return_value=native):
                with self.assertRaises(self.module.ProbeFailure):
                    with self.module.onnx_import_system():
                        self.fail("Unsupported native metadata must not reach dependency import")
            self.assertEqual((platform.system, platform.release), before)

    def test_native_metadata_failure_does_not_leave_partial_overrides(self):
        import platform
        before = (platform.system, platform.release)
        with patch.object(sys, "getwindowsversion", side_effect=OSError("synthetic native metadata failure")):
            with self.assertRaises(OSError):
                with self.module.onnx_import_system():
                    self.fail("Unavailable native metadata must not be guessed")
        self.assertEqual((platform.system, platform.release), before)

    def test_nonwindows_context_leaves_metadata_and_native_detection_untouched(self):
        import platform
        before = (platform.system, platform.release)
        with patch.object(sys, "platform", "linux"), patch.object(os, "name", "posix"), \
                patch.object(sys, "getwindowsversion", side_effect=AssertionError("No Windows metadata lookup")):
            with self.module.onnx_import_system():
                self.assertEqual((platform.system, platform.release), before)
        self.assertEqual((platform.system, platform.release), before)

    def test_dependency_import_restores_both_metadata_functions_on_failure(self):
        import platform
        before = (platform.system, platform.release)
        def rejected(name):
            self.assertEqual(name, "onnxruntime")
            self.assertEqual(platform.system(), "Windows")
            self.assertEqual(platform.release(), "11")
            raise ImportError("synthetic dependency error")
        with patch.object(sys, "getwindowsversion", return_value=self.native_version()), \
                patch.object(platform, "release", side_effect=AssertionError("No shell fallback")) as previous_release, \
                patch.object(self.module.importlib, "import_module", side_effect=rejected):
            with self.assertRaises(ImportError):
                self.module.import_dependency("onnxruntime")
            self.assertIs(platform.release, previous_release)
        self.assertEqual((platform.system, platform.release), before)

    def test_onnx_import_disables_telemetry_before_returning_dependency(self):
        events = []
        class Dependency:
            def disable_telemetry_events(self):
                events.append("disable_telemetry")
        dependency = Dependency()
        def imported(name):
            self.assertEqual(name, "onnxruntime")
            events.append("import")
            return dependency
        with patch.object(sys, "getwindowsversion", return_value=self.native_version()), \
                patch.object(self.module.importlib, "import_module", side_effect=imported):
            returned = self.module.import_dependency("onnxruntime")
            events.append("returned")
        self.assertIs(returned, dependency)
        self.assertEqual(events, ["import", "disable_telemetry", "returned"])

    def test_telemetry_disable_failure_is_not_silently_accepted(self):
        import platform
        before = (platform.system, platform.release)
        class Dependency:
            def disable_telemetry_events(self):
                raise RuntimeError("synthetic telemetry control failure")
        with patch.object(sys, "getwindowsversion", return_value=self.native_version()), \
                patch.object(self.module.importlib, "import_module", return_value=Dependency()):
            with self.assertRaises(RuntimeError):
                self.module.import_dependency("onnxruntime")
        self.assertEqual((platform.system, platform.release), before)

    def test_other_dependencies_keep_original_platform_functions(self):
        import platform
        before = (platform.system, platform.release)
        sentinel = object()
        def inspected(name):
            self.assertEqual(name, "numpy")
            self.assertEqual((platform.system, platform.release), before)
            return sentinel
        with patch.object(self.module.importlib, "import_module", side_effect=inspected):
            self.assertIs(self.module.import_dependency("numpy"), sentinel)
        self.assertEqual((platform.system, platform.release), before)


class ReleaseProbeHttpTests(unittest.TestCase):
    def setUp(self):
        self.module = probe_module(self)

    @staticmethod
    def markup():
        update = "".join(map(chr, (0x0628, 0x0647, 0x200C, 0x0631, 0x0648, 0x0632, 0x0631,
                                  0x0633, 0x0627, 0x0646, 0x06CC, 0x200C, 0x0647, 0x0627)))
        health = "".join(map(chr, (0x0633, 0x0644, 0x0627, 0x0645, 0x062A, 0x0020, 0x0633,
                                  0x0631, 0x0648, 0x06CC, 0x0633, 0x200C, 0x0647, 0x0627)))
        return ('<html lang="fa" dir="rtl"><head></head><body>'
                '<header><div class="irica-maint-actions" lang="fa" dir="rtl">'
                '<button id="irica-maint-open-updates"><span>' + update +
                '</span></button><button id="irica-maint-open-health"><span>' + health +
                '</span></button></div></header></body></html>')

    def test_shared_controls_need_unique_ids_persian_labels_and_rtl_container(self):
        markup = self.markup()
        self.module.verify_markup(markup)
        invalid = (markup.replace('id="irica-maint-open-health"', 'id="irica-maint-open-updates"'),
                   markup.replace('dir="rtl"', 'dir="ltr"'),
                   markup.replace(chr(0x06CC), chr(0x064A)),
                   markup.replace('<header>', '<main>').replace('</header>', '</main>'))
        for page in invalid:
            with self.subTest(page=page), self.assertRaises(self.module.ProbeFailure):
                self.module.verify_markup(page)

    def test_http_uses_real_panel_session_csrf_and_three_pages_without_login_or_workers(self):
        import panel
        import panel_auth
        with ExitStack() as safety:
            for target, symbol in ((panel_auth, "seed_default_users"),
                                   (panel, "confirm_application_update"),
                                   (panel, "maintenance_check"), (panel, "cloud_worker_start"),
                                   (panel, "crm_scheduler_start")):
                safety.enter_context(patch.object(target, symbol,
                    side_effect=AssertionError("The release HTTP gate crossed a side-effect boundary")))
            result = self.module.verify_http(panel, panel_auth)
        self.assertEqual(result, {"pages": 3, "unauthenticated": 3, "csrf": 2,
                                  "requests": 8, "server_closed": True})


if __name__ == "__main__":
    unittest.main()
