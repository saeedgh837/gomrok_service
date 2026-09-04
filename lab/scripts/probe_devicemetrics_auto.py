"""Decide WHERE the viewport fix belongs, and prove it survives a resize.

Two findings from lab/scripts/probe_devicemetrics_auto.py steer this:

  * chromedriver ACCEPTS `deviceMetrics.height = 0` in the `mobileEmulation`
    capability, and 0 means "do not override this axis" -- the emulated viewport
    then equals the real window content area.
  * a post-launch `Emulation.setDeviceMetricsOverride` is IGNORED when
    chromedriver owns the emulation (the capability wins), so the fix cannot be a
    CDP call after launch. It has to be in the capability.

`about:blank` is useless for measuring this: with no viewport meta tag, mobile
emulation applies the 980 CSS px fallback layout width, so every number is scaled
by 980/430. This probe uses a page WITH a viewport meta tag, like the real site.

Checks, for height 932 (current) vs height 0 (candidate):
  1. layout viewport == window content area?  (nothing clipped)
  2. a short page still has a scroll range?
  3. does a real wheel event scroll it?
  4. does the viewport FOLLOW a window resize?
  5. is the mobile layout intact (width 430, dpr 3, touch, mobile UA)?
  6. is the captcha screenshot scale unchanged?  (the cropper divides image
     width by viewport width -- a change here would break OCR)

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/probe_devicemetrics_auto.py
"""
import base64
import io
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

PAGE = ("<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{margin:0;font:16px sans-serif}"
        "#filler{height:820px;background:linear-gradient(#eee,#999)}"
        "#submit{height:60px;width:220px;margin:10px;font-size:20px}"
        "img.captchaImage{width:200px;height:60px;background:#333}</style></head>"
        "<body><div id='filler'>short page, like the login page</div>"
        "<img class='captchaImage'><button id='submit'>SUBMIT</button></body></html>")

PROBE = """
return {
  innerW: window.innerWidth, innerH: window.innerHeight,
  dpr: window.devicePixelRatio, touch: navigator.maxTouchPoints,
  mobileUA: /iPhone/.test(navigator.userAgent),
  scrollH: document.documentElement.scrollHeight,
  maxScroll: Math.max(0, document.documentElement.scrollHeight - window.innerHeight)
};
"""


def page_url():
    path = os.path.join(tempfile.mkdtemp(prefix="irica-dm-"), "p.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(PAGE)
    return "file:///" + path.replace("\\", "/")


def build(height):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={WIN_W},{WIN_H}")
    options.add_experimental_option("mobileEmulation", {
        "deviceMetrics": {"width": WIN_W, "height": height,
                          "pixelRatio": 3.0, "mobile": True},
        "userAgent": UA})
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)


def content_area(driver):
    """Real window content area in CSS px, read with emulation temporarily off."""
    driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
    time.sleep(0.3)
    out = driver.execute_script("return {w: window.innerWidth, h: window.innerHeight}")
    return out["w"], out["h"]


def shot_scale(driver):
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(driver.get_screenshot_as_base64())))
    vw = driver.execute_script("return window.innerWidth")
    return round(img.width / vw, 3), img.size


def run(label, height, url):
    print(f"\n=== {label}: mobileEmulation deviceMetrics height = {height} ===")
    driver = build(height)
    try:
        driver.get(url)
        time.sleep(0.9)
        s = driver.execute_script(PROBE)
        scale, img = shot_scale(driver)

        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.2)
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": 120, "y": 300, "deltaX": 0, "deltaY": 600})
        time.sleep(0.6)
        wheel = driver.execute_script("return Math.round(window.scrollY)")

        driver.execute_script(
            "document.getElementById('submit').scrollIntoView({block:'center'});")
        time.sleep(0.4)
        box = driver.execute_script(
            "const r=document.getElementById('submit').getBoundingClientRect();"
            "return {top: Math.round(r.top), bottom: Math.round(r.bottom)};")

        before_h = s["innerH"]
        driver.maximize_window()
        time.sleep(1.2)
        after = driver.execute_script(PROBE)
        real_w, real_h = content_area(driver)

        print(f"  viewport        : {s['innerW']}x{s['innerH']} CSS px  "
              f"dpr={s['dpr']} touch={s['touch']} mobileUA={s['mobileUA']}")
        print(f"  document height : {s['scrollH']}   scrollRange={s['maxScroll']}"
              f"{'   <-- NOTHING SCROLLS' if s['maxScroll'] == 0 else ''}")
        print(f"  wheel 600       : scrollY={wheel}"
              f"{'   <-- DEAD' if wheel == 0 else ''}")
        print(f"  SUBMIT box      : {box}")
        print(f"  screenshot      : {img}  scale={scale}")
        print(f"  after maximise  : viewport {after['innerW']}x{after['innerH']}, "
              f"real content area {real_w}x{real_h} "
              f"-> {'FOLLOWS' if after['innerH'] > before_h else 'STUCK'}")
        return {"h": s["innerH"], "scroll": s["maxScroll"], "wheel": wheel,
                "scale": scale, "follows": after["innerH"] > before_h,
                "width": s["innerW"], "dpr": s["dpr"]}
    finally:
        driver.quit()


def main():
    url = page_url()
    current = run("A  current", WIN_H, url)
    candidate = run("B  candidate (auto height)", 0, url)

    print("\n=== verdict ===")
    for key, a, b in (("layout viewport height", current["h"], candidate["h"]),
                      ("scroll range on a short page", current["scroll"], candidate["scroll"]),
                      ("wheel scroll works", current["wheel"] > 0, candidate["wheel"] > 0),
                      ("follows window resize", current["follows"], candidate["follows"]),
                      ("layout width (must stay 430)", current["width"], candidate["width"]),
                      ("dpr (must stay 3)", current["dpr"], candidate["dpr"]),
                      ("screenshot scale (must match)", current["scale"], candidate["scale"])):
        print(f"  {key:<32} current={a!s:<8} candidate={b!s}")


if __name__ == "__main__":
    main()
