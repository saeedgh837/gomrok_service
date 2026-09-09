# -*- coding: utf-8 -*-
"""Tests for the default-on system debug logging and log retention caps."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import panel  # noqa: E402


class SystemDebugDefaultsTests(unittest.TestCase):
    def test_default_config_is_enabled_when_no_config_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = os.path.join(temp_dir, "never_created", "system_debug.json")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", missing):
                self.assertEqual(panel.load_system_debug_config(), {"enabled": True})

    def test_saved_disabled_config_is_respected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "system_debug.json")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", config_path):
                panel.save_system_debug_config({"enabled": False})
                self.assertEqual(panel.load_system_debug_config(), {"enabled": False})

    def test_robot_child_env_defaults_to_debug_on(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = os.path.join(temp_dir, "never_created", "system_debug.json")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", missing):
                env = panel._robot_child_env()
                self.assertEqual(env["IRICA_SYSTEM_DEBUG"], "1")


class JsonlRetentionTests(unittest.TestCase):
    def test_append_jsonl_trims_old_lines_beyond_cap(self):
        import json
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_path = os.path.join(temp_dir, "logs.jsonl")
            with patch.object(panel, "LOGS_PATH", logs_path):
                for i in range(60):
                    panel._append_jsonl(LOGS_PATH := logs_path, {"i": i})
                with open(logs_path, encoding="utf-8") as handle:
                    lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(lines), 40)
        self.assertEqual(lines[-1]["i"], 59)

    def test_append_jsonl_rejects_non_dict_and_loses_nothing_on_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_path = os.path.join(temp_dir, "logs.jsonl")
            with patch.object(panel, "LOGS_PATH", logs_path):
                panel._append_jsonl(logs_path, None)
                panel._append_jsonl(logs_path, {"ok": 1})
            with open(logs_path, encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("\"ok\": 1", lines[0])


if __name__ == "__main__":
    unittest.main()
