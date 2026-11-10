"""Tests for the first-page fast-probe and the hard Telegram timeout budget.

Root causes covered:
  1. imeiStep was slow because the robot waited a FIXED CAPTCHA_WAIT_SEC (10s)
     before deciding the page came back incomplete. Now wait_first_page_ready()
     returns as soon as the page is idle (readyState complete, no AJAX, no
     pending images) and the captcha is still missing/broken -> immediate F5.
     A healthy page is confirmed in ~one settle window (<= ~0.5s).
  2. The robot could hang forever on step 4 when Telegram was unreachable.
     Every send now carries a hard deadline; on timeout the robot logs
     "timeout ... mahali zakhire shod" and CONTINUES.
"""
import time
import unittest
from unittest.mock import patch

import gomrok
import telegram_notify as tg


class FakeDriver:
    """Returns a scripted sequence of probe dicts from execute_script."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = 0
        self.refreshes = 0

    def execute_script(self, *_args, **_kwargs):
        self.calls += 1
        if not self.states:
            raise AssertionError("probe called more times than scripted")
        state = self.states[0]
        if len(self.states) > 1:
            self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return dict(state)

    def refresh(self):
        self.refreshes += 1


READY = {"ready": True, "capOk": True, "capBroken": False, "capMissing": False,
         "btnOk": True, "ajaxBusy": False, "pending": False}
IDLE_NO_CAPTCHA = {"ready": True, "capOk": False, "capBroken": False, "capMissing": True,
                   "btnOk": False, "ajaxBusy": False, "pending": False}
BROKEN_CAPTCHA = {"ready": True, "capOk": False, "capBroken": True, "capMissing": False,
                  "btnOk": True, "ajaxBusy": False, "pending": False}
STILL_LOADING = {"ready": False, "capOk": False, "capBroken": False, "capMissing": True,
                 "btnOk": False, "ajaxBusy": True, "pending": True}


class RefreshDriver:
    """Serves `before` until refresh() is called `refresh_to_ready` times."""

    def __init__(self, before, after, refresh_to_ready=1):
        self.before = before
        self.after = after
        self.refresh_to_ready = refresh_to_ready
        self.refreshes = 0

    def execute_script(self, *_a, **_k):
        return dict(self.after if self.refreshes >= self.refresh_to_ready else self.before)

    def refresh(self):
        self.refreshes += 1


class FirstPageProbeTests(unittest.TestCase):
    def test_healthy_page_is_confirmed_immediately(self):
        driver = FakeDriver([READY])
        started = time.monotonic()
        self.assertEqual(gomrok.wait_first_page_ready(driver, budget=6.0), "ready")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_idle_without_captcha_is_incomplete_within_settle_window(self):
        driver = FakeDriver([IDLE_NO_CAPTCHA])
        started = time.monotonic()
        verdict = gomrok.wait_first_page_ready(driver, budget=6.0, settle=0.3)
        elapsed = time.monotonic() - started
        self.assertEqual(verdict, "incomplete")
        # must NOT burn the whole budget — this is the latency bug being fixed
        self.assertLess(elapsed, 1.2, f"took {elapsed:.2f}s")
        self.assertGreaterEqual(elapsed, 0.3)

    def test_broken_captcha_is_incomplete_without_waiting_for_settle(self):
        driver = FakeDriver([BROKEN_CAPTCHA])
        started = time.monotonic()
        self.assertEqual(gomrok.wait_first_page_ready(driver, budget=6.0, settle=2.0),
                         "incomplete")
        self.assertLess(time.monotonic() - started, 0.4)

    def test_still_loading_then_ready_waits_for_the_page(self):
        driver = FakeDriver([STILL_LOADING, STILL_LOADING, READY])
        self.assertEqual(gomrok.wait_first_page_ready(driver, budget=6.0), "ready")

    def test_loading_forever_hits_timeout_and_does_not_claim_ready(self):
        driver = FakeDriver([STILL_LOADING])
        started = time.monotonic()
        self.assertEqual(gomrok.wait_first_page_ready(driver, budget=0.6), "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_probe_errors_are_swallowed_and_retried(self):
        driver = FakeDriver([RuntimeError("no window"), READY])
        self.assertEqual(gomrok.wait_first_page_ready(driver, budget=3.0), "ready")

    def test_ensure_page_ready_refreshes_then_succeeds(self):
        driver = RefreshDriver(IDLE_NO_CAPTCHA, READY, refresh_to_ready=2)
        ok = gomrok.ensure_page_ready(driver, wait=None, dbg=None, max_reload=4)
        self.assertTrue(ok)
        self.assertEqual(driver.refreshes, 2)

    def test_ensure_page_ready_gives_up_after_max_reload(self):
        driver = FakeDriver([IDLE_NO_CAPTCHA])
        ok = gomrok.ensure_page_ready(driver, wait=None, dbg=None, max_reload=2)
        self.assertFalse(ok)
        self.assertEqual(driver.refreshes, 2)


class TelegramDeadlineTests(unittest.TestCase):
    CFG = {"api_base": "https://example.invalid", "bot_token": "T", "chat_ids": ["1"]}

    def test_post_confirmed_stops_at_deadline_with_timeout_error(self):
        calls = {"n": 0}

        def slow_post(*_a, **kwargs):
            calls["n"] += 1
            raise OSError("connection reset")

        with patch("requests.post", side_effect=slow_post):
            deadline = time.monotonic() + 0.25
            with self.assertRaises(TimeoutError):
                tg._post_confirmed("sendMessage", self.CFG, {"chat_id": "1"},
                                   attempts=6, deadline=deadline)
        self.assertGreaterEqual(calls["n"], 1)

    def test_request_timeout_is_clamped_to_remaining_budget(self):
        seen = {}

        def capture(*_a, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise OSError("boom")

        with patch("requests.post", side_effect=capture):
            with self.assertRaises((TimeoutError, RuntimeError)):
                tg._post_confirmed("sendPhoto", self.CFG, {"chat_id": "1"},
                                   files_factory=lambda: {},
                                   attempts=1, deadline=time.monotonic() + 2.0)
        self.assertIsNotNone(seen.get("timeout"))
        self.assertLessEqual(seen["timeout"], 2.0)

    def test_fanout_reports_timeout_when_every_chat_times_out(self):
        with patch("requests.post", side_effect=OSError("down")):
            with self.assertRaises(TimeoutError):
                tg._fanout("sendMessage", self.CFG, ["1", "2"],
                           lambda cid: {"chat_id": cid},
                           deadline=time.monotonic() + 0.2)

    def test_send_message_accepts_timeout_kwarg(self):
        with patch.object(tg, "ensure_config", return_value=self.CFG), \
             patch("requests.post", side_effect=OSError("down")):
            with self.assertRaises((TimeoutError, RuntimeError)):
                tg.send_message("hi", timeout=0.2)


class RobotContinuesAfterTelegramFailureTests(unittest.TestCase):
    def test_deliver_returns_false_and_logs_on_timeout(self):
        logged = []
        with patch.object(gomrok, "log_event", side_effect=lambda *a, **k: logged.append((a, k))), \
             patch("telegram_notify.send_photo", side_effect=TimeoutError("deadline")):
            ok = gomrok._telegram_deliver("photo", photo_path=r"C:\shots\receipt.png",
                                          caption="c", step="telegram: rasid (aks)",
                                          title="T", national="N", imei="I")
        self.assertFalse(ok)
        self.assertEqual(len(logged), 1)
        args, _kwargs = logged[0]
        self.assertEqual(args[0], "warn")
        self.assertIn("TIMEOUT", args[1])
        self.assertIn("mahali zakhire shod", args[1])
        self.assertIn("receipt.png", args[1])

    def test_deliver_returns_false_and_logs_on_generic_failure(self):
        logged = []
        with patch.object(gomrok, "log_event", side_effect=lambda *a, **k: logged.append((a, k))), \
             patch("telegram_notify.send_message", side_effect=RuntimeError("chat not found")):
            ok = gomrok._telegram_deliver("text", text="x", step="telegram: rasid (matn)")
        self.assertFalse(ok)
        self.assertIn("na-movaffagh", logged[0][0][1])

    def test_deliver_returns_true_on_success_without_logging_a_warning(self):
        logged = []
        with patch.object(gomrok, "log_event", side_effect=lambda *a, **k: logged.append(a)), \
             patch("telegram_notify.send_photo", return_value=[{"ok": True}]):
            ok = gomrok._telegram_deliver("photo", photo_path="x.png", caption="c",
                                          step="telegram: rasid (aks)")
        self.assertTrue(ok)
        self.assertEqual(logged, [])

    def test_photo_and_text_budgets_are_bounded(self):
        self.assertLessEqual(gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC, 60)
        self.assertLessEqual(gomrok.TELEGRAM_TEXT_TIMEOUT_SEC, 30)
        self.assertGreater(gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC, gomrok.TELEGRAM_TEXT_TIMEOUT_SEC)

    def test_receipt_and_payment_paths_use_the_bounded_helper(self):
        import inspect
        import os
        import tempfile
        from types import SimpleNamespace
        import panel

        source = inspect.getsource(gomrok.handle_receipt_and_payment)
        self.assertNotIn("tg.send_photo", source)
        self.assertNotIn("tg.send_message", source)
        for receipt_ok, payment_ok in ((True, True), (False, False), (True, False), (False, True)):
            with self.subTest(receipt=receipt_ok, payment=payment_ok), tempfile.TemporaryDirectory() as directory:
                paths = {"PROFILES_PATH": os.path.join(directory, "profiles.json"),
                         "INPUTS_PATH": os.path.join(directory, "inputs.json"),
                         "_STORE_LOCK_PATH": os.path.join(directory, "profiles.json.lock")}
                with patch.multiple(panel, **paths):
                    panel._LAST_GOOD_STORE.clear()
                    panel.save_store({"active_id": "p1", "profiles": [{
                        "id": "p1", "title": "Synthetic", "national": "0012345679",
                        "birth": "1375/04/29", "mobile": "09121234567", "passport": "PP0000001",
                        "imeis": ["352506099000001"], "registration_status": "pending",
                    }]})
                    claim = panel.claim_profile("p1")
                    self.assertIsNotNone(claim)
                    link = "https://payment.example.test/current"
                    button = SimpleNamespace(get_attribute=lambda _name: "location.href='" + link + "';")
                    driver = SimpleNamespace(current_url=link, find_elements=lambda *_args: [button])
                    available = iter((receipt_ok, payment_ok))
                    events = []

                    def screenshot(_driver, path):
                        ok = next(available)
                        if ok:
                            with open(path, "wb") as handle:
                                handle.write(bytes((137, 80, 78, 71, 13, 10, 26, 10)))
                        return ok

                    def log(status, *_args, **_kwargs):
                        if status == "success":
                            self.assertEqual(panel.load_store()["profiles"][0]["payment_link"], link)
                            events.append("accepted")
                        else:
                            events.append(status)

                    def timeout(*_args, **_kwargs):
                        events.append("delivery")
                        raise TimeoutError("synthetic delivery deadline")

                    with patch.object(gomrok, "_SCREENSHOTS_DIR", directory), \
                            patch.object(gomrok, "_CLAIMED_PROFILE_ID", "p1"), \
                            patch.object(gomrok, "_CLAIMED_RUNNER_ID", claim["runner_id"]), \
                            patch.object(gomrok, "_CLAIMED_FINGERPRINT", claim["claimed_identity_fingerprint"]), \
                            patch.object(gomrok, "_CLAIMED_CRM_DEVICE_ID", ""), \
                            patch.object(gomrok, "_CLAIMED_IDENTITY_VERIFIED", True), \
                            patch.object(gomrok, "_full_screenshot", side_effect=screenshot), \
                            patch.object(gomrok.cc, "fire_refresh_events"), \
                            patch.object(gomrok.time, "sleep"), \
                            patch.object(gomrok, "log_event", side_effect=log), \
                            patch.object(gomrok, "_telegram_deliver", wraps=gomrok._telegram_deliver) as deliver, \
                            patch.object(panel, "publish_profile_result", wraps=panel.publish_profile_result) as publish, \
                            patch.object(tg, "send_photo", side_effect=timeout) as photo, \
                            patch.object(tg, "send_message", side_effect=timeout) as text:
                        gomrok.handle_receipt_and_payment(driver, title="Synthetic", national="0012345679")
                    self.assertEqual(publish.call_count, 1)
                    self.assertEqual(publish.call_args.args[:3],
                                     ("p1", claim["runner_id"], claim["claimed_identity_fingerprint"]))
                    kinds = ["photo" if ok else "text" for ok in (receipt_ok, payment_ok)]
                    self.assertEqual([call.args[0] for call in deliver.call_args_list], kinds)
                    self.assertEqual(photo.call_count, kinds.count("photo"))
                    self.assertEqual(text.call_count, kinds.count("text"))
                    for call in photo.call_args_list:
                        self.assertEqual(call.kwargs["timeout"], gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC)
                    for call in text.call_args_list:
                        self.assertEqual(call.kwargs["timeout"], gomrok.TELEGRAM_TEXT_TIMEOUT_SEC)
                    self.assertEqual(events, ["accepted", "delivery", "warn", "delivery", "warn"])


if __name__ == "__main__":
    unittest.main()
