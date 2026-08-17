"""Tests for headless-by-default browser windows + the panel show/hide toggle.

Contract:
  - every profile starts with browser_visible=False (browser runs off-screen);
  - the panel only accepts a visibility change for a RUNNING (busy) profile;
  - claiming a profile resets the flag, and a dead runner clears it;
  - the robot-side controller moves the window off-screen by default and
    follows the panel flag while running;
  - the auto-submit switch is gone from the UI and submit is forced ON,
    while the debug and manual-manage switches are untouched.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import browser_visibility as bvis
import panel


class BrowserVisibilityStoreTests(unittest.TestCase):
    def _paths(self, tmp):
        return patch.multiple(
            panel,
            PROFILES_PATH=os.path.join(tmp, "profiles.json"),
            INPUTS_PATH=os.path.join(tmp, "inputs.json"),
            _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock"),
        )

    def _seed(self, profiles):
        with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump({"active_id": profiles[0]["id"], "profiles": profiles}, f)

    def setUp(self):
        panel._LAST_GOOD_STORE.clear()

    def test_default_profile_hides_browser(self):
        self.assertFalse(panel.PROFILE_DEFAULTS["browser_visible"])
        self.assertFalse(panel._clean_profile({})["browser_visible"])

    def test_toggle_requires_running_profile(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed([{"id": "p1", "title": "A", "run_status": "idle"}])
            status, payload = panel.set_browser_visibility("p1", True)
            self.assertEqual(status, 409)
            self.assertIn("error", payload)
            self.assertFalse(panel.get_browser_visibility("p1"))

    def test_toggle_on_and_off_for_running_profile(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed([{"id": "p1", "title": "A", "run_status": "busy",
                         "runner_id": "r1", "runner_pid": os.getpid()}])
            status, payload = panel.set_browser_visibility("p1", True)
            self.assertEqual(status, 200)
            self.assertTrue(payload["browser_visible"])
            self.assertTrue(panel.get_browser_visibility("p1"))
            status, payload = panel.set_browser_visibility("p1", False)
            self.assertEqual(status, 200)
            self.assertFalse(panel.get_browser_visibility("p1"))

    def test_unknown_profile_and_empty_id(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed([{"id": "p1", "title": "A", "run_status": "busy"}])
            self.assertEqual(panel.set_browser_visibility("nope", True)[0], 404)
            self.assertEqual(panel.set_browser_visibility("", True)[0], 422)
            self.assertIsNone(panel.get_browser_visibility("nope"))
            self.assertIsNone(panel.get_browser_visibility(""))

    def test_claim_resets_visibility_to_hidden(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed([{"id": "p1", "title": "A", "browser_visible": True}])
            claimed = panel.claim_profile("p1")
            self.assertIsNotNone(claimed)
            self.assertFalse(claimed["browser_visible"])
            self.assertFalse(panel.get_browser_visibility("p1"))

    def test_stale_runner_clears_visibility(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed([{"id": "p1", "title": "A", "run_status": "busy",
                         "runner_id": "r1", "runner_pid": 0,
                         "heartbeat_at": "2000-01-01 00:00:00",
                         "browser_visible": True}])
            self.assertTrue(panel.clear_stale_busy_profiles())
            profile = panel.load_store()["profiles"][0]
            self.assertEqual(profile["run_status"], "idle")
            self.assertFalse(profile["browser_visible"])

    def test_visibility_read_survives_corrupt_store(self):
        with tempfile.TemporaryDirectory() as tmp, self._paths(tmp):
            self._seed([{"id": "p1", "title": "A", "run_status": "busy"}])
            panel.load_store()
            backup = panel.PROFILES_PATH + ".bak"
            if os.path.exists(backup):
                os.remove(backup)
            panel._LAST_GOOD_STORE.clear()
            with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
                f.write("{{{ broken")
            self.assertIsNone(panel.get_browser_visibility("p1"))


class _FakeDriver:
    """Minimal stand-in: only the chromedriver pid is ever read."""

    class _Service:
        class _Process:
            pid = 4242
        process = _Process()

    service = _Service()


class VisibilityControllerTests(unittest.TestCase):
    def test_defaults_to_offscreen_and_follows_panel_flag(self):
        calls = []
        with patch.object(bvis.bw, "find_browser_window", return_value=1234), \
             patch.object(bvis.bw, "window_rect", return_value=(0, 0, 900, 700)), \
             patch.object(bvis.bw, "hide_window", side_effect=lambda h: calls.append(("hide", h)) or True), \
             patch.object(bvis.bw, "show_window", side_effect=lambda h, p=None: calls.append(("show", h)) or True):
            controller = bvis.WindowVisibilityController(_FakeDriver(), "p1", visible=False)
            self.assertTrue(controller.apply(force=True))
            self.assertEqual(calls, [("hide", 1234)])
            # panel asks for the window -> restored on screen
            self.assertTrue(controller.set_visible(True))
            self.assertEqual(calls[-1], ("show", 1234))
            # idempotent: same state does not re-issue the move
            before = len(calls)
            controller.set_visible(True)
            self.assertEqual(len(calls), before)
            # and back off-screen to save resources
            self.assertTrue(controller.set_visible(False))
            self.assertEqual(calls[-1], ("hide", 1234))

    def test_missing_window_is_not_fatal(self):
        with patch.object(bvis.bw, "find_browser_window", return_value=None), \
             patch.object(bvis.bw, "window_rect", return_value=None):
            controller = bvis.WindowVisibilityController(_FakeDriver(), "p1")
            self.assertFalse(controller.apply(force=True))

    def test_rebind_reapplies_to_new_driver(self):
        calls = []
        with patch.object(bvis.bw, "find_browser_window", return_value=99), \
             patch.object(bvis.bw, "window_rect", return_value=(0, 0, 900, 700)), \
             patch.object(bvis.bw, "hide_window", side_effect=lambda h: calls.append(h) or True), \
             patch.object(bvis.bw, "show_window", return_value=True):
            controller = bvis.WindowVisibilityController(_FakeDriver(), "p1")
            controller.apply(force=True)
            controller.rebind(_FakeDriver())
            self.assertEqual(calls, [99, 99])

    def test_windows_helpers_never_raise_without_a_window(self):
        self.assertIsNone(bvis.bw.window_rect(None))
        self.assertFalse(bvis.bw.hide_window(None))
        self.assertFalse(bvis.bw.show_window(None))
        self.assertFalse(bvis.bw.move_window(None, 0, 0))
        self.assertEqual(bvis.bw.descendant_pids(0), [])
        x, y = bvis.bw.offscreen_position()
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)


class AutoSubmitAlwaysOnTests(unittest.TestCase):
    def test_submit_is_forced_on_regardless_of_stored_value(self):
        self.assertTrue(panel.PROFILE_DEFAULTS["submit"])
        self.assertTrue(panel._clean_profile({})["submit"])
        self.assertTrue(panel._clean_profile({"submit": False})["submit"])

    def test_ui_has_no_auto_submit_switch_but_keeps_debug_and_manual(self):
        js = panel.PAGE_JS
        self.assertNotIn("submit_on", js)
        self.assertNotIn("submit خودکار", js)
        self.assertIn("debug_on", js)
        self.assertIn("manual_on", js)
        self.assertIn("حالت دیباگ", js)
        self.assertIn("مدیریت دستی", js)

    def test_saved_profile_keeps_submit_on(self):
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(
                panel,
                PROFILES_PATH=os.path.join(tmp, "profiles.json"),
                INPUTS_PATH=os.path.join(tmp, "inputs.json"),
                _STORE_LOCK_PATH=os.path.join(tmp, "profiles.json.lock")):
            panel._LAST_GOOD_STORE.clear()
            clean = panel.save_store({"profiles": [{"id": "p1", "title": "A", "submit": False}],
                                      "active_id": "p1"})
            self.assertTrue(clean["profiles"][0]["submit"])
            with open(panel.INPUTS_PATH, encoding="utf-8") as f:
                self.assertTrue(json.load(f)["submit"])


class BrowserButtonUiTests(unittest.TestCase):
    def test_toggle_button_is_rendered_only_while_running(self):
        js = panel.PAGE_JS
        self.assertIn("function profileBrowserButton(p)", js)
        self.assertIn("if(p.run_status!=='busy') return '';", js)
        self.assertIn("نمایش مرورگر", js)
        self.assertIn("مخفی کردن مرورگر", js)
        self.assertIn("/browser-visibility", js)
        self.assertIn("p.run_status==='busy'?'<div class=\"prof-browser-row\">", js)

    def test_button_styles_exist(self):
        head = panel.PAGE_HEAD
        self.assertIn(".browser-btn", head)
        self.assertIn(".browser-btn.visible", head)
        self.assertIn(".prof-browser-row", head)


if __name__ == "__main__":
    unittest.main()
