# -*- coding: utf-8 -*-
"""The 7-day offline lease (spec §5 of lab/docs/CLOUD_SYNC_SPEC.md).

A machine that has not completed a successful authentication within seven
days becomes **read-only**: local edits are refused, reads keep working. The
refusal is an exception carrying a stable code, not a sentence — the panel
turns that code into its Persian message (spec §11), so nothing here may
carry operator data, a token, or wording beyond what the contract states.

What is persisted
-----------------
Only the epoch second of the last successful authentication, in a plain file
inside the data directory resolved through ``app_paths.data_path(...)`` —
never a hard-coded path. There is no secret in it: the device refresh token
lives in DPAPI-protected ``auth.dat`` (spec §5) and never enters this module.

Boundary
--------
The lease expires **at** ``LEASE_SECONDS`` (7 x 24 x 3600) — not a tick
before and not a tick after: ``is_expired()`` is ``elapsed >= LEASE_SECONDS``.
The clock is injected, so tests pin that boundary exactly instead of sleeping.

Wiring (spec §5, §9): call :meth:`OfflineLease.grant` exactly where a
successful authentication completes, and :meth:`OfflineLease.assert_writable`
in front of every local edit. When it raises, the panel is read-only and
``cloud_sync.state`` models the same fact as ``SyncState.READ_ONLY`` (fire
``SyncEvent.LEASE_EXPIRED`` there, ``AUTH_OK`` when the lease is granted).
"""
import json
import math
import time

import app_paths

from cloud_sync.state import LEASE_EXPIRED_CODE as LEASE_EXPIRED
from cloud_sync.state import write_json_atomic

#: The whole point of the lease: 7 x 24 h, in seconds.
LEASE_SECONDS = 7 * 24 * 60 * 60

#: Where the lease lives inside the data directory. It is non-secret (spec §5
#: keeps credentials in auth.dat), so a plain JSON file is enough.
LEASE_FILE_NAME = "cloud_sync_lease.json"

SCHEMA_VERSION = 1

#: Why the stored lease could not be trusted. Stable identifiers, never a
#: message: they may reach a diagnostic surface.
REASON_MALFORMED = "MALFORMED_LEASE"
REASON_UNREADABLE = "UNREADABLE_LEASE"

#: Why a write was refused: nothing was ever granted, or the seven days ran
#: out. The first one is not an error — a fresh install has no lease yet.
REASON_NEVER_GRANTED = "LEASE_NEVER_GRANTED"
REASON_EXPIRED = "LEASE_EXPIRED"

#: Sanity bound for a stored grant time (epoch seconds; year 2096). Outside
#: it the file is corrupt, not the clock.
_MAX_GRANT_TIME = 4_000_000_000


class LeaseExpiredError(Exception):
    """Raised by :meth:`OfflineLease.assert_writable` when edits must stop.

    A typed error, not a message: the panel switches on ``code`` for its
    Persian wording (spec §11). The text is English and contains nothing
    about the operator, the machine or the server.
    """

    #: What the UI maps (spec §5 names the condition, §11 names the mapping).
    code = LEASE_EXPIRED

    def __init__(self, reason=REASON_EXPIRED):
        self.reason = reason
        if reason == REASON_NEVER_GRANTED:
            text = "offline lease was never granted; authenticate first"
        else:
            text = "offline lease expired after 7 days; authenticate again"
        super().__init__(text)


def _check_number(value, what):
    """Return `value` as a float if it is a real, finite number.

    Raises ValueError naming the *field*, never the value: a value that
    failed this check may be a token or an operator identifier.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a number of epoch seconds" % what)
    if not math.isfinite(value):
        raise ValueError("%s must be a finite epoch second" % what)
    return float(value)


class OfflineLease:
    """Grant, inspect and enforce the 7-day offline lease.

    Once constructed, no decision here reads a clock of its own: the clock is
    injected, so the seven-day boundary is an exact, sleep-free assertion.
    """

    def __init__(self, *, clock=None, path=None):
        self._clock = clock if clock is not None else time.time
        self._path = path
        self._granted_at = None
        #: "" when the stored lease was fine, else why it was not trusted.
        self.restore_reason = ""

    # -- where it lives -------------------------------------------------
    @property
    def path(self):
        """The lease file: `path` when given, else in the data directory."""
        if self._path is not None:
            if not isinstance(self._path, str) or not self._path.strip():
                raise ValueError("lease path must be a non-empty file path")
            return self._path
        return app_paths.data_path(LEASE_FILE_NAME)

    @property
    def granted_at(self):
        """Epoch second of the last successful authentication, or None."""
        return self._granted_at

    # -- the contract ---------------------------------------------------
    def grant(self, at=None):
        """Record a successful authentication and persist it.

        Called exactly where the panel decides authentication succeeded
        (spec §5: the lease runs from the last successful authentication).
        Raises OSError rather than losing the grant silently if it cannot be
        stored — a grant that did not reach disk must look like a failure.
        """
        value = self._clock() if at is None else _check_number(at, "at")
        self._granted_at = _check_number(value, "clock")
        self.restore_reason = ""
        self.save()
        return self._granted_at

    def remaining(self):
        """Seconds left; 0.0 once expired, never more than seven days."""
        if self._granted_at is None:
            return 0.0
        elapsed = _check_number(self._clock(), "clock") - self._granted_at
        left = LEASE_SECONDS - elapsed
        if left < 0.0:
            return 0.0
        # A clock that jumped backwards must not promise more than seven days.
        if left > LEASE_SECONDS:
            return float(LEASE_SECONDS)
        return left

    def is_expired(self):
        """True at exactly seven days and beyond, never a tick before."""
        if self._granted_at is None:
            return True
        elapsed = _check_number(self._clock(), "clock") - self._granted_at
        return elapsed >= LEASE_SECONDS

    def assert_writable(self):
        """Refuse local edits once the lease is gone (spec §5).

        Reads are never gated here; only writes are.
        """
        if self._granted_at is None:
            raise LeaseExpiredError(REASON_NEVER_GRANTED)
        if self.is_expired():
            raise LeaseExpiredError(REASON_EXPIRED)

    # -- persistence ----------------------------------------------------
    def to_dict(self):
        """JSON-safe view: version and the grant time, nothing else."""
        return {"version": SCHEMA_VERSION, "granted_at": self._granted_at}

    @classmethod
    def from_dict(cls, payload, *, clock=None, path=None):
        """Restore a lease; anything untrusted fails closed to *expired*.

        A lease that cannot be trusted must refuse writes, never allow them.
        """
        lease = cls(clock=clock, path=path)
        if not isinstance(payload, dict):
            lease.restore_reason = REASON_MALFORMED
            return lease
        if type(payload.get("version")) is not int \
                or payload["version"] != SCHEMA_VERSION:
            lease.restore_reason = REASON_MALFORMED
            return lease
        if "granted_at" not in payload:
            lease.restore_reason = REASON_MALFORMED
            return lease
        granted = payload["granted_at"]
        if granted is None:
            return lease                      # never granted, and that is fine
        if isinstance(granted, bool) or not isinstance(granted, (int, float)) \
                or not math.isfinite(granted) \
                or not 0 <= granted <= _MAX_GRANT_TIME:
            lease.restore_reason = REASON_MALFORMED
            return lease
        lease._granted_at = float(granted)
        return lease

    @classmethod
    def load(cls, *, path=None, clock=None):
        """Read the lease from disk.

        A missing file is a fresh machine (never granted, therefore
        read-only). An unreadable or untrusted file fails closed the same
        way, with the reason recorded for the diagnostic surface.
        """
        lease = cls(clock=clock, path=path)
        try:
            with open(lease.path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return lease                       # first run: no lease yet
        except OSError:
            lease.restore_reason = REASON_UNREADABLE
            return lease
        try:
            payload = json.loads(raw)
        except ValueError:
            lease.restore_reason = REASON_UNREADABLE
            return lease
        return cls.from_dict(payload, clock=clock, path=path)

    def save(self, path=None):
        """Persist the lease atomically (temp file + rename)."""
        write_json_atomic(path or self.path, self.to_dict())
