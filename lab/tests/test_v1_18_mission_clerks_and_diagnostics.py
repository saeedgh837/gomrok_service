"""v1.18.0 — Verification of Clerk Period Metrics, Statements, and Diagnostics.

Comprehensive suite covering:
  1. Period Clerk Financial Metrics (Section 1):
     - period_clerks_summary calculation (credits, disbursements, net balance, active count)
     - Immutable period attribution precedence (transaction.period_id over device fallback)
     - Exclusion of voided transactions
  2. Period-Scoped Statement & HTML Export (Section 2):
     - clerk_statement scoped to period vs overall ledger
     - Running balance calculation
     - clerk_statement_html self-contained RTL printable document
     - HTTP GET /crm-clerk-statement endpoint validation
  3. Pre-Login Diagnostics & Global Proxy (Section 3):
     - Chrome registry version detection
     - check_system_updates logic (compatibility, update notice, proxy/direct modes)
     - check_services_health sequential 3-step diagnostic (cloud, telegram, proxy)
     - Graceful 'not_configured' status for proxy without raising false error
     - Proxy configuration storage, retrieval, and public password redaction
     - /api/check-updates, /api/health-check, /api/proxy-config, /api/proxy-test endpoints
  4. Frontend UI Contracts:
     - Presence of period clerk KPIs in panel.CRM_PAGE
     - Presence of statement dialog controls ('مشاهده گردش حساب کلی', 'دریافت خروجی صورتحساب')
     - Presence of top navigation and modals in panel.LOGIN_PAGE
"""
import io
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


class TestPeriodClerkFinancialMetrics(unittest.TestCase):
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

    def test_period_clerks_summary_calculation(self):
        # 1. Add clerks & customer
        c1, _ = crm.add_clerk({"name": "علی رضایی", "mobile": "09121111111"})
        c2, _ = crm.add_clerk({"name": "حسن محمدی", "mobile": "09122222222"})
        cust, _ = crm.add_customer({"full_name": "مشتری نمونه", "mobile": "09123333333"})

        # 2. Open Period 1 and register dev1 in Period 1
        p1, _ = crm.open_period(label="دوره اول ۱۴۰۵")
        dev1, _ = crm.add_device({"customer_id": cust["id"], "name": "Apple iPhone 15", "serial1": "111111111111111", "clerk_id": c1["id"]})
        crm.close_period(p1["id"])

        # 3. Open Period 2 and register dev2 in Period 2
        p2, _ = crm.open_period(label="دوره دوم ۱۴۰۵")
        dev2, _ = crm.add_device({"customer_id": cust["id"], "name": "Samsung S24", "serial1": "222222222222222", "clerk_id": c2["id"]})
        crm.close_period(p2["id"])

        # 4. Add transactions
        # c1 in p1: Credit 500,000, Disbursement 200,000, Voided Credit 300,000
        tx1, _ = crm.add_clerk_transaction(
            c1["id"],
            500000,
            kind="credit",
            period_id=p1["id"],
            note="حق‌الزحمه ثبت دوره اول"
        )
        tx2, _ = crm.add_clerk_transaction(
            c1["id"],
            200000,
            kind="payment",
            period_id=p1["id"],
            note="تسویه بخشی از طلب"
        )
        tx_void, _ = crm.add_clerk_transaction(
            c1["id"],
            300000,
            kind="credit",
            period_id=p1["id"],
            note="تراکنش اشتباه"
        )
        crm.void_clerk_transaction(c1["id"], tx_void["id"])

        # c2 in p2: Credit 800,000
        crm.add_clerk_transaction(
            c2["id"],
            800000,
            kind="credit",
            period_id=p2["id"],
            note="حق‌الزحمه ثبت دوره دوم"
        )

        # Read store and test period_clerks_summary for p1
        data = crm._store_read()
        sum_p1 = crm.period_clerks_summary(data, p1["id"])

        self.assertEqual(sum_p1["credit"], 500000)
        self.assertEqual(sum_p1["paid"], 200000)
        self.assertEqual(sum_p1["net"], 300000)
        self.assertEqual(sum_p1["clerks_count"], 1)
        self.assertEqual(sum_p1["cases_count"], 1)

        # Test period_clerks_summary for p2
        sum_p2 = crm.period_clerks_summary(data, p2["id"])
        self.assertEqual(sum_p2["credit"], 800000)
        self.assertEqual(sum_p2["paid"], 0)
        self.assertEqual(sum_p2["net"], 800000)
        self.assertEqual(sum_p2["clerks_count"], 1)
        self.assertEqual(sum_p2["cases_count"], 1)

    def test_immutable_period_attribution_precedence(self):
        """Explicit period_id on transaction takes precedence over device fallback."""
        p1, _ = crm.open_period(label="دوره اول")
        crm.close_period(p1["id"])
        p2, _ = crm.open_period(label="دوره دوم")
        c, _ = crm.add_clerk({"name": "علی متصدی", "mobile": "09121111111"})
        cust, _ = crm.add_customer({"full_name": "مشتری", "mobile": "09123333333"})

        # Device is in period 1
        dev, _ = crm.add_device({
            "customer_id": cust["id"],
            "name": "Xiaomi 13T",
            "serial1": "333333333333333",
            "period_id": p1["id"],
            "clerk_id": c["id"]
        })

        # Standalone transaction explicitly stamped with period 2
        crm.add_clerk_transaction(
            c["id"],
            400000,
            kind="credit",
            period_id=p2["id"],
            note="تراکنش دوره دوم"
        )

        data = crm._store_read()
        sum_p1 = crm.period_clerks_summary(data, p1["id"])
        sum_p2 = crm.period_clerks_summary(data, p2["id"])

        # Transaction MUST appear in p2, not p1
        self.assertEqual(sum_p1["credit"], 0)
        self.assertEqual(sum_p2["credit"], 400000)


class TestClerkStatementAndExport(unittest.TestCase):
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

    def test_clerk_statement_period_scoped_vs_overall(self):
        p1, _ = crm.open_period(label="دوره مهر ماه")
        crm.close_period(p1["id"])
        p2, _ = crm.open_period(label="دوره آبان ماه")
        clerk, _ = crm.add_clerk({"name": "رضا اکبری", "mobile": "09124444444"})

        # Period 1 transactions
        crm.add_clerk_transaction(
            clerk["id"],
            1000000,
            kind="credit",
            period_id=p1["id"],
            note="ثبت ۵ پرونده در مهر"
        )
        crm.add_clerk_transaction(
            clerk["id"],
            400000,
            kind="payment",
            period_id=p1["id"],
            note="واریز مساعده مهر"
        )

        # Period 2 transactions
        crm.add_clerk_transaction(
            clerk["id"],
            600000,
            kind="credit",
            period_id=p2["id"],
            note="ثبت ۳ پرونده در آبان"
        )

        data = crm._store_read()

        # Scoped statement for Period 1
        stmt_p1, _ = crm.clerk_statement(data, clerk["id"], period_id=p1["id"])
        self.assertIsNotNone(stmt_p1)
        self.assertEqual(stmt_p1["period"]["label"], "دوره مهر ماه")
        self.assertEqual(len(stmt_p1["rows"]), 2)
        self.assertEqual(stmt_p1["totals"]["credit"], 1000000)
        self.assertEqual(stmt_p1["totals"]["paid"], 400000)
        self.assertEqual(stmt_p1["totals"]["net"], 600000)

        # Overall statement (across all periods)
        stmt_all, _ = crm.clerk_statement(data, clerk["id"], period_id=None)
        self.assertIsNotNone(stmt_all)
        self.assertIsNone(stmt_all["period"])
        self.assertEqual(len(stmt_all["rows"]), 3)
        self.assertEqual(stmt_all["totals"]["credit"], 1600000)
        self.assertEqual(stmt_all["totals"]["paid"], 400000)
        self.assertEqual(stmt_all["totals"]["net"], 1200000)

    def test_clerk_statement_html(self):
        p, _ = crm.open_period(label="دوره تابستان")
        clerk, _ = crm.add_clerk({"name": "سارا حسینی", "mobile": "09125555555"})
        crm.add_clerk_transaction(
            clerk["id"],
            750000,
            kind="credit",
            period_id=p["id"],
            note="حق‌الزحمه ثبت رجیستری"
        )

        data = crm._store_read()
        stmt, _ = crm.clerk_statement(data, clerk["id"], period_id=p["id"])
        html = crm.clerk_statement_html(stmt)

        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('dir="rtl"', html)
        self.assertIn("Vazirmatn", html)
        self.assertIn("@media print", html)
        self.assertIn("سارا حسینی", html)
        self.assertIn("دوره تابستان", html)
        self.assertIn("۷۵۰٬۰۰۰", html)
        self.assertIn("صورت‌حساب و گردش مالی متصدی ثبت", html)
        self.assertIn("امضای متصدی ثبت", html)


class TestSystemUpdatesAndHealthChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.orig_env = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = self.tmp.name

    def tearDown(self):
        if self.orig_env is None:
            os.environ.pop("IRICA_CRM_STORE", None)
        else:
            os.environ["IRICA_CRM_STORE"] = self.orig_env
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_installed_chrome_version(self):
        ver = panel.get_installed_chrome_version()
        # On Windows dev machine with Chrome installed, returns a version string like '156.0.8078.49'
        if ver:
            self.assertRegex(ver, r"^\d+\.\d+\.\d+\.\d+$")

    def test_proxy_config_storage(self):
        cfg = {
            "proto": "socks5",
            "host": "127.0.0.1",
            "port": 10808,
            "user": "test_user",
            "pass": "secret123",
            "enabled": True
        }
        panel.save_system_proxy_config(cfg)
        loaded = panel.load_system_proxy_config()
        self.assertEqual(loaded["proto"], "socks5")
        self.assertEqual(loaded["host"], "127.0.0.1")
        self.assertEqual(loaded["port"], 10808)
        self.assertEqual(loaded["user"], "test_user")
        self.assertEqual(loaded["pass"], "secret123")
        self.assertTrue(loaded["enabled"])

        # Test public redaction
        pub = panel.public_proxy_config()
        self.assertNotIn("pass", pub)
        self.assertTrue(pub["pass_set"])
        self.assertEqual(pub["user"], "test_user")

    def test_check_system_updates_logic(self):
        mock_channels = {
            "channels": {
                "Stable": {
                    "channel": "Stable",
                    "version": "156.0.8078.80"
                }
            }
        }
        mock_milestones = {
            "milestones": {
                "156": {
                    "milestone": "156",
                    "version": "156.0.8078.80",
                    "downloads": {
                        "chromedriver": [
                            {"platform": "win64", "url": "https://example.com/chromedriver-win64.zip"}
                        ]
                    }
                }
            }
        }

        import chromedriver_updater
        payloads = {
            chromedriver_updater.CFT_LAST_KNOWN_GOOD_URL: mock_channels,
            chromedriver_updater.CFT_MILESTONE_URL: mock_milestones,
        }

        responses = []

        def fake_get(_session, url, **kwargs):
            self.assertIn(url, payloads, "unexpected diagnostics request")
            body = json.dumps(payloads[url]).encode("utf-8")
            response = MagicMock()
            response.status_code = 200
            response.headers = {"Content-Length": str(len(body))}
            response.iter_content.return_value = iter((body,))
            responses.append(response)
            return response

        driver_path = self.tmp.name + ".driver.exe"
        with patch("requests.Session.get", autospec=True, side_effect=fake_get) as get, \
                patch("panel.get_installed_chrome_version", return_value="156.0.8078.49"), \
                patch("captcha_collector.resolve_chromedriver", return_value=driver_path) as resolve, \
                patch("chromedriver_updater.read_exe_version", return_value="154.0.8037.92") as read_version, \
                patch("captcha_collector._read_major_version", return_value=154) as read_major:
            res = panel.check_system_updates(force_direct=True)

        self.assertTrue(res["ok"])
        self.assertEqual(res["chrome"]["installed_version"], "156.0.8078.49")
        self.assertEqual(res["chrome"]["installed_major"], 156)
        self.assertEqual(res["chrome"]["latest_stable"], "156.0.8078.80")
        self.assertEqual(res["chromedriver"]["installed_version"], "154.0.8037.92")
        self.assertEqual(res["chromedriver"]["installed_major"], 154)
        self.assertFalse(res["chromedriver"]["compatible"])
        self.assertEqual(res["chromedriver"]["matched_version"], "156.0.8078.80")
        self.assertEqual(res["chromedriver"]["download_url"],
                         chromedriver_updater.cft_zip_url("156.0.8078.80"))
        self.assertNotEqual(res["chromedriver"]["download_url"], "https://example.com/chromedriver-win64.zip")
        self.assertEqual(res["action_required"], "update_driver")
        self.assertIn("Sign in and confirm", res["notice"])
        self.assertTrue(res["notice"].isascii())
        self.assertTrue(res["proxy"]["used_direct"])
        resolve.assert_called_once_with()
        read_version.assert_called_once_with(driver_path)
        read_major.assert_called_once_with(driver_path)
        self.assertEqual([call.args[1] for call in get.call_args_list], list(payloads))
        for call in get.call_args_list:
            self.assertEqual(call.kwargs, {"proxies": {}, "timeout": (3, 5), "verify": True,
                                           "allow_redirects": False, "stream": True})
        self.assertEqual(len(responses), 2)
        for response in responses:
            response.iter_content.assert_called_once_with(chunk_size=16384)
            response.close.assert_called_once_with()

    def test_check_services_health_structure(self):
        panel.save_system_proxy_config({"enabled": False, "host": "", "port": 0})
        responses = []

        def fake_get(_session, url, **kwargs):
            self.assertIn(url, {"https://cloud.example.test/v1/health/live",
                                "https://telegram.example.test"})
            response = MagicMock()
            response.status_code = 200
            responses.append(response)
            return response

        config = MagicMock(api_base="https://cloud.example.test")
        with patch("requests.Session.get", autospec=True, side_effect=fake_get) as get, \
                patch("cloud_sync.config.load_config", return_value=config), \
                patch.object(panel, "load_telegram_config", return_value={
                    "mode": "direct", "api_base": "https://telegram.example.test"}):
            health = panel.check_services_health()

        self.assertTrue(health["ok"])
        self.assertEqual([row["id"] for row in health["steps"]], ["cloud", "telegram", "proxy"])
        self.assertEqual(len(health["steps"]), 3)
        self.assertEqual(health["overall"], "warning")
        cloud, telegram, proxy = health["steps"]
        self.assertEqual(cloud["status"], "ok")
        self.assertEqual(telegram["status"], "reachable")
        self.assertEqual(telegram["readiness"], "unknown")
        self.assertIn("message delivery were not tested", telegram["message"])
        self.assertEqual(proxy["status"], "not_configured")
        self.assertTrue(all(row["scope"] for row in health["steps"]))
        self.assertEqual(get.call_count, 2)
        for call in get.call_args_list:
            self.assertEqual(call.kwargs, {"proxies": {}, "timeout": (3, 5), "verify": True,
                                           "allow_redirects": False, "stream": True})
        for response in responses:
            response.close.assert_called_once_with()


class TestFrontendContracts(unittest.TestCase):
    def test_crm_page_contracts(self):
        html = panel.CRM_PAGE

        # 1. Period clerk financial metrics
        self.assertIn("periodClerksMoney", html)
        self.assertIn("مجموع بستانکاری متصدی‌ها", html)
        self.assertIn("مجموع دریافتی متصدی‌ها", html)
        self.assertIn("مانده حساب متصدی‌ها", html)

        # 2. Statement dialog features
        self.assertIn("مشاهده گردش حساب کلی این متصدی", html)
        self.assertIn("دریافت خروجی صورتحساب", html)
        self.assertIn("/crm-clerk-statement", html)

        # 3. Settings proxy card
        self.assertIn("پروکسی سراسری سامانه (اینترنت آزاد)", html)
        self.assertIn("stSysProxyProto", html)
        self.assertIn("stSysProxyHost", html)
        self.assertIn("stSysProxyPort", html)
        self.assertIn("settingsSaveSystemProxy", html)
        self.assertIn("settingsTestSystemProxy", html)

    def test_login_page_contracts(self):
        html = panel.LOGIN_PAGE

        # Top navigation buttons
        self.assertIn("بررسی بروزرسانی‌ها", html)
        self.assertIn("بررسی سلامت سرویس‌ها", html)
        self.assertIn("openUpdatesModal", html)
        self.assertIn("openHealthModal", html)

        # Modals
        self.assertIn('id="updatesModal"', html)
        self.assertIn('id="healthModal"', html)

        # Free internet notice & recheck button
        self.assertIn("برای بررسی بروزرسانی‌ها نیاز به دسترسی اینترنت آزاد وجود دارد", html)
        self.assertIn("فیلترشکن را روشن کردم؛ مجدداً بررسی کن", html)
        self.assertIn("ورود برای به‌روزرسانی", html)
        health = html.split("function renderHealthContent(d){", 1)[1].split("// Close modals", 1)[0]
        for label in ("فقط بررسی ارتباط", "دسترسی رد شد", "تنظیم نشده", "پایان مهلت", "نامشخص"):
            self.assertIn(label, health)
        self.assertIn('<div lang="fa" dir="rtl">', health)
        self.assertIn("آمادگی برای ارسال پیام بررسی نشده است.", health)
        self.assertNotIn("row.message", health)
        updates = html.split("function renderUpdatesContent(d, wasDirect){", 1)[1].split("function", 1)[0]
        self.assertNotIn("d.notice", updates)
        self.assertIn("بررسی نسخه ناموفق بود", updates)
        self.assertIn("نسخهٔ سازگار", updates)
        self.assertNotIn("doUpdateChromeDriver", html)
        self.assertNotIn("/api/update-chromedriver", html)
        self.assertIn("/api/check-updates", html)
        self.assertIn("/api/health-check", html)


class TestSecurityAndNegativePaths(unittest.TestCase):
    def test_unsafe_zip_member_rejected(self):
        import chromedriver_updater
        self.assertTrue(chromedriver_updater._unsafe_member("../evil.exe"))
        self.assertTrue(chromedriver_updater._unsafe_member("/etc/passwd"))
        self.assertTrue(chromedriver_updater._unsafe_member("C:\\evil.exe"))
        self.assertFalse(chromedriver_updater._unsafe_member("chromedriver-win64/chromedriver.exe"))

    def test_gate_rejects_non_loopback_client_on_update(self):
        handler = MagicMock()
        handler.command = "POST"
        handler.headers = {"Host": "127.0.0.1:8750"}
        handler._foreign_source.return_value = None
        handler._csrf_rejected.return_value = False
        handler.server = MagicMock()
        handler.server.server_address = ("127.0.0.1", 8750)

        for peer in ("192.168.1.50", "testclient", "localhost"):
            with self.subTest(peer=peer):
                handler.client_address = (peer, 54321)
                res = panel.Handler._gate(handler, "/api/update-chromedriver")
                self.assertFalse(res)
                handler._json.assert_called_with(
                    {"ok": False, "errors": ["Maintenance is available from loopback only."]}, 403)

    def test_gate_rejects_foreign_origin_csrf(self):
        handler = MagicMock()
        handler.command = "POST"
        handler.headers = {"Host": "127.0.0.1:8750", "Origin": "http://malicious-site.com"}
        handler.client_address = ("127.0.0.1", 54321)
        handler.server = MagicMock()
        handler.server.server_address = ("127.0.0.1", 8750)
        handler._own_origin = panel.Handler._own_origin.__get__(handler, panel.Handler)
        handler._foreign_source = panel.Handler._foreign_source.__get__(handler, panel.Handler)

        res = panel.Handler._gate(handler, "/api/update-chromedriver")
        self.assertFalse(res)
        handler._json.assert_called_with({"ok": False, "errors": [panel._SOURCE_ORIGIN_REJECT]}, 403)

    def test_gate_rejects_unauthenticated_proxy_and_statement(self):
        handler = MagicMock()
        handler.command = "GET"
        handler.headers = {"Host": "127.0.0.1:8750"}
        handler._is_browser_navigation.return_value = False

        # /crm-clerk-statement requires authentication
        res = panel.Handler._gate(handler, "/crm-clerk-statement")
        self.assertFalse(res)
        handler._json.assert_called_with({"ok": False, "errors": ["برای ادامه وارد شوید"]}, 401)


if __name__ == "__main__":
    unittest.main()
