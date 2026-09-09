"""v1.1.4 — parallel estelam runs + CRM UX batch.

- runs[] registry + concurrency cap (ESTELAM_MAX_CONCURRENT)
- crm-state exposes estelam_runs
- CRM page: stage-guide modal, ledger detail modal, periodic add-devices modal,
  tab-count refresh fix, per-device periodic toggle, transaction detail icon
- registry-fee note carries the device name (crm.py)
- person identity handoff to the robot child via IRICA_PERSON_* env vars
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import panel  # noqa: E402


class CapConstantsTests(unittest.TestCase):
    def test_cap_defined(self):
        self.assertGreaterEqual(panel.ESTELAM_MAX_CONCURRENT, 2)

    def test_live_runs_prunes_dead_pids(self):
        with tempfile.TemporaryDirectory() as d:
            old = panel.ESTELAM_PATH
            panel.ESTELAM_PATH = os.path.join(d, "s.json")
            try:
                panel.estelam_save({"person": {}, "devices": [], "runs": [
                    {"pid": 99999999, "device_id": "d1", "service": "gomrok",
                     "started": ""},
                    {"pid": 99999998, "device_id": "d2", "service": "hamta",
                     "started": ""},
                ]})
                # both pids are dead -> registry prunes to empty
                self.assertEqual(panel.estelam_live_runs(), [])
            finally:
                panel.ESTELAM_PATH = old


class FeeNoteTests(unittest.TestCase):
    """Registry-fee transaction note carries the device name."""

    def setUp(self):
        import crm as crm_mod
        self.crm = crm_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = os.path.join(self.tmp.name, "crm_store.json")
        self._p = patch.dict(os.environ, {"IRICA_CRM_STORE": self.store})
        self._p.start()
        self.addCleanup(self._p.stop)
        self.crm.reset_cache()
        self.addCleanup(self.crm.reset_cache)

    def test_registry_fee_note_contains_device_name(self):
        cust, errs = self.crm.add_customer({"full_name": "رضا محمدی",
                                            "mobile": "09121234567"})
        self.assertEqual(errs, [])
        rec, errs = self.crm.add_device({"name": "آیفون ۱۳ پرو",
                                         "serial1": "356938035643809",
                                         "customer_id": cust["id"]})
        self.assertEqual(errs, [])
        data = self.crm._store_read()
        customer = next(c for c in data["customers"]
                        if c["id"] == cust["id"])
        fee_tx = [tx for tx in customer["transactions"]
                  if tx.get("kind") == "base_fee"]
        self.assertTrue(fee_tx, "registry fee transaction missing")
        note = fee_tx[0]["note"]
        self.assertIn("هزینهٔ ثبت رجیستری", note)
        self.assertIn("آیفون ۱۳ پرو", note)


class CrmPageMarkupTests(unittest.TestCase):
    """CRM page contains the new v1.1.4 UI hooks."""

    def setUp(self):
        self.page = panel.CRM_PAGE

    def test_stage_guide_modal_exists(self):
        self.assertIn("stageGuide", self.page)
        self.assertIn("STAGE_GUIDE", self.page)
        self.assertIn("stageGuideModal(", self.page)

    def test_periodic_add_devices_modal_exists(self):
        self.assertIn("periodicAddModal", self.page)
        self.assertIn("paAddSelected", self.page)
        self.assertIn("PA_PAGE", self.page)
        self.assertIn("+ افزودن پرونده", self.page)

    def test_ledger_detail_modal_exists(self):
        self.assertIn("txDetail", self.page)
        self.assertIn("ICONS.info", self.page)

    def test_device_card_periodic_toggle(self):
        self.assertIn("periodicReAdd(", self.page)
        self.assertIn("periodicRemove(", self.page)

    def test_refresh_rerenders_tabs(self):
        # bugfix: tab counts stayed 0 until a tab was clicked
        self.assertIn("renderTabs();\n  renderView();", self.page)


class CrmStateRunsTests(unittest.TestCase):
    def test_crm_state_payload_includes_runs(self):
        live = [{"pid": 11, "device_id": "d1", "service": "gomrok",
                 "started": "x"}]
        with patch.object(panel, "estelam_live_runs", return_value=live), \
                patch.object(panel, "estelam_load",
                             return_value={"person": {}, "devices": []}):
            payload = panel.crm_state_payload()
        self.assertEqual(payload["estelam_runs"], live)
        self.assertTrue(payload["estelam_running"])


class IdentityHandoffTests(unittest.TestCase):
    """Person identity travels via child env (parallel-start race fix)."""

    def test_start_estelam_process_injects_person_env(self):
        captured = {}

        def fake_popen(command, cwd=None, env=None, stdin=None,
                       stdout=None, stderr=None):
            captured["env"] = env
            captured["command"] = command

            class P:
                pid = 424242

                def poll(self):
                    return 0
            return P()

        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "robot.log")
            open(log, "w").close()
            with patch.object(panel, "ROBOT_LOG_PATH", log), \
                    patch.object(panel.subprocess, "Popen", fake_popen):
                panel.start_estelam_process(device_id="d1", service="gomrok",
                                            person={"national": "0012345678",
                                                    "birth": "1375/04/29",
                                                    "mobile": "09121234567"})
        env = captured["env"]
        self.assertEqual(env.get("IRICA_PERSON_NATIONAL"), "0012345678")
        self.assertEqual(env.get("IRICA_PERSON_BIRTH"), "1375/04/29")
        self.assertEqual(env.get("IRICA_PERSON_MOBILE"), "09121234567")

    def test_robot_prefers_env_identity(self):
        src = open(os.path.join(ROOT, "estelam_gomrok.py"), encoding="utf-8").read()
        self.assertIn('os.environ.get("IRICA_PERSON_"', src)
        self.assertIn("stored_person", src)


if __name__ == "__main__":
    unittest.main()
