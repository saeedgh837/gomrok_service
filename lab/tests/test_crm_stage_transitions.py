# -*- coding: utf-8 -*-
"""Stage-machine completion contract (user items 3 + 12).

1. `stage_back` from `done` is legal (operator decision): it returns the case
   to `payment_done` in one step and clears the completion timestamps, so the
   delivery can be redone via `deliver_code`.
2. `payment_done` has no skip entry: `deliver_code` is the ONLY action that
   can complete a case.
3. Exhaustive stage x action matrices (raw synthetic devices + fully walked
   devices) assert the completion invariant: nothing but `deliver_code` may
   end at `done`, and nothing but `close_case` may end at `closed`.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crm  # noqa: E402
import panel  # noqa: E402

ALL_STAGES = ["new", "inquiry", "problem", "ready_owner", "handed",
              "to_payment", "payment", "payment_done", "done", "closed"]
# v1.5: detach_owner joined the matrix — it is allowed ONLY at
# ready_owner/problem/closed and never moves the stage (link mutation).
ALL_ACTIONS = ["queue_inquiry", "mark_problem", "mark_ready",
               "requeue_inquiry", "close_case", "attach_owner",
               "detach_owner", "handover",
               "to_payment", "start_payment", "confirm_payment",
               "deliver_code", "stage_back", "stage_skip"]


def _synthetic_national(prefix9):
    """Checksum-valid synthetic national code (same mod-11 rule as
    crm.national_checksum_ok). Test fixture only — never a real identity."""
    digits = [int(ch) for ch in str(prefix9)[:9].zfill(9)]
    total = sum(d * (10 - i) for i, d in enumerate(digits)) % 11
    check = total if total < 2 else 11 - total
    return "".join(str(d) for d in digits) + str(check)


def _synthetic_owner_fields(seq):
    """Synthetic passenger fields; `card_no` stays a valid 16-digit number so
    this file stays green with or without optional cards (parallel change)."""
    return {
        "first_name": "مسافر",
        "last_name": "آزمون یک",
        "national": _synthetic_national(100000000 + seq),
        "birth": "1375/04/29",
        "mobile": "09120000000",
        "card_no": "6219861012345678",
        "passport": "PP0000001",
    }


class _StoreHarness(unittest.TestCase):
    """Isolated CRM store (env var) + patched panel estelam path. The real
    panel.ESTELAM_PATH is never written to."""

    # Walk paths: stage -> actions that legally reach it from `new`.
    # `attach_owner` is inserted before `handover` by _walk_to; for
    # `ready_owner` it is explicit so that stage's device also carries an
    # owner (attach keeps the stage, ALLOWED["ready_owner"]).
    ACTION_PATH = {
        "new": [],
        "inquiry": ["queue_inquiry"],
        "problem": ["queue_inquiry", "mark_problem"],
        "ready_owner": ["queue_inquiry", "mark_ready", "attach_owner"],
        "handed": ["queue_inquiry", "mark_ready", "handover"],
        "to_payment": ["queue_inquiry", "mark_ready", "handover",
                       "to_payment"],
        "payment": ["queue_inquiry", "mark_ready", "handover", "to_payment",
                    "start_payment"],
        "payment_done": ["queue_inquiry", "mark_ready", "handover",
                         "to_payment", "start_payment", "confirm_payment"],
        "done": ["queue_inquiry", "mark_ready", "handover", "to_payment",
                 "start_payment", "confirm_payment", "deliver_code"],
        "closed": ["queue_inquiry", "close_case"],
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        self._patches = [
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)
        self._owner_seq = 0
        self._serial_seq = 0

    # -- fixtures ---------------------------------------------------------

    def _serial(self):
        """Unique, obviously synthetic 15-digit serial (validator: \\d{15})."""
        self._serial_seq += 1
        return ("35693803564" + str(self._serial_seq).zfill(4))[:15]

    def _add_owner(self):
        self._owner_seq += 1
        owner, errors = crm.add_owner(_synthetic_owner_fields(self._owner_seq))
        self.assertEqual(errors, [])
        return owner

    def _seed_green(self, device_id):
        """Cumulative green estelam rows (gomrok healthy + hamta good) so the
        mark_ready inquiry gate opens for walked devices."""
        rows = []
        if os.path.exists(self.estelam_store):
            with open(self.estelam_store, encoding="utf-8") as handle:
                rows = json.load(handle).get("devices") or []
        if not any(str(row.get("id")) == str(device_id) for row in rows):
            rows.append({"id": str(device_id), "status": "healthy",
                         "hamta_status": "good"})
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": rows}, handle,
                      ensure_ascii=False)

    def _device(self, device_id):
        data = crm.store_read()
        return next(d for d in data["devices"] if d["id"] == device_id)

    def _raw_store(self, path, stage, owner=None):
        """Write a raw synthetic store: one device at `stage`, merged over
        crm._empty_store() by _store_read on load."""
        store = {"devices": [{"id": "dev01", "stage": stage, "owner_id": ""}],
                 "owners": [owner] if owner else []}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False)

    def _walk_to(self, stage):
        """Fresh device walked down the machine to `stage` with a green
        estelam row and an owner attached before `handover`."""
        record, errors = crm.add_device({"name": "D", "serial1": self._serial()})
        self.assertEqual(errors, [])
        device_id = record["id"]
        self._seed_green(device_id)
        actions = list(self.ACTION_PATH[stage])
        if "handover" in actions and "attach_owner" not in actions:
            actions.insert(actions.index("handover"), "attach_owner")
        for action in actions:
            if action == "attach_owner":
                owner = self._add_owner()
                _record, errors = crm.advance(device_id, "attach_owner",
                                              owner_id=owner["id"])
            elif action == "close_case":
                _record, errors = crm.advance(device_id, action,
                                              reason="آزمون")
            else:
                _record, errors = crm.advance(device_id, action)
            self.assertEqual(errors, [])
        device = self._device(device_id)
        self.assertEqual(device["stage"], stage)
        return device


class StageBackFromDoneTests(_StoreHarness):
    """User item 3: rollback from `done` is a legal operator action."""

    def test_stage_back_from_done_returns_to_payment_done(self):
        device = self._walk_to("done")
        record, errors = crm.advance(device["id"], "stage_back")
        self.assertEqual(errors, [])
        self.assertEqual(record["stage"], "payment_done")

    def test_stage_back_from_done_clears_completion_timestamps(self):
        device = self._walk_to("done")
        self.assertTrue(self._device(device["id"])["code_delivered_at"])
        self.assertTrue(self._device(device["id"])["done_at"])
        _record, errors = crm.advance(device["id"], "stage_back")
        self.assertEqual(errors, [])
        row = self._device(device["id"])
        self.assertEqual(row["code_delivered_at"], "")
        self.assertEqual(row["done_at"], "")

    def test_deliver_code_possible_again_after_rollback(self):
        device = self._walk_to("done")
        _record, errors = crm.advance(device["id"], "stage_back")
        self.assertEqual(errors, [])
        record, errors = crm.advance(device["id"], "deliver_code")
        self.assertEqual(errors, [])
        self.assertEqual(record["stage"], "done")
        self.assertTrue(record["done_at"])
        self.assertEqual(self._device(device["id"])["stage"], "done")

    def test_stage_back_from_closed_still_illegal(self):
        self._raw_store(self.crm_store, "closed")
        _record, errors = crm.advance("dev01", "stage_back")
        self.assertTrue(errors)
        self.assertEqual(self._device("dev01")["stage"], "closed")


class SkipCannotCompleteTests(_StoreHarness):
    """User item 12: no skip path may reach `done`; `deliver_code` is the
    only action that completes a case."""

    def test_skip_from_payment_done_rejected(self):
        device = self._walk_to("payment_done")
        _record, errors = crm.advance(device["id"], "stage_skip")
        self.assertTrue(errors)
        self.assertEqual(self._device(device["id"])["stage"], "payment_done")

    def test_skip_from_done_rejected(self):
        self._raw_store(self.crm_store, "done")
        _record, errors = crm.advance("dev01", "stage_skip")
        self.assertTrue(errors)
        self.assertEqual(self._device("dev01")["stage"], "done")

    def test_deliver_code_is_the_only_completion_action(self):
        # At payment_done every other action must be checked on a FRESH
        # device; none of them may end at `done`.
        for action in [a for a in ALL_ACTIONS if a != "deliver_code"]:
            with self.subTest(action=action):
                device = self._walk_to("payment_done")
                _record, _errors = crm.advance(
                    device["id"], action, reason="آزمون")
                self.assertNotEqual(self._device(device["id"])["stage"],
                                    "done")
        device = self._walk_to("payment_done")
        record, errors = crm.advance(device["id"], "deliver_code")
        self.assertEqual(errors, [])
        self.assertEqual(record["stage"], "done")


class CompletionInvariantTests(_StoreHarness):
    """User item 12 invariant, exhaustive over stage x action.

    Layer 1 (synthetic): raw devices at every one of the 10 stages in a
    fresh store per cell — gate-checked actions fail closed here.
    Layer 2 (walked): devices genuinely walked to each stage (green estelam,
    owner attached), so gate-checked actions (mark_ready, handover) can also
    succeed and the invariant is exercised on success paths too.
    """

    def _assert_cell(self, stage, action, device_id, errors):
        row = self._device(device_id)
        if errors:
            # A rejected action must never move the device.
            self.assertEqual(
                row["stage"], stage,
                f"{stage} + {action}: errored but stage moved to "
                f"{row['stage']}")
        else:
            if action != "deliver_code":
                self.assertNotEqual(
                    row["stage"], "done",
                    f"{stage} + {action}: reached done without deliver_code")
            if action != "close_case":
                self.assertNotEqual(
                    row["stage"], "closed",
                    f"{stage} + {action}: reached closed without close_case")

    def test_synthetic_matrix_never_done_or_closed_unexpectedly(self):
        owner_record = dict(_synthetic_owner_fields(0), id="own01")
        for stage in ALL_STAGES:
            for action in ALL_ACTIONS:
                with self.subTest(stage=stage, action=action):
                    store_path = os.path.join(
                        self.tmp.name, f"synth_{stage}_{action}.json")
                    self._raw_store(store_path, stage, owner=owner_record)
                    os.environ["IRICA_CRM_STORE"] = store_path
                    _device, errors = crm.advance(
                        "dev01", action, reason="آزمون", owner_id="own01")
                    self._assert_cell(stage, action, "dev01", errors)

    def test_walked_matrix_never_done_or_closed_unexpectedly(self):
        for stage in ALL_STAGES:
            for action in ALL_ACTIONS:
                with self.subTest(stage=stage, action=action):
                    device = self._walk_to(stage)
                    owner = self._add_owner()
                    _record, errors = crm.advance(
                        device["id"], action, reason="آزمون",
                        owner_id=owner["id"])
                    self._assert_cell(stage, action, device["id"], errors)


if __name__ == "__main__":
    unittest.main()
