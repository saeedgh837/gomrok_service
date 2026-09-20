"""Ledger integrity around device deletion (Gate-H finding 2) plus
post-review record fixes:

1. Deleting a device reverses its booked registry fee: a `base_fee_refund`
   row with the same device_id marker lands on the customer ledger holding
   the original `base_fee` row, so the serial-typo delete-and-recreate flow
   forced by serial1 immutability stays fee-neutral. No booked fee means no
   refund; a second delete cannot double-refund; customer reassignment
   after booking still nets out on the original holder.
2. Stage rollback from `done` and re-completion via deliver_code never
   touch the customer ledger.
3. Owner records survive rows lacking card_no (the old normalization loop
   raised KeyError -> HTTP 500) and store space-separated card numbers
   normalized, matching what validate_owner accepts.
4. The serial1 immutability error carries the fee-reversal clause, and
   advance()'s skip branch carries no dead done-stamping.
"""
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm    # noqa: E402
import panel  # noqa: E402

FEE = 24_000_000


def synth_serial(n):
    """Unique synthetic 15-digit serial (pattern shared with the other CRM
    test modules; every test here runs against its own isolated store)."""
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


class LedgerStoreCase(unittest.TestCase):
    """Isolated CRM + estelam stores. delete_device reads the estelam row
    for the mid-robot check and syncs through panel's estelam accessors, so
    panel.ESTELAM_PATH must point into the temp dir too (pattern:
    test_crm_case_integrity.IntegrityStoreCase)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        os.environ["IRICA_CRM_STORE"] = self.crm_store
        self.addCleanup(os.environ.pop, "IRICA_CRM_STORE", None)
        estelam_patcher = patch.object(panel, "ESTELAM_PATH",
                                       self.estelam_store)
        estelam_patcher.start()
        self.addCleanup(estelam_patcher.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    # -- fixtures ----------------------------------------------------------

    def add_customer(self, n):
        customer, errors = crm.add_customer(
            {"full_name": "مشتری آزمون " + str(n),
             "mobile": "09120000000"})
        self.assertEqual(errors, [], errors)
        return customer

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

    # -- store accessors ---------------------------------------------------

    def customer_by_id(self, customer_id):
        data = crm.store_read()
        return next(c for c in data["customers"] if c["id"] == customer_id)

    def balance(self, customer_id):
        return crm.customer_balance(self.customer_by_id(customer_id))

    def txs(self, customer_id, kind=None, device_id=None):
        """The customer's transaction rows, optionally filtered."""
        rows = list(self.customer_by_id(customer_id)
                    .get("transactions", []))
        if kind is not None:
            rows = [t for t in rows if t.get("kind") == kind]
        if device_id is not None:
            rows = [t for t in rows if t.get("device_id") == device_id]
        return rows

    def all_transactions(self):
        """Every (customer_id, row) pair in the store."""
        data = crm.store_read()
        return [(c["id"], t) for c in data.get("customers", [])
                for t in c.get("transactions", [])]


class FeeReversalTests(LedgerStoreCase):
    def test_typo_correction_is_fee_neutral(self):
        typo_serial = "356938035640001"
        fixed_serial = "356938035640002"
        customer = self.add_customer(1)
        device = self.add_device(1, serial1=typo_serial,
                                 customer_id=customer["id"])
        # Booked: exactly one -24M base_fee row for the device; balance B.
        fees = self.txs(customer["id"], kind="base_fee")
        self.assertEqual(len(fees), 1)
        self.assertEqual(int(fees[0]["amount"]), -FEE)
        self.assertEqual(fees[0]["device_id"], device["id"])
        balance = self.balance(customer["id"])
        self.assertEqual(balance, -FEE)
        # Correcting the typo in place is rejected with the fee-reversal
        # guidance (serial1 immutability forces delete-and-recreate).
        _device, errors = crm.update_device(
            device["id"], {"serial1": fixed_serial})
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("برمی‌گردد", errors[0])
        # The forced delete refunds the booked fee on the same ledger.
        ok, errors = crm.delete_device(device["id"])
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        refunds = self.txs(customer["id"], kind="base_fee_refund")
        self.assertEqual(len(refunds), 1)
        self.assertEqual(int(refunds[0]["amount"]), FEE)
        self.assertEqual(refunds[0]["device_id"], device["id"])
        self.assertEqual(refunds[0]["note"],
                         "بازگشت " + crm.REGISTRY_FEE_NOTE + "، Device 1")
        self.assertEqual(self.balance(customer["id"]), balance + FEE)
        # Re-add with the corrected serial: a fresh fee, balance back to B.
        replacement = self.add_device(2, serial1=fixed_serial,
                                      customer_id=customer["id"])
        self.assertNotEqual(replacement["id"], device["id"])
        self.assertEqual(self.balance(customer["id"]), balance)
        # Final ledger: the old device id keeps one fee + one refund; the
        # new device id carries exactly one fresh fee.
        self.assertEqual(
            len(self.txs(customer["id"], kind="base_fee",
                         device_id=device["id"])), 1)
        self.assertEqual(
            len(self.txs(customer["id"], kind="base_fee_refund",
                         device_id=device["id"])), 1)
        self.assertEqual(
            len(self.txs(customer["id"], kind="base_fee",
                         device_id=replacement["id"])), 1)
        self.assertEqual(
            len(self.customer_by_id(customer["id"])
                .get("transactions", [])), 3)

    def test_delete_without_customer_books_no_refund(self):
        unrelated = self.add_customer(1)
        device = self.add_device(1)
        ok, errors = crm.delete_device(device["id"])
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        self.assertEqual(self.txs(unrelated["id"]), [])
        self.assertEqual(self.all_transactions(), [])

    def test_delete_with_fee_disabled_books_no_refund(self):
        data = crm.store_read()
        data["settings"]["registry_fee_amount"] = 0
        crm.store_write(data)
        customer = self.add_customer(1)
        device = self.add_device(1, customer_id=customer["id"])
        self.assertEqual(self.txs(customer["id"], kind="base_fee"), [])
        ok, errors = crm.delete_device(device["id"])
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        self.assertEqual(self.txs(customer["id"], kind="base_fee_refund"),
                         [])
        self.assertEqual(self.all_transactions(), [])

    def test_refund_targets_the_customer_holding_the_fee_row(self):
        first = self.add_customer(1)
        second = self.add_customer(2)
        device = self.add_device(1, customer_id=first["id"])
        # Reassign the device to another customer AFTER the fee was booked.
        _device, errors = crm.update_device(
            device["id"], {"customer_id": second["id"]})
        self.assertEqual(errors, [])
        ok, errors = crm.delete_device(device["id"])
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        # The refund lands on the ledger holding the original row: first
        # nets out, second's ledger is untouched.
        self.assertEqual(len(self.txs(first["id"], kind="base_fee_refund")),
                         1)
        self.assertEqual(self.balance(first["id"]), 0)
        self.assertEqual(self.txs(second["id"]), [])

    def test_double_delete_cannot_double_refund(self):
        customer = self.add_customer(1)
        device = self.add_device(1, customer_id=customer["id"])
        ok, errors = crm.delete_device(device["id"])
        self.assertEqual(errors, [])
        self.assertTrue(ok)
        self.assertEqual(len(self.txs(customer["id"],
                                      kind="base_fee_refund")), 1)
        ok, errors = crm.delete_device(device["id"])
        self.assertFalse(ok)
        self.assertEqual(errors, ["دستگاه پیدا نشد"])
        self.assertEqual(len(self.txs(customer["id"],
                                      kind="base_fee_refund")), 1)


class LedgerUntouchedTests(LedgerStoreCase):
    def _walk_to_done(self):
        customer = self.add_customer(1)
        record = self.add_device(1, customer_id=customer["id"])
        device_id = record["id"]
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": device_id, "status": "healthy",
                 "hamta_status": "good"}]}, handle)
        owner = self.add_owner(1)
        steps = (("queue_inquiry", {}), ("mark_ready", {}),
                 ("attach_owner", {"owner_id": owner["id"]}),
                 ("handover", {}), ("to_payment", {}),
                 ("start_payment", {}), ("confirm_payment", {}),
                 ("deliver_code", {}))
        for action, payload in steps:
            _device, errors = crm.advance(device_id, action, **payload)
            self.assertEqual(errors, [], (action, errors))
        return customer["id"], device_id

    def test_done_rollback_and_recompletion_leave_ledger_untouched(self):
        customer_id, device_id = self._walk_to_done()
        data = crm.store_read()
        device = next(d for d in data["devices"] if d["id"] == device_id)
        self.assertEqual(device["stage"], "done")
        balance = self.balance(customer_id)
        count = len(self.customer_by_id(customer_id)["transactions"])
        # Roll back the completion: the ledger must not move.
        device, errors = crm.advance(device_id, "stage_back")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "payment_done")
        self.assertEqual(self.balance(customer_id), balance)
        self.assertEqual(
            len(self.customer_by_id(customer_id)["transactions"]), count)
        # Re-complete via deliver_code: still untouched.
        device, errors = crm.advance(device_id, "deliver_code")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "done")
        self.assertEqual(self.balance(customer_id), balance)
        self.assertEqual(
            len(self.customer_by_id(customer_id)["transactions"]), count)


class OwnerRecordFixesTests(LedgerStoreCase):
    def test_update_owner_survives_row_without_card_no(self):
        # Hand-edited store row lacking card_no: the old normalization loop
        # raised KeyError (surfacing as HTTP 500 through the panel route).
        owner_id = "ownhand"
        with open(self.crm_store, "w", encoding="utf-8") as handle:
            json.dump({
                "version": 1, "customers": [], "identities": [],
                "owners": [{"id": owner_id, "first_name": "مسافر",
                            "last_name": "آزمون",
                            "national": synth_national(1),
                            "birth": "1375/04/29", "mobile": "09121234567",
                            "passport": "PP0000001",
                            "created": "2026-01-01 00:00:00"}],
                "devices": [], "settings": {},
            }, handle, ensure_ascii=False)
        record, errors = crm.update_owner(
            owner_id, {"first_name": "مسافر ویرایش‌شده"})
        self.assertEqual(errors, [], errors)   # no KeyError, no 500
        self.assertEqual(record.get("card_no"), "")
        stored = next(o for o in crm.store_read()["owners"]
                      if o["id"] == owner_id)
        self.assertEqual(stored.get("card_no"), "")

    def test_card_internal_spaces_normalized_on_store(self):
        # validate_owner strips internal spaces for the 16-digit check, so a
        # spaced card validates — storage must not keep the spaces behind a
        # validation that would have rejected them.
        spaced = "6219 8610 1234 5678"
        self.assertEqual(crm.validate_owner(owner_fields(1, card=spaced)),
                         [], "spaced card validates")
        owner, errors = crm.add_owner(owner_fields(1, card=spaced))
        self.assertEqual(errors, [], errors)
        stored = next(o for o in crm.store_read()["owners"]
                      if o["id"] == owner["id"])
        self.assertEqual(stored["card_no"], "6219861012345678")
        # update_owner path normalizes spaces too.
        record, errors = crm.update_owner(
            owner["id"], {"card_no": "6219 8610 1234 5679"})
        self.assertEqual(errors, [], errors)
        self.assertEqual(record["card_no"], "6219861012345679")
        stored = next(o for o in crm.store_read()["owners"]
                      if o["id"] == owner["id"])
        self.assertEqual(stored["card_no"], "6219861012345679")
        # The stored (normalized) record still passes validation.
        self.assertEqual(crm.validate_owner(stored), [])

    def test_immutable_serial_error_mentions_fee_reversal(self):
        record = self.add_device(1)
        _device, errors = crm.update_device(
            record["id"], {"serial1": synth_serial(2)})
        self.assertEqual(len(errors), 1, errors)
        message = errors[0]
        # Legacy delete-and-recreate markers plus the new fee-reversal clause.
        for fragment in ("قبلاً", "حذف", "ثبت", "برمی‌گردد"):
            self.assertIn(fragment, message)


class DeadCodeRemovalTests(unittest.TestCase):
    def test_skip_branch_has_no_done_stamp(self):
        # No _STAGE_SKIP_NEXT value maps to `done` (deliver_code is the only
        # completion action), so the skip branch must not stamp done_at.
        source = inspect.getsource(crm.advance)
        self.assertNotIn('next_stage == "done"', source)


if __name__ == "__main__":
    unittest.main()
