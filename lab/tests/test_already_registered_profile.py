"""Tests for the "profile already registered" short-circuit.

If, right after submitting imeiStep, the site serves the PRE-PAYMENT INVOICE page
instead of hamtaStep, the profile has already been through the flow and already
holds a payment link. The robot must then:

  * report it (logs.jsonl row + a Telegram message, both bounded),
  * mark the panel profile SUCCESS (not blocked), with the payment link + receipt,
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
    MESSAGE = "Existing prepayment output accepted for the current attempt"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="already-reg-")
        self.tmp = temporary.name
        self.addCleanup(temporary.cleanup)
        paths = patch.multiple(panel,
            PROFILES_PATH=os.path.join(self.tmp, "profiles.json"),
            INPUTS_PATH=os.path.join(self.tmp, "inputs.json"),
            _STORE_LOCK_PATH=os.path.join(self.tmp, "profiles.json.lock"))
        paths.start()
        self.addCleanup(paths.stop)
        shots = patch.object(gomrok, "_SCREENSHOTS_DIR", self.tmp)
        shots.start()
        self.addCleanup(shots.stop)
        panel._LAST_GOOD_STORE.clear()
        panel.save_store({"active_id": "p1", "profiles": [{
            "id": "p1", "title": "Synthetic passenger", "national": "0012345679",
            "birth": "1375/04/29", "mobile": "09121234567", "passport": "PP0000001",
            "imeis": ["352506099000001"], "registration_status": "pending",
        }]})

    def _run(self, screenshot_ok=True, profile_id="p1", record_ok=True):
        self.logged = []
        self.delivered = []
        self.outputs = []
        self.claim = panel.claim_profile("p1") if profile_id else None
        if profile_id:
            self.assertIsNotNone(self.claim)
        runner = self.claim["runner_id"] if self.claim else ""
        fingerprint = self.claim["claimed_identity_fingerprint"] if self.claim else ""

        def fake_shot(_driver, path):
            if screenshot_ok:
                with open(path, "wb") as handle:
                    handle.write(bytes((137, 80, 78, 71, 13, 10, 26, 10)))
            return screenshot_ok

        driver = FakeDriver([gomrok.WP_PARDAKHT], onclick=ONCLICK)
        with patch.object(gomrok, "_full_screenshot", side_effect=fake_shot), \
                patch.object(gomrok, "log_event",
                             side_effect=lambda *a, **k: self.logged.append((a, k))), \
                patch.object(gomrok, "_telegram_deliver",
                             side_effect=lambda kind, **k: self.delivered.append((kind, k)) or True), \
                patch.object(panel, "publish_profile_result", wraps=panel.publish_profile_result) as publish, \
                patch.object(gomrok, "_CLAIMED_PROFILE_ID", profile_id), \
                patch.object(gomrok, "_CLAIMED_RUNNER_ID", runner), \
                patch.object(gomrok, "_CLAIMED_FINGERPRINT", fingerprint if record_ok else "wrong-fingerprint"), \
                patch.object(gomrok, "_CLAIMED_CRM_DEVICE_ID", ""), \
                patch.object(gomrok, "_CLAIMED_IDENTITY_VERIFIED", False):
            try:
                link = gomrok.handle_already_registered(
                    driver, title="Ali", national="0012345679", imei="352506099000001")
            finally:
                self.outputs = list(publish.call_args_list)
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
        shots = [n for n in os.listdir(self.tmp) if n.endswith("_existing_prepayment.png")]
        self.assertEqual(len(shots), 1)

    def test_logs_success_only_after_current_publication_is_accepted(self):
        self._run()
        self.assertEqual(len(self.logged), 1)
        args, kwargs = self.logged[0]
        self.assertEqual(args[0], "success")          # NOT "error" — user's decision
        self.assertIn(self.MESSAGE, args[1])
        self.assertNotIn(PAY_LINK, args[1])
        self.assertEqual(kwargs["imei"], "352506099000001")
        self.assertEqual(kwargs["title"], "Ali")
        self.assertEqual(kwargs["step"], "prepayment")

    def test_sends_a_telegram_photo_with_the_message_and_link(self):
        self._run()
        self.assertEqual(len(self.delivered), 1)
        kind, kwargs = self.delivered[0]
        self.assertEqual(kind, "photo")
        self.assertIn("Current registration prepayment", kwargs["caption"])
        self.assertIn(PAY_LINK, kwargs["caption"])
        self.assertTrue(kwargs["photo_path"].endswith("_existing_prepayment.png"))

    def test_falls_back_to_a_text_message_when_the_screenshot_fails(self):
        self._run(screenshot_ok=False)
        kind, kwargs = self.delivered[0]
        self.assertEqual(kind, "text")
        self.assertIn("Current registration prepayment", kwargs["text"])

    def test_marks_the_profile_success_via_exact_publication(self):
        self._run()
        self.assertEqual(len(self.outputs), 1)
        args = self.outputs[0].args
        self.assertEqual(args[:3], ("p1", self.claim["runner_id"],
                                   self.claim["claimed_identity_fingerprint"]))
        result = args[3]
        self.assertTrue(result["receipt"].endswith("_existing_prepayment.png"))
        self.assertEqual(result["link"], PAY_LINK)
        self.assertTrue(result["existing_prepayment"])
        profile = panel.load_store()["profiles"][0]
        self.assertEqual(profile["registration_status"], "success")
        self.assertEqual(profile["payment_link"], PAY_LINK)
        self.assertFalse(hasattr(panel, "mark_profile_success"))

    def test_does_not_block_the_profile(self):
        """This must NOT call mark_profile_unusable — user wants SUCCESS, not blocked."""
        import inspect
        source = inspect.getsource(gomrok.handle_already_registered)
        code = "\n".join(l.split("#")[0] for l in source.splitlines())
        self.assertIsNone(re.search(r"mark_profile_unusable\s*\(", code))
        self.assertIsNone(re.search(r"_mark_panel_profile_unusable\s*\(", code))

    def test_rejected_publication_has_no_legacy_fallback_or_success_notice(self):
        with self.assertRaises(panel.ProfileIdentityStopped):
            self._run(record_ok=False)
        self.assertEqual(len(self.outputs), 1)
        self.assertEqual(self.outputs[0].args[2], "wrong-fingerprint")
        self.assertEqual(self.logged, [])
        self.assertEqual(self.delivered, [])
        self.assertEqual(panel.load_store()["profiles"][0]["payment_link"], "")
        self.assertFalse(hasattr(panel, "mark_profile_success"))

    def test_unclaimed_output_is_not_published_or_announced(self):
        with self.assertRaises(panel.ProfileIdentityStopped):
            self._run(profile_id="")
        self.assertEqual(self.outputs, [])
        self.assertEqual(self.logged, [])
        self.assertEqual(self.delivered, [])


class PanelMarksSuccessTests(unittest.TestCase):
    def _paths(self, tmp):
        return patch.multiple(
            panel,
            PROFILES_PATH=os.path.join(tmp, "profiles.json"),
            INPUTS_PATH=os.path.join(tmp, "inputs.json"),
            _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock"))

    def test_record_output_sets_success_and_link_and_frees_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            panel._LAST_GOOD_STORE.clear()
            panel.save_store({"active_id": "p1", "profiles": [{
                "id": "p1", "title": "Ali", "national": "0012345679",
                "birth": "1375/04/29", "mobile": "09121234567", "passport": "PP0000001",
                "imeis": ["352506099000001"], "registration_status": "pending",
            }]})
            claimed = panel.claim_profile("p1")
            self.assertIsNotNone(claimed)
            receipt = os.path.join(tmp, "inv.png")
            ok = panel.record_profile_output(
                "p1", claimed["runner_id"], receipt=receipt, payment="", link=PAY_LINK,
                expected_fingerprint=claimed["claimed_identity_fingerprint"])
            self.assertTrue(ok)
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["registration_status"], "success")
            self.assertEqual(profile["payment_link"], PAY_LINK)
            self.assertEqual(profile["receipt_screenshot"], receipt)
            self.assertTrue(panel.release_profile("p1", claimed["runner_id"]))
            self.assertEqual(panel.load_store()["profiles"][0]["run_status"], "idle")

    def test_a_success_profile_cannot_be_started_again(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            panel._LAST_GOOD_STORE.clear()
            panel.save_store({"active_id": "p1", "profiles": [{
                "id": "p1", "title": "Ali", "national": "0012345678",
                "birth": "1375/04/29", "imeis": ["352506099000001"],
                "registration_status": "success",
            }]})
            status, _payload = panel.start_profile("p1")
            self.assertEqual(status, 409)

    def test_a_success_profile_is_not_claimable(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            panel._LAST_GOOD_STORE.clear()
            panel.save_store({"active_id": "p1", "profiles": [{
                "id": "p1", "title": "Ali", "registration_status": "success",
            }]})
            self.assertIsNone(panel.claim_profile())


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
