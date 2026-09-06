import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import panel
import profile_inquiry


class ProfileDeviceInquiryTests(unittest.TestCase):
    def _store(self, path, **profile):
        base = {"id": "p1", "title": "Test", "national": "0012345678",
                "birth": "1360/01/01", "mobile": "09120000000",
                "imeis": ["350358333315545", "350358333315552"]}
        base.update(profile)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "p1", "profiles": [base]}, handle)

    def _paths(self, tmp):
        path = os.path.join(tmp, "profiles.json")
        return path, patch.object(panel, "PROFILES_PATH", path), \
            patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
            patch.object(panel, "_STORE_LOCK_PATH", path + ".lock")

    def test_record_is_bound_to_explicit_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path)
            with p1, p2, p3:
                self.assertTrue(panel.record_profile_device_inquiry("p1", "hamta", [
                    {"serial": "350358333315545", "status": "good",
                     "result": "قابل ثبت", "checked_at": "2026-08-17 10:00:00"}]))
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["hamta_inquiry_status"], "complete")
                self.assertEqual(profile["hamta_inquiry_results"][0]["serial"], "350358333315545")

    def test_start_validates_fields_and_builds_exact_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path)
            process = Mock(pid=4321)
            with p1, p2, p3, patch.object(panel.subprocess, "Popen", return_value=process) as popen:
                status, payload = panel.start_profile_device_inquiry("p1", "all")
                self.assertEqual(status, 200)
                command = popen.call_args.args[0]
                self.assertIn("robot_launcher.py", command[1])
                self.assertIn("profile_inquiry.py", command[2])
                self.assertEqual(command[-4:], ["--profile-id", "p1", "--service", "all"])
                profile = panel.load_store()["profiles"][0]
                self.assertEqual(profile["gomrok_inquiry_status"], "running")
                self.assertEqual(profile["hamta_inquiry_status"], "running")

    def test_gomrok_requires_identity_but_hamta_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, p1, p2, p3 = self._paths(tmp)
            self._store(path, national="", birth="")
            with p1, p2, p3, patch.object(panel.subprocess, "Popen", return_value=Mock(pid=9)):
                self.assertEqual(panel.start_profile_device_inquiry("p1", "gomrok")[0], 422)
                self.assertEqual(panel.start_profile_device_inquiry("p1", "hamta")[0], 200)

    def test_temporary_store_uses_profile_serials(self):
        profile = {"id": "p1", "title": "A", "national": "0012345678",
                   "birth": "1360/01/01", "mobile": "09120000000",
                   "imeis": ["350358333315545", "350358333315552"]}
        data = profile_inquiry._store(profile)
        # One device, dual-SIM: serial1=first IMEI, serial2=second IMEI
        self.assertEqual(len(data["devices"]), 1)
        self.assertEqual(data["devices"][0]["serial1"], "350358333315545")
        self.assertEqual(data["devices"][0]["serial2"], "350358333315552")
        self.assertEqual(data["person"]["national"], profile["national"])

    def test_rendered_page_has_three_modes_results_and_endpoint(self):
        html = panel.render_page().decode("utf-8")
        self.assertIn("استعلام همتا", html)
        self.assertIn("استعلام گمرک", html)
        self.assertIn("استعلام گمرک و همتا", html)
        self.assertIn("buildDeviceInquiryPanel", html)
        self.assertIn("/profile-device-inquiry", html)
        self.assertIn("device-result-text", html)


if __name__ == "__main__":
    unittest.main()
