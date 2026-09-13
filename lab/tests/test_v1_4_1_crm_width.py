# -*- coding: utf-8 -*-
"""v1.4.1 — operator-controlled CRM page width.

The estelam panel had a frame-width control; the CRM gets a better one:
a header «عرض» popover with three presets (باریک/عادی/عریض), a fine
range slider, a live pixel readout, a reset, persistence in localStorage
and clamping — and it must never fight the 5-second refresh.
"""
import re
import unittest

import panel


def crm_js():
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body(js, name):
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


class PageWidthTests(unittest.TestCase):

    def setUp(self):
        self.js = crm_js()
        self.src = open(panel.__file__, encoding="utf-8").read()

    def test_wrap_uses_the_variable_capped_by_viewport(self):
        self.assertIn(".wrap{width:min(var(--crm-w,1240px),98vw);margin:0 auto",
                      self.src)

    def test_state_and_clamp(self):
        self.assertIn(
            "var CRMW={key:'irica-crm-w',min:900,max:1920,def:1240,cur:1240};",
            self.js)
        apply_body = fn_body(self.js, "crmWApply")
        self.assertIn("Math.max(CRMW.min,Math.min(CRMW.max", apply_body)
        self.assertIn("setProperty('--crm-w'", apply_body)

    def test_persistence(self):
        set_body = fn_body(self.js, "crmWSet")
        self.assertIn("localStorage.setItem(CRMW.key", set_body)
        self.assertIn("try{localStorage.setItem", set_body)  # never throws

    def test_popover_controls(self):
        for marker in ('id="widthBtn"', 'id="wPop"', 'id="wRange"',
                      'id="wVal"', 'id="wReset"'):
            self.assertIn(marker, self.src)
        self.assertIn('data-w="1080">باریک', self.src)
        self.assertIn('data-w="1240">عادی', self.src)
        self.assertIn('data-w="1920">عریض', self.src)
        self.assertIn('aria-expanded', self.src)
        self.assertIn('aria-label="عرض دقیق صفحه"', self.src)

    def test_popover_closes_on_outside_click_and_escape(self):
        self.assertIn("document.addEventListener('click'", self.js)
        self.assertIn("e.key==='Escape'", self.js)
        # Esc only closes the popover when no dialog is open (the dialog's
        # own cancel path must stay untouched)
        self.assertIn("!document.getElementById('dlg').open", self.js)

    def test_slider_applies_live_and_persists_on_change(self):
        self.assertIn(
            "range.addEventListener('input',function(){crmWApply(this.value)",
            self.js)
        self.assertIn(
            "range.addEventListener('change',function(){crmWSet(this.value)",
            self.js)

    def test_control_lives_outside_the_refresh_region(self):
        # the popover sits in the header, NOT inside #view — the 5s
        # refresh re-renders only #tabBody/#crmAside
        view_pos = self.src.index('<div id="view"></div>')
        btn_pos = self.src.index('id="widthBtn"')
        self.assertLess(btn_pos, view_pos,
                        "the width control must be in the header, above #view")

    def test_popover_css(self):
        self.assertIn(".wpop{position:absolute", self.src)
        self.assertIn(".wpop.open{display:block}", self.src)
        self.assertIn("accent-color:var(--brand)", self.src)


if __name__ == "__main__":
    unittest.main()
