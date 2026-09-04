"""Live check: manual review is possible in the robot's real browser window.

Runs the ACTUAL driver builders the robots use (`captcha_collector.build_driver`
and `estelam_hamta.build_driver`) against local pages, then measures with real OS
mouse input.

Two pages are needed, because the gomrok driver applies `html{zoom:0.75}` to the
site: a 932 CSS px document renders as ~699 effective px there, which FITS in the
781 px viewport and therefore has -- correctly -- nothing to scroll. Judging that
path by "is there a scroll range" would be wrong. So:

  * SHORT page (932 px, the shape that used to be clipped) -> assert that every
    part of it is REACHABLE: either it fits, or it can be scrolled to.
  * TALL page (2400 px) -> assert that real OS wheel, real OS drag and
    `scroll_page()` all actually move it.

Invariants asserted on both robots: viewport width 430 CSS px, dpr 3, mobile UA,
screenshot scale 3.0 (the captcha cropper divides by this), and the emulated
viewport never taller than the window can show.

No network: local files stand in for the site. Chrome + chromedriver required.

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/live_check_manual_scroll.py
    ... live_check_manual_scroll.py gomrok
    ... live_check_manual_scroll.py hamta
"""
import base64
import ctypes
import io
import os
import sys
import tempfile
import time
from ctypes import wintypes

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

CHECKS = []

_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{{margin:0}}
 #top{{height:60px;background:#2b6;color:#fff;font:20px sans-serif;padding:8px}}
 #mid{{height:{mid}px;background:repeating-linear-gradient(#fff 0 96px,#eee 96px 192px)}}
 #bottom{{height:80px;background:#b22;color:#fff;font:22px sans-serif;padding:8px}}
 img.captchaImage{{width:200px;height:60px;background:#333}}
</style></head><body>
<div id="top">TOP MARKER</div>
<div id="mid">middle<img class="captchaImage"></div>
<div id="bottom">BOTTOM MARKER</div>
</body></html>"""

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
INPUT_MOUSE = 0
SM_CXSCREEN = 0
SM_CXVIRTUALSCREEN = 78


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


def ck(name, cond, extra=""):
    CHECKS.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (f"   | {str(extra)[:110]}" if extra else ""))


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


def page(mid_height):
    directory = tempfile.mkdtemp(prefix="irica-live-scroll-")
    path = os.path.join(directory, f"p{mid_height}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_TEMPLATE.format(mid=mid_height))
    return "file:///" + path.replace("\\", "/")


def scroll_y(driver):
    return driver.execute_script("return Math.round(window.scrollY)")


def geometry(driver):
    return driver.execute_script(
        "return {w: window.innerWidth, h: window.innerHeight,"
        " dpr: window.devicePixelRatio,"
        " mobileUA: /iPhone/.test(navigator.userAgent),"
        " scrollH: document.documentElement.scrollHeight,"
        " maxScroll: Math.max(0, document.documentElement.scrollHeight"
        " - window.innerHeight)}")


def content_area(driver, url):
    """True window content area in CSS px; re-navigates to restore emulation."""
    driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
    time.sleep(0.3)
    out = driver.execute_script("return {w: window.innerWidth, h: window.innerHeight}")
    driver.get(url)
    time.sleep(0.9)
    return out["w"], out["h"]


def shot(driver):
    from PIL import Image
    return Image.open(io.BytesIO(base64.b64decode(driver.get_screenshot_as_base64())))


def usable_point(driver, hwnd, tall_url):
    """A screen point where a real OS wheel demonstrably reaches the page.

    Always-on-top windows (the Hermes desktop overlay, for one) can swallow
    synthetic input, so the wheel itself is the gate. Candidates span BOTH
    monitors, since an overlay on one display leaves the other usable. The TALL
    page is used so there is definitely something to scroll.
    """
    import browser_window as bw
    user = u32()
    positions = [(60, 40), (900, 40), (1200, 200), (60, 300)]
    primary_w = user.GetSystemMetrics(SM_CXSCREEN)
    if user.GetSystemMetrics(SM_CXVIRTUALSCREEN) > primary_w:
        positions = [(primary_w + 120, 80), (primary_w + 700, 80)] + positions
    for x, y in positions:
        driver.set_window_rect(x=x, y=y, width=430, height=932)
        time.sleep(0.5)
        if hwnd:
            bw.show_window(hwnd, (x, y))
            time.sleep(0.8)
        driver.get(tall_url)
        time.sleep(0.8)
        rect = driver.get_window_rect()
        cx = rect["x"] + rect["width"] // 2
        cy = rect["y"] + rect["height"] // 2
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
        os_wheel(cx, cy)
        time.sleep(0.8)
        if scroll_y(driver) > 0:
            return rect, cx, cy
        print(f"      (no OS input at {cx},{cy}; trying another position)")
    return None, None, None


def check(label, driver, short_url, tall_url):
    import browser_visibility as bvis
    import browser_window as bw
    import captcha_collector as cc

    print(f"\n=== {label} ===")
    driver.get(tall_url)
    time.sleep(1.0)
    hwnd = bw.find_browser_window(bvis.chromedriver_pid(driver))
    rect, cx, cy = usable_point(driver, hwnd, tall_url)
    if cx is None:
        ck(f"{label}: OS input reaches the window", False,
           "no usable point on either monitor")
        return

    real_w, real_h = content_area(driver, tall_url)

    # --- invariants + input, on the TALL page (guaranteed overflow) ----------
    tall = geometry(driver)
    scale = round(shot(driver).width / tall["w"], 3)
    ck(f"{label}: viewport fits inside the window (nothing clipped)",
       tall["h"] <= real_h + 2, f"viewport h={tall['h']}, window shows {real_h}")
    ck(f"{label}: mobile layout intact (width 430, dpr 3, mobile UA)",
       tall["w"] == 430 and tall["dpr"] == 3 and tall["mobileUA"], tall)
    ck(f"{label}: screenshot scale unchanged for the captcha cropper",
       scale == 3.0, f"scale={scale}")

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.3)
    os_wheel(cx, cy)
    time.sleep(0.8)
    wheel_y = scroll_y(driver)
    ck(f"{label}: real OS mouse WHEEL scrolls", wheel_y > 0, f"scrollY={wheel_y}")

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.3)
    os_drag(cx, cy + 180, -320)
    time.sleep(0.9)
    drag_y = scroll_y(driver)
    ck(f"{label}: real OS mouse DRAG scrolls (phone-like)", drag_y > 0,
       f"scrollY={drag_y}")

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.2)
    cc.scroll_page(driver, "down")
    time.sleep(0.4)
    down_y = scroll_y(driver)
    cc.scroll_page(driver, "up")
    time.sleep(0.4)
    up_y = scroll_y(driver)
    cc.scroll_page(driver, "bottom")
    time.sleep(0.4)
    bottom_y = scroll_y(driver)
    cc.scroll_page(driver, "top")
    time.sleep(0.4)
    top_y = scroll_y(driver)
    ck(f"{label}: scroll_page down/up/bottom/top",
       down_y > 0 and up_y < down_y and bottom_y >= down_y and top_y == 0,
       f"down={down_y} up={up_y} bottom={bottom_y} top={top_y}")

    # --- the reported bug, on the SHORT page --------------------------------
    # The real property is REACHABILITY: the bottom of a login-page-sized
    # document must end up fully inside the window, whether by fitting already
    # or by scrolling. (gomrok applies html{zoom:0.75}, so its short page fits
    # and needs no scroll; hamta has no zoom and scrolls 151 px.)
    driver.get(short_url)
    time.sleep(0.9)
    short = geometry(driver)
    box = driver.execute_script(
        "document.getElementById('bottom').scrollIntoView({block:'end'});"
        "const r = document.getElementById('bottom').getBoundingClientRect();"
        "return {top: Math.round(r.top), bottom: Math.round(r.bottom)};")
    ck(f"{label}: BOTTOM of a login-sized page is fully reachable",
       box["top"] >= 0 and box["bottom"] <= real_h + 2,
       f"{box} within 0..{real_h}; document={short['scrollH']} "
       f"viewport={short['h']} scrollRange={short['maxScroll']}")
    ck(f"{label}: nothing is left outside the window on the short page",
       short["h"] <= real_h + 2,
       f"viewport h={short['h']} vs window {real_h}")


def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
    short_url, tall_url = page(792), page(2400)

    if which in ("both", "gomrok"):
        import captcha_collector as cc
        driver = cc.build_driver(cc.USER_AGENTS[0])
        try:
            check("gomrok (captcha_collector.build_driver)", driver,
                  short_url, tall_url)
        finally:
            driver.quit()

    if which in ("both", "hamta"):
        import estelam_hamta as eh
        driver = eh.build_driver()
        try:
            check("hamta (estelam_hamta.build_driver)", driver,
                  short_url, tall_url)
        finally:
            driver.quit()

    print(f"\n=== {sum(CHECKS)}/{len(CHECKS)} checks passed ===")
    return 0 if CHECKS and all(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
