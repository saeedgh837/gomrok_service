"""Is drag-to-scroll safe to leave on permanently?

Two questions, both settled with real OS input:

  1. Does a genuine OS mouse CLICK still register as a click while
     `Emulation.setEmitTouchEventsForMouse(mobile)` is active? That setting turns
     mouse input into touch input, so a click could be swallowed as a swipe --
     which would break the operator's own manual interaction.
  2. Does the setting survive navigation, or must it be re-applied per page?

Two measurement problems had to be solved first, or every number here would be a
lie:

  * An always-on-top overlay can cover the screen, so `WindowFromPoint` is not a
    usable gate -- and it is too strict anyway, since click-through overlays still
    pass input to the window below. Instead a WHEEL acts as the canary: wheel
    scrolling is already known to work, so if the wheel moves the page, real input
    is reaching Chrome at that point.
  * CSS-pixel to screen-pixel mapping under mobile emulation is not 1:1 (a 430 px
    emulated viewport is stretched across a ~500 px content area), so computed
    click coordinates miss. The click target here covers the WHOLE page, which
    removes the mapping from the question entirely.

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/probe_dragscroll_safety.py
"""
import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

CHROMEDRIVER = r"C:\chromedriver.exe"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1")
WIN_W, WIN_H = 430, 932

# The click target covers the entire document, so any point inside the content
# area is a hit -- no CSS-px to screen-px conversion needed.
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0}
 #hit{height:1600px;background:repeating-linear-gradient(#fff 0 96px,#eee 96px 192px);
      font:20px sans-serif;padding:10px}
</style></head><body>
<div id="hit">click / drag target</div>
<script>
 window.clicks = 0; window.taps = 0;
 document.getElementById('hit').addEventListener('click', function(){ window.clicks++; });
 document.getElementById('hit').addEventListener('touchend', function(){ window.taps++; });
</script></body></html>"""

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
INPUT_MOUSE = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


def u32():
    return ctypes.WinDLL("user32", use_last_error=True)


def send(flags, data=0):
    inp = INPUT(type=INPUT_MOUSE,
                mi=MOUSEINPUT(0, 0, ctypes.c_int(data).value & 0xFFFFFFFF,
                              flags, 0, None))
    return u32().SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def os_wheel(x, y, clicks=-5):
    u32().SetCursorPos(int(x), int(y))
    time.sleep(0.2)
    send(MOUSEEVENTF_WHEEL, clicks * 120)


def os_click(x, y, jitter=0):
    user = u32()
    user.SetCursorPos(int(x), int(y))
    time.sleep(0.25)
    send(MOUSEEVENTF_LEFTDOWN)
    if jitter:
        user.SetCursorPos(int(x + jitter), int(y + jitter))
        time.sleep(0.04)
    time.sleep(0.08)
    send(MOUSEEVENTF_LEFTUP)


def os_drag(x, y, dy, steps=16):
    user = u32()
    user.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    send(MOUSEEVENTF_LEFTDOWN)
    for i in range(1, steps + 1):
        user.SetCursorPos(int(x), int(y + dy * i / steps))
        time.sleep(0.02)
    time.sleep(0.1)
    send(MOUSEEVENTF_LEFTUP)


def build(drag_scroll_at_build):
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
    if drag_scroll_at_build:
        driver.execute_cdp_cmd("Emulation.setEmitTouchEventsForMouse",
                               {"enabled": True, "configuration": "mobile"})
    return driver


def scroll_y(driver):
    return driver.execute_script("return Math.round(window.scrollY)")


def canary(driver, x, y):
    """Wheel test: proves OS input is reaching this window at this point."""
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.3)
    os_wheel(x, y)
    time.sleep(0.8)
    return scroll_y(driver) > 0


def usable_point(driver, hwnd, page_url):
    """Move the window until an OS wheel demonstrably scrolls the page."""
    import browser_window as bw
    for x, y in ((60, 40), (900, 40), (60, 300), (1200, 200)):
        driver.set_window_rect(x=x, y=y, width=WIN_W, height=WIN_H)
        time.sleep(0.5)
        if hwnd:
            bw.show_window(hwnd, (x, y))
            time.sleep(0.8)
        rect = driver.get_window_rect()
        cx = rect["x"] + rect["width"] // 2
        cy = rect["y"] + rect["height"] // 2
        if canary(driver, cx, cy):
            return rect, cx, cy
        print(f"  wheel canary failed at ({cx},{cy}); trying another position")
    return None, None, None


def main():
    import browser_visibility as bvis
    import browser_window as bw

    path = os.path.join(tempfile.mkdtemp(prefix="irica-safe-"), "p.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(PAGE)
    url = "file:///" + path.replace("\\", "/")

    driver = build(drag_scroll_at_build=True)
    try:
        driver.get(url)
        time.sleep(0.9)
        hwnd = bw.find_browser_window(bvis.chromedriver_pid(driver))

        print("=== 0. locate a point where OS input reaches the window ===")
        rect, cx, cy = usable_point(driver, hwnd, url)
        if cx is None:
            print("  OS input never reached the window -- cannot measure. Abort.")
            return
        print(f"  usable point ({cx},{cy}), window {rect}\n")

        print("=== 1. drag-to-scroll (applied at build time) ===")
        for attempt in range(1, 4):
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.3)
            os_drag(cx, cy + 180, -320)
            time.sleep(0.9)
            print(f"  attempt {attempt}: drag -> scrollY={scroll_y(driver)}")

        print("\n=== 2. does a real OS CLICK still register? ===")
        for jitter, label in ((0, "clean click"), (3, "3px jitter"),
                              (8, "8px jitter")):
            driver.execute_script("window.scrollTo(0,0); window.clicks=0; window.taps=0;")
            time.sleep(0.25)
            os_click(cx, cy, jitter=jitter)
            time.sleep(0.7)
            hits = driver.execute_script("return {c: window.clicks, t: window.taps}")
            print(f"  {label:<14} click={hits['c']} touchend={hits['t']}  "
                  f"{'OK' if hits['c'] >= 1 else 'MISSED'}")

        print("\n=== 3. persistence across navigation ===")
        for label, action in (("driver.get(same url)", lambda: driver.get(url)),
                              ("refresh()", driver.refresh),
                              ("cross-document nav", lambda: (
                                  driver.get("data:text/html,<h1>x</h1>"),
                                  time.sleep(0.5), driver.get(url)))):
            action()
            time.sleep(1.0)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.3)
            os_drag(cx, cy + 180, -320)
            time.sleep(0.9)
            print(f"  after {label:<22} drag -> scrollY={scroll_y(driver)}")
    finally:
        driver.quit()

    print("\n=== 4. applied AFTER navigation instead of at build time ===")
    driver = build(drag_scroll_at_build=False)
    try:
        driver.get(url)
        time.sleep(0.9)
        hwnd = bw.find_browser_window(bvis.chromedriver_pid(driver))
        rect, cx, cy = usable_point(driver, hwnd, url)
        if cx is None:
            print("  OS input never reached the window -- skip.")
            return
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
        os_drag(cx, cy + 180, -320)
        time.sleep(0.9)
        print(f"  before enabling  drag -> scrollY={scroll_y(driver)}")
        driver.execute_cdp_cmd("Emulation.setEmitTouchEventsForMouse",
                               {"enabled": True, "configuration": "mobile"})
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
        os_drag(cx, cy + 180, -320)
        time.sleep(0.9)
        print(f"  after  enabling  drag -> scrollY={scroll_y(driver)}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
