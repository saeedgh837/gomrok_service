"""Prove the exact failure mode the operator describes, with numbers.

Hypothesis: `mobileEmulation.deviceMetrics = 430x932` forces a LAYOUT VIEWPORT
taller than the browser window can display (window 932 px tall - 151 px of window
chrome = 781 CSS px visible). For a page shorter than 932 CSS px -- the gomrok
login page is one -- the browser sees no overflow, so:

  * scrollHeight == clientHeight  -> nothing scrolls, no scrollbar, wheel is a
    no-op, and there is no way to bring the lower part into view;
  * the bottom 151 CSS px of the viewport sit outside the window and are
    therefore permanently invisible.

Two page heights are measured against two viewport configurations, so the effect
is attributable to the configuration and not to the page.

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/diagnose_mobile_scroll.py
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

PROBE_JS = """
return {
  innerW: window.innerWidth, innerH: window.innerHeight,
  dpr: window.devicePixelRatio,
  scrollH: document.documentElement.scrollHeight,
  clientH: document.documentElement.clientHeight,
  maxScroll: Math.max(0, document.documentElement.scrollHeight - window.innerHeight),
  touchPoints: navigator.maxTouchPoints
};
"""


def page(height_px, label):
    """A page `height_px` CSS px tall whose LAST element is a form control."""
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{margin:0;font:16px sans-serif}"
        f"#filler{{height:{height_px - 80}px;background:linear-gradient(#eee,#bbb)}}"
        "#submit{height:60px;width:200px;margin:10px;font-size:20px}</style>"
        f"</head><body><div id='filler'>{label}</div>"
        "<button id='submit'>SUBMIT (last element)</button></body></html>"
    )
    path = os.path.join(tempfile.mkdtemp(prefix="irica-scroll-"), "p.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return "file:///" + path.replace("\\", "/")


def measure(driver, url, visible_h):
    driver.get(url)
    time.sleep(0.8)
    state = driver.execute_script(PROBE_JS)

    driver.execute_script("window.scrollTo(0, 99999);")
    time.sleep(0.4)
    state["scrollY_after_scroll_to_bottom"] = driver.execute_script(
        "return Math.round(window.scrollY)")

    # Where does the LAST control end up, at best effort, relative to the part of
    # the viewport the window can actually show?
    box = driver.execute_script(
        "const r = document.getElementById('submit').getBoundingClientRect();"
        "return {top: Math.round(r.top), bottom: Math.round(r.bottom)};")
    state["submit_box_when_scrolled"] = box
    state["submit_reachable"] = box["bottom"] <= visible_h
    driver.execute_script("window.scrollTo(0, 0);")
    return state


def main():
    import captcha_collector as cc

    win_w = cc.MOBILE_METRICS["width"]
    win_h = cc.MOBILE_METRICS["height"]
    print("=== project settings ===")
    print(f"MOBILE_METRICS = {cc.MOBILE_METRICS}   SITE_ZOOM = {cc.SITE_ZOOM}")

    # ---- step 1: how much page can this window really show? ----------------
    saved = cc.MOBILE_EMULATION
    cc.MOBILE_EMULATION = False
    ref = cc.build_chrome(None)
    try:
        ref.set_window_rect(x=10, y=10, width=win_w, height=win_h)
        time.sleep(0.6)
        ref.get("about:blank")
        real = ref.execute_script(PROBE_JS)
        rect = ref.get_window_rect()
    finally:
        ref.quit()
        cc.MOBILE_EMULATION = saved

    visible_h = real["innerH"]
    chrome_h = rect["height"] - visible_h
    print("\n=== step 1: real content area of the robot's window ===")
    print(f"requested window : {win_w}x{win_h}")
    print(f"actual window    : {rect['width']}x{rect['height']}  "
          f"(Chrome enforces a minimum width)")
    print(f"window chrome    : {chrome_h} px tall")
    print(f"VISIBLE page area: {real['innerW']}x{visible_h} CSS px")

    # ---- step 2: emulation ON, one short page and one tall page ------------
    print("\n=== step 2: emulation ON (current behaviour) ===")
    driver = cc.build_driver(cc.USER_AGENTS[0])
    try:
        for h, label in ((900, "SHORT page (900px, like the login page)"),
                         (2400, "TALL page (2400px)")):
            state = measure(driver, page(h, label), visible_h)
            print(f"\n  {label}")
            print(f"    emulated viewport : {state['innerW']}x{state['innerH']}")
            print(f"    document height   : {state['scrollH']}")
            print(f"    max scrollable    : {state['maxScroll']}"
                  f"{'   <-- NOTHING TO SCROLL' if state['maxScroll'] == 0 else ''}")
            print(f"    scrollY at bottom : {state['scrollY_after_scroll_to_bottom']}")
            print(f"    last control box  : {state['submit_box_when_scrolled']} "
                  f"(window shows 0..{visible_h})")
            print(f"    reachable by eye  : {state['submit_reachable']}"
                  f"{'   <-- INVISIBLE, UNREACHABLE' if not state['submit_reachable'] else ''}")
    finally:
        driver.quit()

    # ---- step 3: the same pages with the viewport SIZED TO THE WINDOW ------
    print(f"\n=== step 3: same window, deviceMetrics height = {visible_h} "
          f"(the fix under test) ===")
    cc.MOBILE_EMULATION = False
    driver = cc.build_chrome(None)
    try:
        driver.set_window_rect(x=10, y=10, width=win_w, height=win_h)
        time.sleep(0.5)
        driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": win_w, "height": visible_h,
            "deviceScaleFactor": cc.MOBILE_METRICS["pixelRatio"],
            "mobile": True})
        driver.execute_cdp_cmd("Emulation.setUserAgentOverride",
                               {"userAgent": cc.USER_AGENTS[0]})
        driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled",
                               {"enabled": True, "maxTouchPoints": 1})
        for h, label in ((900, "SHORT page (900px)"), (2400, "TALL page (2400px)")):
            state = measure(driver, page(h, label), visible_h)
            print(f"\n  {label}")
            print(f"    emulated viewport : {state['innerW']}x{state['innerH']}")
            print(f"    max scrollable    : {state['maxScroll']}")
            print(f"    last control box  : {state['submit_box_when_scrolled']} "
                  f"(window shows 0..{visible_h})")
            print(f"    reachable by eye  : {state['submit_reachable']}")
    finally:
        driver.quit()
        cc.MOBILE_EMULATION = saved


if __name__ == "__main__":
    main()
