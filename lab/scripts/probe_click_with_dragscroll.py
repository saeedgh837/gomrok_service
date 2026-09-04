"""Does enabling drag-to-scroll break the robot's own clicking?

The chosen fix adds `Emulation.setEmitTouchEventsForMouse(mobile)` so the
operator can drag the page with the mouse like on a phone (proven 3/3 in
lab/scripts/probe_scroll_reliability.py). That setting turns mouse input into
touch input, which is exactly the input the robots use to click:

  * Selenium `element.click()` -> chromedriver Input.dispatchMouseEvent
  * gomrok.py's own JS chain    -> touchstart/touchend/pointer/mouse dispatch
  * `element.send_keys()`       -> requires focus from a click

If any of those regress, the setting is not acceptable at any price. Every click
path is exercised against a local page that records what it received.

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/probe_click_with_dragscroll.py
"""
import os
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

CHROMEDRIVER = r"C:\chromedriver.exe"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1")
WIN_W, WIN_H = 430, 932

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;font:16px sans-serif}
 #btn{height:56px;width:240px;margin:16px;font-size:18px}
 #txt{font-size:18px;padding:10px;margin:16px;width:240px}
 #chk{width:28px;height:28px;margin:16px}
 #log{font:13px monospace;white-space:pre-wrap;padding:8px}
 #tall{height:1200px;background:repeating-linear-gradient(#fff 0 96px,#eee 96px 192px)}
</style></head><body>
<button id="btn">CLICK ME</button>
<input id="txt" type="text" placeholder="type here">
<input id="chk" type="checkbox">
<div id="log"></div>
<div id="tall">tall area for scrolling</div>
<script>
 window.hits = {click:0, touchstart:0, pointerdown:0, mousedown:0, change:0};
 const log = document.getElementById('log');
 function note(k){ window.hits[k]=(window.hits[k]||0)+1;
   log.textContent = JSON.stringify(window.hits); }
 const b = document.getElementById('btn');
 ['click','touchstart','pointerdown','mousedown'].forEach(function(t){
   b.addEventListener(t, function(){ note(t); }, {passive:true});
 });
 document.getElementById('chk').addEventListener('change', function(){ note('change'); });
</script></body></html>"""


def build(drag_scroll):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={WIN_W},{WIN_H}")
    options.add_experimental_option("mobileEmulation", {
        "deviceMetrics": {"width": WIN_W, "height": 0,
                          "pixelRatio": 3.0, "mobile": True},
        "userAgent": UA})
    driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)
    if drag_scroll:
        driver.execute_cdp_cmd("Emulation.setEmitTouchEventsForMouse",
                               {"enabled": True, "configuration": "mobile"})
    return driver


FIRE_JS = r"""
  var b = document.getElementById('btn');
  var r = b.getBoundingClientRect();
  var cx = r.left + r.width/2, cy = r.top + r.height/2;
  function ev(type, C){ b.dispatchEvent(new C(type, {bubbles:true, cancelable:true,
    clientX:cx, clientY:cy, view:window})); }
  try{ var t = new Touch({identifier:1, target:b, clientX:cx, clientY:cy});
    b.dispatchEvent(new TouchEvent('touchstart', {bubbles:true, cancelable:true,
      touches:[t], targetTouches:[t], changedTouches:[t]}));
    b.dispatchEvent(new TouchEvent('touchend', {bubbles:true, cancelable:true,
      touches:[], targetTouches:[], changedTouches:[t]}));
  }catch(e){ return 'touch-ctor-failed: ' + e.message; }
  try{ ev('pointerdown', PointerEvent); ev('pointerup', PointerEvent); }catch(e){}
  ev('mousedown', MouseEvent); ev('mouseup', MouseEvent); ev('click', MouseEvent);
  return 'ok';
"""


def run(label, drag_scroll):
    path = os.path.join(tempfile.mkdtemp(prefix="irica-click-"), "p.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(PAGE)
    url = "file:///" + path.replace("\\", "/")

    driver = build(drag_scroll)
    try:
        driver.get(url)
        time.sleep(0.9)
        print(f"\n=== {label} ===")

        # 1. Selenium native click
        driver.find_element("id", "btn").click()
        time.sleep(0.4)
        print(f"  Selenium .click()        hits={driver.execute_script('return window.hits')}")

        # 2. gomrok.py's own JS event chain
        driver.execute_script("window.hits={click:0,touchstart:0,pointerdown:0,"
                              "mousedown:0,change:0};")
        out = driver.execute_script(FIRE_JS)
        time.sleep(0.4)
        print(f"  gomrok JS chain ({out})  hits={driver.execute_script('return window.hits')}")

        # 3. typing into a field (needs a working click/focus)
        field = driver.find_element("id", "txt")
        field.click()
        field.send_keys("09121234567")
        time.sleep(0.3)
        typed = driver.execute_script(
            "return document.getElementById('txt').value")
        print(f"  click + send_keys        value={typed!r} "
              f"{'OK' if typed == '09121234567' else 'BROKEN'}")

        # 4. checkbox toggle (the Wicket checkbox path)
        driver.find_element("id", "chk").click()
        time.sleep(0.3)
        checked = driver.execute_script(
            "return document.getElementById('chk').checked")
        print(f"  checkbox click           checked={checked} "
              f"{'OK' if checked else 'BROKEN'}")

        # 5. does scrollIntoView still work (used all over the robots)?
        driver.execute_script(
            "document.getElementById('tall').scrollIntoView({block:'center'});")
        time.sleep(0.4)
        y = driver.execute_script("return Math.round(window.scrollY)")
        print(f"  scrollIntoView           scrollY={y} "
              f"{'OK' if y > 0 else 'BROKEN'}")

        # 6. ActionChains click (offset click path)
        from selenium.webdriver.common.action_chains import ActionChains
        driver.execute_script("window.scrollTo(0,0); window.hits={click:0,"
                              "touchstart:0,pointerdown:0,mousedown:0,change:0};")
        time.sleep(0.3)
        ActionChains(driver).move_to_element(
            driver.find_element("id", "btn")).click().perform()
        time.sleep(0.4)
        print(f"  ActionChains click       hits={driver.execute_script('return window.hits')}")
    finally:
        driver.quit()


if __name__ == "__main__":
    run("WITHOUT drag-to-scroll (baseline)", False)
    run("WITH drag-to-scroll enabled", True)
