# -*- coding: utf-8 -*-
"""v1.9.1 — the inquiry result screenshot: store field, safe serving, icon.

The robots (estelam_gomrok.py / estelam_hamta.py) now capture the result
element at the verdict moment and write its path into estelam_store.json
under gomrok_screenshot / hamta_screenshot. This slice covers the panel side:

1. The field round-trips through estelam_set_device -> estelam_load (raw
   device dicts flow into __ESTELAM_JSON__, so the inquiry tab sees them
   without a whitelist).
2. /estelam-shot serves the PNG only when the recorded path resolves INSIDE
   the screenshots data subdir — an empty field, an unknown device, a bogus
   svc, or a path pointing outside (C:\\Windows, the repo) is a 404 and
   never serves a byte.
3. renderDevices() paints a camera icon per service only while the field is
   truthy, and the modal markup + showShotModal exist in the page.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_paths  # noqa: E402
import panel  # noqa: E402

PNG = bytes(bytearray([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A] + [0] * 24))

_NODE = {}


def _node_available():
    """True when a `node` binary is on PATH — probed once per run.

    The JS-engine tests skip only for a genuinely missing engine. An earlier
    harness turned every non-zero exit into a skip, which hid a stub the
    script under test could not run at all; a script that fails must fail its
    test.
    """
    if "ok" not in _NODE:
        try:
            proc = subprocess.run(["node", "--version"], capture_output=True,
                                  text=True, timeout=60)
            _NODE["ok"] = proc.returncode == 0
            _NODE["version"] = (proc.stdout or proc.stderr).strip()
        except (OSError, subprocess.SubprocessError):
            _NODE["ok"] = False
    return _NODE["ok"]


def _run_node(script):
    """Run a JS harness through node and return its stdout.

    Skips (only) when node is absent; a non-zero exit is an assertion failure
    that carries the engine's stderr, so a broken harness is visible instead
    of silently skipped.
    """
    if not _node_available():
        raise unittest.SkipTest("node is not installed on PATH")
    proc = subprocess.run(["node", "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise AssertionError(
            "node exited with %d\n--- stderr ---\n%s"
            % (proc.returncode, (proc.stderr or "").strip()[:600]))
    return proc.stdout


class IsolatedStoreMixin:
    """A temp estelam store + a temp DATA_DIR so the screenshots root is
    disposable too (the screenshots dir is derived from app_paths).

    Reusable: subclass it and keep every data-directory call after the env
    patch is active -- app_paths.data_subdir() CREATES its directory, so a
    call before the patch writes into the operator's live data dir.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._drop_tmp)
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        self.estelam_logs = os.path.join(self.tmp.name, "estelam_logs.jsonl")
        # Registered first, so it runs LAST -- after the env patch stopped.
        # The temp dir is deleted by then; its path must not stay in
        # app_paths' cache for whatever test runs next.
        self.addCleanup(app_paths.reset_cache)
        self._patches = [
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            # panel.estelam_log() writes here and the /estelam-shot negatives
            # log (unknown device, unknown service, an escaping path), so an
            # unpatched global appends to the operator's live
            # estelam_logs.jsonl. Computed at import -> patch it explicitly.
            patch.object(panel, "ESTELAM_LOGS_PATH", self.estelam_logs),
            patch.dict(os.environ, {"IRICA_DATA_DIR": self.tmp.name}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        app_paths.reset_cache()
        # resolve the screenshots dir against the temp DATA_DIR
        self.shot_dir = panel.app_paths.data_subdir("screenshots")

    def _drop_tmp(self):
        """Remove the temp tree, retrying briefly on a locked file.

        On Windows the tree holds the PNG /estelam-shot just served, and a
        scanner can hold that file for a moment after the response -- the
        route itself closes it inside `with open(...)`, so the lock is
        transient (milliseconds on an idle box, longer while a scan or a
        parallel test run is competing for the disk), and a bounded retry
        keeps the run reliable without silently leaving the tree behind."""
        deadline = time.time() + 15.0
        while True:
            try:
                self.tmp.cleanup()
                return
            except OSError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.05)

    def add_device(self, **fields):
        record = panel.estelam_add_device({"name": "آیفون",
                                           "serial1": "356938035643809"})
        if fields:
            panel.estelam_set_device(record["id"], **fields)
        return record


class FieldRoundTripTests(IsolatedStoreMixin, unittest.TestCase):
    def test_the_two_new_fields_round_trip_through_the_store(self):
        record = self.add_device()
        hamta_shot = os.path.join(self.shot_dir, "estelam_hamta_x_y.png")
        gomrok_shot = os.path.join(self.shot_dir, "estelam_gomrok_x_y.png")
        panel.estelam_set_device(record["id"], hamta_screenshot=hamta_shot)
        panel.estelam_set_device(record["id"], gomrok_screenshot=gomrok_shot)
        devices = panel.estelam_load()["devices"]
        self.assertEqual(devices[0]["hamta_screenshot"], hamta_shot)
        self.assertEqual(devices[0]["gomrok_screenshot"], gomrok_shot)

    def test_the_fields_reach_the_page_payload_verbatim(self):
        """render_estelam_page embeds estelam_load() raw; the inquiry tab's
        STATE must carry the paths (no whitelist strips them)."""
        record = self.add_device()
        shot = os.path.join(self.shot_dir, "estelam_hamta_a_b.png")
        panel.estelam_set_device(record["id"], hamta_screenshot=shot)
        html = panel.render_estelam_page().decode("utf-8")
        # the marker line is `STATE=<json>;` (the replaced placeholder)
        line = next(ln for ln in html.splitlines()
                    if ln.startswith("STATE={"))
        state = json.loads(line[len("STATE="):].rstrip(";"))
        device = state["devices"][0]
        self.assertEqual(device["hamta_screenshot"], shot)

    def test_an_old_row_without_the_fields_still_renders(self):
        """A row written before v1.9.1 (no screenshot keys) must not break the
        page render."""
        self.add_device()
        html = panel.render_estelam_page().decode("utf-8")
        self.assertNotIn("__ESTELAM_JSON__", html)


class RouteContainmentTests(IsolatedStoreMixin, unittest.TestCase):
    """The route is exercised over real HTTP (the same gate + dispatch chain
    the browser hits), with a temp screenshots root."""

    def setUp(self):
        super().setUp()
        from lab.tests import _auth_harness
        self.token = _auth_harness.seed_test_users()
        httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        req.add_header("Cookie", f"irica_session={self.token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_a_valid_shot_is_served_as_png(self):
        shot = os.path.join(self.shot_dir, "estelam_hamta_a_b.png")
        with open(shot, "wb") as handle:
            handle.write(PNG)
        record = self.add_device(hamta_screenshot=shot)
        status, body = self.get(
            f"/estelam-shot?id={record['id']}&svc=hamta")
        self.assertEqual(status, 200)
        self.assertEqual(body[:4], b"\x89PNG")

    def test_an_empty_field_is_a_404(self):
        record = self.add_device()      # no screenshot recorded
        self.assertEqual(
            self.get(f"/estelam-shot?id={record['id']}&svc=hamta")[0], 404)

    def test_an_unknown_device_is_a_404(self):
        shot = os.path.join(self.shot_dir, "estelam_hamta_x.png")
        with open(shot, "wb") as handle:
            handle.write(PNG)
        self.assertEqual(self.get(f"/estelam-shot?id=nope&svc=hamta")[0], 404)

    def test_an_unknown_service_is_a_404(self):
        record = self.add_device()
        self.assertEqual(
            self.get(f"/estelam-shot?id={record['id']}&svc=other")[0], 404)

    def test_a_path_outside_the_screenshots_dir_is_refused(self):
        """A recorded path that escapes the screenshots root (a stray value,
        or a traversal) must never be opened. The file EXISTS here — the
        refusal is purely the containment check."""
        outside = os.path.join(self.tmp.name, "outside.png")
        with open(outside, "wb") as handle:
            handle.write(PNG)
        record = self.add_device(gomrok_screenshot=outside)
        self.assertEqual(
            self.get(f"/estelam-shot?id={record['id']}&svc=gomrok")[0], 404)
        # an absolute system path is outside the data dir too
        record2 = self.add_device(hamta_screenshot=r"C:\Windows\win.ini")
        self.assertEqual(
            self.get(f"/estelam-shot?id={record2['id']}&svc=hamta")[0], 404)
        # and a repo-relative path (the handler serves nothing relative)
        record3 = self.add_device(gomrok_screenshot="panel.py")
        self.assertEqual(
            self.get(f"/estelam-shot?id={record3['id']}&svc=gomrok")[0], 404)

    def test_a_missing_file_on_disk_is_a_404(self):
        """The field points at a file the operator already cleaned up."""
        shot = os.path.join(self.shot_dir, "estelam_gomrok_gone.png")
        record = self.add_device(gomrok_screenshot=shot)   # file never written
        self.assertEqual(
            self.get(f"/estelam-shot?id={record['id']}&svc=gomrok")[0], 404)

    def test_a_directory_is_not_a_file(self):
        record = self.add_device(gomrok_screenshot=self.shot_dir)
        self.assertEqual(
            self.get(f"/estelam-shot?id={record['id']}&svc=gomrok")[0], 404)


def page_js():
    """The ESTELAM_PAGE script body (source-level contract tests)."""
    with open(panel.__file__, encoding="utf-8") as handle:
        src = handle.read()
    page = src[src.index("ESTELAM_PAGE = "):src.index("CRM_PAGE = ")]
    return page.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def rendered_page_html():
    with open(panel.__file__, encoding="utf-8") as handle:
        src = handle.read()
    return src[src.index("ESTELAM_PAGE = "):src.index("CRM_PAGE = ")]


def _strip_function(js, name):
    """Remove a whole `function name(...){...}` block by brace matching."""
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not match:
        return js
    depth, i = 1, match.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[:match.start()] + js[i:]


def _fn_body(js, name):
    """The whole `function name(...){...}` block, by brace matching ("" when
    the function is gone)."""
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


class PageContractTests(unittest.TestCase):
    def test_the_camera_icon_exists_in_the_page_icon_set(self):
        js = page_js()
        self.assertIn("camera:icsvg(", js)
        # the icon is the same 24-grid stroke-2 language as its siblings
        self.assertIn('viewBox="0 0 24 24"', js)

    def test_the_shot_modal_markup_and_helpers_exist(self):
        page = rendered_page_html()
        self.assertIn('id="shot-modal"', page)
        self.assertIn("function showShotModal(", page)
        self.assertIn("function closeShotModal()", page)
        self.assertIn("/estelam-shot?id=", page)
        self.assertIn("encodeURIComponent(", page)

    def test_the_icon_button_only_renders_when_the_field_is_truthy(self):
        """The source half of the guard: each status-grid slot asks shotBtn()
        for a button, and the helper keys its check on the device's
        per-service capture field, so an old row (no capture recorded) has
        nothing to paint. Both branches are proved against the rendered DOM in
        RenderDevicesIconTests -- no exact expression text is pinned here, so
        rewriting the same behaviour does not fail this test."""
        js = page_js()
        self.assertIn("hamta_screenshot", js)
        self.assertIn("gomrok_screenshot", js)
        self.assertIn("showShotModal(", js)
        grid = _fn_body(js, "renderDevices")
        self.assertIn("shotBtn('hamta')", grid)
        self.assertIn("shotBtn('gomrok')", grid)
        helper = _fn_body(js, "shotBtn")
        # the helper reads the field named after the service it was asked for
        # and bails out with no markup when the device does not carry it
        self.assertIn("hamta_screenshot", helper)
        self.assertIn("gomrok_screenshot", helper)
        self.assertIn("return ''", helper)


class RenderDevicesIconTests(unittest.TestCase):
    """Render the JS against two synthetic STATE payloads in a JS engine and
    inspect the produced DOM. Node is present in this environment (the
    existing estelam-service test uses it), so the guard is exercised for
    real rather than asserted at the source level."""

    def _render(self, devices):
        js = page_js()
        # the template markers are replaced exactly as the server does it
        js = js.replace("__ESTELAM_JSON__",
                        json.dumps({"person": {}, "devices": devices,
                                    "runtime": {}}, ensure_ascii=False))
        js = js.replace("__STATUS_JSON__",
                        json.dumps(panel.ESTELAM_STATUS_FA, ensure_ascii=False))
        js = js.replace("__BADGE_JSON__",
                        json.dumps(panel.ESTELAM_BADGE_FA, ensure_ascii=False))
        js = js.replace("__VERSION__", "test")
        # The page script's boot tail calls DOM/timer helpers node has no
        # implementation for (no document, no setInterval). Every function
        # that touches them is removed wholesale by brace-matching, and the
        # boot calls that invoked them are reaped, so what is left still
        # parses and renderDevices() runs against the stub document below.
        for name in ("renderLogFilters", "setLogFilter", "renderLogs",
                     "paintLogs", "notifyNewLogs", "pushLogNotif",
                     "checkApiHealth", "renderRuntime", "initTheme",
                     "initWrapW", "fillPerson", "applyTheme", "setTheme",
                     "toggleTheme", "setWrapW", "updateNotifToggle",
                     "hideToast", "showToast"):
            js = _strip_function(js, name)
        js = re.sub(r"document\.addEventListener\([^\n]*\)\s*;", "", js)
        js = re.sub(r"setInterval\([^\n]*\)\s*;", "", js)
        boot = ("initTheme();", "initWrapW();",
                "renderLogFilters();updateNotifToggle();renderLogs();",
                "checkApiHealth();")
        for call in boot:
            js = js.replace(call, "")
        js = re.sub(r"^fillPerson\(\);renderDevices\(\);renderRuntime\(STATE"
                    r"\.runtime\|\|\{\}\);", "renderDevices();",
                    js, flags=re.MULTILINE)
        harness = (
            "var __out='';"
            "var document={documentElement:{setAttribute:function(){},"
            "style:{setProperty:function(){},getPropertyValue:function(){"
            "return '';}},clientWidth:1280},body:{appendChild:function(){},"
            "removeChild:function(){},style:{}},"
            "createElement:function(tag){return {tagName:String(tag||'')"
            ".toUpperCase(),click:function(){},style:{},classList:{add:"
            "function(){},remove:function(){}},setAttribute:function(){},"
            "appendChild:function(){},removeChild:function(){},"
            "addEventListener:function(){}};},"
            "getElementById:function(id){"
            "var stub={innerHTML:'',value:'',classList:{add:function(){},"
            "remove:function(){},toggle:function(){}},textContent:'',style:{},"
            "addEventListener:function(){},setAttribute:function(){},"
            "appendChild:function(){},querySelector:function(){return null;},"
            "querySelectorAll:function(){return [];},checked:false,"
            "hidden:true};"
            "if(id==='dev-list'){Object.defineProperty(stub,'innerHTML',"
            "{get:function(){return __out;},set:function(v){__out=String(v);}});}"
            "return stub;},"
            "addEventListener:function(){},"
            "querySelectorAll:function(){return [];},querySelector:function(){"
            "return null;},title:''};\n"
            "var window=this,location={href:'http://127.0.0.1/'},"
            "screen={width:1920},addEventListener=function(){};\n"
            "window.addEventListener=function(){};"
            "window.localStorage=localStorage;\n"
            "var fetch=function(){return Promise.resolve({ok:true,json:"
            "function(){return Promise.resolve({logs:[]});},text:function(){"
            "return Promise.resolve('');}});};\n"
            "var setInterval=function(){return 0;},clearInterval=function(){};\n"
            "var setTimeout=function(){return 0;},clearTimeout=function(){};\n"
            "var requestAnimationFrame=function(){return 0;};\n"
            "var localStorage={getItem:function(){return null;},"
            "setItem:function(){},removeItem:function(){}};\n"
            "var navigator={clipboard:null,userAgent:'node'};\n"
            "var SVC='all';\n"
            + js +
            "\nrenderDevices();\n"
            "process.stdout.write(JSON.stringify({out:__out}));\n"
        )
        out = _run_node(harness)
        return json.loads(out)["out"]

    def test_the_camera_icon_is_painted_when_the_field_is_present(self):
        shot = os.path.join(os.sep, "shots", "estelam_hamta_a_b.png")
        out = self._render([{"id": "d1", "name": "آیفون",
                             "serial1": "356938035643809", "serial2": "",
                             "status": "healthy", "hamta_status": "good",
                             "hamta_screenshot": shot,
                             "gomrok_screenshot": ""}])
        self.assertIn('onclick="showShotModal(\'d1\',\'hamta\')"', out)
        # the button is an icon button with the camera svg (lens circle)
        self.assertIn('class="ibtn sh"', out)
        # the real button carries title + aria-label (not data-tip); both spell
        # the label with U+0654 (hamza above), copied from panel.py
        self.assertIn('title="مشاهدهٔ عکس نتیجهٔ همتا"', out)
        self.assertIn('aria-label="مشاهدهٔ عکس نتیجهٔ همتا"', out)
        self.assertIn('<circle cx="12" cy="13" r="4"/>', out)
        # the gomrok slot stayed empty: one icon, not two
        self.assertEqual(out.count("showShotModal("), 1)
        # it sits inside the status grid, not in a cell of its own
        self.assertIn('class="shot-w"', out)

    def test_no_camera_button_when_both_fields_are_absent(self):
        out = self._render([{"id": "d2", "name": "نوکیا",
                             "serial1": "356938035643810", "serial2": "",
                             "status": "pending", "hamta_status": "pending"}])
        self.assertNotIn("showShotModal(", out)
        self.assertNotIn("ibtn sh", out)
        self.assertNotIn("shot-w", out)


def _modal_helpers_and_stub():
    """The icsvg + modal helpers plus a faithful-enough DOM stub.

    The stub's appendChild registers an appended child by its id, the way a
    real document does, so onerror removing the <img> makes the next
    getElementById('shot-modal-img') return null and the recreate branch is
    actually taken."""
    with open(panel.__file__, encoding="utf-8") as handle:
        src = handle.read()
    js = src[src.index("<script>", src.index("ESTELAM_PAGE = ")):]
    js = js[js.index("<script>") + len("<script>"):js.index("</script>")]
    head = js[js.index("function icsvg("):js.index("var SHOT_URL=")]
    tail = js[js.index("var SHOT_URL="):].split("/* chips-e gomrok")[0]
    stub = (
        "var store={},toast='';\n"
        "function mk(id){var node={__kids:[],childNodes:[],id:id,src:'',"
        "style:{display:''},classList:{_c:[],add:function(c){this._c.push(c);},"
        "remove:function(c){this._c=this._c.filter(function(x){"
        "return x!==c;});},contains:function(c){"
        "return this._c.indexOf(c)>=0;}},_text:'',_html:'',parentNode:null,"
        "addEventListener:function(){}};"
        "Object.defineProperty(node,'textContent',{get:function(){"
        "return this._text;},set:function(v){while(this.childNodes.length)"
        "this.removeChild(this.childNodes[0]);this._text=String(v);}});"
        "Object.defineProperty(node,'innerHTML',{get:function(){return this._html;},"
        "set:function(v){while(this.childNodes.length)"
        "this.removeChild(this.childNodes[0]);this._text='';this._html=String(v);}});"
        "node.appendChild=function(c){this.__kids.push(c);this.childNodes.push(c);"
        "c.parentNode=this;if(c.id){store[c.id]=c;}return c;};"
        "node.removeChild=function(c){var i=this.childNodes.indexOf(c);"
        "if(i>=0){this.childNodes.splice(i,1);this.__kids.splice(i,1);}"
        "if(c.id&&store[c.id]===c)delete store[c.id];c.parentNode=null;return c;};"
        "return node;};\n"
        "function el(id){return id in store?store[id]:null;}\n"
        "var document={documentElement:{setAttribute:function(){},style:{"
        "setProperty:function(){},getPropertyValue:function(){return '';}},"
        "clientWidth:1280},body:mk('body'),"
        "createElement:function(tag){var n=mk('');n.tagName=String(tag||'')"
        ".toUpperCase();n.__clicks=0;n.click=function(){n.__clicks++;"
        "window.__clicked=n;};window.__created.push(n);return n;},"
        "getElementById:el,"
        "addEventListener:function(){},querySelectorAll:function(){return [];"
        "},querySelector:function(){return null;},title:''};\n"
        "var window=this,location={href:'http://x/'},screen={width:1920},"
        "addEventListener=function(){};window.addEventListener=function(){};"
        "window.__created=[];window.__clicked=null;"
        "window.open=function(u){window.__opened=u;};\n"
        "var localStorage={getItem:function(){return null;},setItem:"
        "function(){},removeItem:function(){}};\n"
        "var navigator={clipboard:null,userAgent:'node'};\n"
        "var fetch=function(){return Promise.resolve({ok:true,json:function()"
        "{return Promise.resolve({});},text:function(){return Promise.resolve("
        "'');}});};\n"
        "var setInterval=function(){return 0;},clearInterval=function(){},"
        "setTimeout=function(){return 0;},clearTimeout=function(){};\n"
        "var showToast=function(ok,msg){toast=msg;};\n"
        "['shot-modal','shot-modal-title','shot-modal-sn','shot-modal-body',"
        "'shot-modal-img','shot-modal-dl','shot-modal-dl-ic'].forEach("
        "function(i){store[i]=mk(i);});"
        "store['shot-modal-body'].appendChild(store['shot-modal-img']);\n"
    )
    return stub, head, tail


class ShotModalBehaviourTests(unittest.TestCase):
    """showShotModal against a stub DOM: the open/close contract, and the
    onerror state machine (a modal whose picture failed to load must be
    reusable)."""

    def _run(self, probe):
        stub, head, tail = _modal_helpers_and_stub()
        state = {"person": {}, "devices": [
            {"id": "d1", "serial1": "356938035643809", "serial2": "",
             "hamta_screenshot": "/shots/a.png", "gomrok_screenshot": ""}]}
        boot = "var STATE=%s;" % json.dumps(state, ensure_ascii=False)
        src = stub + boot + head + "\n" + tail + "\n" + probe
        return _run_node(src).strip()

    def test_open_sets_the_title_serials_and_url(self):
        out = self._run(
            "showShotModal('d1','hamta');\n"
            "console.log(JSON.stringify({"
            "shown:store['shot-modal'].classList.contains('show'),"
            "title:store['shot-modal-title'].textContent,"
            "sn:store['shot-modal-sn'].textContent,"
            "src:store['shot-modal-img'].src,"
            "dl_icon:store['shot-modal-dl-ic'].innerHTML.indexOf('<svg')>=0,"
            "toast:toast}));\n"
            "closeShotModal();\n"
            "console.log('closed:'+!store['shot-modal']."
            "classList.contains('show'));\n")
        payload = json.loads(out.splitlines()[0])
        self.assertTrue(payload["shown"])
        self.assertEqual(payload["title"], "عکس نتیجهٔ استعلام — همتا")
        self.assertEqual(payload["sn"], "356938035643809")
        self.assertRegex(payload["src"],
                         r"^/estelam-shot\?id=d1&svc=hamta&cb=\d+$")
        self.assertTrue(payload["dl_icon"])
        self.assertEqual(payload["toast"], "")
        self.assertEqual(out.splitlines()[-1], "closed:true")

    def test_a_missing_field_toasts_and_stays_closed(self):
        out = self._run(
            "showShotModal('d1','gomrok');\n"
            "console.log(JSON.stringify({toast:toast,"
            "shown:store['shot-modal'].classList.contains('show')}));\n")
        payload = json.loads(out)
        self.assertEqual(payload["toast"],
                         "عکس نتیجهٔ این استعلام موجود نیست")
        self.assertFalse(payload["shown"])

    def test_download_saves_the_served_url_through_a_temporary_anchor(self):
        """shotDownload() builds a temporary <a download> and clicks it: the
        route answers image/png with no Content-Disposition, so window.open
        would only render the picture in a new tab. The anchor must carry the
        served URL and the per-device file name, be clicked exactly once and
        be taken back out of the document."""
        out = self._run(
            "showShotModal('d1','hamta');shotDownload();\n"
            "var a=null;window.__created.forEach(function(n){"
            "if(n.tagName==='A')a=n;});\n"
            "console.log(JSON.stringify({"
            "href:a?a.href:null,download:a?a.download:null,"
            "clicked:a?a.__clicks:0,"
            "still_attached:a?a.parentNode!==null:true,"
            "body_children:document.body.childNodes.length,"
            "opened:window.__opened===undefined}));\n")
        payload = json.loads(out)
        self.assertRegex(payload["href"],
                         r"^/estelam-shot\?id=d1&svc=hamta&cb=\d+$")
        self.assertEqual(payload["download"], "estelam-hamta-d1.png")
        self.assertEqual(payload["clicked"], 1)
        self.assertFalse(payload["still_attached"])
        self.assertEqual(payload["body_children"], 0)
        # the old window.open path is gone for good
        self.assertTrue(payload["opened"])

    def test_onerror_text_is_cleared_and_the_img_recreated_on_reopen(self):
        """onerror replaces the body with the Persian failure text, which
        destroys the <img>. Reopening must rebuild it, clear the stale text
        and re-arm the download button, or every open after a failure would
        show «عکس بارگذاری نشد» forever."""
        out = self._run(
            "showShotModal('d1','hamta');\n"
            "var first=document.getElementById('shot-modal-img');\n"
            "first.onerror.call(first);          // simulate a 404\n"
            "console.log(JSON.stringify({"
            "err_text:store['shot-modal-body'].textContent.trim(),"
            "img_removed:document.getElementById('shot-modal-img')===null,"
            "dl_hidden:store['shot-modal-dl'].style.display}));\n"
            "closeShotModal();\n"
            "showShotModal('d1','hamta');\n"
            "console.log(JSON.stringify({"
            "img_back:document.getElementById('shot-modal-img')!==null,"
            "stale_text_cleared:"
            "store['shot-modal-body'].textContent.trim()==='',"
            "src:document.getElementById('shot-modal-img').src,"
            "dl_restored:store['shot-modal-dl'].style.display===''"
            "}));\n")
        failed = json.loads(out.splitlines()[0])
        self.assertEqual(failed["err_text"],
                         "عکس بارگذاری نشد — فایل پیدا نیست")
        self.assertTrue(failed["dl_hidden"] == "none")
        reopened = json.loads(out.splitlines()[-1])
        self.assertTrue(reopened["img_back"])
        self.assertTrue(reopened["stale_text_cleared"])
        self.assertRegex(reopened["src"],
                         r"^/estelam-shot\?id=d1&svc=hamta&cb=\d+$")
        self.assertTrue(reopened["dl_restored"])


if __name__ == "__main__":
    unittest.main()
