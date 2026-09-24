# -*- coding: utf-8 -*-
"""v1.9.1 — robot-side result-screenshot capture (estelam_shot + both robots).

Run:
    python lab/tests/test_v1_9_1_capture.py

At the verdict moment each robot asks `estelam_shot.capture_result` for one
picture of the result: first the result element itself, then the whole page
(CDP), and "" when neither works. It never raises — a failed capture must not
kill an inquiry that already produced a verdict. The robots write the path
into estelam_store.json (hamta_screenshot / gomrok_screenshot) and attach the
picture to the Telegram result message.

`capture_result` returns `(path, exact)`. A full-page capture is panel-only
evidence and must never leave the machine: it still shows the traveler's
national ID and birth date in the form above the verdict, and the Telegram
send fans out to every configured chat. Only `exact` captures are attached.

Everything here runs against small fakes: no browser, no network. Every class
that can reach the data directory isolates it (temp dir + IRICA_DATA_DIR +
explicitly patched path globals), so the suite never writes into the
operator's live screenshots folder or logs.
"""
import base64
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import app_paths  # noqa: E402
import estelam_shot  # noqa: E402
import panel  # noqa: E402

# Repeating fake serial — never a real device identifier.
SERIAL = "350358333315545"


# --- fake PNG (no PIL needed) ------------------------------------------
def _tiny_png():
    """A 1x1 8-bit RGB PNG, built with struct/zlib."""
    raw = b"\x00\xff\x00\x00"   # filter byte + 1 RGB pixel

    def chunk(ctype, data):
        return (struct.pack(">I", len(data)) + ctype + data +
                struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG_BYTES = _tiny_png()

#: Constant box returned by the fake driver's execute_script, so _wait_settled
#: sees the same value twice in a row and returns instead of polling for 2 s.
FAKE_BOX = [120, 40, 320, 90]


# --- fake selenium -----------------------------------------------------
class _FakeElement:
    """Stand-in for a Selenium WebElement: just what estelam_shot reads."""

    def __init__(self, png=PNG_BYTES, text="result", size=None):
        self._png = png
        self.text = text
        self.size = {"width": 120, "height": 40} if size is None else size

    @property
    def screenshot_as_png(self):
        return self._png

    def is_displayed(self):
        """False, so estelam_hamta._is_form_modal sees no inquiry form.

        In this fake there is no form on screen — only the verdict, which is
        the state the capture runs in.
        """
        return False


class _FakeDriver:
    """Fake Selenium driver.

    mode:
      "element" -> find_elements returns one rendered, non-empty element.
      "page"    -> find_elements returns nothing, so only the CDP page
                   capture can succeed.
      "dead"    -> every call raises, like a lost browser session.
    """

    def __init__(self, mode="element", png=PNG_BYTES, elements=None):
        self.mode = mode
        self.png = png
        self.scripts = []
        self.cdp = []
        self.shots = []
        self.searches = []
        self.quit_called = False
        self.element = _FakeElement(png)
        if elements is None:
            elements = [self.element] if mode == "element" else []
        self.elements = elements

    def _boom(self, what):
        raise RuntimeError(f"{self.mode} driver: {what} failed")

    def find_elements(self, by, selector):
        self.searches.append((by, selector))
        if self.mode == "dead":
            self._boom("find_elements")
        return list(self.elements)

    def find_element(self, by, selector):
        self.searches.append((by, selector))
        if self.mode == "dead":
            self._boom("find_element")
        return self.element

    def execute_script(self, script, *args):
        if self.mode == "dead":
            self._boom("execute_script")
        self.scripts.append(script)
        return list(FAKE_BOX)

    def execute_cdp_cmd(self, cmd, params):
        if self.mode == "dead":
            self._boom("execute_cdp_cmd")
        self.cdp.append(cmd)
        return {"data": base64.b64encode(self.png).decode("ascii")}

    def save_screenshot(self, path):
        if self.mode == "dead":
            self._boom("save_screenshot")
        self.shots.append(path)
        with open(path, "wb") as handle:
            handle.write(self.png)
        return True

    def quit(self):
        self.quit_called = True


# --- stubs -------------------------------------------------------------
class _Stub:
    """Fake telegram_notify module (records sends, can be made to raise)."""

    def __init__(self, raise_=False):
        self.raise_ = raise_
        self.photos = []
        self.captions = []
        self.messages = []

    def send_photo(self, path, caption="", **kw):
        if self.raise_:
            raise RuntimeError("stub: send_photo failed")
        self.photos.append(path)
        self.captions.append(caption)
        return True

    def send_message(self, text, **kw):
        if self.raise_:
            raise RuntimeError("stub: send_message failed")
        self.messages.append(text)
        return True


class _CaptureCounter:
    """Fake estelam_shot module: counts capture_result/attach calls.

    The robots `import estelam_shot` inside the function, so the stub has to
    sit in sys.modules (see `_stub_modules`). `returns` is the (path, exact)
    tuple capture_result must hand back.
    """

    def __init__(self, returns=("ok.png", True)):
        self.returns = returns
        self.calls = []
        self.photos = []
        self.captions = []

    def capture_result(self, driver, service, device_id, serial,
                       element_selector=None, **kwargs):
        self.calls.append((service, str(device_id), str(serial),
                           element_selector))
        return self.returns

    def attach(self, photo_path, caption):
        self.photos.append(photo_path)
        self.captions.append(caption)
        return True


class _FailingAttach(_CaptureCounter):
    """estelam_shot whose attach returns False.

    That is the real "the photo did not go out" path: estelam_shot.attach
    swallows its own failures and returns False, so it never raises into the
    robot. The robot must still send the text.
    """

    def attach(self, photo_path, caption):
        return False


def _stub_modules(**mods):
    """patch.dict(sys.modules, ...) — the robots `import estelam_shot` /
    `import telegram_notify` inside their functions."""
    clean = {k: v for k, v in mods.items() if v is not None}
    return patch.dict(sys.modules, clean)


class _DataDirIsolation(unittest.TestCase):
    """Point every data path at a throwaway directory.

    patch.dict(IRICA_DATA_DIR) alone is not enough: panel and estelam_hamta
    compute their store/log paths at import time from app_paths, so those
    globals are patched explicitly here. Without this a capture test writes
    into the operator's live data directory (screenshots, estelam_logs.jsonl,
    hamta_logs.jsonl).
    """

    def setUp(self):
        super().setUp()
        self._data_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._data_tmp.cleanup)      # runs last
        self.data_tmp = self._data_tmp.name
        self._env = patch.dict(os.environ, {"IRICA_DATA_DIR": self.data_tmp})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(app_paths.reset_cache)
        app_paths.reset_cache()
        import estelam_hamta
        for patcher in (
            patch.object(panel, "ESTELAM_PATH",
                         os.path.join(self.data_tmp, "estelam_store.json")),
            patch.object(panel, "ESTELAM_LOGS_PATH",
                         os.path.join(self.data_tmp, "estelam_logs.jsonl")),
            patch.object(estelam_hamta, "_LOG_PATH",
                         os.path.join(self.data_tmp, "hamta_logs.jsonl")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def pngs_written(self):
        """Every .png under the isolated data directory (must stay empty)."""
        found = []
        for base, _dirs, files in os.walk(self.data_tmp):
            found += [os.path.join(base, name) for name in files
                      if name.endswith(".png")]
        return found


# =====================================================================
# estelam_shot.shot_path — one stable slot per (service, device)
# =====================================================================
class ShotPathTests(_DataDirIsolation):
    def test_stable_for_same_inputs(self):
        a = estelam_shot.shot_path("hamta", "dev7", SERIAL)
        b = estelam_shot.shot_path("hamta", "dev7", SERIAL)
        self.assertEqual(a, b)

    def test_differs_per_service(self):
        h = estelam_shot.shot_path("hamta", "dev7", SERIAL)
        g = estelam_shot.shot_path("gomrok", "dev7", SERIAL)
        self.assertNotEqual(h, g)
        self.assertIn("hamta", os.path.basename(h))
        self.assertIn("gomrok", os.path.basename(g))

    def test_lands_in_data_screenshots_subdir(self):
        p = estelam_shot.shot_path("hamta", "dev7", SERIAL)
        base = os.path.basename(p)
        self.assertEqual(os.path.dirname(p),
                         app_paths.data_subdir("screenshots"))
        self.assertTrue(base.startswith("estelam_hamta_"))
        self.assertTrue(base.endswith(".png"))
        self.assertIn("dev7", base)
        self.assertIn(SERIAL, base)

    def test_unknown_service_is_gomrok(self):
        p = estelam_shot.shot_path("bogus-service", "dev7", SERIAL)
        self.assertIn("estelam_gomrok_", p)

    def test_hostile_chars_cannot_escape_the_directory(self):
        """Serial and device id come from the site: `..` and separators must
        not be able to walk out of the screenshots directory (path traversal)."""
        hostile = "../../../../../etc/passwd"
        p = estelam_shot.shot_path("hamta", hostile, hostile)
        base = os.path.basename(p)
        self.assertNotIn("..", base)
        self.assertNotIn("/", base)
        self.assertNotIn("\\", base)
        self.assertEqual(os.path.dirname(p),
                         app_paths.data_subdir("screenshots"))
        # the final path is still inside the data directory
        self.assertTrue(os.path.normpath(p).startswith(
            os.path.normpath(app_paths.data_dir())))

    def test_empty_device_id_has_no_slot(self):
        """No device id -> no slot at all, and capture_result does nothing.

        A CLI single-serial run has no panel row to show a picture in, and
        letting every such run share one fallback slot would make two
        concurrent runs overwrite each other's evidence.
        """
        self.assertEqual(estelam_shot.shot_path("hamta", "", SERIAL), "")
        self.assertEqual(estelam_shot.shot_path("hamta", "   ", SERIAL), "")

        class Untouched:
            """Any driver access would mean the empty slot was not honoured."""
            def __getattr__(self, name):
                raise AssertionError(f"driver.{name} called with no slot")

        self.assertEqual(
            estelam_shot.capture_result(Untouched(), "hamta", "", SERIAL),
            ("", False))
        self.assertEqual(self.pngs_written(), [])

    def test_long_serial_is_truncated(self):
        p = estelam_shot.shot_path("hamta", "dev1", "9" * 200)
        self.assertLessEqual(len(os.path.basename(p)), 200)

    def test_missing_serial_still_gets_a_slot(self):
        p = estelam_shot.shot_path("hamta", "dev7", "")
        self.assertTrue(os.path.basename(p).endswith("_na.png"))

    def test_safe_part_blank_input_is_empty(self):
        self.assertEqual(estelam_shot._safe_part(""), "")
        self.assertEqual(estelam_shot._safe_part("   "), "")
        self.assertEqual(estelam_shot._safe_part(None), "")

    def test_safe_part_truncates_and_strips_underscores(self):
        self.assertEqual(estelam_shot._safe_part("a b"), "a_b")
        self.assertEqual(len(estelam_shot._safe_part("9" * 200)), 40)
        self.assertEqual(estelam_shot._safe_part("..7//"), "7")


# =====================================================================
# estelam_shot.capture_element — the element's own PNG is written
# =====================================================================
class CaptureElementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "el.png")

    def test_writes_real_png_bytes(self):
        d = _FakeDriver("element")
        self.assertTrue(estelam_shot.capture_element(d, "div.x", self.path))
        with open(self.path, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(data), 30)
        self.assertEqual(data, PNG_BYTES)

    def test_scrolled_into_view_before_shot(self):
        d = _FakeDriver("element")
        estelam_shot.capture_element(d, "div.x", self.path)
        self.assertTrue(any("scrollIntoView" in s for s in d.scripts))

    def test_missing_element_returns_false_no_raise(self):
        d = _FakeDriver("page")   # find_elements matches nothing
        self.assertFalse(estelam_shot.capture_element(d, "div.x", self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_blank_text_element_is_skipped(self):
        """The gomrok feedback node stays in the DOM but empty between
        attempts; a picture of it would be filed as the verdict's evidence."""
        blank = _FakeElement(text="   ")
        d = _FakeDriver("element", elements=[blank])
        self.assertFalse(estelam_shot.capture_element(d, "div.x", self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_zero_size_element_is_skipped(self):
        zero = _FakeElement(size={"width": 0, "height": 0})
        d = _FakeDriver("element", elements=[zero])
        self.assertFalse(estelam_shot.capture_element(d, "div.x", self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_unmatched_selector_returns_false_no_raise(self):
        d = _FakeDriver("element", elements=[])
        self.assertFalse(estelam_shot.capture_element(d, "div.nope", self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_dead_driver_returns_false_no_raise(self):
        self.assertFalse(estelam_shot.capture_element(
            _FakeDriver("dead"), "div.x", self.path))


# =====================================================================
# estelam_shot.capture_page — CDP path + save_screenshot fallback
# =====================================================================
class CapturePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "page.png")

    def test_cdp_path_writes_png(self):
        d = _FakeDriver("page")
        self.assertTrue(estelam_shot.capture_page(d, self.path))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), PNG_BYTES)

    def test_falls_back_to_save_screenshot(self):
        class NoCdp(_FakeDriver):
            def execute_cdp_cmd(self, cmd, params):
                raise RuntimeError("no CDP")
        d = NoCdp("page")
        self.assertTrue(estelam_shot.capture_page(d, self.path))
        self.assertEqual(d.shots, [self.path + ".tmp"])

    def test_save_screenshot_without_png_bytes_fails_closed(self):
        """A True return is not proof: the file itself is read back."""
        class LyingDriver(_FakeDriver):
            def execute_cdp_cmd(self, cmd, params):
                raise RuntimeError("no CDP")

            def save_screenshot(self, path):
                with open(path, "wb") as handle:
                    handle.write(b"not a png")
                return True
        self.assertFalse(estelam_shot.capture_page(LyingDriver("page"),
                                                   self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_total_failure_returns_false_no_raise(self):
        self.assertFalse(estelam_shot.capture_page(_FakeDriver("dead"),
                                                   self.path))


# =====================================================================
# estelam_shot.capture_result — element -> page -> "", never raises
# =====================================================================
class CaptureResultTests(_DataDirIsolation):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.good = os.path.join(self.tmp.name, "good.png")

    def test_element_success_returns_path_and_exact_true(self):
        d = _FakeDriver("element")
        with patch.object(estelam_shot, "shot_path", return_value=self.good):
            p, exact = estelam_shot.capture_result(d, "hamta", "dev1", SERIAL,
                                                   element_selector="div.modal")
        self.assertEqual(p, self.good)
        self.assertTrue(exact)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), PNG_BYTES)

    def test_falls_back_to_page_with_exact_false(self):
        d = _FakeDriver("page")   # no usable element, CDP works
        with patch.object(estelam_shot, "shot_path", return_value=self.good):
            p, exact = estelam_shot.capture_result(d, "hamta", "dev1", SERIAL,
                                                   element_selector="div.modal")
        self.assertEqual(p, self.good)
        self.assertFalse(exact, "a page capture must not claim to be exact")
        self.assertTrue(os.path.exists(p))
        self.assertGreater(os.path.getsize(p), 30)

    def test_no_selector_goes_straight_to_page(self):
        """element_selector=None -> no element search at all, page only."""
        d = _FakeDriver("page")
        with patch.object(estelam_shot, "shot_path", return_value=self.good), \
                patch.object(_FakeDriver, "find_elements",
                             side_effect=AssertionError("find_elements called")):
            p, exact = estelam_shot.capture_result(d, "hamta", "dev1", SERIAL)
        self.assertEqual(p, self.good)
        self.assertFalse(exact)

    def test_returns_empty_when_everything_fails(self):
        with patch.object(estelam_shot, "shot_path", return_value=self.good):
            self.assertEqual(
                estelam_shot.capture_result(_FakeDriver("dead"), "hamta",
                                            "dev1", SERIAL,
                                            element_selector="div.modal"),
                ("", False))
        self.assertFalse(os.path.exists(self.good))

    def test_never_raises_is_the_contract(self):
        """No combination of failures may raise."""
        with patch.object(estelam_shot, "shot_path",
                          side_effect=OSError("disk full")):
            self.assertEqual(
                estelam_shot.capture_result(_FakeDriver("dead"), "hamta",
                                            "dev1", SERIAL,
                                            element_selector="div"),
                ("", False))

    def test_path_is_derived_under_the_screenshots_dir(self):
        """The derived path sits under DATA_DIR/screenshots and carries
        service, device and serial in its name — inside the isolated data
        directory, never the operator's live screenshots folder."""
        p = estelam_shot.shot_path("hamta", "dev-test-shot", SERIAL)
        self.assertEqual(os.path.dirname(p),
                         app_paths.data_subdir("screenshots"))
        self.assertTrue(os.path.dirname(p).startswith(self.data_tmp))
        self.assertIn("estelam_hamta_", os.path.basename(p))
        self.assertIn("dev-test-shot", os.path.basename(p))
        # nothing has been written yet
        self.assertFalse(os.path.exists(p))
        # capture_result uses that very path when it is not overridden
        self.assertEqual(
            estelam_shot.capture_result(_FakeDriver("element"), "hamta",
                                        "dev-test-shot", SERIAL,
                                        element_selector="div.modal"),
            (p, True))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), PNG_BYTES)
        self.assertEqual(self.pngs_written(), [p])


# =====================================================================
# estelam_shot.attach — best effort, never raises
# =====================================================================
class AttachTests(unittest.TestCase):
    def test_returns_false_when_send_fails(self):
        tg = _Stub(raise_=True)
        with _stub_modules(telegram_notify=tg):
            self.assertFalse(estelam_shot.attach("x.png", "caption"))

    def test_returns_true_when_send_works(self):
        tg = _Stub()
        with _stub_modules(telegram_notify=tg):
            self.assertTrue(estelam_shot.attach("x.png", "caption"))
        self.assertEqual(tg.photos, ["x.png"])
        self.assertEqual(tg.captions, ["caption"])

    def test_dead_stub_does_not_raise(self):
        class NoSendPhoto:
            pass
        with _stub_modules(telegram_notify=NoSendPhoto()):
            self.assertFalse(estelam_shot.attach("x.png", "caption"))


# =====================================================================
# estelam_hamta._telegram_result — photo, then text fallback
# =====================================================================
class HamtaTelegramResultTests(_DataDirIsolation):
    def setUp(self):
        super().setUp()
        import estelam_hamta as H
        self.H = H
        self.tg = _Stub()
        self.count = _CaptureCounter()
        self.mods = _stub_modules(telegram_notify=self.tg,
                                  estelam_shot=self.count)
        self.mods.start()
        self.addCleanup(self.mods.stop)
        self._orig_tele = H._telegram
        H._telegram = self.tg.send_message

    def tearDown(self):
        self.H._telegram = self._orig_tele

    def test_disabled_sends_nothing(self):
        self.H._telegram_result(SERIAL, "good", "result text",
                                enabled=False, shot_path="ok.png",
                                shot_exact=True)
        self.assertEqual(self.tg.photos, [])
        self.assertEqual(self.tg.messages, [])
        self.assertEqual(self.count.photos, [])
        self.assertEqual(self.tg.captions, [])

    def test_enabled_with_exact_shot_sends_exactly_one_photo(self):
        self.H._telegram_result(SERIAL, "good", "result text",
                                enabled=True, shot_path="ok.png",
                                shot_exact=True)
        # one send: the photo. no plain text message.
        self.assertEqual(self.count.photos, ["ok.png"])
        self.assertEqual(self.tg.messages, [])
        self.assertIn("نتیجه استعلام", self.count.captions[0])
        self.assertIn(SERIAL, self.count.captions[0])
        # the real telegram_notify was never called (estelam_shot is stubbed)
        self.assertEqual(self.tg.photos, [])

    def test_enabled_without_shot_falls_back_to_text(self):
        self.H._telegram_result(SERIAL, "good", "result text",
                                enabled=True, shot_path="")
        self.assertEqual(self.count.photos, [])
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("نتیجه استعلام", self.tg.messages[0])
        self.assertIn(SERIAL, self.tg.messages[0])

    def test_page_capture_is_never_attached(self):
        """Privacy rule: a full-page capture shows the form above the verdict
        (national ID, birth date) and goes to every configured chat, so only
        an exact capture may be attached. Text only here."""
        self.H._telegram_result(SERIAL, "good", "result text",
                                enabled=True, shot_path="page.png",
                                shot_exact=False)
        self.assertEqual(self.count.photos, [])
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("نتیجه استعلام", self.tg.messages[0])
        self.assertIn(SERIAL, self.tg.messages[0])

    def test_failed_photo_still_sends_the_text(self):
        """A failed upload must not cost the operator the verdict.

        estelam_shot.attach swallows its own failures and returns False, so
        this is the real "the photo did not go out" path."""
        failing = _FailingAttach()
        with _stub_modules(telegram_notify=self.tg, estelam_shot=failing):
            self.H._telegram_result(SERIAL, "good", "result text",
                                    enabled=True, shot_path="ok.png",
                                    shot_exact=True)
        self.assertEqual(failing.photos, [])   # attach was tried, no photo sent
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn(SERIAL, self.tg.messages[0])

    def test_no_network_needed_at_import(self):
        """estelam_shot must not need the network at import time (no socket,
        no selenium import)."""
        with open(os.path.join(_ROOT, "estelam_shot.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("requests.", src)
        self.assertNotIn("urllib", src)
        self.assertNotIn("import socket", src)


# =====================================================================
# estelam_hamta.inquire — capture only at the verdict
# =====================================================================
class HamtaInquireTerminalTests(_DataDirIsolation):
    """Core contract: capture_result runs after a terminal verdict and after
    the retry loop gives up — never inside a captcha retry. Fake driver plus
    a counting estelam_shot."""

    def setUp(self):
        super().setUp()
        import estelam_hamta as H
        self.H = H
        self.count = _CaptureCounter(returns=("shot.png", True))
        self.tg = _Stub()
        self.mods = _stub_modules(telegram_notify=self.tg,
                                  estelam_shot=self.count)
        self.mods.start()
        self.addCleanup(self.mods.stop)

        # hamta helpers that would need a real browser/selenium
        self._saved = {
            "open_page_healthy": H.open_page_healthy,
            "open_modal": H.open_modal,
            "_set_input": H._set_input,
            "_field_value": H._field_value,
            "grab_captcha_pil": H.grab_captcha_pil,
            "refresh_captcha": H.refresh_captcha,
            "dismiss_swal": H.dismiss_swal,
            "submit_and_read": H.submit_and_read,
            "REFRESH_DELAY": H.REFRESH_DELAY,
            "MAX_CAPTCHA_TRIES": H.MAX_CAPTCHA_TRIES,
        }
        H.open_page_healthy = lambda d, w, *a, **k: True
        H.open_modal = lambda d, w: True
        H._set_input = lambda d, s, v: None
        # captcha verification passes, so submit_and_read is reached
        H._field_value = lambda d, s: "1234"
        H.grab_captcha_pil = lambda d, el: "img"
        H.refresh_captcha = lambda d: None
        H.dismiss_swal = lambda d, *a, **k: True
        H.REFRESH_DELAY = 0
        H.MAX_CAPTCHA_TRIES = 12

        class FakeSolver:
            def read_pil(self, img):
                return {"number": 1234, "confidence": 0.9, "text": "1234"}
        self.solver = FakeSolver()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(self.H, name, value)

    def _driver(self):
        return _FakeDriver("element")

    def test_capture_called_once_on_terminal_verdict(self):
        self.H.submit_and_read = lambda d, w: ("good", "result text")
        out = self.H.inquire(self._driver(), None, self.solver, SERIAL,
                             telegram=True, device_id="dev1")
        self.assertEqual(out["status"], "good")
        self.assertEqual(out["shot"], "shot.png")
        self.assertEqual(len(self.count.calls), 1, "exactly one capture")
        self.assertEqual(self.count.calls[0][0], "hamta")
        self.assertEqual(self.count.calls[0][1], "dev1")
        self.assertEqual(self.count.calls[0][2], SERIAL)
        self.assertEqual(self.count.calls[0][3], self.H.SEL_MODAL)
        # estelam_shot is stubbed -> photo went out, plain text did not
        self.assertEqual(self.count.photos, ["shot.png"])
        self.assertEqual(self.tg.messages, [])

    def test_no_capture_on_page_failure(self):
        """The page never loaded: early error verdict, no capture at all."""
        self.H.open_page_healthy = lambda d, w, *a, **k: False
        self.H.submit_and_read = lambda d, w: ("good", "x")
        out = self.H.inquire(self._driver(), None, self.solver, SERIAL)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["shot"], "")
        self.assertEqual(self.count.calls, [])
        self.assertEqual(self.count.photos, [])

    def test_capture_on_error_exhaustion(self):
        """After MAX_CAPTCHA_TRIES failed attempts the error verdict is
        terminal, so it gets one capture as well."""
        self.H.submit_and_read = lambda d, w: ("captcha_wrong", "wrong")
        self.H.MAX_CAPTCHA_TRIES = 2
        out = self.H.inquire(self._driver(), None, self.solver, SERIAL,
                             device_id="dev2")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["shot"], "shot.png")
        self.assertEqual(len(self.count.calls), 1)
        self.assertEqual(self.count.calls[0][1], "dev2")

    def test_device_id_flows_to_the_slot(self):
        """device_id from run_from_store must reach shot_path (each device
        has its own slot)."""
        self.H.submit_and_read = lambda d, w: ("blocked", "blocked")
        self.H.inquire(self._driver(), None, self.solver, SERIAL,
                       device_id="device-xyz")
        self.assertEqual(self.count.calls[0][1], "device-xyz")

    def test_run_from_store_persists_the_screenshot_key(self):
        """run_from_store writes hamta_screenshot from the result."""
        # The extra stubs below are restored by this test's tearDown as well,
        # otherwise later tests (test_read_pil_on_real_captcha) would run
        # against this test's patched load_solver.
        self._saved.update({
            "inquire": self.H.inquire,
            "_panel_set_device": self.H._panel_set_device,
            "build_driver": self.H.build_driver,
            "load_solver": self.H.load_solver,
            "_store_read": self.H._store_read,
        })
        self.H.inquire = lambda d, w, s, serial, telegram=True, \
            device_id="": {"status": "good", "result": "r", "serial": serial,
                           "shot": "shot.png"}
        saved = {}
        self.H._panel_set_device = lambda did, **f: saved.update(f)
        self.H.build_driver = lambda: self._driver()
        self.H.load_solver = lambda: None
        self.H._store_read = lambda: {
            "devices": [{"id": "dev-x", "serial1": SERIAL,
                         "hamta_status": "pending"}]}
        # driver.quit is called in the finally block; the driver is a fake
        n = self.H.run_from_store(device_id="dev-x")
        self.assertEqual(n, 0)
        self.assertEqual(saved.get("hamta_status"), "good")
        self.assertEqual(saved.get("hamta_result"), "r")
        self.assertEqual(saved.get("hamta_screenshot"), "shot.png")


# =====================================================================
# estelam_gomrok.inquire_device — wrapper capture + telegram always sent
# =====================================================================
class GomrokInquireDeviceTests(_DataDirIsolation):
    def setUp(self):
        super().setUp()
        import estelam_gomrok as E
        self.E = E
        self.tg = _Stub()
        self.count = _CaptureCounter(returns=("shot.png", True))
        self.mods = _stub_modules(telegram_notify=self.tg,
                                  estelam_shot=self.count)
        self.mods.start()
        self.addCleanup(self.mods.stop)
        self._orig_inquire = E._inquire_device
        self._orig_tele = E._telegram
        E._telegram = self.tg.send_message
        self.device = {"id": "dev-g", "serial1": "356938035643809",
                       "serial2": "356938035643817"}
        self.person = {"national": "0012345678", "birth": "1375/04/29"}

    def tearDown(self):
        self.E._inquire_device = self._orig_inquire
        self.E._telegram = self._orig_tele

    def _stub_inquire(self, status, result="result"):
        def fake(driver, wait, solver, person, device, no_confirm_check=False):
            return {"status": status, "result": result,
                    "serial1": self.device["serial1"], "messages": []}
        self.E._inquire_device = fake

    def test_wrapper_captures_and_passes_shot(self):
        self._stub_inquire("healthy")
        out = self.E.inquire_device(_FakeDriver("element"), None, None,
                                    self.person, self.device)
        self.assertEqual(out["status"], "healthy")
        self.assertEqual(out.get("shot"), "shot.png")
        self.assertEqual(len(self.count.calls), 1)
        self.assertEqual(self.count.calls[0][0], "gomrok")
        self.assertEqual(self.count.calls[0][1], "dev-g")
        self.assertEqual(self.count.calls[0][2], self.device["serial1"])
        self.assertEqual(self.count.calls[0][3],
                         self.E.GOMROK_RESULT_SELECTOR)
        # photo went out, plain text did not
        self.assertEqual(self.count.photos, ["shot.png"])
        self.assertEqual(self.tg.messages, [])
        self.assertIn("نتیجه استعلام", self.count.captions[0])
        self.assertIn(self.device["serial1"], self.count.captions[0])

    def test_capture_failure_does_not_block_telegram(self):
        """The capture sits in a try of its OWN, separate from the result
        telegram: if `_capture_result_shot` raises, (1) the result message
        must still go out, (2) `shot` must be "" rather than missing, so
        `(outcome or {}).get("shot")` in main always works."""
        self._stub_inquire("problem", "problem text")
        with patch.object(self.E, "_capture_result_shot",
                          side_effect=RuntimeError("capture boom")):
            out = self.E.inquire_device(_FakeDriver("element"), None, None,
                                        self.person, self.device)
        # failed closed: the key exists and is empty
        self.assertEqual(out.get("shot"), "")
        # the result telegram went out as text (no photo available)
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("problem text", self.tg.messages[0])
        # nothing was attached (shot is empty) -> text path
        self.assertEqual(self.count.photos, [])
        self.assertEqual(self.count.calls, [])

    def test_capture_failure_outcome_key_missing(self):
        """`shot` must always be a str ("" on failure) so every read of
        `outcome["shot"]` or `.get("shot")` keeps working."""
        self._stub_inquire("healthy")
        with patch.object(self.E, "_capture_result_shot",
                          side_effect=RuntimeError("boom")):
            out = self.E.inquire_device(_FakeDriver("element"), None, None,
                                        self.person, self.device)
        self.assertIn("shot", out)
        self.assertEqual(out["shot"], "")
        self.assertEqual(type(out["shot"]), str)

    def test_empty_shot_falls_back_to_text(self):
        self._stub_inquire("healthy")
        self.count.returns = ("", False)
        out = self.E.inquire_device(_FakeDriver("element"), None, None,
                                    self.person, self.device)
        self.assertEqual(out.get("shot"), "")
        self.assertEqual(self.count.photos, [])
        self.assertEqual(len(self.tg.messages), 1)

    def test_shot_is_read_from_device_not_global(self):
        """The capture path is keyed on the device's id/serial, not on some
        global value (each device has its own slot)."""
        self._stub_inquire("healthy")
        self.device = {"id": "OTHER", "serial1": "111111111111111"}
        self.E.inquire_device(_FakeDriver("element"), None, None,
                              self.person, self.device)
        self.assertEqual(self.count.calls[0][1], "OTHER")
        self.assertEqual(self.count.calls[0][2], "111111111111111")

    def test_main_writes_gomrok_screenshot(self):
        """main() writes gomrok_screenshot from the outcome (source level)."""
        with open(os.path.join(_ROOT, "estelam_gomrok.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("gomrok_screenshot=", src)
        self.assertIn("_panel_set_device(", src)


# =====================================================================
# estelam_gomrok._telegram_result — photo, then text fallback
# =====================================================================
class GomrokTelegramResultTests(_DataDirIsolation):
    def setUp(self):
        super().setUp()
        import estelam_gomrok as E
        self.E = E
        self.tg = _Stub()
        self.count = _CaptureCounter()
        self.mods = _stub_modules(telegram_notify=self.tg,
                                  estelam_shot=self.count)
        self.mods.start()
        self.addCleanup(self.mods.stop)
        self._orig = E._telegram
        E._telegram = self.tg.send_message
        self.device = {"id": "d", "serial1": "356938035643809",
                       "serial2": "356938035643817"}

    def tearDown(self):
        self.E._telegram = self._orig

    def test_with_exact_shot_sends_photo_only(self):
        self.E._telegram_result(self.device, {}, "healthy", "result",
                                shot_path="s.png", shot_exact=True)
        self.assertEqual(self.count.photos, ["s.png"])
        self.assertEqual(self.tg.messages, [])
        self.assertIn("نتیجه استعلام", self.count.captions[0])

    def test_page_capture_is_never_attached(self):
        """Privacy rule: the full-page fallback still shows the traveler's
        national ID and birth date in the form above the panel, and this send
        goes to every configured chat — so it stays panel-only evidence."""
        self.E._telegram_result(self.device, {}, "healthy", "result",
                                shot_path="page.png", shot_exact=False)
        self.assertEqual(self.count.photos, [])
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("result", self.tg.messages[0])

    def test_without_shot_sends_text(self):
        self.E._telegram_result(self.device, {}, "healthy", "result",
                                shot_path="")
        self.assertEqual(self.count.photos, [])
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("result", self.tg.messages[0])

    def test_failed_photo_falls_back_to_text(self):
        failing = _FailingAttach()
        with _stub_modules(telegram_notify=self.tg, estelam_shot=failing):
            self.E._telegram_result(self.device, {}, "healthy", "result",
                                    shot_path="s.png", shot_exact=True)
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("result", self.tg.messages[0])


# =====================================================================
# Source-level contract: capture only at the verdict moment
# =====================================================================
class SourceContractTests(unittest.TestCase):
    """In both robots the capture is called only from terminal paths
    (source-level check, like test_v1_9_0_ux)."""

    def _src(self, name):
        with open(os.path.join(_ROOT, name), encoding="utf-8") as f:
            return f.read()

    def test_hamta_capture_calls_live_only_in_terminal_blocks(self):
        src = self._src("estelam_hamta.py")
        lines = src.splitlines()
        # Two call sites (the wrapper `_safe_capture` is what they call, so it
        # appears in the def line too and is skipped here): one in the verdict
        # branch, one after the retry loop gives up. Nothing in a captcha
        # retry.
        call_lines = [i for i, ln in enumerate(lines)
                      if "_safe_capture(driver" in ln
                      and not ln.strip().startswith("def ")]
        self.assertEqual(len(call_lines), 2,
                         "expected 2 captures: verdict + exhaustion")
        verdict_line = call_lines[0]
        exhaust_line = call_lines[1]

        # 1) the first call sits in the verdict branch (if cls in (...))
        block = "\n".join(lines[max(0, verdict_line - 12):verdict_line])
        self.assertIn("if cls in", block)
        self.assertIn('("good", "registered", "blocked", "success", '
                      '"unknown")', block)

        # 2) the second call sits right after the loop exhausted its tries
        exhaust_block = "\n".join(lines[max(0, exhaust_line - 12):exhaust_line])
        self.assertIn("natije nagerftim", exhaust_block)

        # 3) no capture in the early failure paths (open_page / open_modal):
        #    they return without a shot. Only the def line appears this early.
        early = "\n".join(lines[:verdict_line])
        self.assertEqual(early.count("_safe_capture(driver"), 1)
        # and in those two paths the outcome carries an empty shot
        self.assertGreaterEqual(early.count('"shot": ""'), 2)

        # 4) no capture in the captcha retry branch: between the two calls
        #    there are only retries (continue/break, captcha refresh).
        between = "\n".join(lines[verdict_line + 1:exhaust_line])
        self.assertNotIn("_safe_capture", between)
        self.assertNotIn("_capture_result_shot", between)
        self.assertNotIn("estelam_shot", between)

    def test_gomrok_capture_only_after_the_verdict(self):
        src = self._src("estelam_gomrok.py")
        i_cap = src.index("_capture_result_shot(driver, device)")
        i_wrapper = src.index("def inquire_device(")
        i_impl = src.index("def _inquire_device(")
        # 1) the capture call lives in the wrapper, before _inquire_device
        #    (the capture helper itself is a separate def, shown earlier).
        self.assertLess(i_cap, i_wrapper)
        self.assertLess(i_wrapper, i_impl)
        # 2) _inquire_device itself never captures
        next_def = src.index("\ndef ", i_impl + 10)
        body = src[i_impl:next_def]
        self.assertNotIn("_capture_result_shot", body)
        self.assertNotIn("estelam_shot", body)
        # 3) in the wrapper the capture happens after the inquiry returned
        wrapper_body = src[i_wrapper:i_impl]
        self.assertIn("outcome = _inquire_device(", wrapper_body)
        self.assertLess(wrapper_body.index("outcome = _inquire_device("),
                        wrapper_body.index("_capture_result_shot("))

    def test_both_robots_write_screenshot_key_to_store(self):
        hamta = self._src("estelam_hamta.py")
        gomrok = self._src("estelam_gomrok.py")
        self.assertIn("hamta_screenshot=", hamta)
        self.assertIn("gomrok_screenshot=", gomrok)

    def test_never_raises_contract_in_source(self):
        """estelam_shot.capture_result/attach/capture_element all fail closed
        (they catch, never propagate)."""
        src = self._src("estelam_shot.py")
        self.assertIn('return ""', src)
        self.assertIn("return False", src)
        self.assertGreaterEqual(src.count("except Exception"), 3)

    def test_capture_result_docstring_states_never_raises(self):
        src = self._src("estelam_shot.py")
        self.assertIn("Never raises", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
