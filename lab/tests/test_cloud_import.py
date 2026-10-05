# -*- coding: utf-8 -*-
"""One-shot bootstrap import (workstream K).

Contract: `lab/docs/CLOUD_SYNC_SPEC.md` — §3 (settings allowlist), §4 (ids and
time), §6 (error envelope), §8 (one-shot import), §9 (retries), §11 (the UI
maps codes, never sentences), §12 (offline, mock data only), §15 rulings 1–5
and 13.

What is pinned here:

* the §8 body — every entity present, flat records carrying the legacy id,
  `settings` a one-element list, foreign keys rewritten to UUIDs, Tehran wall
  clock converted to UTC;
* `import_id` = uuid5(`installation` / `"import"` / sha256(canonical
  payload)): a byte-identical retry reproduces it, any edit changes it, and a
  different installation always differs;
* a timestamped backup is written **before** the request and never overwrites
  an existing file;
* `409 DATASET_NOT_EMPTY` / `409 IMPORT_CONFLICT` are terminal and are never
  retried whatever the attempt budget; timeout / connection error / 429 / 5xx
  are retried with the *same* `import_id`, a bounded count and jittered
  backoff;
* a local-only `settings` key is dropped by the mapper and surfaced on the
  result as `dropped_settings`, never forwarded (spec §3);
* the server's `message` never reaches a result, a `repr` or a log (spec §6);
* the wire shape is accepted end to end by `lab/tests/_cloud_api_fake.py`.

Everything runs offline against the loopback fake. No test writes to
`%LOCALAPPDATA%\\irica\\`; every file lands in a `tempfile` directory.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py \\
        test_cloud_import
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
import uuid
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import requests  # noqa: E402

import _cloud_api_fake as fake_api  # noqa: E402
from cloud_sync import importer  # noqa: E402
from cloud_sync.auth import (  # noqa: E402
    AUTH_FILE_NAME, MemorySecretStore, TokenVault,
)
from cloud_sync.client import SyncClient  # noqa: E402
from cloud_sync.config import CONFIG_FILE_NAME, CloudConfig  # noqa: E402
from cloud_sync.errors import (  # noqa: E402
    AuthRequiredError, ConfigError, HttpError, RequestTimeoutError,
    RequestValidationError, UnresolvedReferenceCloudError, NET_TIMEOUT,
)
from cloud_sync.ids import legacy_to_uuid  # noqa: E402
from cloud_sync.mapper import (  # noqa: E402 - read-only, workstream B
    ENTITY_FIELDS, SettingsAllowlistWarning,
)

#: One machine: every id in this module is minted from it.
INSTALL = "11111111-1111-4111-8111-111111111111"
#: Frozen clock, so backup names and backoff delays are exact.
FROZEN = 1_800_000_000
#: Every canned server message carries it; none may ever be observed.
CANARY = fake_api.SERVER_MESSAGE_CANARY

# --------------------------------------------------------------------------
# Sample document (mock data only — no real customer, no real credential)
# --------------------------------------------------------------------------
TRANSACTIONS = [
    {"at": "2026-09-03 08:00:00", "amount": 1500000,
     "note": "sample deposit", "kind": "payment", "image": "sample01.png",
     "device_id": "d1d2d3d4"},
    {"at": "2026-09-04 09:00:01", "amount": 24000000,
     "note": "sample reversal", "kind": "void", "image": "",
     "device_id": ""},
]

CUSTOMER = {
    "id": "a1b2c3d4", "full_name": "Sample Customer",
    "address": "Sample Street 1", "mobile": "09120000001",
    "telegram_id": "@sample_tg", "instagram_id": "@sample_ig",
    "notes": "sample note",
    "created": "2026-09-01 10:00:00", "updated": "2026-09-05 14:00:00",
}

OWNER = {
    "id": "e5f6a7b8", "first_name": "Sample", "last_name": "Passenger",
    "national": "0012345679", "birth": "1375/04/29",
    "mobile": "09120000002", "card_no": "6037991234567890",
    "passport": "AB1234567", "created": "2026-09-01 10:10:00",
}

IDENTITY = {
    "id": "c9d0e1f2", "label": "sample pool entry", "national": "0012345679",
    "birth": "1375/04/29", "mobile": "09120000003",
    "created": "2026-09-01 10:11:00", "updated": "2026-09-04 08:00:00",
}

DEVICE = {
    "id": "d1d2d3d4", "name": "sample device",
    "serial1": "123456789012345", "serial2": "543210987654321",
    "customer_id": "a1b2c3d4", "owner_id": "e5f6a7b8", "stage": "closed",
    "auto_advance": True, "profile_id": "prof0001",
    "handed_at": "2026-09-02 09:00:00", "registered_at": "2026-09-02 10:00:00",
    "payment_confirmed_at": "2026-09-02 11:00:00",
    "code_delivered_at": "2026-09-02 12:00:00",
    "done_at": "2026-09-02 12:05:00", "last_inquiry_at": "2026-09-03 07:00:00",
    "notes": "sample case notes", "advance_attempts": 0,
    "period_id": "aa11bb22",
    "created": "2026-09-01 10:12:00", "updated": "2026-09-05 14:00:00",
    "skipped_stages": [{"stage": "inquiry", "to": "ready_owner",
                        "at": "2026-09-01 11:00:00"}],
    "closed_at": "2026-09-05 14:00:00",
    "closed_reason": "sample closed reason",
    "periodic_opt_out": True,
    "payment_started_at": "2026-09-02 10:30:00",
}

PERIOD = {
    "id": "aa11bb22", "number": 1, "label": "sample period one",
    "opened_at": "2026-09-01 00:00:00", "opened_by": "operator",
    "closed_at": "", "closed_by": "", "closed_reason": "",
}

SETTINGS = {
    "business_name": "Sample Trading Co", "business_phone": "02100000000",
    "watch_interval_hours": 2, "scheduler_enabled": True,
    "auto_advance_default": False, "telegram_report_full_data": True,
    "registry_fee_amount": 24000000, "registration_chat_id": "-1001234567890",
    "report_chat_id": "", "sound_enabled": True,
    "current_period_id": "aa11bb22",
    "links": [{"name": "sample link", "url": "https://example.invalid/"}],
}

#: Device-local / secret-shaped keys that must never reach the wire (§3).
LOCAL_ONLY_SETTINGS = {
    "device_token": "placeholder-token-not-real",
    "profile_dir": "C:/placeholder/profiles",
}

EXPECTED_COUNTS = {
    "customers": 1, "customer_transactions": 2, "owners": 1,
    "identities": 1, "crm_devices": 1, "periods": 1, "settings": 1,
}


def sample_store(*, with_local_settings=True):
    """A complete, hand-built CRM document with every synced collection."""
    settings = dict(SETTINGS)
    if with_local_settings:
        settings.update(LOCAL_ONLY_SETTINGS)
    return {
        "version": 1,
        "customers": [dict(CUSTOMER,
                           transactions=[dict(tx) for tx in TRANSACTIONS])],
        "owners": [dict(OWNER)],
        "identities": [dict(IDENTITY)],
        "devices": [dict(DEVICE)],
        "periods": [dict(PERIOD)],
        "settings": settings,
    }


class TempDirCase(unittest.TestCase):
    """A throwaway directory; nothing here touches the live data dir."""

    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory(prefix="irica_import_")
        self.addCleanup(holder.cleanup)
        self.root = holder.name
        self.backup_dir = os.path.join(self.root, "backups")
        os.makedirs(self.backup_dir)
        self.sent = []

    def recorder(self, response=None, error=None):
        """A transport that answers from a script and records every body."""
        def send(body):
            self.sent.append(json.loads(json.dumps(body)))
            if error is not None:
                raise error
            return response
        return send

    def document(self, response=None, error=None):
        """A transport that answers from a script and records every body."""
        if response is None:
            response = _ok_payload()
        return self.recorder(response=response, error=error)


def _ok_payload(**overrides):
    payload = {"ok": True, "import_id": str(uuid.uuid4()),
               "tenant_id": "tenant-sample", "dataset_epoch": 1,
               "source_installation_id": INSTALL,
               "counts": dict(EXPECTED_COUNTS), "rows": 8, "cursor": 42}
    payload.update(overrides)
    return payload


# =====================================================================
# The §8 payload
# =====================================================================
class TestPayload(unittest.TestCase):

    def build(self, store=None, **kwargs):
        kwargs.setdefault("source_installation_id", INSTALL)
        return importer.build_document(
            sample_store() if store is None else store, **kwargs)

    def test_the_body_has_exactly_the_three_fields_the_host_reads(self):
        body = self.build()
        self.assertEqual(sorted(body),
                         ["collections", "import_id",
                          "source_installation_id"])
        self.assertEqual(body["source_installation_id"], INSTALL)

    def test_every_entity_is_present_and_is_a_list(self):
        collections = self.build()["collections"]
        self.assertEqual(set(collections), set(ENTITY_FIELDS))
        for entity, entries in collections.items():
            self.assertIsInstance(entries, list, entity)
            for record in entries:
                self.assertIsInstance(record, dict, entity)
                self.assertIsInstance(record.get("id"), str, entity)
        # spec §15.13: settings is a 0- or 1-element list, never a bare dict.
        self.assertEqual(len(collections["settings"]), 1)

    def test_records_carry_the_legacy_id_the_host_stores(self):
        collections = self.build()["collections"]
        self.assertEqual(collections["customers"][0]["id"], "a1b2c3d4")
        self.assertEqual(collections["owners"][0]["id"], "e5f6a7b8")
        self.assertEqual(collections["crm_devices"][0]["id"], "d1d2d3d4")

    def test_foreign_keys_are_uuids_and_point_inside_this_document(self):
        collections = self.build()["collections"]
        device = collections["crm_devices"][0]
        self.assertEqual(device["customer_id"],
                         legacy_to_uuid(INSTALL, "customers", "a1b2c3d4"))
        self.assertEqual(device["owner_id"],
                         legacy_to_uuid(INSTALL, "owners", "e5f6a7b8"))
        self.assertEqual(device["period_id"],
                         legacy_to_uuid(INSTALL, "periods", "aa11bb22"))
        # profile_id and image are opaque strings on purpose (spec §15.4).
        self.assertEqual(device["profile_id"], "prof0001")
        transactions = collections["customer_transactions"]
        self.assertTrue(uuid.UUID(transactions[0]["id"]).version == 5)
        self.assertEqual(transactions[0]["seq"], 0)
        self.assertEqual(transactions[1]["seq"], 1)
        self.assertIsNone(transactions[1]["device_id"])
        self.assertEqual(collections["settings"][0]["current_period_id"],
                         legacy_to_uuid(INSTALL, "periods", "aa11bb22"))

    def test_store_wall_clock_becomes_utc_with_an_offset(self):
        collections = self.build()["collections"]
        # 10:00 in Tehran (UTC+03:30) is 06:30 UTC (spec §15.5).
        self.assertEqual(collections["customers"][0]["created"],
                         "2026-09-01T06:30:00+00:00")
        self.assertEqual(collections["periods"][0]["opened_at"],
                         "2026-08-31T20:30:00+00:00")
        self.assertIsNone(collections["periods"][0]["closed_at"])

    def test_a_different_timezone_would_move_the_history(self):
        shifted = importer.build_document(
            sample_store(), source_installation_id=INSTALL,
            tz=datetime.timezone.utc)
        self.assertNotEqual(
            shifted["collections"]["customers"][0]["created"],
            self.build()["collections"]["customers"][0]["created"])

    # -- import_id -----------------------------------------------------
    def test_the_import_id_is_a_version_5_uuid(self):
        identifier = self.build()["import_id"]
        parsed = uuid.UUID(identifier)
        self.assertEqual(parsed.version, 5)
        self.assertEqual(str(parsed), identifier)

    def test_the_same_document_reproduces_the_same_import_id(self):
        first = self.build()["import_id"]
        second = self.build(sample_store())["import_id"]
        self.assertEqual(first, second)
        # A JSON round trip changes key order and nothing else: the id must
        # survive it, or a retry after a lost response would not replay.
        third = importer.build_document(
            json.loads(json.dumps(sample_store())),
            source_installation_id=INSTALL)["import_id"]
        self.assertEqual(first, third)

    def test_any_edit_changes_the_import_id(self):
        edited = sample_store()
        edited["customers"][0]["full_name"] = "Another Sample"
        self.assertNotEqual(self.build()["import_id"],
                            importer.build_document(
                                edited,
                                source_installation_id=INSTALL)["import_id"])

    def test_a_different_installation_changes_the_import_id(self):
        other = importer.build_document(
            sample_store(),
            source_installation_id="22222222-2222-4222-8222-222222222222")
        self.assertNotEqual(self.build()["import_id"], other["import_id"])

    def test_the_import_id_is_the_documented_uuid5_of_the_digest(self):
        collections, _ = importer.split_document(
            sample_store(), source_installation_id=INSTALL)
        digest = importer.payload_digest(collections)
        expected = str(uuid.uuid5(
            uuid.NAMESPACE_URL, "%s/import/%s" % (INSTALL, digest)))
        self.assertEqual(importer.import_id_for(collections, INSTALL),
                         expected)

    def test_canonical_json_ignores_key_order_and_whitespace(self):
        left = {"b": 1, "a": {"z": [1, 2], "y": "x"}}
        right = {"a": {"y": "x", "z": [1, 2]}, "b": 1}
        self.assertEqual(importer.canonical_json(left),
                         importer.canonical_json(right))
        self.assertEqual(importer.canonical_json(left),
                         '{"a":{"y":"x","z":[1,2]},"b":1}')

    def test_a_nan_in_the_document_is_refused_rather_than_serialised(self):
        broken = sample_store()
        broken["owners"][0]["birth"] = float("nan")
        with self.assertRaises(RequestValidationError):
            importer.build_document(broken, source_installation_id=INSTALL)

    def test_a_non_uuid_installation_is_refused(self):
        for bad in (None, "", "not-a-uuid", 12,
                    "11111111111141118111111111111111"):
            with self.assertRaises(RequestValidationError, msg=repr(bad)):
                importer.build_document(sample_store(),
                                        source_installation_id=bad)

    # -- validation before any I/O ------------------------------------
    def test_an_unresolvable_reference_is_refused_before_any_io(self):
        holder = tempfile.TemporaryDirectory(prefix="irica_import_")
        self.addCleanup(holder.cleanup)
        directory = os.path.join(holder.name, "backups")
        os.makedirs(directory)
        sent = []
        store = sample_store()
        store["devices"][0]["customer_id"] = "ffffffff"   # nobody's id

        with self.assertRaises(UnresolvedReferenceCloudError):
            importer.run_import(store, send=sent.append,
                                source_installation_id=INSTALL,
                                backup_dir=directory, now=FROZEN)
        self.assertEqual(sent, [], "nothing may be sent for a bad document")
        self.assertEqual(os.listdir(directory), [],
                         "no backup for a document that will never be sent")

    def test_a_document_missing_an_entity_is_refused(self):
        # split_store always emits all seven; this pins the guard that keeps
        # a future mapper change from silently dropping one from the payload.
        collections, _ = importer.split_document(
            sample_store(), source_installation_id=INSTALL)
        del collections["periods"]
        with self.assertRaises(RequestValidationError):
            importer._validate(collections, INSTALL)

    def test_a_record_without_an_id_is_refused(self):
        collections, _ = importer.split_document(
            sample_store(), source_installation_id=INSTALL)
        collections["owners"][0].pop("id")
        with self.assertRaises(RequestValidationError):
            importer._validate(collections, INSTALL)

    def test_more_than_one_settings_row_is_refused(self):
        collections, _ = importer.split_document(
            sample_store(), source_installation_id=INSTALL)
        collections["settings"].append(dict(collections["settings"][0]))
        with self.assertRaises(RequestValidationError):
            importer._validate(collections, INSTALL)

    # -- spec §3: local-only settings ----------------------------------
    def test_local_only_settings_keys_are_never_forwarded(self):
        collections, _ = importer.split_document(
            sample_store(), source_installation_id=INSTALL)
        wire_settings = collections["settings"][0]
        self.assertEqual(wire_settings["business_name"], "Sample Trading Co")
        for key in LOCAL_ONLY_SETTINGS:
            self.assertNotIn(key, wire_settings)

    def test_local_only_settings_keys_are_surfaced_to_the_caller(self):
        holder = tempfile.TemporaryDirectory(prefix="irica_import_")
        self.addCleanup(holder.cleanup)
        directory = os.path.join(holder.name, "backups")
        os.makedirs(directory)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = importer.run_import(
                sample_store(), send=lambda body: _ok_payload(),
                source_installation_id=INSTALL, backup_dir=directory,
                now=FROZEN)
        self.assertTrue(result.ok)
        self.assertEqual(result.dropped_settings,
                         ("device_token", "profile_dir"))
        self.assertEqual(
            [w.category for w in caught], [SettingsAllowlistWarning,
                                           SettingsAllowlistWarning])

    def test_a_clean_store_reports_nothing_dropped(self):
        collections, dropped = importer.split_document(
            sample_store(with_local_settings=False),
            source_installation_id=INSTALL)
        self.assertEqual(dropped, ())
        self.assertEqual(collections["settings"][0]["business_name"],
                         SETTINGS["business_name"])


# =====================================================================
# Backup (spec §8.3)
# =====================================================================
class TestBackup(TempDirCase):

    def test_a_timestamped_backup_is_taken_and_returned(self):
        result = importer.run_import(
            sample_store(), send=self.document(),
            source_installation_id=INSTALL, backup_dir=self.backup_dir,
            now=FROZEN)
        self.assertTrue(result.ok)
        self.assertTrue(os.path.isfile(result.backup_path))
        name = os.path.basename(result.backup_path)
        self.assertTrue(name.startswith(importer.BACKUP_PREFIX), name)
        self.assertTrue(name.endswith(".json"), name)
        with open(result.backup_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored, sample_store())
        self.assertEqual(os.listdir(self.backup_dir), [name])

    def test_a_second_backup_never_overwrites_the_first(self):
        store = sample_store()
        first = importer.write_backup(store, self.backup_dir, now=FROZEN)
        second = importer.write_backup(store, self.backup_dir, now=FROZEN)
        third = importer.write_backup(store, self.backup_dir, now=FROZEN)
        self.assertEqual(len({first, second, third}), 3)
        for path in (first, second, third):
            self.assertTrue(os.path.isfile(path), path)
        with open(first, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), store)
        self.assertEqual(len(os.listdir(self.backup_dir)), 3)

    def test_a_missing_backup_directory_is_created(self):
        target = os.path.join(self.root, "nested", "backups")
        path = importer.write_backup(sample_store(), target, now=FROZEN)
        self.assertTrue(os.path.isfile(path))

    def test_an_unusable_backup_directory_stops_the_import(self):
        blocked = os.path.join(self.root, "not-a-directory")
        with open(blocked, "w", encoding="utf-8") as handle:
            handle.write("occupied")
        sent = []
        with self.assertRaises(ConfigError):
            importer.run_import(sample_store(), send=sent.append,
                                source_installation_id=INSTALL,
                                backup_dir=blocked, now=FROZEN)
        self.assertEqual(sent, [], "no backup means no request")

    def test_the_backup_is_taken_before_the_request(self):
        observed = []

        def send(body):
            observed.extend(os.listdir(self.backup_dir))
            return _ok_payload()

        result = importer.run_import(sample_store(), send=send,
                                     source_installation_id=INSTALL,
                                     backup_dir=self.backup_dir, now=FROZEN)
        self.assertTrue(result.ok)
        self.assertEqual(len(observed), 1,
                         "the backup must already exist when send() runs")


# =====================================================================
# Outcome classification and retries (spec §8.4, §9)
# =====================================================================
class TestOutcome(TempDirCase):

    def run_import(self, store=None, **kwargs):
        kwargs.setdefault("source_installation_id", INSTALL)
        kwargs.setdefault("backup_dir", self.backup_dir)
        kwargs.setdefault("now", FROZEN)
        return importer.run_import(store or sample_store(), **kwargs)

    def test_a_success_reports_what_the_host_committed(self):
        result = self.run_import(send=self.document())
        self.assertTrue(result.ok, result)
        self.assertEqual(result.code, "")
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.terminal)
        self.assertEqual(result.rows, 8)
        self.assertEqual(result.counts, EXPECTED_COUNTS)
        self.assertEqual(result.cursor, 42)
        self.assertEqual(result.dataset_epoch, 1)
        self.assertTrue(result.backup_path)

    def test_a_409_is_terminal_and_never_retried(self):
        for code in ("DATASET_NOT_EMPTY", "IMPORT_CONFLICT"):
            self.sent.clear()
            result = self.run_import(
                send=self.document(error=HttpError(
                    code=code, route=importer.IMPORT_ROUTE, status=409)),
                max_attempts=5)
            self.assertFalse(result.ok, code)
            self.assertEqual(result.code, code)
            self.assertTrue(result.terminal, code)
            self.assertEqual(result.attempts, 1,
                             "%s must stop on the first attempt" % code)
            self.assertEqual(len(self.sent), 1)
            self.assertIsNone(result.payload)

    def test_a_409_maps_to_the_documented_code_constant(self):
        self.assertEqual(importer.DATASET_NOT_EMPTY, "DATASET_NOT_EMPTY")
        self.assertEqual(importer.IMPORT_CONFLICT, "IMPORT_CONFLICT")

    def test_a_client_error_is_terminal(self):
        for error, code in (
                (AuthRequiredError(route=importer.IMPORT_ROUTE), "AUTH_REQUIRED"),
                (HttpError(code="VALIDATION_FAILED",
                           route=importer.IMPORT_ROUTE, status=400),
                 "VALIDATION_FAILED"),
                (HttpError(route=importer.IMPORT_ROUTE, status=403),
                 "HTTP_403")):
            self.sent.clear()
            result = self.run_import(send=self.document(error=error))
            self.assertFalse(result.ok)
            self.assertEqual(result.code, code)
            self.assertTrue(result.terminal)
            self.assertEqual(result.attempts, 1)

    def test_a_timeout_is_retried_with_the_same_import_id(self):
        sleeps = []
        attempts = []

        def send(body):
            attempts.append(body["import_id"])
            if len(attempts) < 3:
                raise RequestTimeoutError(route=importer.IMPORT_ROUTE)
            return _ok_payload()

        result = self.run_import(send=send, max_attempts=3, base_delay=1.0,
                                 sleep=sleeps.append, rng=lambda: 0.5)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(set(attempts)), 1,
                         "every retry must reuse the idempotent import_id")
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_a_5xx_is_retried(self):
        attempts = []

        def send(body):
            attempts.append(1)
            if len(attempts) < 2:
                raise HttpError(code="HTTP_500",
                                route=importer.IMPORT_ROUTE, status=500)
            return _ok_payload()

        result = self.run_import(send=send, base_delay=0,
                                 sleep=lambda delay: None)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.attempts, 2)

    def test_the_attempt_budget_is_bounded(self):
        sleeps = []

        def send(body):
            raise RequestTimeoutError(route=importer.IMPORT_ROUTE)

        result = self.run_import(send=send, max_attempts=4, base_delay=1.0,
                                 sleep=sleeps.append, rng=lambda: 0.5)
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 4)
        self.assertEqual(len(sleeps), 3, "one nap between attempts, no more")
        self.assertEqual(result.code, NET_TIMEOUT)
        # A transient fault is not a verdict: the caller may try again later.
        self.assertFalse(result.terminal)

    def test_the_budget_is_at_least_one_attempt(self):
        with self.assertRaises(ConfigError):
            self.run_import(send=self.document(), max_attempts=0)

    def test_send_must_be_callable(self):
        for bad in (None, "POST", 42):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                self.run_import(send=bad)

    def test_a_returned_failure_envelope_is_classified_and_terminal(self):
        body = {"ok": False,
                "error": {"code": "DATASET_NOT_EMPTY",
                          "message": "%s: secret server text" % CANARY,
                          "request_id": str(uuid.uuid4())}}
        result = self.run_import(send=self.document(response=body))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DATASET_NOT_EMPTY")
        self.assertTrue(result.terminal)
        self.assertEqual(result.attempts, 1)
        self.assertIsNone(result.payload)

    def test_the_server_message_never_reaches_the_result(self):
        body = {"ok": False, "error": {"code": "IMPORT_CONFLICT",
                                       "message": CANARY,
                                       "request_id": str(uuid.uuid4())}}
        result = self.run_import(send=self.document(response=body))
        self.assertEqual(result.code, "IMPORT_CONFLICT")
        for text in (repr(result), str(result), result.code,
                     json.dumps(result.counts)):
            self.assertNotIn(CANARY, text)
        self.assertNotIn("secret server text", repr(result))

    def test_a_body_that_is_not_an_envelope_is_a_bad_payload(self):
        result = self.run_import(send=self.document(response=["not", "a",
                                                               "dict"]))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "BAD_PAYLOAD")
        self.assertTrue(result.terminal)

    def test_a_non_integer_count_is_not_a_count(self):
        result = self.run_import(
            send=self.document(response={"ok": True, "rows": "eight",
                                         "counts": EXPECTED_COUNTS}))
        self.assertTrue(result.ok)
        self.assertEqual(result.rows, 0)
        self.assertEqual(result.counts, EXPECTED_COUNTS,
                         "a valid counts map is still readable")

    def test_a_raw_requests_timeout_is_classified(self):
        def send(body):
            raise requests.ReadTimeout("read timed out")

        result = self.run_import(send=send, max_attempts=2, base_delay=0,
                                 sleep=lambda delay: None)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, NET_TIMEOUT)
        self.assertEqual(result.attempts, 2)

    def test_a_raw_requests_connection_error_is_classified(self):
        def send(body):
            raise requests.ConnectionError("refused")

        result = self.run_import(send=send, max_attempts=1, base_delay=0,
                                 sleep=lambda delay: None)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NET_UNREACHABLE")
        self.assertFalse(result.terminal)

    def test_the_reader_callable_is_supported(self):
        calls = []

        def read():
            calls.append(1)
            return sample_store()

        result = self.run_import(store=read, send=self.document())
        self.assertTrue(result.ok, result)
        self.assertEqual(calls, [1])

    def test_repr_carries_no_business_data(self):
        result = self.run_import(send=self.document())
        text = repr(result)
        for value in ("Sample Customer", "09120000001", "0012345679",
                      "Sample Trading Co"):
            self.assertNotIn(value, text)


# =====================================================================
# End to end against the loopback fake (spec §12)
# =====================================================================
class _ActingUserSession(requests.Session):
    """Adds the spec §6 ``X-Acting-User`` header.

    ``SyncClient`` has no hook for extra headers, and its import route does
    not exist yet, so the session is where the second credential of §8.2 (an
    admin **user** token, next to the device bearer) enters the request.
    """

    def __init__(self, vault):
        super().__init__()
        self._vault = vault

    def request(self, method, url, **kwargs):
        token = self._vault.user_access_token
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers["X-Acting-User"] = "Bearer " + token
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


class HttpCase(unittest.TestCase):
    """The real transport against the loopback fake; never a real host."""

    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory(prefix="irica_import_http_")
        self.addCleanup(holder.cleanup)
        self.root = holder.name
        self.backup_dir = os.path.join(self.root, "backups")
        os.makedirs(self.backup_dir)

        self.handle = fake_api.start_api(self, needs_fake=True)
        self.api = self.handle.fake
        self.vault = TokenVault(store=MemorySecretStore(),
                                path=os.path.join(self.root, AUTH_FILE_NAME))
        self.config = CloudConfig(
            api_base=self.handle.base_url, tenant_id=self.api.tenant_id,
            source_installation_id=INSTALL,
            path=os.path.join(self.root, CONFIG_FILE_NAME))
        self.client = SyncClient(self.config, self.vault,
                                 session=_ActingUserSession(self.vault))
        # log in as the fake admin, then enrol this machine (spec §5/§8.2)
        self.client.login(fake_api.FAKE_USERNAME, fake_api.FAKE_PASSWORD)
        self.client.enroll()

    def send(self, body):
        # `_request` is the transport: it maps the failure envelope to a typed
        # error and drops the server's message, which is what is under test.
        return self.client._request("POST", importer.IMPORT_ROUTE, body=body,
                                    auth=True, allow_refresh=True,
                                    user_auth=False)

    def run_import(self, store=None, **kwargs):
        kwargs.setdefault("source_installation_id", INSTALL)
        kwargs.setdefault("backup_dir", self.backup_dir)
        kwargs.setdefault("now", FROZEN)
        return importer.run_import(store or sample_store(), send=self.send,
                                   **kwargs)


class TestHttpRoundTrip(HttpCase):

    def test_the_document_is_accepted_and_the_rows_land(self):
        result = self.run_import()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.code, "")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.rows, 8)
        self.assertEqual(result.counts, EXPECTED_COUNTS)
        self.assertGreater(result.cursor, 0)
        self.assertTrue(os.path.isfile(result.backup_path))

        customer_id = legacy_to_uuid(INSTALL, "customers", "a1b2c3d4")
        customers = self.api.collections()["customers"]
        self.assertEqual(list(customers), [customer_id])
        self.assertEqual(customers[customer_id]["record"]["full_name"],
                         "Sample Customer")
        device_id = legacy_to_uuid(INSTALL, "crm_devices", "d1d2d3d4")
        device = self.api.collections()["crm_devices"][device_id]["record"]
        self.assertEqual(device["customer_id"], customer_id)
        self.assertEqual(len(self.api.collections()["customer_transactions"]), 2)
        self.assertEqual(len(self.api.imports), 1)

    def test_the_same_document_replays_the_same_result(self):
        first = self.run_import()
        second = self.run_import()
        self.assertTrue(first.ok, first)
        self.assertTrue(second.ok, second)
        self.assertEqual(second.import_id, first.import_id)
        self.assertEqual(second.payload, first.payload,
                         "spec §8.4: a replay answers byte for byte")
        self.assertEqual(second.rows, first.rows)
        self.assertEqual(len(self.api.imports), 1)
        self.assertEqual(len(self.api.calls(importer.IMPORT_ROUTE)), 2)

    def test_a_changed_document_is_refused_as_dataset_not_empty(self):
        first = self.run_import()
        self.assertTrue(first.ok, first)
        edited = sample_store()
        edited["customers"][0]["full_name"] = "Another Sample"
        result = self.run_import(edited)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DATASET_NOT_EMPTY")
        self.assertTrue(result.terminal)
        self.assertEqual(result.attempts, 1)
        self.assertNotEqual(result.import_id, first.import_id)
        self.assertEqual(len(self.api.imports), 1,
                         "the refused import must not be recorded")

    def test_a_server_error_is_retried_with_the_same_import_id(self):
        self.api.fail_next("POST " + importer.IMPORT_ROUTE, 500, "HTTP_500",
                           count=2)
        result = self.run_import()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(self.api.calls(importer.IMPORT_ROUTE)), 3)
        self.assertEqual(len(self.api.imports), 1, "one import, applied once")

    def test_a_409_from_the_server_is_terminal_and_hides_its_message(self):
        self.api.fail_next("POST " + importer.IMPORT_ROUTE, 409,
                           "DATASET_NOT_EMPTY", count=1)
        result = self.run_import()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DATASET_NOT_EMPTY")
        self.assertTrue(result.terminal)
        self.assertEqual(result.attempts, 1)
        self.assertNotIn(CANARY, repr(result))
        self.assertNotIn(CANARY, str(result))
        self.assertIsNone(result.payload)

    def test_an_import_without_an_acting_user_is_refused(self):
        bare = SyncClient(self.config, self.vault)   # no X-Acting-User
        result = importer.run_import(
            sample_store(),
            send=lambda body: bare._request(
                "POST", importer.IMPORT_ROUTE, body=body,
                auth=True, allow_refresh=True, user_auth=False),
            source_installation_id=INSTALL, backup_dir=self.backup_dir,
            now=FROZEN)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "AUTH_REQUIRED")
        self.assertTrue(result.terminal)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(self.api.imports, {}, "nothing may be imported")

    def test_a_non_admin_acting_user_is_refused(self):
        self.api.users["member"] = {"password": "FAKE-member-password",
                                    "role": "user",
                                    "must_change_password": False}
        self.client.login("member", "FAKE-member-password")
        result = self.run_import()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "HTTP_403")
        self.assertTrue(result.terminal)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(self.api.imports, {}, "nothing may be imported")

    def test_a_device_without_enrolment_cannot_import(self):
        self.vault.revoke_local()
        result = self.run_import()
        self.assertFalse(result.ok)
        self.assertTrue(result.terminal)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(self.api.imports, {})

    def test_the_settings_allowlist_survives_the_wire(self):
        result = self.run_import()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.dropped_settings,
                         ("device_token", "profile_dir"))
        settings = self.api.collections()["settings"]
        record = list(settings.values())[0]["record"]
        self.assertEqual(record["business_name"], "Sample Trading Co")
        self.assertNotIn("device_token", record)
        self.assertNotIn("profile_dir", record)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
