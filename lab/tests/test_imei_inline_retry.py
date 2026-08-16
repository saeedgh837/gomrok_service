import inspect
import unittest
from unittest.mock import Mock, patch

import gomrok


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
