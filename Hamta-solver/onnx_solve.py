"""onnx_solve.py — torch-free HAMTA captcha solver used by the packaged release.

Why this exists: `estelam_hamta.py` reads the modal captcha through
`live_solve.load_solver()`. The development machine has PyTorch, but the shipped
Windows bundle deliberately does not (onnxruntime is ~6 MB against torch's
~1.1 GB), so without this module the hamta inquiry robot crashes the moment it
reaches the captcha.

It reuses the project's own preprocessing (preprocess.py), vocabulary
(vocab.py) and number snapping (number_decode.py); only the two torch tensor
helpers — greedy CTC decode and the confidence signals — are reimplemented on
numpy. Parity with the torch path is asserted at build time by
lab/scripts/export_hamta_onnx.py, which refuses to emit an artifact unless every
sample decodes identically.

Public surface matches HamtaSolver so callers need no change:

    solver = OnnxHamtaSolver(onnx_path)
    solver.read_pil(pil_image)      -> {'number','text','confidence','snap_distance'}
    solver.read_bgr(bgr_ndarray)    -> same dict
    solver.read_file(path)          -> same dict
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

import number_decode as nd
from preprocess import prepare_line_for_model, to_model_gray, trim_whole
from vocab import VOCAB

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ONNX = os.path.join(_HERE, "checkpoints", "best.onnx")

_EMPTY = {"number": None, "text": "", "confidence": 0.0, "snap_distance": -1}


# --------------------------------------------------------------------------- #
# numpy ports of utils.greedy_decode and number_decode._emissions.
# Kept deliberately literal so a reader can diff them against the torch source.
# --------------------------------------------------------------------------- #
def _greedy_decode(log_probs: np.ndarray) -> str:
    """log_probs: (T, B, C) -> decoded string for item 0."""
    path = np.asarray(log_probs).argmax(axis=2)[:, 0].tolist()
    return VOCAB.ctc_greedy_decode(path)


def _emissions(logp_2d: np.ndarray) -> list[dict]:
    """Mirror of number_decode._emissions for a (T, C) numpy array."""
    probs = np.exp(np.asarray(logp_2d, dtype=np.float64))
    argmax = probs.argmax(axis=1).tolist()
    # np.sort ascending; the two largest are the last two columns.
    top2 = np.sort(probs, axis=1)[:, -2:]
    out: list[dict] = []
    prev = -1
    for t, a in enumerate(argmax):
        if a != VOCAB.blank_idx and a != prev:
            p1 = float(top2[t, 1])
            p2 = float(top2[t, 0])
            out.append({"char": VOCAB.itos[a], "p1": p1, "margin": p1 - p2})
        prev = a
    return out


def _confidence_signals(log_probs: np.ndarray, b: int = 0) -> dict:
    """Mirror of number_decode.confidence_signals on numpy input."""
    emits = _emissions(np.asarray(log_probs)[:, b, :])
    if not emits:
        return {"mean_conf": 0.0, "min_word_conf": 0.0, "min_char_conf": 0.0,
                "min_margin": 0.0, "n_words": 0, "n_chars": 0}
    words: list[list[dict]] = [[]]
    for e in emits:
        if e["char"] == " ":
            if words[-1]:
                words.append([])
        else:
            words[-1].append(e)
    words = [w for w in words if w]
    all_p1 = [e["p1"] for e in emits if e["char"] != " "]
    all_mg = [e["margin"] for e in emits if e["char"] != " "]
    word_means = [sum(e["p1"] for e in w) / len(w) for w in words] or [0.0]
    return {
        "mean_conf": sum(all_p1) / len(all_p1) if all_p1 else 0.0,
        "min_word_conf": min(word_means),
        "min_char_conf": min(all_p1) if all_p1 else 0.0,
        "min_margin": min(all_mg) if all_mg else 0.0,
        "n_words": len(words),
        "n_chars": len(all_p1),
    }


class OnnxHamtaSolver:
    """Drop-in replacement for HamtaSolver backed by onnxruntime."""

    def __init__(self, onnx_path: str = None, device: str = "cpu"):
        import onnxruntime as ort

        onnx_path = onnx_path or _DEFAULT_ONNX
        if not Path(onnx_path).exists():
            raise FileNotFoundError(f"HAMTA ONNX model not found: {onnx_path}")
        self.onnx_path = onnx_path
        self.sess = ort.InferenceSession(onnx_path,
                                         providers=["CPUExecutionProvider"])
        self.input_name = self.sess.get_inputs()[0].name
        self.output_name = self.sess.get_outputs()[0].name
        print(f"loaded ONNX hamta solver: {onnx_path}")

    def read_bgr(self, bgr: np.ndarray) -> dict:
        if bgr is None or getattr(bgr, "size", 0) == 0:
            return dict(_EMPTY)
        gray = to_model_gray(bgr)
        arr = prepare_line_for_model(trim_whole(gray))
        batch = arr[None, None, :, :].astype(np.float32)
        log_probs = self.sess.run([self.output_name], {self.input_name: batch})[0]
        if log_probs is None:
            return dict(_EMPTY)
        text = VOCAB.normalize(_greedy_decode(log_probs))
        snap_n, snap_d = nd.nearest_number(text)
        sig = _confidence_signals(log_probs, b=0)
        return {
            "number": snap_n,
            "text": text,
            "confidence": round(sig["min_word_conf"], 3),
            "snap_distance": snap_d,
        }

    def read_pil(self, pil_img) -> dict:
        rgb = np.array(pil_img.convert("RGB"))
        return self.read_bgr(rgb[:, :, ::-1].copy())

    def read_file(self, path: str) -> dict:
        data = np.fromfile(str(path), dtype=np.uint8)
        return self.read_bgr(cv2.imdecode(data, cv2.IMREAD_COLOR))
