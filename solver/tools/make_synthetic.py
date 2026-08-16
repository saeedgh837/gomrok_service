"""
tools/make_synthetic.py
Generate synthetic captchas that imitate the REAL IRICA captcha, plus labels.csv.

Style measured on the real captchas_irica set:
  * canvas ~386x151, light-gray background ~(215,218,220) BGR-agnostic
  * 5 dark, near-black glyphs, lowercase a-z + 0-9
  * glyphs fill almost the full height, slight per-glyph rotation + baseline wave
  * a single faint RED line (~(232,37,38) RGB) sweeps across, drawn OVER the text

The red line is intentionally rendered on top of the glyphs (like the real one)
so the CRNN learns to read through it — matching preprocess.to_model_gray, which
does NOT remove it.

Usage:
    python tools/make_synthetic.py --n 20000
    python tools/make_synthetic.py --n 2000 --append
Outputs into data/images/*.png and data/labels.csv
"""
from __future__ import annotations
import argparse
import csv
import math
import random
from pathlib import Path

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import ALPHABET, CAPTCHA_LEN, IMAGES_DIR, LABELS_CSV  # noqa: E402

# --------------------------------------------------------------------------- #
# Style constants (measured), all in RGB for PIL.
# --------------------------------------------------------------------------- #
CANVAS_W, CANVAS_H = 386, 151
BG_RGB = (215, 218, 220)
INK_RGB = (17, 16, 17)
RED_RGB = (232, 37, 38)

# Latin fonts present on this machine (checked via C:/Windows/Fonts).
# Bold faces are listed twice to bias sampling toward heavier ink, matching the
# real captcha whose glyphs are thick (dark-pixel ratio ~0.20).
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/courbd.ttf",
    "C:/Windows/Fonts/courbd.ttf",
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/consolab.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/cour.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/times.ttf",
    "C:/Windows/Fonts/verdana.ttf",
]


def find_fonts():
    return [f for f in FONT_CANDIDATES if Path(f).exists()]


def random_text() -> str:
    return "".join(random.choice(ALPHABET) for _ in range(CAPTCHA_LEN))


def _jitter_color(rgb, spread=18):
    return tuple(int(np.clip(c + random.randint(-spread, spread), 0, 255)) for c in rgb)


def make_one(text: str, font_path: str) -> np.ndarray:
    """Render one synthetic captcha as a BGR uint8 array (for cv2 write)."""
    # Slightly vary background so the model doesn't key on an exact bg value.
    bg = _jitter_color(BG_RGB, 10)
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), bg)
    draw = ImageDraw.Draw(img)

    # Font size so 5 glyphs fill most of the width and height. Real glyphs span
    # ~144/151 px vertically. Cap letters at ~0.7*em, so we need a large em to
    # reach that; push the size up near / past the canvas height.
    size = random.randint(150, 180)
    try:
        font = ImageFont.truetype(font_path, size)
    except Exception:
        font = ImageFont.load_default()

    # Layout: spread the 5 glyphs across the width with per-glyph jitter.
    n = len(text)
    margin_x = random.randint(14, 26)
    usable = CANVAS_W - 2 * margin_x
    step = usable / n
    for i, ch in enumerate(text):
        # measure glyph box
        try:
            bbox = font.getbbox(ch)
            gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
            ox, oy = bbox[0], bbox[1]
        except Exception:
            gw, gh, ox, oy = size // 2, size, 0, 0
        cx = margin_x + step * (i + 0.5) + random.uniform(-6, 6)
        cy = CANVAS_H / 2 + random.uniform(-10, 10)
        # baseline wave
        cy += 8.0 * math.sin(i * 1.1 + random.uniform(0, 6.28))

        # Render each glyph on its own layer so we can rotate it independently.
        pad = 20
        gimg = Image.new("RGBA", (gw + 2 * pad, gh + 2 * pad), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(gimg)
        gdraw.text((pad - ox, pad - oy), ch, font=font, fill=_jitter_color(INK_RGB, 12) + (255,))
        angle = random.uniform(-18, 18)
        gimg = gimg.rotate(angle, resample=Image.BICUBIC, expand=True)
        px = int(cx - gimg.width / 2)
        py = int(cy - gimg.height / 2)
        img.paste(gimg, (px, py), gimg)

    arr = np.array(img)  # RGB

    # --- Red noise line(s) drawn OVER the text -------------------------- #
    n_lines = random.choice([1, 1, 1, 2])
    for _ in range(n_lines):
        _draw_red_curve(arr)

    # Mild global wave distortion (sine remap on x).
    arr = _wave_distort(arr)

    # A touch of blur + noise to match the anti-aliased look.
    if random.random() < 0.6:
        arr = cv2.GaussianBlur(arr, (3, 3), 0)
    noise = np.random.normal(0, random.uniform(2, 6), arr.shape)
    arr = np.clip(arr.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    # PIL RGB -> cv2 BGR
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _draw_red_curve(arr: np.ndarray):
    """Draw a smooth-ish red polyline across the width, on top of the glyphs."""
    h, w, _ = arr.shape
    npts = random.randint(3, 6)
    xs = np.linspace(0, w, npts)
    ys = np.random.uniform(h * 0.2, h * 0.8, npts)
    pts = np.stack([xs, ys], axis=1).astype(np.int32).reshape(-1, 1, 2)
    color_bgr_agnostic = tuple(int(c) for c in _jitter_color(RED_RGB, 20))  # RGB order
    thick = random.randint(2, 4)
    # arr is RGB; cv2 uses the tuple as-is per channel, so pass RGB tuple.
    cv2.polylines(arr, [pts], isClosed=False, color=color_bgr_agnostic,
                  thickness=thick, lineType=cv2.LINE_AA)


def _wave_distort(arr: np.ndarray) -> np.ndarray:
    h, w, _ = arr.shape
    amp = random.uniform(1.5, 4.0)
    period = random.uniform(60, 140)
    phase = random.uniform(0, 6.28)
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    map_y = (yy + amp * np.sin(2 * np.pi * xx / period + phase)).astype(np.float32)
    map_x = xx.astype(np.float32)
    return cv2.remap(arr, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--append", action="store_true",
                    help="append to existing data/labels.csv and continue numbering")
    ap.add_argument("--font", default=None, help="force a single font path")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    fonts = [args.font] if args.font else find_fonts()
    if not fonts:
        raise SystemExit("no usable fonts found under C:/Windows/Fonts")
    print(f"fonts ({len(fonts)}): {fonts}")

    start_idx = 0
    mode = "a" if args.append and LABELS_CSV.exists() else "w"
    if mode == "a":
        existing = list(IMAGES_DIR.glob("synth_*.png"))
        start_idx = len(existing)

    rows = []
    for i in range(args.n):
        text = random_text()
        img = make_one(text, random.choice(fonts))
        fname = f"synth_{start_idx + i:06d}.png"
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(str(IMAGES_DIR / fname))
        rows.append((fname, text))
        if (i + 1) % 2000 == 0:
            print(f"  {i+1}/{args.n} generated")

    with open(LABELS_CSV, mode, encoding="utf-8", newline="") as f:
        wri = csv.writer(f)
        if mode == "w":
            wri.writerow(["filename", "text"])
        wri.writerows(rows)
    print(f"wrote {len(rows)} images to {IMAGES_DIR} and labels to {LABELS_CSV} (mode={mode})")


if __name__ == "__main__":
    main()
