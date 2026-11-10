import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import ctypes


ROOT = Path(__file__).resolve().parents[2]


class MaintenanceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="irica-maintenance-test-")
        self.addCleanup(self.temp.cleanup)
        try:
            module = importlib.import_module("maintenance_gate")
        except ModuleNotFoundError:
            self.fail("The maintenance admission gate is not implemented")
        self.gate = module.MaintenanceGate(
            install_dir=self.temp.name,
            registry_path=str(Path(self.temp.name) / "jobs.json"),
        )

    def test_idle_install_closes_admission_until_lease_closes(self):
        lease = self.gate.begin_install(exclude_pids={os.getpid()})
        try:
            with self.assertRaisesRegex(RuntimeError, "update"):
                with self.gate.admit():
                    self.fail("A job was admitted during installation")
        finally:
            lease.close()
        with self.gate.admit():
            pass

    def test_registered_live_worker_blocks_installation(self):
        created = self.gate.register(os.getpid())
        self.addCleanup(self.gate.unregister, os.getpid(), expected_created=created)
        with self.assertRaisesRegex(RuntimeError, "active"):
            self.gate.begin_install()
        with self.gate.admit():
            pass

    def test_dead_worker_does_not_block_installation(self):
        child = subprocess.Popen(self._worker("wait"), stdin=subprocess.PIPE)
        try:
            self.gate.register(child.pid)
            child.stdin.write(b"x")
            child.stdin.flush()
            child.wait(timeout=10)
            lease = self.gate.begin_install(exclude_pids={os.getpid()})
            lease.close()
        finally:
            child.stdin.close()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)

    def test_corrupt_registry_fails_closed(self):
        Path(self.gate.registry_path).write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "registry"):
            self.gate.begin_install(exclude_pids={os.getpid()})

    def test_process_enumeration_failure_blocks_installation(self):
        def failed(*_):
            ctypes.set_last_error(5)
            return False
        with patch.object(self.gate._api.dll, "Process32FirstW", side_effect=failed):
            with self.assertRaisesRegex(RuntimeError, "inspect"):
                self.gate.begin_install(exclude_pids={os.getpid()})

    def test_pid_reuse_cannot_remove_a_new_worker(self):
        import json
        newer = {"pid": os.getpid(), "created": 2}
        Path(self.gate.registry_path).write_text(
            json.dumps({"version": 1, "jobs": [newer]}), encoding="utf-8")
        self.gate.unregister(os.getpid(), expected_created=1)
        stored = json.loads(Path(self.gate.registry_path).read_text(encoding="utf-8"))
        self.assertEqual(stored["jobs"], [newer])

    def test_nested_lifetime_with_new_gate_object_preserves_outer_registration(self):
        from maintenance_gate import MaintenanceGate
        import maintenance_gate
        lifetime = getattr(maintenance_gate, "job_lifetime", None)
        self.assertTrue(callable(lifetime), "A process-lifetime context is required")
        same_domain = MaintenanceGate(install_dir=self.temp.name,
                                     registry_path=self.gate.registry_path)
        with lifetime(gate=self.gate) as outer_created:
            self.assertIsInstance(outer_created, int)
            with lifetime(gate=same_domain) as inner_created:
                self.assertEqual(inner_created, outer_created)
            self.assertEqual(self.gate._read_jobs(), [{"pid": os.getpid(), "created": outer_created}])
            with self.assertRaisesRegex(RuntimeError, "active"):
                self.gate.begin_install()
        self.assertEqual(self.gate._read_jobs(), [])

    def test_nested_lifetime_rechecks_install_lease_without_releasing_outer_owner(self):
        import maintenance_gate
        with maintenance_gate.job_lifetime(gate=self.gate) as created:
            with self.gate.begin_install(exclude_pids={os.getpid()}):
                with self.assertRaisesRegex(RuntimeError, "update"):
                    with maintenance_gate.job_lifetime(gate=self.gate):
                        self.fail("Nested work was admitted under an installation lease")
            self.assertEqual(self.gate._read_jobs(), [{"pid": os.getpid(), "created": created}])
        self.assertEqual(self.gate._read_jobs(), [])

    def test_lifetime_cleanup_cannot_remove_reused_pid_record(self):
        import maintenance_gate
        lifetime = getattr(maintenance_gate, "job_lifetime", None)
        self.assertTrue(callable(lifetime), "A process-lifetime context is required")
        with lifetime(gate=self.gate) as created:
            newer = {"pid": os.getpid(), "created": created + 1}
            self.gate._write_jobs([newer])
        self.assertEqual(self.gate._read_jobs(), [newer])
        self.gate.unregister(os.getpid(), expected_created=newer["created"])

    def _worker(self, mode):
        return [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--gate-worker", mode, self.temp.name, self.gate.registry_path]

    @unittest.skipUnless(sys.platform == "win32", "Windows kernel-object test")
    def test_another_process_observes_install_admission_closed(self):
        lease = self.gate.begin_install(exclude_pids={os.getpid()})
        try:
            result = subprocess.run(
                self._worker("admit"), capture_output=True, text=True, timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("update", result.stderr.lower())
        finally:
            lease.close()

    def test_handoff_handle_keeps_admission_closed(self):
        lease = self.gate.begin_install(exclude_pids={os.getpid()})
        child = subprocess.Popen(self._worker("hold-event"), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b"ready")
            lease.close()
            with self.assertRaisesRegex(RuntimeError, "update"):
                self.gate.assert_admission_open()
            child.stdin.write(b"x")
            child.stdin.flush()
            child.wait(timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr.read())
            self.gate.assert_admission_open()
        finally:
            lease.close()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def test_install_cannot_race_an_admission_in_progress(self):
        with self.gate.admit():
            result = subprocess.run(self._worker("install"), capture_output=True,
                                    text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("admission guard is busy", result.stderr.lower())
        self.gate.assert_admission_open()


def _gate_worker():
    if len(sys.argv) != 5 or sys.argv[2] not in ("wait", "admit", "hold-event", "install"):
        raise SystemExit("Invalid gate fixture arguments")
    sys.path.insert(0, str(ROOT))
    from maintenance_gate import MaintenanceGate, job_lifetime
    gate = MaintenanceGate(install_dir=sys.argv[3], registry_path=sys.argv[4])
    if sys.argv[2] == "admit":
        with job_lifetime(gate=gate):
            pass
    elif sys.argv[2] == "install":
        with gate.begin_install(exclude_pids={os.getpid()}):
            pass
    elif sys.argv[2] == "wait":
        sys.stdin.buffer.read(1)
    else:
        handle = gate._api.dll.OpenEventW(gate._api.SYNCHRONIZE, False, gate.event_name)
        if not handle:
            raise SystemExit("The installation event was not inherited")
        try:
            print("ready", flush=True)
            sys.stdin.buffer.read(1)
        finally:
            gate._api.dll.CloseHandle(handle)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--gate-worker":
        _gate_worker()
    else:
        unittest.main()
