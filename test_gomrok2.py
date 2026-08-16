import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gomrok2
import panel


class Gomrok2Tests(unittest.TestCase):
    def test_uses_site_two_login_url(self):
        self.assertEqual(gomrok2.URL, "https://epl.irica.ir/login?2")

    def test_manual_banner_has_three_phases_and_reinjects(self):
        source = gomrok2.ManualHandoff._BANNER_JS
        self.assertIn("__gomrok2_bar", source)
        self.assertIn("__gomrok2_continue", source)
        self.assertIn("__gomrok2_code", source)
        self.assertIn("renderHandoff", source)
        self.assertIn("renderCode", source)
        self.assertIn("renderDone", source)
        self.assertIn("MutationObserver", source)

    def test_wait_continue_returns_when_flag_set(self):
        # _ensure() 2 execute_script mizanad (title + banner), sepas check-e continue.
        # pas baraye bargasht dar avalin dor: [None, None, True].
        driver = Mock()
        driver.execute_script.side_effect = [None, None, True]
        handoff = gomrok2.ManualHandoff(driver, poll_interval=0)
        handoff.wait_continue()  # bayad bedun-e loop-e bi-payan bargardad

    def test_submit_one_code_reports_wrong_on_alert(self):
        driver = Mock()

        def fake(js, *a):
            if js is gomrok2._CLEAR_ALERT_JS:
                return True
            if js is gomrok2._FIRE_JS:
                return True
            if js is gomrok2._BUSY_JS:
                return False
            if js is gomrok2._READ_JS:
                return {"alert": gomrok2.BAD_CODE_TEXT, "formGone": False,
                        "sentGone": False, "url": "u"}
            return None

        driver.execute_script.side_effect = fake
        verdict, info = gomrok2._submit_one_code(driver, "1234")
        self.assertEqual(verdict, "wrong")
        self.assertIn(gomrok2.BAD_CODE_TEXT, info)

    def test_submit_one_code_reports_success_when_page_changes(self):
        driver = Mock()

        def fake(js, *a):
            if js is gomrok2._BUSY_JS:
                return False
            if js is gomrok2._READ_JS:
                return {"alert": "", "formGone": True, "sentGone": True,
                        "url": "https://epl.irica.ir/next"}
            if js is gomrok2._FIRE_JS:
                return True
            return None

        driver.execute_script.side_effect = fake
        driver.page_source = "<html>next</html>"
        with patch.object(gomrok2, "_dump_after_code"):
            verdict, info = gomrok2._submit_one_code(driver, "1234")
        self.assertEqual(verdict, "success")

    def test_fast_retry_js_is_async_and_uses_stable_wicketpaths(self):
        js = gomrok2._FAST_RETRY_JS
        # Async: resolves via the injected callback (last argument).
        self.assertIn("arguments[arguments.length-1]", js)
        self.assertIn("DONE(", js)
        # MutationObserver drives the in-browser retry loop (no Python round-trip).
        self.assertIn("MutationObserver", js)
        self.assertIn("wicket-ajax-indicator", gomrok2._BUSY_JS)
        # Stable selectors only — no volatile Wicket id25/confirmeCode literals.
        for wp in (gomrok2.WP_CODE_FIELD, gomrok2.WP_CONFIRM_BTN, gomrok2.WP_CODE_ALERT):
            self.assertIn(wp, js)
        # Success = confirm button leaves the DOM; wrong = alert text matches BAD.
        self.assertIn("formGone", js)
        self.assertIn("BAD", js)

    def test_fast_retry_js_uses_per_attempt_code_sequence(self):
        js = gomrok2._FAST_RETRY_JS
        # The loop consumes a per-attempt code array, not a single CODE constant.
        self.assertIn("CODES=arguments[0]", js)
        self.assertIn("CODES[Math.min(n-1, CODES.length-1)]", js)
        # Each attempt records which code it fired (for the report).
        self.assertIn("code:code", js)

    def test_parse_code_sequence_expands_repeat_and_plain_forms(self):
        self.assertEqual(
            gomrok2._parse_code_sequence("6323*3,6324*2"),
            ["6323", "6323", "6323", "6324", "6324"])
        self.assertEqual(
            gomrok2._parse_code_sequence("6323,6323,6323,6324,6324"),
            ["6323", "6323", "6323", "6324", "6324"])
        self.assertEqual(gomrok2._parse_code_sequence("6323 6324"), ["6323", "6324"])
        self.assertEqual(gomrok2._parse_code_sequence(""), [])

    def test_default_plan_entries_are_four_digit_codes(self):
        # The plan is baked into the file (not taken from the banner bar). Its exact
        # range may change, but every entry must be a non-empty numeric code.
        plan = gomrok2._parse_code_sequence(gomrok2.DEFAULT_CODE_PLAN)
        self.assertTrue(plan)
        self.assertTrue(all(c.isdigit() for c in plan))

    def test_keep_open_after_fail_is_ten_minutes(self):
        self.assertEqual(gomrok2.KEEP_OPEN_AFTER_FAIL_S, 600)

    @staticmethod
    def _args(**over):
        base = dict(code=None, codes=None, attempts=5, gap_ms=0,
                    diagnose=False, manual=False)
        base.update(over)
        return SimpleNamespace(**base)

    def test_panel_default_flow_is_baked_plan_not_manual(self):
        # Panel start (only --profile-id) => no code args => baked plan, FAST mode.
        mode, codes = gomrok2._choose_codeflow(self._args())
        self.assertEqual(mode, "fast")
        self.assertEqual(
            codes, gomrok2._parse_code_sequence(gomrok2.DEFAULT_CODE_PLAN))

    def test_choose_codeflow_manual_opts_into_bar_entry(self):
        mode, codes = gomrok2._choose_codeflow(self._args(manual=True))
        self.assertEqual(mode, "manual")
        self.assertEqual(codes, [])

    def test_choose_codeflow_single_code_repeats_across_attempts(self):
        mode, codes = gomrok2._choose_codeflow(self._args(code="6323", attempts=3))
        self.assertEqual(mode, "fast")
        self.assertEqual(codes, ["6323", "6323", "6323"])

    def test_choose_codeflow_diagnose_uses_first_code(self):
        mode, codes = gomrok2._choose_codeflow(
            self._args(diagnose=True, codes="6323*3,6324*2"))
        self.assertEqual(mode, "diagnose")
        self.assertEqual(codes, ["6323"])

    def test_run_codeflow_routes_fast_and_returns_outcome(self):
        with patch.object(gomrok2, "run_code_fast", return_value="failed") as fast, \
                patch.object(gomrok2, "run_code_entry") as manual:
            outcome = gomrok2._run_codeflow(Mock(), Mock(), self._args())
        self.assertEqual(outcome, "failed")
        fast.assert_called_once()
        manual.assert_not_called()
        # The baked plan reached run_code_fast unchanged.
        self.assertEqual(fast.call_args.args[2],
                         gomrok2._parse_code_sequence(gomrok2.DEFAULT_CODE_PLAN))

    def test_run_codeflow_manual_returns_none_and_calls_entry(self):
        with patch.object(gomrok2, "run_code_entry") as manual, \
                patch.object(gomrok2, "run_code_fast") as fast:
            outcome = gomrok2._run_codeflow(Mock(), Mock(), self._args(manual=True))
        self.assertIsNone(outcome)
        manual.assert_called_once()
        fast.assert_not_called()

    def test_fast_report_shows_per_attempt_code_for_sequence(self):
        res = {
            "result": "failed",
            "total": 2,
            "attempts": [
                {"n": 1, "code": "6323", "result": "wrong", "ms": 90, "t0": 100.0,
                 "t1": 190.0, "alert": gomrok2.BAD_CODE_TEXT},
                {"n": 2, "code": "6324", "result": "wrong", "ms": 88, "t0": 190.0,
                 "t1": 278.0, "alert": gomrok2.BAD_CODE_TEXT},
            ],
        }
        report = gomrok2._fast_report(["6323", "6324"], res, 0.30)
        self.assertIn("kod=6323->6324", report)
        self.assertIn("kod=6323 |", report)
        self.assertIn("kod=6324 |", report)

    def test_fast_report_orders_attempts_and_shows_timing(self):
        res = {
            "result": "failed",
            "total": 2,
            "attempts": [
                {"n": 1, "result": "wrong", "ms": 91, "t0": 100.0, "t1": 191.0,
                 "alert": gomrok2.BAD_CODE_TEXT},
                {"n": 2, "result": "wrong", "ms": 88, "t0": 191.0, "t1": 279.0,
                 "alert": gomrok2.BAD_CODE_TEXT},
            ],
        }
        report = gomrok2._fast_report("6323", res, 0.42)
        self.assertIn("natije-ye nahayi : failed", report)
        self.assertIn("pasokh=91ms", report)
        # Second attempt fired 0ms after the first settled (min-latency proof).
        self.assertIn("fasele-az-ghabli=0ms", report)

    def test_parse_code_sequence_expands_repeat_spec(self):
        # "3 bar 6323 va 2 bar 6324" -> per-attempt list.
        self.assertEqual(
            gomrok2._parse_code_sequence("6323*3,6324*2"),
            ["6323", "6323", "6323", "6324", "6324"],
        )
        # Plain comma/space list is preserved in order.
        self.assertEqual(
            gomrok2._parse_code_sequence("6323, 6324 6325"),
            ["6323", "6324", "6325"],
        )
        self.assertEqual(gomrok2._parse_code_sequence(""), [])

    def test_fast_report_shows_per_attempt_code_for_sequence(self):
        res = {
            "result": "failed",
            "total": 2,
            "attempts": [
                {"n": 1, "code": "6323", "result": "wrong", "ms": 91,
                 "t0": 100.0, "t1": 191.0, "alert": gomrok2.BAD_CODE_TEXT},
                {"n": 2, "code": "6324", "result": "wrong", "ms": 88,
                 "t0": 191.0, "t1": 279.0, "alert": gomrok2.BAD_CODE_TEXT},
            ],
        }
        report = gomrok2._fast_report(["6323", "6324"], res, 0.42)
        self.assertIn("kod=6323->6324", report)
        self.assertIn("kod=6323 | wrong", report)
        self.assertIn("kod=6324 | wrong", report)

    def test_panel_has_separate_site_two_start_endpoint(self):
        with open(panel.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertIn('elif self.path == "/start2":', source)
        self.assertIn('"gomrok2.py"', source)
        self.assertIn("request('/start2'", panel.render_site_two_page().decode("utf-8"))

    def test_start_profile2_does_not_self_mark_busy(self):
        with open(panel.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        start = source.index("def start_profile2(")
        end = source.index("def start_profile(", start)
        body = source[start:end]
        self.assertNotIn('profile["run_status"] = "busy"', body)
        self.assertIn("set_profile_runtime(profile_id, runtime[\"pid\"])", body)


if __name__ == "__main__":
    unittest.main()
