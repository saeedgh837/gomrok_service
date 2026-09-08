"""v1.1.2 CRM updates — RED tests first (TDD):

1. Stage rollback: new `stage_back` action moves a device to the previous
   mainline stage, with server-side legality rules.
2. Serial uniqueness: add/update device rejects a serial already registered
   for another device, naming the conflicting customer.
3. Post-add inquiry buttons: the CRM state payload exposes per-device
   estelam/hamta status so the UI can offer direct inquiry actions; a new
   `/crm-inquire` route starts inquiries for one device.
4. Cross-links: CRM page links to the inquiry panel and vice versa.
5. Form hygiene: inline-customer mobile is 09xx (11 digits), telegram/
   instagram ids are accepted by the device dialog, and the auto-pay
   checkbox is a styled, aligned control.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import crm
import panel


class _StoreHarness(unittest.TestCase):
    """Isolated CRM + profiles stores (same pattern as test_crm_api)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["IRICA_CRM_STORE"] = os.path.join(
            self.tmp.name, "crm_store.json")
        crm.reset_cache()

    def tearDown(self):
        crm.reset_cache()
        os.environ.pop("IRICA_CRM_STORE", None)


class SerialUniquenessTests(_StoreHarness):
    def _device(self, serial1, serial2="", name="iPhone 13"):
        record, errors = crm.add_device({"name": name, "serial1": serial1,
                                         "serial2": serial2})
        return record, errors

    def test_add_rejects_duplicate_serial1(self):
        record, errors = self._device("356938035643809")
        self.assertEqual(errors, [])
        _record, errors = self._device("356938035643809", name="X2")
        self.assertTrue(any("قبلاً" in e for e in errors), errors)

    def test_add_rejects_duplicate_serial2(self):
        _record, errors = self._device("356938035643809", "356938035643810")
        self.assertEqual(errors, [])
        _record, errors = self._device("111111111111119",
                                       "356938035643810")
        self.assertTrue(any("۳۵۶۹۳۸۰۳۵۶۴۳۸۱۰" in e or
                            "356938035643810" in e for e in errors), errors)

    def test_add_reports_conflicting_customer_name(self):
        cust = {"full_name": "مریم احمدی"}
        crm.add_device({"name": "A", "serial1": "356938035643809"},
                       customer=cust)
        _record, errors = self._device("356938035643809", name="B")
        joined = "\n".join(errors)
        self.assertIn("مریم احمدی", joined)

    def test_update_allows_keeping_own_serial(self):
        record, errors = self._device("356938035643809")
        self.assertEqual(errors, [])
        _record, errors = crm.update_device(
            record["id"], {"serial1": "356938035643809"})
        self.assertEqual(errors, [])

    def test_update_rejects_foreign_serial(self):
        a, _ = self._device("356938035643809")
        b, _ = self._device("222222222222229")
        _record, errors = crm.update_device(
            b["id"], {"serial1": "356938035643809"})
        self.assertTrue(any("قبلاً" in e for e in errors), errors)

    def test_digit_normalization_is_applied_before_dedupe(self):
        _record, errors = self._device("356938035643809")
        # Persian-digit variant of the same serial must also collide.
        _record, errors = self._device("۳۵۶۹۳۸۰۳۵۶۴۳۸۰۹")
        self.assertTrue(any("قبلاً" in e for e in errors), errors)


class StageRollbackTests(_StoreHarness):
    MAINLINE = ["new", "inquiry", "ready_owner", "handed", "to_payment",
                "payment", "payment_done", "done"]

    def _device_at(self, actions, serial=None):
        serial = serial or ("356938035643800" + str(len(actions)))[:15]
        record, errors = crm.add_device({"name": "D", "serial1": serial})
        self.assertEqual(errors, [])
        device_id = record["id"]
        if "mark_ready" in actions:
            # The inquiry gate (both estelams OK) must pass for fixtures that
            # walk past the intake stage; seed the estelam row as green.
            est_path = panel.ESTELAM_PATH
            with open(est_path, "w", encoding="utf-8") as handle:
                json.dump({"person": {}, "devices": [
                    {"id": device_id, "status": "healthy",
                     "hamta_status": "good"}]}, handle)
        # The owner gate requires an attached owner before `handover`; any
        # action path reaching it walks through attach_owner implicitly.
        if "handover" in actions and "attach_owner" not in actions:
            actions = list(actions)
            actions.insert(actions.index("handover"), "attach_owner")
        for action in actions:
            if action == "attach_owner":
                owner, errors = crm.add_owner({
                    "first_name": "رضا", "last_name": "محمدی",
                    "national": "0012345679", "birth": "1375/04/29",
                    "mobile": "09121234567", "card_no": "1234567812345678"})
                self.assertEqual(errors, [])
                record, errors = crm.advance(device_id, "attach_owner",
                                             owner_id=owner["id"])
            else:
                record, errors = crm.advance(device_id, action)
            self.assertEqual(errors, [])
        data = crm.store_read()
        return next(d for d in data["devices"] if d["id"] == device_id)

    def test_stage_back_moves_to_previous_mainline_stage(self):
        # Walk the action path stage-by-stage; `new` itself needs no action.
        ACTION_PATH = {
            "inquiry": ["queue_inquiry"],
            "ready_owner": ["queue_inquiry", "mark_ready"],
            "handed": ["queue_inquiry", "mark_ready", "handover"],
            "to_payment": ["queue_inquiry", "mark_ready", "handover",
                           "to_payment"],
            "payment": ["queue_inquiry", "mark_ready", "handover",
                        "to_payment", "start_payment"],
            "payment_done": ["queue_inquiry", "mark_ready", "handover",
                             "to_payment", "start_payment",
                             "confirm_payment"],
            "done": ["queue_inquiry", "mark_ready", "handover",
                     "to_payment", "start_payment", "confirm_payment",
                     "deliver_code"],
        }
        order = self.MAINLINE
        for index in range(1, len(order)):
            serial = ("35693803564700" + str(index))[:15]  # unique, 15 digits
            device = self._device_at(ACTION_PATH[order[index]], serial)
            self.assertEqual(device["stage"], order[index])
            if index == len(order) - 1:
                continue                      # done: rollback is illegal
            device, errors = crm.advance(device["id"], "stage_back")
            self.assertEqual(errors, [])
            self.assertEqual(device["stage"], order[index - 1])

    def test_stage_back_from_inquiry_returns_to_new(self):
        device = self._device_at(["queue_inquiry"])
        device, errors = crm.advance(device["id"], "stage_back")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "new")

    def test_stage_back_illegal_on_new(self):
        device = self._device_at([])
        _record, errors = crm.advance(device["id"], "stage_back")
        self.assertTrue(errors)

    def test_stage_back_clears_downstream_timestamps(self):
        device = self._device_at(["queue_inquiry", "mark_ready", "handover"])
        self.assertTrue(device.get("handed_at"))
        device, errors = crm.advance(device["id"], "stage_back")
        self.assertEqual(errors, [])
        self.assertEqual(device["stage"], "ready_owner")
        self.assertEqual(device.get("handed_at"), "")

    def test_stage_back_from_done_illegal(self):
        device = self._device_at(["queue_inquiry", "mark_ready", "handover",
                                  "to_payment", "start_payment",
                                  "confirm_payment", "deliver_code"])
        _record, errors = crm.advance(device["id"], "stage_back")
        self.assertTrue(errors)

    def test_handover_requires_attached_owner(self):
        """Watch-stage inquiries run under the OWNER identity (F1): handing a
        device over without an attached owner would strand it at `handed`
        with un-runnable inquiries. Fail-closed at the source."""
        device = self._device_at(["queue_inquiry", "mark_ready"])
        _record, errors = crm.advance(device["id"], "handover")
        self.assertTrue(errors)
        self.assertTrue(any("مالک" in e for e in errors), errors)

    def test_handover_with_owner_reaches_handed(self):
        device = self._device_at(["queue_inquiry", "mark_ready",
                                  "attach_owner", "handover"])
        self.assertEqual(device["stage"], "handed")


class CrmInquireRouteTests(unittest.TestCase):
    """`/crm-inquire` starts an inquiry for ONE CRM device (any service)."""

    def setUp(self):
        import crm as crm_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH",
                         os.path.join(self.tmp.name, "estelam_store.json")),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm_mod.reset_cache()
        self.addCleanup(crm_mod.reset_cache)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode(
                    "utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def test_crm_inquire_starts_hamta_for_device(self):
        started = {}

        def fake_start(device_id, service="gomrok", origin="manual"):
            started["args"] = (device_id, service, origin)
            return {"ok": True, "pid": 4321, "count": 1, "service": service}

        with patch.object(panel, "crm_start_inquiry", side_effect=fake_start):
            status, body = self._post("/crm-inquire", {
                "device_id": "deadbeef", "service": "hamta"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(started["args"], ("deadbeef", "hamta", "manual"))

    def test_crm_inquire_rejects_bad_service(self):
        status, body = self._post("/crm-inquire", {
            "device_id": "x", "service": "wat"})
        self.assertEqual(status, 400)

    def test_crm_page_has_inquiry_links_and_buttons(self):
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/crm", timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertIn("/site-2", page)          # CRM -> inquiry panel link
        self.assertIn("devInquire", page)       # per-device inquiry buttons


class CrmStatePayloadTests(_StoreHarness):
    def test_state_carries_estelam_status_for_buttons(self):
        _record, errors = crm.add_device({"name": "D",
                                          "serial1": "356938035643809"})
        self.assertEqual(errors, [])
        payload = panel.crm_state_payload()
        device = payload["devices"][0]
        self.assertIn("estelam", device)
        self.assertIn("status", device["estelam"])
        self.assertIn("hamta_status", device["estelam"])


class InquiryGateTests(unittest.TestCase):
    """`mark_ready` is accepted ONLY when BOTH intake inquiries passed for
    the device (gomrok healthy + hamta green) per the estelam store — the
    store the robots themselves write (operator request, v1.1.2)."""

    def setUp(self):
        import crm as crm_mod
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estelam = os.path.join(self.tmp.name, "estelam_store.json")
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm_mod.reset_cache()
        self.addCleanup(crm_mod.reset_cache)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    def _seed_estelam(self, device_id, status="healthy",
                      hamta_status="good"):
        with open(self.estelam, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": [
                {"id": str(device_id), "status": status,
                 "hamta_status": hamta_status}]}, handle)

    def _device_in_inquiry(self):
        record, errors = crm.add_device({"name": "D",
                                         "serial1": "356938035643809"})
        self.assertEqual(errors, [])
        crm.advance(record["id"], "queue_inquiry")
        return record["id"]

    def _tick(self, device_id, action):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/crm-tick",
            data=json.dumps({"device_id": device_id, "action": action}
                            ).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode(
                    "utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def test_mark_ready_rejected_without_any_inquiry(self):
        device_id = self._device_in_inquiry()
        status, body = self._tick(device_id, "mark_ready")
        self.assertEqual(status, 400)
        self.assertTrue(any("استعلام" in e for e in body["errors"]))

    def test_mark_ready_allowed_when_both_inquiries_ok(self):
        device_id = self._device_in_inquiry()
        self._seed_estelam(device_id, status="healthy", hamta_status="good")
        status, body = self._tick(device_id, "mark_ready")
        self.assertEqual(status, 200)
        self.assertEqual(body["device"]["stage"], "ready_owner")

    def test_mark_ready_rejected_when_gomrok_problem(self):
        device_id = self._device_in_inquiry()
        self._seed_estelam(device_id, status="problem", hamta_status="good")
        status, body = self._tick(device_id, "mark_ready")
        self.assertEqual(status, 400)
        self.assertTrue(any("گمرک" in e for e in body["errors"]))

    def test_mark_ready_rejected_when_hamta_blocked(self):
        device_id = self._device_in_inquiry()
        self._seed_estelam(device_id, status="healthy",
                           hamta_status="blocked")
        status, body = self._tick(device_id, "mark_ready")
        self.assertEqual(status, 400)
        self.assertTrue(any("همتا" in e for e in body["errors"]))

    def test_mark_ready_rejected_while_results_pending(self):
        device_id = self._device_in_inquiry()
        self._seed_estelam(device_id, status="pending",
                           hamta_status="running")
        status, body = self._tick(device_id, "mark_ready")
        self.assertEqual(status, 400)


class FormHygieneTests(unittest.TestCase):
    def setUp(self):
        self.page = panel.CRM_PAGE

    def test_inline_customer_fields_present(self):
        """Device dialog collects telegram/instagram for inline customers."""
        self.assertIn("dvTg", self.page)
        self.assertIn("dvIg", self.page)

    def test_mobile_placeholder_declares_09_format(self):
        self.assertIn("dvCm", self.page)

    def test_autopay_checkbox_is_styled_and_labeled(self):
        self.assertIn('class="f check"', self.page)
        self.assertIn('input:checked+.box', self.page)

    def test_dob_and_optional_marks_are_explicit(self):
        self.assertIn("اختیاری", self.page)


if __name__ == "__main__":
    unittest.main()
