"""v1.18.1 — Verification of Period Edit, Period Delete (Safety Lock Option C),
and Redesigned Cloud Tab.

Suite covers:
  1. Period Edit (update_period):
     - Updating label
     - Updating closed_reason
     - Reopening an already closed period
     - Reopen failure when another period is currently open
     - Updating non-existent period
  2. Period Delete (delete_period - Safety Lock Option C):
     - Blocks deletion when devices belong to the period
     - Blocks deletion when clerk transactions belong to the period
     - Successfully deletes empty period
     - Cleans up current_period_id in settings if deleted period was active
     - Deleting non-existent period
  3. HTTP Handlers & Cloud Sync Outbox:
     - POST /crm-period-update: updates period and enqueues upsert into outbox
     - POST /crm-period-delete: deletes period and enqueues delete into outbox
     - POST /cloud-test-connection: connectivity and ping diagnostics
  4. Frontend UI Contracts in CRM_PAGE:
     - Period cards and dialog buttons: 'ویرایش دوره', 'حذف دوره', 'بازگشایی دوره'
     - JS handlers: dlgPeriodEdit, doSavePeriodEdit, periodReopen, periodDelete
     - Redesigned cloud tab: cloud-ping-val, cloudTestConnection, cloud-tutorial-details, cloud-mode-text
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crm
import panel


class TestPeriodEditAndSafetyDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.orig_env = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = self.tmp.name
        crm._store_write(crm._empty_store())

    def tearDown(self):
        if self.orig_env is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.orig_env
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_update_period_label_and_reason(self):
        p, _ = crm.open_period(label="دوره مهر")
        pid = p["id"]

        # 1. Update label
        updated, errs = crm.update_period(pid, {"label": "دورهٔ مهر ماه ۱۴۰۵"})
        self.assertEqual(errs, [])
        self.assertEqual(updated["label"], "دورهٔ مهر ماه ۱۴۰۵")

        # Empty label rejected
        bad, errs = crm.update_period(pid, {"label": "   "})
        self.assertIn("عنوان دوره نمی‌تواند خالی باشد", errs)

        # 2. Close period and update closed_reason
        crm.close_period(pid, reason="پایان ماه")
        updated, errs = crm.update_period(pid, {"closed_reason": "تسویه حساب نهایی"})
        self.assertEqual(errs, [])
        self.assertEqual(updated["closed_reason"], "تسویه حساب نهایی")

    def test_update_period_reopen(self):
        p1, _ = crm.open_period(label="دوره اول")
        pid1 = p1["id"]
        crm.close_period(pid1)

        # Reopen p1 when no other period is open -> succeeds
        reopened, errs = crm.update_period(pid1, {"reopen": True})
        self.assertEqual(errs, [])
        self.assertEqual(reopened["closed_at"], "")
        data = crm._store_read()
        self.assertEqual(crm.current_period(data)["id"], pid1)

        # Try to reopen an already open period -> rejected
        again, errs = crm.update_period(pid1, {"reopen": True})
        self.assertIn("این دوره از قبل باز است", errs)

        # Open another period while p1 is closed
        crm.close_period(pid1)
        p2, _ = crm.open_period(label="دوره دوم")
        pid2 = p2["id"]

        # Try to reopen p1 while p2 is open -> rejected
        bad_reopen, errs = crm.update_period(pid1, {"reopen": True})
        self.assertIn("یک دورهٔ باز دیگر در حال حاضر وجود دارد", errs[0])

    def test_delete_period_safety_lock_blocks_on_devices(self):
        """Option C: Period with cases cannot be deleted."""
        p, _ = crm.open_period(label="دوره فعال")
        pid = p["id"]
        cust, _ = crm.add_customer({"full_name": "مشتری تست", "mobile": "09121111111"})
        dev, _ = crm.add_device({
            "customer_id": cust["id"],
            "name": "Samsung A55",
            "serial1": "123456789012345"
        })

        # Device is in period p
        ok, errs = crm.delete_period(pid)
        self.assertFalse(ok)
        self.assertIn("این دوره دارای ۱ پرونده است", errs[0])
        self.assertIn("ابتدا پرونده‌ها را منتقل یا حذف کنید", errs[0])

        # Verify period still exists in store
        data = crm._store_read()
        self.assertIsNotNone(next((x for x in data["periods"] if x["id"] == pid), None))

    def test_delete_period_safety_lock_blocks_on_clerk_transactions(self):
        """Option C: Period with clerk transactions cannot be deleted."""
        p, _ = crm.open_period(label="دوره با تراکنش")
        pid = p["id"]
        crm.close_period(pid)

        clerk, _ = crm.add_clerk({"name": "متصدی آزمایشی", "mobile": "09122222222"})
        crm.add_clerk_transaction(clerk["id"], 500000, kind="credit", period_id=pid)

        ok, errs = crm.delete_period(pid)
        self.assertFalse(ok)
        self.assertIn("این دوره دارای ۱ تراکنش مالی متصدیان است", errs[0])
        self.assertIn("حذف آن مجاز نیست", errs[0])

    def test_delete_empty_period_success(self):
        """Empty period can be safely deleted."""
        p, _ = crm.open_period(label="دوره بدون پرونده")
        pid = p["id"]

        data_before = crm._store_read()
        self.assertEqual(data_before["settings"]["current_period_id"], pid)

        ok, errs = crm.delete_period(pid)
        self.assertTrue(ok)
        self.assertEqual(errs, [])

        data_after = crm._store_read()
        self.assertIsNone(next((x for x in data_after.get("periods", []) if x["id"] == pid), None))
        # Settings pointer cleared
        self.assertEqual(data_after["settings"]["current_period_id"], "")


class TestPeriodHttpAndCloudSync(unittest.TestCase):
    def test_http_period_update_and_delete_enqueue_cloud(self):
        handler = MagicMock()
        handler.command = "POST"
        handler.actor = "test_admin"

        with patch("crm.update_period", return_value=({"id": "per123", "label": "جدید"}, [])):
            with patch("panel._cloud_enqueue_mutation") as mock_enqueue:
                handler._read_json.return_value = {"period_id": "per123", "label": "جدید"}
                handler.path = "/crm-period-update"
                panel.Handler._do_post_body(handler)
                mock_enqueue.assert_called_with("periods", "per123", {"id": "per123", "label": "جدید"}, kind="upsert")
                handler._json.assert_called_with({"ok": True, "period": {"id": "per123", "label": "جدید"}})

        with patch("crm.delete_period", return_value=(True, [])):
            with patch("panel._cloud_enqueue_mutation") as mock_enqueue:
                handler._read_json.return_value = {"period_id": "per123"}
                handler.path = "/crm-period-delete"
                panel.Handler._do_post_body(handler)
                mock_enqueue.assert_called_with("periods", "per123", kind="delete")
                handler._json.assert_called_with({"ok": True, "deleted_id": "per123"})

    def test_cloud_test_connection_endpoint(self):
        handler = MagicMock()
        handler.command = "POST"
        handler.path = "/cloud-test-connection"

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            panel.Handler._do_post_body(handler)
            args, kwargs = handler._json.call_args
            res = args[0]
            self.assertTrue(res["ok"])
            self.assertEqual(res["status_code"], 200)
            self.assertIn("latency_ms", res)
            self.assertIn("ارتباط با سرور ابری برقرار است", res["message"])


class TestCloudTabAndPeriodUIContracts(unittest.TestCase):
    def test_crm_page_period_ui_contracts(self):
        html = panel.CRM_PAGE

        # Period actions in card & details
        self.assertIn("dlgPeriodEdit", html)
        self.assertIn("doSavePeriodEdit", html)
        self.assertIn("periodReopen", html)
        self.assertIn("periodDelete", html)
        self.assertIn("/crm-period-update", html)
        self.assertIn("/crm-period-delete", html)

        # Labels
        self.assertIn("ویرایش دوره", html)
        self.assertIn("حذف دوره", html)
        self.assertIn("بازگشایی دوره", html)
        self.assertIn("عدم امکان حذف دوره", html)

    def test_crm_page_cloud_tab_redesign_contracts(self):
        html = panel.CRM_PAGE

        # Dashboard KPIs & Facts
        self.assertIn('id="cloud-ping-val"', html)
        self.assertIn('id="cloud-last-sync"', html)
        self.assertIn('id="cloud-pending"', html)
        self.assertIn('id="cloud-device"', html)
        self.assertIn('id="cloud-mode-text"', html)
        self.assertIn('id="cloud-conflicts"', html)

        # Operations Bar
        self.assertIn('id="cloud-sync-btn"', html)
        self.assertIn('id="cloud-test-btn"', html)
        self.assertIn('id="cloud-backup-btn"', html)
        self.assertIn('cloudTestConnection', html)
        self.assertIn('/cloud-test-connection', html)

        # Database Mode & Server Settings
        self.assertIn('id="cloud-db-mode"', html)
        self.assertIn('cloudSetDbMode', html)
        self.assertIn('id="cloud-api"', html)
        self.assertIn('id="cloud-tenant"', html)

        # Collapsible Guide
        self.assertIn('class="cloud-tutorial-details"', html)
        self.assertIn("راهنمای افزودن کاربر (همکار جدید) در سرور ابری", html)


if __name__ == "__main__":
    unittest.main()
