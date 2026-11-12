import ast
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MaintenancePreviewTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "lab/scripts/run_maintenance_preview.py"
        self.assertTrue(path.is_file(), "The isolated maintenance preview is missing")
        spec = importlib.util.spec_from_file_location("maintenance_preview_fixture", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.fixture = self.module.PreviewMaintenance()

    def test_reads_and_checks_never_confirm_an_installation(self):
        self.assertFalse(self.fixture.status()["app"]["checked"])
        result = self.fixture.check(force=False)
        self.assertEqual(result["app"]["state"], "available")
        self.assertEqual(result["driver"]["state"], "available")
        self.assertEqual(self.fixture.audit()["checks"], 1)
        self.assertEqual(self.fixture.audit()["app_confirmations"], 0)
        self.assertEqual(self.fixture.audit()["driver_confirmations"], 0)
        self.assertEqual(self.fixture.audit()["health_checks"], 0)

    def test_fixture_confirmation_uses_exact_offer_and_boolean_consent(self):
        snapshot = self.fixture.check()
        self.assertFalse(self.fixture.confirm_app("substituted-offer")["ok"])
        driver = snapshot["driver"]["offer"]["offer_id"]
        for consent in (False, 1, "true"):
            self.assertFalse(self.fixture.confirm_driver(driver, consent=consent)["ok"])
        self.assertEqual(self.fixture.audit()["driver_confirmations"], 0)
        self.assertTrue(self.fixture.confirm_driver(driver, consent=True)["ok"])
        self.assertEqual(self.fixture.audit()["driver_confirmations"], 1)

    def test_busy_confirmation_has_a_visible_waiting_state(self):
        self.fixture.set_scenario("busy")
        snapshot = self.fixture.check()
        offer = snapshot["app"]["offer"]["offer_id"]
        result = self.fixture.confirm_app(offer)
        self.assertEqual(result["app"]["state"], "waiting_for_idle")
        self.assertEqual(self.fixture.audit()["app_confirmations"], 1)

    def release_fixture(self):
        self.assertTrue(hasattr(self.module, "PreviewReleaseNotes"), "Release-summary preview scenarios are missing")
        temporary = tempfile.TemporaryDirectory(prefix="irica-release-preview-")
        self.addCleanup(temporary.cleanup)
        return self.module.PreviewReleaseNotes(Path(temporary.name))

    def test_release_preview_uses_real_durable_markers_without_maintenance_side_effects(self):
        notes = self.release_fixture()
        before = self.fixture.audit()
        initial = notes.snapshot()
        self.assertFalse(initial["seen"])
        notes.acknowledge({"version": "1.21.0", "notice_id": initial["notice_id"]})
        self.assertTrue(notes.snapshot()["seen"])
        self.assertEqual(self.fixture.audit(), before)

    def test_release_preview_can_distinguish_seen_upgrade_and_another_account(self):
        notes = self.release_fixture()
        for scenario, seen in (("seen", True), ("unseen", False), ("new_version", False), ("account_b", False), ("blocked", False), ("editing", False)):
            notes.set_scenario(scenario)
            self.assertIs(notes.snapshot()["seen"], seen)
        initial = notes.snapshot()
        notes.set_scenario("unseen")
        with self.assertRaises(ValueError):
            notes.acknowledge({"version": "1.21.0", "notice_id": initial["notice_id"]})

    def test_release_preview_storage_failure_never_marks_seen(self):
        import release_notes
        notes = self.release_fixture()
        notes.set_scenario("storage_error")
        snapshot = notes.snapshot()
        with self.assertRaises(release_notes.StorageError):
            notes.acknowledge({"version": "1.21.0", "notice_id": snapshot["notice_id"]})
        self.assertFalse(notes.snapshot()["seen"])

    def test_preview_policy_keeps_the_read_only_callback_default(self):
        source = (ROOT / "lab/scripts/run_maintenance_preview.py").read_text(encoding="utf-8")
        policy = next(node for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.ClassDef) and node.name == "PreviewPolicy")
        method = next(node for node in policy.body if isinstance(node, ast.FunctionDef) and node.name == "path")
        self.assertTrue(method.args.defaults, "Read-only audit callbacks omit the optional write flag")
        self.assertIs(method.args.defaults[-1].value, False)

    def test_preview_isolation_precedes_all_application_imports(self):
        source = (ROOT / "lab/scripts/run_maintenance_preview.py").read_text(encoding="utf-8")
        self.assertLess(source.index("_environment(scratch)"), source.index("import panel"))
        self.assertLess(source.index("app_paths.bootstrap ="), source.index("import panel"))
        self.assertIn("IsolationPolicy", source)
        self.assertIn("/api/maintenance/check", source)
        self.assertNotIn("seed_default_users(", source)
        self.assertNotIn("panel.main(", source)


if __name__ == "__main__":
    unittest.main()
