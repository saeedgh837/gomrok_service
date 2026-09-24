"""Does the payment-stage receipt photo reach EVERY destination in time -- and
is a missing destination still silent?

The operator reported (v1.10) that the payment-stage receipt never arrived for
several cases while the payment-link message did. Root cause: the receipt
capture is a ~1.41 MB full-page PNG and the robot's whole Telegram budget is
gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC = 25 s; on the upstream the live log implies,
that upload alone eats the budget, so the second destination was starved
("deadline reached after 0 attempts") while gomrok._telegram_deliver still
printed "ersal shod" and recorded nothing.

These tests drive the operator-visible layer itself, gomrok._telegram_deliver,
against the local fake Bot API (lab/tests/_telegram_fake.py). No test here
resolves a real host or uses a real token, and every path (gomrok._LOGS_PATH,
telegram_notify._CFG_PATH, telegram_notify.delivery_log_path) points into a
throwaway directory.

Scenarios:
  1. production shape   -- two destinations, the first delayed 7.5 s, harness
     receipt PNG (1,415,000 B): both destinations must get it, < 25 s, the
     robot must still report "ersal shod".
  2. tight budget      -- photo budget 8.0 s with a 7.5 s delay: every
     destination must at least be ATTEMPTED and the call must stay bounded.
     Whatever a starved destination leaves behind is printed verbatim (this is
     the honest-reporting evidence for the supervisor).
  3. slow upstream     -- the production fix measured against the immutable
     pre-fix module (git blob, sha256-pinned, a mismatch is a hard error):
     BEFORE, the 1.41 MB upload burns the budget and the second destination
     receives nothing; AFTER, both receive the shrunk JPEG on the same link.
  4. total outage      -- blackholed endpoint: False, bounded, one warn row in
     logs.jsonl naming step/title/imei and the locally kept file.
  5. healthy           -- no delay: True, one request per destination, no row.

Run standalone:  python lab/tests/test_telegram_receipt_path.py
Runner:          python lab/scripts/run_panel_tests.py test_telegram_receipt_path
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _telegram_fake  # noqa: E402  (shared harness; also isolates IRICA_DATA_DIR)

# Before app_paths is imported anywhere: app_paths.data_dir() caches its first
# answer forever, and a late override would send this run into the operator's
# live data directory.
DATA_DIR = _telegram_fake.temp_data_dir()

# Imported here, not inside the measurements: a cold `import requests` costs
# hundreds of ms and would land inside the first timed delivery.
import requests  # noqa: E402,F401
import gomrok  # noqa: E402
import telegram_notify  # noqa: E402

# The immutable pre-fix telegram_notify.py (v1.10.0 release commit) used for the
# BEFORE column of the slow-upstream scenario. The identity is checked, never
# skipped: a mismatch means the comparison is meaningless and must fail loudly.
PRE_FIX_COMMIT = "a5a714e"
PRE_FIX_BLOB = "1652a7675c61df9003748e9a1e94a5b66b4de479"
PRE_FIX_SHA256 = "85ab0deb4434f0968bb6da3d2f16fb211831414764adf8399e783179663c2ccc"

CHAT_A = "100000001"
CHAT_B = "100000002"
CHAT_IDS = (CHAT_A, CHAT_B)

STEP = "telegram: rasid (aks)"
TITLE = "T"
NATIONAL = "0012345678"
IMEI = "352506099000001"
CAPTION = "rasid-e pardakht"

#: The real receipt capture is ~1.41 MB; the harness builds exactly this many
#: bytes (random bytes behind a PNG signature -- upload payload, not an image).
RECEIPT_TOTAL_BYTES = 1_415_000

#: Bytes the fake reads per SLOW_READ_SLEEP_SEC (20 ms) chunk, i.e. the wire
#: rate: 2560 B / chunk == ~80 KB/s on this host, in the neighbourhood of the
#: ~57 KB/s the live log implies (1.41 MB was not enough for a 25 s budget).
#: Calibrated, not guessed: 1,415,000 B at this chunk size takes ~17.6 s, which
#: leaves the first destination enough of the 25 s budget to succeed and the
#: second one starved -- exactly the production shape, "deadline reached after
#: 0 attempts" for the destination that got nothing. The chunk size IS the rate
#: (the harness sleeps once per chunk), so a literal 24000 would be ~1.2 MB/s
#: -- 1.9 s for this file -- and would reproduce no starvation at all.
SLOW_LINK_CHUNK_BYTES = 2560

#: Where the shrunk JPEG has to land (telegram_notify.PHOTO_SHRINK_MIN_BYTES is
#: the 300 KB floor for shrinking at all; the real 1.41 MB receipt uploads
#: 194 KB, so anything in that range proves the shrink bought the margin).
SHRUNK_UPLOAD_MAX_BYTES = 400_000

#: Scenario-2 budget, patched onto gomrok for that test only.
TIGHT_BUDGET_SEC = 8.0

_PRE_FIX = {}


def _git_bytes(args):
    """stdout of a git command in ROOT, or None when git failed.

    A missing git binary is a hard error: the pre-fix comparison must never
    quietly degrade into "not compared".
    """
    try:
        done = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    except OSError as exc:
        raise AssertionError(f"cannot run {args[0]!r}: {exc}") from exc
    return done.stdout if done.returncode == 0 else None


def _pre_fix_source_bytes():
    """The pre-fix telegram_notify.py as bytes, from the pinned blob (or the commit)."""
    raw = _git_bytes(["git", "cat-file", "blob", PRE_FIX_BLOB])
    if not raw:
        raw = _git_bytes(["git", "show", f"{PRE_FIX_COMMIT}:telegram_notify.py"])
    if not raw:
        raise AssertionError(
            f"cannot read the pre-fix telegram_notify.py from git "
            f"(blob {PRE_FIX_BLOB}, commit {PRE_FIX_COMMIT})")
    return raw


def _load_pre_fix_module():
    """Import the sha256-verified pre-fix module under its own name (cached).

    Normalizes CRLF -> LF before hashing, so the check works whatever the
    checkout's line endings are. The .py is written into a temp directory:
    nothing of ours may land in the repository.
    """
    if _PRE_FIX:
        return _PRE_FIX["module"]
    content = _pre_fix_source_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(content).hexdigest()
    if digest != PRE_FIX_SHA256:
        raise AssertionError(
            f"pre-fix snapshot mismatch: {PRE_FIX_BLOB} hashes to {digest}, "
            f"expected {PRE_FIX_SHA256} -- the BEFORE column cannot be trusted")
    directory = tempfile.mkdtemp(prefix="irica_pre_fix_")
    path = os.path.join(directory, "telegram_notify_pre_fix.py")
    with open(path, "wb") as handle:
        handle.write(content)
    spec = importlib.util.spec_from_file_location("telegram_notify_pre_fix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PRE_FIX["module"] = module
    _PRE_FIX["dir"] = directory
    return module


def tearDownModule():
    if _PRE_FIX.get("dir"):
        shutil.rmtree(_PRE_FIX["dir"], ignore_errors=True)


def build_receipt_page(path, total_bytes=RECEIPT_TOTAL_BYTES, size=(2200, 4000)):
    """A deterministic DECODABLE stand-in for the full-page receipt capture.

    The harness's make_receipt_png is random bytes behind a PNG signature: real
    bytes on the wire, but no decoder accepts it, so _shrink_photo correctly
    falls back to uploading the original (what scenarios 1, 2, 4 and 5 use).
    The shrink scenario needs a file an image library can open, so this draws a
    page of text-like bars (deterministic, ~169 KB once re-encoded at
    PHOTO_MAX_EDGE) and pads it after IEND to the capture's total size: the wire
    sees the size, the shrink sees a page. The padding is inert -- IEND ends the
    stream -- and both facts are asserted here rather than assumed.
    """
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    y, row = 40, 0
    while y < height - 60:
        x = 60
        while x < width - 80:
            span = 70 + ((row * 37 + x * 13) % 160)
            draw.rectangle([x, y, min(x + span, width - 80), y + 6], fill=30)
            x += span + 40
        y += 72
        row += 1
    body = os.path.join(os.path.dirname(os.path.abspath(path)), "_receipt_body.png")
    image.save(body, format="PNG")
    with open(body, "rb") as handle:
        raw = handle.read()
    os.remove(body)
    with open(path, "wb") as handle:
        handle.write(raw)
        handle.write(b"\x00" * max(0, total_bytes - len(raw)))
    if os.path.getsize(path) != total_bytes:
        raise AssertionError(f"receipt fixture is not {total_bytes} bytes")
    with Image.open(path) as check:
        check.load()
        if check.size != size:
            raise AssertionError(f"receipt fixture does not decode: {check.size}")
    return path


@contextlib.contextmanager
def captured_stdout():
    """Collect what the robot prints while a delivery runs."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


def uploaded_bytes(calls, chat_id):
    """Largest photo byte count recorded for `chat_id` (0 = upload cut short).

    The fake logs an attempt at arrival, before the body is consumed, so a
    destination whose budget ran out mid-upload shows its attempt with
    photo_bytes == 0 and finished is None.
    """
    values = [call["photo_bytes"] for call in calls if call["chat_id"] == chat_id]
    return max(values or [0])


def server_seconds(calls, chat_id):
    """Longest served time for `chat_id`, or None when no request was served."""
    times = [call["finished"] - call["started"] for call in calls
             if call["chat_id"] == chat_id and call["finished"] is not None]
    return round(max(times), 2) if times else None


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_lf_text(path):
    """sha256 of a text file's content with CRLF normalized to LF."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest()


class ReceiptDeliveryTests(unittest.TestCase):
    """gomrok._telegram_deliver must reach every destination, or say so."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="irica_receipt_")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.logs_path = os.path.join(self.scratch, "logs.jsonl")
        self.delivery_path = os.path.join(self.scratch, "telegram_delivery.jsonl")
        self.config_path = os.path.join(self.scratch, "telegram_config.json")
        self.receipt = _telegram_fake.make_receipt_png(os.path.join(self.scratch, "receipt.png"))

        # gomrok logs into the live data directory by default; point it at the
        # throwaway one and put it back afterwards (prior art:
        # lab/scripts/live_check_telegram_timeout.py).
        self._original_logs_path = gomrok._LOGS_PATH
        gomrok._LOGS_PATH = self.logs_path
        self.addCleanup(setattr, gomrok, "_LOGS_PATH", self._original_logs_path)

        # telegram_notify.delivery_log_path() is read at call time; keep the
        # audit log inside the throwaway directory too.
        delivery_patch = patch.object(telegram_notify, "delivery_log_path",
                                      lambda: self.delivery_path)
        delivery_patch.start()
        self.addCleanup(delivery_patch.stop)

    # -- fixtures ----------------------------------------------------------
    def start_fake(self, **kwargs):
        """Start the fake and point the notifier's config at it."""
        fake = _telegram_fake.FakeTelegram(**kwargs).start()
        self.addCleanup(fake.stop)
        config = fake.write_config(self.config_path, CHAT_IDS)
        self.addCleanup(_telegram_fake.use_config(telegram_notify, config))
        return fake

    def set_photo_budget(self, seconds):
        original = gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC
        gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC = seconds
        self.addCleanup(setattr, gomrok, "TELEGRAM_PHOTO_TIMEOUT_SEC", original)

    def receipt_page(self):
        return build_receipt_page(os.path.join(self.scratch, "receipt_page.png"))

    def read_jsonl(self, path):
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def warn_rows(self):
        return [row for row in self.read_jsonl(self.logs_path)
                if row.get("status") == "warn"]

    def deliver_receipt(self, photo_path, **overrides):
        """gomrok._telegram_deliver exactly as the robot calls it."""
        kwargs = {"photo_path": photo_path, "caption": CAPTION, "step": STEP,
                  "title": TITLE, "national": NATIONAL, "imei": IMEI}
        kwargs.update(overrides)
        started = time.monotonic()
        with captured_stdout() as output:
            delivered = gomrok._telegram_deliver("photo", **kwargs)
        return delivered, output.getvalue(), time.monotonic() - started

    def report(self, label, fake, elapsed, expected_bytes=None):
        """One line per destination: attempts, uploaded bytes, served seconds.

        `received` is only claimed when a full upload size is known; "-" means
        the scenario asserts something else (an outage cannot be told from the
        byte count, because the fake consumes the body before it goes silent).
        """
        calls = fake.calls()
        counts = fake.counts()
        print(f"[{label}] elapsed={elapsed:.2f}s")
        for chat_id in CHAT_IDS:
            uploaded = uploaded_bytes(calls, chat_id)
            if expected_bytes is None:
                received = "-"
            else:
                received = "yes" if uploaded == expected_bytes else "no"
            print(f"[{label}] destination={chat_id} "
                  f"attempts={counts.get(chat_id, 0)} "
                  f"photo_bytes={uploaded} "
                  f"server_sec={server_seconds(calls, chat_id)} "
                  f"received={received}")

    # -- 1 ----------------------------------------------------------------
    def test_production_shape_receipt_reaches_both_destinations(self):
        """Two destinations, the first delayed 7.5 s: both get it inside 25 s."""
        fake = self.start_fake(delay={CHAT_A: 7.5})
        delivered, output, elapsed = self.deliver_receipt(self.receipt)
        self.report("1 production shape", fake, elapsed, RECEIPT_TOTAL_BYTES)
        self.assertIn("ersal shod", output)
        self.assertTrue(delivered, output)
        self.assertEqual(fake.counts(), {CHAT_A: 1, CHAT_B: 1})
        self.assertLess(elapsed, gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC)
        calls = fake.calls()
        for chat_id in CHAT_IDS:
            self.assertEqual(uploaded_bytes(calls, chat_id), RECEIPT_TOTAL_BYTES,
                             f"{chat_id} did not receive the whole receipt")

    # -- 2 ----------------------------------------------------------------
    def test_tight_budget_attempts_every_destination_and_stays_bounded(self):
        """An 8 s budget with a 7.5 s delay: nobody is skipped, nothing hangs."""
        self.set_photo_budget(TIGHT_BUDGET_SEC)
        fake = self.start_fake(delay={CHAT_A: 7.5})
        delivered, output, elapsed = self.deliver_receipt(self.receipt)
        self.report("2 tight budget", fake, elapsed, RECEIPT_TOTAL_BYTES)

        # The starved destination's footprint, verbatim: this is the evidence
        # for "is a lost destination silent?" -- reported, not judged.
        print("[2 tight budget] returned:", delivered)
        for line in output.splitlines():
            print("[2 tight budget] stdout:", line)
        records = self.read_jsonl(self.delivery_path)
        print(f"[2 tight budget] telegram_delivery.jsonl rows: {len(records)}")
        for record in records:
            print("[2 tight budget] telegram_delivery.jsonl:", json.dumps(record))
        print(f"[2 tight budget] gomrok logs.jsonl rows: "
              f"{len(self.read_jsonl(self.logs_path))}")

        for chat_id in CHAT_IDS:
            self.assertGreaterEqual(fake.counts().get(chat_id, 0), 1,
                                    f"{chat_id} was never attempted")
        self.assertLessEqual(elapsed, 10.0)
        calls = fake.calls()
        self.assertEqual(uploaded_bytes(calls, CHAT_B), RECEIPT_TOTAL_BYTES)
        self.assertLess(uploaded_bytes(calls, CHAT_A), RECEIPT_TOTAL_BYTES,
                        "the delayed destination should have missed the receipt")

    # -- 3 ----------------------------------------------------------------
    def test_slow_upstream_pre_fix_starves_second_destination_v2_does_not(self):
        """The production fix against the pinned pre-fix module, same link."""
        page = self.receipt_page()
        self.assertEqual(os.path.getsize(page), RECEIPT_TOTAL_BYTES)
        source_sha_before = _sha256_file(page)

        fake = self.start_fake(slow_read={CHAT_A: SLOW_LINK_CHUNK_BYTES,
                                          CHAT_B: SLOW_LINK_CHUNK_BYTES})

        # ---- BEFORE: the immutable pre-fix notifier, driven directly. -------
        pre_fix = _load_pre_fix_module()
        pre_fix._CFG_PATH = self.config_path
        started = time.monotonic()
        with captured_stdout() as pre_fix_output:
            pre_fix_results = pre_fix.send_photo(page, caption=CAPTION,
                                                 timeout=gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC)
        before_elapsed = time.monotonic() - started
        for line in pre_fix_output.getvalue().splitlines():
            print("[3 slow upstream] pre-fix stdout: %s" % line)
        before_calls = fake.calls()
        before_counts = fake.counts()
        fake.reset()
        self.assertFalse(os.path.exists(self.delivery_path),
                         "the pre-fix module has no delivery log at all")

        # ---- AFTER: the working tree, through the robot's own entry point. --
        delivered, output, after_elapsed = self.deliver_receipt(page)
        after_calls = fake.calls()
        after_counts = fake.counts()
        shrunk_bytes = self.expected_shrunk_bytes(page)

        self.print_table(before_elapsed, after_elapsed, before_calls, before_counts,
                         after_calls, after_counts, shrunk_bytes)

        # BEFORE: the first destination got the whole 1.41 MB, the second one
        # received nothing while the send reported success -- the silent loss
        # the operator saw.
        self.assertEqual(len(pre_fix_results), 1)
        self.assertEqual(uploaded_bytes(before_calls, CHAT_A), RECEIPT_TOTAL_BYTES)
        self.assertGreaterEqual(before_counts.get(CHAT_B, 0), 1)
        self.assertNotEqual(uploaded_bytes(before_calls, CHAT_B), RECEIPT_TOTAL_BYTES)

        # AFTER: same file, same link, same 25 s budget -- both destinations
        # get a payload well under the original size.
        self.assertTrue(delivered, output)
        self.assertEqual(after_counts, {CHAT_A: 1, CHAT_B: 1})
        self.assertNotIn("NAGHS", output)
        self.assertLessEqual(after_elapsed, gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC / 2)
        self.assertFalse(os.path.exists(self.delivery_path))
        for chat_id in CHAT_IDS:
            received = uploaded_bytes(after_calls, chat_id)
            self.assertEqual(received, shrunk_bytes, f"{chat_id} did not get the upload")
            self.assertLessEqual(received, SHRUNK_UPLOAD_MAX_BYTES)
        self.assertEqual(_sha256_file(page), source_sha_before,
                         "the receipt on disk must never be modified")

    def expected_shrunk_bytes(self, photo_path):
        """The size of the JPEG telegram_notify would upload for this photo."""
        upload_path, temp_path = telegram_notify._shrink_photo(photo_path)
        try:
            return os.path.getsize(upload_path)
        finally:
            telegram_notify._remove_file(temp_path)

    def print_table(self, before_elapsed, after_elapsed, before_calls, before_counts,
                    after_calls, after_counts, shrunk_bytes):
        """The before/after contrast in one place (bytes, seconds, attempts)."""
        print("--- scenario 3: 25.0 s budget, ~%d KB/s upstream "
              "(chunk %d B / 20 ms), receipt %d B ---"
              % (round(SLOW_LINK_CHUNK_BYTES / 0.032 / 1024), SLOW_LINK_CHUNK_BYTES,
                 RECEIPT_TOTAL_BYTES))
        print(f"{'phase':<8}{'destination':<14}{'attempts':>8}{'photo_bytes':>13}"
              f"{'seconds':>9}  received")
        self.print_rows("BEFORE", before_calls, before_counts, RECEIPT_TOTAL_BYTES,
                        before_elapsed)
        self.print_rows("AFTER", after_calls, after_counts, shrunk_bytes, after_elapsed)
        print(f"shrunk upload: {shrunk_bytes} bytes "
              f"(limit {SHRUNK_UPLOAD_MAX_BYTES})")

    def print_rows(self, phase, calls, counts, expected_bytes, elapsed):
        for chat_id in CHAT_IDS:
            uploaded = uploaded_bytes(calls, chat_id)
            received = "yes" if uploaded == expected_bytes else "no"
            print(f"{phase:<8}{chat_id:<14}{counts.get(chat_id, 0):>8}{uploaded:>13}"
                  f"{str(server_seconds(calls, chat_id)):>9}  {received}")
        print(f"{phase:<8}{'(total)':<14}{'':>8}{'':>13}{round(elapsed, 2):>9}")

    # -- 4 ----------------------------------------------------------------
    def test_total_outage_returns_false_and_leaves_a_warn_row(self):
        """Blackholed endpoint: bounded failure, and the file's location logged."""
        fake = self.start_fake(mode="blackhole")
        delivered, output, elapsed = self.deliver_receipt(self.receipt)
        self.report("4 total outage", fake, elapsed)

        self.assertFalse(delivered, output)
        self.assertLessEqual(elapsed, gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC + 5.0)
        rows = self.warn_rows()
        self.assertEqual(len(rows), 1, self.read_jsonl(self.logs_path))
        row = rows[0]
        print("[4 total outage] warn row:", json.dumps(row))
        self.assertEqual(row["step"], STEP)
        self.assertEqual(row["title"], TITLE)
        self.assertEqual(row["imei"], IMEI)
        self.assertIn("mahali zakhire shod", row["message"])
        self.assertIn("receipt.png", row["message"])
        for chat_id in CHAT_IDS:
            self.assertGreaterEqual(fake.counts().get(chat_id, 0), 1)

    # -- 5 ----------------------------------------------------------------
    def test_healthy_endpoint_delivers_once_per_destination_silently(self):
        fake = self.start_fake()
        delivered, output, elapsed = self.deliver_receipt(self.receipt)
        self.report("5 healthy", fake, elapsed, RECEIPT_TOTAL_BYTES)

        self.assertTrue(delivered, output)
        self.assertEqual(fake.counts(), {CHAT_A: 1, CHAT_B: 1})
        self.assertIn("ersal shod", output)
        self.assertNotIn("NAGHS", output)
        self.assertEqual(self.warn_rows(), [])
        self.assertEqual(self.read_jsonl(self.logs_path), [])
        self.assertFalse(os.path.exists(self.delivery_path))

    # -- identity of the BEFORE column ------------------------------------
    def test_pre_fix_snapshot_is_the_pinned_blob_not_the_fixed_module(self):
        """The comparison above is only meaningful if the snapshot really is pre-fix."""
        module = _load_pre_fix_module()
        self.assertEqual(_sha256_lf_text(module.__file__), PRE_FIX_SHA256)
        for missing in ("_shrink_photo", "_report_missed_destinations",
                        "delivery_log_path"):
            self.assertFalse(hasattr(module, missing),
                             f"the snapshot already carries {missing}; it is not pre-fix")
        # ... while the working tree does carry the fix.
        for present in ("_shrink_photo", "_report_missed_destinations",
                        "delivery_log_path"):
            self.assertTrue(hasattr(telegram_notify, present))


if __name__ == "__main__":
    unittest.main()
