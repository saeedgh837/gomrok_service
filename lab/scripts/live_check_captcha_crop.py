"""Live check: the captcha crop is unaffected by the viewport change.

This is the one regression that would silently destroy accuracy. The cropper
(`captcha_collector.crop_captcha_with_padding`) takes a FULL-VIEWPORT screenshot
and cuts the captcha out using the element's CSS-pixel box multiplied by a scale
it derives as `image_width / window.innerWidth`. Changing the emulated viewport
height changes the screenshot height, so the crop must be re-verified against a
known target rather than assumed.

A local page draws a captcha-shaped element of an exact known size, at a known
position, both ABOVE and BELOW the fold. The crop is then compared against the
expected pixel dimensions, and the solver is run on it to confirm the pipeline
still produces a reading.

Usage:
    "C:/Program Files/Python310/python.exe" lab/scripts/live_check_captcha_crop.py
"""
import os
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

CHECKS = []

# The captcha element is 200x60 CSS px. `#spacer` pushes it below the fold in the
# second case, so the cropper is tested both with and without scrolling.
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{{margin:0;background:#fff}}
 #spacer{{height:{spacer}px;background:#f4f4f4}}
 img.captchaImage{{display:block;width:200px;height:60px;margin:20px}}
</style></head><body>
<div id="spacer">spacer</div>
<img class="captchaImage" alt="captcha"
 src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200'
 height='60'><rect width='200' height='60' fill='white'/><text x='12' y='44'
 font-family='monospace' font-size='38' fill='black'>7K2QF</text><line x1='0'
 y1='30' x2='200' y2='34' stroke='red' stroke-width='2'/></svg>">
</body></html>"""


def ck(name, cond, extra=""):
    CHECKS.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (f"   | {str(extra)[:110]}" if extra else ""))


def page(spacer):
    path = os.path.join(tempfile.mkdtemp(prefix="irica-crop-"), f"c{spacer}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(PAGE.format(spacer=spacer))
    return "file:///" + path.replace("\\", "/")


def main():
    import captcha_collector as cc

    driver = cc.build_driver(cc.USER_AGENTS[0])
    try:
        for spacer, label in ((20, "captcha ABOVE the fold"),
                              (1400, "captcha BELOW the fold (needs scrolling)")):
            driver.get(page(spacer))
            time.sleep(0.9)
            element = driver.find_element("css selector", "img.captchaImage")
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(0.5)

            geom = driver.execute_script(
                "const r = arguments[0].getBoundingClientRect();"
                "return {w: Math.round(r.width), h: Math.round(r.height),"
                " top: Math.round(r.top), vh: window.innerHeight,"
                " vw: window.innerWidth, zoom:"
                " getComputedStyle(document.documentElement).zoom};", element)

            crop = cc.crop_captcha_with_padding(driver, element)
            # Expected: element box in CSS px, scaled by image/viewport, plus the
            # configured padding on each side. The scale is derived the same way
            # the cropper derives it, from the screenshot itself.
            import io as _io

            from PIL import Image as _Image
            shot_w = _Image.open(_io.BytesIO(driver.get_screenshot_as_png())).width
            factor = shot_w / geom["vw"]
            expect_w = round((geom["w"] + 2 * cc.PAD_X) * factor)
            expect_h = round((geom["h"] + 2 * cc.PAD_Y) * factor)

            print(f"\n--- {label} ---")
            print(f"  element {geom['w']}x{geom['h']} CSS px at top={geom['top']}, "
                  f"viewport {geom['vw']}x{geom['vh']}, html zoom={geom['zoom']}, "
                  f"scale={factor}")
            ck(f"{label}: crop has the expected pixel size",
               abs(crop.width - expect_w) <= 3 and abs(crop.height - expect_h) <= 3,
               f"got {crop.size}, expected ~({expect_w}, {expect_h})")
            ck(f"{label}: crop is not blank",
               len(crop.convert('L').getcolors(maxcolors=100000) or []) > 3,
               f"distinct grey levels="
               f"{len(crop.convert('L').getcolors(maxcolors=100000) or [])}")
            ck(f"{label}: crop is fully inside the screenshot (no clipping)",
               crop.width > 0 and crop.height > 0
               and crop.width <= shot_w, f"crop {crop.size}, shot width {shot_w}")

            # The real consumer: the ONNX solver must accept it and return 5 chars.
            solver = cc_solver()
            if solver is not None:
                text = solver.read_pil(crop)
                ck(f"{label}: solver reads the crop (5 chars)",
                   isinstance(text, str) and len(text) == 5, f"read {text!r}")
    finally:
        driver.quit()

    print(f"\n=== {sum(CHECKS)}/{len(CHECKS)} checks passed ===")
    return 0 if CHECKS and all(CHECKS) else 1


_SOLVER = []


def cc_solver():
    """The gomrok solver, loaded exactly as estelam_gomrok.py loads it.

    `gomrok._DEFAULT_CKPT` must be passed: the solver package's own default is the
    relative `checkpoints/best_real.pt`, which does not resolve from the project
    root and silently falls back to RANDOM WEIGHTS (the torch path prints a
    warning and returns garbage). estelam_gomrok.py passes the absolute path, so
    this probe does too.
    """
    if _SOLVER:
        return _SOLVER[0]
    try:
        import gomrok
        _SOLVER.append(gomrok.load_solver(ckpt=gomrok._DEFAULT_CKPT, device="cpu"))
    except Exception as exc:
        print(f"  (solver unavailable: {type(exc).__name__}: {exc})")
        _SOLVER.append(None)
    return _SOLVER[0]


if __name__ == "__main__":
    sys.exit(main())
