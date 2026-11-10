"""Robots must run under the interpreter that owns the shipped dependencies.

Regression cover for the v1.0.3 field failure: the panel spawned every robot
with a hardcoded `C:\\Program Files\\Python310\\python.exe`. On a machine where
that interpreter exists and has torch installed, the solver packages chose their
torch backend -- whose modules (`solver/predict.py`, `Hamta-solver/model.py`) are
deliberately NOT shipped in the release -- so every inquiry died with
`ModuleNotFoundError` before the browser opened.

Three independent contracts are pinned here:

1. The interpreter: robots run under the panel's own `sys.executable`.
2. The import path: `robot_launcher.py` works under an ISOLATED interpreter,
   which is what the embedded runtime in the bundle is (`-I` reproduces it: no
   script directory on `sys.path`, `PYTHONPATH` ignored).
3. Backend selection: a solver only takes its torch path when the torch-only
   modules are actually importable, so a stray torch install cannot break it.

Run through lab/scripts/run_isolated_tests.py using the bundled interpreter.
Launcher bootstrap checks execute trusted source copies in scratch with import
state restored; no repository probe or raw subprocess authority is required.
"""
import contextlib
import io
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
import unittest
from unittest.mock import Mock, patch

import panel

ROOT = os.path.dirname(os.path.abspath(panel.__file__))
PROBE_SOURCE = (
    "import system_debug\n"
    "import panel\n"
    "print('PROBE_OK', system_debug.__name__, panel.PORT)\n"
)


class RobotInterpreterTests(unittest.TestCase):
    """The panel must never hand a robot to a foreign interpreter."""

    def test_default_robot_interpreter_is_the_panel_interpreter(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IRICA_ROBOT_PYTHON", None)
            self.assertEqual(panel._resolve_robot_python(), sys.executable)

    def test_override_is_honoured_only_when_it_points_at_a_real_file(self):
        with patch.dict(os.environ, {"IRICA_ROBOT_PYTHON": sys.executable}):
            self.assertEqual(panel._resolve_robot_python(), sys.executable)
        with patch.dict(os.environ, {"IRICA_ROBOT_PYTHON": os.path.join(ROOT, "no-such-python.exe")}):
            self.assertEqual(panel._resolve_robot_python(), sys.executable)
        with patch.dict(os.environ, {"IRICA_ROBOT_PYTHON": "   "}):
            self.assertEqual(panel._resolve_robot_python(), sys.executable)

    def test_module_level_interpreter_is_resolved_not_hardcoded(self):
        self.assertTrue(os.path.isfile(panel.ROBOT_PYTHON), panel.ROBOT_PYTHON)
        source = open(panel.__file__, encoding="utf-8").read()
        self.assertNotIn(r"ROBOT_PYTHON = r\"C:\Program Files", source)

    def test_every_spawn_helper_launches_the_resolved_interpreter(self):
        helpers = (
            ("start_profile_process", ("p1",)),
            ("start_profile2_process", ("p1",)),
            ("start_site2_manual_process", ()),
            ("start_exitban_process", ("p1",)),
            ("start_estelam_process", (None, "hamta")),
        )
        for name, args in helpers:
            with self.subTest(helper=name):
                with patch.object(panel.subprocess, "Popen") as popen:
                    popen.return_value = Mock(pid=1234)
                    getattr(panel, name)(*args)
                    command = popen.call_args.args[0]
                self.assertEqual(command[0], panel.ROBOT_PYTHON)
                self.assertEqual(command[0], sys.executable)
                self.assertIn("robot_launcher.py", command[1])

    def test_no_spawn_helper_reads_sys_executable_directly(self):
        """`sys.executable` belongs in _resolve_robot_python() and nowhere else.

        A helper reading it directly would drift out of sync with the override.
        """
        for name in ("start_profile_process", "start_profile2_process",
                     "start_site2_manual_process", "start_exitban_process",
                     "start_estelam_process", "start_profile_device_inquiry"):
            with self.subTest(helper=name):
                self.assertNotIn("executable", getattr(panel, name).__code__.co_names)


class LauncherBootstrapTests(unittest.TestCase):
    """Exercise real copied sources with clean isolated/nonisolated import paths."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="launcher-source-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Copy trusted source only. No installed binaries, configs or live data.
        for name in ("robot_launcher.py", "system_debug.py", "panel.py", "panel_auth.py",
                     "maintenance_gate.py", "maintenance_ui.py", "exitban.py"):
            shutil.copyfile(Path(ROOT) / name, self.root / name)
        self.probe = self.root / "launcher_probe.py"
        self.probe.write_text(PROBE_SOURCE, encoding="utf-8")

    @contextlib.contextmanager
    def _fresh_project_imports(self, *, isolated):
        saved_path = list(sys.path)
        saved_modules = dict(sys.modules)
        saved_argv = list(sys.argv)
        original_cwd = os.getcwd()
        roots = (Path(ROOT).resolve(), self.root.resolve())

        def project_path(value):
            if not value:
                return False
            path = Path(value).resolve()
            return any(path == root or root in path.parents for root in roots)

        # app_paths must retain the runner's disabled bootstrap. All modules
        # whose lookup is under test are removed so cached imports cannot pass.
        keep = {"app_paths", "__main__", "run_isolated_tests", __name__}
        for name, module in list(sys.modules.items()):
            if name not in keep and project_path(getattr(module, "__file__", None)):
                sys.modules.pop(name, None)
        sys.path[:] = [value for value in saved_path if value and not project_path(value)]
        if not isolated:
            sys.path.insert(0, str(self.root))
        os.chdir(self.root)
        try:
            self.assertNotIn("panel", sys.modules)
            self.assertNotIn("system_debug", sys.modules)
            self.assertNotIn("exitban", sys.modules)
            yield
        finally:
            os.chdir(original_cwd)
            sys.path[:] = saved_path
            sys.argv[:] = saved_argv
            # Restore project imports only: unloading freshly imported native
            # extension modules can corrupt their next import in this process.
            for name, module in list(sys.modules.items()):
                if name not in keep and project_path(getattr(module, "__file__", None)):
                    sys.modules.pop(name, None)
            for name, module in saved_modules.items():
                if project_path(getattr(module, "__file__", None)):
                    sys.modules[name] = module

    def _execute(self, script, *, args=(), isolated=True):
        output = io.StringIO()
        with self._fresh_project_imports(isolated=isolated), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            sys.argv[:] = [str(script), *map(str, args)]
            try:
                runpy.run_path(str(script), run_name="__main__")
            except SystemExit as error:
                code = 0 if error.code is None else error.code
            except Exception:
                traceback.print_exc()
                code = 1
            else:
                code = 0
        return subprocess.CompletedProcess([str(script), *map(str, args)], code, output.getvalue())

    def test_launcher_imports_project_modules_under_isolated_interpreter(self):
        result = self._execute(self.root / "robot_launcher.py", args=(self.probe,))
        self.assertEqual(result.returncode, 0, result.stdout[-1200:])
        self.assertIn("PROBE_OK", result.stdout)

    def test_launcher_still_works_on_a_normal_interpreter(self):
        result = self._execute(self.root / "robot_launcher.py", args=(self.probe,), isolated=False)
        self.assertEqual(result.returncode, 0, result.stdout[-1200:])
        self.assertIn("PROBE_OK", result.stdout)

    def test_launcher_bootstraps_before_importing_project_code(self):
        source = (self.root / "robot_launcher.py").read_text(encoding="utf-8")
        self.assertLess(source.index("sys.path"), source.index("import system_debug"),
                        "sys.path must be repaired before the first project import")
        # A real import without the launcher's bootstrap must fail even though
        # the outer test process imported panel before entering this fixture.
        control = self.root / "control_system_debug.py"
        control.write_text("import system_debug\nprint('CONTROL_OK')\n", encoding="utf-8")
        result = self._execute(control)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ModuleNotFoundError", result.stdout)
        self.assertNotIn("CONTROL_OK", result.stdout)

    def test_panel_lazy_imports_survive_an_isolated_interpreter(self):
        control = self.root / "control_exitban.py"
        control.write_text("import exitban\nprint('CONTROL_OK')\n", encoding="utf-8")
        blank = self._execute(control)
        self.assertNotEqual(blank.returncode, 0,
                            "isolated import path must not find project modules")
        self.assertIn("ModuleNotFoundError", blank.stdout)
        self.assertNotIn("CONTROL_OK", blank.stdout)
        probe = self.root / "panel_lazy_probe.py"
        probe.write_text(
            "import runpy\n"
            f"runpy.run_path({str(self.root / 'panel.py')!r}, run_name='panel_probe')\n"
            "import exitban\n"
            "print('LAZY_OK', bool(exitban.missing_identity_fields({})))\n",
            encoding="utf-8")
        result = self._execute(probe)
        self.assertEqual(result.returncode, 0, result.stdout[-1200:])
        self.assertIn("LAZY_OK True", result.stdout)


class ChildSpawnTests(unittest.TestCase):
    """Robots that spawn robots must go through the launcher too."""

    def test_estelam_all_runs_children_through_the_launcher(self):
        import estelam_all
        with patch.object(estelam_all.subprocess, "call", return_value=0) as call:
            estelam_all._run("estelam_gomrok.py", ["--device-id", "d1"])
            command = call.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertIn("robot_launcher.py", command[1])
        self.assertIn("estelam_gomrok.py", command[2])
        self.assertEqual(command[-2:], ["--device-id", "d1"])

    def test_profile_inquiry_runs_children_through_the_launcher(self):
        import profile_inquiry
        with patch.object(profile_inquiry.subprocess, "call", return_value=0) as call:
            profile_inquiry._run("estelam_hamta.py", ["--from-store"], "store.json")
            command = call.call_args.args[0]
            env = call.call_args.kwargs["env"]
        self.assertEqual(command[0], sys.executable)
        self.assertIn("robot_launcher.py", command[1])
        self.assertIn("estelam_hamta.py", command[2])
        self.assertEqual(command[-1], "--from-store")
        self.assertEqual(env["IRICA_ESTELAM_STORE"], "store.json")


class SolverBackendSelectionTests(unittest.TestCase):
    """A solver may only take its torch path when that path can actually load."""

    @contextlib.contextmanager
    def _live(self, package, *, torch_available=True, backend_modules=True):
        """Run real backend detection over isolated source/dependency fixtures.

        Training config creates checkpoints at import, so trusted .py sources
        are copied to scratch rather than permitting writes into the checkout.
        Torch presence is synthetic; selection functions are never mocked.
        """
        import importlib

        names = ("live_solve", "config", "vocab", "preprocess", "utils",
                 "onnx_solve", "number_decode", "model", "predict")
        absent = "model" if package == "Hamta-solver" else "predict"
        saved_path = list(sys.path)
        saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
        torch_missing = object()
        saved_torch = sys.modules.get("torch", torch_missing)
        torch_fixture = types.ModuleType("torch") if torch_available else None
        if torch_fixture is not None:
            torch_fixture.no_grad = lambda: (lambda function: function)
        with tempfile.TemporaryDirectory(prefix="solver-source-fixture-") as directory:
            try:
                for name in names:
                    source = Path(ROOT) / package / (name + ".py")
                    if source.is_file() and (backend_modules or name != absent):
                        shutil.copyfile(source, Path(directory) / source.name)
                sys.path.insert(0, directory)
                sys.modules["torch"] = torch_fixture
                yield importlib.import_module("live_solve")
            finally:
                if saved_torch is torch_missing:
                    sys.modules.pop("torch", None)
                else:
                    sys.modules["torch"] = saved_torch
                for name in names:
                    sys.modules.pop(name, None)
                sys.modules.update(saved)
                sys.path[:] = saved_path

    def test_hamta_solver_reports_its_torch_backend_availability(self):
        with self._live("Hamta-solver") as live:
            self.assertTrue(live._torch_backend_available())
            self.assertFalse(live._module_importable("no_such_backend_module_xyz"))

    def test_gomrok_solver_reports_its_torch_backend_availability(self):
        with self._live("solver") as live:
            self.assertTrue(live._torch_backend_available())
            self.assertFalse(live._module_importable("no_such_backend_module_xyz"))

    def test_backend_falls_back_to_onnx_when_torch_modules_are_absent(self):
        """The release ships no predict.py/model.py; that must select ONNX."""
        for package, marker in (("Hamta-solver", "OnnxHamtaSolver"), ("solver", "OnnxSolver")):
            with self.subTest(package=package), self._live(package, backend_modules=False) as live:
                # This test is about backend selection, not loading external
                # model artifacts. The packaged release verifies the real ONNX
                # weights separately, while clean source checkouts intentionally
                # contain neither .pt nor .onnx files.
                fake_module = type(sys)("onnx_solve")
                fake_solver = type(marker, (), {
                    "__init__": lambda self, *args, **kwargs: None,
                })
                setattr(fake_module, marker, fake_solver)
                with patch.dict(sys.modules, {"onnx_solve": fake_module}):
                    self.assertFalse(live._torch_backend_available())
                    solver = live.load_solver(device="cpu")
                self.assertEqual(type(solver).__name__, marker)

    def test_torch_alone_does_not_select_the_torch_backend(self):
        """Exactly the v1.0.3 failure: torch present, backend modules absent."""
        for package, module in (("Hamta-solver", "model"), ("solver", "predict")):
            with self.subTest(package=package), self._live(package, backend_modules=False) as live:
                self.assertFalse(live._module_importable(module))
                self.assertFalse(live._torch_backend_available())

    def test_missing_torch_rejects_backend_even_when_backend_sources_exist(self):
        for package in ("Hamta-solver", "solver"):
            with self.subTest(package=package), self._live(package, torch_available=False) as live:
                self.assertFalse(live._torch_backend_available())


if __name__ == "__main__":
    unittest.main()
