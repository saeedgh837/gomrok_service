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


if __name__ == "__main__":
    unittest.main()
