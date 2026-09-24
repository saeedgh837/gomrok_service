# -*- coding: utf-8 -*-
"""v1.9.0 — three operator requests, pinned at the source level.

1. Lists newest-first: the store is append-only, so raw array order was
   oldest-first and a list that never sorted buried the newest row at the
   bottom. newestFirst() sorts a COPY by the creation stamp, breaking ties
   with the store index (crm._now() is second-resolution, so two rows really
   can share a stamp — a pure timestamp sort is unstable and the list would
   flip between two orders across the 5s re-render).
2. The related-sites box moved to the bottom-LEFT corner (in RTL,
   inset-inline-start is the physical right, where the toasts also live), is
   dismissable, and its buttons are stored in settings so every browser sees
   the same list.
3. UI notification sounds: one short cue per event family, served from /sfx/
   by a whitelisted route, gated by a settings switch + a header mirror, and
   rate-limited so a flapping connection cannot beep once a second.

The HTTP behaviour of 2 and 3 is exercised end to end in test_crm_api
(settings round-trip incl. links and sound) and here at the source contract.
"""
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm    # noqa: E402
import panel  # noqa: E402


def crm_js():
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body(js, name):
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not match:
        return ""
    depth, i = 1, match.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[match.start():i]


def page_src():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


class NewestFirstTests(unittest.TestCase):
    """The ordering primitive and every list that uses it."""

    def setUp(self):
        self.js = crm_js()

    def test_the_helper_sorts_a_copy_and_breaks_ties_by_index(self):
        body = fn_body(self.js, "newestFirst")
        self.assertIn("rows||[]", body)
        self.assertIn(".map(function(r,i)", body)
        # The comparison must be a total order: stamp first, then the store
        # index, so two rows sharing a second-resolution stamp never swap.
        self.assertIn("b.i-a.i", body)

    def test_every_list_that_was_oldest_first_now_sorts(self):
        """The lists that had no ordering at all (raw append order)."""
        for name in ("customersHtml", "ownersHtml", "identitiesHtml",
                     "periodicHtml", "paRender", "perOwnersHtml",
                     "perCustomersHtml", "ledgerHtml", "dlgDevice",
                     "dlgDeviceEdit", "dlgAttach"):
            self.assertIn("newestFirst(", fn_body(self.js, name), name)

    def test_the_lists_that_were_already_newest_are_left_alone(self):
        """devicesHtml keeps its needs-action-first order; the statement is a
        financial document and stays chronological."""
        body = fn_body(self.js, "devicesHtml")
        self.assertNotIn("newestFirst(", body)
        self.assertIn("needsAction(", body)
        self.assertNotIn("newestFirst(", fn_body(self.js, "stmtBodyHtml"))

    def test_the_passenger_lists_are_numbered(self):
        for name in ("ownersHtml", "ownerArchiveHtml"):
            body = fn_body(self.js, name)
            self.assertIn("fa(i+1)", body, name)
            self.assertIn("ردیف", body, name)

    def test_sorting_never_touches_the_store_arrays(self):
        """STATE order is read by other renders (and STATE.devices feeds the
        robots); the sort must be on a copy."""
        for name in ("customersHtml", "ownersHtml", "identitiesHtml"):
            body = fn_body(self.js, name)
            self.assertNotIn("STATE.customers.sort(", body)
            self.assertNotIn("STATE.owners.sort(", body)
            self.assertNotIn("STATE.identities.sort(", body)

    def test_the_transaction_index_stays_intact(self):
        """The ledger row button hands txDetailModal the TRUE array index, so
        only the outer (customer) order may change."""
        body = fn_body(self.js, "ledgerHtml")
        self.assertIn("txs.length-1", body)
        self.assertIn("txDetailModal(", body)
        self.assertNotIn("txs.sort(", body)


class SoundEngineTests(unittest.TestCase):
    """The cue engine: one sound per family, gated, rate-limited, silent off-tab."""

    def setUp(self):
        self.js = crm_js()

    def test_the_seven_cues_exist_and_are_the_whitelist(self):
        self.assertEqual(sorted(panel.SFX_FILES), sorted([
            "ui-ok.wav", "ui-error.wav", "ui-warn.wav", "ui-open.wav",
            "ui-tab.wav", "ui-login.wav", "ui-login-error.wav"]))

    def test_the_engine_is_gated_and_never_throws(self):
        body = fn_body(self.js, "sfx")
        self.assertIn("SOUND.enabled", body)
        self.assertIn("document.hidden", body)          # never beep off-tab
        self.assertIn("350", body)                      # rate limit per family
        self.assertIn("catch(", body)                   # a cue is decoration
        self.assertIn("currentTime=0", body)

    def test_the_switch_is_mirrored_from_the_server_state(self):
        body = fn_body(self.js, "refresh")
        self.assertIn("sound_enabled", body)
        self.assertIn("paintSoundBtn()", body)

    def test_the_header_button_and_settings_save_agree(self):
        self.assertIn("id=\"soundBtn\"", panel.CRM_PAGE)
        self.assertIn("toggleSound()", self.js)
        self.assertIn("settingsSaveSound()", self.js)
        self.assertIn("stSound", panel.CRM_PAGE)

    def test_the_cues_are_hooked_at_the_real_events(self):
        self.assertIn("sfx('ui-open')", fn_body(self.js, "openDlg"))
        self.assertIn("sfx('ui-tab')", fn_body(self.js, "setTab"))
        toast = fn_body(self.js, "toast")
        self.assertIn("ui-error", toast)
        self.assertIn("ui-warn", toast)
        self.assertIn("ui-ok", toast)

    def test_the_login_page_has_its_own_cue_code(self):
        login = panel.LOGIN_PAGE
        # The cue names are passed as literals, and the URL is built from the
        # whitelisted route prefix (so a name outside SFX_FILES can never be
        # requested from this page either).
        self.assertIn('new Audio("/sfx/" + name)', login)
        self.assertIn('sfx("ui-login")', login)
        self.assertIn('sfx("ui-login-error")', login)
        self.assertIn("irica-sound", login)             # the pre-session mirror
        # The success cue plays BEFORE the navigation, so it is not cut off.
        self.assertLess(login.index('sfx("ui-login")'),
                        login.index("window.location.replace"))

    def test_the_route_is_public_but_whitelisted(self):
        self.assertIn("/sfx/", panel.Handler.PUBLIC_GET_PATHS)
        for name in panel.SFX_FILES:
            self.assertIn(name, panel.SFX_FILES)

    def test_serving_only_the_whitelisted_names(self):
        """A name outside the whitelist is refused, and a path traversal does
        not escape the sfx folder (os.path.basename + membership)."""
        import io
        import urllib.request
        import threading
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = os.path.join(tmp.name, "crm_store.json")
        patches = [
            patch.dict(os.environ, {"IRICA_CRM_STORE": store}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)
        from lab.tests import _auth_harness
        _auth_harness.seed_test_users()
        token = _auth_harness.seed_test_users()

        httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)

        def get(path):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
            req.add_header("Cookie", f"irica_session={token}")
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, len(r.read())
            except urllib.error.HTTPError as e:
                return e.code, 0

        status, size = get("/sfx/ui-ok.wav")
        self.assertEqual(status, 200)
        self.assertGreater(size, 1000)
        # Not in the whitelist.
        self.assertEqual(get("/sfx/../panel.py")[0], 404)
        self.assertEqual(get("/sfx/nope.wav")[0], 404)


class QuickBoxTests(unittest.TestCase):
    """The related-sites box: left corner, dismissable, server-backed links."""

    def setUp(self):
        self.js = crm_js()

    def test_the_box_is_pinned_to_the_left_corner(self):
        self.assertIn(".quickbox-wrap{position:fixed;bottom:14px;inset-inline-end:14px",
                      panel.CRM_PAGE)
        self.assertNotIn("inset-inline-start:14px;z-index:60}", panel.CRM_PAGE)

    def test_the_box_has_a_close_button_that_persists(self):
        body = fn_body(self.js, "renderQuickBox")
        self.assertIn('id="quickBox"', body)
        self.assertIn("qb-x", body)
        self.assertIn("quickBoxHide", body)
        hide = fn_body(self.js, "quickBoxHide")
        self.assertIn("irica-quickbox", hide)
        self.assertIn("'0'", hide)

    def test_the_links_come_from_the_server_not_localStorage(self):
        self.assertNotIn("irica-crm-links", panel.CRM_PAGE)
        self.assertNotIn("CRM_LINKS_KEY", panel.CRM_PAGE)
        self.assertIn("STATE.settings.links", fn_body(self.js, "loadLinks"))

    def test_the_box_is_painted_after_the_state_lands(self):
        """The links are a server setting, so the box has nothing to render
        until the first fetch resolves. Painting it at boot (before refresh)
        showed «لینکی ثبت نشده» for the whole first 5s. It now paints inside
        the fetch's try block, so a failed fetch leaves the previous box
        standing rather than clearing it."""
        refresh = fn_body(self.js, "refresh")
        self.assertIn("renderQuickBox()", refresh)
        boot = self.js[self.js.rfind("renderTabs();"):]
        self.assertNotIn("renderQuickBox()", boot)
        # and it must be inside the try, not the catch — a flapping
        # connection must not wipe the buttons
        self.assertLess(refresh.index("renderQuickBox()"),
                        refresh.index("}catch(e){ LOADFAIL=true; }"))

    def test_the_box_stays_closed_and_is_recoverable(self):
        body = fn_body(self.js, "renderQuickBox")
        self.assertIn("getItem('irica-quickbox')", body)
        show = fn_body(self.js, "quickBoxShow")
        self.assertIn("removeItem('irica-quickbox')", show)

    def test_the_settings_card_edits_and_saves_links(self):
        body = fn_body(self.js, "settingsHtml")
        self.assertIn("linkRows", body)
        self.assertNotIn("rows||", body)
        save = fn_body(self.js, "settingsLinksSave")
        self.assertIn("draftLinks()", save)
        self.assertIn("links:", save)

    def test_the_draft_helpers_dont_rerender_the_whole_tab(self):
        """Add/remove repaint only the row container, so unsaved edits in the
        other settings cards survive."""
        for name in ("settingsLinkAdd", "settingsLinkRemove"):
            body = fn_body(self.js, name)
            self.assertIn("settingsLinksPaint(", body)
            self.assertNotIn("renderView(", body)

    def test_the_default_links_survive_an_old_store(self):
        data = crm._empty_store()
        # _empty_store carries the defaults verbatim; the merge in _store_read
        # backfills the same list for a store from before v1.9.
        self.assertEqual([l["url"] for l in data["settings"]["links"]],
                         crm.LINKS_DEFAULT_URLS)
        self.assertIn("https://hamta.ntsw.ir/", crm.LINKS_DEFAULT_URLS)
        self.assertIn("https://epl.irica.gov.ir/ImeiRegister?1",
                      crm.LINKS_DEFAULT_URLS)

    def test_an_empty_list_is_kept_not_refilled(self):
        """An operator who removed every link means "no buttons". If the read
        merge refilled the defaults on an empty list, the two default buttons
        would resurrect within the next 5s refresh and there would be no way
        to hide the box except the per-browser ×."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = os.path.join(temp_dir, "crm_store.json")
            with patch.dict(os.environ, {"IRICA_CRM_STORE": store}):
                crm.reset_cache()
                try:
                    data = crm._empty_store()
                    data["settings"]["links"] = []
                    crm.store_write(data)
                    read = crm.store_read()
                finally:
                    crm.reset_cache()
        self.assertEqual(read["settings"]["links"], [])

    def test_a_missing_or_invalid_list_falls_back_to_the_defaults(self):
        for raw in (None, "nope", {"a": 1}):
            with self.subTest(raw=raw):
                settings = dict(crm.DEFAULT_SETTINGS)
                if raw is None:
                    settings.pop("links", None)
                else:
                    settings["links"] = raw
                out = crm._normalise_settings(settings)
                self.assertEqual([l["url"] for l in out["links"]],
                                 crm.LINKS_DEFAULT_URLS)


class SettingsSoundLinksTests(unittest.TestCase):
    """crm.update_settings' new keys: the boolean and the link list."""

    def test_the_defaults(self):
        self.assertIs(crm.DEFAULT_SETTINGS["sound_enabled"], True)
        self.assertIn("links", crm.DEFAULT_SETTINGS)

    def test_the_boolean_is_in_the_shared_tuple(self):
        """The same loop that coerces the other booleans must include the new
        one, or the checkbox would never persist."""
        src = inspect_source()
        self.assertIn('"sound_enabled"', src)

    def test_link_validation(self):
        """A javascript: URL in a target=_blank button would run in the
        panel's own origin, and a duplicate is noise. A rejected URL reports
        one Persian error, and the good links in the same payload still
        cannot land: the whole batch is atomic (nothing is written)."""
        import inspect
        src = inspect.getsource(panel.crm_update_settings)

        with patch.object(crm, "store_read") as read, \
                patch.object(crm, "store_write") as write:
            read.return_value = {"settings": {}, "devices": []}
            settings, errors = panel.crm_update_settings({
                "links": [
                    {"name": "سایت اول", "url": "https://ok.example/"},
                    {"name": "x", "url": "javascript:alert(1)"},
                    {"name": "dup", "url": "https://ok.example/"},
                    {"name": "no url", "url": ""},
                    {"url": "https://noname.example/"},  # name is optional
                ]})
        self.assertIsNone(settings)               # the batch is all-or-nothing
        self.assertEqual(len(errors), 1)
        self.assertIn("http", errors[0])
        self.assertFalse(write.called)

    def test_a_fully_valid_batch_lands_deduped(self):
        with patch.object(crm, "store_read") as read, \
                patch.object(crm, "store_write") as write:
            read.return_value = {"settings": {}, "devices": []}
            settings, errors = panel.crm_update_settings({
                "links": [
                    {"name": "سایت اول", "url": "https://ok.example/"},
                    {"name": "dup", "url": "https://ok.example/"},
                    {"url": "https://noname.example/"},  # name is optional
                ]})
        self.assertEqual(errors, [])
        self.assertEqual([l["url"] for l in settings["links"]],
                         ["https://ok.example/", "https://noname.example/"])
        self.assertEqual(settings["links"][1]["name"], "https://noname.example/")
        self.assertTrue(write.called)

    def test_a_bad_url_is_reported_and_nothing_is_written(self):
        with patch.object(crm, "store_read") as read, \
                patch.object(crm, "store_write") as write:
            read.return_value = {"settings": {}, "devices": []}
            settings, errors = panel.crm_update_settings(
                {"links": [{"name": "bad", "url": "javascript:alert(1)"}]})
        self.assertIsNone(settings)
        self.assertEqual(len(errors), 1)
        self.assertFalse(write.called)


def inspect_source():
    import inspect
    return inspect.getsource(panel.crm_update_settings)


if __name__ == "__main__":
    unittest.main()
