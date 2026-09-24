# -*- coding: utf-8 -*-
"""v1.10 period filter — «پرونده‌های دورهٔ فعلی» and «لیست پرونده‌ها بر اساس
دوره‌ها» on the CRM case lists (operator request, 2026-09-24).

The period filter is a SECOND dimension next to the stage chips, and every
consumer (both list filters, the chips, the result pill) asks the one resolver,
so they cannot disagree. This module EXECUTES the shipped page functions in
node — extracted from panel.CRM_PAGE with the same helpers the v1.4.0 tab-search
module uses — instead of re-implementing them in Python: a Python twin would
keep passing while the real JS drifted. The node step is skipped with a reason
where node is absent; the source markers below still run.

Behaviour pinned here:
- 'all' keeps each tab's own base set: the devices tab shows exactly the
  in-flight cases it showed before, and its chip row gains no finished stage.
- a period id narrows to that period at every stage, finished cases included
  — that is what makes a closed period's cases reachable as a list.
- 'cur' is the live set: the in-flight cases PLUS the open period's cases, so
  an operator mid-period never loses a case from view.
- two values degrade to 'all' instead of to a dead end: a period the store no
  longer has, and 'cur' while no period is open.
- off the case-list tabs the filter reads as 'all' — shared toolbar state must
  never filter a table that has no period dimension.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lab.tests.test_v1_4_0_tab_search import crm_js, fn_body  # noqa: E402

NODE = shutil.which("node")

# The page's own bodies. Only the state they read and the display helpers they
# call (esc, the two name lookups, the stage labels) are supplied by the
# prelude — everything asserted below is the shipped code.
EXTRACT = [
    "isActiveCase", "needsAction", "normSearch", "tabQ", "tabSearchMatches",
    "devHay", "periodById", "activePeriod", "periodSorted", "periodName",
    "periodFilterTab", "periodFilterResolved", "periodPass",
    "periodBaseDevices", "searchActive", "filterDevices", "filterDevRows",
    "stageChipsHtml", "periodPidAttr", "periodChipsHtml", "fa",
]

PRELUDE = """
var TAB='devices';
var TAB_PERIOD_FILTER='all';
var TAB_STAGE_FILTER='all';
var TAB_Q={};
var STAGE_FA={};      // chip labels are not asserted; the data-f keys are
var STATE={settings:{current_period_id:'p2'},periods:[],devices:[]};
function esc(s){return String(s==null?'':s);}   // identity: fixtures are plain
function ownerName(){return '';}                // devHay stubs; no name search
function custName(){return '';}
"""

FIXTURES = """
var PERIODS=[
  {id:'p1',number:1,label:'',opened_at:'2026-01-01 10:00:00',closed_at:'2026-01-31 10:00:00'},
  {id:'p2',number:2,label:'',opened_at:'2026-02-01 10:00:00',closed_at:''}
];
var DEVICES=[
  {id:'d1',stage:'inquiry',period_id:'p1'},   // in flight, registered in a CLOSED period
  {id:'d2',stage:'done',period_id:'p1'},      // finished, closed period
  {id:'d3',stage:'problem',period_id:'p1'},
  {id:'d4',stage:'inquiry',period_id:'p2'},   // in flight, open period
  {id:'d5',stage:'done',period_id:'p2'},      // finished, open period
  {id:'d6',stage:'closed',period_id:'p2'}
];
function reset(){
  STATE.periods=PERIODS;STATE.devices=DEVICES;
  STATE.settings.current_period_id='p2';
  TAB='devices';TAB_PERIOD_FILTER='all';TAB_STAGE_FILTER='all';TAB_Q={};
}
function ok(cond,msg){if(!cond)throw new Error('FAIL: '+msg);}
function ids(ds){return ds.map(function(d){return d.id;}).join(',');}
function hit(html,key){return html.indexOf(key)>=0;}
function chipCount(html){return (html.match(/class="chipbtn/g)||[]).length;}
"""

CHECKS = r"""
reset();
ok(ids(periodBaseDevices())==='d1,d4',
   'default base set must be the in-flight cases only');
ok(periodPass(DEVICES[1])===true,'an inactive period filter must pass everything');
ok(searchActive()===false,'an untouched toolbar must not count as filtered');

// a period id: that period at every stage, finished cases included
TAB_PERIOD_FILTER='p1';
ok(ids(periodBaseDevices())==='d1,d2,d3',
   'a closed period must show its cases at every stage');
ok(periodPass(DEVICES[3])===false,'another period must not pass');
ok(periodFilterResolved()==='p1','a present period must resolve to itself');
ok(searchActive()===true,'a selected period must count as a filter');

// 'cur': the in-flight cases plus the open period
TAB_PERIOD_FILTER='cur';
ok(ids(periodBaseDevices())==='d1,d4,d5,d6',
   'cur must keep every in-flight case and the open period');
ok(periodPass(DEVICES[1])===false,
   'a finished case of a closed period must not pass cur');
ok(periodPass(DEVICES[4])===true,
   'a finished case of the open period must pass cur');

// 'cur' with no open period degrades to 'all' (never a dead end)
STATE.settings.current_period_id='';
ok(periodFilterResolved()==='all','cur without an open period must read as all');
ok(ids(periodBaseDevices())==='d1,d4',
   'cur without an open period must show the in-flight cases');
ok(searchActive()===false,'a degraded filter must not count as active');

// a period the store no longer has degrades to 'all'
STATE.settings.current_period_id='p2';
TAB_PERIOD_FILTER='p9';
ok(periodFilterResolved()==='all','a missing period must read as all');
ok(ids(periodBaseDevices())==='d1,d4','a missing period must not empty the list');

// inert off the case-list tabs
TAB_PERIOD_FILTER='p1';TAB='customers';
ok(periodFilterResolved()==='all','the filter must not apply off the case lists');
ok(periodPass(DEVICES[3])===true,'...and must pass every row there');
ok(searchActive()===false,'...and must not mark those tabs as filtered');
TAB='done_tab';
ok(periodFilterResolved()==='p1','the done tab IS a case list');
TAB='devices';

// the stage chip row follows the period base set
TAB_PERIOD_FILTER='p1';
var chips=stageChipsHtml();
ok(hit(chips,'data-f="done"'),'a period with finished cases must offer their chip');
ok(hit(chips,'data-f="problem"'),'...for every stage it holds');
TAB_PERIOD_FILTER='all';
chips=stageChipsHtml();
ok(!hit(chips,'data-f="done"'),'the default chip row must not gain finished stages');
ok(!hit(chips,'data-f="closed"'),'...nor closed ones');

// composition: period + stage + query
TAB_PERIOD_FILTER='p1';TAB_STAGE_FILTER='done';
ok(ids(filterDevices(periodBaseDevices()))==='d2','period and stage must compose');
TAB_STAGE_FILTER='need';
ok(ids(filterDevices(periodBaseDevices()))==='d3','the need chip must compose too');
TAB_STAGE_FILTER='all';TAB_Q.devices='d4';
ok(ids(filterDevices(periodBaseDevices()))==='','a query must still compose');
TAB_Q.devices='';TAB_PERIOD_FILTER='p2';
ok(ids(filterDevices(DEVICES))==='d4,d5,d6',
   'filterDevices must apply the period rule to a raw list as well');

// done/problem/closed rows: the query-only identity is preserved
TAB_PERIOD_FILTER='all';
var arr=[DEVICES[0]];
ok(filterDevRows(arr)===arr,
   'an unfiltered filterDevRows call must hand the caller its own array back');
TAB_PERIOD_FILTER='p2';
ok(ids(filterDevRows(DEVICES))==='d4,d5,d6','filterDevRows must apply the period');
TAB_Q.devices='d6';
ok(ids(filterDevRows(DEVICES))==='d6','...and still compose with the query');
TAB_Q.devices='';

// chips
TAB_PERIOD_FILTER='all';
var html=periodChipsHtml();
ok(chipCount(html)===3,'all + cur + one other period: 3 chips');
ok(hit(html,'data-f="cur"'),'an open period must offer the cur chip');
ok(!hit(html,'data-f="p2"'),
   'the open period is represented by cur, not by a second near-identical chip');
ok(hit(html,'data-f="p1"'),'another period must have its own chip');
TAB_PERIOD_FILTER='cur';
ok(hit(periodChipsHtml(),'class="chipbtn active" data-f="cur"'),
   'cur must be highlighted while it is selected');

// no open period: no cur chip, one chip per period
STATE.settings.current_period_id='';
var h2=periodChipsHtml();
ok(!hit(h2,'data-f="cur"'),'no cur chip while no period is open');
ok(chipCount(h2)===3,'all + one chip per period');

// the cap keeps the toolbar usable and names what it left out
var many=[];
for(var i=1;i<=12;i++)
  many.push({id:'q'+('0'+i).slice(-2),number:i,label:'',
             opened_at:'2026-03-'+('0'+i).slice(-2)+' 10:00:00',
             closed_at:'2026-04-'+('0'+i).slice(-2)+' 10:00:00'});
STATE.periods=many;
var h3=periodChipsHtml();
ok(chipCount(h3)===PERIOD_CHIP_MAX+1,
   'all + the newest PERIOD_CHIP_MAX periods when the store holds more');
ok(hit(h3,'kbd'),'the trimmed periods must be named, not dropped silently');
ok(!hit(h3,'data-f="q01"'),'the oldest period is the one left out');
ok(hit(h3,'data-f="q12"'),'the newest period must be first in line');

// an id reaches an onclick string literal, so it is reduced to what an id can be
ok(periodPidAttr('p1"){alert(1)}//')==='p1alert1',
   'a hostile period id must lose everything that is not id-shaped');
ok(periodPidAttr(null)==='','a missing id must not become a literal');
console.log('period-filter checks: '+String(true));
"""


def build_script():
    js = crm_js()
    missing = [n for n in EXTRACT if not fn_body(js, n)]
    if missing:                     # a renamed/removed function must fail loudly
        raise AssertionError("functions not found in the page: %r" % missing)
    cap = re.search(r"var PERIOD_CHIP_MAX=(\d+);", js)
    if not cap:
        raise AssertionError("PERIOD_CHIP_MAX is not declared in the page")
    tabs = re.search(r"var PERIOD_FILTER_TABS=\[[^\]]*\];", js)
    if not tabs:
        raise AssertionError("PERIOD_FILTER_TABS is not declared in the page")
    body = "\n".join(fn_body(js, n) for n in EXTRACT)
    return (PRELUDE
            + "var PERIOD_CHIP_MAX=%s;\n" % cap.group(1)
            + tabs.group(0) + "\n"
            + body + "\n" + FIXTURES + CHECKS)


def strip_block_comments(js):
    """Only /* */ — the page's `//` inside URLs must survive untouched."""
    return re.sub(r"/\*[\s\S]*?\*/", "", js)


@unittest.skipUnless(NODE, "node is not installed: cannot execute the page JS")
class PeriodFilterBehaviourTests(unittest.TestCase):
    """The shipped functions, run as-is."""

    def test_page_functions_match_the_period_contract(self):
        path = os.path.join(tempfile.mkdtemp(prefix="irica_js_"), "period.js")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(build_script())
        proc = subprocess.run([NODE, path], capture_output=True, timeout=60)
        out = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        # The harness throws on the first broken expectation, so a failure
        # names exactly the contract item that drifted.
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIn("period-filter checks:", out)


class PeriodFilterSourceTests(unittest.TestCase):
    """Wiring the node run cannot see: the toolbar, the reset, the dispatch."""

    def setUp(self):
        self.js = crm_js()

    def test_chip_row_is_rendered_on_every_case_list(self):
        bar = fn_body(self.js, "tabBarHtml")
        self.assertTrue(bar, "tabBarHtml not found")
        self.assertIn("periodFilterTab()?", bar)
        self.assertIn("periodChipsHtml()", bar)
        self.assertIn("id=\"periodChips\"", bar)
        tabs = re.search(r"var PERIOD_FILTER_TABS=\[([^\]]*)\];", self.js)
        self.assertTrue(tabs, "PERIOD_FILTER_TABS not found")
        for tab in ("devices", "done_tab", "problem_tab", "closed_tab"):
            self.assertIn("'%s'" % tab, tabs.group(1))

    def test_one_resolver_reads_the_period_state(self):
        """Every reader asks periodFilterResolved, never the raw state."""
        js = strip_block_comments(self.js)
        # a read is anything that is not an assignment, so `===` still counts
        read = r"TAB_PERIOD_FILTER(?!\s*=(?!=))"
        in_resolver = re.findall(read, fn_body(js, "periodFilterResolved"))
        self.assertTrue(in_resolver, "the resolver must read the period state")
        self.assertEqual(len(re.findall(read, js)), len(in_resolver),
                         "TAB_PERIOD_FILTER must only be read by its resolver")

    def test_chip_click_rebuilds_the_stage_chips_in_place(self):
        body = fn_body(self.js, "tabPeriodSet")
        self.assertTrue(body, "tabPeriodSet not found")
        self.assertIn("TAB_PERIOD_FILTER=f", body)
        self.assertIn("tabStageEnsure()", body)
        self.assertIn("document.getElementById('stageChips')", body)
        self.assertIn("syncPeriodChips()", body)
        self.assertIn("renderBody()", body)
        self.assertIn("updateTabCount()", body)

    def test_chips_keep_the_in_place_sync_contract(self):
        # the toolbar is never re-rendered while the operator works in it
        body = fn_body(self.js, "syncPeriodChips")
        self.assertTrue(body, "syncPeriodChips not found")
        self.assertIn("'#periodChips .chipbtn'", body)
        self.assertIn("classList.toggle('active'", body)
        self.assertIn("getAttribute('data-f')", body)

    def test_reset_all_clears_the_period_filter_too(self):
        body = fn_body(self.js, "searchResetAll")
        self.assertIn("TAB_PERIOD_FILTER='all'", body)
        self.assertIn("syncPeriodChips()", body)

    def test_devices_base_set_comes_from_the_period_helper(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertIn("periodBaseDevices()", body)
        # the old inline active-case selection must not come back: it would
        # silently ignore a selected period.
        self.assertNotIn("isActiveCase", body)

    def test_the_filter_never_fetches(self):
        """The whole feature derives from the STATE the page already holds, so
        it can never disagree with the case list it links to."""
        for name in ("periodFilterResolved", "periodPass", "periodBaseDevices",
                     "periodChipsHtml", "filterDevices", "filterDevRows"):
            self.assertNotIn("api(", fn_body(self.js, name),
                             "%s must not fetch" % name)


if __name__ == "__main__":
    unittest.main()
