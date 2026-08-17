"""Tests for the "profile already registered" short-circuit.

If, right after submitting imeiStep, the site serves the PRE-PAYMENT INVOICE page
instead of hamtaStep, the profile has already been through the flow and already
holds a payment link. The robot must then:

  * report it (logs.jsonl row + a Telegram message, both bounded),
  * mark the panel profile unusable (blocked + disabled),
  * stop — no retry, browser closed.
"""
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import gomrok
import panel


class FakeElement:
    def __init__(self, onclick=""):
        self._onclick = onclick

    def get_attribute(self, name):
        return self._onclick if name == "onclick" else None


class FakeDriver:
    """Answers find_elements() from a set of present wicketpaths."""

    def __init__(self, present, onclick="", url="https://epl.irica.gov.ir/invoice"):
        self.present = set(present)
        self.onclick = onclick
        self.current_url = url
        self.quit_calls = 0

    def find_elements(self, _by, selector):
        m = re.search(r'wicketpath="([^"]+)"', selector)
        wp = m.group(1) if m else ""
        if wp in self.present:
            return [FakeElement(self.onclick if wp == gomrok.WP_PARDAKHT else "")]
        return []

    def quit(self):
        self.quit_calls += 1


PAY_LINK = "https://epl.irica.gov.ir/pay?id=abc123"
ONCLICK = f"window.location.href = '{PAY_LINK}';"


class DetectPrepaymentPageTests(unittest.TestCase):
    def test_detects_invoice_page(self):
        driver = FakeDriver([gomrok.WP_PARDAKHT])
        self.assertTrue(gomrok.detect_prepayment_page(driver))

    def test_ignores_page_without_pay_button(self):
        driver = FakeDriver([gomrok.WP_HAMTA_CONTINUE])
        self.assertFalse(gomrok.detect_prepayment_page(driver))

    def test_does_not_fire_while_still_on_imei_step(self):
        driver = FakeDriver([gomrok.WP_PARDAKHT, gomrok.WP_CONTINUE])
        self.assertFalse(gomrok.detect_prepayment_page(driver))

    def test_does_not_fire_when_hamta_step_is_also_present(self):
        driver = FakeDriver([gomrok.WP_PARDAKHT, gomrok.WP_HAMTA_CONTINUE])
        self.assertFalse(gomrok.detect_prepayment_page(driver))

    def test_driver_errors_are_not_fatal(self):
        class Boom:
            def find_elements(self, *_a):
                raise RuntimeError("no session")

        self.assertFalse(gomrok.detect_prepayment_page(Boom()))


class ExtractPaymentLinkTests(unittest.TestCase):
    def test_reads_link_from_onclick(self):
        driver = FakeDriver([gomrok.WP_PARDAKHT], onclick=ONCLICK)
        self.assertEqual(gomrok._extract_payment_link(driver), PAY_LINK)

    def test_falls_back_to_current_url(self):
        driver = FakeDriver([gomrok.WP_PARDAKHT], onclick="doSomethingElse();",
                            url="https://epl.irica.gov.ir/invoice?x=1")
        self.assertEqual(gomrok._extract_payment_link(driver),
                         "https://epl.irica.gov.ir/invoice?x=1")


class HandleAlreadyRegisteredTests(unittest.TestCase):
    MESSAGE = "قبلاً این پروفایل لینک پرداخت گرفته است"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="already-reg-")
        self._shots = gomrok._SCREENSHOTS_DIR
        gomrok._SCREENSHOTS_DIR = self.tmp

    def tearDown(self):
        gomrok._SCREENSHOTS_DIR = self._shots

    def _run(self, screenshot_ok=True, profile_id="p1"):
        self.logged = []
        self.delivered = []
        self.unusable = []

        def fake_shot(_driver, path):
            if screenshot_ok:
                with open(path, "wb") as f:
                    f.write(b"\x89PNG\r\n\x1a\n")
            return screenshot_ok

        driver = FakeDriver([gomrok.WP_PARDAKHT], onclick=ONCLICK)
        with patch.object(gomrok, "_full_screenshot", side_effect=fake_shot), \
             patch.object(gomrok, "log_event",
                          side_effect=lambda *a, **k: self.logged.append((a, k))), \
             patch.object(gomrok, "_telegram_deliver",
                          side_effect=lambda kind, **k: self.delivered.append((kind, k)) or True), \
             patch.object(gomrok, "_mark_panel_profile_unusable",
                          side_effect=lambda pid, text, **k: self.unusable.append((pid, text, k)) or True), \
             patch.object(gomrok, "_CLAIMED_PROFILE_ID", profile_id), \
             patch.object(gomrok, "_CLAIMED_RUNNER_ID", "r1"):
            link = gomrok.handle_already_registered(
                driver, title="Ali", national="0012345678", imei="352506099000001")
        return link, driver

    def test_returns_and_persists_the_payment_link(self):
        link, _ = self._run()
        self.assertEqual(link, PAY_LINK)
        txt = [n for n in os.listdir(self.tmp) if n.startswith("payment-link-")]
        self.assertEqual(len(txt), 1)
        with open(os.path.join(self.tmp, txt[0]), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), PAY_LINK)

    def test_saves_an_invoice_screenshot(self):
        self._run()
        shots = [n for n in os.listdir(self.tmp) if n.endswith("_already_paid.png")]
        self.assertEqual(len(shots), 1)

    def test_logs_an_error_row_with_the_persian_message(self):
        self._run()
        self.assertEqual(len(self.logged), 1)
        args, kwargs = self.logged[0]
        self.assertEqual(args[0], "error")
        self.assertIn(self.MESSAGE, args[1])
        self.assertIn(PAY_LINK, args[1])
        self.assertEqual(kwargs["imei"], "352506099000001")
        self.assertEqual(kwargs["title"], "Ali")
        self.assertIn("faktor", kwargs["step"])

    def test_sends_a_telegram_photo_with_the_message_and_link(self):
        self._run()
        self.assertEqual(len(self.delivered), 1)
        kind, kwargs = self.delivered[0]
        self.assertEqual(kind, "photo")
        self.assertIn(self.MESSAGE, kwargs["caption"])
        self.assertIn(PAY_LINK, kwargs["caption"])
        self.assertTrue(kwargs["photo_path"].endswith("_already_paid.png"))

    def test_falls_back_to_a_text_message_when_the_screenshot_fails(self):
        self._run(screenshot_ok=False)
        kind, kwargs = self.delivered[0]
        self.assertEqual(kind, "text")
        self.assertIn(self.MESSAGE, kwargs["text"])

    def test_marks_the_panel_profile_unusable_with_link_and_receipt(self):
        self._run()
        self.assertEqual(len(self.unusable), 1)
        pid, text, kwargs = self.unusable[0]
        self.assertEqual(pid, "p1")
        self.assertIn(self.MESSAGE, text)
        self.assertIn(PAY_LINK, text)
        # the link and screenshot must ride along so the panel can show them
        # WITHOUT record_profile_output() (which would flip status to success)
        self.assertEqual(kwargs.get("payment_link"), PAY_LINK)
        self.assertTrue(str(kwargs.get("receipt") or "").endswith("_already_paid.png"))

    def test_does_not_call_record_profile_output(self):
        """record_profile_output() sets status=success and would undo 'blocked'."""
        import inspect
        import re as _re
        source = inspect.getsource(gomrok.handle_already_registered)
        # ignore comments (the code explains WHY it is not used)
        code = "\n".join(l.split("#")[0] for l in source.splitlines())
        self.assertIsNone(_re.search(r"record_profile_output\s*\(", code))

    def test_works_without_a_claimed_profile(self):
        link, _ = self._run(profile_id="")
        self.assertEqual(link, PAY_LINK)
        self.assertEqual(self.unusable, [])
        self.assertEqual(len(self.logged), 1)


class PanelBlocksTheProfileTests(unittest.TestCase):
    def test_mark_unusable_blocks_disables_and_frees_the_profile(self):
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(
                panel,
                PROFILES_PATH=os.path.join(tmp, "profiles.json"),
                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock")):
            panel._LAST_GOOD_STORE.clear()
            panel.save_store({"active_id": "p1", "profiles": [{
                "id": "p1", "title": "Ali", "run_status": "busy",
                "runner_id": "r1", "runner_pid": 4242,
            }]})
            self.assertTrue(panel.mark_profile_unusable(
                "p1", "قبلاً این پروفایل لینک پرداخت گرفته است"))
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["registration_status"], "blocked")
            self.assertFalse(profile["enabled"])
            self.assertEqual(profile["run_status"], "idle")
            self.assertEqual(profile["runner_pid"], 0)
            self.assertIn("لینک پرداخت", profile["runtime_error"])
            # a blocked profile can no longer be started
            status, _payload = panel.start_profile("p1")
            self.assertEqual(status, 409)


class RobotStopsInsteadOfRetryingTests(unittest.TestCase):
    def test_main_returns_after_detecting_the_invoice_page(self):
        """The call site must close the browser and RETURN (no retry loop)."""
        import inspect
        source = inspect.getsource(gomrok.main)
        idx = source.find("if detect_prepayment_page(driver):")
        self.assertGreater(idx, -1, "call site missing from main()")
        block = source[idx:idx + 500]
        self.assertIn("handle_already_registered(", block)
        self.assertIn("driver.quit()", block)
        self.assertIn("return", block)
        self.assertNotIn("continue", block.split("return")[0])
        # and it must run BEFORE the hamtaStep handling
        self.assertLess(idx, source.find("MARHALE-ye 2: safhe-ye hamtaStep"))


if __name__ == "__main__":
    unittest.main()
