import contextlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import panel


class MaintenanceHttpDrainTests(unittest.TestCase):
    def setUp(self):
        self.started, self.release = threading.Event(), threading.Event()
        started, release = self.started, self.release

        class BlockingHandler(panel.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/work":
                    started.set()
                    release.wait(timeout=3)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_):
                pass

        self.server = panel.PanelHTTPServer(("127.0.0.1", 0), BlockingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.client_errors = []

    def stop_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def request(self, path):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_idle_server_closes_admission_until_rollback(self):
        operation = getattr(self.server, "begin_maintenance", None)
        self.assertTrue(callable(operation), "The HTTP server cannot drain active requests")
        self.assertTrue(operation(timeout=0.2))
        self.assertEqual(self.request("/"), 503)
        self.server.end_maintenance()
        self.assertEqual(self.request("/"), 200)

    def test_busy_request_prevents_shutdown_and_timeout_reopens_admission(self):
        operation = getattr(self.server, "begin_maintenance", None)
        self.assertTrue(callable(operation), "The HTTP server cannot drain active requests")
        worker = threading.Thread(target=lambda: self.request("/work"), daemon=True)
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(self.release.set)
        self.assertTrue(self.started.wait(timeout=2))
        self.assertFalse(operation(timeout=0.03))
        self.assertEqual(self.request("/"), 200)
        self.release.set()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(operation(timeout=0.2))


class GracefulUpdateShutdownTests(unittest.TestCase):
    def setUp(self):
        operation = getattr(panel, "prepare_application_update_shutdown", None)
        self.assertTrue(callable(operation), "The graceful update shutdown is not implemented")
        self.operation = operation
        self.temp = tempfile.TemporaryDirectory(prefix="irica-shutdown-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = Mock()
        self.server.begin_maintenance.return_value = True
        self.cloud, self.scheduler, self.bootstrap = Mock(), Mock(), Mock()
        for thread in (self.cloud, self.scheduler, self.bootstrap):
            thread.is_alive.return_value = False
        for name, value in {
            "_ACTIVE_HTTP_SERVER": self.server,
            "_CLOUD_WORKER_THREAD": self.cloud,
            "_CRM_SCHEDULER_THREAD": self.scheduler,
            "_TELEGRAM_BOOTSTRAP_THREAD": self.bootstrap,
            "_SHUTDOWN_REQUESTED": threading.Event(),
            "_MAINTENANCE_DRAINING": threading.Event(),
            "_STORE_LOCK_PATH": str(self.root / "profiles.lock"),
        }.items():
            self.start_patch(panel, name, value, create=True)
        self.cloud_stop = self.start_patch(panel, "cloud_worker_stop", return_value=self.cloud)
        self.scheduler_stop = self.start_patch(panel, "crm_scheduler_stop")
        self.start_patch(panel, "cloud_worker_start", return_value=self.cloud)
        self.start_patch(panel, "crm_scheduler_start", return_value=self.scheduler)
        self.forced_shutdown = self.start_patch(panel, "shutdown_everything")
        self.force_process = self.start_patch(panel, "stop_profile_process")
        self.environment = patch.dict("os.environ", {"IRICA_CRM_STORE": str(self.root / "crm.json")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def start_patch(self, target, name, *args, **kwargs):
        operation = patch.object(target, name, *args, **kwargs)
        result = operation.start()
        self.addCleanup(operation.stop)
        return result

    def test_idle_shutdown_joins_workers_and_never_forces_a_job(self):
        self.assertTrue(self.operation(timeout=0.2))
        self.assertTrue(panel._SHUTDOWN_REQUESTED.is_set())
        for thread in (self.cloud, self.scheduler, self.bootstrap):
            thread.join.assert_called_once()
        self.forced_shutdown.assert_not_called()
        self.force_process.assert_not_called()
        self.server.end_maintenance.assert_not_called()

    def test_http_drain_failure_leaves_workers_and_panel_running(self):
        self.server.begin_maintenance.return_value = False
        self.assertFalse(self.operation(timeout=0.2))
        self.assertFalse(panel._SHUTDOWN_REQUESTED.is_set())
        self.cloud_stop.assert_not_called()
        self.scheduler_stop.assert_not_called()
        self.forced_shutdown.assert_not_called()

    def test_worker_timeout_rolls_back_admission_and_shutdown(self):
        self.cloud.is_alive.return_value = True
        self.assertFalse(self.operation(timeout=0.04))
        self.assertFalse(panel._SHUTDOWN_REQUESTED.is_set())
        self.assertFalse(panel._MAINTENANCE_DRAINING.is_set())
        self.server.end_maintenance.assert_called_once()
        self.forced_shutdown.assert_not_called()
        self.force_process.assert_not_called()

    def test_profile_lock_timeout_prevents_partial_shutdown(self):
        @contextlib.contextmanager
        def denied(*_, **__):
            raise TimeoutError("synthetic profile lock")
            yield

        self.start_patch(panel, "_locked_store", denied)
        self.assertFalse(self.operation(timeout=0.2))
        self.assertFalse(panel._SHUTDOWN_REQUESTED.is_set())
        self.server.end_maintenance.assert_called_once()

    def test_missing_live_server_never_authorizes_shutdown(self):
        panel._ACTIVE_HTTP_SERVER = None
        self.assertFalse(self.operation(timeout=0.2))
        self.assertFalse(panel._SHUTDOWN_REQUESTED.is_set())
        self.forced_shutdown.assert_not_called()


class MaintenanceWorkerRollbackTests(unittest.TestCase):
    def check_resume_race(self, cloud):
        first_started, stop_seen = threading.Event(), threading.Event()
        release_exit, restarted, finish = threading.Event(), threading.Event(), threading.Event()
        calls = []
        prefix = "_CLOUD_WORKER" if cloud else "_CRM_SCHEDULER"
        start = panel.cloud_worker_start if cloud else panel.crm_scheduler_start
        stop = panel.cloud_worker_stop if cloud else panel.crm_scheduler_stop
        loop_name = "_cloud_worker_loop" if cloud else "_crm_scheduler_loop"
        environment = "IRICA_CLOUD_WORKER_OFF" if cloud else "IRICA_CRM_SCHEDULER_OFF"

        def body():
            calls.append(threading.current_thread())
            if len(calls) == 1:
                first_started.set()
                if cloud:
                    with panel._CLOUD_WORKER_CV:
                        panel._CLOUD_WORKER_CV.wait_for(lambda: panel._CLOUD_WORKER_STOP, timeout=2)
                else:
                    panel._CRM_SCHEDULER_STOP.wait(timeout=2)
                stop_seen.set()
                release_exit.wait(timeout=2)
            else:
                restarted.set()
                finish.wait(timeout=2)

        with contextlib.ExitStack() as patches:
            patches.enter_context(patch.dict("os.environ", {environment: "0"}))
            patches.enter_context(patch.object(panel, "_MAINTENANCE_DRAINING", threading.Event(), create=True))
            patches.enter_context(patch.object(panel, prefix + "_THREAD", None))
            patches.enter_context(patch.object(panel, prefix + "_RESTART", False, create=True))
            patches.enter_context(patch.object(panel, loop_name, side_effect=body))
            if cloud:
                patches.enter_context(patch.object(panel, "_CLOUD_WORKER_STOP", False))
                patches.enter_context(patch.object(panel, "_CLOUD_WORKER_KICK", False))
                patches.enter_context(patch.object(panel, "_cloud_configured", return_value=True))
            else:
                patches.enter_context(patch.object(panel, "_CRM_SCHEDULER_STOP", threading.Event()))
            first = start()
            try:
                self.assertTrue(first_started.wait(timeout=1))
                stop()
                self.assertTrue(stop_seen.wait(timeout=1))
                start()
                release_exit.set()
                self.assertTrue(restarted.wait(timeout=1), "Rollback lost a worker that had already observed stop")
            finally:
                stop()
                release_exit.set()
                finish.set()
                first.join(timeout=3)
                for thread in calls:
                    thread.join(timeout=3)
                    self.assertFalse(thread.is_alive())

    def test_cloud_rollback_restarts_a_worker_already_leaving(self):
        self.check_resume_race(cloud=True)

    def test_scheduler_rollback_restarts_a_worker_already_leaving(self):
        self.check_resume_race(cloud=False)


class MaintenanceStoreBarrierTests(unittest.TestCase):
    def test_store_barrier_has_a_bounded_thread_lock_wait(self):
        import inspect
        self.assertIn("timeout", inspect.signature(panel._locked_store).parameters,
                      "The profile lock cannot enforce the shutdown deadline")
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory(prefix="irica-lock-barrier-") as directory:
            with patch.object(panel, "_STORE_LOCK_PATH", str(Path(directory) / "profiles.lock")):
                def hold():
                    with panel._locked_store():
                        entered.set()
                        release.wait(timeout=3)

                holder = threading.Thread(target=hold, daemon=True)
                holder.start()
                try:
                    self.assertTrue(entered.wait(timeout=1))
                    with self.assertRaises(TimeoutError):
                        with panel._locked_store(timeout=0.03):
                            self.fail("A competing store transaction entered")
                finally:
                    release.set()
                    holder.join(timeout=3)
                    self.assertFalse(holder.is_alive())


if __name__ == "__main__":
    unittest.main()
