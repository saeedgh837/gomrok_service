# -*- coding: utf-8 -*-
"""Tests for default-on system debug logging, JSONL retention caps, and
the CRM stage-skip action (operator-initiated, confirmation-guarded)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import panel  # noqa: E402
import crm  # noqa: E402
import log_utils  # noqa: E402


class SystemDebugDefaultsTests(unittest.TestCase):
    def test_default_config_is_enabled_when_no_config_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = os.path.join(temp_dir, "never_created", "system_debug.json")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", missing):
                self.assertEqual(panel.load_system_debug_config(), {"enabled": True})

    def test_saved_disabled_config_is_respected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "system_debug.json")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", config_path):
                panel.save_system_debug_config({"enabled": False})
                self.assertEqual(panel.load_system_debug_config(), {"enabled": False})

    def test_robot_child_env_defaults_to_debug_on(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = os.path.join(temp_dir, "never_created", "system_debug.json")
            with patch.object(panel, "SYSTEM_DEBUG_CONFIG_PATH", missing):
                self.assertEqual(panel._robot_child_env()["IRICA_SYSTEM_DEBUG"], "1")


class JsonlRetentionTests(unittest.TestCase):
    def test_append_jsonl_keeps_only_last_cap_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_path = os.path.join(temp_dir, "logs.jsonl")
            for i in range(60):
                log_utils.append_jsonl(logs_path, {"i": i}, cap=40)
            with open(logs_path, encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(lines), 40)
        self.assertEqual(lines[0]["i"], 20)
        self.assertEqual(lines[-1]["i"], 59)

    def test_append_jsonl_ignores_non_dict_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_path = os.path.join(temp_dir, "logs.jsonl")
            log_utils.append_jsonl(logs_path, None)
            log_utils.append_jsonl(logs_path, {"ok": 1})
            with open(logs_path, encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("ok", lines[0])

    def test_append_jsonl_survives_unwritable_target(self):
        log_utils.append_jsonl(os.path.join(os.devnull, "x", "y.jsonl"), {"a": 1})

    def test_panel_and_crm_jsonl_writers_route_through_log_utils(self):
        panel_src = open(panel.__file__, encoding="utf-8").read()
        crm_src = open(crm.__file__, encoding="utf-8").read()
        self.assertIn("append_jsonl", panel_src)
        self.assertIn("append_jsonl", crm_src)


class _CrmStoreBase(unittest.TestCase):
    def _device(self, stage):
        store = {"devices": [{"id": "dev01", "stage": stage, "owner_id": ""}],
                 "owners": []}
        return store


class StageSkipTests(_CrmStoreBase):
    def _advance(self, store, action):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = os.path.join(temp_dir, "crm.json")
            with open(store_path, "w", encoding="utf-8") as handle:
                json.dump(store, handle, ensure_ascii=False)
            with patch.dict(os.environ, {"IRICA_CRM_STORE": store_path}), \
                    patch.object(crm, "log_path",
                                 return_value=os.path.join(temp_dir, "l.jsonl")):
                return crm.advance("dev01", action)

    def test_skip_moves_inquiry_to_ready_owner(self):
        device, errors = self._advance(self._device("inquiry"), "stage_skip")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "ready_owner")

    def test_skip_bypasses_inquiry_gate_and_owner_gate(self):
        # Device with NO inquiry results and NO owner: normal mark_ready and
        # handover would fail; stage_skip must still proceed (operator choice).
        device, errors = self._advance(self._device("inquiry"), "stage_skip")
        self.assertEqual(errors, [])
        device, errors = self._advance(
            self._device("ready_owner"), "stage_skip")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "handed")

    def test_skip_records_provenance(self):
        device, errors = self._advance(self._device("inquiry"), "stage_skip")
        self.assertEqual(errors, [])
        skipped = device.get("skipped_stages") or []
        self.assertEqual([s["stage"] for s in skipped], ["inquiry"])
        self.assertTrue(skipped[0].get("at"))

    def test_skip_from_new_goes_to_inquiry(self):
        device, errors = self._advance(self._device("new"), "stage_skip")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "inquiry")

    def test_skip_blocked_at_done(self):
        _, errors = self._advance(self._device("done"), "stage_skip")
        self.assertTrue(errors)

    def test_skip_from_payment_done_completes_case(self):
        device, errors = self._advance(self._device("payment_done"), "stage_skip")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "done")


class StageSkipUiTests(unittest.TestCase):
    def test_panel_offers_skip_with_confirmation(self):
        src = open(panel.__file__, encoding="utf-8").read()
        self.assertIn("'__skip'", src)
        self.assertIn("stage_skip", src)
        self.assertIn("رد کردن مرحله", src)
        self.assertIn("confirmDlg", src)


if __name__ == "__main__":
    unittest.main()
