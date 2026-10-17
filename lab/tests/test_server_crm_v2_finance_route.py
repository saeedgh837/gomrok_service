# -*- coding: utf-8 -*-
"""The panel's /crm-write route must actually reach the finance transport.

`test_server_crm_v2_finance_outbox` pins the outbox side of the v1.15.0
server-authoritative ledger: an operation marked ``transport="finance_append"``
or ``"finance_void"`` is routed to the dedicated host serialization point
instead of generic row CAS. What was never pinned is the *producer* — the
panel route that is supposed to stamp that marker.

The v1.15.0 route read the enqueued record out of the customer:

    record, errors = crm_mod.add_transaction(...)
    txs = record.get("transactions", [])      # the ledger ENTRY, not a customer
    if txs:                                   # always false
        ... transport="finance_append"

``crm.add_transaction`` / ``crm.void_transaction`` return the ledger entry
they just wrote, so both branches were dead and no desktop ledger write ever
reached ``Finance.php``. These tests drive the real HTTP route (not the
marker constructor) and assert what landed in the outbox.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_paths  # noqa: E402
import crm  # noqa: E402
import panel  # noqa: E402

FINANCE_APPEND = "finance_append"
FINANCE_VOID = "finance_void"


class _StubOutbox(object):
    """Captures the operations the route enqueues, without any network."""

    def __init__(self):
        self.enqueued = []

    def enqueue(self, operation):
        self.enqueued.append(operation)
        return "op-%d" % len(self.enqueued)


class FinanceTransportRouteTests(unittest.TestCase):
    """One real POST /crm-customer, one assertion on the outbox."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="irica_finance_route_")
        self.addCleanup(self.tmp.cleanup)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        os.environ["IRICA_CRM_STORE"] = self.crm_store
        self.addCleanup(os.environ.pop, "IRICA_CRM_STORE", None)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

        # app_paths resolves the data dir once and caches it; the cloud config
        # has to live somewhere disposable before it is first asked for.
        self._data = patch.object(app_paths, "data_dir",
                                  return_value=self.tmp.name)
        self._data.start()
        self.addCleanup(self._data.stop)

        self.outbox = _StubOutbox()
        self.runtime = {
            "config": _SourceConfig(),
            "outbox": self.outbox,
            "client": None,
            "worker": None,
            "machine": None,
            "lease": None,
        }
        self._runtime = patch.object(panel, "_cloud_runtime",
                                     return_value=self.runtime)
        self._runtime.start()
        self.addCleanup(self._runtime.stop)
        self._configured = patch.object(panel, "_cloud_configured",
                                        return_value=True)
        self._configured.start()
        self.addCleanup(self._configured.stop)
        self._kick = patch.object(panel, "cloud_worker_kick", return_value=None)
        self._kick.start()
        self.addCleanup(self._kick.stop)

        # The CRM routes sit behind the v1.2.0 login gate; the shared harness
        # installs a real session cookie plus the CSRF header into urlopen.
        from lab.tests import _auth_harness
        _auth_harness.install_auth_cookie(self, _auth_harness.seed_test_users())

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

        customer, errors = crm.add_customer(
            {"full_name": "Sample Ledger Customer"})
        self.assertEqual(errors, [], errors)
        self.customer = customer

    def _post(self, path, payload):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _finances(self, marker):
        return [op for op in self.outbox.enqueued
                if op.get("transport") == marker]

    def _customer_uuid(self):
        """The wire identity of the customer the row belongs to.

        Resolved through the same ``load_config()`` the enqueue path reads,
        so the assertion is about the mapping and not about which config
        object the test happened to hand the runtime.
        """
        from cloud_sync import ids
        from cloud_sync.config import load_config
        installation = load_config().source_installation_id
        return ids.legacy_to_uuid(installation, "customers",
                                  self.customer["id"])

    def _void_target_uuid(self, seq):
        """The wire identity of the ledger row a void must name."""
        from cloud_sync import ids
        from cloud_sync.config import load_config
        installation = load_config().source_installation_id
        return ids.legacy_to_uuid(
            installation, "customer_transactions",
            "%s/%d" % (self.customer["id"], seq))

    def test_a_new_receipt_reaches_the_finance_append_transport(self):
        status, body = self._post("/crm-customer", {
            "action": "tx", "id": self.customer["id"],
            "amount": "250000", "kind": "receipt", "note": "cash"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)

        ops = self._finances(FINANCE_APPEND)
        self.assertEqual(len(ops), 1, self.outbox.enqueued)
        operation = ops[0]
        self.assertEqual(operation["entity"], "customer_transactions")
        # The customer reference must be the CUSTOMER, never the ledger entry.
        self.assertEqual(operation["record"]["customer_id"],
                         self._customer_uuid())
        self.assertEqual(operation["record"]["amount"], 250000)
        # seq is the index of the row just appended inside the customer list.
        self.assertEqual(operation["record"]["seq"], 0)

    def test_a_second_receipt_uses_the_second_sequence_number(self):
        for expected in (0, 1):
            status, body = self._post("/crm-customer", {
                "action": "tx", "id": self.customer["id"],
                "amount": "1000", "kind": "receipt", "note": "n"})
            self.assertEqual(status, 200, body)
            self.assertEqual(
                self._finances(FINANCE_APPEND)[expected]["record"]["seq"],
                expected)

    def test_a_void_reaches_the_finance_void_transport_with_its_reason(self):
        status, body = self._post("/crm-customer", {
            "action": "tx", "id": self.customer["id"],
            "amount": "250000", "kind": "receipt", "note": "cash"})
        self.assertEqual(status, 200, body)

        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": self.customer["id"], "tx_index": "0",
            "reason": "operator correction"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)

        ops = self._finances(FINANCE_VOID)
        self.assertEqual(len(ops), 1, self.outbox.enqueued)
        operation = ops[0]
        self.assertEqual(operation["entity"], "customer_transactions")
        # The operation addresses the row the HOST must void — the original at
        # seq 0 — and not the local reversal the panel just appended, which the
        # server has never seen. The host appends the reversal itself.
        self.assertEqual(operation["record"]["customer_id"],
                         self._customer_uuid())
        self.assertEqual(operation["record"]["seq"], 0)
        self.assertEqual(operation["id"],
                         self._void_target_uuid(0))
        # outbox._push_finance_void reads void_reason and refuses the entry
        # without it, so the producer owes the server that field.
        self.assertEqual(operation["record"]["void_reason"],
                         "operator correction")

    def test_a_rejected_void_enqueues_nothing(self):
        status, body = self._post("/crm-customer", {
            "action": "void_tx", "id": self.customer["id"], "tx_index": 7,
            "reason": "no such row"})
        self.assertEqual(status, 400, body)
        self.assertEqual(self._finances(FINANCE_VOID), [])

    def test_an_unconfigured_client_enqueues_nothing(self):
        with patch.object(panel, "_cloud_configured", return_value=False):
            status, body = self._post("/crm-customer", {
                "action": "tx", "id": self.customer["id"],
                "amount": "1000", "kind": "receipt", "note": "n"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.outbox.enqueued, [])


class _SourceConfig(object):
    """The two config attributes the enqueue path reads."""

    source_installation_id = "11111111-1111-4111-8111-111111111111"


class _RecordingClient(object):
    """Stands in for SyncClient: records the calls the worker makes."""

    def __init__(self):
        self.cursor = 100
        self.appended = []
        self.voided = []

    def append_ledger_transaction(self, customer_id, amount, *, note="",
                                  kind="", image="", device_id=None,
                                  operation_id=None):
        self.appended.append({
            "customer_id": customer_id, "amount": amount, "note": note,
            "kind": kind, "image": image, "device_id": device_id,
            "operation_id": operation_id,
        })
        self.cursor += 1
        return {
            "transaction": {
                "id": "22222222-2222-4222-8222-222222222222",
                "record": {"seq": 0},
            },
            "cursor": self.cursor,
        }

    def void_ledger_transaction(self, transaction_id, reason, *,
                                operation_id=None):
        self.voided.append({
            "transaction_id": transaction_id, "reason": reason,
            "operation_id": operation_id,
        })
        self.cursor += 1
        return {
            "original": {"id": transaction_id, "row_version": 2},
            "reversal": {"id": "33333333-3333-4333-8333-333333333333",
                         "row_version": 1},
            "cursor": self.cursor,
        }


class FinanceTransportWorkerTests(unittest.TestCase):
    """What the panel queued must survive the outbox worker's own checks.

    The route tests above prove the marker and the fields are produced; this
    proves the entry the producer builds is one ``_push_finance_append`` and
    ``_push_finance_void`` accept, because those two functions re-validate
    every field and reject the whole operation outright when one is missing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="irica_finance_worker_")
        self.addCleanup(self.tmp.cleanup)
        from cloud_sync.outbox import Outbox, OutboxWorker

        self.outbox = Outbox(path=os.path.join(self.tmp.name, "outbox.jsonl"))
        self.client = _RecordingClient()
        self.worker = OutboxWorker(self.outbox, self.client)
        self.customer = "a1b2c3d4"

    def test_a_queued_append_pushes_through_the_finance_transport(self):
        self.outbox.enqueue({
            "kind": "upsert", "entity": "customer_transactions",
            "id": "44444444-4444-4444-8444-444444444444",
            "base_version": 0,
            "transport": FINANCE_APPEND,
            "record": {"customer_id": self.customer, "seq": 0, "amount": 250000,
                       "note": "cash", "kind": "receipt", "image": "",
                       "device_id": None},
        })
        outcome = self.worker.run_once()
        self.assertEqual(outcome.applied, 1, outcome)
        self.assertEqual(self.outbox.pending_count, 0)
        self.assertEqual(len(self.client.appended), 1)
        self.assertEqual(self.client.appended[0]["amount"], 250000)

    def test_a_queued_void_pushes_through_the_finance_transport(self):
        operation_id = self.outbox.enqueue({
            "kind": "upsert", "entity": "customer_transactions",
            "id": "55555555-5555-4555-8555-555555555555",
            "base_version": 1,
            "transport": FINANCE_VOID,
            "record": {"customer_id": self.customer, "seq": 0,
                       "void_reason": "operator correction"},
        })
        outcome = self.worker.run_once()
        self.assertEqual(outcome.applied, 1, outcome)
        self.assertEqual(self.outbox.pending_count, 0)
        self.assertEqual(self.client.voided, [{
            "transaction_id": "55555555-5555-4555-8555-555555555555",
            "reason": "operator correction",
            "operation_id": operation_id,
        }])


if __name__ == "__main__":
    unittest.main()
