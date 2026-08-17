"""Durability tests for the profile store (panel.save_store / panel.load_store).

Root cause covered: on loaded machines a non-atomic write left profiles.json
empty/truncated, and load_store() then silently fell back to a single blank
profile and OVERWROTE the file -> every profile disappeared from the panel.
"""
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import panel


class StoreDurabilityTests(unittest.TestCase):
    def _paths(self, tmp):
        return patch.multiple(
            panel,
            PROFILES_PATH=os.path.join(tmp, "profiles.json"),
            INPUTS_PATH=os.path.join(tmp, "inputs.json"),
            _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock"),
        )

    def _seed(self, count=3):
        profiles = [{"id": f"p{i}", "title": f"T{i}", "national": f"00000000{i}"}
                    for i in range(1, count + 1)]
        with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump({"active_id": "p1", "profiles": profiles}, f)

    def setUp(self):
        panel._LAST_GOOD_STORE.clear()

    def test_write_is_atomic_no_partial_file_and_backup_kept(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(2)
            store = panel.load_store()
            store["profiles"].append(panel._clean_profile({"id": "p9", "title": "New"}))
            panel.save_store(store)
            # main file parses and holds all three
            reloaded = panel.load_store()
            self.assertEqual([p["id"] for p in reloaded["profiles"]], ["p1", "p2", "p9"])
            # a backup of the previous healthy version exists
            backup = panel.PROFILES_PATH + ".bak"
            self.assertTrue(os.path.isfile(backup))
            with open(backup, encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["profiles"]), 2)
            # no stray temp files left behind
            leftovers = [n for n in os.listdir(tmp) if n.startswith(".profiles-")]
            self.assertEqual(leftovers, [])

    def test_truncated_file_recovers_from_backup_and_does_not_wipe(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(3)
            panel.save_store(panel.load_store())   # creates .bak with the 3 profiles
            panel._LAST_GOOD_STORE.clear()         # simulate a fresh process
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                f.write('{"profiles": [{"id": "p1",')   # truncated write
            recovered = panel.load_store()
            self.assertEqual(len(recovered["profiles"]), 3)
            # the corrupt file must NOT have been replaced by a blank profile
            with open(panel.PROFILES_PATH, encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"profiles": [{"id": "p1",')

    def test_empty_file_without_backup_raises_instead_of_wiping(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                f.write("")
            with self.assertRaises(panel.StoreUnreadableError):
                panel.load_store()
            # file untouched: no blank-profile rewrite
            self.assertEqual(os.path.getsize(panel.PROFILES_PATH), 0)

    def test_corrupt_file_falls_back_to_process_cache(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(2)
            panel.load_store()                     # populates the cache
            os.remove(panel.PROFILES_PATH + ".bak") if os.path.exists(
                panel.PROFILES_PATH + ".bak") else None
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                f.write("not json at all")
            cached = panel.load_store()
            self.assertEqual([p["id"] for p in cached["profiles"]], ["p1", "p2"])

    def test_missing_file_still_migrates_from_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            with open(panel.INPUTS_PATH, "w", encoding="utf-8") as f:
                json.dump({"title": "Legacy", "national": "0012345678"}, f)
            store = panel.load_store()
            self.assertEqual(len(store["profiles"]), 1)
            self.assertEqual(store["profiles"][0]["title"], "Legacy")
            self.assertTrue(os.path.isfile(panel.PROFILES_PATH))

    def test_concurrent_writers_never_lose_the_profile_list(self):
        """Many concurrent claim/heartbeat/save cycles keep the store intact."""
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(6)
            panel.load_store()
            errors = []

            def churn(index):
                try:
                    for _ in range(12):
                        with panel._locked_store():
                            store = panel.load_store()
                            self.assertEqual(len(store["profiles"]), 6)
                            store["profiles"][index % 6]["runtime_error"] = f"x{index}"
                            panel.save_store(store)
                except Exception as exc:            # noqa: BLE001 - surfaced below
                    errors.append(exc)

            threads = [threading.Thread(target=churn, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            final = panel.load_store()
            self.assertEqual(len(final["profiles"]), 6)
            leftovers = [n for n in os.listdir(tmp) if n.startswith(".profiles-")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
