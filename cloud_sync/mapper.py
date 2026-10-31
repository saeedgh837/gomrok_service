"""cloud_sync.mapper — deterministic JSON document ⇄ SQL row mapping (B).

Pure functions only: no file access, no network, no clock. The two ambient
inputs are injected, never read from the environment:

* ``tz`` — the source timezone of the store's wall-clock strings (spec §15.5).
* ``source_installation_id`` — the UUID that makes id mapping deterministic
  (spec §4, §15.2). Required by :func:`to_row`.

Contracts
---------
``to_row(entity, record, source_installation_id=…)`` builds the **wire
record** (spec §6.1): business columns only, ``id`` kept as the record's own
legacy id (spec §15.1), timestamps in UTC ISO-8601 with offset, and every
foreign key rewritten to the UUID the host schema needs. ``from_row`` is its
inverse::

    from_row(entity, to_row(entity, record, …), reference_index=…) \\
        == project(entity, record)

``project`` keeps only the keys of ``ENTITY_FIELDS[entity]``. For six of the
seven entities that is the record itself; the exception is ``customers``,
whose nested ``transactions`` live in their own table and travel through
:func:`lift_transactions` / :func:`restore_transactions`.

The **sync envelope** — ``id`` (UUID), ``legacy_id``, ``tenant_id``,
``row_version``, ``created_at``, ``updated_at``, ``deleted_at``,
``deleted_by`` (spec §4 + §15.1) — is never emitted by ``to_row``/``from_row``;
it comes from :func:`envelope`. Spec §15.1 stores the two sides in two
columns: the envelope ``id`` goes to ``id``, and the legacy id that ``to_row``
returns under the key ``id`` goes to ``legacy_id``. They must never be
flattened into one dict.

References (spec §15 / manager ruling 1)
----------------------------------------
These columns are foreign keys on the host and therefore carry UUIDs, never
the store's eight-hex ids::

    crm_devices.customer_id / owner_id / period_id
    customer_transactions.customer_id / device_id
    settings.current_period_id

:func:`map_reference` performs the forward rewrite (``""``/``None`` → SQL
NULL). The rewrite is uuid5, so it is **not invertible**: reading a row back
needs :func:`reference_index`, built from the local store, which maps each
UUID to the legacy id the JSON document holds. Without that index
:func:`from_row` refuses to answer rather than write a UUID into a store the
robots still read.

Two entities have no eight-hex id of their own (spec §15.1/§15.2), so
``to_row`` mints their ``id`` itself and ``from_row`` does not return one.

Time
----
``crm._now()`` writes ``%Y-%m-%d %H:%M:%S`` **local wall clock**; the host
stores UTC and returns ``…T…+00:00`` (``host/src/Schema.php`` line 791).
Time is converted for storage correctness only — spec §4 forbids using it to
decide a conflict.
"""
import datetime
import re
import warnings

from cloud_sync.ids import ENTITIES, legacy_to_uuid

__all__ = [
    "ENTITY_FIELDS", "ENVELOPE_FIELDS", "LIFTED_OUT_FIELDS",
    "SETTINGS_ALLOWLIST", "SETTINGS_LEGACY_ID", "SETTINGS_LEGACY_KEY",
    "STORE_COLLECTIONS", "REFERENCE_FIELDS", "NULLABLE_REFERENCE_FIELDS",
    "SYNTHETIC_ID_ENTITIES",
    "MappingError", "UnknownEntityError", "MissingFieldError",
    "FieldTypeError", "InvalidTimestampError", "UnresolvedReferenceError",
    "SettingsAllowlistWarning", "UnexpectedFieldWarning",
    "STORE_TIME_FORMAT", "IRAN_TZ",
    "to_wire_time", "from_wire_time",
    "to_row", "from_row", "project",
    "map_reference", "reference_index", "transaction_legacy_id",
    "lift_transactions", "restore_transactions",
    "split_settings", "merge_settings",
    "split_store", "merge_store", "envelope",
]


# --------------------------------------------------------------------------
# Errors and warnings
# --------------------------------------------------------------------------

class MappingError(ValueError):
    """Base class for every rejection raised by this module."""


class UnknownEntityError(MappingError):
    """The entity name is not one of the spec §3 table names."""


class MissingFieldError(MappingError):
    """A record/row lacks a field that identifies it (e.g. ``id``)."""


class FieldTypeError(MappingError):
    """A field carries a value of the wrong type — never coerced silently."""


class InvalidTimestampError(FieldTypeError):
    """A timestamp string does not parse in either direction."""


class UnresolvedReferenceError(MappingError):
    """A wire foreign key cannot be turned back into a local legacy id."""


class SettingsAllowlistWarning(UserWarning):
    """A settings key outside the spec §3 allowlist was dropped."""


class UnexpectedFieldWarning(UserWarning):
    """A record/row field outside ``ENTITY_FIELDS`` was not carried over."""


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

# crm._now() / every panel timestamp writer: "%Y-%m-%d %H:%M:%S".
STORE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Python 3.10's fromisoformat wants exactly 3 or 6 fractional digits; the
# host's own validator accepts any width, so pad before parsing.
_FRACTION_RE = re.compile(r"\.(\d+)(?=[+-]|$)")

# Source timezone for the store's wall-clock strings (spec §15.5): fixed
# UTC+03:30 (IRST, Asia/Tehran — daylight saving was abolished in 2022), so
# there is no DST ambiguity and no dependency on tzdata. ASSUMPTION: the
# machine that wrote the store kept Tehran local time. Callers whose store was
# written elsewhere must pass ``tz=``; this module never reads the OS default.
IRAN_TZ = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

_UTC = datetime.timezone.utc


def to_wire_time(value, tz=IRAN_TZ):
    """Store wall-clock string → UTC ISO-8601 with offset.

    ``""``/``None`` (the store's "not set") → ``None`` (SQL NULL). Anything
    unparsable raises :class:`InvalidTimestampError`.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise FieldTypeError(
            f"timestamp must be a string, got {type(value).__name__}")
    try:
        naive = datetime.datetime.strptime(value, STORE_TIME_FORMAT)
    except ValueError as exc:
        raise InvalidTimestampError(
            f"unparsable store timestamp {value!r}, expected "
            f"{STORE_TIME_FORMAT}") from exc
    return naive.replace(tzinfo=tz).astimezone(_UTC).isoformat(
        timespec="seconds")


def from_wire_time(value, tz=IRAN_TZ):
    """UTC ISO-8601 with offset → store wall-clock string in ``tz``.

    Accepts exactly what the host reads back (``Schema.php`` renders
    ``Y-m-d\\TH:i:s+00:00``) as well as ``Z``, a space separator and any
    fractional-second width the host's own validator allows — the store format
    has second resolution, so a fraction is padded (and then dropped) rather
    than rejected. ``None``/``""`` → ``""``. A naive value (no offset) is
    rejected: guessing an offset would silently move a timestamp.
    """
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise FieldTypeError(
            f"wire timestamp must be a string, got {type(value).__name__}")
    text = value[:-1] + "+00:00" if value[-1:] in ("Z", "z") else value
    text = _FRACTION_RE.sub(
        lambda match: "." + (match.group(1) + "000000")[:6], text)
    try:
        aware = datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidTimestampError(
            f"unparsable wire timestamp {value!r}") from exc
    if aware.tzinfo is None:
        return aware.strftime(STORE_TIME_FORMAT)
    return aware.astimezone(tz).strftime(STORE_TIME_FORMAT)


# --------------------------------------------------------------------------
# Schema — derived from the code that writes the store
# --------------------------------------------------------------------------

_STR, _INT, _BOOL, _TIME = "str", "int", "bool", "time"
_LIST_DICT, _SKIPS = "list[dict]", "skipped_stages"

# entity -> {column: type tag}, in column order. Single source of truth:
# ENTITY_FIELDS is derived from it, so the two can never drift.
#   customers              crm.add_customer / update_customer
#   customer_transactions  crm.add_transaction, void_transaction,
#                          _charge_registry_fee, _refund_registry_fee,
#                          crm.add_device (intake receipt)
#   owners                 crm.add_owner / update_owner
#   identities             crm.add_identity / update_identity
#   crm_devices            crm.add_device / update_device / advance /
#                          stage_skip / close_case / detach_owner,
#                          panel.crm_periodic_toggle, panel auto-advance
#   periods                crm.open_period / close_period
#   settings               crm.DEFAULT_SETTINGS + panel.crm_update_settings
FIELD_TYPES = {
    "customers": {
        "id": _STR, "full_name": _STR, "address": _STR, "mobile": _STR,
        "telegram_id": _STR, "instagram_id": _STR, "notes": _STR,
        "created": _TIME, "updated": _TIME,
    },
    "customer_transactions": {
        # MINTED by to_row (spec §15.2), dropped by from_row.
        "id": _STR,
        "seq": _INT, "customer_id": _STR, "at": _TIME, "amount": _INT,
        "note": _STR, "kind": _STR, "image": _STR, "device_id": _STR,
        "voided_at": _TIME, "void_reason": _STR,
    },
    "owners": {
        "id": _STR, "first_name": _STR, "last_name": _STR, "national": _STR,
        "birth": _STR, "mobile": _STR, "card_no": _STR, "passport": _STR,
        "created": _TIME, "updated": _TIME,
    },
    "identities": {
        "id": _STR, "label": _STR, "national": _STR, "birth": _STR,
        "mobile": _STR, "created": _TIME, "updated": _TIME,
    },
    "crm_devices": {
        "id": _STR, "name": _STR, "serial1": _STR, "serial2": _STR,
        "customer_id": _STR, "owner_id": _STR, "stage": _STR,
        "auto_advance": _BOOL, "profile_id": _STR, "handed_at": _TIME,
        "registered_at": _TIME, "payment_confirmed_at": _TIME,
        "code_delivered_at": _TIME, "done_at": _TIME, "last_inquiry_at": _TIME,
        "notes": _STR, "advance_attempts": _INT, "period_id": _STR,
        "created": _TIME, "updated": _TIME,
        # Added after creation, so a young record simply lacks them:
        "skipped_stages": _SKIPS, "closed_at": _TIME, "closed_reason": _STR,
        "periodic_opt_out": _BOOL, "payment_started_at": _TIME,
    },
    "periods": {
        "id": _STR, "number": _INT, "label": _STR, "opened_at": _TIME,
        "opened_by": _STR, "closed_at": _TIME, "closed_by": _STR,
        "closed_reason": _STR,
    },
    "settings": {
        # MINTED by to_row (spec §15.2), dropped by from_row.
        "id": _STR,
        "business_name": _STR, "business_phone": _STR,
        "watch_interval_hours": _INT, "scheduler_enabled": _BOOL,
        "auto_advance_default": _BOOL, "telegram_report_full_data": _BOOL,
        "registry_fee_amount": _INT, "registration_chat_id": _STR,
        "report_chat_id": _STR, "sound_enabled": _BOOL,
        "current_period_id": _STR, "links": _LIST_DICT,
    },
}

# Exact business columns of one row per entity (spec §3).
ENTITY_FIELDS = {entity: tuple(types) for entity, types in FIELD_TYPES.items()}

# Spec §3 "settings allowlist": the ONLY keys that may cross the wire.
SETTINGS_ALLOWLIST = tuple(
    field for field in ENTITY_FIELDS["settings"] if field != "id")

# Spec §15.2 — synthetic legacy ids for the two entities that have no 8-hex
# id. BOTH sides must compute these identically or the uuid5 ids disagree.
#   customer_transactions -> "<customer legacy_id>/<seq>"
#   settings              -> "settings/<key>"
# The host stores ONE settings row per tenant (host/migrations/001_initial.sql,
# UNIQUE KEY uq_settings_tenant), so the whole document has a single key and
# SETTINGS_LEGACY_KEY is that key. Workstream F's import must use this exact
# string: it is the only thing that makes both uuid5 computations agree.
SETTINGS_LEGACY_KEY = "settings"
SETTINGS_LEGACY_ID = f"settings/{SETTINGS_LEGACY_KEY}"

# Entities whose `id` to_row mints and from_row therefore does not return.
SYNTHETIC_ID_ENTITIES = frozenset({"customer_transactions", "settings"})

# entity -> {column: target entity}. These are real foreign keys on the host
# (host/src/Schema.php `ref`); an 8-hex value would raise SQL error 1452.
# Deliberately absent (spec §15.4 — they stay local and dangle on purpose):
#   crm_devices.profile_id, customer_transactions.image
REFERENCE_FIELDS = {
    "crm_devices": {"customer_id": "customers", "owner_id": "owners",
                    "period_id": "periods"},
    "customer_transactions": {"customer_id": "customers",
                               "device_id": "crm_devices"},
    "settings": {"current_period_id": "periods"},
}

# Reference columns that allow SQL NULL on the host (001_initial.sql).
# Dangling legacy references on these columns sanitize to empty / NULL rather
# than crashing the entire sync.
NULLABLE_REFERENCE_FIELDS = {
    "crm_devices": frozenset({"customer_id", "owner_id", "period_id"}),
    "customer_transactions": frozenset({"device_id"}),
    "settings": frozenset({"current_period_id"}),
}

# Fields that are in ENTITY_FIELDS but must not be in a row: they are lifted
# into their own table instead.
LIFTED_OUT_FIELDS = {"customers": frozenset({"transactions"})}

# Fields whose value is required (and must be a non-empty string).
# customer_transactions/settings have no source id — it is minted, so
# spec §15.1 keeps their `legacy_id` column fed by the synthetic formula.
REQUIRED_FIELDS = {
    "customers": ("id",),
    "owners": ("id",),
    "identities": ("id",),
    "crm_devices": ("id",),
    "periods": ("id",),
    "customer_transactions": (),
    "settings": (),
}

# store key -> entity name, for the collections that are lists in the document.
STORE_COLLECTIONS = (
    ("customers", "customers"),
    ("owners", "owners"),
    ("identities", "identities"),
    ("devices", "crm_devices"),
    ("periods", "periods"),
)

# Entities that carry a source id and can therefore appear in a reference
# index. settings/customer_transactions mint theirs (spec §15.2).
SOURCE_ID_ENTITIES = tuple(entity for _key, entity in STORE_COLLECTIONS)

# Spec §4 + §15.1: what the client knows about a row's envelope. Only
# `id`/`legacy_id`/`tenant_id`/`row_version` are client-supplied —
# created_at/updated_at are minted by the server (spec §2) and
# deleted_at/deleted_by arrive with a tombstone.
ENVELOPE_FIELDS = ("id", "legacy_id", "tenant_id", "row_version",
                   "created_at", "updated_at", "deleted_at", "deleted_by")

_STORE_KEY_TO_ENTITY = dict(STORE_COLLECTIONS)


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

def _entity_types(entity):
    types = FIELD_TYPES.get(entity)
    if types is None:
        raise UnknownEntityError(
            f"unknown entity {entity!r}; expected one of "
            f"{', '.join(ENTITIES)}")
    return types


def _require_mapping(entity, value, what):
    if not isinstance(value, dict):
        raise FieldTypeError(
            f"{entity}: {what} must be a dict, got {type(value).__name__}")


def _require_installation(source_installation_id):
    if not isinstance(source_installation_id, str) \
            or not source_installation_id:
        raise FieldTypeError(
            "source_installation_id must be a non-empty string, got "
            f"{source_installation_id!r}")


def _require_fields(entity, source):
    for field in REQUIRED_FIELDS[entity]:
        if field not in source:
            raise MissingFieldError(f"{entity}: required field {field!r} "
                                    "is missing")
        value = source[field]
        if not isinstance(value, str) or not value:
            raise FieldTypeError(
                f"{entity}: {field} must be a non-empty string, got "
                f"{value!r}")


def transaction_legacy_id(customer_legacy_id, seq):
    """Spec §15.2 synthetic legacy id of one ledger row."""
    if not isinstance(customer_legacy_id, str) or not customer_legacy_id:
        raise FieldTypeError(
            "customer_legacy_id must be a non-empty string, got "
            f"{customer_legacy_id!r}")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise FieldTypeError(f"seq must be an int >= 0, got {seq!r}")
    return f"{customer_legacy_id}/{seq}"


def map_reference(target_entity, value, *, source_installation_id):
    """Legacy reference → UUID column value; unset → ``None`` (never ``""``).

    ``""``/``None`` mean "no customer / no owner / no period" and become SQL
    NULL, which is the only thing a foreign key accepts (host
    ``EMPTY_REFERENCE``; SQL error 1452 otherwise).
    """
    _entity_types(target_entity)
    _require_installation(source_installation_id)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise FieldTypeError(
            f"reference must be a string, got {type(value).__name__}")
    return legacy_to_uuid(source_installation_id, target_entity, value)


def reference_index(records, *, source_installation_id):
    """``{(entity, uuid): legacy_id}`` for every record that has a source id.

    Accepts either a ``crm_store.json`` document (store keys: ``devices`` …)
    or the rows from :func:`split_store` (SQL entity names: ``crm_devices`` …).
    This is the only way a wire foreign key can be turned back into the 8-hex
    id the local store holds: uuid5 is one-way.
    """
    _require_mapping("records", records, "document")
    _require_installation(source_installation_id)
    index = {}
    for key, items in records.items():
        entity = _STORE_KEY_TO_ENTITY.get(key)
        if entity is None and key in SOURCE_ID_ENTITIES:
            entity = key
        if entity is None:
            continue                      # "version", "settings", …
        if items is None:
            continue
        if not isinstance(items, list):
            raise FieldTypeError(
                f"{key} must be a list, got {type(items).__name__}")
        for record in items:
            _require_mapping(entity, record, "record")
            legacy = record.get("id")
            if not isinstance(legacy, str) or not legacy:
                continue
            index[(entity, legacy_to_uuid(
                source_installation_id, entity, legacy))] = legacy
    return index


def _resolve_reference(target_entity, value, reference_index, where):
    """UUID column → the local 8-hex id (or ``""`` for SQL NULL)."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise FieldTypeError(
            f"{where} must be a string, got {type(value).__name__}")
    if reference_index is None:
        raise UnresolvedReferenceError(
            f"{where}: reference_index is required to read a foreign key "
            "back into the local store (uuid5 cannot be inverted)")
    legacy = reference_index.get((target_entity, value))
    if legacy is None:
        raise UnresolvedReferenceError(
            f"{where}: no local {target_entity} record maps to {value}")
    return legacy


def _encode(value, tag, entity, field, tz):
    where = f"{entity}.{field}"
    if tag == _TIME:
        return to_wire_time(value, tz)
    if tag == _STR:
        if not isinstance(value, str):
            raise FieldTypeError(
                f"{where} must be a string, got {type(value).__name__}")
        return value
    if tag == _INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise FieldTypeError(
                f"{where} must be an int, got {type(value).__name__}")
        return value
    if tag == _BOOL:
        if not isinstance(value, bool):
            raise FieldTypeError(
                f"{where} must be a bool, got {type(value).__name__}")
        return value
    if tag == _LIST_DICT:
        if value is None:
            return []
        if not isinstance(value, list):
            raise FieldTypeError(
                f"{where} must be a list, got {type(value).__name__}")
        out = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise FieldTypeError(
                    f"{where}[{index}] must be a dict, got "
                    f"{type(item).__name__}")
            out.append(dict(item))
        return out
    if tag == _SKIPS:
        if value is None:
            return []
        if not isinstance(value, list):
            raise FieldTypeError(
                f"{where} must be a list, got {type(value).__name__}")
        return [_map_skip(item, index, entity, tz, to_wire=True)
                for index, item in enumerate(value)]
    raise MappingError(f"{where}: unsupported type tag {tag!r}")


def _decode(value, tag, entity, field, tz):
    where = f"{entity}.{field}"
    if tag == _TIME:
        return from_wire_time(value, tz)
    if tag == _STR:
        if value is None:
            return ""          # SQL NULL on a text column == store ""
        if not isinstance(value, str):
            raise FieldTypeError(
                f"{where} must be a string, got {type(value).__name__}")
        return value
    if tag == _INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise FieldTypeError(
                f"{where} must be an int, got {type(value).__name__}")
        return value
    if tag == _BOOL:
        # spec §15.3: TINYINT(1) arrives as int; 0/1 are accepted explicitly,
        # anything else is rejected rather than coerced.
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise FieldTypeError(
            f"{where} must be a bool (or 0/1), got {value!r}")
    if tag == _LIST_DICT:
        if value is None:
            return []
        if not isinstance(value, list):
            raise FieldTypeError(
                f"{where} must be a list, got {type(value).__name__}")
        out = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise FieldTypeError(
                    f"{where}[{index}] must be a dict, got "
                    f"{type(item).__name__}")
            out.append(dict(item))
        return out
    if tag == _SKIPS:
        if value is None:
            return []
        if not isinstance(value, list):
            raise FieldTypeError(
                f"{where} must be a list, got {type(value).__name__}")
        return [_map_skip(item, index, entity, tz, to_wire=False)
                for index, item in enumerate(value)]
    raise MappingError(f"{where}: unsupported type tag {tag!r}")


def _map_skip(item, index, entity, tz, to_wire):
    """One ``skipped_stages`` entry: a dict whose ``at`` is a timestamp."""
    where = f"{entity}.skipped_stages[{index}]"
    if not isinstance(item, dict):
        raise FieldTypeError(
            f"{where} must be a dict, got {type(item).__name__}")
    out = dict(item)
    if "at" in out:
        out["at"] = (to_wire_time(out["at"], tz) if to_wire
                     else from_wire_time(out["at"], tz))
    return out


def project(entity, record):
    """The record restricted to ``ENTITY_FIELDS[entity]`` — the round-trip
    target of :func:`to_row` / :func:`from_row`. A minted ``id`` is not part
    of a source record, so it is never projected."""
    _entity_types(entity)
    if not isinstance(record, dict):
        raise FieldTypeError(
            f"{entity}: record must be a dict, got {type(record).__name__}")
    return {key: record[key] for key in ENTITY_FIELDS[entity] if key in record}


# --------------------------------------------------------------------------
# Row mapping
# --------------------------------------------------------------------------

def to_row(entity, record, *, tz=IRAN_TZ, source_installation_id):
    """Store record → wire record (business columns, no sync envelope).

    Three things happen beyond a plain projection:

    * every column in ``REFERENCE_FIELDS[entity]`` is rewritten to the UUID the
      host's foreign key needs (``""``/``None`` → SQL NULL, never ``""``);
    * ``id`` is minted for the two entities that have no source id (spec §15.2);
    * timestamps become UTC ISO-8601 with offset.

    A key that is neither declared nor known-lifted is dropped **with a
    warning** so a column added to the store cannot disappear silently;
    ``customers.transactions`` is the designed exception (it is lifted into
    its own table, never warned).
    """
    types = _entity_types(entity)
    _require_mapping(entity, record, "record")
    _require_fields(entity, record)
    _require_installation(source_installation_id)
    source = record
    if entity == "settings":
        # Only the allowlist may leave this process (spec §3); the rest comes
        # back as local_only and is never forwarded.
        source, _local_only = split_settings(record)
    references = REFERENCE_FIELDS.get(entity, {})
    minted = entity in SYNTHETIC_ID_ENTITIES
    known = set(types) | LIFTED_OUT_FIELDS.get(entity, frozenset())
    row = {}
    for field, tag in types.items():
        if field == "id" and minted:
            continue
        if field not in source:
            continue
        if field in references:
            row[field] = map_reference(
                references[field], source[field],
                source_installation_id=source_installation_id)
            continue
        row[field] = _encode(source[field], tag, entity, field, tz)
    if minted:
        row["id"] = _minted_id(entity, source, source_installation_id)
    for key in source:
        if key not in known:
            warnings.warn(
                f"{entity}: field {key!r} is not in ENTITY_FIELDS and is "
                "not synced", UnexpectedFieldWarning, stacklevel=2)
    return row


def _minted_id(entity, source, source_installation_id):
    """uuid5 for the two entities that have no 8-hex id (spec §15.2)."""
    if entity == "settings":
        return legacy_to_uuid(source_installation_id, entity,
                              SETTINGS_LEGACY_ID)
    legacy_id = source.get("customer_id")
    if not isinstance(legacy_id, str) or not legacy_id:
        raise MissingFieldError(
            "customer_transactions: required field 'customer_id' is missing")
    if "seq" not in source:
        raise MissingFieldError(
            "customer_transactions: required field 'seq' is missing")
    return legacy_to_uuid(
        source_installation_id, entity,
        transaction_legacy_id(legacy_id, source["seq"]))


def from_row(entity, row, *, tz=IRAN_TZ, reference_index=None):
    """Wire record → store record (business columns only).

    Foreign keys are resolved back to the local 8-hex ids through
    ``reference_index`` — uuid5 is one-way, so a row with a non-NULL reference
    and no index is **refused** rather than written into the store. SQL NULL
    becomes ``""``, the store's own "unset" sentinel, so a round trip holds.

    A column absent from the row stays absent from the record. Envelope
    columns are recognised and skipped silently; anything else outside
    ``ENTITY_FIELDS`` is dropped **with a warning** — that is what keeps a
    foreign settings key in a host payload from reaching the local store.
    """
    types = _entity_types(entity)
    _require_mapping(entity, row, "row")
    _require_fields(entity, row)
    references = REFERENCE_FIELDS.get(entity, {})
    minted = entity in SYNTHETIC_ID_ENTITIES
    record = {}
    for field, tag in types.items():
        if field == "id" and minted:
            continue                      # minted, not part of the record
        if field not in row:
            continue
        if field in references:
            record[field] = _resolve_reference(
                references[field], row[field], reference_index,
                f"{entity}.{field}")
            continue
        record[field] = _decode(row[field], tag, entity, field, tz)
    known = set(types) | set(ENVELOPE_FIELDS)
    category = (SettingsAllowlistWarning if entity == "settings"
                else UnexpectedFieldWarning)
    for key in row:
        if key not in known:
            warnings.warn(
                f"{entity}: column {key!r} is not part of the record and was "
                "dropped", category, stacklevel=2)
    return record


# --------------------------------------------------------------------------
# Nested transactions
# --------------------------------------------------------------------------

def lift_transactions(customer, *, tz=IRAN_TZ, source_installation_id):
    """``customers.transactions[]`` → ``customer_transactions`` rows.

    Each row gets ``seq`` (its index inside the array — the order the store and
    every report rely on), a ``customer_id`` rewritten to the customer's UUID,
    and the spec §15.2 minted ``id``.
    """
    _require_mapping("customers", customer, "record")
    _require_fields("customers", customer)
    raw = customer.get("transactions", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise FieldTypeError(
            f"customers.transactions must be a list, got "
            f"{type(raw).__name__}")
    rows = []
    for seq, tx in enumerate(raw):
        if not isinstance(tx, dict):
            raise FieldTypeError(
                f"customers.transactions[{seq}] must be a dict, got "
                f"{type(tx).__name__}")
        source = {**tx, "seq": seq, "customer_id": customer["id"]}
        rows.append(to_row("customer_transactions", source, tz=tz,
                           source_installation_id=source_installation_id))
    return rows


def restore_transactions(rows, *, tz=IRAN_TZ, reference_index=None):
    """``customer_transactions`` rows → the nested list, ordered by ``seq``.

    ``seq``/``customer_id`` are link metadata, not part of the nested record,
    and are stripped; the minted ``id`` is dropped by :func:`from_row`.
    """
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise FieldTypeError(
            f"customer_transactions must be a list, got "
            f"{type(rows).__name__}")
    ordered = []
    for index, row in enumerate(rows):
        _require_mapping("customer_transactions", row, "row")
        if "seq" not in row:
            raise MissingFieldError(
                f"customer_transactions[{index}]: required field 'seq' "
                "is missing")
        seq = row["seq"]
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise FieldTypeError(
                f"customer_transactions[{index}].seq must be an int, got "
                f"{type(seq).__name__}")
        ordered.append((seq, index, row))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [from_row("customer_transactions",
                     {k: v for k, v in row.items()
                      if k not in ("seq", "customer_id")},
                     tz=tz, reference_index=reference_index)
            for _seq, _index, row in ordered]


# --------------------------------------------------------------------------
# Settings allowlist (spec §3)
# --------------------------------------------------------------------------

def split_settings(settings):
    """Split the settings dict into ``(syncable, local_only)``.

    Only keys on the spec §3 allowlist may cross the wire. Everything else
    (tokens, paths, hosts, device names — any future device-local key) is
    returned as ``local_only`` **and warned about**: it is never forwarded.
    """
    _require_mapping("settings", settings, "settings")
    syncable, local_only = {}, {}
    for key, value in settings.items():
        if key in SETTINGS_ALLOWLIST:
            syncable[key] = value
        else:
            local_only[key] = value
            warnings.warn(
                f"settings: key {key!r} is not on the sync allowlist and "
                "stays local", SettingsAllowlistWarning, stacklevel=2)
    return syncable, local_only


def merge_settings(base, incoming):
    """Merge syncable settings from the host into the local dict.

    ``local_only`` keys are untouched — they always win locally, because
    ``incoming`` can never carry them. An unknown key in ``incoming`` is
    dropped **with a warning** and never applied or forwarded.

    ``incoming`` must be **store-shaped**: pass the output of
    ``from_row("settings", row, reference_index=…)``, not the raw wire row, or
    ``current_period_id`` would land in the local store as a UUID.
    """
    _require_mapping("settings", base, "settings")
    _require_mapping("settings", incoming, "incoming settings")
    merged = dict(base)
    types = FIELD_TYPES["settings"]
    for key, value in incoming.items():
        if key not in SETTINGS_ALLOWLIST:
            warnings.warn(
                f"settings: incoming key {key!r} is not on the sync "
                "allowlist and was dropped", SettingsAllowlistWarning,
                stacklevel=2)
            continue
        merged[key] = _encode(value, types[key], "settings", key, IRAN_TZ)
    return merged


# --------------------------------------------------------------------------
# Document level
# --------------------------------------------------------------------------

def split_store(store, *, tz=IRAN_TZ, source_installation_id):
    """CRM document → ``{entity: rows}``.

    ``settings`` maps to ONE row (a dict — it is not a collection); every other
    entity maps to a list. Store metadata (``version``) is not synced.
    """
    _require_mapping("store", store, "store document")
    _require_installation(source_installation_id)
    out = {name: [] for name in ENTITIES}
    for key, entity in STORE_COLLECTIONS:
        items = store.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            raise FieldTypeError(
                f"store[{key!r}] must be a list, got {type(items).__name__}")
        for record in items:
            _require_mapping(entity, record, "record")
            out[entity].append(to_row(
                entity, record, tz=tz,
                source_installation_id=source_installation_id))
            if entity == "customers":
                out["customer_transactions"].extend(lift_transactions(
                    record, tz=tz, source_installation_id=source_installation_id))
    out["settings"] = to_row(
        "settings", store.get("settings") or {}, tz=tz,
        source_installation_id=source_installation_id)
    return out


def merge_store(rows, *, tz=IRAN_TZ, source_installation_id):
    """Inverse of :func:`split_store`.

    The reference index is rebuilt from the batch itself — every row of the
    five entities still carries its legacy id under ``id`` — so foreign keys
    resolve without any extra input. Transactions are re-nested into their
    customer and restored in ``seq`` order. Every customer gets a
    ``transactions`` list (the store always writes one), so a hand-trimmed
    record without the key gains ``[]``.
    """
    _require_mapping("rows", rows, "rows document")
    _require_installation(source_installation_id)
    index = reference_index(rows, source_installation_id=source_installation_id)

    customer_rows = rows.get("customers") or []
    customers_by_uuid = {
        legacy_to_uuid(source_installation_id, "customers", row["id"]): row
        for row in customer_rows if isinstance(row.get("id"), str) and row["id"]
    }
    grouped = {}
    for row in rows.get("customer_transactions") or []:
        _require_mapping("customer_transactions", row, "row")
        customer_id = row.get("customer_id")
        if not isinstance(customer_id, str) or not customer_id:
            raise MissingFieldError(
                "customer_transactions: required field 'customer_id' is "
                "missing")
        grouped.setdefault(customer_id, []).append(row)

    customers = []
    for row in customer_rows:
        record = from_row("customers", row, tz=tz, reference_index=index)
        uuid = legacy_to_uuid(source_installation_id, "customers",
                              record["id"])
        record["transactions"] = restore_transactions(
            grouped.pop(uuid, []), tz=tz, reference_index=index)
        customers.append(record)
    if grouped:
        raise MappingError(
            f"{len(grouped)} customer_transactions row(s) reference a "
            "customer that is not in the batch")

    def _records(entity):
        return [from_row(entity, row, tz=tz, reference_index=index)
                for row in (rows.get(entity) or [])]

    return {
        "customers": customers,
        "owners": _records("owners"),
        "identities": _records("identities"),
        "devices": _records("crm_devices"),
        "periods": _records("periods"),
        "settings": from_row("settings", rows.get("settings") or {}, tz=tz,
                             reference_index=index),
    }


def envelope(entity, legacy_id, *, source_installation_id, tenant_id,
             row_version=1):
    """The client-known sync-envelope columns for one record (spec §4, §15.1).

    Returned **separately** from ``to_row``'s payload: spec §15.1 stores the
    envelope ``id`` in the ``id`` column and the payload's legacy id in
    ``legacy_id``, so the two must never be flattened into one dict. Both are
    produced here because ``legacy_id`` is an envelope column too (Schema.php
    ``ENVELOPE``) and it is the only place the synthetic id of the two entities
    without an 8-hex id can be stated. ``created_at``/``updated_at`` are
    omitted on purpose — the server mints every timestamp it stores (spec §2)
    and time never decides a conflict (spec §4).

    For the five entities with a source id pass ``record["id"]``; for the two
    synthetic ones pass :data:`SETTINGS_LEGACY_ID` or
    :func:`transaction_legacy_id`, and the UUID matches what :func:`to_row`
    mints.
    """
    _entity_types(entity)
    if not isinstance(legacy_id, str) or not legacy_id:
        raise FieldTypeError(
            f"legacy_id must be a non-empty string, got {legacy_id!r}")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise FieldTypeError(
            f"tenant_id must be a non-empty string, got {tenant_id!r}")
    if isinstance(row_version, bool) or not isinstance(row_version, int) \
            or row_version < 1:
        raise FieldTypeError(
            f"row_version must be an int >= 1, got {row_version!r}")
    return {
        "id": legacy_to_uuid(source_installation_id, entity, legacy_id),
        "legacy_id": legacy_id,
        "tenant_id": tenant_id,
        "row_version": row_version,
    }
