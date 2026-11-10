import contextlib
import copy
import importlib
import threading
import unittest
from unittest.mock import Mock


class ApplicationBoundary:
    def __init__(self):
        self.value = {"state": "available", "checked": True, "can_install": True,
                      "offer": {"offer_id": "app-offer", "version": "1.19.0"}}
        self.checks = []
        self.confirmations = []

    def status(self):
        return copy.deepcopy(self.value)

    def check(self, force=False):
        self.checks.append(force)
        return self.status()

    def confirm(self, offer_id):
        self.confirmations.append(offer_id)
        self.value["state"] = "downloading"
        return self.status()


class MaintenanceServiceTests(unittest.TestCase):
    def setUp(self):
        try:
            module = importlib.import_module("maintenance_service")
        except ModuleNotFoundError:
            self.fail("The shared maintenance service is not implemented")
        self.module = module
        self.application = ApplicationBoundary()
        self.clock = [100.0]
        self.entered = []
        self.driver_check = Mock(return_value={
            "ok": True, "action_required": "update_driver", "notice": "fixture status",
            "chrome": {"installed_version": "154.0.1.1"},
            "chromedriver": {"installed_version": "154.0.1.1",
                             "matched_version": "154.0.1.2", "compatible": True},
        })
        self.driver_install = Mock(return_value={"status": "updated", "version": "154.0.1.2"})

        @contextlib.contextmanager
        def admission():
            self.entered.append("enter")
            try:
                yield
            finally:
                self.entered.append("exit")

        self.service = module.MaintenanceService(
            application=self.application, driver_check=self.driver_check,
            driver_install=self.driver_install, admission=admission,
            clock=lambda: self.clock[0])
        self.addCleanup(self.join_worker)

    def join_worker(self):
        thread = self.service._thread
        if thread is not None:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "A maintenance fixture worker did not finish")

    def check(self, force=False):
        self.service.check(force=force)
        self.join_worker()
        return self.service.status()

    def offer(self):
        return self.check()["driver"]["offer"]["offer_id"]

    def test_constructor_and_status_do_not_check_or_install(self):
        self.assertEqual(self.service.status()["driver"]["state"], "not_checked")
        self.driver_check.assert_not_called()
        self.driver_install.assert_not_called()
        self.assertEqual(self.application.checks, [])
        self.assertEqual(self.application.confirmations, [])
        self.assertEqual(self.entered, [])

    def test_check_mints_an_opaque_exact_driver_version_offer(self):
        state = self.check()
        self.assertEqual(state["driver"]["state"], "available")
        self.assertEqual(state["driver"]["offer"]["version"], "154.0.1.2")
        self.assertGreaterEqual(len(state["driver"]["offer"]["offer_id"]), 32)
        self.driver_install.assert_not_called()
        self.assertEqual(self.application.confirmations, [])
        self.assertEqual(self.entered, [])

    def test_snapshot_mutation_does_not_change_the_current_offer(self):
        state = self.check()
        expected = state["driver"]["offer"]["offer_id"]
        state["driver"]["offer"]["version"] = "999.0.0.0"
        state["driver"]["offer"]["offer_id"] = "substituted"
        current = self.service.status()["driver"]["offer"]
        self.assertEqual(current, {"offer_id": expected, "version": "154.0.1.2"})

    def test_automatic_and_manual_checks_use_cache_and_minimum_interval(self):
        self.check()
        self.clock[0] += 8
        self.check(force=True)
        self.assertEqual(len(self.application.checks), 1)
        self.clock[0] += 3
        self.check()
        self.assertEqual(len(self.application.checks), 1)
        self.check(force=True)
        self.assertEqual(self.application.checks, [False, True])
        self.clock[0] += 899
        self.check()
        self.assertEqual(len(self.application.checks), 2)
        self.clock[0] += 2
        self.check()
        self.assertEqual(len(self.application.checks), 3)

    def test_check_is_nonblocking_and_single_flight(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def blocked_check():
            entered.set()
            self.assertTrue(release.wait(timeout=3))
            return self.driver_check.return_value

        self.driver_check.side_effect = blocked_check
        self.service.check()
        self.assertTrue(entered.wait(timeout=2))
        self.assertTrue(self.service.status()["checking"])
        self.service.check(force=True)
        self.assertEqual(self.driver_check.call_count, 1)
        release.set()
        self.join_worker()
        self.assertFalse(self.service.status()["checking"])

    def test_malformed_failed_or_downgrade_checks_never_offer_installation(self):
        payloads = [None, {"ok": False, "details": "private-proxy-secret"},
                    {"ok": True, "action_required": "up_to_date", "chromedriver": {}},
                    {"ok": True, "action_required": "update_driver",
                     "chrome": {"installed_version": "154.0.1.1"},
                     "chromedriver": {"installed_version": "154.0.1.2",
                                      "matched_version": "154.0.1.1"}}]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.clock[0] += 11
                self.driver_check.return_value = payload
                state = self.check(force=True)["driver"]
                self.assertEqual(state["state"], "failed")
                self.assertIsNone(state["offer"])
                self.assertNotIn("private-proxy-secret", json_text(state))

    def test_exact_compatible_current_version_is_not_an_offer(self):
        self.driver_check.return_value["action_required"] = "up_to_date"
        self.driver_check.return_value["chromedriver"]["matched_version"] = "154.0.1.1"
        state = self.check()["driver"]
        self.assertEqual(state["state"], "current")
        self.assertIsNone(state["offer"])

    def test_no_consent_invalid_and_expired_offers_have_no_install_side_effect(self):
        offer = self.offer()
        for candidate, consent in ((offer, False), (offer, 1), ("wrong", True), ("ß", True)):
            with self.subTest(offer=candidate, consent=consent):
                self.assertFalse(self.service.confirm_driver(candidate, consent=consent)["ok"])
        self.clock[0] += 901
        self.assertFalse(self.service.confirm_driver(offer, consent=True)["ok"])
        self.driver_install.assert_not_called()
        self.assertEqual(self.entered, [])

    def test_expired_waiting_driver_offer_allows_fresh_check_and_new_consent(self):
        self.driver_install.return_value = {"status": "busy"}
        old_offer = self.offer()
        self.assertTrue(self.service.confirm_driver(old_offer, consent=True)["ok"])
        self.join_worker()
        self.clock[0] += 901
        state = self.service.status()["driver"]
        self.assertEqual(state["state"], "failed")
        self.assertIsNone(state["offer"])
        self.assertEqual(self.driver_install.call_count, 1)
        refreshed = self.check(force=True)["driver"]
        self.assertEqual(refreshed["state"], "available")
        new_offer = refreshed["offer"]["offer_id"]
        self.assertNotEqual(new_offer, old_offer)
        self.assertFalse(self.service.confirm_driver(old_offer, consent=True)["ok"])
        self.driver_install.return_value = {"status": "updated", "version": "154.0.1.2"}
        self.assertTrue(self.service.confirm_driver(new_offer, consent=True)["ok"])
        self.join_worker()
        self.assertEqual(self.driver_install.call_count, 2)
        self.assertEqual(self.service.status()["driver"]["state"], "current")

    def test_expired_waiting_driver_never_blocks_application_confirmation(self):
        self.driver_install.return_value = {"status": "driver-in-use"}
        old_offer = self.offer()
        self.service.confirm_driver(old_offer, consent=True)
        self.join_worker()
        self.clock[0] += 901
        result = self.service.confirm_app("app-offer")
        self.assertTrue(result["ok"])
        self.assertEqual(self.application.confirmations, ["app-offer"])
        self.assertEqual(self.driver_install.call_count, 1)

    def test_confirmed_driver_install_pins_version_and_holds_admission(self):
        self.assertTrue(self.service.confirm_driver(self.offer(), consent=True)["ok"])
        self.join_worker()
        self.driver_install.assert_called_once_with(consent=True, desired_version="154.0.1.2")
        self.assertEqual(self.entered, ["enter", "exit"])
        self.assertEqual(self.service.status()["driver"]["state"], "current")
        self.assertIsNone(self.service.status()["driver"]["offer"])

    def test_duplicate_confirmation_cannot_launch_two_installs(self):
        offer = self.offer()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def blocked_install(**_):
            entered.set()
            self.assertTrue(release.wait(timeout=3))
            return {"status": "updated", "version": "154.0.1.2"}

        self.driver_install.side_effect = blocked_install
        self.service.confirm_driver(offer, consent=True)
        self.assertTrue(entered.wait(timeout=2))
        self.service.confirm_driver(offer, consent=True)
        self.assertEqual(self.driver_install.call_count, 1)
        self.assertFalse(self.service.confirm_app("app-offer")["ok"])
        self.assertEqual(self.application.confirmations, [])
        release.set()
        self.join_worker()

    def test_busy_jobs_wait_without_install_and_require_explicit_retry(self):
        from maintenance_gate import MaintenanceBlocked
        offer = self.offer()

        @contextlib.contextmanager
        def busy():
            raise MaintenanceBlocked("Installation is waiting for active jobs to finish")
            yield

        self.service._admission = busy
        self.service.confirm_driver(offer, consent=True)
        self.join_worker()
        self.assertEqual(self.service.status()["driver"]["state"], "waiting_for_idle")
        self.driver_install.assert_not_called()
        self.service._admission = contextlib.nullcontext
        self.assertEqual(self.service.status()["driver"]["state"], "waiting_for_idle")
        self.service.confirm_driver(offer, consent=True)
        self.join_worker()
        self.assertEqual(self.driver_install.call_count, 1)

    def test_install_failure_is_sanitized_and_does_not_claim_current(self):
        self.driver_install.side_effect = RuntimeError("private proxy credential fixture")
        self.service.confirm_driver(self.offer(), consent=True)
        self.join_worker()
        state = self.service.status()["driver"]
        self.assertEqual(state["state"], "failed")
        self.assertNotIn("credential", json_text(state))
        self.assertEqual(self.entered, ["enter", "exit"])

    def test_application_update_blocks_driver_installation(self):
        offer = self.offer()
        self.assertTrue(self.service.confirm_app("app-offer")["ok"])
        self.assertEqual(self.application.confirmations, ["app-offer"])
        self.assertFalse(self.service.confirm_driver(offer, consent=True)["ok"])
        self.driver_install.assert_not_called()


def json_text(value):
    import json
    return json.dumps(value, ensure_ascii=True)


if __name__ == "__main__":
    unittest.main()
