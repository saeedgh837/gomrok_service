"""Phase D — scheduler, auto-advance, profile auto-create/link, migration.

- crm_scheduler_tick(): due watch devices get ONE per-device inquiry start.
- crm_auto_advance_scan(): estelam 'advanced' rows move to to_payment; when
  auto_advance is on, start_payment fires and the stage moves ONLY on 200
  (review F5); failures keep the device in to_payment with advance_attempts
  capped at 6.
- crm_ensure_registration_profile(): at handover, link or create the site-1
  profile (dedupe by serial1/owner-national — review F9).
- app_paths.MIGRATED_FILES carries the CRM data files (review F14).
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app_paths
import crm
import panel


class CrmSchedulerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estelam = os.path.join(self.tmp.name, "estelam_store.json")
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self._patches = [
            patch.object(panel, "ESTELAM_PATH", self.estelam),
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH", os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    def _device_at_handed(self, with_owner=True, auto_advance=False):
        device, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": "356938035643809",
                                       "auto_advance": auto_advance})
        assert errs == [], errs
        crm.advance(device["id"], "queue_inquiry")
        crm.advance(device["id"], "mark_ready")
        if with_owner:
            owner, errs = crm.add_owner({
                "first_name": "رضا", "last_name": "محمدی",
                "national": "0012345679", "birth": "1375/04/29",
                "mobile": "09121234567", "card_no": "6219861012345678"})
            assert errs == [], errs
            crm.advance(device["id"], "attach_owner", owner_id=owner["id"])
            crm.advance(device["id"], "handover")
        return device

    def _set_stage(self, device_id, stage):
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device_id)
        dev["stage"] = stage
        crm.store_write(data)


class TestSchedulerTick(CrmSchedulerCase):
    def test_due_handed_device_gets_inquiry(self):
        device = self._device_at_handed()
        with patch.object(panel, "crm_start_inquiry") as start, \
                patch.object(panel, "estelam_is_running", return_value=False):
            start.return_value = {"ok": True}
            result = panel.crm_scheduler_tick()
        self.assertTrue(result["ok"])
        started = [call["device_id"] for call in result["started"]]
        self.assertIn(device["id"], started)

    def test_not_due_device_skipped(self):
        device = self._device_at_handed()
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        import time as _time
        dev["last_inquiry_at"] = _time.strftime(
            "%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 30 * 60))
        crm.store_write(data)
        with patch.object(panel, "crm_start_inquiry") as start, \
                patch.object(panel, "estelam_is_running", return_value=False):
            start.return_value = {"ok": True}
            result = panel.crm_scheduler_tick()
        self.assertEqual(result["started"], [])

    def test_scheduler_disabled_is_noop(self):
        device = self._device_at_handed()
        data = crm.store_read()
        data["settings"]["scheduler_enabled"] = False
        crm.store_write(data)
        with patch.object(panel, "crm_start_inquiry") as start, \
                patch.object(panel, "estelam_is_running", return_value=False):
            result = panel.crm_scheduler_tick()
        self.assertEqual(result["started"], [])
        start.assert_not_called()

    def test_running_estelam_blocks_tick(self):
        device = self._device_at_handed()
        with patch.object(panel, "estelam_is_running", return_value=True):
            result = panel.crm_scheduler_tick()
        self.assertFalse(result["ok"])
        self.assertEqual(result["started"], [])

    def test_non_watch_stages_never_started(self):
        self._device_at_handed(with_owner=False)   # stuck at ready_owner
        with patch.object(panel, "crm_start_inquiry") as start, \
                patch.object(panel, "estelam_is_running", return_value=False):
            result = panel.crm_scheduler_tick()
        self.assertEqual(result["started"], [])


class TestAutoAdvance(CrmSchedulerCase):
    def _advanced_estelam_row(self, device):
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [{
                "id": device["id"], "name": device["name"],
                "serial1": device["serial1"], "status": "advanced",
                "result": "شناسه در گمرک ورودی ثبت است",
            }]}, handle)

    def test_advanced_row_moves_to_to_payment_without_auto_start(self):
        device = self._device_at_handed()
        self._advanced_estelam_row(device)
        with patch.object(panel, "estelam_is_running", return_value=False):
            result = panel.crm_auto_advance_scan()
        self.assertIn(device["id"], result["moved"])
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        self.assertEqual(dev["stage"], "to_payment")
        self.assertEqual(dev["advance_attempts"], 0)

    def test_auto_advance_moves_stage_only_on_200(self):
        """F5: a 409/422/500 from start_profile must NOT advance the stage."""
        device = self._device_at_handed(auto_advance=True)
        self._advanced_estelam_row(device)
        # create + link the profile so start fires
        with patch.object(panel, "crm_ensure_registration_profile") as ensure, \
                patch.object(panel, "start_profile") as startp, \
                patch.object(panel, "estelam_is_running", return_value=False):
            ensure.return_value = "prof-1"
            startp.return_value = (409, {"error": "profile is already running"})
            result = panel.crm_auto_advance_scan()
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        self.assertEqual(dev["stage"], "to_payment")
        self.assertEqual(dev["advance_attempts"], 1)

    def test_auto_advance_succeeds_on_200(self):
        device = self._device_at_handed(auto_advance=True)
        self._advanced_estelam_row(device)
        with patch.object(panel, "crm_ensure_registration_profile") as ensure, \
                patch.object(panel, "start_profile") as startp, \
                patch.object(panel, "estelam_is_running", return_value=False):
            ensure.return_value = "prof-1"
            startp.return_value = (200, {"pid": 4242})
            result = panel.crm_auto_advance_scan()
        self.assertIn(device["id"], result["started"])
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        self.assertEqual(dev["stage"], "payment")

    def test_auto_advance_gives_up_after_six_attempts(self):
        device = self._device_at_handed(auto_advance=True)
        self._advanced_estelam_row(device)
        for _ in range(8):
            with patch.object(panel, "crm_ensure_registration_profile") as ensure, \
                    patch.object(panel, "start_profile") as startp, \
                    patch.object(panel, "estelam_is_running", return_value=False):
                ensure.return_value = "prof-1"
                startp.return_value = (409, {"error": "busy"})
                panel.crm_auto_advance_scan()
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        self.assertEqual(dev["stage"], "to_payment")
        self.assertEqual(dev["advance_attempts"], 6)   # capped

    def test_no_auto_flag_never_starts_profile(self):
        device = self._device_at_handed(auto_advance=False)
        self._advanced_estelam_row(device)
        with patch.object(panel, "start_profile") as startp, \
                patch.object(panel, "estelam_is_running", return_value=False):
            result = panel.crm_auto_advance_scan()
        startp.assert_not_called()
        self.assertIn(device["id"], result["moved"])


class TestProfileEnsure(CrmSchedulerCase):
    def test_creates_profile_with_owner_and_imei(self):
        device = self._device_at_handed()   # handover happened
        profile_id = panel.crm_ensure_registration_profile(device["id"])
        self.assertTrue(profile_id)
        store = json.load(open(self.profiles, encoding="utf-8"))
        profile = next(p for p in store["profiles"] if p["id"] == profile_id)
        self.assertEqual(profile["national"], "0012345679")
        self.assertIn("356938035643809", profile["imeis"])
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        self.assertEqual(dev["profile_id"], profile_id)

    def test_links_existing_profile_by_serial(self):
        """F9: operator already registered the device manually pre-CRM."""
        existing = {"id": "p-existing", "title": "old manual",
                    "national": "0012345679", "birth": "1375/4/29",
                    "mobile": "09121234567", "imeis": ["356938035643809"],
                    "enabled": True}
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": [existing]}, handle)
        device = self._device_at_handed()
        profile_id = panel.crm_ensure_registration_profile(device["id"])
        self.assertEqual(profile_id, "p-existing")   # LINKED, not duplicated
        store = json.load(open(self.profiles, encoding="utf-8"))
        self.assertEqual(len(store["profiles"]), 1)

    def test_idempotent_second_call_returns_same_profile(self):
        device = self._device_at_handed()
        first = panel.crm_ensure_registration_profile(device["id"])
        second = panel.crm_ensure_registration_profile(device["id"])
        self.assertEqual(first, second)


class TestMigration(CrmSchedulerCase):
    def test_crm_files_registered_in_migrated_files(self):
        """F14: a release upgrade must carry CRM data across."""
        self.assertIn("crm_store.json", app_paths.MIGRATED_FILES)
        self.assertIn("crm_store.json.bak", app_paths.MIGRATED_FILES)
        self.assertIn("crm_logs.jsonl", app_paths.MIGRATED_FILES)


if __name__ == "__main__":
    unittest.main()


class TestSchedulerThreadLifecycle(unittest.TestCase):
    """Regression: main() calls crm_scheduler_start(); the definition and the
    daemon thread must really exist (v1.1.0 shipped a panel whose main()
    raised NameError on startup because the thread block was lost)."""

    def test_crm_scheduler_start_spots_daemon_thread(self):
        with patch.dict(os.environ, {"IRICA_CRM_SCHEDULER_OFF": ""}), \
                patch.object(panel, "crm_scheduler_tick", return_value={"ok": False}), \
                patch.object(panel, "crm_auto_advance_scan",
                             return_value={"ok": True, "moved": [], "started": []}):
            thread = panel.crm_scheduler_start()
            self.assertIsNotNone(thread)
            self.assertTrue(thread.is_alive())
            self.assertEqual(thread.name, "crm-scheduler")
            self.assertTrue(thread.daemon)
            stopped = panel.crm_scheduler_stop()
            self.assertIsNone(stopped)

    def test_kill_switch_blocks_start(self):
        with patch.dict(os.environ, {"IRICA_CRM_SCHEDULER_OFF": "1"}):
            self.assertIsNone(panel.crm_scheduler_start())

    def test_main_wires_scheduler_start(self):
        import inspect
        self.assertIn("crm_scheduler_start()", inspect.getsource(panel.main))
