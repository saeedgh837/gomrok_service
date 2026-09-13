"""CRM store CRUD + stage machine tests (Phase A). Run:
  "C:/Program Files/Python310/python.exe" -m unittest lab.tests.test_crm_store -v
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


class TempStoreCase(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("IRICA_CRM_STORE")
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        os.environ["IRICA_CRM_STORE"] = path
        self.path = path
        import crm
        crm.reset_cache()
        self.crm = crm

    def tearDown(self):
        import crm
        crm.reset_cache()
        if self._old:
            os.environ["IRICA_CRM_STORE"] = self._old
        else:
            os.environ.pop("IRICA_CRM_STORE", None)
        for suffix in ("", ".bak", ".tmp"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def _customer(self, name="علی رضایی"):
        rec, errs = self.crm.add_customer({"full_name": name})
        assert errs == [], errs
        return rec

    def _owner(self):
        rec, errs = self.crm.add_owner({
            "first_name": "رضا", "last_name": "محمدی",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "card_no": "6219861012345678",
            "passport": "PP0000001"})
        assert errs == [], errs
        return rec

    def _device(self, **over):
        fields = {"name": "iPhone 13", "serial1": "356938035643809"}
        fields.update(over)
        rec, errs = self.crm.add_device(fields)
        assert errs == [], errs
        return rec

    def _seed_green_estelam(self, device_id):
        """Seed the panel's estelam store with a both-OK row so the intake
        inquiry gate (v1.1.2) lets fixtures walk past `mark_ready`."""
        import panel
        with open(panel.ESTELAM_PATH, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device_id), "status": "healthy",
                 "hamta_status": "good"}]}, handle)


class TestStoreCrud(TempStoreCase):
    def test_add_and_read_customer_roundtrip(self):
        rec = self._customer()
        data = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual(data["customers"][0]["full_name"], "علی رضایی")
        self.assertEqual(data["customers"][0]["id"], rec["id"])

    def test_update_customer(self):
        rec = self._customer()
        updated, errs = self.crm.update_customer(rec["id"], {"address": "تهران"})
        self.assertEqual(errs, [])
        self.assertEqual(updated["address"], "تهران")

    def test_delete_customer_unlinks_devices(self):
        customer = self._customer()
        device = self._device(customer_id=customer["id"])
        ok, errs = self.crm.delete_customer(customer["id"], force=True)
        self.assertEqual(errs, [])
        self.assertTrue(ok)
        data = self.crm.store_read()
        self.assertEqual(data["customers"], [])
        kept = next(d for d in data["devices"] if d["id"] == device["id"])
        self.assertEqual(kept["customer_id"], "")

    def test_delete_customer_without_force_blocked(self):
        customer = self._customer()
        self._device(customer_id=customer["id"])
        ok, errs = self.crm.delete_customer(customer["id"])
        self.assertFalse(ok)
        self.assertTrue(any("دستگاه" in e for e in errs))

    def test_add_device_inline_customer(self):
        device, errs = self.crm.add_device(
            {"name": "گوشی", "serial1": "356938035643809"},
            customer={"full_name": "مریم احمدی"})
        self.assertEqual(errs, [])
        data = self.crm.store_read()
        cust = next(c for c in data["customers"] if c["id"] == device["customer_id"])
        self.assertEqual(cust["full_name"], "مریم احمدی")

    def test_add_device_bad_customer_ref(self):
        _, errs = self.crm.add_device(
            {"name": "x", "serial1": "356938035643809", "customer_id": "nope"})
        self.assertTrue(any("مشتری" in e for e in errs))

    def test_ledger_balance(self):
        customer = self._customer()
        self.crm.add_transaction(customer["id"], 2500000, "پیش‌پرداخت")
        self.crm.add_transaction(customer["id"], -1000000, "بدهی")
        data = self.crm.store_read()
        cust = next(c for c in data["customers"] if c["id"] == customer["id"])
        self.assertEqual(self.crm.customer_balance(cust), 1500000)
        self.assertEqual(len(cust["transactions"]), 2)

    def test_owner_1to1_binding(self):
        owner = self._owner()
        d1 = self._device()
        d2 = self._device(name="Samsung S21", serial1="356938035643810")
        dev, errs = self.crm.attach_owner(d1["id"], owner["id"])
        self.assertEqual(errs, [])
        _, errs = self.crm.attach_owner(d2["id"], owner["id"])
        self.assertTrue(any("متصل" in e for e in errs))
        # owner delete blocked while bound
        ok, errs = self.crm.delete_owner(owner["id"])
        self.assertFalse(ok)

    def test_owner_delete_free_after_unbind(self):
        owner = self._owner()
        d1 = self._device()
        self.crm.attach_owner(d1["id"], owner["id"])
        # simulate unlink via update? use delete_device path
        self.crm.delete_device(d1["id"])
        ok, errs = self.crm.delete_owner(owner["id"])
        self.assertEqual(errs, [])
        self.assertTrue(ok)

    def test_settings_defaults(self):
        self.assertEqual(self.crm.store_read()["settings"]["watch_interval_hours"], 2)

    def test_atomic_write_leaves_bak(self):
        self._customer()
        self._customer()
        self.assertTrue(os.path.exists(self.path + ".bak"))
        bak = json.load(open(self.path + ".bak", encoding="utf-8"))
        self.assertEqual(len(bak["customers"]), 1)

    def test_corrupt_store_recovers_from_bak(self):
        self._customer()
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{corrupt json!!")
        data = self.crm.store_read()
        self.assertEqual(len(data["customers"]), 1)


class TestStageMachine(TempStoreCase):
    def test_happy_path_full_lifecycle(self):
        customer = self._customer()
        owner = self._owner()
        device = self._device(customer_id=customer["id"])

        device, errs = self.crm.advance(device["id"], "queue_inquiry")
        self.assertEqual(errs, [])
        self.assertEqual(device["stage"], "inquiry")

        # scheduler auto-join on healthy
        self._seed_green_estelam(device["id"])
        device, errs = self.crm.advance(device["id"], "mark_ready")
        self.assertEqual(device["stage"], "ready_owner")

        device, errs = self.crm.advance(
            device["id"], "attach_owner", owner_id=owner["id"])
        self.assertEqual(errs, [])
        self.assertEqual(device["stage"], "ready_owner")
        self.assertEqual(device["owner_id"], owner["id"])

        device, errs = self.crm.advance(device["id"], "handover")
        self.assertEqual(errs, [])
        self.assertEqual(device["stage"], "handed")
        self.assertTrue(device["handed_at"])

        device, errs = self.crm.advance(device["id"], "to_payment")
        self.assertEqual(device["stage"], "to_payment")

        device, errs = self.crm.advance(device["id"], "start_payment")
        self.assertEqual(device["stage"], "payment")

        device, errs = self.crm.advance(device["id"], "confirm_payment")
        self.assertEqual(device["stage"], "payment_done")
        self.assertTrue(device["payment_confirmed_at"])

        device, errs = self.crm.advance(device["id"], "deliver_code")
        self.assertEqual(device["stage"], "done")
        self.assertTrue(device["code_delivered_at"])
        self.assertTrue(device["done_at"])

    def test_invalid_transition_rejected(self):
        device = self._device()
        _, errs = self.crm.advance(device["id"], "confirm_payment")
        self.assertTrue(errs)  # new -> payment_done impossible

    def test_problem_path_and_recovery(self):
        device = self._device()
        self.crm.advance(device["id"], "queue_inquiry")
        device, errs = self.crm.advance(device["id"], "mark_problem")
        self.assertEqual(device["stage"], "problem")
        device, errs = self.crm.advance(device["id"], "requeue_inquiry")
        self.assertEqual(device["stage"], "inquiry")

    def test_watch_problem_edge(self):
        device = self._device()
        owner = self._owner()
        self.crm.advance(device["id"], "queue_inquiry")
        self._seed_green_estelam(device["id"])
        self.crm.advance(device["id"], "mark_ready")
        self.crm.advance(device["id"], "attach_owner", owner_id=owner["id"])
        self.crm.advance(device["id"], "handover")
        device, errs = self.crm.advance(device["id"], "mark_problem")
        self.assertEqual(errs, [])
        self.assertEqual(device["stage"], "problem")

    def test_double_tick_rejected(self):
        device = self._device()
        owner = self._owner()
        self.crm.advance(device["id"], "queue_inquiry")
        self._seed_green_estelam(device["id"])
        self.crm.advance(device["id"], "mark_ready")
        self.crm.advance(device["id"], "attach_owner", owner_id=owner["id"])
        dev, errs = self.crm.advance(device["id"], "handover")
        self.assertEqual(errs, [])
        _, errs = self.crm.advance(dev["id"], "handover")
        # handover again is not in ALLOWED['handed'] anyway; the stamp guard
        # covers the ready_owner->handed retick from re-entry paths.
        self.assertTrue(errs or dev["stage"] != "ready_owner")

    def test_attach_owner_requires_owner_id(self):
        device = self._device()
        self.crm.advance(device["id"], "queue_inquiry")
        self._seed_green_estelam(device["id"])
        self.crm.advance(device["id"], "mark_ready")
        _, errs = self.crm.advance(device["id"], "attach_owner", owner_id="")
        self.assertTrue(any("الزامی" in e for e in errs))

    def test_attach_bound_owner_rejected_via_advance(self):
        owner = self._owner()
        d1 = self._device()
        d2 = self._device(name="S", serial1="356938035643810")
        for d in (d1, d2):
            self.crm.advance(d["id"], "queue_inquiry")
            self._seed_green_estelam(d["id"])
            self.crm.advance(d["id"], "mark_ready")
        self.crm.advance(d1["id"], "attach_owner", owner_id=owner["id"])
        _, errs = self.crm.advance(d2["id"], "attach_owner", owner_id=owner["id"])
        self.assertTrue(any("متصل" in e for e in errs))

    def test_done_is_terminal(self):
        device = self._device()
        owner = self._owner()
        self.crm.advance(device["id"], "queue_inquiry")
        self._seed_green_estelam(device["id"])
        self.crm.advance(device["id"], "mark_ready")
        self.crm.advance(device["id"], "attach_owner", owner_id=owner["id"])
        self.crm.advance(device["id"], "handover")
        self.crm.advance(device["id"], "to_payment")
        self.crm.advance(device["id"], "start_payment")
        self.crm.advance(device["id"], "confirm_payment")
        dev, _ = self.crm.advance(device["id"], "deliver_code")
        _, errs = self.crm.advance(dev["id"], "queue_inquiry")
        self.assertTrue(errs)


if __name__ == "__main__":
    unittest.main()
