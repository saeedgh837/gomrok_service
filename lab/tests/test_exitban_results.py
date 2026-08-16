import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import exitban
import panel


class ExitbanResultTests(unittest.TestCase):
    def _paths(self, tmp):
        return patch.multiple(
            panel,
            PROFILES_PATH=os.path.join(tmp, "profiles.json"),
            INPUTS_PATH=os.path.join(tmp, "inputs.json"),
            _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock"),
        )

    def _seed(self, tmp):
        with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump({"active_id": "p1", "profiles": [{"id": "p1", "title": "A"}]}, f)

    def test_new_profile_has_not_checked_exitban_result(self):
        profile = panel._clean_profile({"id": "p1"})
        self.assertEqual(profile["exitban_status"], "not_checked")
        self.assertEqual(profile["exitban_result"], "")
        self.assertEqual(profile["exitban_checked_at"], "")

    def test_clear_result_is_classified_and_persisted(self):
        text = "شخص با کد ملی ثبت‌شده ممنوع‌الخروج نمی‌باشد."
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(tmp)
            result = panel.record_exitban_result("p1", text)
            profile = panel.load_store()["profiles"][0]
        self.assertTrue(result)
        self.assertEqual(profile["exitban_status"], "clear")
        self.assertEqual(profile["exitban_result"], text)
        self.assertTrue(profile["exitban_checked_at"])

    def test_blocked_result_is_classified_and_persisted(self):
        text = "شخص با کد ملی ثبت‌شده ممنوع‌الخروج می‌باشد."
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(tmp)
            panel.record_exitban_result("p1", text)
            profile = panel.load_store()["profiles"][0]
        self.assertEqual(profile["exitban_status"], "blocked")

    def test_identity_robot_can_report_result_through_exitban_module(self):
        with patch.object(exitban.panel, "record_exitban_result", return_value=True) as record:
            self.assertTrue(exitban.report_result("p1", "نتیجه"))
        record.assert_called_once_with("p1", "نتیجه", "")

    def test_official_clear_message_is_sanitized_and_classified(self):
        text, status = exitban.parse_result_text(
            "استعلام زیر صرفا مربوط به پرونده‌ها است\n"
            "شخص با کدملی «0012345678» ممنوع الخروج نمی باشد\nبستن"
        )
        self.assertEqual(text, "شخص با کد ملی ثبت‌شده ممنوع‌الخروج نمی‌باشد.")
        self.assertEqual(status, "clear")
        self.assertNotIn("0012345678", text)

    def test_official_blocked_message_is_sanitized_and_classified(self):
        text, status = exitban.parse_result_text(
            "شخص با کدملی «0012345678» ممنوع الخروج می باشد"
        )
        self.assertEqual(text, "شخص با کد ملی ثبت‌شده ممنوع‌الخروج می‌باشد.")
        self.assertEqual(status, "blocked")
        self.assertNotIn("0012345678", text)

    def test_panel_starts_exitban_with_submit_enabled(self):
        with patch.object(panel.subprocess, "Popen") as popen:
            popen.return_value.pid = 1234
            panel.start_exitban_process("p1")
        command = popen.call_args.args[0]
        self.assertIn("--profile-id", command)
        self.assertIn("p1", command)
        self.assertIn("--submit", command)
        self.assertIsNot(popen.call_args.kwargs.get("stdout"), panel.subprocess.DEVNULL)
        self.assertNotEqual(popen.call_args.kwargs.get("stderr"), panel.subprocess.STDOUT)

    def test_exitban_failures_are_persisted_without_sensitive_values(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed(tmp)
            error_path = os.path.join(tmp, "exitban-errors.jsonl")
            with patch.object(panel, "EXITBAN_ERRORS_PATH", error_path):
                panel.record_exitban_error("p1", "TimeoutException: national=0012345678")
            with open(error_path, encoding="utf-8") as f:
                row = json.loads(f.read())
        self.assertEqual(row["profile_id"], "p1")
        self.assertNotIn("0012345678", row["message"])

    def test_submit_flow_reports_normalized_result(self):
        identity = {key: "value" for key in exitban.IDENTITY_FIELDS}
        driver = MagicMock()
        with patch.object(exitban, "load_identity", return_value=identity), \
                patch.object(exitban, "_build_driver", return_value=driver), \
                patch.object(exitban, "_fill_and_verify"), \
                patch("selenium.webdriver.support.ui.WebDriverWait") as wait, \
                patch.object(exitban, "wait_for_result", return_value=(exitban.CLEAR_RESULT_TEXT, "clear")), \
                patch.object(exitban, "report_result", return_value=True) as report, \
                patch.object(exitban.panel, "load_store", return_value={"active_id": "p1"}):
            result = exitban.run("p1", submit=True)
        self.assertEqual(result["status"], "clear")
        report.assert_called_once_with("p1", exitban.CLEAR_RESULT_TEXT, "clear")

    def test_profile_inquiry_renders_result_card_above_button_and_refreshes(self):
        source = panel.PAGE_JS
        view_start = source.index("'<div id=\"view-profile-inquiries\"")
        view_end = source.index("'</div>'+", view_start)
        view = source[view_start:view_end]
        self.assertLess(view.index("buildExitbanResult(p)"), view.index("استعلام آنلاین خروج از کشور"))
        for marker in (
            "exitban-result-card", "exitban-result-card.clear", "exitban-result-card.blocked",
            "هنوز استعلامی انجام نشده است", "refreshExitbanResult", "/exitban-result",
        ):
            haystack = panel.PAGE_HEAD + source if marker.startswith("exitban-result-card.") else source
            if marker == "/exitban-result":
                haystack = panel.Handler.do_POST.__code__.co_consts
            self.assertIn(marker, haystack)
        self.assertIn("Date.now()-startedAt<130000", source)


if __name__ == "__main__":
    unittest.main()
