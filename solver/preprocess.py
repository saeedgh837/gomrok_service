"""
preprocess.py
Grayscale normalization for IRICA captchas.

Domain notes (measured on the real captchas_irica set)
------------------------------------------------------
  * light-gray background (mean RGB ~166), dark glyphs (~20% dark pixels)
  * a faint RED noise line drawn across/over the text (~2% of pixels)
  * mild wave distortion of the glyph baseline
  * the whole thing is ONE line of exactly 5 chars

Following the hard-won lesson from the persian_ocr project: do NOT try to
binarize by color to delete the red line. Instead feed a normalized GRAYSCALE
image (text bright on a dark background) straight to the CRNN and let the
network learn to read through the red stroke. The synthetic training data draws
the same kind of red line, so the model sees it at train time too.

Pipeline
--------
1. to_model_gray(bgr)      -> normalized grayscale, text bright (~255) on dark
2. trim_whole(gray)        -> horizontal whitespace trim (never splits vertically)
3. prepare_line_for_model  -> resize+pad to the fixed CRNN input geometry
"""
from __future__ import annotations

import cv2
import numpy as np

from config import CFG

# On the normalized grayscale (text bright on dark), pixels brighter than this
# count as "ink" for column trimming. Kept low so faint anti-aliased edges still
# register, but above the contrast-suppressed background (which is ~0).
INK_THR = 40


# --------------------------------------------------------------------------- #
# 1. Grayscale normalization
# --------------------------------------------------------------------------- #
def to_model_gray(bgr: np.ndarray) -> np.ndarray:
    """
    Convert a raw captcha to the normalized grayscale the model consumes.

    Normalize by the bright background, invert so text becomes bright, then
    stretch contrast so the glyphs stand out from the faint background haze.
    The red noise line is NOT removed — it survives as a gray stroke the CRNN
    learns to ignore (it is trained with synthetic red lines too).

    Returns
    -------
    gray : np.ndarray (H, W) uint8, text bright (~255) on dark (~0) background.
    """
    if bgr.ndim == 3:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = bgr

    # Bright background as the normalization baseline (robust high percentile).
    bg = max(1.0, float(np.percentile(gray, 90)))
    norm = np.clip(gray / bg, 0.0, 1.0)
    inv = 1.0 - norm                                  # dark text -> bright

    # Contrast stretch: push the background haze (below the median) to 0 and let
    # real strokes span the full range.
    lo, hi = np.percentile(inv, 50), np.percentile(inv, 99.5)
    if hi - lo > 1e-3:
        inv = np.clip((inv - lo) / (hi - lo), 0.0, 1.0)

    return (inv * 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# 2. Horizontal trim (single-line — never split vertically)
# --------------------------------------------------------------------------- #
def trim_whole(gray: np.ndarray, pad: int = 4, ink_thr: int = INK_THR) -> np.ndarray:
    """Trim left/right whitespace around the ink of a single-line grayscale image."""
    h, w = gray.shape
    cols = np.where((gray > ink_thr).sum(axis=0) > 0)[0]
    if len(cols):
        x0 = max(0, cols[0] - pad)
        x1 = min(w, cols[-1] + pad)
        return gray[:, x0:x1]
    return gray


# --------------------------------------------------------------------------- #
# 3. Model input geometry
# --------------------------------------------------------------------------- #
def prepare_line_for_model(
    line_gray: np.ndarray,
    height: int = None,
    width: int = None,
) -> np.ndarray:
    """
    Resize a grayscale line to the fixed CRNN input (H x W), preserving aspect by
    resizing on height then right-padding width.

    Returns float32 in [0, 1], shape (H, W), text bright on dark bg.
    """
    height = height or CFG.image.height
    width = width or CFG.image.width

    if line_gray.ndim == 3:
        line_gray = cv2.cvtColor(line_gray, cv2.COLOR_BGR2GRAY)

    h, w = line_gray.shape
    if h == 0 or w == 0:
        return np.zeros((height, width), dtype=np.float32)

    new_w = max(1, int(round(w * (height / h))))
    resized = cv2.resize(line_gray, (new_w, height), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((height, width), dtype=np.uint8)
    if new_w <= width:
        canvas[:, :new_w] = resized                   # right-pad with dark bg
    else:
        canvas = cv2.resize(resized, (width, height), interpolation=cv2.INTER_AREA)

    return canvas.astype(np.float32) / 255.0


# --------------------------------------------------------------------------- #
# End-to-end helper used by dataset.py and predict.py
# --------------------------------------------------------------------------- #
def image_to_model_input(bgr: np.ndarray) -> np.ndarray:
    """raw BGR -> normalized gray -> trim -> model-ready (H, W) float32 array."""
    gray = to_model_gray(bgr)
    return prepare_line_for_model(trim_whole(gray))


# --------------------------------------------------------------------------- #
# Debug entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python preprocess.py <image_path> [out_dir]")
        raise SystemExit(1)

    img = cv2.imread(sys.argv[1], cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"could not read {sys.argv[1]}")

    gray = to_model_gray(img)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    cv2.imwrite(f"{out_dir}/_gray.png", gray)
    cv2.imwrite(f"{out_dir}/_trim.png", trim_whole(gray))
    print(f"wrote _gray.png and _trim.png to {out_dir}")
