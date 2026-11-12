import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_MODULES = ("app_updater.py", "maintenance_gate.py", "installer_handoff.py",
                   "maintenance_service.py", "maintenance_ui.py", "release_notes.py")


class MaintenancePackagingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "maintenance_release_builder", ROOT / "lab/scripts/build_windows_release.py")
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.temp = tempfile.TemporaryDirectory(prefix="irica-packaging-test-")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.stage = Path(self.temp.name) / "stage"
        self.source.mkdir()
        self.stage.mkdir()
        for name in RUNTIME_MODULES:
            (self.source / name).write_text("VALUE = 'fixture'\n", encoding="utf-8")
        for name in ("solver/checkpoints/best_real_v2.onnx",
                     "Hamta-solver/checkpoints/best.onnx", ".tools/chromedriver.exe"):
            file = self.source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"synthetic fixture")

    def copy_app(self):
        with patch.object(self.builder, "ROOT", self.source):
            with contextlib.redirect_stdout(io.StringIO()):
                self.builder.copy_app(self.stage)

    def test_release_copies_maintenance_runtime_modules(self):
        self.copy_app()
        for name in RUNTIME_MODULES:
            with self.subTest(module=name):
                self.assertTrue((self.stage / name).is_file(),
                                "The release must ship " + name)
                self.assertEqual((self.stage / name).read_text(encoding="utf-8"),
                                 "VALUE = 'fixture'\n")

    def test_release_requires_curated_content_matching_the_packaged_version(self):
        with patch.object(self.builder, "ROOT", ROOT):
            self.builder.validate_release_notes("1.21.0")
            for version in ("1.20.0", "1.22.0"):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    self.builder.validate_release_notes(version)

    def test_first_run_requires_explicit_driver_confirmation(self):
        self.copy_app()
        text = (self.stage / "FIRST_RUN.txt").read_text(encoding="utf-8")
        instructions = text.split("2)", 1)[1].split("3)", 1)[0]
        self.assertIn("confirm", instructions.lower())
        self.assertNotIn("repairs itself", instructions.lower())
        self.assertTrue(instructions.isascii())

    def test_existing_installations_have_a_bootstrap_upgrade_path(self):
        self.copy_app()
        text = (self.stage / "FIRST_RUN.txt").read_text(encoding="utf-8")
        self.assertIn("one normal installer upgrade", text)
        self.assertIn("Portable copies require a manual upgrade", text)

    def test_existing_private_data_is_not_copied(self):
        for name in ("cloud_config.json", "crm_store.json", "profiles.json"):
            (self.source / name).write_text('{"fixture": true}', encoding="utf-8")
        self.copy_app()
        for name in ("cloud_config.json", "crm_store.json", "profiles.json"):
            with self.subTest(file=name):
                self.assertFalse((self.stage / name).exists())


if __name__ == "__main__":
    unittest.main()
