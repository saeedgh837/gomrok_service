# -*- coding: utf-8 -*-
"""Cloud sync state machine and offline lease (workstream A).

Contract: `lab/docs/CLOUD_SYNC_SPEC.md` — §5 (auth + the 7-day offline
lease), §9 (observable sync state), §11 (status surface), §12 (offline tests
only), §13 (these three files and nothing else).

What is pinned here:

* the transition table is checked **exhaustively** — every legal pair moves to
  the state the contract says, and every other pair (all 104 of them, at zero
  and at two pending conflicts) raises instead of falling through;
* serialise -> restore round-trips, and anything unknown, malformed or
  unreadable on disk fails CLOSED (never to `syncing`);
* the lease expires at exactly 7 x 24 h under an injected clock — no sleep,
  no wall clock, boundary asserted one second either side;
* `assert_writable()` refuses edits after expiry and allows them before;
* both persisted files land in a throwaway `IRICA_DATA_DIR` and a token or
  PII-shaped value never reaches a state dict, a message or a file.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_cloud_state
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import app_paths  # noqa: E402
from cloud_sync.state import (  # noqa: E402
    SCHEMA_VERSION, SyncEvent, SyncState, SyncStateMachine,
    IllegalTransitionError, KNOWN_ERROR_CODES, LEASE_EXPIRED_CODE,
    STATE_FILE_NAME, TARGET_RECOVER, TARGET_RESOLVE, TARGET_RESUME,
    TRANSITIONS, UnknownEventError, validate_error_code,
)
from cloud_sync.lease import (  # noqa: E402
    LEASE_EXPIRED, LEASE_FILE_NAME, LEASE_SECONDS, LeaseExpiredError,
    OfflineLease, REASON_EXPIRED, REASON_MALFORMED, REASON_NEVER_GRANTED,
    REASON_UNREADABLE,
)
from cloud_sync.state import (  # noqa: E402
    REASON_INCONSISTENT, REASON_INTERRUPTED,
    REASON_MALFORMED as STATE_MALFORMED, REASON_NO_CONFLICT,
    REASON_NOT_LEGAL, REASON_UNREADABLE as STATE_UNREADABLE, REASON_UNKNOWN,
)

#: Integer epoch, so the 7-day boundary is exact in floating point.
CLOCK = 1_800_000_000
#: A full payload: every field SCHEMA_VERSION requires, no more.
FIELDS = ("state", "disabled_from", "last_successful_sync", "pending_changes",
          "conflict_count", "last_error_code", "restore_reason")

# Shapes that must never appear in a state dict or an exception message.
# All of them are realistic: a JWT, an email, an Iranian mobile number and a
# bearer header. None is a real credential — this is synthetic test data.
PII_SAMPLES = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP",
    "operator@example.com",
    "09123456789",
    "Bearer sk-live-abcdef123456",
    "C:\\Users\\Public\\irica\\auth.dat",
)
PII_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]+"),                 # JWT header
    re.compile(r"Bearer\s"),                          # authorization header
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+"),  # e-mail
    re.compile(r"\b09\d{9}\b"),                       # mobile number
    re.compile(r"[A-Za-z]:\\"),                       # a local file path
)


def assert_no_pii(testcase, text, where):
    """No token-, e-mail-, phone- or path-shaped value in `text`."""
    for pattern in PII_PATTERNS:
        testcase.assertIsNone(pattern.search(text),
                              "%s leaked %s" % (where, pattern.pattern))
    testcase.assertTrue(text.isascii(), "%s is not plain English/ASCII" % where)


def payload(**overrides):
    """A complete, valid restore payload (spec: every field of the version)."""
    data = {
        "version": SCHEMA_VERSION,
        "state": SyncState.NOT_ENROLLED.value,
        "disabled_from": None,
        "last_successful_sync": None,
        "pending_changes": 0,
        "conflict_count": 0,
        "last_error_code": None,
        "restore_reason": "",
    }
    data.update(overrides)
    return data


def at(state, conflicts=0, disabled_from=None, **overrides):
    """A machine standing in `state`, built the way a restart would build it.

    `syncing` cannot be restored directly (a batch does not survive a
    restart), so it is restored as idle and then started — which is also how
    the panel reaches it in production.
    """
    target = SyncState(state) if not isinstance(state, SyncState) else state
    machine = SyncStateMachine.from_dict(payload(
        state=target.value, conflict_count=conflicts,
        disabled_from=disabled_from, **overrides), clock=lambda: CLOCK)
    if target is SyncState.SYNCING and machine.state is not SyncState.SYNCING:
        machine.apply(SyncEvent.START_SYNC)
    return machine


class _DataDirIsolation(unittest.TestCase):
    """Every write lands in a throwaway data directory.

    patch.dict(IRICA_DATA_DIR) alone is not enough: app_paths caches the
    resolved directory on its first call, so the cache is reset with the
    environment and reset again on the way out.
    """

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)
        self.data_tmp = self._data_tmp.name
        self._env = patch.dict(os.environ, {"IRICA_DATA_DIR": self.data_tmp})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(app_paths.reset_cache)
        app_paths.reset_cache()
        self.assertEqual(os.path.abspath(app_paths.data_dir()),
                         os.path.abspath(self.data_tmp))

    def files_written(self):
        """Everything this test created under the data directory."""
        found = []
        for base, _dirs, names in os.walk(self.data_tmp):
            found += [os.path.join(base, n) for n in names]
        return found


# =====================================================================
# The shape of the contract
# =====================================================================
class ContractShapeTests(unittest.TestCase):
    def test_states_are_exactly_the_eight_the_contract_requires(self):
        # §5 + workstream brief: not-enrolled, idle, syncing, offline,
        # conflict, auth-required, read-only, disabled.
        self.assertEqual([s.value for s in SyncState], [
            "not_enrolled", "idle", "syncing", "offline", "conflict",
            "auth_required", "read_only", "disabled",
        ])

    def test_state_values_are_unique_and_stable(self):
        values = [s.value for s in SyncState]
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(SyncState("read_only"), SyncState.READ_ONLY)

    def test_events_are_unique_and_addressable_by_value(self):
        values = [e.value for e in SyncEvent]
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(SyncEvent("start_sync"), SyncEvent.START_SYNC)

    def test_table_mentions_only_known_states_and_events(self):
        for source, row in TRANSITIONS.items():
            self.assertIsInstance(source, SyncState)
            for event, target in row.items():
                self.assertIsInstance(event, SyncEvent)
                self.assertTrue(
                    isinstance(target, SyncState)
                    or target in (TARGET_RECOVER, TARGET_RESOLVE,
                                  TARGET_RESUME),
                    "unexpected target %r" % (target,))

    def test_every_state_can_be_reached_and_every_state_has_an_exit(self):
        reachable = set()
        for row in TRANSITIONS.values():
            for target in row.values():
                if isinstance(target, SyncState):
                    reachable.add(target)
                elif target == TARGET_RECOVER:
                    reachable.update((SyncState.IDLE, SyncState.CONFLICT))
                elif target == TARGET_RESOLVE:
                    reachable.update((SyncState.IDLE, SyncState.CONFLICT))
                else:
                    reachable.add(SyncState.NOT_ENROLLED)
        self.assertEqual(reachable, set(SyncState))
        for source in SyncState:
            self.assertTrue(TRANSITIONS[source], "%s has no exit" % source)

    def test_every_event_is_legal_somewhere(self):
        used = set()
        for row in TRANSITIONS.values():
            used.update(row)
        self.assertEqual(used, set(SyncEvent))

    def test_recovery_events_exist_for_every_fault(self):
        # Each fault state has exactly the recovery that may clear it.
        self.assertIn(SyncEvent.NETWORK_RESTORED,
                      TRANSITIONS[SyncState.OFFLINE])
        self.assertIn(SyncEvent.AUTH_OK, TRANSITIONS[SyncState.AUTH_REQUIRED])
        self.assertIn(SyncEvent.AUTH_OK, TRANSITIONS[SyncState.READ_ONLY])
        self.assertIn(SyncEvent.CONFLICTS_RESOLVED,
                      TRANSITIONS[SyncState.CONFLICT])

    def test_only_a_renewed_lease_makes_a_read_only_machine_offerable(self):
        # READ_ONLY may be left for a fault or for DISABLE; the only event
        # that hands the operator an editable machine back is AUTH_OK (the
        # lease is granted exactly there — spec §5).
        offering = (SyncState.IDLE, SyncState.CONFLICT, SyncState.SYNCING)
        left_by = set()
        for event in SyncEvent:
            machine = at("read_only", conflicts=1, disabled_from="idle")
            if not machine.can(event):
                continue
            got = _apply(machine, "read_only", event)
            if got in offering:
                left_by.add(event)
        self.assertEqual(left_by, {SyncEvent.AUTH_OK})


# =====================================================================
# Exhaustive transitions: legal pairs, and every illegal pair
# =====================================================================
# Written out here as an independent statement of the contract; the
# implementation's own TRANSITIONS is not read to decide what is legal.
R, S, V = TARGET_RECOVER, TARGET_RESOLVE, TARGET_RESUME
LEGAL = {
    "not_enrolled": {"enrol": R, "disable": "disabled"},
    "idle": {
        "start_sync": "syncing", "network_lost": "offline",
        "network_restored": "idle", "auth_failed": "auth_required",
        "auth_ok": "idle", "conflict_found": "conflict",
        "lease_expired": "read_only", "disable": "disabled",
    },
    "syncing": {
        "sync_ok": R, "sync_failed": R, "network_lost": "offline",
        "network_restored": "syncing", "auth_failed": "auth_required",
        "auth_ok": "syncing", "conflict_found": "conflict",
        "conflicts_resolved": S, "lease_expired": "read_only",
        "disable": "disabled",
    },
    "offline": {
        "network_lost": "offline", "network_restored": R,
        "auth_failed": "auth_required", "auth_ok": "offline",
        "conflicts_resolved": S, "lease_expired": "read_only",
        "disable": "disabled",
    },
    "conflict": {
        "sync_ok": R, "sync_failed": R, "start_sync": "syncing",
        "network_lost": "offline", "network_restored": "conflict",
        "auth_failed": "auth_required", "auth_ok": "conflict",
        "conflict_found": "conflict", "conflicts_resolved": S,
        "lease_expired": "read_only", "disable": "disabled",
    },
    "auth_required": {
        "network_lost": "auth_required", "network_restored": "auth_required",
        "auth_failed": "auth_required", "auth_ok": R,
        "conflicts_resolved": S, "lease_expired": "read_only",
        "disable": "disabled",
    },
    "read_only": {
        "network_lost": "read_only", "network_restored": "read_only",
        "auth_failed": "auth_required", "auth_ok": R,
        "conflicts_resolved": S, "lease_expired": "read_only",
        "disable": "disabled",
    },
    "disabled": {"enable": V},
}


def _apply(machine, state_name, event_name):
    """Fire one event with the arguments that event is allowed to take."""
    kwargs = {}
    if event_name == "sync_failed":
        kwargs["error_code"] = "HTTP_500"
    if event_name == "conflicts_resolved":
        kwargs["conflicts_cleared"] = 1
    return machine.apply(SyncEvent(event_name), **kwargs)


def _expected(marker, state_name, conflicts, disabled_from):
    """Resolve the table's tokens the way the contract says they resolve."""
    if marker == R:
        return "conflict" if conflicts else "idle"
    if marker == S:
        if conflicts == 0 and state_name == "conflict":
            return "idle"
        return state_name
    if marker == V:
        return disabled_from or "not_enrolled"
    return marker


class LegalTransitionTests(unittest.TestCase):
    """Every legal pair, at zero pending conflicts."""

    def test_every_legal_transition_moves_where_the_contract_says(self):
        checked = 0
        for state_name, row in LEGAL.items():
            for event_name, marker in row.items():
                if event_name == "conflicts_resolved":
                    continue                      # needs a pending conflict
                with self.subTest(state=state_name, event=event_name):
                    machine = at(state_name, disabled_from="idle")
                    got = _apply(machine, state_name, event_name)
                    self.assertEqual(
                        got.value,
                        _expected(marker, state_name, 0, "idle"))
                    self.assertEqual(machine.state, got)
                    checked += 1
        # 2 + 8 + 9 + 6 + 10 + 6 + 6 + 1 pairs (conflicts_resolved, which
        # needs a pending conflict, is walked in the next test).
        self.assertEqual(checked, 48)

    def test_every_legal_conflicts_resolved_returns_to_idle(self):
        for state_name, row in LEGAL.items():
            if "conflicts_resolved" not in row:
                continue
            with self.subTest(state=state_name):
                machine = at(state_name, conflicts=1, disabled_from="idle")
                got = _apply(machine, state_name, "conflicts_resolved")
                expected = "idle" if state_name == "conflict" else state_name
                self.assertEqual(got.value, expected)
                self.assertEqual(machine.conflict_count, 0)

    def test_every_legal_transition_at_two_pending_conflicts(self):
        # The guard and the tokens both depend on the conflict count, so the
        # whole table is walked a second time with work still waiting.
        for state_name, row in LEGAL.items():
            if state_name == "idle":
                continue     # IDLE with pending conflicts is not restorable
            for event_name, marker in row.items():
                with self.subTest(state=state_name, event=event_name):
                    # disabled_from is "offline", not "idle": ENABLE out of a
                    # machine that still has conflicts must not try to park it
                    # in IDLE (the invariant sends it to CONFLICT instead).
                    machine = at(state_name, conflicts=2,
                                 disabled_from="offline")
                    got = _apply(machine, state_name, event_name)
                    expected = _expected(marker, state_name,
                                         machine.conflict_count, "offline")
                    if marker == S:
                        expected = state_name   # one of the two was cleared
                    self.assertEqual(got.value, expected)
                    if event_name == "conflicts_resolved":
                        self.assertEqual(machine.conflict_count, 1)


class IllegalTransitionTests(unittest.TestCase):
    """Every pair that is not in the table raises, at zero conflicts."""

    def test_every_illegal_transition_raises(self):
        checked = 0
        for state_name, row in LEGAL.items():
            for event_name in (e.value for e in SyncEvent):
                if event_name in row and event_name != "conflicts_resolved":
                    continue
                with self.subTest(state=state_name, event=event_name):
                    machine = at(state_name, disabled_from="idle")
                    with self.assertRaises(IllegalTransitionError) as ctx:
                        _apply(machine, state_name, event_name)
                    self.assertEqual(machine.state, SyncState(state_name),
                                     "a refused event must not move the state")
                    checked += 1
        # 104 cells: 48 legal without the guard, 5 guarded pairs that are
        # illegal while nothing is pending, 51 cells that are never legal.
        self.assertEqual(checked, 56)

    def test_every_illegal_transition_raises_with_conflicts_pending(self):
        # Same walk with work waiting: only the guard entries flip, so the
        # count drops from 51 (the non-idle part of the walk above) to 46 —
        # the 5 guarded pairs are legal now.
        checked = 0
        for state_name, row in LEGAL.items():
            if state_name == "idle":
                continue
            for event_name in (e.value for e in SyncEvent):
                if event_name in row:
                    continue
                with self.subTest(state=state_name, event=event_name):
                    machine = at(state_name, conflicts=2,
                                 disabled_from="idle")
                    with self.assertRaises(IllegalTransitionError):
                        _apply(machine, state_name, event_name)
                    checked += 1
        self.assertEqual(checked, 46)

    def test_resolving_without_a_pending_conflict_is_refused(self):
        for state_name, row in LEGAL.items():
            if "conflicts_resolved" not in row:
                continue
            with self.subTest(state=state_name):
                machine = at(state_name, conflicts=0, disabled_from="idle")
                with self.assertRaises(IllegalTransitionError) as ctx:
                    _apply(machine, state_name, "conflicts_resolved")
                self.assertEqual(ctx.exception.reason, REASON_NO_CONFLICT)
                self.assertEqual(machine.conflict_count, 0)

    def test_illegal_transition_reports_a_stable_reason(self):
        machine = at("offline")
        with self.assertRaises(IllegalTransitionError) as ctx:
            machine.apply(SyncEvent.START_SYNC)
        self.assertEqual(ctx.exception.reason, REASON_NOT_LEGAL)
        self.assertEqual(ctx.exception.state, SyncState.OFFLINE)
        self.assertEqual(ctx.exception.event, SyncEvent.START_SYNC)

    def test_message_carries_only_our_own_identifiers(self):
        machine = at("read_only")
        with self.assertRaises(IllegalTransitionError) as ctx:
            machine.apply(SyncEvent.START_SYNC)
        text = str(ctx.exception)
        self.assertIn("read_only", text)
        self.assertIn(REASON_NOT_LEGAL, text)
        assert_no_pii(self, text, "IllegalTransitionError")

    def test_an_event_that_does_not_exist_raises_without_echoing_it(self):
        machine = at("idle")
        for bogus in ("eyJhbGciOiJIUzI1NiJ9.secret.value",
                      "operator@example.com", "09123456789",
                      object(), None, 7):
            with self.subTest(bogus=type(bogus).__name__):
                with self.assertRaises(UnknownEventError) as ctx:
                    machine.apply(bogus)
                self.assertNotIn("eyJ", str(ctx.exception))
                assert_no_pii(self, str(ctx.exception), "UnknownEventError")
                self.assertEqual(machine.state, SyncState.IDLE)

    def test_can_matches_apply_for_every_cell(self):
        for state_name in LEGAL:
            for event in SyncEvent:
                with self.subTest(state=state_name, event=event.value):
                    machine = at(state_name, disabled_from="idle")
                    self.assertEqual(
                        machine.can(event),
                        event.value in LEGAL[state_name]
                        and not (event is SyncEvent.CONFLICTS_RESOLVED
                                 and machine.conflict_count < 1))

    def test_can_refuses_a_name_that_does_not_exist(self):
        machine = at("idle")
        with self.assertRaises(UnknownEventError):
            machine.can("not-an-event")


# =====================================================================
# Semantics the table alone does not show
# =====================================================================
class LifecycleTests(unittest.TestCase):
    def test_a_fresh_machine_is_unenrolled_and_silent(self):
        machine = SyncStateMachine(clock=lambda: CLOCK)
        self.assertEqual(machine.state, SyncState.NOT_ENROLLED)
        self.assertEqual(machine.restore_reason, "")
        self.assertEqual(machine.last_error_code, None)
        self.assertEqual(machine.last_successful_sync, None)

    def test_enrolment_is_the_only_way_in(self):
        machine = SyncStateMachine(clock=lambda: CLOCK)
        self.assertFalse(machine.can(SyncEvent.START_SYNC))
        machine.apply(SyncEvent.ENROL)
        self.assertEqual(machine.state, SyncState.IDLE)
        with self.assertRaises(IllegalTransitionError):
            machine.apply(SyncEvent.ENROL)      # already enrolled

    def test_disable_remembers_where_it_came_from_and_enable_returns(self):
        for origin in ("idle", "conflict", "read_only"):
            with self.subTest(origin=origin):
                machine = at(origin, disabled_from="idle")
                machine.apply(SyncEvent.DISABLE)
                self.assertEqual(machine.state, SyncState.DISABLED)
                self.assertEqual(machine.disabled_from, SyncState(origin))
                self.assertEqual(machine.apply(SyncEvent.ENABLE),
                                 SyncState(origin))

    def test_enable_on_a_machine_that_was_never_disabled_refuses(self):
        machine = at("idle")
        with self.assertRaises(IllegalTransitionError):
            machine.apply(SyncEvent.ENABLE)
        machine.apply(SyncEvent.DISABLE)
        with self.assertRaises(IllegalTransitionError):
            machine.apply(SyncEvent.DISABLE)    # already disabled

    def test_a_disabled_machine_does_nothing_at_all(self):
        machine = at("idle")
        machine.apply(SyncEvent.DISABLE)
        for event in SyncEvent:
            if event is SyncEvent.ENABLE:
                continue
            with self.subTest(event=event.value):
                with self.assertRaises(IllegalTransitionError):
                    machine.apply(event)
                self.assertEqual(machine.state, SyncState.DISABLED)

    def test_start_sync_is_refused_where_a_batch_could_not_run(self):
        for state_name in ("offline", "auth_required", "read_only", "syncing"):
            with self.subTest(state=state_name):
                machine = at(state_name, disabled_from="idle")
                with self.assertRaises(IllegalTransitionError):
                    machine.apply(SyncEvent.START_SYNC)

    def test_an_expired_lease_survives_a_network_blip(self):
        machine = at("read_only", disabled_from="idle")
        machine.apply(SyncEvent.NETWORK_LOST)
        self.assertEqual(machine.state, SyncState.READ_ONLY)
        machine.apply(SyncEvent.NETWORK_RESTORED)
        self.assertEqual(machine.state, SyncState.READ_ONLY)
        # Only a renewed lease makes it writable again.
        self.assertEqual(machine.apply(SyncEvent.AUTH_OK), SyncState.IDLE)

    def test_a_dead_authentication_is_not_cleared_by_the_socket(self):
        machine = at("auth_required", disabled_from="idle")
        machine.apply(SyncEvent.NETWORK_LOST)
        self.assertEqual(machine.state, SyncState.AUTH_REQUIRED)
        machine.apply(SyncEvent.NETWORK_RESTORED)
        self.assertEqual(machine.state, SyncState.AUTH_REQUIRED)
        self.assertEqual(machine.apply(SyncEvent.AUTH_OK), SyncState.IDLE)

    def test_offline_is_reported_once_and_restored_once(self):
        machine = at("idle")
        machine.apply(SyncEvent.NETWORK_LOST)
        machine.apply(SyncEvent.NETWORK_LOST)          # idempotent
        self.assertEqual(machine.state, SyncState.OFFLINE)
        self.assertEqual(machine.apply(SyncEvent.NETWORK_RESTORED),
                         SyncState.IDLE)

    def test_a_batch_that_found_conflicts_may_still_report_its_outcome(self):
        # Either order with CONFLICT_FOUND must work: the batch completed
        # and the conflicts are a separate, operator-owned fact.
        first = at("syncing")
        first.apply(SyncEvent.CONFLICT_FOUND)
        self.assertEqual(first.apply(SyncEvent.SYNC_OK), SyncState.CONFLICT)
        self.assertIsNotNone(first.last_successful_sync)

        second = at("syncing")
        second.apply(SyncEvent.SYNC_OK)
        self.assertEqual(second.state, SyncState.IDLE)
        second.apply(SyncEvent.CONFLICT_FOUND)
        self.assertEqual(second.state, SyncState.CONFLICT)

    def test_conflicts_are_never_auto_resolved_and_idle_never_holds_them(self):
        machine = at("idle")
        machine.apply(SyncEvent.CONFLICT_FOUND)
        machine.apply(SyncEvent.CONFLICT_FOUND)
        self.assertEqual(machine.conflict_count, 2)
        self.assertEqual(machine.state, SyncState.CONFLICT)
        # Two separate resolutions are needed, one at a time.
        machine.apply(SyncEvent.CONFLICTS_RESOLVED, conflicts_cleared=1)
        self.assertEqual(machine.state, SyncState.CONFLICT)
        self.assertEqual(machine.apply(SyncEvent.CONFLICTS_RESOLVED),
                         SyncState.IDLE)

    def test_clearing_more_conflicts_than_are_pending_is_refused(self):
        machine = at("conflict", conflicts=1, disabled_from="idle")
        with self.assertRaises(IllegalTransitionError) as ctx:
            machine.apply(SyncEvent.CONFLICTS_RESOLVED, conflicts_cleared=2)
        self.assertEqual(ctx.exception.reason, REASON_NO_CONFLICT)
        self.assertEqual(machine.conflict_count, 1)

    def test_a_conflict_keeps_syncing_around_it(self):
        machine = at("conflict", conflicts=1, disabled_from="idle")
        self.assertEqual(machine.apply(SyncEvent.START_SYNC), SyncState.SYNCING)
        self.assertEqual(machine.apply(SyncEvent.SYNC_OK), SyncState.CONFLICT)
        self.assertEqual(machine.conflict_count, 1)

    def test_only_the_right_event_may_carry_an_error_code(self):
        machine = at("syncing")
        for event in (SyncEvent.NETWORK_LOST, SyncEvent.AUTH_OK,
                      SyncEvent.CONFLICT_FOUND):
            with self.subTest(event=event.value):
                with self.assertRaises(ValueError):
                    machine.apply(event, error_code="HTTP_500")
        with self.assertRaises(ValueError):
            machine.apply(SyncEvent.SYNC_OK, conflicts_cleared=1)
        with self.assertRaises(ValueError):
            machine.apply(SyncEvent.NETWORK_LOST, conflicts_cleared=1)


# =====================================================================
# Observables (spec §9)
# =====================================================================
class ObservableTests(unittest.TestCase):
    def test_sync_ok_stamps_the_injected_clock_in_utc_iso(self):
        machine = at("syncing")
        machine.apply(SyncEvent.SYNC_OK)
        self.assertEqual(machine.last_successful_sync[:4], "2027")
        self.assertTrue(machine.last_successful_sync.endswith("+00:00"))
        # The same instant, restated: no wall clock was consulted.
        self.assertEqual(machine.last_successful_sync,
                         SyncStateMachine.from_dict(
                             machine.to_dict(), clock=lambda: CLOCK
                         ).last_successful_sync)

    def test_sync_ok_retires_the_previous_error(self):
        machine = at("syncing")
        machine.apply(SyncEvent.SYNC_FAILED, error_code="HTTP_500")
        self.assertEqual(machine.last_error_code, "HTTP_500")
        machine.apply(SyncEvent.START_SYNC)
        machine.apply(SyncEvent.SYNC_OK)
        self.assertEqual(machine.last_error_code, None)

    def test_a_no_op_recovery_does_not_hide_a_live_error(self):
        machine = at("auth_required", disabled_from="idle")
        machine.apply(SyncEvent.AUTH_FAILED, error_code="AUTH_EXPIRED")
        self.assertEqual(machine.last_error_code, "AUTH_EXPIRED")
        machine.apply(SyncEvent.NETWORK_RESTORED)   # not the fault we hold
        self.assertEqual(machine.last_error_code, "AUTH_EXPIRED")
        machine.apply(SyncEvent.AUTH_OK)
        self.assertEqual(machine.last_error_code, None)

    def test_sync_failed_requires_a_code(self):
        machine = at("syncing")
        with self.assertRaises(ValueError):
            machine.apply(SyncEvent.SYNC_FAILED)
        self.assertEqual(machine.state, SyncState.SYNCING)  # nothing moved

    def test_an_expired_lease_records_its_stable_code(self):
        machine = at("idle")
        machine.apply(SyncEvent.LEASE_EXPIRED)
        self.assertEqual(machine.last_error_code, LEASE_EXPIRED_CODE)
        self.assertEqual(machine.last_error_code, LEASE_EXPIRED)

    def test_the_outbox_depth_is_validated(self):
        machine = at("idle")
        machine.set_pending(4)
        self.assertEqual(machine.pending_changes, 4)
        self.assertEqual(machine.add_pending(-2), 2)
        for bad in (-1, "3", 2.5, True, None):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    machine.set_pending(bad)
        with self.assertRaises(ValueError):
            machine.add_pending(1.5)
        self.assertEqual(machine.pending_changes, 2)

    def test_pending_count_is_not_untouched_by_a_sync_outcome(self):
        # The outbox owns the counter; a transition must not silently zero it.
        machine = at("syncing")
        machine.set_pending(7)
        machine.apply(SyncEvent.SYNC_OK)
        self.assertEqual(machine.pending_changes, 7)

    def test_error_codes_are_accepted_when_they_are_identifiers(self):
        for code in sorted(KNOWN_ERROR_CODES):
            self.assertEqual(validate_error_code(code), code)
        for code in ("HTTP_500", "NET_TIMEOUT", "VERSION_CONFLICT",
                     "QUEUE_FULL", "A_BC"):
            self.assertEqual(validate_error_code(code), code)

    def test_error_codes_reject_anything_that_could_be_data(self):
        for value in PII_SAMPLES + ("", "lower_case", "HTTP 500", "HTTP500",
                                    "A", "TOO_MANY_" + "X" * 40, "12345",
                                    "Lease_Expired"):
            with self.subTest(value=value[:24]):
                with self.assertRaises(ValueError) as ctx:
                    validate_error_code(value)
                assert_no_pii(self, str(ctx.exception), "validate_error_code")
        # Nothing that looks like operator data is quoted back at the caller:
        # the refusal names the field and the accepted shape, never the value.
        for sample in PII_SAMPLES:
            with self.subTest(sample=sample[:24]):
                with self.assertRaises(ValueError) as ctx:
                    validate_error_code(sample)
                self.assertNotIn(sample, str(ctx.exception))

    def test_a_pii_error_code_never_reaches_the_machine(self):
        machine = at("syncing")
        for sample in PII_SAMPLES:
            with self.subTest(sample=sample[:24]):
                with self.assertRaises(ValueError):
                    machine.apply(SyncEvent.SYNC_FAILED, error_code=sample)
                self.assertEqual(machine.state, SyncState.SYNCING)
                self.assertEqual(machine.last_error_code, None)


# =====================================================================
# Serialise -> restore, and failing closed
# =====================================================================
class SerialisationTests(unittest.TestCase):
    def test_round_trip_through_json_preserves_every_observable(self):
        machine = at("conflict", conflicts=3, disabled_from="idle")
        machine.set_pending(9)
        machine.apply(SyncEvent.SYNC_FAILED, error_code="NET_TIMEOUT")
        wire = json.loads(json.dumps(machine.to_dict()))
        restored = SyncStateMachine.from_dict(wire, clock=lambda: CLOCK)
        self.assertEqual(restored.to_dict(), machine.to_dict())
        self.assertEqual(restored.state, SyncState.CONFLICT)
        self.assertEqual(restored.conflict_count, 3)
        self.assertEqual(restored.pending_changes, 9)
        self.assertEqual(restored.last_error_code, "NET_TIMEOUT")

    def test_to_dict_is_plain_json_with_no_objects(self):
        machine = at("read_only", disabled_from="conflict")
        data = machine.to_dict()
        self.assertEqual(sorted(data),
                         sorted(("version", "state", "disabled_from",
                                 "last_successful_sync", "pending_changes",
                                 "conflict_count", "last_error_code",
                                 "restore_reason")))
        for key, value in data.items():
            self.assertIn(type(value), (str, int, type(None)),
                          "%s is not JSON-safe" % key)
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_a_restored_timestamp_is_canonical_utc(self):
        machine = at("syncing")
        machine.apply(SyncEvent.SYNC_OK)
        first = machine.last_successful_sync
        again = SyncStateMachine.from_dict(
            machine.to_dict()).last_successful_sync
        self.assertEqual(again, first)
        self.assertIn("+00:00", again)

    def test_a_syncing_payload_is_downgraded_to_idle(self):
        # A batch does not survive a restart; keeping it would make the
        # machine refuse START_SYNC forever with no worker to complete it.
        restored = SyncStateMachine.from_dict(payload(state="syncing"))
        self.assertEqual(restored.state, SyncState.IDLE)
        self.assertEqual(restored.restore_reason, REASON_INTERRUPTED)

    def test_a_payload_naming_an_unknown_state_fails_closed(self):
        cases = [(v, {"state": v}) for v in
                 ("syncing_right_now", "SYNCING", "", "Idle", "paused")]
        # An unknown disabled_from is unknown too: it names a state this
        # build does not have, so the machine cannot know where ENABLE goes.
        cases.append(("paused_state", {"disabled_from": "paused_state"}))
        for value, overrides in cases:
            with self.subTest(value=value):
                restored = SyncStateMachine.from_dict(payload(**overrides))
                self.assertEqual(restored.state, SyncState.NOT_ENROLLED)
                self.assertEqual(restored.restore_reason, REASON_UNKNOWN)
                if value:
                    self.assertNotIn(value, json.dumps(restored.to_dict()))

    def test_malformed_payloads_fail_closed_to_disabled(self):
        broken = [
            None, [], "idle", 42,
            payload(version=2),
            payload(version="1"),
            payload(version=True),
            payload(state=None),
            payload(state=7),
            payload(disabled_from="disabled"),
            payload(disabled_from=3),
            payload(pending_changes=-1),
            payload(pending_changes=True),
            payload(pending_changes=1.5),
            payload(conflict_count=-2),
            payload(conflict_count="0"),
            payload(last_error_code="net timeout"),
            payload(last_successful_sync="yesterday"),
            payload(last_successful_sync="2027-01-15T00:00:00"),  # no zone
            payload(restore_reason="operator said so"),
            {"version": SCHEMA_VERSION, "state": "idle"},
        ]
        for bad in broken:
            with self.subTest(payload=repr(bad)[:60]):
                restored = SyncStateMachine.from_dict(bad)
                self.assertEqual(restored.state, SyncState.DISABLED)
                self.assertEqual(restored.restore_reason, STATE_MALFORMED)
                self.assertEqual(restored.pending_changes, 0)
                self.assertEqual(restored.conflict_count, 0)
                self.assertEqual(restored.last_error_code, None)
                self.assertEqual(restored.last_successful_sync, None)

    def test_a_malformed_payload_never_restores_into_syncing(self):
        for state_value in [s.value for s in SyncState] + ["nonsense"]:
            restored = SyncStateMachine.from_dict(payload(state=state_value))
            self.assertNotEqual(restored.state, SyncState.SYNCING)

    def test_a_disabled_payload_keeps_where_it_came_from(self):
        restored = SyncStateMachine.from_dict(
            payload(state="disabled", disabled_from="offline"))
        self.assertEqual(restored.state, SyncState.DISABLED)
        self.assertEqual(restored.disabled_from, SyncState.OFFLINE)
        self.assertEqual(restored.apply(SyncEvent.ENABLE), SyncState.OFFLINE)

    def test_idle_with_pending_conflicts_is_normalised_to_conflict(self):
        restored = SyncStateMachine.from_dict(
            payload(state="idle", conflict_count=2))
        self.assertEqual(restored.state, SyncState.CONFLICT)
        self.assertEqual(restored.restore_reason, REASON_INCONSISTENT)
        self.assertEqual(restored.conflict_count, 2)

    def test_fail_closed_keeps_nothing_from_a_poisoned_payload(self):
        poisoned = payload(state="idle", pending_changes=40,
                           conflict_count=5, last_error_code="HTTP_500",
                           last_successful_sync="not-a-time",
                           restore_reason="")
        restored = SyncStateMachine.from_dict(poisoned)
        self.assertEqual(restored.state, SyncState.DISABLED)
        self.assertEqual(restored.pending_changes, 0)
        self.assertEqual(restored.conflict_count, 0)
        self.assertEqual(restored.last_error_code, None)

    def test_pii_in_a_payload_never_survives_a_restore(self):
        for sample in PII_SAMPLES:
            for field in ("state", "restore_reason", "last_error_code",
                          "last_successful_sync"):
                with self.subTest(field=field, sample=sample[:24]):
                    restored = SyncStateMachine.from_dict(
                        payload(**{field: sample}))
                    blob = json.dumps(restored.to_dict())
                    assert_no_pii(self, blob, "restored state dict")
                    self.assertNotIn(sample, blob)


class StatePersistenceTests(_DataDirIsolation):
    def test_state_path_lands_in_the_data_directory(self):
        path = app_paths.data_path(STATE_FILE_NAME)
        self.assertEqual(os.path.abspath(os.path.dirname(path)),
                         os.path.abspath(self.data_tmp))
        self.assertNotIn(os.sep + "irica" + os.sep + "irica", path)

    def test_save_then_load_resumes_in_the_same_state(self):
        machine = at("auth_required", conflicts=2, disabled_from="idle")
        machine.set_pending(5)
        machine.apply(SyncEvent.AUTH_FAILED, error_code="AUTH_REVOKED")
        machine.save()

        path = app_paths.data_path(STATE_FILE_NAME)
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk["state"], "auth_required")

        restored = SyncStateMachine.load()
        self.assertEqual(restored.state, SyncState.AUTH_REQUIRED)
        self.assertEqual(restored.conflict_count, 2)
        self.assertEqual(restored.pending_changes, 5)
        self.assertEqual(restored.last_error_code, "AUTH_REVOKED")
        self.assertEqual(restored.restore_reason, "")
        self.assertEqual(restored.to_dict(), machine.to_dict())

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        at("idle").save()
        names = sorted(os.path.basename(p) for p in self.files_written())
        self.assertEqual(names, [STATE_FILE_NAME])

    def test_a_missing_file_is_a_fresh_unenrolled_machine(self):
        machine = SyncStateMachine.load()
        self.assertEqual(machine.state, SyncState.NOT_ENROLLED)
        self.assertEqual(machine.restore_reason, "")
        self.assertEqual(self.files_written(), [])

    def test_an_unreadable_file_fails_closed_to_disabled(self):
        # Text that is not JSON cannot be read at all (UNREADABLE_STATE);
        # text that is JSON but not a version-1 machine is rejected as
        # MALFORMED_STATE by the parser. Both fail closed to the same place.
        path = app_paths.data_path(STATE_FILE_NAME)
        for content, reason in (("{not json", STATE_UNREADABLE),
                                ('"just a string"', STATE_MALFORMED),
                                ("[]", STATE_MALFORMED),
                                ('{"version": 1, "state": ', STATE_UNREADABLE)):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            with self.subTest(content=content[:24]):
                machine = SyncStateMachine.load()
                self.assertEqual(machine.state, SyncState.DISABLED)
                self.assertEqual(machine.restore_reason, reason)
                self.assertEqual(machine.pending_changes, 0)

    def test_an_oversized_file_is_refused_without_parsing_it(self):
        path = app_paths.data_path(STATE_FILE_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[" + ",".join(['"x"'] * 40000) + "]")
        machine = SyncStateMachine.load()
        self.assertEqual(machine.state, SyncState.DISABLED)
        self.assertEqual(machine.restore_reason, STATE_UNREADABLE)

    def test_a_file_with_an_unknown_state_fails_closed_on_load(self):
        at("idle").save()
        path = app_paths.data_path(STATE_FILE_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload(state="teleporting"), handle)
        machine = SyncStateMachine.load()
        self.assertEqual(machine.state, SyncState.NOT_ENROLLED)
        self.assertEqual(machine.restore_reason, REASON_UNKNOWN)

    def test_the_live_data_directory_is_never_touched(self):
        machine = at("conflict", conflicts=1)
        machine.save()
        for written in self.files_written():
            self.assertTrue(written.startswith(self.data_tmp),
                            "%s escaped the scratch directory" % written)
        self.assertEqual(os.path.abspath(app_paths.data_dir()),
                         os.path.abspath(self.data_tmp))


# =====================================================================
# The 7-day offline lease (spec §5)
# =====================================================================
class LeaseBoundaryTests(_DataDirIsolation):
    """Injected clock only: the boundary is asserted, never waited for."""

    def setUp(self):
        super().setUp()
        self.granted = CLOCK
        # The clock is a plain variable: moving it moves the lease, with no
        # sleep and no wall clock. `grant()` persists into the scratch dir.
        self.lease = OfflineLease(clock=lambda: self.granted, path=None)
        self.lease.grant(at=self.granted)
        self.lease.restore_reason = ""

    def test_the_lease_runs_for_exactly_seven_days(self):
        self.assertEqual(LEASE_SECONDS, 7 * 24 * 60 * 60)

    def test_not_expired_one_second_before_the_boundary(self):
        self.granted = CLOCK + LEASE_SECONDS - 1
        self.assertFalse(self.lease.is_expired())
        self.assertEqual(self.lease.remaining(), 1.0)
        self.lease.assert_writable()          # must not raise

    def test_expired_exactly_at_the_boundary(self):
        self.granted = CLOCK + LEASE_SECONDS
        self.assertTrue(self.lease.is_expired())
        self.assertEqual(self.lease.remaining(), 0.0)
        with self.assertRaises(LeaseExpiredError):
            self.lease.assert_writable()

    def test_still_expired_one_second_after_the_boundary(self):
        self.granted = CLOCK + LEASE_SECONDS + 1
        self.assertTrue(self.lease.is_expired())
        self.assertEqual(self.lease.remaining(), 0.0)

    def test_remaining_is_the_full_lease_just_after_a_grant(self):
        self.assertEqual(self.lease.remaining(), float(LEASE_SECONDS))
        self.assertFalse(self.lease.is_expired())

    def test_a_machine_that_never_authenticated_is_read_only(self):
        fresh = OfflineLease(clock=lambda: CLOCK, path=None)
        self.assertTrue(fresh.is_expired())
        self.assertEqual(fresh.remaining(), 0.0)
        with self.assertRaises(LeaseExpiredError) as ctx:
            fresh.assert_writable()
        self.assertEqual(ctx.exception.reason, REASON_NEVER_GRANTED)

    def test_the_refusal_carries_a_stable_code_and_nothing_else(self):
        self.granted = CLOCK + LEASE_SECONDS
        with self.assertRaises(LeaseExpiredError) as ctx:
            self.lease.assert_writable()
        self.assertEqual(ctx.exception.code, LEASE_EXPIRED)
        self.assertEqual(ctx.exception.code, LEASE_EXPIRED_CODE)
        self.assertEqual(ctx.exception.reason, REASON_EXPIRED)
        text = str(ctx.exception)
        assert_no_pii(self, text, "LeaseExpiredError")
        # The wording is the UI's job (spec §11): the module says only
        # enough English to be readable in a log, and nothing at all in
        # another language — the panel maps `code` to Persian itself.
        self.assertTrue(text.isascii())

    def test_a_backward_clock_cannot_promise_a_longer_lease(self):
        lease = OfflineLease(clock=lambda: CLOCK - 10_000, path=None)
        lease.grant(at=CLOCK)
        self.assertEqual(lease.remaining(), float(LEASE_SECONDS))
        self.assertFalse(lease.is_expired())

    def test_a_far_future_clock_expires_it(self):
        lease = OfflineLease(clock=lambda: CLOCK + 40 * 24 * 3600, path=None)
        lease.grant(at=CLOCK)
        self.assertTrue(lease.is_expired())

    def test_a_grant_rejects_a_clock_that_is_not_a_number(self):
        lease = OfflineLease(clock=lambda: "now", path=None)
        with self.assertRaises(ValueError) as ctx:
            lease.grant()
        self.assertNotIn("now", str(ctx.exception))
        for bad in (float("nan"), float("inf"), True, "1759000000"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    lease.grant(at=bad)


class LeasePersistenceTests(_DataDirIsolation):
    def test_the_lease_file_lands_in_the_data_directory(self):
        lease = OfflineLease(clock=lambda: CLOCK)
        self.assertEqual(os.path.abspath(os.path.dirname(lease.path)),
                         os.path.abspath(self.data_tmp))
        self.assertEqual(os.path.basename(lease.path), LEASE_FILE_NAME)

    def test_a_grant_survives_a_restart(self):
        OfflineLease(clock=lambda: CLOCK).grant()
        path = app_paths.data_path(LEASE_FILE_NAME)
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(sorted(stored), ["granted_at", "version"])

        later = OfflineLease.load(clock=lambda: CLOCK + 3600)
        self.assertEqual(later.granted_at, float(CLOCK))
        self.assertEqual(later.remaining(), float(LEASE_SECONDS - 3600))
        self.assertEqual(later.restore_reason, "")
        later.assert_writable()

    def test_the_lease_is_still_expired_after_the_restart_at_the_boundary(self):
        OfflineLease(clock=lambda: CLOCK).grant()
        later = OfflineLease.load(clock=lambda: CLOCK + LEASE_SECONDS)
        self.assertTrue(later.is_expired())
        with self.assertRaises(LeaseExpiredError):
            later.assert_writable()

    def test_a_missing_lease_file_is_never_granted(self):
        lease = OfflineLease.load(clock=lambda: CLOCK)
        self.assertTrue(lease.is_expired())
        self.assertEqual(lease.restore_reason, "")
        self.assertEqual(self.files_written(), [])

    def test_an_unreadable_lease_file_fails_closed(self):
        path = app_paths.data_path(LEASE_FILE_NAME)
        for content in ("{oops", '"nope"', "", "[1,2,3]"):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            with self.subTest(content=content):
                lease = OfflineLease.load(clock=lambda: CLOCK)
                self.assertTrue(lease.is_expired())
                self.assertIn(lease.restore_reason,
                              (REASON_UNREADABLE, REASON_MALFORMED))
                with self.assertRaises(LeaseExpiredError):
                    lease.assert_writable()

    def test_a_malformed_lease_payload_fails_closed(self):
        cases = [
            {"version": 2, "granted_at": CLOCK},
            {"version": 1},
            {"version": 1, "granted_at": "tomorrow"},
            {"version": 1, "granted_at": -1},
            {"version": 1, "granted_at": 9_000_000_000_000},
            {"version": 1, "granted_at": True},
            {"version": True, "granted_at": CLOCK},
        ]
        for bad in cases:
            with self.subTest(payload=repr(bad)):
                lease = OfflineLease.from_dict(bad, clock=lambda: CLOCK)
                self.assertTrue(lease.is_expired())
                self.assertEqual(lease.restore_reason, REASON_MALFORMED)
                self.assertIsNone(lease.granted_at)

    def test_a_lease_round_trips_through_a_dict(self):
        lease = OfflineLease(clock=lambda: CLOCK, path=None)
        lease.grant(at=CLOCK)
        restored = OfflineLease.from_dict(
            json.loads(json.dumps(lease.to_dict())), clock=lambda: CLOCK)
        self.assertEqual(restored.granted_at, lease.granted_at)
        self.assertEqual(restored.to_dict(), lease.to_dict())

    def test_the_lease_file_holds_nothing_but_two_numbers(self):
        OfflineLease(clock=lambda: CLOCK).grant()
        path = app_paths.data_path(LEASE_FILE_NAME)
        with open(path, "r", encoding="utf-8") as handle:
            blob = handle.read()
        assert_no_pii(self, blob, "lease file")
        self.assertLess(len(blob), 200)
        for written in self.files_written():
            self.assertTrue(written.startswith(self.data_tmp))

    def test_a_grant_that_cannot_be_stored_is_not_swallowed(self):
        # A grant that never reached disk must surface: swallowing it would
        # let the panel believe it is writable after a restart that lost the
        # lease. The in-memory grant still stands for this session.
        lease = OfflineLease(clock=lambda: CLOCK, path=None)
        with patch("cloud_sync.lease.write_json_atomic",
                   side_effect=OSError("read-only volume")):
            with self.assertRaises(OSError):
                lease.grant()
        self.assertEqual(lease.granted_at, float(CLOCK))
        self.assertEqual(self.files_written(), [])


class CodeConsistencyTests(unittest.TestCase):
    def test_the_lease_and_the_state_machine_share_one_code(self):
        self.assertEqual(LEASE_EXPIRED, LEASE_EXPIRED_CODE)
        self.assertIn(LEASE_EXPIRED_CODE, KNOWN_ERROR_CODES)
        self.assertTrue(validate_error_code(LEASE_EXPIRED))

    def test_every_documented_code_is_a_valid_code(self):
        for code in KNOWN_ERROR_CODES:
            self.assertTrue(validate_error_code(code), code)
        for reason in (STATE_MALFORMED, REASON_UNKNOWN, STATE_UNREADABLE,
                       REASON_INTERRUPTED, REASON_NOT_LEGAL,
                       REASON_NO_CONFLICT, REASON_MALFORMED,
                       REASON_UNREADABLE, REASON_NEVER_GRANTED,
                       REASON_EXPIRED):
            self.assertTrue(validate_error_code(reason), reason)

    def test_states_and_events_survive_a_wire_round_trip(self):
        for state in SyncState:
            self.assertEqual(SyncState(state.value), state)
        for event in SyncEvent:
            self.assertEqual(SyncEvent(event.value), event)


if __name__ == "__main__":
    unittest.main(verbosity=2)
