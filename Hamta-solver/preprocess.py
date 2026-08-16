"""
preprocess.py
Grayscale normalization + line segmentation for Persian OCR (hamta captchas).

Key domain insight (REVISED 2026-07-11)
---------------------------------------
The earlier approach tried to BINARIZE by color: keep dark + achromatic pixels,
drop colored ones. That works on our *synthetic* text (neutral gray) but FAILS
on the *real* hamta captchas, whose text is navy/tinted (high saturation) — the
achromatic mask deleted 30–83% of the real ink (proven by tools/probe_hsv.py).
Text and noise on hamta are NOT separable by hue/brightness (both dark, both
colored, noise sometimes darker than ink).

New approach: DON'T binarize. Feed a normalized GRAYSCALE image (text bright on
a dark background) straight to the CRNN and let the network learn to read
through the colored scribbles (it's trained on synthetic images whose colored
noise now likewise survives as faint gray strokes, then fine-tuned on real).

Pipeline
--------
1. to_model_gray(bgr)  -> normalized grayscale, text bright (~255) on dark (~0)
2. segment_lines(gray) -> 1..N line row-ranges via horizontal projection
3. crop_lines(gray)    -> grayscale line crops (top->bottom), whitespace-trimmed
4. prepare_line_for_model() -> resize+pad to the fixed CRNN input geometry
"""
from __future__ import annotations
from typing import List, Tuple

import cv2
import numpy as np

from config import CFG

# On the normalized grayscale (text bright on dark), pixels brighter than this
# count as "ink" for projection/trimming. Kept low so faint anti-aliased edges
# still register, but above the contrast-suppressed background (which is ~0).
INK_THR = 40


# --------------------------------------------------------------------------- #
# 1. Grayscale normalization (replaces the old color-based binarization)
# --------------------------------------------------------------------------- #
def to_model_gray(bgr: np.ndarray) -> np.ndarray:
    """
    Convert a raw captcha to the normalized grayscale the model consumes.

    hamta captchas: light background (~239), dark (tinted) text, colored noise.
    We normalize by the bright background, invert so text becomes bright, then
    stretch contrast so the text stands out from faint background/noise haze.
    Colored noise is NOT removed — it survives as gray strokes the CRNN learns
    to ignore.

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

    # Contrast stretch: push the background/noise haze (below the median) to 0
    # and let real strokes span the full range.
    lo, hi = np.percentile(inv, 50), np.percentile(inv, 99.5)
    if hi - lo > 1e-3:
        inv = np.clip((inv - lo) / (hi - lo), 0.0, 1.0)

    return (inv * 255).astype(np.uint8)


# Backward-compat: some call sites / tools still import remove_color_noise.
# It now returns the normalized grayscale (same polarity: text bright on dark),
# so downstream resize/segmentation behave identically.
def remove_color_noise(bgr: np.ndarray, *_, **__) -> np.ndarray:
    return to_model_gray(bgr)


# --------------------------------------------------------------------------- #
# 2. Line segmentation (Horizontal Projection Profile on the grayscale)
# --------------------------------------------------------------------------- #
def segment_lines(
    gray: np.ndarray,
    min_line_height_ratio: float = 0.12,
    smooth: int = 5,
    ink_thr: int = INK_THR,
) -> List[Tuple[int, int]]:
    """
    Detect text lines with a horizontal projection profile on the normalized
    grayscale (text bright). Returns (y_start, y_end) ranges, TOP -> BOTTOM.
    Works for 1 or 2+ lines; a single blob yields one full-height line.
    """
    h, w = gray.shape
    proj = (gray > ink_thr).sum(axis=1).astype(np.float32)
    if smooth > 1:
        kernel = np.ones(smooth, dtype=np.float32) / smooth
        proj = np.convolve(proj, kernel, mode="same")

    thresh = max(1.0, 0.03 * proj.max())
    is_text = proj > thresh

    bands: List[Tuple[int, int]] = []
    start = None
    for y in range(h):
        if is_text[y] and start is None:
            start = y
        elif not is_text[y] and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, h))

    min_h = int(min_line_height_ratio * h)
    bands = [(s, e) for (s, e) in bands if (e - s) >= min_h]
    if not bands:
        return [(0, h)]

    # Merge bands separated by a very small gap (Persian dots/diacritics sit
    # above the body and can create a spurious split).
    merged = [bands[0]]
    for s, e in bands[1:]:
        ps, pe = merged[-1]
        if s - pe < 0.4 * min_h:
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))
    return merged


def _trim_cols(gray: np.ndarray, pad: int, ink_thr: int) -> np.ndarray:
    """Trim left/right whitespace around the ink of a grayscale strip."""
    h, w = gray.shape
    cols = np.where((gray > ink_thr).sum(axis=0) > 0)[0]
    if len(cols):
        x0 = max(0, cols[0] - pad)
        x1 = min(w, cols[-1] + pad)
        return gray[:, x0:x1]
    return gray


def crop_lines(gray: np.ndarray, pad: int = 4, ink_thr: int = INK_THR) -> List[np.ndarray]:
    """Return grayscale line crops (top->bottom) with vertical pad + h-trim."""
    h, w = gray.shape
    lines = []
    for (y0, y1) in segment_lines(gray, ink_thr=ink_thr):
        y0 = max(0, y0 - pad)
        y1 = min(h, y1 + pad)
        lines.append(_trim_cols(gray[y0:y1, :], pad, ink_thr))
    return lines


def trim_whole(gray: np.ndarray, pad: int = 4, ink_thr: int = INK_THR) -> np.ndarray:
    """
    Horizontal-trim a single-line grayscale image WITHOUT vertical splitting.
    Used for training samples (synthetic is single-line by construction), so a
    noisy projection can never wrongly split one label across two crops.
    """
    return _trim_cols(gray, pad, ink_thr)


# --------------------------------------------------------------------------- #
# 3. Model input geometry
# --------------------------------------------------------------------------- #
def prepare_line_for_model(
    line_gray: np.ndarray,
    height: int = None,
    width: int = None,
) -> np.ndarray:
    """
    Resize a (grayscale) line to the fixed CRNN input (H x W), preserving aspect
    by resizing on height then right-padding width.

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
def image_to_line_tensors(bgr: np.ndarray) -> List[np.ndarray]:
    """raw BGR -> normalized gray -> segment -> list of model-ready arrays."""
    gray = to_model_gray(bgr)
    return [prepare_line_for_model(c) for c in crop_lines(gray)]


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
    bands = segment_lines(gray)
    print(f"detected {len(bands)} line(s): {bands}")

    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    cv2.imwrite(f"{out_dir}/_gray.png", gray)
    for i, crop in enumerate(crop_lines(gray)):
        cv2.imwrite(f"{out_dir}/_line_{i}.png", crop)
    print(f"wrote _gray.png and {len(bands)} line crops to {out_dir}")
