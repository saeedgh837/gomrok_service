import inspect
import unittest
from unittest.mock import Mock, patch

import gomrok

# The exact text the customs login page shows for an IMEI that is already
# registered and active on the domestic network. Reported from the field.
ALREADY_ACTIVE = ("خطا در استعلام imei از سامانه همتا : شناسه (IMEI) وارد شده "
                  "قبلا در شبکه داخلی کشور ثبت و فعال شده و امکان ورود آن وجود ندارد")


class AlreadyActiveImeiTests(unittest.TestCase):
    """An IMEI already active on the domestic network can NEVER be entered.

    The message arrives wrapped in "خطا در استعلام imei از سامانه همتا", which is
    also how a genuine transient HAMTA inquiry failure arrives. Classifying it as
    retryable makes the robot refresh the captcha and resubmit forever
    (`max_attempts=None`), so the profile is never marked unusable and the
    operator sees an endless loop instead of a clear verdict.
    """

    def test_the_message_is_fatal(self):
        self.assertTrue(gomrok.is_imei_fatal_error([ALREADY_ACTIVE]))

    def test_the_message_is_not_retryable(self):
        self.assertFalse(gomrok.is_imei_retryable_error([ALREADY_ACTIVE]))
        self.assertFalse(gomrok.is_imei_captcha_retry_error([ALREADY_ACTIVE]))
        self.assertFalse(gomrok.is_imei_transient_error([ALREADY_ACTIVE]))

    def test_fatal_wins_when_the_site_shows_both(self):
        """A stale captcha warning must not rescue a permanently rejected IMEI."""
        both = ["کد امنیتی صحیح نمی باشد", ALREADY_ACTIVE]
        self.assertTrue(gomrok.is_imei_fatal_error(both))
        self.assertFalse(gomrok.is_imei_retryable_error(both))

    def test_a_real_hamta_inquiry_failure_is_still_retryable(self):
        """The wrapper alone, with no permanent reason, must keep retrying."""
        for text in ("خطا در استعلام IMEI از سامانه همتا",
                     "خطا در استعلام imei از سامانه همتا : خطا در دریافت پاسخ از سازمان مربوطه",
                     "خطا در استعلام imei از سامانه همتا : لطفا مجددا تلاش کنید"):
            with self.subTest(text=text):
                self.assertFalse(gomrok.is_imei_fatal_error([text]))
                self.assertTrue(gomrok.is_imei_retryable_error([text]))

    def test_a_wrong_captcha_is_still_retryable(self):
        self.assertTrue(gomrok.is_imei_retryable_error(["کد امنیتی صحیح نمی باشد"]))
        self.assertFalse(gomrok.is_imei_fatal_error(["کد امنیتی صحیح نمی باشد"]))

    def test_the_retry_loop_stops_immediately_on_this_message(self):
        """Even if entered, the loop must not spin: one attempt, then out.

        `main` drives this loop with `max_attempts=None`, so a wrong verdict here
        means an unbounded retry against the live site. The stub therefore aborts
        after a few calls rather than hanging the test run.
        """
        driver = Mock()
        driver.find_element.return_value = Mock()
        solver = Mock()
        solver.read_pil.return_value = "abcde"
        submit = Mock()

        calls = []

        def read_result():
            calls.append(1)
            if len(calls) > 3:
                raise AssertionError(
                    "retry loop kept going on a permanently rejected IMEI "
                    f"({len(calls)} submits)")
            return [ALREADY_ACTIVE], False

        with patch.object(gomrok.cc, "wait_for_new_captcha", return_value=True), \
                patch.object(gomrok.cc, "click_refresh"), \
                patch.object(gomrok.cc, "prepare_captcha_for_shot"), \
                patch.object(gomrok.cc, "is_captcha_empty", return_value=False), \
                patch.object(gomrok.cc, "crop_captcha_with_padding", return_value=Mock()), \
                patch.object(gomrok.cc, "snapshot_captcha", return_value=("s", "f")), \
                patch.object(gomrok, "type_and_verify", return_value=True):
            errors, advanced, attempts = gomrok.retry_imei_captcha_in_place(
                driver, Mock(), solver, ("s0", "f0"), submit, read_result,
                max_attempts=None, retryable=gomrok.is_imei_retryable_error)

        self.assertEqual(errors, [ALREADY_ACTIVE])
        self.assertFalse(advanced)
        self.assertEqual(attempts, 1)
        self.assertEqual(submit.call_count, 1)

    def test_main_marks_the_profile_unusable_on_a_fatal_imei_error(self):
        source = inspect.getsource(gomrok.main)
        self.assertIn("_mark_panel_profile_unusable", source)
        self.assertIn("is_imei_fatal_error", source)

    def test_the_panel_blocks_a_profile_carrying_this_error(self):
        import panel
        with patch.object(panel, "load_store", return_value={"profiles": [{
                "id": "p1", "registration_status": "blocked", "run_status": "idle",
                "runtime_error": "این پروفایل دیگر قابل ورود نیست: " + ALREADY_ACTIVE,
        }]}):
            status, payload = panel.start_profile("p1")
        self.assertEqual(status, 409)


class InlineImeiCaptchaRetryTests(unittest.TestCase):
    def test_main_uses_in_place_retry_for_captcha_and_hamta_errors(self):
        source = inspect.getsource(gomrok.main)
        self.assertIn("retry_imei_captcha_in_place", source)
        self.assertIn("is_imei_retryable_error", source)
        self.assertIn("click_refresh", source)
        self.assertIn("submitted_captcha_ref", source)

    def test_retry_classifier_handles_wrong_captcha_and_hamta_inquiry(self):
        self.assertTrue(gomrok.is_imei_captcha_retry_error([
            "کد امنیتی صحیح نمی باشد"
        ]))
        self.assertTrue(gomrok.is_imei_captcha_retry_error([
            "خطا در استعلام IMEI از سامانه همتا"
        ]))
        self.assertFalse(gomrok.is_imei_captcha_retry_error([
            "شناسه در اطلاعات دریافت شده از گمرک ورودی یافت نشد"
        ]))

    def test_retries_changed_captcha_in_same_browser_until_page_advances(self):
        driver = Mock()
        captcha = Mock()
        driver.find_element.return_value = captcha
        solver = Mock()
        solver.read_pil.side_effect = ["abcde", "fghij"]
        submit_once = Mock()
        wait_result = Mock(side_effect=[
            (["خطا در استعلام imei از سامانه همتا"], False),
            ([], True),
        ])

        with patch.object(gomrok.cc, "wait_for_new_captcha", return_value=True) as wait_new, \
                patch.object(gomrok.cc, "click_refresh") as refresh, \
                patch.object(gomrok.cc, "prepare_captcha_for_shot"), \
                patch.object(gomrok.cc, "is_captcha_empty", return_value=False), \
                patch.object(gomrok.cc, "crop_captcha_with_padding", return_value=Mock()), \
                patch.object(gomrok.cc, "snapshot_captcha", side_effect=[("s1", "f1"), ("s2", "f2")]), \
                patch.object(gomrok, "type_and_verify", return_value=True):
            errors, advanced, attempts = gomrok.retry_imei_captcha_in_place(
                driver, Mock(), solver, ("s0", "f0"), submit_once, wait_result,
                max_attempts=5,
            )

        self.assertEqual(errors, [])
        self.assertTrue(advanced)
        self.assertEqual(attempts, 2)
        self.assertEqual(submit_once.call_count, 2)
        self.assertEqual(wait_new.call_count, 2)
        refresh.assert_not_called()
        self.assertEqual(solver.read_pil.call_count, 2)

    def test_refreshes_explicitly_if_site_did_not_change_captcha(self):
        driver = Mock()
        captcha = Mock()
        driver.find_element.return_value = captcha
        solver = Mock()
        solver.read_pil.return_value = "abcde"

        with patch.object(gomrok.cc, "wait_for_new_captcha", return_value=False), \
                patch.object(gomrok.cc, "click_refresh", return_value=True) as refresh, \
                patch.object(gomrok.cc, "prepare_captcha_for_shot"), \
                patch.object(gomrok.cc, "is_captcha_empty", return_value=False), \
                patch.object(gomrok.cc, "crop_captcha_with_padding", return_value=Mock()), \
                patch.object(gomrok.cc, "snapshot_captcha", return_value=("s1", "f1")), \
                patch.object(gomrok, "type_and_verify", return_value=True):
            errors, advanced, attempts = gomrok.retry_imei_captcha_in_place(
                driver, Mock(), solver, ("s0", "f0"), Mock(),
                Mock(return_value=([], True)), max_attempts=2,
            )

        refresh.assert_called_once()
        self.assertEqual((errors, advanced, attempts), ([], True, 1))

    def test_transient_conversion_error_is_retryable(self):
        self.assertTrue(gomrok.is_imei_transient_error([
            "خطای دریافتی : خطا در تبدیل اطلاعات"
        ]))
        self.assertTrue(gomrok.is_imei_retryable_error([
            "خطای دریافتی : خطا در تبدیل اطلاعات"
        ]))
        self.assertFalse(gomrok.is_imei_retryable_error([
            "شناسه در اطلاعات دریافت شده از گمرک ورودی یافت نشد"
        ]))

    def test_unhealthy_captcha_f5s_until_healthy_and_reprepares_form(self):
        driver = Mock()
        captcha = Mock()
        driver.find_element.return_value = captcha
        wait = Mock()
        solver = Mock()
        solver.read_pil.return_value = "abcde"
        prepare_form = Mock()

        with patch.object(gomrok.cc, "wait_for_new_captcha", return_value=False), \
                patch.object(gomrok.cc, "click_refresh", return_value=False), \
                patch.object(gomrok.cc, "prepare_captcha_for_shot"), \
                patch.object(gomrok.cc, "is_captcha_empty", side_effect=[True, True, False]), \
                patch.object(gomrok.cc, "crop_captcha_with_padding", return_value=Mock()), \
                patch.object(gomrok.cc, "snapshot_captcha", return_value=("s1", "f1")), \
                patch.object(gomrok, "type_and_verify", return_value=True):
            errors, advanced, attempts = gomrok.retry_imei_captcha_in_place(
                driver, wait, solver, ("s0", "f0"), Mock(),
                Mock(return_value=([], True)), max_attempts=None,
                retryable=gomrok.is_imei_retryable_error,
                prepare_form=prepare_form,
            )

        self.assertEqual(driver.refresh.call_count, 2)
        self.assertEqual(prepare_form.call_count, 2)
        self.assertEqual((errors, advanced, attempts), ([], True, 1))

    def test_registrant_end_error_classifier(self):
        self.assertTrue(gomrok.is_registrant_end_error(
            "شما چند اظهار ناتمام دارید یا شناسه IMEI توسط شخص دیگری درحال ثبت بودهاست. "
            "لطفا دکمه پایان را بزنید و مجدد یک شناسه IMEI را وارد کنید."
        ))
        self.assertTrue(gomrok.is_registrant_end_error("اظهار ناتمام دارید"))
        self.assertTrue(gomrok.is_registrant_end_error("پایان را بزنید"))
        self.assertTrue(gomrok.is_registrant_end_error("توسط شخص دیگری درحال ثبت"))
        self.assertFalse(gomrok.is_registrant_end_error(""))
        self.assertFalse(gomrok.is_registrant_end_error(
            "سامانه در حال حاضر شلوغ است، لطفا بعدا تلاش کنید"))

    def test_main_has_page1_restart_handling(self):
        source = inspect.getsource(gomrok.main)
        self.assertIn("reuse_driver", source)
        self.assertIn("Page1Restart", source)
        self.assertIn("WP_REG_END", source)
        self.assertIn("is_registrant_end_error", source)
        self.assertIn("except Page1Restart", source)
        # quit only in the generic handler, NOT for Page1Restart
        page1_handler = source.split("except Page1Restart")[1].split("except Exception")[0]
        self.assertNotIn("driver.quit()", page1_handler)


if __name__ == "__main__":
    unittest.main()
