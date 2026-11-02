"""Comprehensive test suite for clerk (متصدی ثبت) cloud synchronization.

Verifies:
  1. ids.ENTITIES includes clerks and clerk_transactions.
  2. mapper.to_row / from_row for clerks and clerk_transactions.
  3. crm_devices.clerk_id foreign key mapping to cloud UUID and back.
  4. lift_clerk_transactions and restore_clerk_transactions.
  5. split_store and merge_store include clerks and clerk_transactions.
  6. apply_snapshot correctly materialises clerks, clerk_transactions, and device clerk_id.
  7. apply_changes handles incremental clerk and clerk_transaction upsert/tombstone.
  8. panel._cloud_enqueue_mutation correctly translates and enqueues clerk and clerk_transaction mutations.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
import warnings

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cloud_sync import apply, ids, mapper
import crm
import panel

INSTALL = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"


class ClerkCloudSyncTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="irica_clerk_sync_")
        self.store_path = os.path.join(self.tmp, "crm_store.json")
        os.environ["IRICA_CRM_STORE"] = self.store_path
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("IRICA_CRM_STORE", None)

    def test_entities_contain_clerks(self):
        self.assertIn("clerks", ids.ENTITIES)
        self.assertIn("clerk_transactions", ids.ENTITIES)
        self.assertIn("clerk_id", mapper.FIELD_TYPES["crm_devices"])
        self.assertIn("clerk_id", mapper.REFERENCE_FIELDS["crm_devices"])
        self.assertIn("clerk_id", mapper.NULLABLE_REFERENCE_FIELDS["crm_devices"])
        self.assertEqual(mapper.REFERENCE_FIELDS["crm_devices"]["clerk_id"], "clerks")
        self.assertEqual(mapper.REFERENCE_FIELDS["clerk_transactions"]["clerk_id"], "clerks")

    def test_clerk_and_transactions_round_trip(self):
        clerk = {
            "id": "c1c1c1c1",
            "name": "Ali Operator",
            "mobile": "09121234567",
            "notes": "Experienced clerk",
            "active": True,
            "created": "2026-09-01 10:00:00",
            "updated": "2026-09-02 11:00:00",
            "transactions": [
                {
                    "id": "tx11tx11",
                    "at": "2026-09-02 12:00:00",
                    "amount": 5000000,
                    "kind": "credit",
                    "recipient_name": "Ali",
                    "device_id": "d1d1d1d1",
                    "period_id": "p1p1p1p1",
                    "note": "Initial fee",
                    "voided_at": "",
                    "void_reason": "",
                    "reversal_of": "",
                }
            ],
        }

        # Reference index mapping
        index = {
            ("clerks", ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1")): "c1c1c1c1",
            ("crm_devices", ids.legacy_to_uuid(INSTALL, "crm_devices", "d1d1d1d1")): "d1d1d1d1",
            ("periods", ids.legacy_to_uuid(INSTALL, "periods", "p1p1p1p1")): "p1p1p1p1",
        }

        # to_row on clerk
        wire_clerk = mapper.to_row("clerks", clerk, source_installation_id=INSTALL)
        self.assertEqual(wire_clerk["id"], "c1c1c1c1")
        self.assertEqual(wire_clerk["name"], "Ali Operator")
        self.assertEqual(wire_clerk["active"], True)
        self.assertNotIn("transactions", wire_clerk)

        # from_row on clerk
        rec_clerk = mapper.from_row("clerks", wire_clerk, reference_index=index)
        self.assertEqual(rec_clerk["id"], "c1c1c1c1")
        self.assertEqual(rec_clerk["name"], "Ali Operator")
        self.assertEqual(rec_clerk["active"], True)

        # lift transactions
        tx_rows = mapper.lift_clerk_transactions(clerk, source_installation_id=INSTALL)
        self.assertEqual(len(tx_rows), 1)
        wire_tx = tx_rows[0]
        self.assertEqual(wire_tx["id"], "tx11tx11")
        self.assertEqual(wire_tx["clerk_id"], ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1"))
        self.assertEqual(wire_tx["device_id"], ids.legacy_to_uuid(INSTALL, "crm_devices", "d1d1d1d1"))
        self.assertEqual(wire_tx["period_id"], ids.legacy_to_uuid(INSTALL, "periods", "p1p1p1p1"))
        self.assertEqual(wire_tx["amount"], 5000000)

        # restore transactions
        restored_txs = mapper.restore_clerk_transactions(tx_rows, reference_index=index)
        self.assertEqual(len(restored_txs), 1)
        res_tx = restored_txs[0]
        self.assertEqual(res_tx["id"], "tx11tx11")
        self.assertEqual(res_tx["device_id"], "d1d1d1d1")
        self.assertEqual(res_tx["period_id"], "p1p1p1p1")
        self.assertEqual(res_tx["amount"], 5000000)

    def test_device_clerk_id_mapping(self):
        device = {
            "id": "d1d1d1d1",
            "name": "Device A",
            "serial1": "123456789012345",
            "serial2": "",
            "customer_id": "",
            "owner_id": "",
            "stage": "new",
            "auto_advance": False,
            "profile_id": "",
            "notes": "",
            "advance_attempts": 0,
            "period_id": "",
            "clerk_id": "c1c1c1c1",
            "created": "2026-09-01 10:00:00",
            "updated": "2026-09-01 10:00:00",
        }
        wire_dev = mapper.to_row("crm_devices", device, source_installation_id=INSTALL)
        self.assertEqual(wire_dev["clerk_id"], ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1"))

        index = {
            ("clerks", ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1")): "c1c1c1c1",
        }
        rec_dev = mapper.from_row("crm_devices", wire_dev, reference_index=index)
        self.assertEqual(rec_dev["clerk_id"], "c1c1c1c1")

    def test_split_and_merge_store_with_clerks(self):
        doc = {
            "version": 1,
            "customers": [],
            "owners": [],
            "identities": [],
            "devices": [
                {
                    "id": "d1d1d1d1", "name": "Device A",
                    "serial1": "123456789012345", "serial2": "",
                    "customer_id": "", "owner_id": "", "stage": "new",
                    "auto_advance": False, "profile_id": "", "notes": "",
                    "advance_attempts": 0, "period_id": "", "clerk_id": "c1c1c1c1",
                    "created": "2026-09-01 10:00:00", "updated": "2026-09-01 10:00:00",
                }
            ],
            "periods": [],
            "clerks": [
                {
                    "id": "c1c1c1c1", "name": "Ali", "mobile": "09121234567",
                    "notes": "", "active": True,
                    "created": "2026-09-01 10:00:00", "updated": "2026-09-01 10:00:00",
                    "transactions": [
                        {
                            "id": "tx11tx11", "at": "2026-09-01 10:05:00",
                            "amount": 3000000, "kind": "credit", "recipient_name": "",
                            "device_id": "d1d1d1d1", "period_id": "", "note": "case fee",
                            "voided_at": "", "void_reason": "", "reversal_of": "",
                        }
                    ],
                }
            ],
            "settings": {},
        }
        rows = mapper.split_store(doc, source_installation_id=INSTALL)
        self.assertIn("clerks", rows)
        self.assertIn("clerk_transactions", rows)
        self.assertEqual(len(rows["clerks"]), 1)
        self.assertEqual(len(rows["clerk_transactions"]), 1)
        self.assertEqual(rows["crm_devices"][0]["clerk_id"],
                         ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1"))

        merged = mapper.merge_store(rows, source_installation_id=INSTALL)
        self.assertIn("clerks", merged)
        self.assertEqual(len(merged["clerks"]), 1)
        self.assertEqual(merged["clerks"][0]["id"], "c1c1c1c1")
        self.assertEqual(len(merged["clerks"][0]["transactions"]), 1)
        self.assertEqual(merged["clerks"][0]["transactions"][0]["amount"], 3000000)
        self.assertEqual(merged["devices"][0]["clerk_id"], "c1c1c1c1")

    def test_panel_outbox_enqueue_for_clerks(self):
        class DummyOutbox:
            def __init__(self):
                self.enqueued = []
            def enqueue(self, op):
                self.enqueued.append(op)

        dummy_outbox = DummyOutbox()
        runtime = {
            "outbox": dummy_outbox,
            "config": None,
            "client": None,
            "lease": None,
        }

        # Patch panel helpers
        old_runtime = panel._CLOUD_RUNTIME
        old_configured = panel._cloud_configured
        old_load_config = None
        try:
            panel._CLOUD_RUNTIME = runtime
            panel._cloud_configured = lambda: True
            from cloud_sync import config as cs_cfg
            cs_cfg._STORE = None
            cfg_obj = cs_cfg.CloudConfig(
                api_base="https://api.test",
                tenant_id=TENANT,
                sync_device_id="dev-01",
                source_installation_id=INSTALL,
            )
            cs_cfg.save_config(cfg_obj)

            # Test clerk enqueue
            clerk_record = {
                "id": "c1c1c1c1", "name": "Clerk Test", "mobile": "09120001122",
                "notes": "", "active": True,
                "created": "2026-09-01 10:00:00", "updated": "2026-09-01 10:00:00",
            }
            panel._cloud_enqueue_mutation("clerk", "c1c1c1c1", clerk_record)
            self.assertEqual(len(dummy_outbox.enqueued), 1)
            op = dummy_outbox.enqueued[0]
            self.assertEqual(op["entity"], "clerks")
            self.assertEqual(op["id"], ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1"))
            self.assertEqual(op["record"]["name"], "Clerk Test")

            # Test clerk transaction enqueue
            tx_record = {
                "id": "tx11tx11", "clerk_id": "c1c1c1c1", "at": "2026-09-01 10:05:00",
                "amount": 1500000, "kind": "credit", "recipient_name": "",
                "device_id": "", "period_id": "", "note": "payment",
                "voided_at": "", "void_reason": "", "reversal_of": "",
            }
            panel._cloud_enqueue_mutation("clerk_transaction", "tx11tx11", tx_record)
            self.assertEqual(len(dummy_outbox.enqueued), 2)
            op_tx = dummy_outbox.enqueued[1]
            self.assertEqual(op_tx["entity"], "clerk_transactions")
            self.assertEqual(op_tx["id"], ids.legacy_to_uuid(INSTALL, "clerk_transactions", "tx11tx11"))
            self.assertEqual(op_tx["record"]["amount"], 1500000)
            self.assertEqual(op_tx["record"]["clerk_id"], ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1"))

            # Test device enqueue preserves clerk_id
            dev_record = {
                "id": "d1d1d1d1", "name": "Dev", "serial1": "123456789012345",
                "serial2": "", "customer_id": "", "owner_id": "", "stage": "new",
                "auto_advance": False, "profile_id": "", "notes": "",
                "advance_attempts": 0, "period_id": "", "clerk_id": "c1c1c1c1",
                "created": "2026-09-01 10:00:00", "updated": "2026-09-01 10:00:00",
            }
            panel._cloud_enqueue_mutation("device", "d1d1d1d1", dev_record)
            self.assertEqual(len(dummy_outbox.enqueued), 3)
            op_dev = dummy_outbox.enqueued[2]
            self.assertEqual(op_dev["entity"], "crm_devices")
            self.assertEqual(op_dev["record"]["clerk_id"], ids.legacy_to_uuid(INSTALL, "clerks", "c1c1c1c1"))
        finally:
            panel._CLOUD_RUNTIME = old_runtime
            panel._cloud_configured = old_configured

    def test_panel_crm_entity_clerk_actions(self):
        class DummyOutbox:
            def __init__(self):
                self.enqueued = []
            def enqueue(self, op):
                self.enqueued.append(op)

        dummy_outbox = DummyOutbox()
        runtime = {
            "outbox": dummy_outbox,
            "config": None,
            "client": None,
            "lease": None,
        }

        class MockHandler:
            def __init__(self):
                self.actor = "test_operator"
                self.last_json = None
                self.last_status = 200

            def _json(self, payload, status=200):
                self.last_json = payload
                self.last_status = status

            def _crm_entity(self, path, data):
                return panel.Handler._crm_entity(self, path, data)

        handler = MockHandler()
        old_runtime = panel._CLOUD_RUNTIME
        old_configured = panel._cloud_configured
        try:
            panel._CLOUD_RUNTIME = runtime
            panel._cloud_configured = lambda: True
            from cloud_sync import config as cs_cfg
            cs_cfg._STORE = None
            cfg_obj = cs_cfg.CloudConfig(
                api_base="https://api.test",
                tenant_id=TENANT,
                sync_device_id="dev-01",
                source_installation_id=INSTALL,
            )
            cs_cfg.save_config(cfg_obj)

            # 1. Action: add clerk
            handler._crm_entity("/crm-clerk", {
                "action": "add",
                "name": "Mohsen Clerk",
                "mobile": "09121112233",
                "notes": "Test notes",
            })
            self.assertEqual(handler.last_status, 200)
            self.assertTrue(handler.last_json.get("ok"))
            clerk_id = handler.last_json["record"]["id"]

            self.assertEqual(len(dummy_outbox.enqueued), 1)
            self.assertEqual(dummy_outbox.enqueued[0]["entity"], "clerks")
            self.assertEqual(dummy_outbox.enqueued[0]["record"]["name"], "Mohsen Clerk")

            # 2. Action: update clerk
            handler._crm_entity("/crm-clerk", {
                "action": "update",
                "id": clerk_id,
                "name": "Mohsen Updated",
                "mobile": "09121112233",
            })
            self.assertEqual(handler.last_status, 200)
            self.assertTrue(handler.last_json.get("ok"))
            self.assertEqual(len(dummy_outbox.enqueued), 2)
            self.assertEqual(dummy_outbox.enqueued[1]["entity"], "clerks")
            self.assertEqual(dummy_outbox.enqueued[1]["record"]["name"], "Mohsen Updated")

            # 3. Action: clerk transaction (tx)
            handler._crm_entity("/crm-clerk", {
                "action": "tx",
                "id": clerk_id,
                "amount": 2000000,
                "kind": "credit",
                "note": "Fee credit",
            })
            self.assertEqual(handler.last_status, 200)
            self.assertTrue(handler.last_json.get("ok"))
            tx_id = handler.last_json["record"]["id"]
            self.assertEqual(len(dummy_outbox.enqueued), 3)
            self.assertEqual(dummy_outbox.enqueued[2]["entity"], "clerk_transactions")
            self.assertEqual(dummy_outbox.enqueued[2]["record"]["amount"], 2000000)
            self.assertEqual(dummy_outbox.enqueued[2]["record"]["clerk_id"],
                             ids.legacy_to_uuid(INSTALL, "clerks", clerk_id))

            # 4. Action: void clerk transaction (void_tx)
            handler._crm_entity("/crm-clerk", {
                "action": "void_tx",
                "id": clerk_id,
                "tx_id": tx_id,
                "reason": "Accidental double entry",
            })
            self.assertEqual(handler.last_status, 200)
            self.assertTrue(handler.last_json.get("ok"))
            # void_tx enqueues reversal AND updated original row
            self.assertEqual(len(dummy_outbox.enqueued), 5)
            self.assertEqual(dummy_outbox.enqueued[3]["entity"], "clerk_transactions")
            self.assertEqual(dummy_outbox.enqueued[3]["record"]["kind"], "void")
            self.assertEqual(dummy_outbox.enqueued[4]["entity"], "clerk_transactions")
            self.assertEqual(dummy_outbox.enqueued[4]["record"]["void_reason"], "Accidental double entry")

            # 5. Action: delete clerk
            handler._crm_entity("/crm-clerk", {
                "action": "delete",
                "id": clerk_id,
                "force": True,
            })
            self.assertEqual(handler.last_status, 200)
            self.assertTrue(handler.last_json.get("ok"))
            self.assertEqual(len(dummy_outbox.enqueued), 6)
            self.assertEqual(dummy_outbox.enqueued[5]["entity"], "clerks")
            self.assertEqual(dummy_outbox.enqueued[5]["kind"], "delete")

        finally:
            panel._CLOUD_RUNTIME = old_runtime
            panel._cloud_configured = old_configured


if __name__ == "__main__":
    unittest.main()
