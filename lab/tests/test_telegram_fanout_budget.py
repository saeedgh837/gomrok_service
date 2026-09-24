"""Fanout budget: the pre-fix silent loss, frozen, and the fixed arithmetic, pinned.

`telegram_notify._fanout` sends to every configured chat_id in turn. Before the
fix it handed ONE absolute deadline to every destination, so the first one could
spend the whole caller budget and the later ones raised
``TimeoutError("Telegram deadline reached after 0 attempts: ...")`` without ever
being contacted -- while the call still reported success, because one
destination had taken the message. That is how the starved receipt target went
unnoticed by ``gomrok._telegram_deliver``.

Two halves, both against the local fake only (never a real bot, never the live
data directory):

* :class:`PreFixSnapshotReproTests` characterises the bug on the IMMUTABLE
  pre-fix snapshot (``PRE_FIX_COMMIT`` / ``PRE_FIX_BLOB`` / ``PRE_FIX_SHA256``,
  loaded with ``git cat-file blob``). A hash mismatch is a hard error, never a
  skip; the same assertions stay green before and after the fix, because the
  snapshot never changes.
* :class:`WorkingTreeFanoutBudgetTests` pins the fixed per-destination budget
  arithmetic of the working tree. ``IRICA_TG_TARGET=pre_fix`` makes those bodies
  load the snapshot instead, which is how the discrimination is demonstrated:
  run once with the variable set (the starved destination shows up as
  ``... after 0 attempts: None``) and once without it (green).

Timings are wall-clock and the margins are what this harness allows, not what
the task text assumed. Measured on the pre-fix snapshot (6 consecutive runs,
2026-09-24): with the fake delaying the first destination 0.70 s and a caller
budget of 0.50 s, the first destination is delivered at ~0.72 s, the call
returns its result, and the second destination reports
``deadline reached after 0 attempts: None`` -- identical on every run
(``counts == {"A": 1}``, elapsed 0.73-0.80 s). The margins are ~0.2 s on both
sides, which is the most this harness leaves: a request may overrun the caller
deadline only through ``_post_confirmed``'s 1.0 s request-timeout floor, so the
window between "still confirmed" and "already timed out" is bounded by 1.0 s
minus the budget no matter how the fake is driven. The literal recipe of the
task text (a 500 on request #1, the ok on request #2 0.9 s later, budget 1.2 s)
was traced too: ``_post_confirmed`` caps a retry's backoff at the deadline, so
the retried attempt starts exactly ON the deadline and the whole destination
fails -- the call then RAISES instead of reporting success and the "starved
while reporting success" assertion cannot hold. The per-attempt delay a
retry-driven trace would need is not expressible with ``FakeTelegram(delay=...)``
(it is keyed by chat, not by attempt); the recipe below keeps the budget-eating
behaviour with honest, repeatable margins.
"""
import contextlib
import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for _path in (ROOT, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _telegram_fake import (FakeTelegram, make_receipt_png,  # noqa: E402
                            temp_data_dir, use_config)

# Before app_paths / telegram_notify are imported: app_paths.data_dir() caches
# its first result forever, and that cache must never reach the live directory.
DATA_DIR = temp_data_dir()

#: v1.10.0 release commit -- the last release before the fanout-budget fix.
PRE_FIX_COMMIT = "a5a714e"
#: `git rev-parse a5a714e:telegram_notify.py`.
PRE_FIX_BLOB = "1652a7675c61df9003748e9a1e94a5b66b4de479"
#: sha256 of that blob's LF-normalized content.
PRE_FIX_SHA256 = "85ab0deb4434f0968bb6da3d2f16fb211831414764adf8399e783179663c2ccc"

#: The full-page receipt capture the robot sends (~1.41 MB of PNG).
RECEIPT_BYTES = 1_415_000
#: Markers _fanout prints on a destination that did not take the message.
FAILURE_MARKERS = ("KHATA", "NA-MOTABAR")

#: "pre_fix" (or any of its spellings) runs the shared bodies on the snapshot.
TARGET = (os.environ.get("IRICA_TG_TARGET") or "working").strip().lower()
_PRE_FIX_TARGETS = ("pre_fix", "pre-fix", "prefix", "snapshot")

_PRE_FIX_MODULE = None


def _git_bytes(args):
    """Stdout of a git command, or a loud AssertionError (never a skip, never
    a silent fallback). A missing git is exactly as fatal as a bad hash."""
    try:
        completed = subprocess.run(args, cwd=ROOT, capture_output=True)
    except OSError as exc:
        raise AssertionError(
            "the pre-fix snapshot needs the git command line: %r" % (exc,)) from exc
    if completed.returncode != 0 or not completed.stdout:
        raise AssertionError(
            "could not read the pre-fix snapshot from this repository: %s -> %s"
            % (" ".join(args), completed.stderr.decode("utf-8", "replace").strip()))
    return completed.stdout


def pre_fix_source_bytes():
    """The pre-fix telegram_notify.py, LF-normalized, hash-checked."""
    try:
        raw = _git_bytes(["git", "cat-file", "blob", PRE_FIX_BLOB])
    except AssertionError:
        raw = _git_bytes(["git", "show", "%s:telegram_notify.py" % PRE_FIX_COMMIT])
    source = raw.replace(b"\r\n", b"\n")
    digest = hashlib.sha256(source).hexdigest()
    if digest != PRE_FIX_SHA256:
        raise AssertionError(
            "pre-fix snapshot mismatch (hard error, never a skip): sha256=%s expected=%s "
            "(commit %s, blob %s)"
            % (digest, PRE_FIX_SHA256, PRE_FIX_COMMIT, PRE_FIX_BLOB))
    return source


def pre_fix_module():
    """The frozen pre-fix module object, loaded once per process."""
    global _PRE_FIX_MODULE
    if _PRE_FIX_MODULE is not None:
        return _PRE_FIX_MODULE
    source = pre_fix_source_bytes()
    directory = tempfile.mkdtemp(prefix="irica_tg_prefix_")
    path = os.path.join(directory, "telegram_notify_pre_fix.py")
    with open(path, "wb") as handle:
        handle.write(source)
    spec = importlib.util.spec_from_file_location("telegram_notify_pre_fix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PRE_FIX_MODULE = module
    return module


def module_under_test():
    """The module the shared test bodies exercise (the TARGET_MODULE switch)."""
    if TARGET in _PRE_FIX_TARGETS:
        return pre_fix_module()
    import telegram_notify
    return telegram_notify


def delivered_chat_ids(fake, result):
    """Chat ids that had their send confirmed, derived from the fake's replies.

    FakeTelegram answers ``{"ok": true, "result": {"message_id": seen_index + 1}}``
    (the 0-based index of the request in its call log), so a returned body points
    back at the destination it came from.
    """
    calls = fake.calls()
    delivered = []
    for body in result or []:
        index = (body.get("result") or {}).get("message_id")
        if isinstance(index, int) and 1 <= index <= len(calls):
            delivered.append(calls[index - 1]["chat_id"])
    return delivered


def failure_lines(stdout, chat_id):
    """The stdout lines that name `chat_id` together with a failure marker."""
    return [line for line in stdout.splitlines()
            if str(chat_id) in line and any(m in line for m in FAILURE_MARKERS)]


class _FanoutTestCase(unittest.TestCase):
    """Shared scaffolding: fake server, temp config, captured stdout."""

    def _resolve_module(self):
        return module_under_test()

    def setUp(self):
        self.module = self._resolve_module()
        self.scratch = tempfile.mkdtemp(prefix="irica_tg_budget_")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.receipt = make_receipt_png(os.path.join(self.scratch, "receipt.png"),
                                        size_bytes=RECEIPT_BYTES)

    def fake_server(self, **kwargs):
        """A started fake on 127.0.0.1:<ephemeral>, stopped at test end."""
        fake = FakeTelegram(**kwargs).start()
        self.addCleanup(fake.stop)
        return fake

    def point_notifier_at(self, fake, chat_ids):
        """Temp config for the module under test, plus a temp delivery log."""
        cfg = fake.write_config(os.path.join(self.scratch, "telegram_config.json"),
                                chat_ids)
        self.addCleanup(use_config(self.module, cfg))
        # delivery_log_path() is read at call time; keep the JSONL record of a
        # missed destination inside this test's scratch directory.
        if hasattr(self.module, "delivery_log_path"):
            previous = self.module.delivery_log_path
            self.module.delivery_log_path = lambda: os.path.join(
                self.scratch, "telegram_delivery.jsonl")
            self.addCleanup(setattr, self.module, "delivery_log_path", previous)

    def send_receipt(self, **kwargs):
        """(result, captured stdout, exception) of one send_photo call."""
        buffer = io.StringIO()
        result = error = None
        try:
            with contextlib.redirect_stdout(buffer):
                result = self.module.send_photo(self.receipt, caption="receipt", **kwargs)
        except Exception as exc:        # noqa: BLE001 - asserted by the caller
            error = exc
        return result, buffer.getvalue(), error


class PreFixSnapshotReproTests(_FanoutTestCase):
    """Permanent characterisation of the silent partial loss (pre-fix snapshot).

    Always the immutable snapshot, whatever IRICA_TG_TARGET says: these are the
    assertions that must stay green before and after the production fix.
    """

    #: The first destination is slower than the whole caller budget. Its request
    #: takes ~0.72 s here (the fake's pause plus the 1.4 MB upload), which leaves
    #: room on both sides: the ~1.0 s `request_timeout` floor does not abort it,
    #: and it still lands after the deadline below.
    SLOW_DESTINATION_SEC = 0.70
    #: The caller's budget. `_post_confirmed` floors a request timeout at 1.0 s,
    #: so a request that started before the deadline can still be confirmed after
    #: it -- which is exactly how the first destination eats the second one's turn.
    CALLER_BUDGET_SEC = 0.50

    def _resolve_module(self):
        return pre_fix_module()

    def test_a_starved_second_destination_is_never_attempted_while_the_call_reports_success(self):
        """A eats the budget; B is never contacted; the call still looks fine."""
        fake = self.fake_server(delay={"A": self.SLOW_DESTINATION_SEC})
        self.point_notifier_at(fake, ["A", "B"])
        started = time.monotonic()
        result, stdout, error = self.send_receipt(timeout=self.CALLER_BUDGET_SEC)
        elapsed = time.monotonic() - started

        self.assertIsNone(error, "the call must report success, not raise: %r" % (error,))
        self.assertTrue(result, "the call must return the delivered destination: %r" % (result,))
        self.assertEqual(fake.counts().get("B", 0), 0,
                         "the starved destination must never be contacted: %s" % (fake.counts(),))
        self.assertEqual(fake.counts(), {"A": 1},
                         "exactly one destination, contacted once: %s (stdout=%r)"
                         % (fake.counts(), stdout))
        self.assertEqual([call["chat_id"] for call in fake.calls()], ["A"],
                         "only A reached the fake: %s" % (fake.calls(),))
        self.assertGreaterEqual(elapsed, self.CALLER_BUDGET_SEC,
                                "the budget was really spent: %.3f s" % elapsed)
        self.assertTrue(failure_lines(stdout, "B"),
                        "stdout must name B as a failed destination: %r" % (stdout,))
        # Visible on purpose: this map is the characterisation.
        print("pre-fix starvation: counts=%s elapsed=%.3fs" % (fake.counts(), elapsed))

    def test_a_healthy_pair_is_reached_once_each_on_this_harness(self):
        """Without the slow first destination the same harness reaches both."""
        fake = self.fake_server()
        self.point_notifier_at(fake, ["A", "B"])
        result, stdout, error = self.send_receipt(timeout=self.CALLER_BUDGET_SEC)
        self.assertIsNone(error, "a healthy pair must not raise: %r" % (error,))
        self.assertTrue(result, "both destinations took the message: %r" % (result,))
        self.assertEqual(fake.counts(), {"A": 1, "B": 1},
                         "one request per destination: %s (stdout=%r)" % (fake.counts(), stdout))
        self.assertEqual(failure_lines(stdout, "A") + failure_lines(stdout, "B"), [],
                         "a healthy pair prints no failure line: %r" % (stdout,))


class WorkingTreeFanoutBudgetTests(_FanoutTestCase):
    """The budget arithmetic of the working tree -- green once the fix has landed.

    Run with IRICA_TG_TARGET=pre_fix to see exactly these bodies fail on the
    frozen snapshot (the starved destination reports "after 0 attempts: None").
    """

    #: gomrok.py's TELEGRAM_PHOTO_TIMEOUT_SEC.
    PHOTO_BUDGET_SEC = 25.0
    #: The tight budget of the discrimination cases (TELEGRAM_TEXT_TIMEOUT_SEC is
    #: 10.0, so 8.0 is still a plausible caller budget).
    TIGHT_BUDGET_SEC = 8.0
    #: A destination that eats more than half of the 8 s budget.
    SLOW_DESTINATION_SEC = 7.5
    #: A destination slower than the WHOLE budget: it cannot be delivered and the
    #: destination after it must still be attempted.
    SLOWER_THAN_BUDGET_SEC = 12.0
    #: Ceiling for a call that spends its budget (a loose upper bound, not a target).
    ELAPSED_CEILING_SEC = 10.0

    @classmethod
    def setUpClass(cls):
        print("WorkingTreeFanoutBudgetTests requires the fanout-budget fix in telegram_notify.py")

    def test_both_destinations_receive_the_receipt_within_the_photo_budget(self):
        """The 1.4 MB receipt reaches BOTH destinations inside gomrok's 25 s."""
        fake = self.fake_server(delay={"A": self.SLOW_DESTINATION_SEC})
        self.point_notifier_at(fake, ["A", "B"])
        started = time.monotonic()
        result, stdout, error = self.send_receipt(timeout=self.PHOTO_BUDGET_SEC)
        elapsed = time.monotonic() - started

        self.assertIsNone(error, "both destinations must fit the photo budget: %r" % (error,))
        self.assertTrue(result, "at least one destination took the message: %r" % (result,))
        self.assertEqual(fake.counts(), {"A": 1, "B": 1},
                         "each destination gets its own sendPhoto: %s (stdout=%r)"
                         % (fake.counts(), stdout))
        calls = fake.calls()
        self.assertEqual([call["method"] for call in calls], ["sendPhoto", "sendPhoto"],
                         "both destinations got a photo, not a text fallback: %s" % (calls,))
        self.assertEqual([call["chat_id"] for call in calls], ["A", "B"],
                         "in configured order: %s" % (calls,))
        sizes = [call["photo_bytes"] for call in calls]
        self.assertTrue(all(size > 0 for size in sizes),
                        "each upload really carried a photo: %s" % (sizes,))
        self.assertEqual(sizes[0], sizes[1],
                         "both destinations carried the same payload: %s" % (sizes,))
        self.assertLess(elapsed, self.PHOTO_BUDGET_SEC,
                        "the whole fanout stayed inside the photo budget: %.3f s" % elapsed)

    def test_every_configured_destination_is_at_least_attempted_with_a_tight_budget(self):
        """No destination is skipped without a visible line, however tight the budget."""
        fake = self.fake_server(delay={"A": self.SLOW_DESTINATION_SEC})
        self.point_notifier_at(fake, ["A", "B"])
        started = time.monotonic()
        result, stdout, error = self.send_receipt(timeout=self.TIGHT_BUDGET_SEC)
        elapsed = time.monotonic() - started

        self.assertIsNone(error, "the healthy destination must still be delivered: %r" % (error,))
        self.assertLessEqual(elapsed, self.ELAPSED_CEILING_SEC,
                             "the call must not overrun its budget: %.3f s" % elapsed)
        for chat_id in ("A", "B"):
            self.assertGreaterEqual(
                fake.counts().get(chat_id, 0), 1,
                "%s was never attempted: %s (stdout=%r)" % (chat_id, fake.counts(), stdout))
        delivered = delivered_chat_ids(fake, result)
        for chat_id in ("A", "B"):
            if chat_id not in delivered:
                self.assertTrue(
                    failure_lines(stdout, chat_id),
                    "%s received nothing and no line names it: %r" % (chat_id, stdout))

    def test_one_destination_still_uses_the_whole_budget(self):
        """A lone destination is not divided among the destinations that were left."""
        fake = self.fake_server(delay={"A": self.SLOW_DESTINATION_SEC})
        self.point_notifier_at(fake, ["A"])
        result, stdout, error = self.send_receipt(timeout=self.TIGHT_BUDGET_SEC)
        self.assertIsNone(error,
                          "the only destination may spend the whole budget: %r" % (error,))
        self.assertTrue(result, "the only destination took the message: %r" % (result,))
        self.assertEqual(fake.counts(), {"A": 1},
                         "delivered once, no retry needed: %s (stdout=%r)"
                         % (fake.counts(), stdout))

    def test_a_destination_slower_than_the_whole_budget_does_not_starve_the_rest(self):
        """Discrimination case: pre-fix, B is never attempted and the call raises.

        On the frozen snapshot the failure text carries the starved
        destination's ``deadline reached after 0 attempts: None``.
        """
        fake = self.fake_server(delay={"A": self.SLOWER_THAN_BUDGET_SEC})
        self.point_notifier_at(fake, ["A", "B"])
        started = time.monotonic()
        result, stdout, error = self.send_receipt(timeout=self.TIGHT_BUDGET_SEC)
        elapsed = time.monotonic() - started

        if error is not None:
            self.fail("the fanout raised although the healthy destination could still take "
                      "the message: %r\nstdout was:\n%s" % (error, stdout))
        self.assertLessEqual(elapsed, self.ELAPSED_CEILING_SEC,
                             "the call must not overrun its budget: %.3f s" % elapsed)
        self.assertGreaterEqual(
            fake.counts().get("B", 0), 1,
            "the second destination must be attempted even though the first cannot fit "
            "in the whole budget: %s\nstdout was:\n%s" % (fake.counts(), stdout))
        delivered = delivered_chat_ids(fake, result)
        self.assertIn("B", delivered,
                      "the healthy destination received the message: %s" % (delivered,))
        if "A" not in delivered:
            self.assertTrue(failure_lines(stdout, "A"),
                            "A received nothing and no line names it: %r" % (stdout,))

    def test_deadlines_handed_to_destinations_never_exceed_the_callers_budget(self):
        """The slice per destination shrinks with what is left, never grows."""
        timeout = 30.0
        recorded = []

        def recorder(method, cfg, data, files_factory=None, attempts=4, deadline=None):
            recorded.append((str(data.get("chat_id")), deadline))
            return {"ok": True, "result": {"message_id": len(recorded)}}

        original = getattr(self.module, "_post_confirmed", None)
        self.assertIsNotNone(original, "the module must expose _post_confirmed")
        self.module._post_confirmed = recorder
        self.addCleanup(setattr, self.module, "_post_confirmed", original)

        cases = ((["A"], None, ["A"]), (["A", "B"], None, ["A", "B"]),
                 (["A", "B"], ["C"], ["A", "B", "C"]))
        for configured, extra_chat_ids, expected in cases:
            with self.subTest(configured=configured, extra_chat_ids=extra_chat_ids,
                              destinations=expected):
                recorded.clear()
                fake = self.fake_server()       # only supplies an api_base
                self.point_notifier_at(fake, configured)
                started = time.monotonic()
                self.module.send_photo(self.receipt, caption="receipt", timeout=timeout,
                                       extra_chat_ids=extra_chat_ids)
                self.assertEqual([chat_id for chat_id, _d in recorded], expected,
                                 "every configured destination is called, in order: %s"
                                 % (recorded,))
                deadlines = [deadline for _chat_id, deadline in recorded]
                self.assertTrue(all(deadline is not None for deadline in deadlines),
                                "a caller budget must reach every destination: %s" % (deadlines,))
                # The module stamps its own deadline after it has read the
                # config, so allow for that offset (it is a file read, not slack).
                ceiling = started + timeout + 1.0
                self.assertLessEqual(deadlines[0], ceiling,
                                     "the first slice is not larger than the whole budget: "
                                     "%.3f > %.3f" % (deadlines[0], ceiling))
                self.assertLessEqual(max(deadlines), ceiling,
                                     "no destination may be given more than the budget: "
                                     "%.3f > %.3f" % (max(deadlines), ceiling))
                if len(expected) == 1:
                    self.assertGreaterEqual(deadlines[0], started + timeout - 1.0,
                                            "a lone destination keeps the whole budget, it is "
                                            "not divided: %.3f" % deadlines[0])
                else:
                    self.assertLess(deadlines[0], started + timeout,
                                    "the first slice is a share of the budget, not all of it: "
                                    "%.3f" % deadlines[0])
                for earlier, later in zip(deadlines, deadlines[1:]):
                    self.assertLess(earlier, later,
                                    "each destination gets a later deadline than the one "
                                    "before it: %s" % (deadlines,))


if __name__ == "__main__":
    unittest.main()
