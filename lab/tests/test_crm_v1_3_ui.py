# -*- coding: utf-8 -*-
"""v1.3 CRM UI: stage-gated actions, payment robot row, continue/skip,
per-tab history aside, settings tab, quick-links box.

- Inquiry buttons (gomrok/hamta) render only in their intake stages.
- to_payment/payment rows expose the payment-robot controls (stop robot,
  browser visibility, payment link, receipt/screenshot artifacts); with
  auto_advance the robot is started by the scheduler and the card says so.
- Every stage gets a uniform «ادامه به مرحله بعد» button (disabled+dimmed
  until the stage's requirements are met) plus a stage-specific-skip with
  per-stage warning text (warn color kept).
- Each content tab carries a history aside of its own crm_log events.
- A «تنظیمات» tab after «گزارش‌ها»: theme, text size, scheduler toggle,
  quick-site buttons manager (persisted in localStorage).
- Bottom corner quick-links box with one button per site.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import crm
import panel


def crm_page_src():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


class TestStageGatedActions(unittest.TestCase):
    def test_step_tools_scoped_to_their_own_stage(self):
        src = crm_page_src()
        # v1.3.1 round 3 (operator request): manual gomrok/hamta inquiries
        # only on step 2 (inquiry), telegram re-send only on step 4 (handed),
        # periodic watch toggle only on step 5 (to_payment).
        self.assertIn("gomrokGateAttrs()+' onclick=\"devInquire(", src)
        self.assertIn("+(d.stage==='inquiry'", src)
        self.assertIn("+(d.stage==='to_payment'", src)
        self.assertIn("+(d.stage==='handed'", src)
        self.assertNotIn("INTAKE_STAGES.includes", src)
        self.assertNotIn("HANDOFF_STAGES_JS", src)

    def test_problem_stage_nextcard_offers_requeue(self):
        src = crm_page_src()
        # v1.3.1 round 3: problem's «بازگشت به صف» is the «قدم بعدی» hero
        # card, no longer a tools-group button.
        self.assertIn("}else if(st==='problem'){", src)
        self.assertIn("onclick=\"devRequeue(\\''+d.id+'\\')\"", src)
        self.assertNotIn("a.push(['requeue_inquiry'", src)

    def test_continue_button_and_gating_map(self):
        src = crm_page_src()
        self.assertIn("STAGE_CONTINUE_FA", src)  # per-stage continue labels
        self.assertIn("STAGE_READY", src)
        self.assertIn("btn-continue", src)
        self.assertIn("stageContinue", src)

    def test_skip_button_per_stage_warning(self):
        src = crm_page_src()
        self.assertIn("رد کردن این مرحله", src)
        self.assertIn("STAGE_SKIP_WARN", src)
        # handed skip keeps the passenger-bypass wording
        self.assertIn("نادیده گرفته می‌شود", src)


class TestPaymentRobotRow(unittest.TestCase):
    def test_page_markers(self):
        src = crm_page_src()
        self.assertIn("توقف ربات پرداخت", src)
        self.assertIn("نمایش مرورگر", src)
        self.assertIn("/browser-visibility", src)
        self.assertIn("لینک پرداخت", src)
        self.assertIn("payRow", src)
        self.assertIn("روبات پرداخت خودکار در جریان است", src)

    def test_state_payload_joins_profile_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles_path = os.path.join(temp_dir, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as handle:
                json.dump({"active_id": "", "profiles": [{
                    "id": "prof1", "title": "T", "enabled": False,
                    "registration_status": "pending", "run_status": "busy",
                    "browser_visible": True, "payment_link": "https://x/pay",
                    "receipt_screenshot": "r.png",
                    "payment_screenshot": ""}]}, handle)
            estelam_path = os.path.join(temp_dir, "estelam.json")
            with open(estelam_path, "w", encoding="utf-8") as handle:
                json.dump({"person": {}, "devices": []}, handle)
            os.environ["IRICA_CRM_STORE"] = os.path.join(temp_dir, "crm.json")
            crm.reset_cache()
            try:
                device, _ = crm.add_device({"name": "D",
                                            "serial1": "356938035643809"})
                data = crm.store_read()
                next(d for d in data["devices"]
                     if d["id"] == device["id"])["profile_id"] = "prof1"
                crm.store_write(data)
                with patch.object(panel, "PROFILES_PATH", profiles_path), \
                        patch.object(panel, "ESTELAM_PATH", estelam_path), \
                        patch.object(panel, "estelam_is_running",
                                     return_value=False):
                    payload = panel.crm_state_payload()
                row = payload["devices"][0]
                self.assertEqual(row["pay"]["run_status"], "busy")
                self.assertTrue(row["pay"]["browser_visible"])
                self.assertEqual(row["pay"]["payment_link"], "https://x/pay")
            finally:
                crm.reset_cache()
                os.environ.pop("IRICA_CRM_STORE", None)

    def test_missing_profile_gives_empty_pay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles_path = os.path.join(temp_dir, "profiles.json")
            with open(profiles_path, "w", encoding="utf-8") as handle:
                json.dump({"active_id": "", "profiles": []}, handle)
            estelam_path = os.path.join(temp_dir, "estelam.json")
            with open(estelam_path, "w", encoding="utf-8") as handle:
                json.dump({"person": {}, "devices": []}, handle)
            os.environ["IRICA_CRM_STORE"] = os.path.join(temp_dir, "crm.json")
            crm.reset_cache()
            try:
                device, _ = crm.add_device({"name": "D",
                                            "serial1": "356938035643809"})
                with patch.object(panel, "PROFILES_PATH", profiles_path), \
                        patch.object(panel, "ESTELAM_PATH", estelam_path), \
                        patch.object(panel, "estelam_is_running",
                                     return_value=False):
                    payload = panel.crm_state_payload()
                self.assertEqual(payload["devices"][0]["pay"]["run_status"], "")
            finally:
                crm.reset_cache()
                os.environ.pop("IRICA_CRM_STORE", None)


class TestHistoryAside(unittest.TestCase):
    def test_history_aside_and_per_tab_filter(self):
        src = crm_page_src()
        self.assertIn("crm-aside", src)
        self.assertIn("historyHtml", src)
        self.assertIn("تاریخچه", src)
        # per-tab entity keys
        self.assertIn("customer_id", src)
        self.assertIn("owner_id", src)
        self.assertIn("entity_id", src)

    def test_layout_positions_history(self):
        src = crm_page_src()
        self.assertIn("crm-cols", src)


class TestSettingsTab(unittest.TestCase):
    def test_settings_tab_after_reports(self):
        src = crm_page_src()
        reports_pos = src.index("['reports','گزارش‌ها']")
        settings_pos = src.index("['settings','تنظیمات']")
        self.assertLess(reports_pos, settings_pos)

    def test_settings_controls(self):
        src = crm_page_src()
        self.assertIn("settingsHtml", src)
        self.assertIn("crm-fs", src)          # text-size variable
        self.assertIn("data-theme", src)
        self.assertIn("CRM_LINKS_KEY", src)   # quick-site manager
        self.assertIn("scheduler_enabled", src)

    def test_quick_links_box_and_defaults(self):
        src = crm_page_src()
        self.assertIn("id=\"quickBox\"", src)
        self.assertIn("hamta.ntsw.ir", src)   # sensible default buttons
        self.assertIn("epl.irica.gov.ir", src)
        self.assertIn("irica-crm-links", src)


if __name__ == "__main__":
    unittest.main()
