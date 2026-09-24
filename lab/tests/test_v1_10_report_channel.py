# -*- coding: utf-8 -*-
"""v1.10 report channel — the optional Telegram channel id in CRM settings
(operator request, 2026-09-24): «گزارش‌ها به تلگرام تو تنظیمات امکان معرفی
چت ایدی کانال بذار».

Delivery is ADDITIVE by decision (operator, 2026-09-24): the configured chat
list keeps receiving every report and every inquiry result, and a configured
channel only ever gets a copy. That makes three properties load-bearing, and
each is pinned below:

- an empty channel changes nothing at all (the fanout target list is what it
  was before v1.10);
- a destination that is also in the configured list is not messaged twice;
- a channel that has gone away (kicked the bot, deleted, mistyped) can never
  take the report down with it — `_fanout` reports the failure of that one
  destination and still delivers to the rest.

The reader is deliberately forgiving: it runs on every report send, including
from the robot processes, so an unreadable store or a hand-edited value must
read as "not configured" rather than raise. The settings tab and the reader
share one numeric rule, so a value the tab accepted is never silently ignored
by the sender.
"""
import inspect
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm  # noqa: E402
import panel  # noqa: E402
import telegram_notify  # noqa: E402
from lab.tests.test_crm_api import CrmApiHarness  # noqa: E402

CHANNEL = "-1001234567890"
OTHER = "-1009876543210"


def _src(module_name):
    path = os.path.join(ROOT, module_name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class ReportChannelStoreTests(unittest.TestCase):
    """crm.report_chat_id() — the one reader the senders ask."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "crm_store.json")
        env = patch.dict(os.environ, {"IRICA_CRM_STORE": self.path})
        env.start()
        self.addCleanup(env.stop)

    def write_store(self, settings, raw=None):
        with open(self.path, "w", encoding="utf-8") as handle:
            if raw is None:
                json.dump({"version": 1, "settings": settings}, handle)
            else:
                handle.write(raw)

    def test_default_setting_exists_and_is_off(self):
        self.assertIn("report_chat_id", crm.DEFAULT_SETTINGS)
        self.assertEqual(crm.DEFAULT_SETTINGS["report_chat_id"], "")

    def test_missing_store_reads_as_not_configured(self):
        self.assertEqual(crm.report_chat_id(), "")

    def test_a_configured_channel_is_returned(self):
        self.write_store({"report_chat_id": CHANNEL})
        self.assertEqual(crm.report_chat_id(), CHANNEL)

    def test_a_positive_id_is_returned_too(self):
        # a private channel/group chat id is negative, but the reader does not
        # second-guess the sign — it is the settings tab's rule that gates it.
        self.write_store({"report_chat_id": "12345"})
        self.assertEqual(crm.report_chat_id(), "12345")

    def test_blank_reads_as_not_configured(self):
        for value in ("", "   ", None):
            self.write_store({"report_chat_id": value})
            self.assertEqual(crm.report_chat_id(), "", repr(value))

    def test_hand_edited_value_reads_as_not_configured(self):
        for value in ("abc", "+12345", "-100 1234", "100;rm -rf", "@channel",
                      "-1001234567890\n-1009876543210"):
            self.write_store({"report_chat_id": value})
            self.assertEqual(crm.report_chat_id(), "", repr(value))

    def test_an_unreadable_store_reads_as_not_configured(self):
        # a truncated/hand-edited store must not be the reason a report fails
        self.write_store({}, raw='{"version": 1, "settings": {')
        self.assertEqual(crm.report_chat_id(), "")

    def test_a_settings_key_of_the_wrong_type_reads_as_not_configured(self):
        self.write_store({"report_chat_id": ["-1001"]})
        self.assertEqual(crm.report_chat_id(), "")

    def test_the_tab_and_the_reader_agree_on_every_shape(self):
        """Whatever the settings route accepts, the sender must see — and a
        value it rejects must never be persisted for a sender to trip over."""
        for raw in ("", CHANNEL, "12345", "-0", "abc", "+12345", "-100 1234"):
            with self.subTest(raw=raw):
                # the route saves nothing when it rejects the batch, so a
                # rejected value leaves the stored channel as it was
                self.write_store({"report_chat_id": ""})
                settings, errors = panel.crm_update_settings(
                    {"report_chat_id": raw})
                if errors:
                    self.assertIsNone(settings)
                    self.assertEqual(crm.report_chat_id(), "", raw)
                else:
                    self.write_store(
                        {"report_chat_id": settings["report_chat_id"]})
                    self.assertEqual(crm.report_chat_id(),
                                     str(raw).strip(), raw)


class ReportChannelSettingsRouteTests(CrmApiHarness):
    """The settings route is the only writer — it must reach the store."""

    def _settings(self):
        status, body, _ = self._get("/crm-state")
        self.assertEqual(status, 200)
        return body.get("settings") or {}

    def test_the_route_saves_and_clears_a_channel(self):
        status, body = self._post("/crm-settings", {"report_chat_id": CHANNEL})
        self.assertEqual(status, 200, body)
        self.assertEqual(self._settings().get("report_chat_id"), CHANNEL)

        status, body = self._post("/crm-settings", {"report_chat_id": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual(self._settings().get("report_chat_id"), "")

    def test_a_non_numeric_channel_is_rejected_with_a_persian_message(self):
        status, body = self._post("/crm-settings", {"report_chat_id": "-100abc"})
        self.assertEqual(status, 400, body)
        self.assertTrue(body.get("errors"), body)
        self.assertEqual(self._settings().get("report_chat_id"), "")

    def test_saving_another_setting_leaves_the_channel_alone(self):
        self._post("/crm-settings", {"report_chat_id": CHANNEL})
        status, body = self._post("/crm-settings", {"sound_enabled": False})
        self.assertEqual(status, 200, body)
        self.assertEqual(self._settings().get("report_chat_id"), CHANNEL)


class ReportChannelNotifierTests(unittest.TestCase):
    """telegram_notify: what actually leaves the process."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "crm_store.json")
        env = patch.dict(os.environ, {"IRICA_CRM_STORE": self.path})
        env.start()
        self.addCleanup(env.stop)
        self.configured = ["111", "222"]

    def set_channel(self, value):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "settings": {"report_chat_id": value}},
                      handle)

    def cfg(self):
        return {"api_base": "http://127.0.0.1:1", "bot_token": "t",
                "chat_ids": list(self.configured)}

    def capture_send(self, **kwargs):
        """Run a real send_message/send_photo with the network stubbed out."""
        seen = []

        def record(method, cfg, data, files_factory=None, attempts=4,
                   deadline=None):
            seen.append(data["chat_id"])
            return {"ok": True, "result": {"message_id": len(seen)}}

        with patch.object(telegram_notify, "ensure_config", self.cfg), \
                patch.object(telegram_notify, "_post_confirmed", record):
            result = telegram_notify.send_message("متن گزارش", **kwargs)
        return seen, result

    # -- the reader ------------------------------------------------------
    def test_no_channel_means_no_extras(self):
        self.set_channel("")
        self.assertIsNone(telegram_notify.report_extra_chat_ids())

    def test_a_channel_reaches_the_notifier_through_the_store(self):
        # the lazy `import crm` inside the reader is the whole point: the robot
        # processes have no panel state to read the setting from.
        self.set_channel(CHANNEL)
        self.assertEqual(telegram_notify.report_chat_id(), CHANNEL)
        self.assertEqual(telegram_notify.report_extra_chat_ids(), [CHANNEL])

    def test_a_failing_store_is_reported_as_not_configured(self):
        with patch.object(crm, "report_chat_id", side_effect=RuntimeError("boom")):
            self.assertEqual(telegram_notify.report_chat_id(), "")
            self.assertIsNone(telegram_notify.report_extra_chat_ids())

    def test_the_targets_are_additive_and_deduplicated(self):
        cfg = self.cfg()
        self.assertEqual(
            telegram_notify._target_chat_ids(cfg, None), self.configured)
        self.assertEqual(
            telegram_notify._target_chat_ids(cfg, []), self.configured)
        self.assertEqual(
            telegram_notify._target_chat_ids(cfg, [CHANNEL]),
            self.configured + [CHANNEL])
        # a channel that is also configured is not messaged twice
        self.assertEqual(
            telegram_notify._target_chat_ids(cfg, [self.configured[1]]),
            self.configured)

    # -- what leaves the process -----------------------------------------
    def test_a_report_reaches_the_chats_and_the_channel(self):
        self.set_channel(CHANNEL)
        seen, result = self.capture_send(
            timeout=5, actor="تست",
            extra_chat_ids=telegram_notify.report_extra_chat_ids())
        self.assertEqual(seen, self.configured + [CHANNEL])
        self.assertEqual(len(result), 3)

    def test_without_a_channel_nothing_changes(self):
        self.set_channel("")
        seen, _ = self.capture_send(
            timeout=5, actor="تست",
            extra_chat_ids=telegram_notify.report_extra_chat_ids())
        self.assertEqual(seen, self.configured)

    def test_a_photo_report_reaches_the_channel_too(self):
        self.set_channel(CHANNEL)
        seen = []

        def record(method, cfg, data, files_factory=None, attempts=4,
                   deadline=None):
            seen.append((method, data["chat_id"]))
            return {"ok": True, "result": {"message_id": len(seen)}}

        with patch.object(telegram_notify, "ensure_config", self.cfg), \
                patch.object(telegram_notify, "_post_confirmed", record):
            telegram_notify.send_photo(
                os.path.join(self.tmp.name, "no-such-file.png"),
                caption="نتیجهٔ استعلام", timeout=5, actor="تست",
                extra_chat_ids=telegram_notify.report_extra_chat_ids())
        self.assertEqual(seen, [("sendPhoto", cid)
                                for cid in self.configured + [CHANNEL]])

    def test_exactly_one_destination_still_ignores_the_channel(self):
        """The registration handoff carries personal data to ONE destination;
        a configured report channel must never become a second recipient."""
        self.set_channel(CHANNEL)
        seen, _ = self.capture_send(
            timeout=5, actor="تست", chat_id="999",
            extra_chat_ids=telegram_notify.report_extra_chat_ids())
        self.assertEqual(seen, ["999"])

    def test_a_dead_channel_never_takes_the_report_down(self):
        self.set_channel(CHANNEL)
        seen = []

        def record(method, cfg, data, files_factory=None, attempts=4,
                   deadline=None):
            seen.append(data["chat_id"])
            if data["chat_id"] == CHANNEL:
                raise RuntimeError("chat not found")
            return {"ok": True, "result": {"message_id": len(seen)}}

        with patch.object(telegram_notify, "ensure_config", self.cfg), \
                patch.object(telegram_notify, "_post_confirmed", record):
            result = telegram_notify.send_message(
                "متن گزارش", timeout=5, actor="تست",
                extra_chat_ids=telegram_notify.report_extra_chat_ids())
        self.assertEqual(seen, self.configured + [CHANNEL])
        self.assertEqual(len(result), 2)


class ReportChannelWiringTests(unittest.TestCase):
    """Every report sender opts in; the two special paths stay untouched."""

    def test_the_three_panel_reports_pass_the_channel(self):
        for name in ("_crm_report_telegram", "_crm_finance_report_telegram",
                     "_crm_statement_telegram"):
            with self.subTest(sender=name):
                body = inspect.getsource(getattr(panel.Handler, name))
                self.assertIn("extras = tg.report_extra_chat_ids()", body)
                self.assertIn("extra_chat_ids=extras", body)
                self.assertIn("actor=self.actor", body)
                # never the exactly-one destination
                self.assertNotIn("chat_id=extras", body)

    def test_the_two_robots_pass_the_channel(self):
        for module in ("estelam_gomrok.py", "estelam_hamta.py"):
            with self.subTest(module=module):
                src = _src(module)
                self.assertIn("extra_chat_ids=tg.report_extra_chat_ids()", src)
                # the inquiry result is a report: it keeps its own timeout
                self.assertIn("timeout=15", src)

    def test_the_result_screenshot_passes_the_channel(self):
        src = _src("estelam_shot.py")
        self.assertIn("extra_chat_ids=tg.report_extra_chat_ids()", src)
        self.assertIn("tg.send_photo(", src)

    def test_the_registration_handoff_keeps_its_single_destination(self):
        body = inspect.getsource(panel.crm_send_registration_handoff)
        self.assertIn("chat_id=destination", body)
        self.assertNotIn("report_extra_chat_ids", body)
        self.assertNotIn("extra_chat_ids", body)

    def test_gomrok_py_is_untouched_by_this_feature(self):
        # contract: gomrok.py is read-only for this work, and its sends are
        # registration receipts to a caller-given destination, not reports.
        src = _src("gomrok.py")
        self.assertNotIn("report_extra_chat_ids", src)
        self.assertNotIn("report_chat_id", src)

    def test_the_settings_tab_can_edit_the_channel(self):
        js = "\n".join(re.findall(r"<script>([\s\S]*?)</script>",
                                  panel.CRM_PAGE))
        self.assertIn("id=\"stReportChat\"", panel.CRM_PAGE)
        self.assertIn("settingsSaveReportChat()", panel.CRM_PAGE)
        self.assertIn("api('/crm-settings',{report_chat_id:v('stReportChat')})",
                      js.strip())


if __name__ == "__main__":
    unittest.main()
