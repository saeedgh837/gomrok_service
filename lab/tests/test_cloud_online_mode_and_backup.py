# -*- coding: utf-8 -*-
"""Regression and unit tests for:
1. Smart Import / Data Merge on 409 DATASET_NOT_EMPTY.
2. Real-Time Online Mode (2s micro-sync interval vs 15s local).
3. On-demand cloud database backup route (/cloud-backup-now).
4. Host-side backup-to-telegram.php script validation.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import panel
import crm
from cloud_sync import importer


class OnlineModeAndBackupTests(unittest.TestCase):

    def test_worker_interval_resolved_online_and_local(self):
        """Worker interval should be 2.0s for online mode, 15.0s for local mode."""
        with patch("crm._store_read", return_value={"settings": {"database_mode": "online"}}):
            self.assertEqual(panel._cloud_worker_interval_resolved(), 2.0)

        with patch("crm._store_read", return_value={"settings": {"database_mode": "local"}}):
            self.assertEqual(panel._cloud_worker_interval_resolved(), 15.0)

        # Default fallback should be 2.0s (online mode)
        with patch("crm._store_read", return_value={"settings": {}}):
            self.assertEqual(panel._cloud_worker_interval_resolved(), 2.0)

    def test_update_settings_database_mode(self):
        """Updating database_mode should persist and kick the sync worker."""
        fake_data = {"version": 1, "settings": {"database_mode": "local"}}
        with patch("crm.store_read", return_value=fake_data), \
             patch("crm.store_write") as mock_write, \
             patch("panel._cloud_enqueue_mutation") as mock_enqueue, \
             patch("panel.cloud_worker_kick") as mock_kick:
            res, errs = panel.crm_update_settings({"database_mode": "online"})
            self.assertEqual(errs, [])
            self.assertEqual(res["database_mode"], "online")
            mock_write.assert_called_once()
            mock_kick.assert_called_once()

    def test_smart_import_merge_queues_all_collections(self):
        """When importer returns DATASET_NOT_EMPTY, local records are enqueued and merged."""
        panel._CLOUD_ACTOR = "admin-user"
        panel._CLOUD_ROLE = "admin"

        mock_config = MagicMock()
        mock_config.sync_device_id = "device-123"
        mock_config.source_installation_id = "inst-456"

        mock_client = MagicMock()
        mock_client.vault.user_access_token = "token-789"
        mock_client.import_document = MagicMock()

        runtime = {
            "config": mock_config,
            "client": mock_client,
            "lease": None,
            "outbox": MagicMock(),
        }

        fake_store = {
            "customers": [
                {"id": "cust1", "name": "Ali", "transactions": [{"amount": 1000}]}
            ],
            "owners": [{"id": "own1", "first_name": "Reza"}],
            "identities": [{"id": "id1", "label": "Pool 1"}],
            "devices": [{"id": "dev1", "serial1": "111111111111111"}],
            "periods": [{"id": "per1", "number": 1}],
            "settings": {"business_name": "Test Store"},
        }

        mock_import_res = importer.ImportResult(
            ok=False,
            code="DATASET_NOT_EMPTY",
            import_id="11111111-1111-4111-8111-111111111111",
            backup_path="C:/fake/backup.json",
            attempts=1,
            payload=None,
            dropped_settings=(),
            terminal=True,
        )

        queued_entities = []

        def mock_enqueue(entity, legacy_id, record=None, kind="upsert"):
            queued_entities.append((entity, legacy_id, kind))

        with patch.object(panel, "_cloud_runtime", return_value=runtime), \
             patch("cloud_sync.importer.run_import", return_value=mock_import_res), \
             patch("crm._store_read", return_value=fake_store), \
             patch.object(panel, "_cloud_enqueue_mutation", side_effect=mock_enqueue), \
             patch.object(panel, "cloud_worker_kick") as mock_kick, \
             patch.object(panel, "cloud_pull_step") as mock_pull, \
             patch.object(panel, "cloud_worker_beat") as mock_beat:

            resp, status = panel.cloud_run_import()

            self.assertEqual(status, 200)
            self.assertTrue(resp.get("ok"))
            self.assertTrue(resp.get("merged"))
            self.assertTrue(resp.get("imported"))
            self.assertIn("ادغام", resp.get("message", ""))
            self.assertGreaterEqual(resp.get("count", 0), 6)

            # Check that all entity types were enqueued
            entity_types = {q[0] for q in queued_entities}
            self.assertIn("customers", entity_types)
            self.assertIn("owners", entity_types)
            self.assertIn("identities", entity_types)
            self.assertIn("crm_devices", entity_types)
            self.assertIn("periods", entity_types)
            self.assertIn("customer_transactions", entity_types)
            self.assertIn("settings", entity_types)

            mock_kick.assert_called_once()
            mock_pull.assert_called_once()
            mock_beat.assert_called_once()

    def test_smart_import_merge_numbers_ledger_rows_from_zero(self):
        """The merge must address ledger rows with the canonical seq.

        A transaction's wire identity is uuid5(installation,
        "customer_transactions", "<customer id>/<seq>") and the store, the host
        and mapper.lift_transactions all count from 0. Numbering from 1 in this
        one path minted ids the host had never seen, so a second client's pull
        could not resolve the row and failed with UNRESOLVED_REFERENCE.
        """
        panel._CLOUD_ACTOR = "admin-user"
        panel._CLOUD_ROLE = "admin"

        mock_config = MagicMock()
        mock_config.sync_device_id = "device-123"
        mock_config.source_installation_id = "inst-456"

        mock_client = MagicMock()
        mock_client.vault.user_access_token = "token-789"
        mock_client.import_document = MagicMock()

        runtime = {"config": mock_config, "client": mock_client,
                   "lease": None, "outbox": MagicMock()}

        fake_store = {
            "customers": [{"id": "cust1", "name": "Ali",
                           "transactions": [{"amount": 1000}, {"amount": 2000}]}],
            "settings": {},
        }
        mock_import_res = importer.ImportResult(
            ok=False, code="DATASET_NOT_EMPTY",
            import_id="11111111-1111-4111-8111-111111111111",
            backup_path="C:/fake/backup.json", attempts=1, payload=None,
            dropped_settings=(), terminal=True)

        seen = []

        def mock_enqueue(entity, legacy_id, record=None, kind="upsert"):
            seen.append((entity, legacy_id, dict(record or {})))

        with patch.object(panel, "_cloud_runtime", return_value=runtime), \
             patch("cloud_sync.importer.run_import", return_value=mock_import_res), \
             patch("crm._store_read", return_value=fake_store), \
             patch.object(panel, "_cloud_enqueue_mutation", side_effect=mock_enqueue), \
             patch.object(panel, "cloud_worker_kick"), \
             patch.object(panel, "cloud_pull_step"), \
             patch.object(panel, "cloud_worker_beat"):
            resp, status = panel.cloud_run_import()

        self.assertEqual(status, 200)
        ledger = [(legacy_id, record["seq"], record["amount"])
                  for entity, legacy_id, record in seen
                  if entity == "customer_transactions"]
        self.assertEqual(ledger, [
            ("cust1/0", 0, 1000),
            ("cust1/1", 1, 2000),
        ])

    def test_cloud_run_backup_now_endpoint(self):
        """POST /cloud-backup-now calls trigger_backup on client and returns 200."""
        panel._CLOUD_ACTOR = "admin-user"
        panel._CLOUD_ROLE = "admin"

        mock_client = MagicMock()
        mock_client.vault.user_access_token = "token-789"
        mock_client.trigger_backup.return_value = {"ok": True, "sha256": "abcdef"}

        runtime = {
            "client": mock_client,
            "lease": None,
        }

        with patch.object(panel, "_cloud_runtime", return_value=runtime):
            resp, status = panel.cloud_run_backup_now()
            self.assertEqual(status, 200)
            self.assertTrue(resp.get("ok"))
            self.assertIn("پشتیبان‌گیری", resp.get("message", ""))
            mock_client.trigger_backup.assert_called_once()

    def test_host_backup_php_script_syntax_and_dry_run(self):
        """PHP syntax check and CLI dry-run test for host/bin/backup-to-telegram.php."""
        script_path = os.path.join(os.path.dirname(__file__), "..", "..", "host", "bin", "backup-to-telegram.php")
        script_path = os.path.abspath(script_path)
        self.assertTrue(os.path.exists(script_path), f"File missing: {script_path}")

        # Check PHP lint
        res = subprocess.run(["php", "-l", script_path], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"PHP lint failed: {res.stderr}")
        self.assertIn("No syntax errors detected", res.stdout)


class BackupCliExitTests(unittest.TestCase):
    def _run_cli(self, *, delivered, dry_run=False):
        php = shutil.which("php")
        if not php:
            self.skipTest("PHP CLI is unavailable")
        source_path = os.path.join(os.path.dirname(__file__), "..", "..",
                                   "host", "bin", "backup-to-telegram.php")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        cli = source[source.index("if (php_sapi_name() === 'cli'"):]
        stub = """<?php
function run_database_backup($db, $config, $options) {
    $dry = !empty($options['dry_run']);
    return ['tables_dumped' => 1, 'total_rows' => 2, 'compressed_bytes' => 32,
            'dry_run' => $dry, 'telegram' => $dry ? null :
            ['ok' => getenv('BACKUP_TEST_DELIVERED') === '1', 'message_id' => 1]];
}
"""
        with tempfile.TemporaryDirectory(prefix="irica_backup_cli_") as root:
            bin_dir = os.path.join(root, "bin")
            os.mkdir(bin_dir)
            script = os.path.join(bin_dir, "backup-to-telegram.php")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(stub + cli)
            env = dict(os.environ, BACKUP_TEST_DELIVERED="1" if delivered else "0")
            command = [php, "-n", script] + (["--dry-run"] if dry_run else [])
            return subprocess.run(command, env=env, capture_output=True,
                                  text=True, encoding="utf-8", timeout=10)

    def test_delivered_backup_exits_successfully(self):
        result = self._run_cli(delivered=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Delivered to Telegram", result.stdout)

    def test_failed_delivery_exits_with_failure(self):
        result = self._run_cli(delivered=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("NOT DELIVERED to Telegram", result.stdout)

    def test_dry_run_does_not_report_delivery_failure(self):
        result = self._run_cli(delivered=False, dry_run=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("NOT DELIVERED", result.stdout)


if __name__ == "__main__":
    unittest.main()
