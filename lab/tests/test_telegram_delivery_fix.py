# -*- coding: utf-8 -*-
"""The fixed delivery engine, pinned through REAL HTTP against the local fake.

The v1.10.1 fix in telegram_notify gives four properties that were previously
missing: a destination gets its own slice of the caller's budget (the starved
receipt target), a missed destination is reported in one summary line plus one
JSONL record, an oversized receipt is shrunk once per call, and a permanently
dead chat is not retried. Each one is asserted here against
``lab/tests/_telegram_fake.py`` -- a loopback server on an ephemeral port --
instead of a mock of ``requests.post``, so what is checked is the real request
count, the real uploaded bytes and the real per-destination outcome.

Coverage map (the numbered items of the task spec):

1. every destination times out          -> TimeoutError + one record  (method 1)
2. a permanent chat error               -> 1 request, healthy chat served (2)
3. a partial failure                    -> summary line + one record  (3, 10)
4. a full success                       -> silent, no record          (4)
5. the record carries no secret         -> token / base / text absent, also
                                           for a URL-bearing connection error (5)
6. chat_id targets exactly one chat     -> additive lists ignored     (6)
7. extra_chat_ids are additive          -> de-duplicated              (7)
8. the v1.10 report channel             -> the real CRM reader        (8)
9. a big photo is shrunk                -> smaller JPEG, source intact (9)
10. shrinking never raises              -> no Pillow, corrupt file    (10)
11. the temp copy is removed            -> also when the send fails   (11)

Runs both ways:
  "C:/Program Files/Python310/python.exe" lab/tests/test_telegram_delivery_fix.py
  "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py \
      test_telegram_delivery_fix
"""
import contextlib
import hashlib
import io
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _telegram_fake  # noqa: E402  (same directory)

DATA_DIR = _telegram_fake.temp_data_dir()   # BEFORE app_paths / telegram_notify

import telegram_notify  # noqa: E402

#: Scratch space for every fixture and config this module writes.
SCRATCH = tempfile.mkdtemp(prefix="irica_tg_fix_")

FIRST = "111111"
SECOND = "222222"
THIRD = "333333"
CHANNEL = "-100555000111"
TARGET = "999999"

#: The exact record shape `_report_missed_destinations` promises.
RECORD_FIELDS = {"ts", "event", "method", "ok", "total", "delivered",
                 "failed", "elapsed_sec", "budget_sec"}

#: The fake fixture is built once per run and never modified by the tests.
_BIG_PHOTO = None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_big_png(path, width=1300, height=2400):
    """A deterministic 1300x2400 PNG, well over 1 MB, built locally.

    Seeded random RGB noise: the lossless PNG keeps every bit the JPEG encoder
    then throws away, so the shrink ratio is unambiguous. This is never the
    operator's receipt screenshot -- Pillow paints it from a seeded PRNG.
    """
    from PIL import Image

    pixels = random.Random(20260924).randbytes(width * height * 3)
    Image.frombytes("RGB", (width, height), pixels).save(path, format="PNG")
    return path


def big_photo():
    """The cached big fixture (built on first use, kept for the whole run)."""
    global _BIG_PHOTO
    if _BIG_PHOTO is None:
        _BIG_PHOTO = _build_big_png(os.path.join(SCRATCH, "big_receipt.png"))
    return _BIG_PHOTO


def _permanent_for(bad_id, description="Bad Request: chat not found"):
    """A fake mode: `bad_id` answers the real permanent error, everyone else ok."""
    def mode(chat_id, method, seen_index):
        if chat_id == bad_id:
            return 400, {"ok": False, "description": description}
        return 200, {"ok": True, "result": {"message_id": seen_index + 1}}

    return mode


class _TempFileWatcher(threading.Thread):
    """Note every ``tg_photo_*`` name that appears in a directory.

    The notifier's temp copy is deleted before the call returns, so a test that
    only lists the directory afterwards cannot tell "never created" from
    "created and cleaned up". Polling from a thread does.
    """

    def __init__(self, directory, interval=0.005):
        super().__init__(name="temp-file-watcher", daemon=True)
        self.directory = directory
        self.interval = interval
        self.seen = []
        self._done = threading.Event()

    def run(self):
        while not self._done.is_set():
            try:
                names = os.listdir(self.directory)
            except OSError:
                names = []
            for name in names:
                if name.startswith("tg_photo_") and name not in self.seen:
                    self.seen.append(name)
            self._done.wait(self.interval)

    def stop(self):
        self._done.set()
        self.join(timeout=5)


class DeliveryFixTestCase(unittest.TestCase):
    """Shared plumbing: a scratch dir, a patched delivery log, fake endpoints."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="irica_tg_fix_case_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # No test may write the delivery log where the live copy lives.
        self.log_path = os.path.join(self.tmp, "telegram_delivery.jsonl")
        patcher = patch.object(telegram_notify, "delivery_log_path",
                               lambda: self.log_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._fakes = []

    # -- plumbing --------------------------------------------------------
    def start_fake(self, chat_ids, mode="ok", delay=None):
        """Start a loopback Bot API and point telegram_notify's config at it."""
        fake = _telegram_fake.FakeTelegram(mode=mode, delay=delay).start()
        self.addCleanup(fake.stop)
        self._fakes.append(fake)
        cfg = fake.write_config(
            os.path.join(self.tmp, "telegram_config_%d.json" % len(self._fakes)),
            chat_ids)
        self.addCleanup(_telegram_fake.use_config(telegram_notify, cfg))
        return fake

    def small_png(self, name="receipt.png", size_bytes=1200):
        """A small upload fixture (below the shrink threshold, deterministic)."""
        return _telegram_fake.make_receipt_png(os.path.join(self.tmp, name),
                                               size_bytes=size_bytes)

    def capture(self):
        """Send with stdout captured, so only the test's own asserts print."""
        return contextlib.redirect_stdout(io.StringIO())

    def records(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def only_record(self):
        records = self.records()
        self.assertEqual(len(records), 1, records)
        return records[0]

    # -- 1 ---------------------------------------------------------------
    def test_all_destinations_timing_out_raise_timeout_and_are_recorded(self):
        fake = self.start_fake([FIRST, SECOND], mode="blackhole")
        photo = self.small_png()
        started = time.monotonic()
        with self.capture():
            with self.assertRaises(TimeoutError):
                telegram_notify.send_photo(photo, caption="c", timeout=1.5)
        elapsed = time.monotonic() - started
        self.assertLessEqual(elapsed, 3.0, "took %.2fs" % elapsed)
        # The per-destination slice is what makes this 1 attempt each: without
        # it the first destination spends the whole budget and the second is
        # never tried at all.
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1})
        record = self.only_record()
        self.assertIs(record["ok"], False)
        self.assertEqual(record["event"], "telegram_delivery")
        self.assertEqual(record["method"], "sendPhoto")
        self.assertEqual(record["total"], 2)
        self.assertEqual(record["delivered"], [])
        self.assertEqual([f["chat_id"] for f in record["failed"]],
                         [FIRST, SECOND])
        self.assertTrue(all(f["permanent"] is False for f in record["failed"]),
                        record["failed"])

    # -- 2 ---------------------------------------------------------------
    def test_a_permanent_chat_error_is_not_retried_and_spares_the_healthy_chat(self):
        fake = self.start_fake([FIRST, SECOND], mode=_permanent_for(FIRST))
        photo = self.small_png()
        started = time.monotonic()
        with self.capture():
            results = telegram_notify.send_photo(photo, caption="c", timeout=20)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0, "took %.2fs" % elapsed)
        # exactly one attempt for the dead chat: no retry storm
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1})
        healthy = [call for call in fake.calls() if call["chat_id"] == SECOND]
        self.assertEqual(len(healthy), 1, healthy)
        self.assertEqual(healthy[0]["method"], "sendPhoto")
        self.assertEqual(healthy[0]["photo_bytes"], os.path.getsize(photo))
        self.assertEqual(len(results), 1)

    # -- 3 ---------------------------------------------------------------
    def test_a_partial_failure_prints_one_summary_line_and_writes_one_record(self):
        fake = self.start_fake([FIRST, SECOND], mode=_permanent_for(FIRST))
        photo = self.small_png()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            results = telegram_notify.send_photo(photo, caption="c", timeout=20)
        self.assertEqual(len(results), 1, "the healthy send must still be returned")
        naghs = [line for line in buffer.getvalue().splitlines()
                 if "[NAGHS]" in line]
        self.assertEqual(len(naghs), 1, buffer.getvalue())
        self.assertIn(FIRST, naghs[0])
        self.assertNotIn(SECOND, naghs[0])
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1})
        record = self.only_record()
        self.assertEqual(set(record), RECORD_FIELDS)
        self.assertIs(record["ok"], False)
        self.assertEqual(record["event"], "telegram_delivery")
        self.assertEqual(record["method"], "sendPhoto")
        self.assertEqual(record["total"], 2)
        self.assertEqual(record["delivered"], [SECOND])
        self.assertEqual(len(record["failed"]), 1)
        self.assertEqual(record["failed"][0]["chat_id"], FIRST)
        self.assertIs(record["failed"][0]["permanent"], True)
        self.assertIn("chat not found", record["failed"][0]["error"])
        self.assertGreater(record["elapsed_sec"], 0)
        self.assertTrue(0 < record["budget_sec"] <= 20, record["budget_sec"])
        self.assertGreater(record["ts"], 0)

    def test_a_partial_failure_without_a_budget_is_still_reported(self):
        """`deadline=None` keeps the original delivery behaviour -- and the
        miss is still visible in the log (the budget field then is None)."""
        fake = self.start_fake([FIRST, SECOND], mode=_permanent_for(FIRST))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            results = telegram_notify.send_photo(self.small_png(), caption="c")
        self.assertEqual(len(results), 1)
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1})
        self.assertEqual(len([line for line in buffer.getvalue().splitlines()
                              if "[NAGHS]" in line]), 1, buffer.getvalue())
        record = self.only_record()
        self.assertIsNone(record["budget_sec"])
        self.assertEqual(record["delivered"], [SECOND])
        self.assertEqual(set(record), RECORD_FIELDS)

    # -- 4 ---------------------------------------------------------------
    def test_a_full_success_is_quiet(self):
        fake = self.start_fake([FIRST, SECOND])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            results = telegram_notify.send_photo(self.small_png(), caption="c",
                                                 timeout=20)
        self.assertEqual(len(results), 2)
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1})
        self.assertNotIn("NAGHS", buffer.getvalue())
        self.assertEqual(buffer.getvalue(), "", "a clean send printed something")
        self.assertEqual(self.records(), [])

    # -- 5 ---------------------------------------------------------------
    def test_the_record_carries_no_secrets(self):
        caption = "SECRET-CAPTION-MARKER-9f3a"
        photo = self.small_png()
        permanent = self.start_fake([FIRST, SECOND], mode=_permanent_for(FIRST))
        with self.capture():
            telegram_notify.send_photo(photo, caption=caption, timeout=20)
        # A timeout writes the requests error text (host and port only, no
        # scheme, no token) -- the wider record must stay clean just the same.
        blackhole = self.start_fake([FIRST], mode="blackhole")
        with self.capture():
            with self.assertRaises(TimeoutError):
                telegram_notify.send_photo(photo, caption=caption, timeout=1.5)
        with open(self.log_path, encoding="utf-8") as handle:
            text = handle.read()
        for secret in ("TEST:token", permanent.api_base, blackhole.api_base,
                       caption, "SECRET"):
            self.assertNotIn(secret, text)
        records = self.records()
        self.assertEqual(len(records), 2, records)
        for record in records:
            self.assertEqual(set(record), RECORD_FIELDS)

    def test_a_connection_error_cannot_leak_the_bot_token(self):
        """A refused port makes requests put the request URL INTO the error:

            ...Max retries exceeded with url: /bot<TOKEN>/sendMessage

        That text is printed to stdout, stored in ``failed[].error`` and
        re-raised by ``_fanout``, so the configured token must not survive in
        any of the three -- while the diagnostic (the host, the attempt and
        deadline wording) must survive whatever redacts it.
        """
        token = "7777777:AAzzSECRETzz"
        cfg_path = os.path.join(self.tmp, "telegram_config_refused.json")
        with open(cfg_path, "w", encoding="utf-8") as handle:
            # loopback port 9: refused at once, nothing leaves this machine
            json.dump({"api_base": "http://127.0.0.1:9", "bot_token": token,
                       "chat_ids": [FIRST]}, handle)
        self.addCleanup(_telegram_fake.use_config(telegram_notify, cfg_path))

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(Exception) as caught:
                telegram_notify.send_message("hi", timeout=1)
        self.assertIsInstance(caught.exception, (RuntimeError, TimeoutError))
        error_text = str(caught.exception)
        record = self.only_record()

        for where, text in (("stdout", buffer.getvalue()),
                            ("record", json.dumps(record)),
                            ("exception", error_text)):
            self.assertNotIn(token, text, "%s leaked the bot token" % where)
            self.assertNotIn(token.split(":", 1)[1], text,
                             "%s leaked the bot token" % where)

        # ... the redaction must not take the diagnosis with it
        self.assertIn("127.0.0.1", error_text)
        self.assertIn("attempts", error_text)
        self.assertTrue(error_text.strip())
        self.assertTrue(record["failed"][0]["error"].strip())
        self.assertIn("127.0.0.1", record["failed"][0]["error"])

    # -- 6 ---------------------------------------------------------------
    def test_an_explicit_chat_id_targets_exactly_one_destination(self):
        fake = self.start_fake([FIRST, SECOND])
        with self.capture():
            result = telegram_notify.send_message("hi", chat_id=TARGET,
                                                  extra_chat_ids=[THIRD])
        self.assertEqual(fake.counts(), {TARGET: 1})
        for other in (FIRST, SECOND, THIRD):
            self.assertNotIn(other, fake.counts(), "sudden fanout")
        self.assertEqual(fake.calls()[0]["method"], "sendMessage")
        self.assertEqual(len(result), 1)

    def test_a_whitespace_only_chat_id_is_rejected(self):
        fake = self.start_fake([FIRST, SECOND])
        with self.capture():
            with self.assertRaises(ValueError):
                telegram_notify.send_message("hi", chat_id="   ")
        self.assertEqual(fake.counts(), {}, "a request escaped")

    def test_an_empty_chat_id_is_recorded_not_pinned(self):
        """``chat_id=""`` is falsy, so it is NOT the exactly-one path. The spec
        asks for the observation rather than a pinned value: it is printed
        below and nothing is asserted about where the message went."""
        fake = self.start_fake([FIRST, SECOND])
        before = fake.counts()
        observed = "no exception"
        with self.capture():
            try:
                telegram_notify.send_message("hi", chat_id="")
            except BaseException as exc:        # noqa: BLE001 - observation only
                observed = "%s: %s" % (type(exc).__name__, exc)
        after = fake.counts()
        print('NOTE chat_id="" observed: %s; requests before=%s after=%s'
              % (observed, before, after))

    # -- 7 ---------------------------------------------------------------
    def test_extra_chat_ids_are_additive_and_deduplicated(self):
        fake = self.start_fake([FIRST, SECOND])
        with self.capture():
            results = telegram_notify.send_photo(self.small_png(), caption="c",
                                                 timeout=20,
                                                 extra_chat_ids=[SECOND, THIRD])
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1, THIRD: 1})
        self.assertEqual(len(results), 3)

    # -- 8 ---------------------------------------------------------------
    def _crm_store(self, name, settings):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "settings": settings}, handle)
        return path

    def test_a_photo_report_reaches_the_chats_and_the_channel(self):
        fake = self.start_fake([FIRST, SECOND])
        store = self._crm_store("crm_store.json", {"report_chat_id": CHANNEL})
        with patch.dict(os.environ, {"IRICA_CRM_STORE": store}):
            extras = telegram_notify.report_extra_chat_ids()
            self.assertEqual(extras, [CHANNEL])
            with self.capture():
                results = telegram_notify.send_photo(
                    self.small_png(), caption="c", timeout=20,
                    extra_chat_ids=extras)
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1, CHANNEL: 1})
        self.assertEqual(len(results), 3)

    def test_without_a_channel_nothing_changes(self):
        fake = self.start_fake([FIRST, SECOND])
        store = self._crm_store("crm_store_off.json", {})
        with patch.dict(os.environ, {"IRICA_CRM_STORE": store}):
            self.assertIsNone(telegram_notify.report_extra_chat_ids())
            with self.capture():
                results = telegram_notify.send_photo(
                    self.small_png(), caption="c", timeout=20,
                    extra_chat_ids=telegram_notify.report_extra_chat_ids())
        self.assertEqual(fake.counts(), {FIRST: 1, SECOND: 1})
        self.assertEqual(len(results), 2)

    # -- 9 ---------------------------------------------------------------
    def test_a_big_photo_is_shrunk_without_touching_the_source(self):
        source = big_photo()
        before = _sha256(source)
        original_bytes = os.path.getsize(source)
        self.assertGreater(original_bytes, 1_000_000, original_bytes)

        upload, temp = telegram_notify._shrink_photo(source)
        self.addCleanup(telegram_notify._remove_file, temp)
        self.assertIsNotNone(temp, "a >1 MB photo was not shrunk")
        self.assertNotEqual(upload, source)
        self.assertTrue(os.path.isfile(temp), temp)
        uploaded = os.path.getsize(temp)
        self.assertLessEqual(uploaded, original_bytes // 3,
                             "%d bytes from %d" % (uploaded, original_bytes))

        from PIL import Image
        with Image.open(temp) as image:
            self.assertEqual(image.format, "JPEG")
            width, height = image.size
        self.assertLessEqual(max(width, height), telegram_notify.PHOTO_MAX_EDGE)
        self.assertEqual(max(width, height), 2000)
        # the same picture: the ratio may slip by at most one pixel
        self.assertLessEqual(abs(width / height - 1300 / 2400) * height, 1.0)

        self.assertEqual(_sha256(source), before, "the source was modified")

        second_upload, second_temp = telegram_notify._shrink_photo(source)
        self.addCleanup(telegram_notify._remove_file, second_temp)
        self.assertNotEqual(second_temp, temp, "the temp copy was reused")
        self.assertEqual(second_upload, second_temp)
        self.assertEqual(_sha256(source), before, "the source was modified")
        self.assertEqual(os.path.getsize(second_temp), uploaded)

    def test_a_small_or_missing_photo_is_returned_untouched(self):
        small = self.small_png("small.png", size_bytes=299 * 1024)
        self.assertLess(os.path.getsize(small),
                        telegram_notify.PHOTO_SHRINK_MIN_BYTES)
        self.assertEqual(telegram_notify._shrink_photo(small), (small, None))
        missing = os.path.join(self.tmp, "no-such-photo.png")
        self.assertEqual(telegram_notify._shrink_photo(missing), (missing, None))

    # -- 10 --------------------------------------------------------------
    def test_shrinking_without_pillow_returns_the_original(self):
        source = big_photo()
        with patch.dict(sys.modules, {"PIL": None}):
            self.assertEqual(telegram_notify._shrink_photo(source), (source, None))

    def test_a_corrupt_big_file_returns_the_original(self):
        path = os.path.join(self.tmp, "corrupt.png")
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
            handle.write(random.Random(4242).randbytes(400 * 1024))
        self.assertGreater(os.path.getsize(path),
                           telegram_notify.PHOTO_SHRINK_MIN_BYTES)
        self.assertEqual(telegram_notify._shrink_photo(path), (path, None))

    # -- 11 --------------------------------------------------------------
    def _send_in_watched_tempdir(self, fake_kwargs, timeout=20, expect_raise=False):
        """Run one send_photo while a watcher polls the temp directory."""
        temp_root = tempfile.mkdtemp(prefix="irica_tg_fix_tmp_")
        self.addCleanup(shutil.rmtree, temp_root, ignore_errors=True)
        previous = tempfile.tempdir
        tempfile.tempdir = temp_root
        self.addCleanup(setattr, tempfile, "tempdir", previous)

        self.start_fake([FIRST], **fake_kwargs)
        watcher = _TempFileWatcher(temp_root)
        watcher.start()
        try:
            with self.capture():
                if expect_raise:
                    with self.assertRaises(TimeoutError):
                        telegram_notify.send_photo(big_photo(), caption="c",
                                                   timeout=timeout)
                else:
                    self.assertEqual(
                        len(telegram_notify.send_photo(big_photo(), caption="c",
                                                       timeout=timeout)), 1)
        finally:
            watcher.stop()
        return watcher, temp_root

    def test_send_photo_removes_its_temp_file_after_a_successful_send(self):
        watcher, temp_root = self._send_in_watched_tempdir(
            {"mode": "ok", "delay": {FIRST: 0.8}})
        self.assertTrue(watcher.seen, "the temp copy was never created")
        self.assertEqual(os.listdir(temp_root), [], "the temp copy was left")
        self.assertTrue(os.path.isfile(big_photo()), "the source went missing")

    def test_send_photo_removes_its_temp_file_when_the_send_fails(self):
        watcher, temp_root = self._send_in_watched_tempdir(
            {"mode": "blackhole"}, timeout=1.5, expect_raise=True)
        self.assertTrue(watcher.seen, "the temp copy was never created")
        self.assertEqual(os.listdir(temp_root), [], "the temp copy was left")
        self.assertTrue(os.path.isfile(big_photo()), "the source went missing")


def tearDownModule():
    """Remove this module's scratch fixtures."""
    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
