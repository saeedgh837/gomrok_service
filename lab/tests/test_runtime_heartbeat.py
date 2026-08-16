import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import panel


class RuntimeHeartbeatTests(unittest.TestCase):
    def _paths(self, tmp):
        return patch.multiple(
            panel,
            PROFILES_PATH=os.path.join(tmp, "profiles.json"),
            INPUTS_PATH=os.path.join(tmp, "inputs.json"),
            _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock"),
        )

    def test_stale_busy_profile_without_pid_is_released(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            stale = (datetime.now() - timedelta(seconds=130)).strftime("%Y-%m-%d %H:%M:%S")
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "A", "run_status": "busy",
                    "runner_id": "old", "runner_pid": 0, "heartbeat_at": stale,
                }]}, f)
            self.assertTrue(panel.clear_stale_busy_profiles())
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["run_status"], "idle")
            self.assertEqual(profile["runner_id"], "")

    def test_stale_heartbeat_releases_even_if_pid_looks_alive(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp), \
                patch.object(panel, "_pid_alive", return_value=True):
            stale = (datetime.now() - timedelta(seconds=130)).strftime("%Y-%m-%d %H:%M:%S")
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "A", "run_status": "busy",
                    "runner_id": "old", "runner_pid": 999, "heartbeat_at": stale,
                }]}, f)
            self.assertTrue(panel.clear_stale_busy_profiles())
            self.assertEqual(panel.load_store()["profiles"][0]["run_status"], "idle")

    def test_heartbeat_keeps_profile_busy(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "A", "run_status": "busy",
                    "runner_id": "r1", "runner_pid": os.getpid(),
                }]}, f)
            self.assertTrue(panel.heartbeat_profile("p1", "r1"))
            profile = panel.load_store()["profiles"][0]
            self.assertTrue(profile["heartbeat_at"])
            self.assertFalse(panel.clear_stale_busy_profiles())

    def test_specific_claim_never_falls_back_to_other_profile(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [
                    {"id": "p1", "title": "A", "run_status": "busy", "runner_pid": os.getpid(), "heartbeat_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                    {"id": "p2", "title": "B"},
                ]}, f)
            self.assertIsNone(panel.claim_profile("p1"))

    def test_layout_places_profile_sidebar_on_right(self):
        html = panel.render_page().decode("utf-8")
        self.assertIn("flex-direction:row-reverse", html)
        self.assertIn("⚙️ تنظیمات کلی", html)


if __name__ == "__main__":
    unittest.main()
