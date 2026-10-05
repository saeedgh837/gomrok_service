# -*- coding: utf-8 -*-
"""One-shot bootstrap import (spec §8) — workstream K.

Saeed's existing ``crm_store.json`` has to reach the host **exactly once**,
atomically and idempotently. This module is the whole client side of that:
build the §8 document, prove it is sendable, take a local backup, POST it with
a deterministic ``import_id``, and classify whatever comes back.

The module is a pure library with injected I/O. It never imports ``crm`` or
``panel``, never opens the store itself, never sleeps on its own schedule and
never chooses its own transport:

============  =========================================================
``store``     the parsed document, **or** a zero-arg callable that
              returns it (the caller holds whatever lock it needs)
``send``      ``send(body) -> payload`` on success, a
              :class:`cloud_sync.errors.CloudSyncError` on failure
============  =========================================================

``send`` must authenticate as **both** an enrolled device and an admin user
(spec §6: device bearer in ``Authorization`` *and* ``X-Acting-User``) and must
return the §6 success envelope (``{"ok": true, …}``). A transport that answers
with ``{"ok": false}`` instead of raising is treated as a **terminal** failure:
only an exception carries enough information to decide whether a retry is
legal, so a silent failure is never looped on.

What is pinned here
-------------------
* **Payload.** :func:`split_document` runs :func:`cloud_sync.mapper.split_store`
  (spec §15.5 timezone, §4/§15.2 ids), then proves every entity is present,
  every record has an id, and every foreign key resolves to a row inside the
  same document — before a backup is taken, before a byte goes on the wire.
  Local-only ``settings`` keys are dropped by the mapper (spec §3) and
  surfaced on the result as ``dropped_settings``; they are never forwarded.
* **Idempotence.** ``import_id`` is ``uuid5(NAMESPACE_URL,
  "<source_installation_id>/import/<sha256 of the canonical payload>")``.
  The canonical form is sorted-key, separator-tight, whitespace-free JSON, so
  a retry after a lost response reproduces the *same* id and the host replays
  the *same* result (spec §8.4). Any edit to the payload produces a different
  id, which the host then refuses as ``DATASET_NOT_EMPTY`` on a later attempt.
* **Backup.** :func:`write_backup` runs before the request and returns the
  path; it never overwrites an existing file (``O_EXCL`` semantics).
* **Classification.** ``409 DATASET_NOT_EMPTY`` / ``409 IMPORT_CONFLICT`` are
  terminal and are never retried. Only timeout, connection error, ``429`` and
  ``5xx`` are retried (spec §9), at most ``max_attempts`` times, always with
  the *same* ``import_id``.
* **Codes, never sentences.** The server's ``error.message`` is discarded
  (spec §6); the caller receives a stable ``code`` that passes manager ruling
  8's grammar, ready for the panel's Persian mapping (§11).

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py \\
        test_cloud_import
"""
import hashlib
import json
import os
import random
import re
import time
import uuid

import requests

from cloud_sync import ids as _ids
from cloud_sync.errors import (
    BadResponseError, CloudSyncError, ConfigError, HttpError,
    RequestTimeoutError, RequestValidationError, SERVER_ERROR,
    UnreachableError, UnresolvedReferenceCloudError, code_for_status,
    envelope_code, is_retryable, safe_code,
)
from cloud_sync.ids import legacy_to_uuid
from cloud_sync.mapper import (  # read-only: B owns this module
    IRAN_TZ, MappingError, REFERENCE_FIELDS, SETTINGS_ALLOWLIST,
    STORE_COLLECTIONS, SYNTHETIC_ID_ENTITIES, UnresolvedReferenceError,
    split_store,
)

__all__ = [
    "IMPORT_ROUTE", "DATASET_NOT_EMPTY", "IMPORT_CONFLICT",
    "DEFAULT_MAX_ATTEMPTS", "DEFAULT_BASE_DELAY", "BACKUP_PREFIX",
    "canonical_json", "payload_digest", "import_id_for",
    "split_document", "build_document", "write_backup",
    "ImportResult", "run_import",
]

#: The §6 route. The host's ``Import.php`` and the loopback fake both key on it.
IMPORT_ROUTE = "/bootstrap/import"

#: The two 409 answers of spec §8.4. Both are **terminal**: the dataset is
#: already imported, or this ``import_id`` was burned with other bytes. Neither
#: may be looped on — repeating them changes nothing and hides the real state.
DATASET_NOT_EMPTY = "DATASET_NOT_EMPTY"
IMPORT_CONFLICT = "IMPORT_CONFLICT"

#: Bounded attempts for one import (spec §9: no infinite tight loop).
DEFAULT_MAX_ATTEMPTS = 3
#: First retry gap in seconds; doubles per attempt, jittered by ±50 %.
DEFAULT_BASE_DELAY = 1.0

#: Prefix of the pre-import backup file written next to the store.
BACKUP_PREFIX = "crm_store_import_backup_"
#: Distinct names tried inside one second before giving up.
MAX_BACKUP_NAMES = 1000

#: Spec §4 / §15.2: the namespace and the salt that turn the payload digest
#: into a stable import id. ``"import"`` keeps it out of the row-id namespace.
_NAMESPACE = uuid.NAMESPACE_URL
_ID_SALT = "import"

#: Exactly the UUID shape ``host/src/Ids.php isUuid()`` accepts, so a client
#: id the host would reject is refused here instead of costing a round trip.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")

#: The five entities whose row id derives from a legacy id (spec §15.1).
#: The two synthetic ones mint theirs, so they are keyed differently.
_SOURCE_ID_ENTITIES = tuple(entity for _key, entity in STORE_COLLECTIONS)


# =====================================================================
# Canonical form and the idempotent import_id
# =====================================================================
def canonical_json(value):
    """A byte-stable JSON rendering of `value`.

    Sorted keys at every depth, ``,`/` separators, no padding, no ASCII
    escaping. Two documents that differ only in key order therefore hash the
    same, and the same document always produces the same bytes — the property
    a retried import lives or dies by (spec §8.4).

    ``allow_nan`` is off: ``NaN``/``Infinity`` is not JSON, and a payload
    containing one would be rejected by the host's decoder anyway. Failing
    closed here gives the caller a stable code instead of a server 400.
    """
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(route=IMPORT_ROUTE) from exc


def payload_digest(collections):
    """``sha256`` of the canonical ``collections`` document, as hex."""
    if not isinstance(collections, dict):
        raise RequestValidationError(route=IMPORT_ROUTE)
    return hashlib.sha256(
        canonical_json(collections).encode("utf-8")).hexdigest()


def import_id_for(collections, source_installation_id):
    """The idempotent ``import_id`` for one exact payload (spec §8.4).

    ``uuid5(NAMESPACE_URL, "<installation>/import/<sha256>")``. The
    installation is inside the name, so two machines importing byte-identical
    collections still get different ids; the digest is inside it, so one
    machine replaying the same bytes always gets the same one.
    """
    installation = _require_installation(source_installation_id)
    name = "%s/%s/%s" % (installation, _ID_SALT, payload_digest(collections))
    return str(uuid.uuid5(_NAMESPACE, name))


def _require_installation(value):
    """A canonical UUID, or a refusal (host ``Ids::isUuid``).

    Case is not policed: the host matches case-insensitively, and this value
    is fed to :func:`cloud_sync.ids.legacy_to_uuid` unchanged, so whatever
    form is sent is also the form every row id was minted from.
    """
    if not isinstance(value, str) or not _UUID_RE.match(value.lower()):
        raise RequestValidationError(route=IMPORT_ROUTE)
    return value


# =====================================================================
# Payload
# =====================================================================
def split_document(store, *, source_installation_id, tz=IRAN_TZ):
    """``store`` → ``(collections, dropped_settings)``, fully validated.

    ``collections`` is the §8 wire shape: **every** entity a list (spec §15.1
    keeps the legacy id under ``id``, so these are flat records, not sync
    envelopes — the host's ``Import.php identifier()`` reads ``record.id`` as
    the legacy id), ``settings`` a 0- or 1-element list.

    Raises before anything is sent when an entity is missing, a record has no
    id, a foreign key points at a row this document does not contain, or the
    mapper rejects the document. ``dropped_settings`` names the spec §3 keys
    the mapper refused to forward; they are reported, not fatal (§3: dropped
    on write **with a warning**, never sent).
    """
    if not isinstance(store, dict):
        raise RequestValidationError(route=IMPORT_ROUTE)
    settings = store.get("settings")
    dropped = tuple(sorted(key for key in settings
                           if key not in SETTINGS_ALLOWLIST)) \
        if isinstance(settings, dict) else ()
    try:
        rows = split_store(store, tz=tz,
                           source_installation_id=source_installation_id)
    except UnresolvedReferenceError as exc:
        raise UnresolvedReferenceCloudError(route=IMPORT_ROUTE) from exc
    except MappingError as exc:
        raise RequestValidationError(route=IMPORT_ROUTE) from exc

    collections = {}
    for entity in _ids.ENTITIES:
        value = rows.get(entity, [])
        if entity == "settings" and isinstance(value, dict):
            value = [value]              # uniform list form (spec §15.13)
        collections[entity] = value
    _validate(collections, source_installation_id)
    return collections, dropped


def build_document(store, *, source_installation_id, tz=IRAN_TZ):
    """The complete §8 request body, ready to POST."""
    collections, _dropped = split_document(
        store, source_installation_id=source_installation_id, tz=tz)
    return {
        "import_id": import_id_for(collections, source_installation_id),
        "source_installation_id": _require_installation(source_installation_id),
        "collections": collections,
    }


def _validate(collections, source_installation_id):
    """Prove the document the host is about to be asked to insert."""
    expected = set(_ids.ENTITIES)
    present = set(collections)
    if present != expected:
        raise RequestValidationError(route=IMPORT_ROUTE) from ValueError(
            "collections holds %s, expected %s"
            % (sorted(present), sorted(expected)))

    addressed = set()
    for entity in _ids.ENTITIES:
        entries = collections[entity]
        if not isinstance(entries, list):
            raise RequestValidationError(route=IMPORT_ROUTE) from ValueError(
                "collections.%s must be a list" % entity)
        if entity == "settings" and len(entries) > 1:
            # host/src/Import.php: one settings row per tenant (spec §3).
            raise RequestValidationError(route=IMPORT_ROUTE) from ValueError(
                "collections.settings holds %d rows" % len(entries))
        for index, record in enumerate(entries):
            where = "%s[%d]" % (entity, index)
            if not isinstance(record, dict):
                raise RequestValidationError(route=IMPORT_ROUTE) from ValueError(
                    "%s must be an object" % where)
            identifier = record.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise RequestValidationError(route=IMPORT_ROUTE) from ValueError(
                    "%s has no id" % where)
            if entity in SYNTHETIC_ID_ENTITIES:
                # Minted by to_row (spec §15.2); the host demands a real UUID.
                if not _UUID_RE.match(identifier.lower()):
                    raise RequestValidationError(
                        route=IMPORT_ROUTE) from ValueError(
                            "%s carries a non-UUID minted id" % where)
                continue
            if len(identifier) > 64:      # host legacy_id VARCHAR(64)
                raise RequestValidationError(route=IMPORT_ROUTE) from ValueError(
                    "%s legacy id is too long" % where)
            addressed.add((entity,
                           legacy_to_uuid(source_installation_id, entity,
                                          identifier)))

    # Every foreign key must resolve *inside this document*: map_reference is
    # a one-way uuid5 and would happily mint a UUID for a parent that is not
    # here, which the host would then reject as SQL error 1452 (spec §4).
    for entity, references in REFERENCE_FIELDS.items():
        for record in collections[entity]:
            for field, target in references.items():
                value = record.get(field)
                if value is None or value == "":
                    continue
                if not isinstance(value, str) or (target, value) not in addressed:
                    raise UnresolvedReferenceCloudError(
                        route=IMPORT_ROUTE) from ValueError(
                            "%s.%s points at a %s row this document omits"
                            % (entity, field, target))


# =====================================================================
# Backup (spec §8.3)
# =====================================================================
def write_backup(store, directory, *, now=None):
    """Write a timestamped copy of `store` into `directory`; return its path.

    Never overwrites: the file is created exclusively, and a name that already
    exists is skipped for ``_1``, ``_2``, … So two imports in the same second
    — or an import on top of a previous one — both keep their snapshot.

    The directory is created when it is missing, because the caller supplies
    it (typically under the data dir) and a typo must not silently disable the
    safety net. An unusable directory, or a document that cannot be
    serialised, raises :class:`ConfigError` **before** the request is sent:
    §8.3 takes the backup first, so an import with no backup never happens.
    """
    if not isinstance(directory, str) or not directory:
        raise ConfigError(route=IMPORT_ROUTE)
    if not isinstance(store, dict):
        raise ConfigError(route=IMPORT_ROUTE)
    try:
        text = json.dumps(store, ensure_ascii=False, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise ConfigError(route=IMPORT_ROUTE) from exc
    try:
        os.makedirs(directory, exist_ok=True)
    except (OSError, ValueError) as exc:
        raise ConfigError(route=IMPORT_ROUTE) from exc

    moment = time.time() if now is None else float(now)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
    for index in range(MAX_BACKUP_NAMES):
        suffix = "" if index == 0 else "_%d" % index
        path = os.path.join(
            directory, "%s%s%s.json" % (BACKUP_PREFIX, stamp, suffix))
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            continue                      # never overwrite a real backup
        except (OSError, ValueError) as exc:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise ConfigError(route=IMPORT_ROUTE) from exc
        return path
    raise ConfigError(route=IMPORT_ROUTE)


# =====================================================================
# Result
# =====================================================================
class ImportResult(object):
    """What one :func:`run_import` did, in codes the panel can map (§11).

    ``payload`` holds the host's success body (``rows``, ``counts``,
    ``cursor``, ``dataset_epoch`` — what the caller needs to start pulling).
    It is deliberately **not** part of ``repr``/``str``: an exception string,
    a log line or a debug print of this object must never carry business data.
    """

    __slots__ = ("ok", "code", "import_id", "backup_path", "attempts",
                 "payload", "dropped_settings", "terminal")

    def __init__(self, *, ok, code, import_id, backup_path, attempts,
                 payload, dropped_settings, terminal):
        #: ``True`` only when the host committed the import.
        self.ok = bool(ok)
        #: Stable code, ``""`` on success. Never the server's message (§6).
        self.code = code
        #: The idempotency key that was actually sent.
        self.import_id = import_id
        #: Path of the pre-import backup, or ``None`` if none was taken.
        self.backup_path = backup_path
        #: HTTP attempts made, including the first one.
        self.attempts = attempts
        #: The host's success body, or ``None``.
        self.payload = payload
        #: Spec §3 keys the mapper refused to forward. Reported, not fatal.
        self.dropped_settings = dropped_settings
        #: ``True`` when another attempt cannot help: ``409``, any other
        #: 4xx/401, or a payload that never became an envelope. A transient
        #: fault that merely ran out of budget stays ``False``, so the caller
        #: may try again later; ``409`` never may (spec §8.4, §9).
        self.terminal = bool(terminal)

    @property
    def rows(self):
        """Rows the host committed (0 on any failure)."""
        return self._payload_int("rows")

    @property
    def counts(self):
        """Per-entity row counts, or ``{}`` on any failure."""
        if not self.ok or not isinstance(self.payload, dict):
            return {}
        value = self.payload.get("counts")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def cursor(self):
        """``change_log.seq`` after the import — where a pull must resume."""
        return self._payload_int("cursor")

    @property
    def dataset_epoch(self):
        """The epoch the host bumped (spec §8.5)."""
        return self._payload_int("dataset_epoch")

    def _payload_int(self, field):
        if not self.ok or not isinstance(self.payload, dict):
            return 0
        value = self.payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def __repr__(self):
        return ("ImportResult(ok=%r, code=%r, import_id=%r, attempts=%r, "
                "terminal=%r, backup_path=%r, dropped_settings=%r)"
                % (self.ok, self.code, self.import_id, self.attempts,
                   self.terminal, self.backup_path, self.dropped_settings))


# =====================================================================
# The one-shot
# =====================================================================
def run_import(store, *, send, source_installation_id, backup_dir,
               tz=IRAN_TZ, max_attempts=DEFAULT_MAX_ATTEMPTS,
               base_delay=DEFAULT_BASE_DELAY, sleep=time.sleep,
               rng=random.random, now=None):
    """Perform the one-shot import and classify the outcome (spec §8).

    Order: build and validate the document, take the backup, then send. A
    refusal raised before the request carries a stable code and leaves the
    host untouched — nothing is sent when the payload cannot be trusted.

    Retries reuse the **same** ``import_id``, which is what makes a retried
    import safe: the host replays the first result rather than inserting
    twice (spec §8.4). ``409`` is never retried, at any attempt count.
    """
    if not callable(send):
        raise ConfigError(route=IMPORT_ROUTE)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) \
            or max_attempts < 1:
        raise ConfigError(route=IMPORT_ROUTE)

    document = store() if callable(store) else store
    if not isinstance(document, dict):
        raise RequestValidationError(route=IMPORT_ROUTE)
    collections, dropped = split_document(
        document, source_installation_id=source_installation_id, tz=tz)
    import_id = import_id_for(collections, source_installation_id)
    body = {
        "import_id": import_id,
        "source_installation_id": _require_installation(source_installation_id),
        "collections": collections,
    }
    backup_path = write_backup(document, backup_dir, now=now)

    attempt = 0
    while True:
        attempt += 1
        try:
            payload = send(body)
        except CloudSyncError as error:
            if not is_retryable(error) or attempt >= max_attempts:
                return _refusal(error, import_id=import_id,
                                backup_path=backup_path, dropped=dropped,
                                attempts=attempt)
            _pause(attempt, base_delay, sleep, rng)
            continue
        except requests.RequestException as error:
            # A transport that raises the raw library error instead of the
            # typed one: classify it the way cloud_sync.errors would.
            typed = _wrap_transport(error)
            if not is_retryable(typed) or attempt >= max_attempts:
                return _refusal(typed, import_id=import_id,
                                backup_path=backup_path, dropped=dropped,
                                attempts=attempt)
            _pause(attempt, base_delay, sleep, rng)
            continue

        if isinstance(payload, dict) and payload.get("ok") is True:
            return ImportResult(ok=True, code="", import_id=import_id,
                                backup_path=backup_path, attempts=attempt,
                                payload=payload, dropped_settings=dropped,
                                terminal=False)
        # A returned failure envelope carries no status, so no retry can be
        # justified from it: classify the code and stop (spec §9).
        return _refusal(_envelope_failure(payload), import_id=import_id,
                        backup_path=backup_path, dropped=dropped,
                        attempts=attempt)


def _pause(attempt, base_delay, sleep, rng):
    """Exponential backoff with ±50 % jitter, bounded by the attempt count."""
    if base_delay is None:
        return
    delay = float(base_delay) * (2 ** (attempt - 1)) * (0.5 + float(rng()))
    if delay > 0:
        sleep(delay)


def _wrap_transport(error):
    """A raw ``requests`` exception → the typed error it stands for."""
    if isinstance(error, requests.Timeout):
        return RequestTimeoutError(route=IMPORT_ROUTE)
    if isinstance(error, requests.ConnectionError):
        return UnreachableError(route=IMPORT_ROUTE)
    return CloudSyncError(route=IMPORT_ROUTE)


def _envelope_failure(payload):
    """A non-raising ``{"ok": false, …}`` → the typed error for its code."""
    if not isinstance(payload, dict):
        return BadResponseError(route=IMPORT_ROUTE)
    return HttpError(code=envelope_code(payload, None), route=IMPORT_ROUTE,
                     status=None, code_source="envelope")


def _refusal(error, *, import_id, backup_path, dropped, attempts):
    """The result for a failed attempt: a code, never the server's sentence."""
    code = safe_code(getattr(error, "code", None))
    status = getattr(error, "status", None)
    if (not code or code == SERVER_ERROR) and status is not None:
        # Either no code was set, or the class default was: the status says
        # more than "something went wrong". A code the server itself named
        # (``DATASET_NOT_EMPTY`` …) is always kept (spec §6).
        code = code_for_status(status)
    if not code:
        code = SERVER_ERROR
    return ImportResult(ok=False, code=code, import_id=import_id,
                        backup_path=backup_path, attempts=attempts,
                        payload=None, dropped_settings=dropped,
                        terminal=not is_retryable(error))
