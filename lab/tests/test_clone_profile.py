import json
import os
import tempfile
import unittest
from unittest.mock import patch

import panel


class CloneProfileTests(unittest.TestCase):
    def test_clone_profile_copies_input_data_with_unique_numbered_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = os.path.join(tmp, "profiles.json")
            inputs_path = os.path.join(tmp, "inputs.json")
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "Ali", "national": "0012345678",
                    "imeis": ["111111111111111"], "registration_status": "success",
                    "payment_link": "https://old", "run_status": "idle",
                }]}, f)
            with patch.object(panel, "PROFILES_PATH", profiles_path), \
                    patch.object(panel, "INPUTS_PATH", inputs_path), \
                    patch.object(panel, "_STORE_LOCK_PATH", profiles_path + ".lock"):
                cloned = panel.clone_profile("p1")
                store = panel.load_store()

            self.assertNotEqual(cloned["id"], "p1")
            self.assertEqual(cloned["title"], "Ali 2")
            self.assertEqual(cloned["national"], "0012345678")
            self.assertEqual(cloned["imeis"], ["111111111111111"])
            self.assertEqual(cloned["registration_status"], "pending")
            self.assertEqual(cloned["payment_link"], "")
            self.assertEqual(cloned["run_status"], "idle")
            self.assertEqual(store["active_id"], cloned["id"])

    def test_clone_profile_rejects_missing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{"id": "p1", "title": "Ali"}]}, f)
            with patch.object(panel, "PROFILES_PATH", path), \
                    patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
                    patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"):
                self.assertIsNone(panel.clone_profile("missing"))

    def test_cloning_a_blocked_profile_clears_the_block_and_its_reason(self):
        """A cloned profile is a fresh attempt, so it must be runnable.

        Editing a blocked profile does NOT clear the block (merge_profile_update
        preserves runtime/status fields on purpose), so the clone is the operator's
        route back to a usable profile — and a stale `runtime_error` on it would
        keep showing "غیرقابل ورود" for a profile that is actually pending.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "profiles.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": "p1", "profiles": [{
                    "id": "p1", "title": "blocked", "enabled": False,
                    "registration_status": "blocked", "run_status": "idle",
                    "runtime_error": "این پروفایل دیگر قابل ورود نیست: IMEI قبلا ثبت شده",
                    "national": "0011223344", "birth": "1375/04/29",
                    "mobile": "09120000000", "imeis": ["352506099000001"],
                }]}, f, ensure_ascii=False)
            with patch.object(panel, "PROFILES_PATH", path), \
                    patch.object(panel, "INPUTS_PATH", os.path.join(tmp, "inputs.json")), \
                    patch.object(panel, "_STORE_LOCK_PATH", path + ".lock"):
                self.assertEqual(panel.start_profile("p1")[0], 409)
                cloned = panel.clone_profile("p1")
                self.assertEqual(cloned["registration_status"], "pending")
                self.assertEqual(cloned["runtime_error"], "")
                # The block is gone, so start_profile no longer refuses with 409.
                # Spawning the real robot is out of scope here, so the process
                # launcher is stubbed; what matters is the gate, not the child.
                with patch.object(panel, "start_profile_process",
                                  return_value={"pid": 4242,
                                                "profile_id": cloned["id"]}):
                    status, _ = panel.start_profile(cloned["id"])
                self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
