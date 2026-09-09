# -*- coding: utf-8 -*-
"""v1.1.3: captcha-exhaustion must NOT be classified as a device 'problem'.

Root cause evidence (gomrok_worker.log 2026-09-09 17:43-17:48, device
e1a4e106 / serial 354247290562049):
  * 25 retry attempts, every submit rejected with
    'کد اعتبار سنجی صحیح نمی باشد' (the site rejecting our captcha OCR).
  * estelam_gomrok mapped the leftover captcha-retryable error to
    status='problem' and logged 'نتیجه دارای اشکال — کد اعتبار سنجی ...',
    stamping the DEVICE as faulty. The device was fine (hamta 'good' at
    17:52 the same evening); the site simply kept rejecting the captcha.

Fixes under test:
  1. estelam_gomrok final classification: when the remaining error is
     retryable (captcha/transient) and the page did not advance/healthy,
     outcome status must be 'error' (device NOT evaluated) — never
     'problem'. Only genuine non-retryable site verdicts are 'problem'.
  2. gomrok.retry_imei_captcha_in_place accepts an optional on_attempt
     callback receiving (attempt, answer, conf, rejected) and aborts via
     CaptchaStalled so the inquiry robot can bail out of a hopeless
     captcha streak early (bounded telemetry + bounded runtime).
  3. Default behavior without a callback is byte-for-byte unchanged
     (the registration flow shares this loop).
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gomrok  # noqa: E402
import estelam_gomrok  # noqa: E402


CAPTCHA_ERR = ["کد اعتبار سنجی صحیح نمی باشد"]
TRANSIENT_ERR = ["خطا در دریافت پاسخ از سازمان مربوطه"]
FATAL_ERR = ["شناسه (IMEI) وارد شده قبلا در شبکه داخلی کشور ثبت و فعال شده است"]
HEALTHY_ERR = ["شناسه (IMEI) در گمرک ورودی یافت نشد"]


def _write(src_path, **_kw):
    with open(src_path, encoding="utf-8") as f:
        return f.read()


class CaptchaStallExceptionTests(unittest.TestCase):
    def test_captcha_stalled_exception_exists(self):
        self.assertTrue(hasattr(gomrok, "CaptchaStalled"))
        self.assertTrue(issubclass(gomrok.CaptchaStalled, Exception))

    def test_loop_exports_on_attempt_param(self):
        src = _write(os.path.join(ROOT, "gomrok.py"))
        self.assertIn("on_attempt=None", src)

    def test_loop_catches_captcha_stalled(self):
        src = _write(os.path.join(ROOT, "gomrok.py"))
        self.assertIn("except CaptchaStalled", src)

    def test_loop_catches_captcha_stalled_in_estelam_module_scope(self):
        # estelam_gomrok must be able to raise the shared exception type.
        self.assertTrue(hasattr(estelam_gomrok, "CaptchaStalled"))


class RetryLoopCallbackTests(unittest.TestCase):
    """Drive retry_imei_captcha_in_place with stubbed driver/selenium pieces."""

    def _run_loop(self, on_attempt, max_attempts=3):
        """Run the real loop against fakes; returns (calls, attempts)."""
        import captcha_collector as cc
        from unittest import mock

        calls = []

        driver = mock.MagicMock()
        wait = mock.MagicMock()
        solver = mock.MagicMock()

        captcha_el = mock.MagicMock()
        with mock.patch.object(driver, "find_element", return_value=captcha_el), \
             mock.patch.object(cc, "wait_for_new_captcha", return_value=True), \
             mock.patch.object(cc, "snapshot_captcha", return_value=b"ref"), \
             mock.patch.object(cc, "prepare_captcha_for_shot"), \
             mock.patch.object(cc, "is_captcha_empty", return_value=False), \
             mock.patch.object(gomrok, "type_and_verify", return_value=True):
            def fake_read_conf(driver_, captcha_, solver_, wait_):
                return "abcde", 0.93

            def fake_submit():
                pass

            def fake_read_result():
                return list(CAPTCHA_ERR), False

            errors, advanced, attempts = gomrok.retry_imei_captcha_in_place(
                driver, wait, solver, b"old", fake_submit, fake_read_result,
                max_attempts=max_attempts, retryable=gomrok.is_imei_retryable_error,
                read_conf=fake_read_conf, on_attempt=on_attempt)
        return errors, advanced, attempts

    def test_callback_receives_attempt_answer_conf_rejected(self):
        seen = []

        def on_attempt(attempt, answer, conf, rejected):
            seen.append((attempt, answer, conf, rejected))

        _errors, _advanced, attempts = self._run_loop(on_attempt)
        self.assertEqual(len(seen), attempts)
        for i, (attempt, answer, conf, rejected) in enumerate(seen, 1):
            self.assertEqual(attempt, i)
            self.assertEqual(answer, "abcde")
            self.assertEqual(conf, 0.93)
            self.assertTrue(rejected)          # site rejected the captcha

    def test_captcha_stalled_aborts_loop_early(self):
        seen = []

        def on_attempt(attempt, answer, conf, rejected):
            seen.append(attempt)
            if len(seen) >= 2:
                raise gomrok.CaptchaStalled("12 consecutive captcha rejects")

        _errors, _advanced, attempts = self._run_loop(on_attempt, max_attempts=25)
        self.assertEqual(attempts, 2)          # aborted before burning 25
        self.assertEqual(seen, [1, 2])

    def test_no_callback_default_unchanged(self):
        errors, advanced, attempts = self._run_loop(None, max_attempts=3)
        self.assertEqual(attempts, 3)
        self.assertFalse(advanced)
        self.assertEqual(errors, CAPTCHA_ERR)


class StallCounterTests(unittest.TestCase):
    """estelam_gomrok builds the aborting callback (stall limit 12)."""

    def test_make_stall_guard_exists(self):
        self.assertTrue(hasattr(estelam_gomrok, "make_stall_guard"))

    def test_guard_raises_after_limit(self):
        guard = estelam_gomrok.make_stall_guard(limit=3)
        raised = None
        for i in range(1, 6):
            try:
                guard(i, "abcde", 0.9, rejected=True)
            except gomrok.CaptchaStalled as exc:
                raised = exc
                break
        self.assertIsNotNone(raised)
        self.assertEqual(i, 3)

    def test_guard_reset_on_non_captcha_attempt(self):
        guard = estelam_gomrok.make_stall_guard(limit=3)
        guard(1, "abcde", 0.9, rejected=True)
        guard(2, "abcde", 0.9, rejected=True)
        guard(3, "abcde", 0.9, rejected=False)   # advanced/transient: counter resets
        try:
            guard(4, "abcde", 0.9, rejected=True)
            guard(5, "abcde", 0.9, rejected=True)
        except gomrok.CaptchaStalled:
            self.fail("counter should have reset after a non-rejected attempt")

    def test_guard_silent_without_rejection(self):
        guard = estelam_gomrok.make_stall_guard(limit=2)
        for i in range(1, 6):
            guard(i, "abcde", 0.9, rejected=False)


class InquiryClassifierTests(unittest.TestCase):
    """The final classification ladder in estelam_gomrok._inquire_device."""

    def _src(self):
        return _write(os.path.join(ROOT, "estelam_gomrok.py"))

    def test_uses_retryable_gate_before_problem(self):
        src = self._src()
        self.assertIn("classify_outcome", src)

    def test_classify_captcha_exhaustion_is_error(self):
        outcome = estelam_gomrok.classify_outcome(
            messages=list(CAPTCHA_ERR), advanced=False, attempts=25)
        self.assertEqual(outcome["status"], "error")
        self.assertIn("کپچا", outcome["result"])
        self.assertIn("25", outcome["result"])
        self.assertNotIn("دارای اشکال", outcome["result"])

    def test_classify_transient_exhaustion_is_error(self):
        outcome = estelam_gomrok.classify_outcome(
            messages=list(TRANSIENT_ERR), advanced=False, attempts=25)
        self.assertEqual(outcome["status"], "error")

    def test_classify_real_verdict_is_problem(self):
        outcome = estelam_gomrok.classify_outcome(
            messages=list(FATAL_ERR), advanced=False, attempts=1)
        self.assertEqual(outcome["status"], "problem")
        self.assertEqual(outcome["result"], " | ".join(FATAL_ERR))

    def test_classify_healthy_stays_healthy(self):
        outcome = estelam_gomrok.classify_outcome(
            messages=list(HEALTHY_ERR), advanced=False, attempts=1)
        self.assertEqual(outcome["status"], "healthy")

    def test_classify_advanced_stays_advanced(self):
        outcome = estelam_gomrok.classify_outcome(
            messages=list(CAPTCHA_ERR), advanced=True, attempts=2)
        self.assertEqual(outcome["status"], "advanced")


if __name__ == "__main__":
    unittest.main()
