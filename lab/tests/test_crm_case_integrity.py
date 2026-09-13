"""Case/owner record integrity (user items 2+5+10+13):

1. serial1 is the immutable case identity — corrections require deleting the
   case and registering a new device; serial2 stays fully editable.
2. The passenger<->device 1:1 binding holds on both attach paths
   (crm.attach_owner and advance(..., "attach_owner")), is stage-agnostic
   (a done device still blocks), survives concurrent attaches with exactly
   one winner, and is freed by delete_device; update_device cannot set
   owner_id (whitelist audit pin).
3. The owner card number is optional (16 digits still required when present)
   and reports omit the card fragment for cardless owners.
4. Every mutation path stamps `updated`, and legacy device rows get `created`
   backfilled from `updated` on store read (in memory; persisted naturally
   on the next mutation's write).
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm    # noqa: E402
import panel  # noqa: E402

_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_persian_digits(text):
    """ASCII -> Persian digits (inverse of crm.normalize_digits)."""
    return str(text).translate(_TO_FA)


def synth_serial(n):
    """Unique synthetic 15-digit serial (pattern mandated by the task spec
    so serials never collide across test modules)."""
    return ("35693803564" + str(n).zfill(4))[:15]


def synth_national(n):
    """Checksum-valid synthetic national code: 9 chosen digits + mod-11
    check digit (same rule as crm.national_checksum_ok)."""
    base = str(n).zfill(9)
    total = sum(int(d) * (10 - i) for i, d in enumerate(base)) % 11
    check = total if total < 2 else 11 - total
    return base + str(check)


def owner_fields(n, card="6219861012345678"):
    """Synthetic passenger fields; card=None omits the key entirely."""
    fields = {
        "first_name": "مسافر",
        "last_name": "آزمون " + str(n),
        "national": synth_national(n),
        "birth": "1375/04/29",
        "mobile": "09121234567",
        "passport": "PP0000001",
    }
    if card is not None:
        fields["card_no"] = card
    return fields


class IntegrityStoreCase(unittest.TestCase):
    """Isolated CRM + estelam stores. delete_device/delete_customer sync
    through panel's estelam accessors, so panel.ESTELAM_PATH must point
    into the temp dir too (pattern: test_crm_api / test_v1_1_2_crm_updates).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        os.environ["IRICA_CRM_STORE"] = self.crm_store
        estelam_patcher = patch.object(panel, "ESTELAM_PATH",
                                       self.estelam_store)
        estelam_patcher.start()
        self.addCleanup(estelam_patcher.stop)
        crm.reset_cache()

    def tearDown(self):
        crm.reset_cache()
        os.environ.pop("IRICA_CRM_STORE", None)

    # -- fixtures ----------------------------------------------------------

    def add_device(self, n, **over):
        fields = {"name": "Device " + str(n), "serial1": synth_serial(n)}
        fields.update(over)
        record, errors = crm.add_device(fields)
        self.assertEqual(errors, [], errors)
        return record

    def add_owner(self, n, card="6219861012345678"):
        owner, errors = crm.add_owner(owner_fields(n, card))
        self.assertEqual(errors, [], errors)
        return owner

    def device_by_id(self, device_id):
        data = crm.store_read()
        return next(d for d in data["devices"] if d["id"] == device_id)

    def write_store(self, data):
        with open(self.crm_store, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)

    def read_raw_store(self):
        with open(self.crm_store, encoding="utf-8") as handle:
            return json.load(handle)

    def seed_green_inquiries(self, device_id):
        """Seed the estelam store with a both-OK row so the intake inquiry
        gate lets fixtures walk to ready_owner (v1.1.2 pattern)."""
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device_id), "status": "healthy",
                 "hamta_status": "good"}]}, handle)

    def device_at_ready_owner(self, n):
        record = self.add_device(n)
        self.seed_green_inquiries(record["id"])
        for action in ("queue_inquiry", "mark_ready"):
            _device, errors = crm.advance(record["id"], action)
            self.assertEqual(errors, [], (action, errors))
        return record


class SerialImmutabilityTests(IntegrityStoreCase):
    def test_update_with_unchanged_serial1_succeeds(self):
        record = self.add_device(1)
        device, errors = crm.update_device(
            record["id"], {"serial1": synth_serial(1)})
        self.assertEqual(errors, [])
        self.assertEqual(device["serial1"], synth_serial(1))
        # A Persian-digit variant of the SAME serial is still "unchanged".
        device, errors = crm.update_device(
            record["id"], {"serial1": to_persian_digits(synth_serial(1))})
        self.assertEqual(errors, [])
        self.assertEqual(device["serial1"], synth_serial(1))

    def test_update_with_changed_serial1_rejected(self):
        record = self.add_device(1)
        _device, errors = crm.update_device(
            record["id"], {"serial1": synth_serial(2)})
        self.assertEqual(len(errors), 1, errors)
        message = errors[0]
        # Delete-and-recreate guidance; keeps the «قبلاً» marker the legacy
        # duplicate-serial assertions (test_v1_1_2_crm_updates) rely on.
        self.assertIn("قبلاً", message)
        self.assertIn("حذف", message)
        self.assertIn("ثبت", message)
        # The stored record is untouched.
        self.assertEqual(self.device_by_id(record["id"])["serial1"],
                         synth_serial(1))

    def test_serial2_still_editable(self):
        record = self.add_device(1)
        device, errors = crm.update_device(
            record["id"], {"serial2": synth_serial(2)})
        self.assertEqual(errors, [])
        self.assertEqual(device["serial2"], synth_serial(2))
        self.assertEqual(self.device_by_id(record["id"])["serial2"],
                         synth_serial(2))

    def test_update_without_serial1_key_succeeds(self):
        record = self.add_device(1)
        device, errors = crm.update_device(
            record["id"], {"name": "Device renamed", "notes": "n"})
        self.assertEqual(errors, [])
        self.assertEqual(device["name"], "Device renamed")
        self.assertEqual(device["serial1"], synth_serial(1))


class OwnerOneToOneTests(IntegrityStoreCase):
    def test_attach_owner_blocked_when_bound_elsewhere(self):
        first = self.device_at_ready_owner(1)
        second = self.device_at_ready_owner(2)
        owner = self.add_owner(1)
        _device, errors = crm.attach_owner(first["id"], owner["id"])
        self.assertEqual(errors, [])
        # Direct path on the second device.
        _device, errors = crm.attach_owner(second["id"], owner["id"])
        self.assertTrue(any("قبلاً" in e for e in errors), errors)
        # Stage-machine path on the second device.
        _device, errors = crm.advance(second["id"], "attach_owner",
                                      owner_id=owner["id"])
        self.assertTrue(any("قبلاً" in e for e in errors), errors)
        self.assertEqual(self.device_by_id(second["id"]).get("owner_id"), "")

    def test_owner_bound_to_done_device_still_blocked(self):
        # Synthetic legacy store: the owner is already bound to a DONE device;
        # the 1:1 scan is stage-agnostic, so the binding still blocks.
        owner_id = "legacy01"
        self.write_store({
            "version": 1, "customers": [], "identities": [],
            "owners": [{"id": owner_id, "first_name": "مسافر",
                        "last_name": "آزمون", "national": synth_national(1),
                        "birth": "1375/04/29", "mobile": "09121234567",
                        "card_no": "", "created": "2026-01-01 00:00:00"}],
            "devices": [{"id": "devdone", "name": "Device done",
                         "serial1": synth_serial(1), "serial2": "",
                         "customer_id": "", "owner_id": owner_id,
                         "stage": "done", "created": "2026-01-01 00:00:00",
                         "updated": "2026-01-02 00:00:00"}],
            "settings": {},
        })
        second = self.device_at_ready_owner(2)
        _device, errors = crm.attach_owner(second["id"], owner_id)
        self.assertTrue(any("قبلاً" in e for e in errors), errors)
        _device, errors = crm.advance(second["id"], "attach_owner",
                                      owner_id=owner_id)
        self.assertTrue(any("قبلاً" in e for e in errors), errors)

    def test_concurrent_attach_exactly_one_wins(self):
        first = self.device_at_ready_owner(1)
        second = self.device_at_ready_owner(2)
        owner = self.add_owner(1)
        barrier = threading.Barrier(2)
        results = {}

        def attach(device_id, key):
            barrier.wait()
            _device, errors = crm.attach_owner(device_id, owner["id"])
            results[key] = errors

        threads = [threading.Thread(target=attach,
                                    args=(first["id"], "first")),
                   threading.Thread(target=attach,
                                    args=(second["id"], "second"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads),
                         "attach threads did not finish")
        winners = [key for key, errors in results.items() if errors == []]
        self.assertEqual(len(winners), 1, results)
        data = crm.store_read()
        bound = [d for d in data["devices"]
                 if d.get("owner_id") == owner["id"]]
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["id"],
                         first["id"] if winners[0] == "first"
                         else second["id"])

    def test_delete_device_frees_owner(self):
        first = self.add_device(1)
        second = self.add_device(2)
        owner = self.add_owner(1)
        _device, errors = crm.attach_owner(first["id"], owner["id"])
        self.assertEqual(errors, [])
        ok, errors = crm.delete_device(first["id"])
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        # The freed owner can now bind to the other device.
        _device, errors = crm.attach_owner(second["id"], owner["id"])
        self.assertEqual(errors, [])
        self.assertEqual(self.device_by_id(second["id"])["owner_id"],
                         owner["id"])

    def test_update_device_cannot_set_owner_id(self):
        # Audit pin (user item 5): update_device's whitelist never touches
        # owner_id — the only binding paths are the two attach paths, both
        # of which run the all-devices 1:1 scan under STORE_LOCK.
        record = self.add_device(1)
        owner = self.add_owner(1)
        _device, errors = crm.update_device(
            record["id"], {"owner_id": owner["id"], "name": "Renamed"})
        self.assertEqual(errors, [])
        reloaded = self.device_by_id(record["id"])
        self.assertNotEqual(reloaded.get("owner_id"), owner["id"])
        self.assertEqual(reloaded.get("owner_id"), "")
        self.assertEqual(reloaded["name"], "Renamed")


class OptionalCardTests(IntegrityStoreCase):
    def test_owner_without_card_valid(self):
        for variant in (None, "", "   "):
            fields = owner_fields(1, card=None)
            if variant is not None:
                fields["card_no"] = variant
            self.assertEqual(crm.validate_owner(fields), [], repr(variant))

    def test_owner_with_valid_card_valid(self):
        for card in ("6219861012345678",                    # plain 16 digits
                     "6219-8610-1234-5678",                 # dashed form
                     to_persian_digits("6219861012345678")):  # Persian digits
            self.assertEqual(crm.validate_owner(owner_fields(1, card=card)),
                             [], card)

    def test_short_card_still_rejected(self):
        fields = owner_fields(1, card="621986101234567")    # 15 digits
        errors = crm.validate_owner(fields)
        self.assertTrue(any("کارت" in e for e in errors), errors)

    def test_add_owner_without_card_ok(self):
        owner, errors = crm.add_owner(owner_fields(1, card=None))
        self.assertEqual(errors, [])
        stored = next(o for o in crm.store_read()["owners"]
                      if o["id"] == owner["id"])
        self.assertEqual(stored.get("card_no"), "")

    def _report_state(self, card_value):
        owner = {"id": "own1", "first_name": "مسافر", "last_name": "آزمون",
                 "national": synth_national(1), "birth": "1375/04/29",
                 "mobile": "09121234567"}
        if card_value != "ABSENT":
            owner["card_no"] = card_value
        return {"devices": [{"id": "dev1", "name": "Device 1",
                             "serial1": synth_serial(1), "serial2": "",
                             "customer_id": "", "owner_id": "own1",
                             "stage": "ready_owner"}],
                "customers": [], "owners": [owner]}

    def test_report_omits_card_for_cardless_owner(self):
        # Cardless (key absent, or stored as ""): full national shown, no
        # empty «کارت: » fragment.
        for card_value in ("ABSENT", ""):
            text = crm.build_report(self._report_state(card_value),
                                    full_data=True, plain=False)
            self.assertIn("کد ملی کامل", text)
            self.assertNotIn("کارت", text)
        # With a card the full-data report shows it.
        self.assertIn("کارت", crm.build_report(
            self._report_state("6219861012345678"), full_data=True,
            plain=False))
        # The telegram (plain=True) branch never shows the card.
        self.assertNotIn("کارت", crm.build_report(
            self._report_state("6219861012345678"), full_data=True,
            plain=True))


class TimestampIntegrityTests(IntegrityStoreCase):
    def test_update_owner_stamps_updated(self):
        owner = self.add_owner(1)
        _record, errors = crm.update_owner(
            owner["id"], {"first_name": "مسافر ویرایش‌شده"})
        self.assertEqual(errors, [])
        stored = next(o for o in crm.store_read()["owners"]
                      if o["id"] == owner["id"])
        self.assertTrue(stored.get("updated"))
        self.assertTrue(stored.get("created"))    # create stamp preserved

    def test_update_identity_stamps_updated(self):
        identity, errors = crm.add_identity({
            "label": "Pool entry", "national": synth_national(1),
            "birth": "1375/04/29", "mobile": "09121234567"})
        self.assertEqual(errors, [], errors)
        _record, errors = crm.update_identity(identity["id"],
                                              {"label": "Pool renamed"})
        self.assertEqual(errors, [])
        stored = next(i for i in crm.store_read()["identities"]
                      if i["id"] == identity["id"])
        self.assertTrue(stored.get("updated"))
        self.assertTrue(stored.get("created"))

    def test_delete_customer_stamps_cleared_device(self):
        customer, errors = crm.add_customer({"full_name": "مشتری آزمون"})
        self.assertEqual(errors, [])
        device = self.add_device(1, customer_id=customer["id"])
        # Force a distinguishable pre-delete stamp (no sleeps: _now() has
        # one-second resolution).
        data = crm.store_read()
        for record in data["devices"]:
            record["updated"] = "2000-01-01 00:00:00"
        crm.store_write(data)
        ok, errors = crm.delete_customer(customer["id"], force=True)
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        reloaded = self.device_by_id(device["id"])
        self.assertEqual(reloaded["customer_id"], "")
        self.assertTrue(reloaded.get("updated"))
        self.assertNotEqual(reloaded["updated"], "2000-01-01 00:00:00")

    def test_advance_success_stamps_updated(self):
        record = self.add_device(1)
        self.seed_green_inquiries(record["id"])
        owner = self.add_owner(1)
        steps = (("queue_inquiry", {}), ("mark_ready", {}),
                 ("attach_owner", {"owner_id": owner["id"]}),
                 ("handover", {}))
        for action, payload in steps:
            _device, errors = crm.advance(record["id"], action, **payload)
            self.assertEqual(errors, [], (action, errors))
            reloaded = self.device_by_id(record["id"])
            self.assertTrue(reloaded.get("updated"), action)

    def test_legacy_device_created_backfilled_from_updated(self):
        self.write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [{"id": "legacy01", "name": "Legacy",
                         "serial1": synth_serial(1), "stage": "done",
                         "updated": "2026-01-02 03:04:05"}],
            "settings": {},
        })
        data = crm.store_read()
        device = next(d for d in data["devices"] if d["id"] == "legacy01")
        self.assertEqual(device.get("created"), "2026-01-02 03:04:05")
        self.assertEqual(device.get("updated"), "2026-01-02 03:04:05")
        # The backfill is in-memory only: the file on disk is untouched.
        raw_device = next(d for d in self.read_raw_store()["devices"]
                          if d["id"] == "legacy01")
        self.assertNotIn("created", raw_device)

    def test_existing_created_never_overwritten(self):
        self.write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [{"id": "legacy02", "name": "Legacy",
                         "serial1": synth_serial(1), "stage": "new",
                         "created": "2020-01-01 00:00:00",
                         "updated": "2026-01-02 03:04:05"}],
            "settings": {},
        })
        device = next(d for d in crm.store_read()["devices"]
                      if d["id"] == "legacy02")
        self.assertEqual(device["created"], "2020-01-01 00:00:00")
        self.assertEqual(device["updated"], "2026-01-02 03:04:05")

    def test_device_missing_both_timestamps_does_not_crash(self):
        self.write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [{"id": "legacy03", "name": "Old", "stage": "new"}],
            "settings": {},
        })
        data = crm.store_read()              # must not raise
        device = next(d for d in data["devices"] if d["id"] == "legacy03")
        self.assertNotIn("created", device)  # nothing invented

    def test_backfill_persists_after_next_write(self):
        self.write_store({
            "version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [{"id": "legacy04", "name": "Legacy",
                         "serial1": synth_serial(1), "stage": "new",
                         "updated": "2026-01-02 03:04:05"}],
            "settings": {},
        })
        _data = crm.store_read()             # triggers the in-memory backfill
        _device, errors = crm.add_device({"name": "Device 2",
                                          "serial1": synth_serial(2)})
        self.assertEqual(errors, [])
        legacy = next(d for d in self.read_raw_store()["devices"]
                      if d["id"] == "legacy04")
        self.assertEqual(legacy.get("created"), "2026-01-02 03:04:05")
        self.assertEqual(legacy.get("updated"), "2026-01-02 03:04:05")


if __name__ == "__main__":
    unittest.main()
