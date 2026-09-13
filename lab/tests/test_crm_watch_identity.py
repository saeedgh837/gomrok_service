# -*- coding: utf-8 -*-
"""Watch-stage inquiry identity routing + not-owner classification (item 7).

Both intake and watch-stage customs inquiries run under the inquiry-identity
POOL; watch stages no longer fall back to the device owner's identity. A
legally-registered device is recorded under the passenger's national code, so
querying with a pool code returns the site's "registered under another code"
message («متعلق به این کد ملی نیست») — which is itself the positive
"registered" signal that gates the payment step (classified 'advanced').
When no valid pool entry exists, the request fails closed with the pool
error exactly like step-2 intake (no owner-identity fallback).

The handover owner gate itself STAYS: the case record and the step-4
Telegram handoff need the passenger — only its inquiry-identity rationale
is gone.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crm  # noqa: E402
import estelam_gomrok  # noqa: E402
import gomrok  # noqa: E402


# Established synthetic identity values (same values as test_crm_inquiry_pool
# / test_v1_1_2_crm_updates fixtures) — clearly synthetic, never real data.
POOL_NATIONAL = "0076431309"    # distinct from the owner's national below
OWNER_NATIONAL = "0012345679"
BIRTH = "1375/04/29"
MOBILE = "09121234567"

POOL_EMPTY_ERROR = "استخر مشخصات استعلام خالی است یا هیچ ورودی معتبری ندارد"

# Synthetic site-message fixtures (serials from the established synthetic
# 3569380356xxxxx family — never real serials):
NOT_OWNER_MESSAGE = "شناسه 356938035643101 متعلق به این کد ملی نیست"
HEALTHY_MESSAGE = "شناسه 356938035643809 دریافت شده از گمرک ورودی یافت نشد"
KNOWN_PROBLEM_MESSAGE = "این شناسه قبلا در شبکه داخلی کشور ثبت و فعال شده است"


class _TempStoreCase(unittest.TestCase):
    """Isolated CRM store (same pattern as test_v1_1_2_crm_updates)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_path = os.path.join(self.tmp.name, "crm_store.json")
        os.environ["IRICA_CRM_STORE"] = self.store_path
        crm.reset_cache()

    def tearDown(self):
        crm.reset_cache()
        os.environ.pop("IRICA_CRM_STORE", None)

    def _write_store(self, devices, owners=None, identities=None):
        """Inject synthetic records via direct store JSON (minimal rows)."""
        store = {"devices": devices, "owners": owners or [],
                 "identities": identities or []}
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False)
        crm.reset_cache()

    @staticmethod
    def _pool_entry():
        return {"id": "pool01", "label": "استعلام آزمون",
                "national": POOL_NATIONAL, "birth": BIRTH, "mobile": MOBILE}

    @staticmethod
    def _owner_record():
        # Minimal synthetic owner the handover gate can resolve.
        return {"id": "own01", "first_name": "مسافر", "last_name": "آزمون",
                "national": OWNER_NATIONAL, "birth": BIRTH, "mobile": MOBILE}


class TestWatchIdentityRouting(_TempStoreCase):
    def test_every_watch_stage_returns_pool_identity(self):
        # One device at EACH watch stage, every one WITH a bound owner: the
        # pool identity must win over the owner's (user item 7). Exactly one
        # valid pool entry makes the random pick deterministic.
        devices = [{"id": f"dev-{stage}", "stage": stage, "owner_id": "own01"}
                   for stage in crm.WATCH_STAGES]
        self.assertTrue(devices)
        self._write_store(devices, owners=[self._owner_record()],
                          identities=[self._pool_entry()])
        for device in devices:
            person, errors = crm.identity_for_stage(device["id"])
            self.assertEqual(errors, [], device["stage"])
            self.assertEqual(person, {"national": POOL_NATIONAL,
                                      "birth": BIRTH, "mobile": MOBILE})
            # The bound owner's national must NOT be used at watch stages.
            self.assertNotEqual(person["national"], OWNER_NATIONAL)

    def test_empty_pool_fails_closed_at_watch_stage(self):
        # Watch-stage device with a bound owner but ZERO pool identities:
        # fail closed exactly like intake — no owner-identity fallback.
        self._write_store(
            [{"id": "dev01", "stage": "handed", "owner_id": "own01"}],
            owners=[self._owner_record()], identities=[])
        person, errors = crm.identity_for_stage("dev01")
        self.assertIsNone(person)
        self.assertIn(POOL_EMPTY_ERROR, errors)

    def test_invalid_only_pool_fails_closed(self):
        # An entry failing validate_identity is never picked; when the pool
        # holds ONLY such entries the result is the same fail-closed error.
        bad_entry = {"id": "pool-bad", "label": "استعلام آزمون",
                     "national": "123", "birth": BIRTH, "mobile": MOBILE}
        self._write_store(
            [{"id": "dev01", "stage": "handed", "owner_id": "own01"}],
            owners=[self._owner_record()], identities=[bad_entry])
        person, errors = crm.identity_for_stage("dev01")
        self.assertIsNone(person)
        self.assertIn(POOL_EMPTY_ERROR, errors)

    def test_intake_stages_still_return_pool_identity(self):
        # Intake behavior is unchanged: pool identity, no errors.
        devices = [{"id": "dev-new", "stage": "new", "owner_id": ""},
                   {"id": "dev-inquiry", "stage": "inquiry", "owner_id": ""}]
        self._write_store(devices, identities=[self._pool_entry()])
        for device in devices:
            person, errors = crm.identity_for_stage(device["id"])
            self.assertEqual(errors, [], device["stage"])
            self.assertEqual(person["national"], POOL_NATIONAL)

    def test_handover_still_requires_owner(self):
        # The gate itself stays (case record + step-4 Telegram handoff need
        # the passenger); only its stated reason changed. Pins the updated
        # error string AND that a bound owner still lets the handover pass.
        self._write_store(
            [{"id": "dev01", "stage": "ready_owner", "owner_id": ""}],
            identities=[self._pool_entry()])
        device, errors = crm.advance("dev01", "handover")
        self.assertIsNone(device)
        self.assertTrue(any("ابتدا مسافر را به دستگاه متصل کنید" in e
                            for e in errors), errors)

        self._write_store(
            [{"id": "dev01", "stage": "ready_owner", "owner_id": "own01"}],
            owners=[self._owner_record()], identities=[self._pool_entry()])
        device, errors = crm.advance("dev01", "handover")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "handed")


class TestNotOwnerClassification(unittest.TestCase):
    """The not-the-owner site message classifies as 'advanced' (registered).

    Watch-stage inquiries run under POOL identities: a device registered
    under the passenger's code returns the site's not-owner message, and
    THAT message is the positive 'registered' signal the auto-advance join
    reads (estelam row status == 'advanced' -> stage to_payment).
    """

    def test_not_owner_message_classified_advanced(self):
        outcome = estelam_gomrok.classify_outcome(
            [NOT_OWNER_MESSAGE], advanced=False, attempts=1)
        self.assertEqual(outcome["status"], "advanced")
        self.assertTrue(str(outcome["result"]).strip())
        self.assertEqual(outcome["messages"], [NOT_OWNER_MESSAGE])

    def test_healthy_marker_still_healthy(self):
        outcome = estelam_gomrok.classify_outcome(
            [HEALTHY_MESSAGE], advanced=False, attempts=1)
        self.assertEqual(outcome["status"], "healthy")

    def test_not_owner_marker_is_not_healthy_and_not_retryable(self):
        self.assertFalse(estelam_gomrok.is_healthy_result([NOT_OWNER_MESSAGE]))
        self.assertFalse(gomrok.is_imei_retryable_error([NOT_OWNER_MESSAGE]))

    def test_known_problem_message_still_problem(self):
        # Collision guard: the distinct 'already registered in the domestic
        # network' message keeps its own 'problem' classification.
        outcome = estelam_gomrok.classify_outcome(
            [KNOWN_PROBLEM_MESSAGE], advanced=False, attempts=1)
        self.assertEqual(outcome["status"], "problem")

    def test_inquire_device_dispatches_advanced_verdict(self):
        # Source-structure assertion (repo style): _inquire_device must
        # explicitly dispatch status == "advanced" AFTER the classify_outcome
        # call and BEFORE the no-response fallthrough, returning the verdict
        # — otherwise the fallthrough silently destroys it.
        with open(estelam_gomrok.__file__, encoding="utf-8") as handle:
            src = handle.read()
        start = src.index("def _inquire_device")
        end = src.index("\ndef main", start)
        body = src[start:end]
        advanced_at = body.index('if status == "advanced":')
        classify_at = body.index("outcome = classify_outcome")
        fallthrough_at = body.index("بدون پاسخ از سایت")
        self.assertLess(classify_at, advanced_at)
        self.assertLess(advanced_at, fallthrough_at)
        self.assertIn("return outcome", body[advanced_at:fallthrough_at])


if __name__ == "__main__":
    unittest.main()
