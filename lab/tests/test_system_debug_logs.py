import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import panel


class SystemDebugTests(unittest.TestCase):
    def test_config_is_persisted_and_child_env_receives_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "system_debug.json")
            log_dir = os.path.join(temp_dir, "logs", "system")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", config_path), \
                    patch.object(panel, "SYSTEM_LOG_DIR", log_dir):
                # Fresh file -> default ON; after saving disabled -> stays off.
                self.assertEqual(panel.load_system_debug_config(), {"enabled": True})
                self.assertEqual(panel.save_system_debug_config({"enabled": False}),
                                 {"enabled": False})
                self.assertEqual(panel.load_system_debug_config(), {"enabled": False})
                env = panel._robot_child_env()
                self.assertEqual(env["IRICA_SYSTEM_DEBUG"], "0")
                self.assertEqual(env["IRICA_SYSTEM_LOG_DIR"], log_dir)

    def test_logs_are_listed_newest_first_and_read_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(panel, "SYSTEM_LOG_DIR", temp_dir):
            older = os.path.join(temp_dir, "20260817_old.log")
            newer = os.path.join(temp_dir, "20260818_new.log")
            with open(older, "w", encoding="utf-8") as handle:
                handle.write("old")
            with open(newer, "w", encoding="utf-8") as handle:
                handle.write("new traceback")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            logs = panel.list_system_logs()
            self.assertEqual([item["name"] for item in logs], ["20260818_new.log", "20260817_old.log"])
            self.assertEqual(panel.read_system_log("20260818_new.log"), "new traceback")
            with self.assertRaises(ValueError):
                panel.read_system_log("../profiles.json")

    def test_launcher_captures_failures_before_robot_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            script = os.path.join(os.path.dirname(panel.__file__), "missing_diagnostic_fixture.py")
            log_dir = os.path.join(temp_dir, "logs")
            env = os.environ.copy()
            env["IRICA_SYSTEM_DEBUG"] = "1"
            env["IRICA_SYSTEM_LOG_DIR"] = log_dir
            result = subprocess.run([sys.executable, os.path.join(os.path.dirname(panel.__file__), "robot_launcher.py"), script],
                                    cwd=os.path.dirname(panel.__file__), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(result.returncode, 0)
            logs = os.listdir(log_dir)
            self.assertEqual(len(logs), 1)
            with open(os.path.join(log_dir, logs[0]), encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("missing_diagnostic_fixture.py", content)
            self.assertIn("Traceback", content)
            self.assertIn("python=", content)
            self.assertIn("argv=", content)

    def test_panel_contains_debug_controls_and_routes(self):
        html = panel.render_page().decode("utf-8")
        for marker in ("Debug log سیستمی", "system-debug-toggle", "system-log-list",
                       "system-log-viewer", "/system-debug-config", "/system-logs", "/system-log"):
            self.assertIn(marker, html)
        # v1.7: both dispatchers moved behind do_GET/do_POST exception safety
        # nets, so the route literals live in the *_body functions.
        self.assertIn("/system-logs", panel.Handler._do_get_body.__code__.co_consts)
        self.assertIn("/system-debug-config",
                      panel.Handler._do_post_body.__code__.co_consts)

    def test_all_panel_robot_spawns_use_launcher_and_debug_env(self):
        source = open(panel.__file__, encoding="utf-8").read()
        for script in ("gomrok.py", "gomrok2.py", "profile_inquiry.py", "exitban.py"):
            self.assertIn(f'_robot_command(', source)
            self.assertIn(f'"{script}"', source)
        self.assertIn('"robot_launcher.py"', source)
        self.assertNotIn("stdout=subprocess.DEVNULL", source[source.index("def start_profile_process"):source.index("def stop_profile_process")])


if __name__ == "__main__":
    unittest.main()
