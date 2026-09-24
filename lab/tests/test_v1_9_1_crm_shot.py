# -*- coding: utf-8 -*-
"""v1.9.1 — the CRM-side capture button and the delete path.

The inquiry page (ESTELAM_PAGE) is covered by test_v1_9_1_screenshot.py; the
CRM page (CRM_PAGE) reaches the same captures through a different contract,
and this slice pins that contract:

A. Rendered-string / source gates. The CRM row button is gated on the
   PRESENCE flags the state payload forwards (has_hamta_screenshot /
   has_gomrok_screenshot), never on a path — the CRM payload deliberately does
   not carry one. The dialog's error element must sit INSIDE the frame (so a
   404 replaces the picture in place instead of leaving an empty frame above
   the download button), every class the dialog uses must be defined in the
   same page string that uses it, and the inquiry page's icon and its click
   handler must test the same field expression so the camera can never appear
   for something the click then reports as missing.

B. The delete path. estelam_delete_device removes the store row first and only
   then discards the captures, and only for paths that shot_resolve() has
   proven to live inside the screenshots roots. A non-contained path is left
   alone, a failing unlink is logged rather than allowed to resurrect the row.

Deliberately NOT re-asserted here (already covered elsewhere):
  lab/tests/test_crm_api.py::test_case_rows_expose_capture_actions
      -> the mere presence of `function caseShotBtn(d,svc)`,
         `function showCaseShot(id,svc)`, both flag names, `/estelam-shot?id=`,
         `id="case-shot-download"` and `download="estelam-` in the served page.
  lab/tests/test_crm_api.py::test_crm_state_returns_screenshot_slot_flags
      -> the HTTP round-trip of the two flags and the absence of a path key in
         the /crm-state row.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_paths  # noqa: E402
import estelam_shot  # noqa: E402
import panel  # noqa: E402

#: A minimal but valid PNG header — estelam_shot.discard() only checks
#: os.path.isfile, but a real byte string keeps the fixture honest.
PNG = bytes(bytearray([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A] + [0] * 24))


def page_source():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def _slice_source():
    """(estelam_slice, crm_slice) — the two r-string page templates.

    Sliced by the assignment markers so a CRM assertion can never pass on
    inquiry-page markup (the two templates carry near-identical Persian
    strings) and vice versa.
    """
    src = page_source()
    estelam_at = src.index('ESTELAM_PAGE = r"""')
    crm_at = src.index('CRM_PAGE = r"""')
    crm_end = src.index("def render_crm_page")
    if not estelam_at < crm_at < crm_end:
        raise AssertionError("panel.py page-template markers moved")
    return src[estelam_at:crm_at], src[crm_at:crm_end]


def fn_body(js, name):
    """The full source of `function <name>(...)` including its braces."""
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", js)
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


def crm_js():
    """The CRM page's script blocks, concatenated."""
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>",
                                _slice_source()[1]))


def div_span(text, start):
    """`text[start:]` up to and including the `</div>` matching the tag at
    `start` (which must itself be a `<div`)."""
    depth, i = 0, start
    while i < len(text):
        if text.startswith("<div", i):
            depth += 1
            i += 4
            continue
        if text.startswith("</div>", i):
            depth -= 1
            i += 6
            if depth == 0:
                return text[start:i]
            continue
        i += 1
    return text[start:]


def has_css_rule(css, selector):
    """A rule for `selector` — `<selector>` then optional space then `{`."""
    return re.search(re.escape(selector) + r"\s*\{", css) is not None


#: Resolved once per process by node_binary().
_NODE_BIN = []


def node_binary():
    """Node's path, probed once per process.

    SkipTest ONLY when the binary is genuinely missing from the machine. Once
    node is known to be present, a render that fails is a real failure: the
    node run is the only gate here that actually executes the JavaScript, so
    turning a syntax error into a skip would let a broken caseShotBtn ship.
    """
    if not _NODE_BIN:
        found = shutil.which("node")
        if not found:
            raise unittest.SkipTest("node is not installed on this machine")
        _NODE_BIN.append(found)
    return _NODE_BIN[0]


class SourceSliceTests(unittest.TestCase):
    """A. Gates read out of the two page templates, no browser involved."""

    @classmethod
    def setUpClass(cls):
        cls.estelam, cls.crm = _slice_source()

    # -- gate 1 -----------------------------------------------------------
    def test_case_shot_btn_gate_is_the_presence_flag_in_the_source(self):
        body = fn_body(crm_js(), "caseShotBtn")
        self.assertTrue(body, "caseShotBtn is not defined in CRM_PAGE")
        self.assertIn("has_hamta_screenshot", body)
        self.assertIn("has_gomrok_screenshot", body)
        # an early bail-out on the flag, before any markup is built — tolerant
        # of a cosmetic reformat, since the node render below pins the
        # behaviour itself
        bail = re.search(r"if\s*\(\s*!\s*has\s*\)\s*return\s*(?:''|\"\")\s*;", body)
        self.assertIsNotNone(bail, "no early bail-out on the flag: %s" % (body[:200],))
        self.assertLess(bail.start(), body.index("return '<button"))
        # the flag, never the path: `e.<svc>_screenshot` (no `has_` prefix)
        # would be the store path and must not appear
        self.assertIsNone(re.search(r"e\.(?:hamta|gomrok)_screenshot", body),
                          "caseShotBtn reads a capture PATH, not the flag")
        self.assertNotIn("/estelam-shot", body)

    # -- gate 1, strongest form ------------------------------------------
    def test_case_shot_btn_renders_only_when_the_presence_flag_is_set(self):
        js = (
            "var ICONS={camera:'<svg-camera/>'};\n"
            + fn_body(crm_js(), "caseShotBtn") + "\n"
            "var out={"
            "hamta_flag:caseShotBtn({id:'d1',estelam:{has_hamta_screenshot:true}},"
            "'hamta'),"
            "both_false:caseShotBtn({id:'d2',estelam:{has_hamta_screenshot:false,"
            "has_gomrok_screenshot:false}},'hamta'),"
            "no_estelam:caseShotBtn({id:'d3'},'hamta'),"
            "cross:caseShotBtn({id:'d4',estelam:{has_hamta_screenshot:true}},"
            "'gomrok'),"
            "gomrok_flag:caseShotBtn({id:'d5',estelam:{has_gomrok_screenshot:true}},"
            "'gomrok')"
            "};process.stdout.write(JSON.stringify(out));"
        )
        proc = subprocess.run([node_binary(), "-"], input=js, capture_output=True,
                              text=True, encoding="utf-8")
        # node is present, so a bad exit is a real failure, never a skip
        self.assertEqual(proc.returncode, 0,
                         "node rejected the caseShotBtn harness (exit %s): %s"
                         % (proc.returncode, proc.stderr[:800]))
        self.assertEqual(proc.stderr, "",
                         "node wrote to stderr while rendering caseShotBtn")
        out = json.loads(proc.stdout)

        # one row carries the flag -> exactly one button is produced
        button = out["hamta_flag"]
        self.assertIn('class="ibtn"', button)
        self.assertIn('data-static="1"', button)
        self.assertIn("showCaseShot('d1','hamta')", button)
        self.assertIn('aria-label="مشاهدهٔ عکس نتیجهٔ همتا"', button)
        self.assertIn('data-tip="مشاهدهٔ عکس نتیجهٔ همتا"', button)
        self.assertNotIn("/estelam-shot", button)
        # ... and no button where the flag is false or the row is silent
        self.assertEqual(out["both_false"], "")
        self.assertEqual(out["no_estelam"], "")
        # each service reads its OWN flag
        self.assertEqual(out["cross"], "")
        self.assertIn("showCaseShot('d5','gomrok')", out["gomrok_flag"])

    # -- gate 2 -----------------------------------------------------------
    def test_crm_error_div_is_inside_the_case_shot_frame(self):
        open_tag = '<div class="case-shot-frame">'
        at = self.crm.index(open_tag)
        span = div_span(self.crm, at)
        self.assertTrue(span.endswith("</div>"), span[-40:])
        # containment, not co-presence: the error element is INSIDE the frame
        self.assertIn('id="case-shot-error"', span)
        self.assertIn("عکس بارگذاری نشد — فایل پیدا نیست", span)
        # and the img comes first, so the error replaces it in place
        self.assertLess(span.index('id="case-shot-img"'),
                        span.index('id="case-shot-error"'))
        # the frame closes before the download row starts
        self.assertLess(at + len(span), self.crm.index('id="case-shot-download"'))

    # -- gate 3 -----------------------------------------------------------
    def test_every_css_class_the_crm_shot_dialog_uses_is_defined_in_crm_page(self):
        at = self.crm.index('<div class="case-shot-dialog">')
        dialog = div_span(self.crm, at)
        self.assertIn('<div class="case-shot-frame">', dialog)
        self.assertIn('<div class="ibacts">', self.crm)
        self.assertIn('<div class="row" style="margin-top:12px">'
                      '<a id="case-shot-download" class="btn mini ghost"', self.crm)
        # every class used by the dialog has a rule in the SAME page string
        for selector in (".case-shot-dialog", ".case-shot-frame", "a.btn",
                         ".btn.mini", ".btn.ghost", ".ibacts"):
            self.assertTrue(has_css_rule(self.crm, selector),
                            "%s is used but not defined in CRM_PAGE" % (selector,))

    # -- gate 3, reverse direction ---------------------------------------
    def test_inquiry_shot_selectors_are_defined_in_the_inquiry_page_not_the_crm_page(self):
        self.assertIn('class="ibtn sh"', self.estelam)
        self.assertIn('class="shot-w"', self.estelam)
        self.assertTrue(has_css_rule(self.estelam, ".stgrid .ibtn.sh"))
        self.assertTrue(has_css_rule(self.estelam, ".stgrid .shot-w"))
        # the CRM page does not include those rules and must not rely on them
        self.assertFalse(has_css_rule(self.crm, ".stgrid .ibtn.sh"))
        self.assertFalse(has_css_rule(self.crm, ".stgrid .shot-w"))
        self.assertIn('class="ibtn"', fn_body(crm_js(), "caseShotBtn"))

    # -- gate 4 -----------------------------------------------------------
    def test_inquiry_shot_btn_takes_exactly_one_parameter(self):
        match = re.search(r"function\s+shotBtn\s*\(([^)]*)\)", self.estelam)
        self.assertIsNotNone(match, "shotBtn is not defined in ESTELAM_PAGE")
        params = [p.strip() for p in match.group(1).split(",") if p.strip()]
        self.assertEqual(len(params), 1, match.group(0))
        self.assertRegex(params[0], r"^[A-Za-z_$][\w$]*$")

    # -- gate 5 -----------------------------------------------------------
    def test_inquiry_icon_and_click_test_the_same_field(self):
        def field_expr(name):
            body = fn_body(self.estelam, name)
            self.assertTrue(body, "%s is not defined in ESTELAM_PAGE" % (name,))
            found = re.search(r"var\s+field\s*=\s*([^;]+);", body)
            self.assertIsNotNone(found, body[:120])
            return found.group(1)

        icon = field_expr("shotBtn")
        click = field_expr("showShotModal")
        # byte-identical: the camera can never render for something the click
        # then reports as missing
        self.assertEqual(icon, click)
        self.assertEqual(icon,
                         "svc==='hamta'?'hamta_screenshot':'gomrok_screenshot'")

    # -- gate 6 -----------------------------------------------------------
    def test_crm_page_never_carries_a_capture_path(self):
        for leaked in ("d.estelam.gomrok_screenshot", "d.estelam.hamta_screenshot",
                       "e.gomrok_screenshot", "e.hamta_screenshot",
                       "d.gomrok_screenshot", "d.hamta_screenshot"):
            self.assertNotIn(leaked, self.crm)
        # every mention of the store field name in CRM_PAGE is the `has_` flag
        for field in ("gomrok_screenshot", "hamta_screenshot"):
            self.assertEqual(self.crm.count(field),
                             self.crm.count("has_" + field))


class DeletePathTests(unittest.TestCase):
    """B. estelam_delete_device against real files in an isolated temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        self.estelam_logs = os.path.join(self.tmp.name, "estelam_logs.jsonl")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        # register the reset FIRST: addCleanup is LIFO, so the cache is
        # dropped after the environment patch is stopped
        self.addCleanup(app_paths.reset_cache)
        self._patches = [
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.object(panel, "ESTELAM_LOGS_PATH", self.estelam_logs),
            patch.dict(os.environ, {"IRICA_DATA_DIR": self.tmp.name}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        app_paths.reset_cache()
        # only now may data_subdir() run: it CREATES the directory
        self.shot_dir = panel.app_paths.data_subdir("screenshots")

    def add_device(self, name="آیفون"):
        return panel.estelam_add_device({"name": name,
                                         "serial1": "356938035643809"})

    def write_shot(self, name):
        path = os.path.join(self.shot_dir, name)
        with open(path, "wb") as handle:
            handle.write(PNG)
        return path

    def device_ids(self):
        return [str(d.get("id")) for d in panel.estelam_load()["devices"]]

    # 7
    def test_delete_removes_the_contained_capture(self):
        record = self.add_device()
        shot = self.write_shot("estelam_gomrok_%s_x.png" % (record["id"],))
        panel.estelam_set_device(record["id"], gomrok_screenshot=shot)
        self.assertTrue(os.path.isfile(shot))

        panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())
        self.assertFalse(os.path.exists(shot), "the capture was left behind")

    # 8
    def test_delete_removes_both_service_captures(self):
        record = self.add_device()
        gomrok = self.write_shot("estelam_gomrok_%s_x.png" % (record["id"],))
        hamta = self.write_shot("estelam_hamta_%s_x.png" % (record["id"],))
        panel.estelam_set_device(record["id"], gomrok_screenshot=gomrok,
                                 hamta_screenshot=hamta)

        panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())
        self.assertFalse(os.path.exists(gomrok))
        self.assertFalse(os.path.exists(hamta))

    # 9
    def test_delete_leaves_a_non_contained_path_untouched(self):
        record = self.add_device()
        # a real file, but outside the screenshots root -> shot_resolve() ""
        outside = os.path.join(self.tmp.name, "outside.png")
        with open(outside, "wb") as handle:
            handle.write(PNG)
        panel.estelam_set_device(record["id"], hamta_screenshot=outside)

        panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())
        self.assertTrue(os.path.isfile(outside),
                        "a path outside the root must never be unlinked")

    # 10
    def test_delete_does_not_touch_a_sibling_capture(self):
        keep = self.add_device(name="آیفون")
        drop = self.add_device(name="سامسونگ")
        keep_shot = self.write_shot("estelam_gomrok_%s_x.png" % (keep["id"],))
        drop_shot = self.write_shot("estelam_gomrok_%s_x.png" % (drop["id"],))
        panel.estelam_set_device(keep["id"], gomrok_screenshot=keep_shot)
        panel.estelam_set_device(drop["id"], gomrok_screenshot=drop_shot)

        panel.estelam_delete_device(drop["id"])

        ids = self.device_ids()
        self.assertNotIn(drop["id"], ids)
        self.assertIn(keep["id"], ids)
        self.assertFalse(os.path.exists(drop_shot))
        self.assertTrue(os.path.isfile(keep_shot),
                        "the surviving row lost its capture")

    # 11
    def test_a_failing_unlink_does_not_resurrect_the_row(self):
        record = self.add_device()
        shot = self.write_shot("estelam_gomrok_%s_x.png" % (record["id"],))
        panel.estelam_set_device(record["id"], gomrok_screenshot=shot)

        with patch.object(estelam_shot, "discard",
                          side_effect=RuntimeError("boom")):
            # must return normally: the unlink loop is best-effort
            panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())
        self.assertTrue(os.path.isfile(shot), "discard was stubbed to fail")

    # 12
    def test_an_empty_screenshot_field_is_a_no_op(self):
        record = self.add_device()
        panel.estelam_set_device(record["id"], gomrok_screenshot="",
                                 hamta_screenshot="")

        with patch.object(estelam_shot, "discard") as discard:
            panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())
        discard.assert_not_called()

    # 13
    def test_delete_of_a_row_whose_capture_is_already_gone(self):
        record = self.add_device()
        # the field points INSIDE the root but the file was never written
        panel.estelam_set_device(
            record["id"],
            gomrok_screenshot=os.path.join(self.shot_dir,
                                           "estelam_gomrok_gone.png"))

        panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())

    # 14
    def test_a_refused_unlink_is_recorded_and_does_not_resurrect_the_row(self):
        """The real failure mode: discard() answers False, it does not raise.

        The raising stub (test 11) covers the except branch; a False return is
        what a live unlink failure actually looks like, and it used to be
        swallowed -- the row vanished, the PNG stayed, and nothing durable was
        written.
        """
        record = self.add_device()
        shot = self.write_shot("estelam_gomrok_%s_x.png" % (record["id"],))
        panel.estelam_set_device(record["id"], gomrok_screenshot=shot)

        with patch.object(estelam_shot, "discard", return_value=False):
            panel.estelam_delete_device(record["id"])

        self.assertNotIn(record["id"], self.device_ids())
        self.assertTrue(os.path.isfile(shot),
                        "a refused unlink must leave the file on disk")
        self.assertTrue(os.path.exists(self.estelam_logs),
                        "no durable record was written for the refused unlink")
        with open(self.estelam_logs, encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["status"], "shot")
        self.assertIn("could not delete the capture for", records[0]["message"])
        self.assertIn(record["id"], records[0]["message"])

    # 15
    def test_discard_reports_the_real_unlink_contract(self):
        """The real discard(), not a stub: True when the file is gone or there
        was nothing to do, False when the unlink itself failed.

        Every other test here stubs discard(); without this one the except
        branch could silently regress to `return True` and nothing would go
        red.
        """
        shot = self.write_shot("estelam_gomrok_contract.png")

        # a file it can delete
        self.assertIs(estelam_shot.discard(shot), True)
        self.assertFalse(os.path.exists(shot), "the file was not deleted")

        # a path that does not exist is already the desired end state
        self.assertIs(estelam_shot.discard(shot), True)

        # an empty path is a no-op and must not attempt an unlink
        with patch.object(estelam_shot.os, "unlink") as unlink:
            self.assertIs(estelam_shot.discard(""), True)
        unlink.assert_not_called()

        # a genuine failure: the unlink raises, so False -- and the file stays
        locked = self.write_shot("estelam_gomrok_contract_locked.png")
        self.assertTrue(os.path.isfile(locked), "the fixture must exist first")
        real_unlink = os.unlink

        def refuse(target):
            # narrow: only the fixture is refused, anything else delegates
            if target == locked:
                raise OSError("file is in use")
            return real_unlink(target)

        with patch.object(estelam_shot.os, "unlink", side_effect=refuse):
            self.assertIs(estelam_shot.discard(locked), False)
        self.assertTrue(os.path.isfile(locked),
                        "a refused unlink must leave the file on disk")


if __name__ == "__main__":
    unittest.main()
