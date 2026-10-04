"""Cloud-sync mapping layer (workstream B) — offline, mock data only.

Covers cloud_sync.ids and cloud_sync.mapper: id determinism, JSON document ⇄
SQL row round trips for every collection, nested-transaction lift/restore, the
settings allowlist, timezone conversion and validation rejections.

Run:
  "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_cloud_mapper

No network, no real customer data, no writes to %LOCALAPPDATA%\\irica\\ — the
single crm.py-driven test points IRICA_CRM_STORE at a throwaway directory.
"""
import datetime
import inspect
import os
import shutil
import sys
import tempfile
import unittest
import warnings

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cloud_sync import ids, mapper


# --------------------------------------------------------------------------
# Hand-built fixtures, shaped like real crm_store.json records
# --------------------------------------------------------------------------

TRANSACTIONS = [
    {"at": "2026-09-01 10:05:00", "amount": -24000000,
     "note": "registry fee, sample device", "kind": "base_fee", "image": "",
     "device_id": "d1d2d3d4", "voided_at": "2026-09-04 09:00:00",
     "void_reason": "sample void reason"},
    {"at": "2026-09-01 10:06:00", "amount": 5000000,
     "note": "sample receipt", "kind": "receipt", "image": "sample01.png",
     "device_id": "d1d2d3d4"},
    {"at": "2026-09-04 09:00:01", "amount": 24000000,
     "note": "void reversal", "kind": "void", "image": "", "device_id": ""},
]

CUSTOMER = {
    "id": "a1b2c3d4", "full_name": "Sample Customer",
    "address": "Sample Street 1", "mobile": "09120000001",
    "telegram_id": "@sample_tg", "instagram_id": "@sample_ig",
    "notes": "sample note",
    "transactions": list(TRANSACTIONS),
    "created": "2026-09-01 10:00:00", "updated": "2026-09-05 14:00:00",
}

OWNER = {
    # `updated` is absent on purpose: crm.add_owner never writes it.
    "id": "e5f6a7b8", "first_name": "Sample", "last_name": "Passenger",
    "national": "0012345679", "birth": "1375/04/29", "mobile": "09120000002",
    "card_no": "6037991234567890", "passport": "AB1234567",
    "created": "2026-09-01 10:10:00",
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
    # Columns written after creation (crm stage machine / panel):
    "skipped_stages": [{"stage": "inquiry", "to": "ready_owner",
                        "at": "2026-09-01 11:00:00"}],
    "closed_at": "2026-09-05 14:00:00",
    "closed_reason": "sample closed reason",
    "periodic_opt_out": True,
    "payment_started_at": "2026-09-02 10:30:00",
}

PERIOD_OPEN = {
    "id": "aa11bb22", "number": 1, "label": "sample period one",
    "opened_at": "2026-09-01 00:00:00", "opened_by": "operator",
    "closed_at": "", "closed_by": "", "closed_reason": "",
}

PERIOD_CLOSED = {
    "id": "bb33cc44", "number": 2, "label": "sample period two",
    "opened_at": "2026-09-01 00:00:00", "opened_by": "operator",
    "closed_at": "2026-09-06 18:00:00", "closed_by": "operator",
    "closed_reason": "sample month end",
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

# Device-local / secret-shaped keys that must never reach the wire (spec §3).
LOCAL_ONLY_SETTINGS = {
    "device_token": "placeholder",
    "profile_dir": "C:/placeholder/profiles",
}


def store_fixture():
    """A complete, hand-built CRM document with every synced collection."""
    return {
        "version": 1,
        "customers": [dict(CUSTOMER, transactions=list(TRANSACTIONS))],
        "owners": [dict(OWNER)],
        "identities": [dict(IDENTITY)],
        "devices": [dict(DEVICE)],
        "periods": [dict(PERIOD_OPEN), dict(PERIOD_CLOSED)],
        "settings": dict(SETTINGS),
    }


class FixtureTestCase(unittest.TestCase):
    """Round-trip helper: from_row(to_row(r)) must equal the projection."""

    def assertRoundTrip(self, entity, record):
        """``from_row(to_row(record))`` must give the projection back."""
        row = mapper.to_row(entity, record)
        self.assertLessEqual(
            set(row), set(mapper.ENTITY_FIELDS[entity]),
            f"{entity}: to_row emitted an undeclared column")
        self.assertEqual(mapper.from_row(entity, row),
                         mapper.project(entity, record))
        return row

    def assertRowRoundTrip(self, entity, row):
        """``to_row(from_row(row))`` must give an already-built row back."""
        self.assertEqual(mapper.to_row(entity, mapper.from_row(entity, row)),
                         row)
        return row


# --------------------------------------------------------------------------
# ids.py
# --------------------------------------------------------------------------

class IdTests(unittest.TestCase):
    INSTALL_A = "11111111-1111-4111-8111-111111111111"
    INSTALL_B = "22222222-2222-4222-8222-222222222222"

    def test_same_triple_maps_to_the_same_uuid(self):
        first = ids.legacy_to_uuid(self.INSTALL_A, "customers", "a1b2c3d4")
        second = ids.legacy_to_uuid(self.INSTALL_A, "customers", "a1b2c3d4")
        self.assertEqual(first, second)

    def test_a_different_installation_maps_to_a_different_uuid(self):
        mine = ids.legacy_to_uuid(self.INSTALL_A, "customers", "a1b2c3d4")
        theirs = ids.legacy_to_uuid(self.INSTALL_B, "customers", "a1b2c3d4")
        self.assertNotEqual(mine, theirs)

    def test_entity_separates_the_namespaces(self):
        customer = ids.legacy_to_uuid(self.INSTALL_A, "customers", "a1b2c3d4")
        device = ids.legacy_to_uuid(self.INSTALL_A, "crm_devices", "a1b2c3d4")
        self.assertNotEqual(customer, device)

    def test_result_is_a_36_character_uuid(self):
        value = ids.legacy_to_uuid(self.INSTALL_A, "periods", "aa11bb22")
        self.assertEqual(len(value), 36)
        self.assertEqual(value.count("-"), 4)

    def test_new_uuid_is_fresh_and_well_formed(self):
        first, second = ids.new_uuid(), ids.new_uuid()
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 36)

    def test_blank_arguments_are_rejected(self):
        for args in (("", "customers", "x"), (self.INSTALL_A, "customers", ""),
                     (self.INSTALL_A, "", "x")):
            with self.assertRaises(ValueError):
                ids.legacy_to_uuid(*args)

    def test_non_string_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            ids.legacy_to_uuid(None, "customers", "x")
        with self.assertRaises(ValueError):
            ids.legacy_to_uuid(self.INSTALL_A, "customers", 42)

    def test_unknown_entity_is_rejected(self):
        with self.assertRaises(ValueError):
            ids.legacy_to_uuid(self.INSTALL_A, "customer", "a1b2c3d4")

    def test_all_spec_entities_are_accepted(self):
        for entity in ids.ENTITIES:
            self.assertEqual(
                len(ids.legacy_to_uuid(self.INSTALL_A, entity, "x")), 36)


# --------------------------------------------------------------------------
# Time conversion
# --------------------------------------------------------------------------

class TimeTests(unittest.TestCase):
    def test_to_wire_is_utc_with_offset(self):
        self.assertEqual(mapper.to_wire_time("2026-01-01 03:30:00"),
                         "2026-01-01T00:00:00+00:00")

    def test_store_format_round_trips_exactly(self):
        for stamp in ("2026-09-01 10:05:00", "1999-12-31 23:59:59",
                      "2026-01-01 00:00:00"):
            wire = mapper.to_wire_time(stamp)
            self.assertEqual(mapper.from_wire_time(wire), stamp)
            self.assertTrue(wire.endswith("+00:00"))

    def test_unset_timestamps_round_trip_to_empty_string(self):
        self.assertIsNone(mapper.to_wire_time(""))
        self.assertIsNone(mapper.to_wire_time(None))
        self.assertEqual(mapper.from_wire_time(None), "")
        self.assertEqual(mapper.from_wire_time(""), "")
        self.assertEqual(mapper.from_wire_time(
            mapper.to_wire_time("")), "")

    def test_unparsable_store_timestamp_is_rejected(self):
        with self.assertRaises(mapper.InvalidTimestampError):
            mapper.to_wire_time("01/09/2026 10:00:00")
        with self.assertRaises(mapper.InvalidTimestampError):
            mapper.to_wire_time("2026-09-01")

    def test_wire_timestamp_without_offset_is_rejected(self):
        with self.assertRaises(mapper.InvalidTimestampError):
            mapper.from_wire_time("2026-09-01T10:00:00")

    def test_non_string_timestamp_is_rejected(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_wire_time(1756700000)
        with self.assertRaises(mapper.FieldTypeError):
            mapper.from_wire_time(1756700000)

    def test_source_timezone_is_injected_not_read_from_the_system(self):
        # An explicit constant, not datetime.now().astimezone() / tzlocal().
        utc = datetime.timezone.utc
        self.assertEqual(mapper.to_wire_time("2026-01-01 03:30:00", tz=utc),
                         "2026-01-01T03:30:00+00:00")
        self.assertEqual(mapper.to_wire_time("2026-01-01 03:30:00"),
                         mapper.to_wire_time("2026-01-01 03:30:00",
                                             tz=mapper.IRAN_TZ))
        self.assertEqual(mapper.IRAN_TZ.utcoffset(None),
                         datetime.timedelta(hours=3, minutes=30))
        # Round trip through a caller-supplied zone is lossless too.
        self.assertEqual(mapper.from_wire_time(
            mapper.to_wire_time("2026-06-15 12:00:00", tz=utc), tz=utc),
            "2026-06-15 12:00:00")

    def test_module_never_reads_the_system_clock_or_zone(self):
        source = inspect.getsource(mapper)
        self.assertNotIn("datetime.now", source)
        self.assertNotIn("astimezone()", source)
        self.assertNotIn("time.strftime", source)


# --------------------------------------------------------------------------
# Round trip for every collection
# --------------------------------------------------------------------------

class RoundTripTests(FixtureTestCase):
    def test_customers(self):
        row = self.assertRoundTrip("customers", CUSTOMER)
        self.assertEqual(row["created"], "2026-09-01T06:30:00+00:00")
        self.assertNotIn("transactions", row)

    def test_customer_transactions(self):
        for tx in TRANSACTIONS:
            self.assertRoundTrip("customer_transactions", tx)
        # lift_transactions already returns rows (wire timestamps), so the
        # row-level round trip runs the other way round.
        for row in mapper.lift_transactions(CUSTOMER):
            self.assertRowRoundTrip("customer_transactions", row)

    def test_owners(self):
        row = self.assertRoundTrip("owners", OWNER)
        self.assertNotIn("updated", row)   # add_owner never writes it

    def test_identities(self):
        self.assertRoundTrip("identities", IDENTITY)

    def test_crm_devices(self):
        row = self.assertRoundTrip("crm_devices", DEVICE)
        self.assertEqual(row["skipped_stages"],
                         [{"stage": "inquiry", "to": "ready_owner",
                           "at": "2026-09-01T07:30:00+00:00"}])
        self.assertIs(row["periodic_opt_out"], True)

    def test_periods_open_and_closed(self):
        for period in (PERIOD_OPEN, PERIOD_CLOSED):
            self.assertRoundTrip("periods", period)
        open_row = mapper.to_row("periods", PERIOD_OPEN)
        self.assertIsNone(open_row["closed_at"])   # "" -> SQL NULL
        self.assertEqual(mapper.from_row("periods", open_row)["closed_at"], "")

    def test_settings(self):
        self.assertRoundTrip("settings", SETTINGS)

    def test_sparse_records_do_not_gain_fields(self):
        sparse = {"id": "a1b2c3d4", "full_name": "Sample Customer"}
        self.assertEqual(mapper.from_row("customers",
                                         mapper.to_row("customers", sparse)),
                         sparse)

    def test_lifted_transactions_are_never_in_the_customer_row(self):
        row = mapper.to_row("customers", CUSTOMER)
        self.assertEqual(set(row), set(mapper.ENTITY_FIELDS["customers"]))
        self.assertNotIn("transactions", row)


# --------------------------------------------------------------------------
# Nested transactions
# --------------------------------------------------------------------------

class TransactionTests(FixtureTestCase):
    def test_lift_adds_seq_and_customer_id(self):
        rows = mapper.lift_transactions(CUSTOMER)
        self.assertEqual([row["seq"] for row in rows], [0, 1, 2])
        self.assertEqual({row["customer_id"] for row in rows},
                         {CUSTOMER["id"]})

    def test_lift_restore_round_trip_preserves_order(self):
        rows = mapper.lift_transactions(CUSTOMER)
        self.assertEqual(mapper.restore_transactions(rows),
                         CUSTOMER["transactions"])

    def test_restore_orders_by_seq_even_when_the_rows_arrive_shuffled(self):
        rows = mapper.lift_transactions(CUSTOMER)
        shuffled = [rows[2], rows[0], rows[1]]
        restored = mapper.restore_transactions(shuffled)
        self.assertEqual(restored, CUSTOMER["transactions"])

    def test_restore_orders_by_seq_when_the_sequence_is_not_contiguous(self):
        rows = mapper.lift_transactions(CUSTOMER)
        rows[0]["seq"], rows[1]["seq"] = 7, 3   # seqs become 7, 3, 2
        restored = mapper.restore_transactions(rows)
        self.assertEqual(restored, [CUSTOMER["transactions"][2],
                                    CUSTOMER["transactions"][1],
                                    CUSTOMER["transactions"][0]])

    def test_lift_rejects_a_non_dict_entry(self):
        customer = dict(CUSTOMER, transactions=["not a ledger row"])
        with self.assertRaises(mapper.FieldTypeError):
            mapper.lift_transactions(customer)

    def test_lift_requires_the_customer_id(self):
        with self.assertRaises(mapper.MissingFieldError):
            mapper.lift_transactions({"transactions": []})

    def test_restore_rejects_a_missing_seq(self):
        with self.assertRaises(mapper.MissingFieldError):
            mapper.restore_transactions([{"amount": 1}])

    def test_restore_rejects_a_non_integer_seq(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.restore_transactions([{"seq": "0", "amount": 1}])
        with self.assertRaises(mapper.FieldTypeError):
            mapper.restore_transactions([{"seq": True, "amount": 1}])

    def test_a_voided_row_keeps_its_void_metadata(self):
        restored = mapper.restore_transactions(mapper.lift_transactions(
            CUSTOMER))
        self.assertEqual(restored[0]["void_reason"], "sample void reason")
        self.assertEqual(restored[0]["voided_at"], "2026-09-04 09:00:00")
        self.assertNotIn("voided_at", restored[1])


# --------------------------------------------------------------------------
# Settings allowlist (spec §3)
# --------------------------------------------------------------------------

class SettingsTests(FixtureTestCase):
    SPEC_ALLOWLIST = {
        "business_name", "business_phone", "watch_interval_hours",
        "scheduler_enabled", "auto_advance_default",
        "telegram_report_full_data", "registry_fee_amount",
        "registration_chat_id", "report_chat_id", "sound_enabled",
        "current_period_id", "links",
    }

    def test_allowlist_matches_the_spec(self):
        self.assertEqual(set(mapper.SETTINGS_ALLOWLIST),
                         self.SPEC_ALLOWLIST)
        self.assertEqual(set(mapper.ENTITY_FIELDS["settings"]),
                         self.SPEC_ALLOWLIST)

    def test_token_like_and_path_like_keys_never_reach_the_wire(self):
        with self.assertWarns(mapper.SettingsAllowlistWarning):
            syncable, local_only = mapper.split_settings(
                dict(SETTINGS, **LOCAL_ONLY_SETTINGS))
        self.assertNotIn("device_token", syncable)
        self.assertNotIn("profile_dir", syncable)
        self.assertEqual(sorted(local_only),
                         ["device_token", "profile_dir"])

    def test_to_row_drops_a_secret_looking_key(self):
        with self.assertWarns(mapper.SettingsAllowlistWarning):
            row = mapper.to_row("settings",
                                dict(SETTINGS, device_token="placeholder"))
        self.assertNotIn("device_token", row)
        self.assertEqual(set(row), set(mapper.SETTINGS_ALLOWLIST))

    def test_split_returns_the_syncable_subset_unchanged(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", mapper.SettingsAllowlistWarning)
            syncable, local_only = mapper.split_settings(dict(SETTINGS))
        self.assertEqual(syncable, SETTINGS)
        self.assertEqual(local_only, {})

    def test_merge_keeps_local_only_keys_locally(self):
        base = dict(SETTINGS, **LOCAL_ONLY_SETTINGS)
        merged = mapper.merge_settings(base, {"business_name": "New Name"})
        self.assertEqual(merged["business_name"], "New Name")
        self.assertEqual(merged["device_token"], "placeholder")
        self.assertEqual(merged["profile_dir"], "C:/placeholder/profiles")

    def test_merge_drops_an_unknown_incoming_key_with_a_warning(self):
        base = dict(SETTINGS, **LOCAL_ONLY_SETTINGS)
        with self.assertWarns(mapper.SettingsAllowlistWarning):
            merged = mapper.merge_settings(base, {"device_token": "incoming"})
        self.assertEqual(merged["device_token"], "placeholder")

    def test_merge_rejects_a_value_of_the_wrong_type(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.merge_settings(dict(SETTINGS),
                                  {"watch_interval_hours": "two"})

    def test_to_row_rejects_a_value_of_the_wrong_type(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("settings", dict(SETTINGS, sound_enabled="yes"))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("settings", dict(SETTINGS, links="https://x.invalid"))

    def test_a_local_only_key_is_reported_by_split_store(self):
        store = store_fixture()
        store["settings"]["device_token"] = "placeholder"
        with self.assertWarns(mapper.SettingsAllowlistWarning):
            rows = mapper.split_store(store)
        self.assertNotIn("device_token", rows["settings"])

    def test_from_row_drops_a_foreign_settings_key(self):
        # The host payload is untrusted input: a key the allowlist does not
        # name must never reach the local settings dict.
        with self.assertWarns(mapper.SettingsAllowlistWarning):
            record = mapper.from_row("settings", {
                "business_name": "Sample Trading Co",
                "device_token": "placeholder",
                "profile_dir": "C:/placeholder/profiles"})
        self.assertEqual(record, {"business_name": "Sample Trading Co"})


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

class ValidationTests(FixtureTestCase):
    def test_unknown_entity_is_rejected(self):
        with self.assertRaises(mapper.UnknownEntityError):
            mapper.to_row("customer", CUSTOMER)
        with self.assertRaises(mapper.UnknownEntityError):
            mapper.from_row("devices", {})
        with self.assertRaises(mapper.UnknownEntityError):
            mapper.envelope("customer", "x",
                            source_installation_id="i", tenant_id="t")

    def test_a_record_without_an_id_is_rejected(self):
        for entity, record in (("customers", CUSTOMER), ("owners", OWNER),
                               ("identities", IDENTITY),
                               ("crm_devices", DEVICE),
                               ("periods", PERIOD_OPEN)):
            with self.assertRaises(mapper.MissingFieldError, msg=entity):
                mapper.to_row(entity,
                              {k: v for k, v in record.items() if k != "id"})
            row = {k: v for k, v in mapper.to_row(entity, record).items()
                   if k != "id"}
            with self.assertRaises(mapper.MissingFieldError, msg=entity):
                mapper.from_row(entity, row)

    def test_an_empty_or_non_string_id_is_rejected(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("customers", dict(CUSTOMER, id=""))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("customers", dict(CUSTOMER, id=None))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("customers", dict(CUSTOMER, id=1234))

    def test_a_non_dict_record_is_rejected(self):
        for bad in (None, [], "customers", 7):
            with self.assertRaises(mapper.FieldTypeError):
                mapper.to_row("customers", bad)
            with self.assertRaises(mapper.FieldTypeError):
                mapper.from_row("customers", bad)

    def test_wrong_types_are_rejected_not_coerced(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("owners", dict(OWNER, passport=1234))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("periods", dict(PERIOD_OPEN, number="1"))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("crm_devices", dict(DEVICE, auto_advance="true"))
        with self.assertRaises(mapper.FieldTypeError):
            # bool is not an int here — advance_attempts is never a flag.
            mapper.to_row("crm_devices", dict(DEVICE, advance_attempts=True))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.to_row("crm_devices",
                          dict(DEVICE, created="2026-09-01 10:12"))

    def test_from_row_rejects_a_malformed_row(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.from_row("periods", dict(PERIOD_OPEN, number="1"))
        with self.assertRaises(mapper.InvalidTimestampError):
            mapper.from_row("customers", {"id": "a1b2c3d4",
                                          "created": "not-a-timestamp"})

    def test_an_undeclared_field_is_dropped_with_a_warning(self):
        with self.assertWarns(mapper.UnexpectedFieldWarning):
            row = mapper.to_row("customers", dict(CUSTOMER, mystery="x"))
        self.assertNotIn("mystery", row)

    def test_lifted_fields_are_silent_not_warned(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            row = mapper.to_row("customers", CUSTOMER)
        self.assertNotIn("transactions", row)

    def test_from_row_ignores_the_sync_envelope(self):
        row = dict(mapper.to_row("customers", CUSTOMER),
                   tenant_id="t1", row_version=3, deleted_at=None)
        self.assertEqual(mapper.from_row("customers", row),
                         mapper.project("customers", CUSTOMER))

    def test_from_row_warns_on_an_undeclared_column(self):
        with self.assertWarns(mapper.UnexpectedFieldWarning):
            record = mapper.from_row("customers", {"id": "a1b2c3d4",
                                                   "mystery": 1})
        self.assertNotIn("mystery", record)


# --------------------------------------------------------------------------
# Sync envelope (spec §4)
# --------------------------------------------------------------------------

class EnvelopeTests(FixtureTestCase):
    INSTALL = "11111111-1111-4111-8111-111111111111"

    def test_envelope_id_is_the_deterministic_uuid(self):
        env = mapper.envelope("customers", "a1b2c3d4",
                              source_installation_id=self.INSTALL,
                              tenant_id="tenant-1")
        self.assertEqual(env["id"],
                         ids.legacy_to_uuid(self.INSTALL, "customers",
                                            "a1b2c3d4"))
        self.assertEqual(env["row_version"], 1)
        self.assertEqual(env["tenant_id"], "tenant-1")

    def test_envelope_carries_only_client_known_columns(self):
        env = mapper.envelope("crm_devices", "d1d2d3d4",
                              source_installation_id=self.INSTALL,
                              tenant_id="tenant-1", row_version=4)
        # created_at/updated_at are server-minted; deleted_* come with a
        # tombstone. Neither is produced here.
        self.assertEqual(set(env), {"id", "tenant_id", "row_version"})

    def test_envelope_and_record_payload_only_share_the_id_key(self):
        payload = set(mapper.ENTITY_FIELDS["customers"])
        env = set(mapper.envelope("customers", "a1b2c3d4",
                                  source_installation_id=self.INSTALL,
                                  tenant_id="tenant-1"))
        self.assertEqual(env & payload, {"id"})

    def test_envelope_validates_its_arguments(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.envelope("customers", "a1b2c3d4",
                            source_installation_id=self.INSTALL, tenant_id="")
        with self.assertRaises(mapper.FieldTypeError):
            mapper.envelope("customers", "a1b2c3d4",
                            source_installation_id=self.INSTALL,
                            tenant_id="t", row_version=0)


# --------------------------------------------------------------------------
# Document level
# --------------------------------------------------------------------------

class StoreRoundTripTests(FixtureTestCase):
    def test_split_then_merge_restores_the_document(self):
        store = store_fixture()
        with warnings.catch_warnings():
            warnings.simplefilter("error", mapper.SettingsAllowlistWarning)
            rows = mapper.split_store(store)
        merged = mapper.merge_store(rows)

        for key in ("customers", "owners", "identities", "devices", "periods"):
            self.assertEqual(merged[key], store[key], key)
        self.assertEqual(merged["settings"], SETTINGS)   # version not synced

    def test_split_stores_one_row_per_entity_plus_transactions(self):
        rows = mapper.split_store(store_fixture())
        self.assertIsInstance(rows["settings"], dict)    # one row, not a list
        for entity in ("customers", "owners", "identities", "crm_devices",
                       "periods", "customer_transactions"):
            self.assertIsInstance(rows[entity], list, entity)
        self.assertEqual(len(rows["customer_transactions"]),
                         len(TRANSACTIONS))
        self.assertNotIn("version", rows)

    def test_merge_rejects_a_transaction_for_an_unknown_customer(self):
        rows = mapper.split_store(store_fixture())
        orphan = dict(rows["customer_transactions"][0],
                      customer_id="ffffffff")
        rows["customer_transactions"] = [orphan]
        with self.assertRaises(mapper.MappingError):
            mapper.merge_store(rows)

    def test_split_rejects_a_malformed_collection(self):
        with self.assertRaises(mapper.FieldTypeError):
            mapper.split_store(dict(store_fixture(), periods={}))
        with self.assertRaises(mapper.FieldTypeError):
            mapper.split_store({"devices": [dict(DEVICE, id="")]})

    def test_a_device_row_is_flattened_exactly_once(self):
        rows = mapper.split_store(store_fixture())
        self.assertEqual(set(rows["crm_devices"][0]),
                         set(mapper.ENTITY_FIELDS["crm_devices"]))


# --------------------------------------------------------------------------
# Cross-check against the code that actually writes the store
# --------------------------------------------------------------------------

class RealStoreRecordTests(FixtureTestCase):
    """Records produced by crm.py itself must map losslessly.

    This is the guard against a column added to crm.py/panel.py that
    ENTITY_FIELDS never learned about.
    """

    def setUp(self):
        scratch = tempfile.mkdtemp(prefix="irica_cloud_mapper_")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        self._old = os.environ.get("IRICA_CRM_STORE")
        os.environ["IRICA_CRM_STORE"] = os.path.join(scratch,
                                                     "crm_store.json")

        def restore():
            if self._old is None:
                os.environ.pop("IRICA_CRM_STORE", None)
            else:
                os.environ["IRICA_CRM_STORE"] = self._old
        self.addCleanup(restore)

        import crm
        crm.reset_cache()
        self.crm = crm

    def _build_store(self):
        crm = self.crm
        customer, errs = crm.add_customer({
            "full_name": "Sample Customer", "address": "Sample Street 1",
            "mobile": "09120000001", "telegram_id": "@sample_tg",
            "instagram_id": "@sample_ig", "notes": "sample note"})
        self.assertEqual(errs, [])

        crm.add_transaction(customer["id"], 500000, note="sample receipt",
                            kind="receipt", image="", device_id="")
        owner, errs = crm.add_owner({
            "first_name": "Sample", "last_name": "Passenger",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09120000002", "card_no": "6037991234567890",
            "passport": "AB1234567"})
        self.assertEqual(errs, [])
        identity, errs = crm.add_identity({
            "label": "sample pool entry", "national": "0012345679",
            "birth": "1375/04/29", "mobile": "09120000003"})
        self.assertEqual(errs, [])

        period, errs = crm.open_period(label="sample period",
                                       actor="operator")
        self.assertEqual(errs, [])
        device, errs = crm.add_device({
            "name": "sample device", "serial1": "123456789012345",
            "serial2": "543210987654321", "customer_id": customer["id"],
            "auto_advance": True, "notes": "sample case notes"})
        self.assertEqual(errs, [])
        self.assertEqual(device["period_id"], period["id"])

        _, errs = crm.attach_owner(device["id"], owner["id"])
        self.assertEqual(errs, [])
        _, errs = crm.advance(device["id"], "stage_skip")
        self.assertEqual(errs, [])
        _, errs = crm.advance(device["id"], "close_case",
                              reason="sample closed reason")
        self.assertEqual(errs, [])
        _, errs = crm.void_transaction(customer["id"], 0, "sample reason")
        self.assertEqual(errs, [])
        _, errs = crm.close_period(period["id"], actor="operator",
                                   reason="sample month end")
        self.assertEqual(errs, [])

        # The two device columns panel.py writes, mirroring its own
        # read-modify-write under the store lock.
        data = crm.store_read()
        data["devices"][0]["periodic_opt_out"] = True
        data["devices"][0]["payment_started_at"] = "2026-09-02 10:30:00"
        crm.store_write(data)

        data = crm.store_read()
        self.assertIsNotNone(identity)
        return data

    def test_every_field_crm_writes_is_declared(self):
        data = self._build_store()
        for store_key, entity in mapper.STORE_COLLECTIONS:
            records = data.get(store_key) or []
            self.assertTrue(records, store_key)
            for record in records:
                unknown = (set(record) - set(mapper.ENTITY_FIELDS[entity])
                           - mapper.LIFTED_OUT_FIELDS.get(entity, frozenset()))
                self.assertEqual(
                    unknown, set(),
                    f"{store_key} record declares no column for {sorted(unknown)}")

    def test_real_records_round_trip_and_split_merge(self):
        data = self._build_store()
        for store_key, entity in mapper.STORE_COLLECTIONS:
            for record in data.get(store_key) or []:
                self.assertEqual(
                    mapper.from_row(entity, mapper.to_row(entity, record)),
                    mapper.project(entity, record), store_key)

        for customer in data["customers"]:
            rows = mapper.lift_transactions(customer)
            self.assertEqual(mapper.restore_transactions(rows),
                             customer["transactions"])
            for tx in customer["transactions"]:
                self.assertEqual(
                    mapper.from_row("customer_transactions",
                                    mapper.to_row("customer_transactions",
                                                  tx)),
                    mapper.project("customer_transactions", tx))
            for row in rows:
                self.assertRowRoundTrip("customer_transactions", row)

        with warnings.catch_warnings():
            warnings.simplefilter("error", mapper.SettingsAllowlistWarning)
            rows = mapper.split_store(data)
        merged = mapper.merge_store(rows)
        for key in ("customers", "owners", "identities", "devices", "periods"):
            self.assertEqual(merged[key], data[key], key)
        syncable, _local = mapper.split_settings(data["settings"])
        self.assertEqual(merged["settings"], syncable)

    def test_stage_and_closure_columns_are_present(self):
        data = self._build_store()
        device = data["devices"][0]
        for key in ("skipped_stages", "closed_at", "closed_reason",
                    "periodic_opt_out", "payment_started_at"):
            self.assertIn(key, device, key)
        self.assertTrue(data["customers"][0]["transactions"][0]["voided_at"])


if __name__ == "__main__":
    unittest.main()
