# -*- coding: utf-8 -*-
"""Periodic customs-inquiry tab + passenger rename + status color tests.

- Devices reaching `handed` are auto-enrolled in periodic gomrok inquiry
  (scheduler already covers handed/to_payment); operator may opt a device
  out/in via /crm-periodic; opt-out devices are never auto-started.
- New CRM tab «استعلام دوره‌ای گمرک» after «استخر هویت» with immediate-run,
  interval config, per-device history, general log, remove buttons.
- UI rename: مالک -> مسافر (tab «اطلاعات مسافر»), and the handed-skip
  confirm text says the passenger-identity registration check is skipped.
- Hamta status pills get proper Persian labels + colors on device cards.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import crm
import panel


class CrmApiHarness(unittest.TestCase):
    """Boots the real handler with isolated stores (same as test_crm_api)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    def _device_at_handed(self):
        device, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": "356938035643809"})
        assert errs == [], errs
        owner, errs = crm.add_owner({
            "first_name": "رضا", "last_name": "محمدی",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "card_no": "6219861012345678"})
        assert errs == [], errs
        # Intake gates: queue the inquiry, record both green results, attach.
        crm.advance(device["id"], "queue_inquiry")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device["id"]), "status": "healthy",
                 "hamta_status": "good"}]}, handle)
        crm.advance(device["id"], "mark_ready")
        crm.advance(device["id"], "attach_owner", owner_id=owner["id"])
        device, errs = crm.advance(device["id"], "handover")
        assert errs == [], errs
        return device


class TestPeriodicToggle(CrmApiHarness):
    def test_remove_then_add_updates_opt_out(self):
        device = self._device_at_handed()
        result = panel.crm_periodic_toggle(device["id"], remove=True)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertTrue(result["device"]["periodic_opt_out"])
        result = panel.crm_periodic_toggle(device["id"], remove=False)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertFalse(result["device"].get("periodic_opt_out"))

    def test_unknown_device_is_rejected(self):
        result = panel.crm_periodic_toggle("nope", remove=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])


class TestSchedulerOptOut(unittest.TestCase):
    def test_opt_out_device_never_auto_started(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            crm_store = os.path.join(temp_dir, "crm.json")
            os.environ["IRICA_CRM_STORE"] = crm_store
            crm.reset_cache()
            try:
                device, _ = crm.add_device({"name": "D",
                                            "serial1": "356938035643809"})
                data = crm.store_read()
                dev = next(d for d in data["devices"] if d["id"] == device["id"])
                dev["stage"] = "handed"
                dev["periodic_opt_out"] = True
                crm.store_write(data)
                with patch.object(panel, "crm_start_inquiry") as start, \
                        patch.object(panel, "estelam_is_running",
                                     return_value=False):
                    result = panel.crm_scheduler_tick()
                self.assertEqual(result["started"], [])
                start.assert_not_called()
            finally:
                crm.reset_cache()
                os.environ.pop("IRICA_CRM_STORE", None)


class TestPeriodicTabPage(unittest.TestCase):
    def _page(self):
        return open(panel.__file__, encoding="utf-8").read()

    def test_tab_after_identity_pool(self):
        src = self._page()
        self.assertIn("استعلام دوره‌ای گمرک", src)
        identities_pos = src.index("['identities','استخر هویت']")
        periodic_pos = src.index("['periodic','استعلام دوره‌ای گمرک']")
        self.assertLess(identities_pos, periodic_pos)

    def test_immediate_run_and_interval_controls(self):
        src = self._page()
        self.assertIn("شروع فوری استعلام", src)
        self.assertIn("periodicInterval", src)
        self.assertIn("/crm-settings", src)

    def test_remove_and_history_buttons(self):
        src = self._page()
        self.assertIn("periodicRemove", src)
        self.assertIn("periodicHistory", src)
        self.assertIn("حذف از لیست", src)
        self.assertIn("تاریخچه", src)

    def test_general_log_box(self):
        src = self._page()
        self.assertIn("periodicLog", src)
        self.assertIn("/estelam-logs", src)

    def test_handed_skip_confirm_text(self):
        src = self._page()
        self.assertIn("نادیده گرفته می‌شود", src)
        self.assertIn("اطلاعات مسافر", src)

    def test_passenger_rename(self):
        src = self._page()
        self.assertIn("اطلاعات مسافر", src)
        self.assertNotIn("['owners','مالکان']", src)
        self.assertNotIn("سالم — نیازمند مالک", src)
        self.assertIn("سالم — نیازمند مسافر", src)

    def test_hamta_status_labels_and_colors(self):
        src = self._page()
        self.assertIn("HAMTA_PILL", src)
        self.assertIn("مجاز (قابل ثبت)", src)
        # hamta pill is no longer hardcoded neutral on device cards
        self.assertNotIn("'<span class=\"pill n\">همتا: '", src)


class TestPassengerErrorText(unittest.TestCase):
    def test_handover_error_says_passenger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["IRICA_CRM_STORE"] = os.path.join(temp_dir, "c.json")
            crm.reset_cache()
            try:
                device, _ = crm.add_device({"name": "D",
                                            "serial1": "356938035643809"})
                crm.advance(device["id"], "queue_inquiry")
                import json as _json
                est_path = os.path.join(temp_dir, "estelam.json")
                with open(est_path, "w", encoding="utf-8") as handle:
                    _json.dump({"person": {}, "devices": [
                        {"id": str(device["id"]), "status": "healthy",
                         "hamta_status": "good"}]}, handle)
                with patch.object(panel, "ESTELAM_PATH", est_path):
                    crm.advance(device["id"], "mark_ready")
                    _record, errors = crm.advance(device["id"], "handover")
                self.assertTrue(any("مسافر" in e for e in errors), errors)
            finally:
                crm.reset_cache()
                os.environ.pop("IRICA_CRM_STORE", None)


if __name__ == "__main__":
    unittest.main()
