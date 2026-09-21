# -*- coding: utf-8 -*-
"""v1.8.1 — the round that closed the three items left open by v1.8.0, plus
the operator's new rule.

1. The busy spinner was destroyed by the 5s list re-render (it lived on the
   button ELEMENT, and the re-render replaces the node). It now lives on the
   ACTION: re-keyed by (aria-label, onclick), re-marked on the fresh nodes.
2. The period dialog neither refreshed nor came back: opening a case from it
   replaced it for good. It now refreshes on the same tick as the case modal
   (signature-gated) and closeDlg returns to it — tagged «per:».
3. Rule: no case outside a reporting period. The «+ پروندهٔ جدید» button is
   marked unavailable (aria-disabled, so the reason on hover/focus/click is
   reachable without a mouse), dlgDevice refuses, and POST /crm-device
   (action=add) asks crm.add_device(require_period=True), which refuses inside
   the store lock before it creates the inline customer — so a rejected intake
   leaves nothing behind and a concurrently closing period cannot slip a
   period-less case through.

The HTTP behaviour of the rule is exercised end to end in test_crm_api
(test_device_add_requires_an_open_period); this module pins the source contract
of all three.
"""
import inspect
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
    """Concatenated <script> bodies of CRM_PAGE (same trick as the v1.5 tests)."""
    return "\n".join(re.findall(r"<script>([\s\S]*?)</script>", panel.CRM_PAGE))


def fn_body(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
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


def js_string(js, name):
    """The value of a `var NAME='…';` literal, read from the source so the test
    can never drift from the page's own bytes (ZWNJ included)."""
    match = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*'([^']*)'", js)
    return match.group(1) if match else ""


class BusyActionStateTests(unittest.TestCase):
    """The in-flight spinner must survive the list re-render."""

    def setUp(self):
        self.js = crm_js()

    def test_busy_lives_on_the_action_not_the_element(self):
        self.assertIn("var _busyKeys={},_busyTimer=0;", self.js)
        self.assertNotIn("_busyEls", self.js,
                         "element-bound busy state dies with the re-rendered node")

    def test_the_action_key_is_name_plus_handler(self):
        body = fn_body(self.js, "ibtnKey")
        self.assertTrue(body, "ibtnKey not found")
        # pinned whole: keeping the two getAttribute calls but emptying either
        # contribution would make every same-named action spin together
        self.assertIn("return (el.getAttribute('aria-label')||'')+'|'"
                      "+String(el.getAttribute('onclick')||'');", body)

    def test_every_render_re_marks_the_actions_still_running(self):
        paint = fn_body(self.js, "ibtnRepaintBusy")
        self.assertTrue(paint, "ibtnRepaintBusy not found")
        self.assertIn("document.querySelectorAll('.ibtn')", paint)
        self.assertIn("var on=Boolean(_busyKeys[ibtnKey(el)]);", paint)
        self.assertIn("el.classList.toggle('busy',on);", paint)
        self.assertIn("el.removeAttribute('aria-busy')", paint)
        # and it has to run AFTER the DOM was replaced — calling it earlier
        # (e.g. at the top of renderView) would leave the fix a no-op
        view = fn_body(self.js, "renderView")
        self.assertIn("ibtnRepaintBusy();", view)
        self.assertIn("el.innerHTML='<div class=\"crm-cols\">", view)
        self.assertLess(view.index("updateTabCount();"),
                        view.index("ibtnRepaintBusy();"))
        self.assertLess(view.index("renderLogs();"),
                        view.index("ibtnRepaintBusy();"))

    def test_the_never_toasting_handler_is_still_covered(self):
        """The 6s net is the only thing that ends a spinner whose handler never
        toasts — dropping it would let a button spin for good."""
        listener = self.js[self.js.index("var key=ibtnKey(el);"):
                           self.js.index("var key=ibtnKey(el);") + 500]
        self.assertIn("_busyTimer=setTimeout(ibtnClearBusy,6000);", listener)

    def test_a_press_records_the_key_and_starts_the_action(self):
        js = self.js
        start = js.index("document.addEventListener('click',function(e){\n"
                         "  var el=e.target")
        listener = js[start:start + 700]
        self.assertIn("var key=ibtnKey(el);", listener)
        self.assertIn("if(_busyKeys[key])return;", listener)
        self.assertIn("_busyKeys[key]=true;", listener)
        self.assertIn("ibtnRepaintBusy();", listener)
        # the guards that keep inert / open-only buttons out of the state
        self.assertIn("el.disabled", listener)
        self.assertIn("data-static')==='1'", listener)

    def test_the_repaint_never_touches_an_open_only_button(self):
        """A static button can never flip, so it must never inherit a key from
        a flippable button that happens to share its name and handler."""
        paint = fn_body(self.js, "ibtnRepaintBusy")
        self.assertIn("if(el.getAttribute('data-static')==='1')return;", paint)

    def test_clearing_drops_every_key_and_repaints(self):
        body = fn_body(self.js, "ibtnClearBusy")
        self.assertTrue(body, "ibtnClearBusy not found")
        self.assertIn("clearTimeout(_busyTimer);", body)
        self.assertIn("_busyKeys={};", body)
        self.assertIn("ibtnRepaintBusy();", body)

    def test_clearing_is_still_wired_to_the_settling_events(self):
        for name in ("toast", "closeDlg"):
            self.assertIn("ibtnClearBusy()", fn_body(self.js, name), name)
        self.assertIn("function setTab(t){ibtnClearBusy();", self.js)


class PeriodDialogLifecycleTests(unittest.TestCase):
    """The period dialog opens a case, comes back, and stays fresh."""

    def setUp(self):
        self.js = crm_js()

    def test_the_dialog_keeps_its_own_live_state(self):
        self.assertIn("var PER={pid:'',seg:'cases',live:false,sig:''};", self.js)

    def test_opening_over_it_arms_the_return(self):
        body = fn_body(self.js, "openDlg")
        self.assertIn("DLG_BACK=CASE_MODAL.live?CASE_MODAL.id:(PER.live?'per:'+PER.pid:'');",
                      body)
        self.assertIn("PER.live=false;", body)

    def test_closing_returns_to_the_period_dialog(self):
        body = fn_body(self.js, "closeDlg")
        self.assertIn("||(PER.live&&('per:'+PER.pid)===back);", body)
        self.assertIn("PER.live=false;", body)
        self.assertIn("if(back.indexOf('per:')===0)dlgPeriod(back.slice(4));else caseDetail(back);",
                      body)

    def test_the_dialog_is_re_armed_after_it_renders(self):
        body = fn_body(self.js, "dlgPeriod")
        self.assertTrue(body, "dlgPeriod not found")
        self.assertIn("PER.live=true;PER.sig=perSig(PER.pid);", body)
        # openDlg clears the flag, so the re-arm must come after it
        self.assertLess(body.index("openDlg("), body.index("PER.live=true;"))

    def test_the_segment_survives_a_return_to_the_same_period(self):
        body = fn_body(self.js, "dlgPeriod")
        self.assertIn("PER.seg=(String(PER.pid)===String(pid)&&PER.seg)?PER.seg:'cases';",
                      body)
        self.assertNotIn("PER={pid:", body,
                         "replacing PER on every open would reset the segment")

    def test_the_dialog_refreshes_on_the_same_tick_as_the_case_modal(self):
        self.assertIn("dlgPeriodSync();", fn_body(self.js, "refresh"))
        sync = fn_body(self.js, "dlgPeriodSync")
        self.assertTrue(sync, "dlgPeriodSync not found")
        self.assertIn("if(!PER.live||!PER.pid)return;", sync)
        self.assertIn("if(sig===PER.sig)return;", sync)
        self.assertIn("dlgPeriod(PER.pid);", sync)
        # a vanished period must not leave a dead dialog on screen — pinned
        # whole, because a bare "closeDlg();" somewhere in the body would pass
        # while the branch itself is unreachable
        self.assertIn("if(!p){closeDlg();toast('این دوره پیدا نشد؛ پنجرهٔ آن بسته شد',false);return;}",
                      sync)

    def test_the_signature_covers_everything_the_dialog_prints(self):
        body = fn_body(self.js, "perSig")
        self.assertTrue(body, "perSig not found")
        for marker in ("p.closed_at", "p.label", "d.id", "d.name", "d.stage",
                       "d.owner_id", "d.customer_id", "d.serial1", "d.serial2",
                       "o.first_name", "o.mobile", "c.full_name", "c.mobile",
                       "periodMoney(pid)"):
            self.assertIn(marker, body, marker)
        # the case digest is exactly the printed fields — d.updated is stamped
        # by every robot tick, so signing it would rebuild the table (and throw
        # away the reading position) every 5 seconds
        signed = re.search(r"s\.push\(\[d\.id,(.*?)\]\.join", body, re.S)
        self.assertIsNotNone(signed, "the per-case digest not found")
        self.assertNotIn("d.updated", signed.group(1))

    def test_a_rebuild_leaves_the_operator_where_they_were_reading(self):
        body = fn_body(self.js, "dlgPeriodSync")
        self.assertIn("var scroller=document.getElementById('dlgBody');", body)
        self.assertIn("if(fresh)fresh.scrollTop=keep;", body)

    def test_a_close_confirmed_from_the_dialog_comes_back_to_it(self):
        body = fn_body(self.js, "periodClose")
        self.assertIn("var fromDlg=PER.live&&String(PER.pid)===String(id);", body)
        # ...but never over a dialog the operator opened in the meantime
        self.assertIn("if(fromDlg&&periodById(id)&&!document.getElementById('dlg').open)dlgPeriod(id);",
                      body)

    def test_a_case_keeps_the_way_back_it_was_opened_with(self):
        """The reviewer's blocker: dlgCaseSync rebuilds the case modal every
        tick, and openDlg can only arm the modal's own id — so the «per:» return
        was overwritten the moment anything in the case changed. The origin now
        lives on the modal and outlives a rebuild AND a child-dialog restore."""
        modal = fn_body(self.js, "caseDetail")
        self.assertIn("var origin=CASE_MODAL.origin||(PER.live&&PER.pid?'per:'+PER.pid:'');",
                      modal)
        self.assertIn("CASE_MODAL.origin=origin;DLG_BACK=origin;", modal)
        # the re-arm must come after openDlg (which clears both live flags)
        self.assertLess(modal.index("openDlg("), modal.index("DLG_BACK=origin;"))
        self.assertIn("var CASE_MODAL={id:'',sig:'',live:false,origin:''};", self.js)
        # closeDlg keeps the origin alive only across a case-modal restore, so a
        # stale one can never make the next case reopen the wrong dialog
        close = fn_body(self.js, "closeDlg")
        self.assertIn("var keepOrigin=Boolean(back)&&back.indexOf('per:')!==0&&!self;",
                      close)
        self.assertIn("if(!keepOrigin)CASE_MODAL.origin='';", close)
        self.assertIn("CASE_MODAL.live=false;CASE_MODAL.origin='';", self.js)
        # the sync must never touch the origin: one line clearing it here would
        # re-introduce the very blocker this test is named after, silently
        self.assertNotIn("origin", fn_body(self.js, "dlgCaseSync"))

    def test_every_close_path_clears_the_flag(self):
        # the safety net for a close that never went through closeDlg
        tail = self.js[self.js.index("getElementById('dlg').addEventListener('close'"):
                       self.js.index("getElementById('dlg').addEventListener('close'") + 400]
        self.assertIn("PER.live=false;", tail)


class NoCaseWithoutPeriodTests(unittest.TestCase):
    """The operator rule: a case may not exist outside a reporting period."""

    def setUp(self):
        self.js = crm_js()
        self.page = panel.CRM_PAGE

    def test_one_resolver_decides_what_open_means(self):
        body = fn_body(self.js, "activePeriod")
        self.assertTrue(body, "activePeriod not found")
        self.assertIn("current_period_id", body)
        self.assertIn("!p.closed_at", body)
        # every caller asks it — the bar, the card order and the tab's offer
        self.assertIn("var per=activePeriod();", fn_body(self.js, "periodBarHtml"))
        self.assertIn("var cur=activePeriod();", fn_body(self.js, "periodSorted"))
        self.assertIn("var open=activePeriod();", fn_body(self.js, "periodsHtml"))
        # ...and only it: the old hand-rolled pointer lookups are gone
        self.assertNotIn("periodOpenCard(", self.js)

    def test_the_gate_marks_the_button_blocked_and_explains(self):
        body = fn_body(self.js, "periodGateAttrs")
        self.assertTrue(body, "periodGateAttrs not found")
        self.assertIn("""activePeriod()?'':' aria-disabled="true" data-tip="'+NO_PERIOD_TIP+'"';""",
                      body)

    def test_the_blocked_button_stays_reachable_by_keyboard(self):
        """aria-disabled, not disabled: a disabled control cannot take focus, so
        the reason could never be reached without a mouse. The skin still has to
        read as inert, and the click has to answer with the reason."""
        css = "\n".join(re.findall(r"<style>([\s\S]*?)</style>", panel.CRM_PAGE))
        self.assertIn('.btn[aria-disabled="true"]{opacity:.5;cursor:not-allowed}', css)
        self.assertIn('.btn[aria-disabled="true"]:hover{filter:none;transform:none}', css)
        self.assertIn('.btn.primary[aria-disabled="true"]:hover{background:var(--brand)', css)
        self.assertNotIn('class="btn primary" disabled',
                         fn_body(self.js, "devicesHtml"))
        # the focus path shows the same chip the mouse gets
        self.assertIn("document.addEventListener('focusin',tipFocusIn);", self.js)
        # and the click is answered by the dialog's own guard
        self.assertIn("if(!activePeriod()){toast(NO_PERIOD_TIP,false);return;}",
                      fn_body(self.js, "dlgDevice"))

    def test_the_empty_state_does_not_invite_a_blocked_action(self):
        """The empty case list used to say «اولین پرونده را بسازید…» right next
        to a button that cannot open — both the toolbar hint AND the empty card
        now carry the reason instead."""
        body = fn_body(self.js, "devicesHtml")
        self.assertIn("+'<span class=\"muted\">'+(activePeriod()", body)
        self.assertIn(":NO_PERIOD_TIP)+'</span></div>'", body)
        self.assertIn(":(activePeriod()?'هنوز پرونده‌ای نیست؛ با دکمهٔ «+ پروندهٔ جدید» بالا شروع کنید.'",
                      body)
        self.assertIn(":NO_PERIOD_TIP));", body)

    def test_both_new_case_buttons_carry_the_gate(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertEqual(body.count("periodGateAttrs()+' onclick=\"dlgDevice()\""), 2,
                         "the empty state and the toolbar both offer the button")
        self.assertNotIn("class=\"btn primary\" onclick=\"dlgDevice()\"", body)

    def test_the_dialog_refuses_as_well(self):
        body = fn_body(self.js, "dlgDevice")
        self.assertIn("if(!activePeriod()){toast(NO_PERIOD_TIP,false);return;}",
                      body)

    def test_the_tip_names_where_the_period_is_started(self):
        tip = js_string(self.js, "NO_PERIOD_TIP")
        self.assertTrue(tip, "NO_PERIOD_TIP not found")
        self.assertIn("شروع دوره", tip)
        self.assertIn("پرونده", tip)

    def test_the_period_bar_states_the_rule(self):
        body = fn_body(self.js, "periodBarHtml")
        self.assertIn("شروع نشود", body)
        self.assertNotIn("بدون دوره ثبت می", body,
                         "the v1.7 copy promised the opposite of the new rule")

    def test_the_route_asks_the_domain_to_enforce_the_rule(self):
        """The rule is checked inside add_device's critical section, not as a
        check-then-write in the route: a period closing between the route's read
        and the write would otherwise slip a period-less case through (reviewer
        finding), and the refusal has to happen before the inline customer is
        created so nothing orphan is left behind."""
        src = open(panel.__file__, encoding="utf-8").read()
        route = src[src.index("else:  # device"):]
        route = route[:route.index('elif action == "update"')]
        self.assertIn("crm_mod.add_device(data, customer=inline,", route)
        self.assertIn("require_period=True", route)
        # no route-level read-then-write remains
        self.assertNotIn("crm_mod.current_period(crm_mod.store_read())", route)

    def test_the_refusal_is_answerable(self):
        """Every surface names the way out: the button's tip points at
        «شروع دورهٔ جدید», the server names the missing period."""
        self.assertIn("شروع دوره", js_string(self.js, "NO_PERIOD_TIP"))
        self.assertIn("دوره", crm.NO_OPEN_PERIOD_ERROR)
        self.assertIn("باز کنید", crm.NO_OPEN_PERIOD_ERROR)


class PeriodGateBackendTests(unittest.TestCase):
    """The same rule where it cannot be bypassed: crm.add_device refuses while
    no period is open when the intake door asks for the guarantee."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["IRICA_CRM_STORE"] = os.path.join(self.tmp.name,
                                                     "crm_store.json")
        self.addCleanup(os.environ.pop, "IRICA_CRM_STORE", None)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

    @staticmethod
    def _case(**over):
        fields = {"name": "iPhone 13", "serial1": "356938035643809"}
        fields.update(over)
        return fields

    def test_the_intake_is_refused_while_no_period_is_open(self):
        device, errors = crm.add_device(self._case(), require_period=True)
        self.assertIsNone(device)
        self.assertEqual(errors, [crm.NO_OPEN_PERIOD_ERROR])
        self.assertEqual(crm.store_read().get("devices", []), [])

    def test_a_refused_intake_leaves_no_inline_customer(self):
        device, errors = crm.add_device(
            self._case(customer={"full_name": "مشتری آزمایشی"}),
            require_period=True)
        self.assertIsNone(device)
        self.assertEqual(errors, [crm.NO_OPEN_PERIOD_ERROR])
        self.assertEqual(crm.store_read().get("customers", []), [])

    def test_the_intake_passes_once_a_period_is_open(self):
        period, period_errors = crm.open_period("")
        self.assertEqual(period_errors, [])
        device, errors = crm.add_device(self._case(), require_period=True)
        self.assertEqual(errors, [])
        self.assertEqual(device["period_id"], period["id"])

    def test_a_closed_period_does_not_reopen_the_door(self):
        period, _ = crm.open_period("")
        crm.close_period(period["id"])
        device, errors = crm.add_device(self._case(), require_period=True)
        self.assertIsNone(device)
        self.assertEqual(errors, [crm.NO_OPEN_PERIOD_ERROR])

    def test_the_legacy_default_is_unchanged(self):
        """Callers that predate periods (old stores, scripts, fixtures) keep the
        old behaviour: no flag, no rule, an empty period_id."""
        device, errors = crm.add_device(self._case())
        self.assertEqual(errors, [])
        self.assertEqual(device["period_id"], "")

    def test_the_check_sits_inside_the_store_lock(self):
        """Reviewer finding: the refusal has to be decided under the same lock
        that writes the record. Hoisting it above `with STORE_LOCK` (a
        check-then-write) reopens the race the comment rules out, and nothing
        in the suite would notice without this pin."""
        source = inspect.getsource(crm.add_device)
        self.assertIn("with STORE_LOCK:", source)
        check = "require_period and current_period(data) is None"
        self.assertIn(check, source)
        self.assertLess(source.index("with STORE_LOCK:"), source.index(check))
        # ...and before anything is written at all
        self.assertLess(source.index(check),
                        source.index('_collection(data, "devices").append(record)'))
        self.assertLess(source.index(check),
                        source.index("_collection(data, \"customers\").append"))


if __name__ == "__main__":
    unittest.main()
