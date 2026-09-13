# -*- coding: utf-8 -*-
"""v1.5 B10 — Telegram registration handoff (+ additive chat_id targeting).

After a committed `handover` tick the panel sends EXACTLY ONE message with
the user-specified 4-line ASCII body (kodmeli/imei1/imei2/passport) to the
`registration_chat_id` destination. Fail-closed on unset destination /
missing passport / send failure — the handover itself always stays
committed and the operator gets a Persian `handoff_notice`. The sensitive
body targets ONLY the registration channel via the new additive
`chat_id` parameter on telegram_notify.send_message (never the configured
fanout list); the no-`chat_id` path is byte-identical to before.
Also covers: settings validation, the /crm-handoff-resend route for stages
handed and later, and the /activate cp1252 print guard.
"""
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import crm
import panel
import telegram_notify

OWNER_FIELDS = {
    "first_name": "رضا", "last_name": "محمدی",
    "national": "0012345679", "birth": "1375/04/29",
    "mobile": "09121234567", "passport": "PP0000001",
}
SERIAL1 = "356938035643809"
SERIAL2 = "356938035643810"
DESTINATION = "-1001234567890"
# The exact user-specified 4-line body; the imei2 line is ALWAYS present,
# empty value when serial2 is missing.
EXPECTED_BODY = (
    "kodmeli = 0012345679\n"
    "imei1 = 356938035643809\n"
    "imei2 = \n"
    "passport = PP0000001"
)


def panel_source():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def fn_body_js(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[m.start():i]


class HandoffHttpHarness(unittest.TestCase):
    """Real handler + isolated stores (test_crm_api pattern). Every test
    mocks telegram_notify.send_message — nothing ever leaves the machine."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # -- helpers ---------------------------------------------------------
    def _post(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def _add_device(self, **extra):
        record, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": SERIAL1, **extra})
        assert errs == [], errs
        return record

    def _add_owner(self, **override):
        record, errs = crm.add_owner({**OWNER_FIELDS, **override})
        assert errs == [], errs
        return record

    def _patch_device(self, device_id, **fields):
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device_id)
        dev.update(fields)
        crm.store_write(data)
        return dev

    def _patch_owner(self, owner_id, **fields):
        data = crm.store_read()
        owner = next(o for o in data["owners"] if o["id"] == owner_id)
        owner.update(fields)
        crm.store_write(data)
        return owner

    def _seed_ready_for_handover(self, with_serial2=True):
        """Device + passported owner bound at ready_owner."""
        device = self._add_device(
            **({"serial2": SERIAL2} if with_serial2 else {}))
        owner = self._add_owner()
        self._patch_device(device["id"], stage="ready_owner",
                           owner_id=owner["id"])
        return device, owner

    def _set_destination(self, value=DESTINATION):
        status, body = self._post("/crm-settings",
                                  {"registration_chat_id": value})
        assert status == 200, body
        return body

    def _read_log(self, log_file):
        with open(log_file, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _assert_no_personal_data(self, log_file, extra_secrets=()):
        """Log hygiene (plan §12): no national/passport/serial — and no
        exception text — in ANY JSONL line."""
        blob = json.dumps(self._read_log(log_file), ensure_ascii=False)
        secrets = [OWNER_FIELDS["national"], OWNER_FIELDS["passport"],
                   OWNER_FIELDS["mobile"], OWNER_FIELDS["first_name"],
                   OWNER_FIELDS["last_name"], SERIAL1, SERIAL2,
                   *extra_secrets]
        for secret in secrets:
            self.assertNotIn(secret, blob)


class TestHandoverTickHandoff(HandoffHttpHarness):
    """The /crm-tick hook: one message after handover, fail-closed."""

    def test_destination_unset_skips_send_notifies_and_logs(self):
        device, owner = self._seed_ready_for_handover(with_serial2=False)
        log_file = os.path.join(self.tmp.name, "crm_events.jsonl")
        with patch.object(telegram_notify, "send_message") as send, \
                patch.object(crm, "log_path", return_value=log_file):
            status, body = self._post("/crm-tick", {
                "device_id": device["id"], "action": "handover"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["device"]["stage"], "handed")
        send.assert_not_called()
        self.assertIn("تنظیم نشده", body["handoff_notice"])
        skipped = [e for e in self._read_log(log_file)
                   if e.get("event") == "registration_handoff_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].get("device_id"), device["id"])
        self.assertEqual(skipped[0].get("reason"), "no_destination")
        self._assert_no_personal_data(log_file)

    def test_destination_set_sends_exact_body_once_to_destination(self):
        device, owner = self._seed_ready_for_handover(with_serial2=False)
        self._set_destination()
        log_file = os.path.join(self.tmp.name, "crm_events.jsonl")
        with patch.object(telegram_notify, "send_message") as send, \
                patch.object(crm, "log_path", return_value=log_file):
            status, body = self._post("/crm-tick", {
                "device_id": device["id"], "action": "handover"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["device"]["stage"], "handed")
        self.assertEqual(body["handoff_notice"], "")
        # EXACTLY one message, with the exact 4-line body (empty imei2
        # line included), targeted at the registration destination only.
        self.assertEqual(send.call_count, 1)
        args, kwargs = send.call_args
        self.assertEqual(args[0], EXPECTED_BODY)
        self.assertEqual(kwargs.get("chat_id"), DESTINATION)
        # Log: registration_handoff_sent with the destination id ONLY.
        events = self._read_log(log_file)
        sent = [e for e in events
                if e.get("event") == "registration_handoff_sent"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].get("device_id"), device["id"])
        self.assertEqual(sent[0].get("destination"), DESTINATION)
        self.assertEqual(set(sent[0]) - {"at"},
                         {"level", "event", "device_id", "destination"})
        self._assert_no_personal_data(log_file)

    def test_serial2_present_fills_imei2_line(self):
        device, owner = self._seed_ready_for_handover(with_serial2=True)
        self._set_destination()
        with patch.object(telegram_notify, "send_message") as send:
            status, body = self._post("/crm-tick", {
                "device_id": device["id"], "action": "handover"})
        self.assertEqual(status, 200, body)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            send.call_args[0][0],
            "kodmeli = 0012345679\n"
            "imei1 = 356938035643809\n"
            "imei2 = 356938035643810\n"
            "passport = PP0000001")

    def test_owner_without_passport_skips_send_notifies(self):
        device, owner = self._seed_ready_for_handover(with_serial2=False)
        self._patch_owner(owner["id"], passport="")  # passport dropped
        self._set_destination()
        log_file = os.path.join(self.tmp.name, "crm_events.jsonl")
        with patch.object(telegram_notify, "send_message") as send, \
                patch.object(crm, "log_path", return_value=log_file):
            status, body = self._post("/crm-tick", {
                "device_id": device["id"], "action": "handover"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["device"]["stage"], "handed")
        send.assert_not_called()
        self.assertIn("گذرنامه", body["handoff_notice"])
        skipped = [e for e in self._read_log(log_file)
                   if e.get("event") == "registration_handoff_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].get("reason"), "no_passport")
        self._assert_no_personal_data(log_file)

    def test_send_failure_handover_still_committed_log_clean(self):
        device, owner = self._seed_ready_for_handover(with_serial2=False)
        self._set_destination()
        log_file = os.path.join(self.tmp.name, "crm_events.jsonl")
        with patch.object(telegram_notify, "send_message",
                          side_effect=RuntimeError("worker boom")), \
                patch.object(crm, "log_path", return_value=log_file):
            status, body = self._post("/crm-tick", {
                "device_id": device["id"], "action": "handover"})
        # The handover NEVER fails because of the send: still 200/ok/handed.
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["device"]["stage"], "handed")
        self.assertIn("ناموفق", body["handoff_notice"])
        events = self._read_log(log_file)
        failed = [e for e in events
                  if e.get("event") == "registration_handoff_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].get("destination"), DESTINATION)
        # The handover itself was committed and logged.
        stages = [e for e in events if e.get("event") == "stage"
                  and e.get("action") == "handover"]
        self.assertEqual(len(stages), 1)
        # No national/passport/serial — and NO exception text — in any line.
        self._assert_no_personal_data(log_file, extra_secrets=("worker boom",))


class TestRegistrationChatSetting(HandoffHttpHarness):
    """/crm-settings: registration_chat_id validation."""

    def test_numeric_destination_accepted_and_stored(self):
        status, body = self._post("/crm-settings",
                                  {"registration_chat_id": DESTINATION})
        self.assertEqual(status, 200, body)
        settings = crm.store_read()["settings"]
        self.assertEqual(settings["registration_chat_id"], DESTINATION)

    def test_whitespace_is_trimmed_before_validation(self):
        status, body = self._post(
            "/crm-settings", {"registration_chat_id": f"  {DESTINATION}  "})
        self.assertEqual(status, 200, body)
        self.assertEqual(crm.store_read()["settings"]["registration_chat_id"],
                         DESTINATION)

    def test_non_numeric_rejected_400_persian(self):
        status, body = self._post("/crm-settings",
                                  {"registration_chat_id": "abc"})
        self.assertEqual(status, 400, body)
        self.assertTrue(any("عددی" in e for e in body["errors"]), body)
        # rejected value is never stored
        self.assertEqual(
            crm.store_read()["settings"]["registration_chat_id"], "")

    def test_empty_clears_destination(self):
        self._set_destination()
        status, body = self._post("/crm-settings",
                                  {"registration_chat_id": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual(
            crm.store_read()["settings"]["registration_chat_id"], "")


class TestHandoffResend(HandoffHttpHarness):
    """POST /crm-handoff-resend — the case-detail re-send button."""

    def _handed_device(self):
        device = self._add_device()
        owner = self._add_owner()
        self._patch_device(device["id"], stage="handed",
                           owner_id=owner["id"])
        return device, owner

    def test_resend_at_handed_sends_exactly_once(self):
        device, owner = self._handed_device()
        self._set_destination()
        with patch.object(telegram_notify, "send_message") as send:
            status, body = self._post("/crm-handoff-resend",
                                      {"device_id": device["id"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertEqual(body.get("notice"), "")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][0], EXPECTED_BODY)
        self.assertEqual(send.call_args[1].get("chat_id"), DESTINATION)
        # the case itself is untouched
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["stage"], "handed")

    def test_resend_unset_destination_ok_false_persian_notice(self):
        device, owner = self._handed_device()
        with patch.object(telegram_notify, "send_message") as send:
            status, body = self._post("/crm-handoff-resend",
                                      {"device_id": device["id"]})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["ok"], body)
        self.assertIn("تنظیم نشده", body.get("notice") or "")
        send.assert_not_called()

    def test_resend_wrong_stage_400(self):
        device = self._add_device()   # stage 'new' — not handed yet
        with patch.object(telegram_notify, "send_message"):
            status, body = self._post("/crm-handoff-resend",
                                      {"device_id": device["id"]})
        self.assertEqual(status, 400, body)
        self.assertTrue(body["errors"], body)

    def test_resend_unknown_device_400(self):
        with patch.object(telegram_notify, "send_message"):
            status, body = self._post("/crm-handoff-resend",
                                      {"device_id": "nope"})
        self.assertEqual(status, 400, body)
        self.assertTrue(body["errors"], body)


class TestTelegramChatIdParam(unittest.TestCase):
    """The additive `chat_id` parameter on send_message: exclusive
    single-chat targeting when set, byte-identical fanout when not."""

    CFG = {"api_base": "https://worker.example", "bot_token": "t",
           "chat_ids": ["111", "222"]}

    def test_without_chat_id_targets_configured_plus_extras(self):
        with patch.object(telegram_notify, "ensure_config",
                          return_value=dict(self.CFG)), \
                patch.object(telegram_notify, "_fanout",
                             return_value=[{"ok": True}]) as fanout:
            telegram_notify.send_message("hi", extra_chat_ids=["333", "222"])
        targets = fanout.call_args[0][2]
        self.assertEqual(targets, ["111", "222", "333"])

    def test_with_chat_id_targets_exactly_one_chat(self):
        with patch.object(telegram_notify, "ensure_config",
                          return_value=dict(self.CFG)), \
                patch.object(telegram_notify, "_fanout",
                             return_value=[{"ok": True}]) as fanout:
            telegram_notify.send_message("hi", chat_id=DESTINATION,
                                         extra_chat_ids=["111"])
        targets = fanout.call_args[0][2]
        self.assertEqual(targets, [DESTINATION])

    def test_whitespace_chat_id_raises_value_error(self):
        with patch.object(telegram_notify, "ensure_config",
                          return_value=dict(self.CFG)):
            with self.assertRaises(ValueError):
                telegram_notify.send_message("hi", chat_id="   ")


class TestSourceMarkers(unittest.TestCase):
    """Structural markers: /activate print guard, UI wiring, hook wiring."""

    def _src(self):
        return panel_source()

    # -- item 12: /activate print can never break the response -----------
    def test_activate_route_print_is_encoding_safe(self):
        src = self._src()
        start = src.index('self.path == "/activate"')
        chunk = src[start:src.index('self.path == "/toggle"')]
        self.assertIn("print(", chunk)                     # kept as diagnostic
        self.assertIn("except UnicodeEncodeError", chunk)  # but guarded

    # -- B10 wiring -------------------------------------------------------
    def test_handoff_stages_tuple_matches_spec(self):
        self.assertEqual(panel.HANDOFF_STAGES,
                         ("handed", "to_payment", "payment",
                          "payment_done", "done"))

    def test_crm_tick_hook_wired_on_handover(self):
        src = self._src()
        start = src.index('self.path == "/crm-tick"')
        chunk = src[start:src.index('self.path == "/crm-start-payment"')]
        self.assertIn('tick_action == "handover"', chunk)
        self.assertIn("crm_send_registration_handoff", chunk)
        self.assertIn("handoff_notice", chunk)

    def test_settings_html_has_regchat_field_label_and_help(self):
        body = fn_body_js(panel.CRM_PAGE, "settingsHtml")
        self.assertIn('id="stRegChat"', body)
        self.assertIn('dir="ltr"', body)
        self.assertIn('inputmode="numeric"', body)
        self.assertIn("شناسهٔ عددی کانال/گروه ثبت مسافری", body)
        self.assertIn("اطلاعات دستگاه و مسافر که برای فرآیند ثبت لازم است، "
                      "برای تحویل به مسئول ثبت به این کانال/گروه تلگرام "
                      "ارسال می‌شود.", body)
        saver = fn_body_js(panel.CRM_PAGE, "settingsSaveRegChat")
        self.assertIn("registration_chat_id", saver)
        self.assertIn("stRegChat", saver)

    def test_dev_tick_generic_path_surfaces_handoff_notice(self):
        body = fn_body_js(panel.CRM_PAGE, "devTick")
        self.assertIn(
            "if(r.handoff_notice){toast(r.handoff_notice,false);}", body)

    def test_case_detail_offers_resend_and_function_wired(self):
        body = fn_body_js(panel.CRM_PAGE, "caseDetail")
        self.assertIn("HANDOFF_STAGES_JS", body)
        self.assertIn("ارسال مجدد به تلگرام", body)
        fn = fn_body_js(panel.CRM_PAGE, "crmHandoffResend")
        self.assertIn("api('/crm-handoff-resend',{device_id:id})", fn)
        self.assertIn("r.notice", fn)


if __name__ == "__main__":
    unittest.main()
