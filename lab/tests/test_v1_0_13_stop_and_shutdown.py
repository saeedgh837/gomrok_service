"""v1.0.13 features: per-profile inquiry STOP button + SYSTEM CLOSE button.

- stop_profile_inquiry(profile_id): kills the profile_inquiry.py process tree
  tracked in device_inquiry_pid and resets the running inquiry bookkeeping.
- POST /profile-device-stop: endpoint wired in Handler.do_POST.
- POST /shutdown: stops every running robot (gomrok/gomrok2 runners, device
  inquiries, estelam run) then terminates the panel process itself.
- site-1: a STOP button beside the device-inquiry start button; a red
  "بستن سامانه" button in the header actions (confirm() guarded).
- estelam page (/site-2): the same system-close button in its top bar.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import panel


def _store(path, **profile):
    base = {"id": "p1", "title": "Test", "national": "0012345678",
            "birth": "1360/01/01", "mobile": "09120000000",
            "imeis": ["350358333315545"]}
    base.update(profile)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"active_id": "p1", "profiles": [base]}, handle)


class StopProfileInquiryTests(unittest.TestCase):
    def _paths(self, tmp):
        path = os.path.join(tmp, "profiles.json")
        return path, patch.object(panel, "PROFILES_PATH", path), \
            patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
            patch.object(panel, "_STORE_LOCK_PATH", path + ".lock")

    def test_stop_kills_pid_and_resets_running_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            _store(path, device_inquiry_pid=424242,
                   device_inquiry_service="gomrok",
                   gomrok_inquiry_status="running")
            with p1, p2, p3, \
                 patch.object(panel, "stop_profile_process") as kill, \
                 patch.object(panel, "_pid_alive", return_value=True):
                ok, message = panel.stop_profile_inquiry("p1")
            self.assertTrue(ok)
            kill.assert_called_once_with(424242)
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["device_inquiry_pid"], 0)
            self.assertEqual(profile["device_inquiry_service"], "")
            self.assertNotEqual(profile["gomrok_inquiry_status"], "running")

    def test_stop_without_running_inquiry_reports_calm_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            _store(path)
            with p1, p2, p3, patch.object(panel, "stop_profile_process") as kill:
                ok, message = panel.stop_profile_inquiry("p1")
            self.assertTrue(ok)
            kill.assert_not_called()
            self.assertIn("در حال اجرا نیست", message)

    def test_stop_missing_profile_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            _store(path)
            with p1, p2, p3:
                ok, _ = panel.stop_profile_inquiry("nope")
            self.assertFalse(ok)

    def test_stop_survives_dead_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            _store(path, device_inquiry_pid=424242,
                   device_inquiry_service="hamta",
                   hamta_inquiry_status="running")
            with p1, p2, p3, \
                 patch.object(panel, "stop_profile_process") as kill, \
                 patch.object(panel, "_pid_alive", return_value=False):
                ok, _ = panel.stop_profile_inquiry("p1")
            self.assertTrue(ok)
            kill.assert_not_called()
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["hamta_inquiry_status"], "not_checked")


class StopEndpointSurfaceTests(unittest.TestCase):
    def test_page_carries_stop_button_and_endpoint(self):
        html = panel.render_page().decode("utf-8")
        self.assertIn("/profile-device-stop", html)
        self.assertIn("stopProfileDeviceInquiry", html)
        self.assertIn("توقف استعلام", html)

    def test_both_pages_carry_system_close_button(self):
        for html in (panel.render_page().decode("utf-8"),
                     panel.render_estelam_page().decode("utf-8")):
            self.assertIn("closeWholeSystem", html)
            self.assertIn("/shutdown", html)
            self.assertIn("بستن سامانه", html)


class ShutdownEndpointTests(unittest.TestCase):
    def test_shutdown_stops_everything_then_marks_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            _store(path, run_status="busy", runner_pid=111,
                   device_inquiry_pid=222, device_inquiry_service="all",
                   gomrok_inquiry_status="running", hamta_inquiry_status="running")
            estelam = os.path.join(tmp, "estelam.json")
            with open(estelam, "w", encoding="utf-8") as handle:
                json.dump({"person": {}, "devices": [], "run_pid": 333}, handle)
            with patch.object(panel, "PROFILES_PATH", path), \
                 patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
                 patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"), \
                 patch.object(panel, "ESTELAM_PATH", estelam), \
                 patch.object(panel, "stop_profile_process") as kill, \
                 patch.object(panel, "_pid_alive", return_value=True):
                result = panel.shutdown_everything()
            self.assertTrue(result["ok"])
            killed = {call.args[0] for call in kill.call_args_list}
            self.assertEqual(killed, {111, 222, 333})
            self.assertTrue(panel._SHUTDOWN_REQUESTED.is_set())
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["device_inquiry_pid"], 0)
            self.assertNotEqual(profile["gomrok_inquiry_status"], "running")
            self.assertNotEqual(profile["run_status"], "busy")
            self.assertEqual(panel.estelam_load().get("run_pid"), 0)
            panel._SHUTDOWN_REQUESTED.clear()

    def test_shutdown_when_nothing_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            _store(path)
            estelam = os.path.join(tmp, "estelam.json")
            with open(estelam, "w", encoding="utf-8") as handle:
                json.dump({"person": {}, "devices": [], "run_pid": 0}, handle)
            with patch.object(panel, "PROFILES_PATH", path), \
                 patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
                 patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"), \
                 patch.object(panel, "ESTELAM_PATH", estelam), \
                 patch.object(panel, "stop_profile_process") as kill:
                result = panel.shutdown_everything()
            self.assertTrue(result["ok"])
            kill.assert_not_called()
            self.assertEqual(result["stopped"], [])
            panel._SHUTDOWN_REQUESTED.clear()


if __name__ == "__main__":
    unittest.main()
