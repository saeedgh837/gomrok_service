import json
import os
import tempfile
import unittest
from unittest.mock import patch

import panel


class RuntimeApiTests(unittest.TestCase):
    def test_spawned_robot_claims_the_exact_complete_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            profile = {
                "id": "p2", "title": "Selected", "national": "0012345678",
                "birth": "1370/1/1", "mobile": "09120000000",
                "imeis": ["123456789012345"], "enabled": False,
                "registration_status": "pending",
            }
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [
                    {"id": "p1", "title": "Other"}, profile,
                ]}, f)
            with patch.multiple(panel, PROFILES_PATH=profiles_path,
                                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                                _STORE_LOCK_PATH=profiles_path + ".lock"):
                claimed = panel.claim_profile("p2")
                self.assertEqual(claimed["id"], "p2")
                self.assertEqual(claimed["imeis"], ["123456789012345"])
                self.assertEqual(panel.load_store()["profiles"][1]["run_status"], "busy")

    def test_claim_profile_can_target_specific_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [
                    {"id": "p1", "title": "A"}, {"id": "p2", "title": "B"}
                ]}, f)
            with patch.object(panel, "PROFILES_PATH", path), \
                    patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
                    patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"):
                claimed = panel.claim_profile("p2")
                self.assertEqual(claimed["id"], "p2")

    def test_explicit_start_can_claim_disabled_selected_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [
                    {"id": "p1", "title": "A", "enabled": True},
                    {"id": "p2", "title": "B", "enabled": False},
                ]}, f)
            with patch.object(panel, "PROFILES_PATH", path), \
                    patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
                    patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"):
                claimed = panel.claim_profile("p2")
                self.assertIsNotNone(claimed)
                self.assertEqual(claimed["id"], "p2")

    def test_start_command_targets_profile_and_stop_uses_pid(self):
        with patch.dict(os.environ, {"PYTHONPATH": "bad-hermes-path", "VIRTUAL_ENV": "bad-hermes-venv"}), \
                patch.object(panel.subprocess, "Popen") as popen:
            proc = popen.return_value
            proc.pid = 4321
            result = panel.start_profile_process("p2")
            self.assertEqual(result["pid"], 4321)
            command = popen.call_args.args[0]
            self.assertEqual(command[0], panel.ROBOT_PYTHON)
            self.assertIn("--profile-id", command)
            self.assertIn("p2", command)
            child_env = popen.call_args.kwargs["env"]
            self.assertNotIn("PYTHONPATH", child_env)
            self.assertNotIn("VIRTUAL_ENV", child_env)
            self.assertEqual(child_env.get("PYTHONIOENCODING"), "utf-8")
            self.assertEqual(child_env.get("PYTHONUTF8"), "1")
            self.assertNotEqual(popen.call_args.kwargs["stdout"], panel.subprocess.DEVNULL)

        with patch.object(panel, "_pid_alive", return_value=True), \
                patch.object(panel.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(panel.stop_profile_process(4321))
            self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4321", "/T", "/F"])

    def test_gomrok_stdio_utf8_allows_persian_title_print(self):
        import gomrok
        import io
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with patch.object(gomrok.sys, "stdout", buf), patch.object(gomrok.sys, "stderr", buf):
            gomrok.ensure_utf8_stdio()
            self.assertEqual(gomrok.sys.stdout.encoding.lower(), "utf-8")
            gomrok.sys.stdout.write(">>> Profile claim shod: 'موئمن صالحی' | status=BUSY\n")
            gomrok.sys.stdout.flush()

    @unittest.skipUnless(os.name == "nt", "Windows process-tree behavior")
    def test_stop_terminates_real_worker_process(self):
        proc = panel.subprocess.Popen([panel.ROBOT_PYTHON, "-c", "import time; time.sleep(60)"])
        try:
            self.assertTrue(panel.stop_profile_process(proc.pid))
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:
                panel.subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                     stdout=panel.subprocess.DEVNULL, stderr=panel.subprocess.DEVNULL)

    def test_stop_releases_the_requested_profile_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "A", "run_status": "busy",
                    "runner_id": "r1", "runner_pid": 4321,
                    "heartbeat_at": "2026-08-01 12:00:00",
                }]}, f)
            with patch.multiple(panel, PROFILES_PATH=profiles_path,
                                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                                _STORE_LOCK_PATH=profiles_path + ".lock"), \
                    patch.object(panel, "stop_profile_process", return_value=True):
                status, payload = panel.stop_profile("p1")
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["run_status"], "idle")
                self.assertEqual(profile["runner_pid"], 0)

    def test_start_endpoint_launches_selected_profile_even_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [
                    {"id": "p1", "title": "A", "national": "0012345678", "birth": "1370/1/1", "imeis": ["123456789012345"], "enabled": False, "registration_status": "pending"},
                    {"id": "p2", "title": "B", "national": "0012345679", "birth": "1370/1/2", "imeis": ["123456789012346"], "enabled": False, "registration_status": "pending"},
                ]}, f)
            with patch.multiple(panel, PROFILES_PATH=profiles_path,
                                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                                _STORE_LOCK_PATH=profiles_path + ".lock"), \
                    patch.object(panel, "start_profile_process", return_value={"pid": 4321, "profile_id": "p2"}) as start:
                status, payload = panel.start_profile("p2")
                self.assertEqual(status, 200)
                self.assertEqual(payload["profile_id"], "p2")
                start.assert_called_once_with("p2")
                profile = next(p for p in panel.load_store()["profiles"] if p["id"] == "p2")
                self.assertEqual(profile["runner_pid"], 4321)

    def test_start_rejects_incomplete_profile_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "A", "national": "0012345678",
                    "birth": "1370/1/1", "mobile": "09120000000", "imeis": [],
                    "enabled": True, "registration_status": "pending",
                }]}, f)
            with patch.multiple(panel, PROFILES_PATH=profiles_path,
                                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                                _STORE_LOCK_PATH=profiles_path + ".lock"), \
                    patch.object(panel, "start_profile_process") as start:
                status, payload = panel.start_profile("p1")
                self.assertEqual(status, 422)
                self.assertIn("سریال", "، ".join(payload["missing"]))
                start.assert_not_called()

    def test_manual_manage_profile_can_start_without_automatic_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "Manual", "manual_manage": True,
                    "enabled": False, "registration_status": "pending",
                }]}, f)
            with patch.multiple(panel, PROFILES_PATH=profiles_path,
                                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                                _STORE_LOCK_PATH=profiles_path + ".lock"), \
                    patch.object(panel, "start_profile_process", return_value={"pid": 4321, "profile_id": "p1"}):
                status, payload = panel.start_profile("p1")
                self.assertEqual(status, 200)
                self.assertEqual(payload["profile_id"], "p1")

    def test_spawn_failure_returns_error_and_does_not_mark_profile_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "A", "national": "0012345678",
                    "birth": "1370/1/1", "imeis": ["123456789012345"],
                    "enabled": True, "registration_status": "pending",
                }]}, f)
            with patch.multiple(panel, PROFILES_PATH=profiles_path,
                                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                                _STORE_LOCK_PATH=profiles_path + ".lock"), \
                    patch.object(panel, "start_profile_process", side_effect=OSError("spawn failed")):
                status, payload = panel.start_profile("p1")
                self.assertEqual(status, 500)
                self.assertEqual(payload["error"], "شروع ربات انجام نشد")
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["run_status"], "idle")
                self.assertEqual(profile["runner_pid"], 0)


if __name__ == "__main__":
    unittest.main()
