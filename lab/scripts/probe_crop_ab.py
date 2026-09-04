"""A/B: is the captcha crop bit-identical before and after the viewport change?

The crop is the one thing that must not move. It depends on the element's CSS-pixel
box and on a scale the cropper derives as `screenshot_width / window.innerWidth` --
neither of which involves the viewport HEIGHT. This probe proves that rather than
arguing it: the same page is cropped with `deviceMetrics.height = 932` (old) and
`= 0` (new), and the two crops are compared pixel by pixel.

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/probe_crop_ab.py
"""
import hashlib
import os
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;background:#fff}
 #spacer{height:120px;background:#f4f4f4}
 img.captchaImage{display:block;width:200px;height:60px;margin:20px}
</style></head><body>
<div id="spacer">spacer</div>
<img class="captchaImage" alt="captcha"
 src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200'
 height='60'><rect width='200' height='60' fill='white'/><text x='12' y='44'
 font-family='monospace' font-size='38' fill='black'>7K2QF</text><line x1='0'
 y1='30' x2='200' y2='34' stroke='red' stroke-width='2'/></svg>">
</body></html>"""


def page_url():
    path = os.path.join(tempfile.mkdtemp(prefix="irica-ab-"), "c.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(PAGE)
    return "file:///" + path.replace("\\", "/")


def crop_with(height, url):
    """Build a driver exactly as captcha_collector does, but pin the height."""
    import captcha_collector as cc

    saved = dict(cc.MOBILE_METRICS)
    cc.MOBILE_METRICS["height"] = height
    try:
        driver = cc.build_driver(cc.USER_AGENTS[0])
    finally:
        cc.MOBILE_METRICS.clear()
        cc.MOBILE_METRICS.update(saved)
    try:
        driver.get(url)
        time.sleep(0.9)
        element = driver.find_element("css selector", "img.captchaImage")
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.5)
        crop = cc.crop_captcha_with_padding(driver, element)
        info = driver.execute_script(
            "return {vw: window.innerWidth, vh: window.innerHeight,"
            " zoom: getComputedStyle(document.documentElement).zoom}")
        return crop, info
    finally:
        driver.quit()


def digest(image):
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()[:16]


def main():
    url = page_url()
    old, old_info = crop_with(932, url)
    new, new_info = crop_with(0, url)

    print(f"OLD (height=932): viewport {old_info['vw']}x{old_info['vh']}  "
          f"zoom={old_info['zoom']}  crop={old.size}  sha={digest(old)}")
    print(f"NEW (height=0)  : viewport {new_info['vw']}x{new_info['vh']}  "
          f"zoom={new_info['zoom']}  crop={new.size}  sha={digest(new)}")

    same_size = old.size == new.size
    same_bytes = digest(old) == digest(new)
    print(f"\nsame crop size : {same_size}")
    print(f"same crop bytes: {same_bytes}")
    if not same_bytes and same_size:
        from PIL import ImageChops
        diff = ImageChops.difference(old.convert("RGB"), new.convert("RGB"))
        box = diff.getbbox()
        extremes = diff.convert("L").getextrema()
        print(f"  differing region: {box}   max channel delta: {extremes[1]}")
        print("  (a small delta with identical geometry is antialiasing, not a "
              "crop shift; a large one means the crop moved)")
    return 0 if same_size else 1


if __name__ == "__main__":
    sys.exit(main())
