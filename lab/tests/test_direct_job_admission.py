"""Direct packaged entry boundaries use real isolated kernel admission leases.

Only the existing __main__ blocks are executed with safe no-work callbacks;
no browser, target endpoint, solver, authentication batch or subprocess runs.
"""
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ROOT_JOBS = (
    "gomrok.py", "gomrok2.py", "profile_inquiry.py", "exitban.py",
    "estelam_gomrok.py", "estelam_hamta.py", "estelam_all.py",
)


class DirectJobAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="direct-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        # The strict runner already sets safe global paths before imports.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import maintenance_gate
        self.module = maintenance_gate
        self.gate = maintenance_gate.MaintenanceGate(
            install_dir=str(self.scratch), registry_path=str(self.scratch / "jobs.json"))
        self.default = patch.object(maintenance_gate, "default_gate", return_value=self.gate)
        self.default.start()
        self.addCleanup(self.default.stop)

    def entry(self, name, callback):
        path = ROOT / name
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        entries = [node for node in tree.body if isinstance(node, ast.If)
                   and isinstance(node.test, ast.Compare)
                   and isinstance(node.test.left, ast.Name)
                   and node.test.left.id == "__name__"]
        self.assertEqual(len(entries), 1, "A packaged root needs one direct boundary")
        # Execute the actual source boundary while replacing only target work.
        code = compile(ast.fix_missing_locations(ast.Module(body=entries, type_ignores=[])),
                       str(path), "exec")
        namespace = {"__name__": "__main__", "__file__": str(path), "main": callback,
                     "sys": types.SimpleNamespace(exit=lambda code=0: code)}
        exec(code, namespace)

    def jobs(self):
        path = Path(self.gate.registry_path)
        return json.loads(path.read_text(encoding="utf-8"))["jobs"] if path.exists() else []

    def test_direct_roots_refuse_late_start_before_any_callback_work(self):
        for name in ROOT_JOBS:
            with self.subTest(root=name):
                called = []
                with self.gate.begin_install(exclude_pids={os.getpid()}):
                    with self.assertRaises(self.module.MaintenanceBlocked):
                        self.entry(name, lambda: called.append("target work"))
                self.assertEqual(called, [])
                self.assertEqual(self.jobs(), [])

    def test_every_direct_root_registers_before_callback_and_cleans_up(self):
        for name in ROOT_JOBS:
            with self.subTest(root=name):
                observed = []
                def no_work():
                    rows = self.jobs()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["pid"], os.getpid())
                    self.assertGreater(rows[0]["created"], 0)
                    observed.append(rows[0])
                    with self.assertRaisesRegex(self.module.MaintenanceBlocked, "active"):
                        self.gate.begin_install()
                self.entry(name, no_work)
                self.assertEqual(len(observed), 1)
                self.assertEqual(self.jobs(), [])

    def test_launcher_nested_direct_entry_cannot_unregister_outer_lifetime(self):
        import robot_launcher
        observed = []
        def run_path(script, **kwargs):
            observed.append(self.jobs())
            self.entry("gomrok2.py", lambda: observed.append(self.jobs()))
            observed.append(self.jobs())
        with patch.object(sys, "argv", ["robot_launcher.py", str(ROOT / "gomrok2.py")]), \
                patch.object(robot_launcher.system_debug, "configure"), \
                patch.object(robot_launcher.system_debug, "event"), \
                patch.object(robot_launcher.runpy, "run_path", side_effect=run_path):
            robot_launcher.main()
        self.assertEqual(len(observed), 3)
        self.assertTrue(all(len(rows) == 1 for rows in observed))
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[1], observed[2])
        self.assertEqual(self.jobs(), [])

    def test_no_environment_flag_bypasses_an_install_lease(self):
        with patch.dict(os.environ, {"IRICA_JOB_ADMITTED": "1", "IRICA_MAINTENANCE_REGISTERED": "1"}):
            with self.gate.begin_install(exclude_pids={os.getpid()}):
                with self.assertRaises(self.module.MaintenanceBlocked):
                    self.entry("gomrok2.py", lambda: self.fail("Environment bypass reached target work"))

    def test_unverifiable_current_pid_fails_closed_before_callback(self):
        run_job = getattr(self.module, "run_job", None)
        self.assertTrue(callable(run_job), "A common job boundary is required")
        with patch.object(self.gate._api, "process", return_value=None):
            with self.assertRaises(self.module.MaintenanceBlocked):
                run_job(lambda: self.fail("Unregistered process reached target work"))
        self.assertEqual(self.jobs(), [])

    def test_shared_context_preserves_callback_return_value(self):
        self.assertEqual(self.module.run_job(lambda: 37), 37)
        self.assertEqual(self.jobs(), [])

    def test_shared_context_cleans_up_exact_creation_after_baseexception(self):
        run_job = getattr(self.module, "run_job", None)
        self.assertTrue(callable(run_job), "A common job boundary is required")
        def stop():
            self.assertEqual(len(self.jobs()), 1)
            raise SystemExit("Fixture safety stop")
        with self.assertRaises(SystemExit):
            run_job(stop)
        self.assertEqual(self.jobs(), [])


class ManualSubmissionBoundaryTests(unittest.TestCase):
    def setUp(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import gomrok2
        import panel
        self.robot = gomrok2
        self.stop = panel.ProfileIdentityStopped

    def test_live_identity_check_immediately_precedes_one_user_submission(self):
        events = []
        class Driver:
            def execute_script(inner, script, *args):
                if script == self.robot._FIRE_JS:
                    events.append("external submission")
                    return True
                events.append("clear")
                return None
        def changed():
            events.append("identity check")
            raise self.stop("Fixture identity correction during manual wait")
        with patch.object(self.robot, "_assert_identity_current", side_effect=changed), \
                patch.object(self.robot.time, "sleep", side_effect=RuntimeError("Unexpected post-submission wait")):
            with self.assertRaises(self.stop):
                self.robot._submit_one_code(Driver(), "1234")
        self.assertNotIn("external submission", events)
        self.assertEqual(events[-1], "identity check")

    def test_manual_wait_loop_observes_identity_change_before_waiting_again(self):
        handoff = types.SimpleNamespace(set_phase=lambda *a: None, set_status=lambda *a, **k: None,
                                        _ensure=lambda: None)
        class Driver:
            def execute_script(inner, script, *args):
                self.fail("An invalidated manual wait must stop before another page operation")
        with patch.object(self.robot, "_code_page_ready", return_value=True), \
                patch.object(self.robot, "_assert_identity_current",
                             side_effect=self.stop("Fixture correction")), \
                patch.object(self.robot.time, "sleep", side_effect=RuntimeError("Unprotected manual wait")):
            with self.assertRaises(self.stop):
                self.robot.run_code_entry(Driver(), handoff)


class LinkedVerificationRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="linked-verification-policy-")
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        environment = patch.dict(os.environ, {
            "IRICA_DATA_DIR": str(self.scratch), "IRICA_CRM_STORE": str(self.scratch / "crm.json"),
            "IRICA_ESTELAM_STORE": str(self.scratch / "inquiry.json"),
            "IRICA_IMPORT_FROM": str(self.scratch / "absent-legacy"),
            "IRICA_CLOUD_WORKER_OFF": "1", "IRICA_CRM_SCHEDULER_OFF": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import panel
        import crm
        import gomrok
        import gomrok2
        self.panel, self.crm, self.robot, self.robot2 = panel, crm, gomrok, gomrok2
        for attribute, filename in (("PROFILES_PATH", "profiles.json"),
                                    ("_STORE_LOCK_PATH", "profiles.json.lock"),
                                    ("INPUTS_PATH", "inputs.json")):
            item = patch.object(panel, attribute, str(self.scratch / filename))
            item.start()
            self.addCleanup(item.stop)
        owner = {"id": "owner1", "national": "0012345679", "birth": "1375/04/29",
                 "mobile": "09121234567", "passport": "PP0000001"}
        device = {"id": "device1", "owner_id": "owner1", "profile_id": "profile1",
                  "stage": "payment", "serial1": "356938035640001", "serial2": ""}
        crm.store_write({"owners": [owner], "devices": [device], "settings": {}})
        panel.save_store({"active_id": "profile1", "profiles": [{
            **owner, "crm_device_id": "device1", "id": "profile1",
            "imeis": [device["serial1"]], "enabled": True,
            "attempt_history": [{"status": "superseded", "receipt_screenshot": "prior.png"}],
        }]})
        self.attempt = panel.claim_profile("profile1")
        self.assertIsNotNone(self.attempt)
        for module in (gomrok, gomrok2):
            for attribute, value in (("_CLAIMED_PROFILE_ID", "profile1"),
                                     ("_CLAIMED_RUNNER_ID", self.attempt["runner_id"]),
                                     ("_CLAIMED_FINGERPRINT", self.attempt["claimed_identity_fingerprint"]),
                                     ("_CLAIMED_CRM_DEVICE_ID", "device1")):
                item = patch.object(module, attribute, value, create=True)
                item.start()
                self.addCleanup(item.stop)
        self.addCleanup(crm.reset_cache)

    def assert_review_preserved(self):
        profile = self.panel.load_store()["profiles"][0]
        self.assertTrue(profile["identity_review_required"])
        self.assertIn("manual", profile["runtime_error"].lower())
        self.assertNotEqual(profile["registration_status"], "success")
        self.assertEqual(profile["run_status"], "busy")
        self.assertEqual(profile["runner_id"], self.attempt["runner_id"])
        self.assertEqual(profile["claimed_identity_fingerprint"], self.attempt["claimed_identity_fingerprint"])
        self.assertTrue(any(item.get("receipt_screenshot") == "prior.png"
                            for item in profile["attempt_history"]))
        self.assertEqual(self.crm.store_read()["devices"][0]["stage"], "payment")

    def test_site2_default_fast_and_diagnostic_stop_before_selection_or_submission(self):
        modes = ({}, {"code": "1234"}, {"diagnose": True, "code": "1234"},
                 {"codes": "1234,5678"})
        for override in modes:
            with self.subTest(mode=override):
                args = types.SimpleNamespace(manual=False, code=None, codes=None,
                                             diagnose=False, attempts=2, gap_ms=0)
                vars(args).update(override)
                with patch.object(self.robot2, "_choose_codeflow",
                                  side_effect=AssertionError("Unattended code generation reached")) as choose, \
                        patch.object(self.robot2, "run_code_fast") as fast, \
                        patch.object(self.robot2, "run_code_attempts") as diagnostic, \
                        patch.object(self.robot2, "run_code_entry") as manual:
                    with self.assertRaisesRegex(self.panel.ProfileIdentityStopped,
                                                "[Mm]anual verification.*review"):
                        self.robot2._run_codeflow(object(), object(), args)
                    choose.assert_not_called()
                    fast.assert_not_called()
                    diagnostic.assert_not_called()
                    manual.assert_not_called()
                self.assert_review_preserved()

    def test_explicit_site2_manual_mode_remains_the_only_linked_dispatch(self):
        args = types.SimpleNamespace(manual=True, diagnose=False, code=None, codes=None,
                                     attempts=1, gap_ms=0)
        with patch.object(self.robot2, "run_code_entry", return_value=True) as manual, \
                patch.object(self.robot2, "run_code_fast") as fast, \
                patch.object(self.robot2, "run_code_attempts") as diagnostic:
            self.assertIsNone(self.robot2._run_codeflow(object(), object(), args))
            manual.assert_called_once()
            fast.assert_not_called()
            diagnostic.assert_not_called()
        self.assertFalse(self.panel.load_store()["profiles"][0]["identity_review_required"])

    def test_site1_batch_boundary_stops_without_executing_legacy_body(self):
        # The body must never be run during this test, even on the RED version.
        legacy = getattr(self.robot.brute_force_security_code, "__wrapped__", None)
        self.assertIsNotNone(legacy, "A separate fail-closed entry wrapper is required")
        class NoSubmission:
            def __getattr__(inner, name):
                raise AssertionError("Linked verification reached a browser operation")
        with patch.object(self.panel, "publish_profile_result",
                          wraps=self.panel.publish_profile_result) as publish:
            with self.assertRaisesRegex(self.panel.ProfileIdentityStopped,
                                        "[Mm]anual verification.*review"):
                self.robot.brute_force_security_code(NoSubmission())
            publish.assert_called_once_with(
                "profile1", self.attempt["runner_id"], self.attempt["claimed_identity_fingerprint"],
                {"receipt": "", "payment": "", "link": "", "identity_verified": False})
        self.assert_review_preserved()

    def test_unlinked_profile_id_does_not_restrict_legacy_dispatch(self):
        args = types.SimpleNamespace(manual=False)
        with patch.object(self.robot2, "_CLAIMED_CRM_DEVICE_ID", ""), \
                patch.object(self.robot2, "_choose_codeflow", return_value=("fast", ["fixture"])) as choose, \
                patch.object(self.robot2, "run_code_fast", return_value="safe-fixture-result") as fast:
            args.gap_ms = 0
            self.assertEqual(self.robot2._run_codeflow(object(), object(), args), "safe-fixture-result")
            choose.assert_called_once_with(args)
            fast.assert_called_once()
        self.assertFalse(self.panel.load_store()["profiles"][0]["identity_review_required"])

    def test_unlinked_boundary_delegates_only_to_a_safe_no_work_callback(self):
        boundary = getattr(self.robot, "_restrict_unattended_verification", None)
        self.assertTrue(callable(boundary), "A separate registration boundary is required")
        with patch.object(self.robot, "_CLAIMED_CRM_DEVICE_ID", ""):
            calls = []
            callback = boundary(lambda value: calls.append(value) or "legacy-result")
            self.assertEqual(callback("fixture"), "legacy-result")
            self.assertEqual(calls, ["fixture"])

    def test_legacy_authentication_algorithm_source_bytes_are_unchanged(self):
        expected = {
            "gomrok.py": {
                "_SCS_FAST_JS": "31619ecb6de598300f3889ccac924e08ffb83d73033de82c3d76393717508705",
                "brute_force_security_code": "fab4998cbbe1ce20cb37132d68571aa80da7a7707d68e1a10305ea6ac0ae0b3e",
            },
            "gomrok2.py": {
                "_FAST_RETRY_JS": "bc8a99ea19002c9a273b28ad675fae1d3fe46c6a7b9939550ecc48e9c549effd",
                "_submit_code_watched": "ecb15bbd1c1051eb0e95641d011715ca19a4789c7061d80aa27c1c98238ce18e",
                "run_code_attempts": "4942b1f5b52b9187cea003a4abf34d2290110e41d198e8bcbb86e1f9d4004268",
                "run_code_fast": "44f8b4343c16345cfd9c4ece6ac5c96e0763e541902b012ec0a7ca7f4487c737",
            },
        }
        for filename, wanted in expected.items():
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            found = {}
            for node in ast.parse(source).body:
                name = getattr(node, "name", None)
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                if name in wanted:
                    found[name] = hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest()
            self.assertEqual(found, wanted)


if __name__ == "__main__":
    unittest.main()
