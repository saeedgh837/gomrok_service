"""Phase C — CRM identity materialization + per-device estelam start + queue
sync. The robots stay untouched; CRM materializes store['person'] before its
own serialized per-device run (plan section 2, review F1/F2/F3/F8).
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import crm
import panel


class CrmInquiryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estelam = os.path.join(self.tmp.name, "estelam_store.json")
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self._patches = [
            patch.object(panel, "ESTELAM_PATH", self.estelam),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    # -- fixtures --------------------------------------------------------
    def _seed_estelam(self, devices=(), person=None):
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": person or {}, "devices": list(devices)}, handle)

    def _crm_device(self, stage="new", owner=False):
        record, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": "356938035643809"})
        assert errs == [], errs
        if stage != "new":
            crm.advance(record["id"], "queue_inquiry")
            if stage in ("ready_owner", "handed", "to_payment"):
                crm.advance(record["id"], "mark_ready")
        if owner:
            owner_rec, errs = crm.add_owner({
                "first_name": "رضا", "last_name": "محمدی",
                "national": "0012345679", "birth": "1375/04/29",
                "mobile": "09121234567", "card_no": "6219861012345678"})
            assert errs == [], errs
            crm.advance(record["id"], "attach_owner", owner_id=owner_rec["id"])
            if stage == "handed":
                crm.advance(record["id"], "handover")
        return record

    def _pool(self, count=2):
        entries = []
        for i in range(count):
            rec, errs = crm.add_identity({
                "label": f"pool{i}",
                "national": ["0012345679", "0076431309"][i % 2],
                "birth": "1375/04/29", "mobile": "09121234567"})
            assert errs == [], errs
            entries.append(rec)
        return entries


class TestIdentityMaterialization(CrmInquiryCase):
    def test_intake_stage_picks_random_pool_identity(self):
        self._pool(2)
        device = self._crm_device(stage="new")
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running", return_value=False):
            spawn.return_value = {"pid": 123}
            result = panel.crm_start_inquiry(device["id"], service="gomrok")
        self.assertTrue(result["ok"], result)
        saved = json.load(open(self.estelam, encoding="utf-8"))
        # the materialized person came from the pool (one of the two labels)
        self.assertIn(saved["person"]["national"], ("0012345679", "0076431309"))

    def test_invalid_pool_entry_never_picked(self):
        # pool has one valid + one invalid entry; the invalid one must never
        # be materialized (F8 — panel-level filter)
        crm.add_identity({"label": "bad", "national": "123",
                          "birth": "1375/04/29", "mobile": "09121234567"})
        crm.add_identity({"label": "good", "national": "0012345679",
                          "birth": "1375/04/29", "mobile": "09121234567"})
        device = self._crm_device(stage="new")
        for _ in range(6):
            with patch.object(panel, "start_estelam_process") as spawn, \
                    patch.object(panel, "estelam_is_running", return_value=False):
                spawn.return_value = {"pid": 1}
                result = panel.crm_start_inquiry(device["id"], service="gomrok")
                saved = json.load(open(self.estelam, encoding="utf-8"))
                self.assertEqual(saved["person"]["national"], "0012345679")

    def test_handed_stage_uses_owner_identity(self):
        owner_rec = self._crm_device(stage="handed", owner=True)
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running", return_value=False):
            spawn.return_value = {"pid": 5}
            result = panel.crm_start_inquiry(owner_rec["id"], service="gomrok")
        self.assertTrue(result["ok"], result)
        saved = json.load(open(self.estelam, encoding="utf-8"))
        self.assertEqual(saved["person"]["national"], "0012345679")

    def test_handed_without_owner_is_rejected(self):
        """Defense-in-depth: a device AT a watch stage with no resolvable
        owner (e.g. owner record deleted) must not silently run with a random
        pool identity (F1)."""
        device = self._crm_device(stage="new")
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device["id"])
        dev["stage"] = "handed"      # simulate drifted state
        crm.store_write(data)
        result = panel.crm_start_inquiry(device["id"], service="gomrok")
        self.assertFalse(result["ok"])
        self.assertTrue(any("مالک" in e for e in result["errors"]))

    def test_empty_pool_and_no_owner_rejected_for_intake(self):
        device = self._crm_device(stage="new")
        result = panel.crm_start_inquiry(device["id"], service="gomrok")
        self.assertFalse(result["ok"])
        self.assertTrue(any("هویت" in e or "مشخصات" in e for e in result["errors"]))


class TestPerDeviceStart(CrmInquiryCase):
    def test_manual_estelam_rows_never_swept_into_crm_run(self):
        """F2: run is scoped by explicit device_id; a manual site-2 row stays
        pending and untouched."""
        manual_row = {"id": "manual-1", "name": "دستگاه دستی",
                      "serial1": "111111111111111", "status": "pending"}
        self._seed_estelam(devices=[manual_row])
        device = self._crm_device(stage="new")
        self._pool(1)
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running", return_value=False):
            spawn.return_value = {"pid": 9}
            result = panel.crm_start_inquiry(device["id"], service="gomrok")
        self.assertTrue(result["ok"])
        saved = json.load(open(self.estelam, encoding="utf-8"))
        ids = [d["id"] for d in saved["devices"]]
        self.assertIn("manual-1", ids)
        self.assertEqual(saved["run_device_id"], device["id"])

    def test_queue_sync_resets_crm_row_status_to_pending(self):
        """F2b: robot filter skips final-status rows; CRM resets its own row."""
        crm_row = {"id": "crm-1", "name": "x", "serial1": "222222222222222",
                   "status": "healthy", "result": "old"}
        self._seed_estelam(devices=[crm_row])
        device = self._crm_device(stage="new")
        # link by making the crm device id match the row id is NOT required:
        # sync upserts CRM device rows by their own id.
        self._pool(1)
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running", return_value=False):
            spawn.return_value = {"pid": 3}
            panel.crm_start_inquiry(device["id"], service="gomrok")
        saved = json.load(open(self.estelam, encoding="utf-8"))
        row = next(d for d in saved["devices"] if d["id"] == device["id"])
        self.assertEqual(row["status"], "pending")

    def test_non_crm_rows_byte_identical_after_sync(self):
        """F18 regression: sync must not touch rows it does not own."""
        manual_row = {"id": "manual-9", "name": "دستی", "serial1": "333333333333333",
                      "status": "healthy", "result": "نتیجهٔ قبلی",
                      "gomrok_at": "2026-01-01 10:00:00"}
        self._seed_estelam(devices=[json.loads(json.dumps(manual_row))])
        device = self._crm_device(stage="new")
        self._pool(1)
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running", return_value=False):
            spawn.return_value = {"pid": 2}
            panel.crm_start_inquiry(device["id"], service="gomrok")
        saved = json.load(open(self.estelam, encoding="utf-8"))
        row = next(d for d in saved["devices"] if d["id"] == "manual-9")
        for key, value in manual_row.items():
            self.assertEqual(row.get(key), value, key)

    def test_refuses_when_estelam_already_running(self):
        device = self._crm_device(stage="new")
        self._pool(1)
        with patch.object(panel, "estelam_is_running", return_value=True):
            result = panel.crm_start_inquiry(device["id"], service="gomrok")
        self.assertFalse(result["ok"])
        self.assertTrue(any("در حال انجام" in e for e in result["errors"]))

    def test_hamta_ignores_identity(self):
        device = self._crm_device(stage="new")
        with patch.object(panel, "start_estelam_process") as spawn, \
                patch.object(panel, "estelam_is_running", return_value=False):
            spawn.return_value = {"pid": 7}
            result = panel.crm_start_inquiry(device["id"], service="hamta")
        self.assertTrue(result["ok"], result)


class TestEstelamSaveAtomic(CrmInquiryCase):
    def test_estelam_save_is_atomic_with_bak(self):
        """F6: CRM sync relies on estelam_save; make it tmp+replace with .bak."""
        self._seed_estelam(person={"national": "1", "birth": "2", "mobile": "3"})
        before = panel.estelam_load()
        self.assertTrue(os.path.exists(self.estelam))
        # first save: bak seeded from current content
        data = panel.estelam_load()
        data["person"]["national"] = "999"
        panel.estelam_save(data)
        bak = json.load(open(self.estelam + ".bak", encoding="utf-8"))
        self.assertEqual(bak["person"]["national"], "1")
        main = json.load(open(self.estelam, encoding="utf-8"))
        self.assertEqual(main["person"]["national"], "999")

    def test_estelam_save_recovers_from_corrupt_main(self):
        """A corrupt main falls back to the last-known-good generation (.bak):
        content is at most one save stale, never empty (F6)."""
        self._seed_estelam(person={"national": "1", "birth": "2", "mobile": "3"})
        # a routine read seeds the .bak (panel loads constantly at runtime)
        panel.estelam_load()
        with open(self.estelam, "w", encoding="utf-8") as handle:
            handle.write("{corrupted")
        loaded = panel.estelam_load()
        self.assertEqual(loaded["person"]["birth"], "2")


if __name__ == "__main__":
    unittest.main()
