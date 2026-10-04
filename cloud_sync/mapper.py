"""cloud_sync.mapper — deterministic JSON document ⇄ SQL row mapping (B).

Pure functions only: no file access, no network, no clock. The single ambient
input is the **source timezone**, which every time-converting call accepts and
defaults to an explicit fixed constant — never the OS default — so two machines
in different zones produce identical rows.

Contracts
---------
``to_row(entity, record)`` projects a store record onto ``ENTITY_FIELDS[entity]``
(the exact business columns of that table) and validates every value's type.
``from_row(entity, row)`` is its inverse. Together they guarantee::

    from_row(entity, to_row(entity, record)) == project(entity, record)

where ``project`` keeps only the keys of ``ENTITY_FIELDS[entity]``. For six of
the seven entities that projection is the record itself; the sole exception is
``customers``, whose nested ``transactions`` live in their own table and are
carried by :func:`lift_transactions` / :func:`restore_transactions`.

The **sync envelope** — ``id`` (the mapped UUID), ``tenant_id``,
``row_version``, ``created_at``, ``updated_at``, ``deleted_at``,
``deleted_by`` (spec §4) — is deliberately *not* part of a record and is never
emitted by ``to_row``/``from_row``. It is produced separately by
:func:`envelope`. That separation is not cosmetic: the envelope's ``id`` and a
record's own ``id`` share a column name, so flattening both into one dict would
destroy the legacy id. On the wire (spec §6.1) the UUID sits on the operation
and the legacy id stays inside ``record``, so no ambiguity arises there.

Time
----
``crm._now()`` writes ``%Y-%m-%d %H:%M:%S`` **local wall clock**. Spec §4 wants
UTC ISO-8601 with offset on the host, and forbids using time for conflict
resolution — these converters exist for storage correctness only.
"""
import datetime
import warnings

from cloud_sync.ids import ENTITIES, legacy_to_uuid

__all__ = [
    "ENTITY_FIELDS", "ENVELOPE_FIELDS", "LIFTED_OUT_FIELDS",
    "SETTINGS_ALLOWLIST", "STORE_COLLECTIONS",
    "MappingError", "UnknownEntityError", "MissingFieldError",
    "FieldTypeError", "InvalidTimestampError",
    "SettingsAllowlistWarning", "UnexpectedFieldWarning",
    "STORE_TIME_FORMAT", "IRAN_TZ",
    "to_wire_time", "from_wire_time",
    "to_row", "from_row", "project",
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


class SettingsAllowlistWarning(UserWarning):
    """A settings key outside the spec §3 allowlist was dropped."""


class UnexpectedFieldWarning(UserWarning):
    """A record field outside ``ENTITY_FIELDS`` was not carried to the row."""


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

# crm._now() / every panel timestamp writer: "%Y-%m-%d %H:%M:%S".
STORE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Source timezone for the store's wall-clock strings. Fixed UTC+03:30 (IRST,
# Asia/Tehran — daylight saving was abolished in 2022), so there is no DST
# ambiguity and no dependency on tzdata. ASSUMPTION: the machine that wrote the
# store kept Tehran local time. Callers whose store was written in another zone
# must pass ``tz=`` explicitly; this module never reads the system default.
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

    ``None``/``""`` → ``""``. A naive value (no offset) is rejected: guessing
    an offset would silently move a timestamp.
    """
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise FieldTypeError(
            f"wire timestamp must be a string, got {type(value).__name__}")
    text = value[:-1] + "+00:00" if value[-1:] in ("Z", "z") else value
    try:
        aware = datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidTimestampError(
            f"unparsable wire timestamp {value!r}") from exc
    if aware.tzinfo is None:
        raise InvalidTimestampError(f"wire timestamp has no offset: {value!r}")
    return aware.astimezone(tz).strftime(STORE_TIME_FORMAT)


# --------------------------------------------------------------------------
# Schema — derived from the code that writes the store (see module docstring)
# --------------------------------------------------------------------------

_STR, _INT, _BOOL, _TIME = "str", "int", "bool", "time"
_LIST_DICT, _SKIPS = "list[dict]", "skipped_stages"

# entity -> {column: type tag}, in column order. This is the single source of
# truth: ENTITY_FIELDS is derived from it, so the two can never drift.
#   customers           crm.add_customer / update_customer
#   customer_transactions  crm.add_transaction, void_transaction,
#                       _charge_registry_fee, _refund_registry_fee,
#                       crm.add_device (intake receipt)
#   owners              crm.add_owner / update_owner
#   identities          crm.add_identity / update_identity
#   crm_devices         crm.add_device / update_device / advance /
#                       stage_skip / close_case / detach_owner,
#                       panel.crm_periodic_toggle, panel auto-advance
#   periods             crm.open_period / close_period
#   settings            crm.DEFAULT_SETTINGS + panel.crm_update_settings
FIELD_TYPES = {
    "customers": {
        "id": _STR, "full_name": _STR, "address": _STR, "mobile": _STR,
        "telegram_id": _STR, "instagram_id": _STR, "notes": _STR,
        "created": _TIME, "updated": _TIME,
    },
    "customer_transactions": {
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
SETTINGS_ALLOWLIST = ENTITY_FIELDS["settings"]

# Fields that live in ENTITY_FIELDS but must NOT be in a row: they are lifted
# into their own table instead.
LIFTED_OUT_FIELDS = {"customers": frozenset({"transactions"})}

# Fields whose value is required (and, for `id`, must be a non-empty string).
# customer_transactions has no source id — its identity is (customer_id, seq),
# added by lift_transactions; settings is one row per tenant.
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

# Spec §4. Client-visible part only: created_at/updated_at are minted by the
# server (spec §2), deleted_at/deleted_by arrive with a tombstone.
ENVELOPE_FIELDS = ("id", "tenant_id", "row_version", "created_at",
                   "updated_at", "deleted_at", "deleted_by")


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
        # SQL TINYINT arrives as int; 0/1 are accepted explicitly, anything
        # else is rejected rather than coerced.
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
    target of :func:`to_row` / :func:`from_row`."""
    _entity_types(entity)
    if not isinstance(record, dict):
        raise FieldTypeError(
            f"{entity}: record must be a dict, got {type(record).__name__}")
    return {key: record[key] for key in ENTITY_FIELDS[entity] if key in record}


# --------------------------------------------------------------------------
# Row mapping
# --------------------------------------------------------------------------

def to_row(entity, record, *, tz=IRAN_TZ):
    """Store record → SQL row (business columns only, no sync envelope).

    Timestamps are converted to UTC ISO-8601 with offset; everything else is
    type-checked and passed through unchanged. Only keys declared in
    ``ENTITY_FIELDS[entity]`` are carried; a key that is neither declared nor
    known-lifted is dropped **with a warning** so a column added to the store
    cannot disappear silently. ``customers.transactions`` is the designed
    exception (lifted into its own table, never warned).
    """
    types = _entity_types(entity)
    _require_mapping(entity, record, "record")
    _require_fields(entity, record)
    source = record
    if entity == "settings":
        # Only the allowlist may leave this process (spec §3); the rest comes
        # back as local_only and is never forwarded.
        source, _local_only = split_settings(record)
    known = set(types) | LIFTED_OUT_FIELDS.get(entity, frozenset())
    row = {}
    for field, tag in types.items():
        if field in source:
            row[field] = _encode(source[field], tag, entity, field, tz)
    for key in source:
        if key not in known:
            warnings.warn(
                f"{entity}: field {key!r} is not in ENTITY_FIELDS and is "
                "not synced", UnexpectedFieldWarning, stacklevel=2)
    return row


def from_row(entity, row, *, tz=IRAN_TZ):
    """SQL row → store record (business columns only).

    A column absent from the row stays absent from the record, so a partially
    populated row never invents a field. Envelope columns are recognised and
    skipped silently; anything else outside ``ENTITY_FIELDS`` is dropped
    **with a warning** — that is what keeps a foreign settings key in a host
    payload from being written into the local store.
    """
    types = _entity_types(entity)
    _require_mapping(entity, row, "row")
    _require_fields(entity, row)
    record = {}
    for field, tag in types.items():
        if field in row:
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

def lift_transactions(customer, *, tz=IRAN_TZ):
    """``customers.transactions[]`` → ``customer_transactions`` rows.

    Each row carries ``seq`` (its index inside the array — the order the store
    and every report rely on) and ``customer_id`` (the foreign key).
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
        body = to_row("customer_transactions", tx, tz=tz)
        rows.append({**body, "seq": seq, "customer_id": customer["id"]})
    return rows


def restore_transactions(rows, *, tz=IRAN_TZ):
    """``customer_transactions`` rows → the nested list, ordered by ``seq``.

    Rows with the same ``seq`` keep their incoming relative order.
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
                      if k not in ("seq", "customer_id")}, tz=tz)
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
    """Merge syncable settings from the wire into the local dict.

    ``local_only`` keys are untouched — they always win locally, because
    ``incoming`` can never carry them. An unknown key in ``incoming`` is
    dropped **with a warning** and never applied or forwarded.
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

def split_store(store, *, tz=IRAN_TZ):
    """CRM document → ``{entity: rows}``.

    ``settings`` maps to ONE row (a dict — it is not a collection); every other
    entity maps to a list. Store metadata (``version``) is not synced.
    """
    _require_mapping("store", store, "store document")
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
            out[entity].append(to_row(entity, record, tz=tz))
            if entity == "customers":
                out["customer_transactions"].extend(
                    lift_transactions(record, tz=tz))
    out["settings"] = to_row(
        "settings", store.get("settings") or {}, tz=tz)
    return out


def merge_store(rows, *, tz=IRAN_TZ):
    """Inverse of :func:`split_store`.

    Transactions are re-nested into their customer and restored in ``seq``
    order. Every customer gets a ``transactions`` list (the store always writes
    one), so a hand-trimmed record without the key gains ``[]``.
    """
    _require_mapping("rows", rows, "rows document")
    grouped = {}
    for index, row in enumerate(rows.get("customer_transactions") or []):
        _require_mapping("customer_transactions", row, "row")
        customer_id = row.get("customer_id")
        if not isinstance(customer_id, str) or not customer_id:
            raise MissingFieldError(
                f"customer_transactions[{index}]: required field "
                "'customer_id' is missing")
        grouped.setdefault(customer_id, []).append(row)

    customers = []
    for row in rows.get("customers") or []:
        record = from_row("customers", row, tz=tz)
        record["transactions"] = restore_transactions(
            grouped.pop(record["id"], []), tz=tz)
        customers.append(record)
    if grouped:
        raise MappingError(
            f"{len(grouped)} customer_transactions row(s) reference a "
            "customer that is not in the batch")

    def _records(entity):
        return [from_row(entity, row, tz=tz)
                for row in (rows.get(entity) or [])]

    return {
        "customers": customers,
        "owners": _records("owners"),
        "identities": _records("identities"),
        "devices": _records("crm_devices"),
        "periods": _records("periods"),
        "settings": from_row("settings", rows.get("settings") or {}, tz=tz),
    }


def envelope(entity, legacy_id, *, source_installation_id, tenant_id,
             row_version=1):
    """The client-known sync-envelope columns for one record (spec §4).

    Returned **separately** from ``to_row``'s payload: the envelope ``id`` is
    the mapped UUID while the record keeps its legacy id under the same key, so
    they must never share one dict. ``created_at``/``updated_at`` are omitted
    on purpose — the server mints every timestamp it stores (spec §2) and time
    never decides a conflict (spec §4).
    """
    _entity_types(entity)
    if not isinstance(tenant_id, str) or not tenant_id:
        raise FieldTypeError(
            f"tenant_id must be a non-empty string, got {tenant_id!r}")
    if isinstance(row_version, bool) or not isinstance(row_version, int) \
            or row_version < 1:
        raise FieldTypeError(
            f"row_version must be an int >= 1, got {row_version!r}")
    return {
        "id": legacy_to_uuid(source_installation_id, entity, legacy_id),
        "tenant_id": tenant_id,
        "row_version": row_version,
    }
