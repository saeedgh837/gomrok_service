"""onnx_solve.py
Torch-free captcha solver used by the packaged Windows release.

`live_solve.load_solver()` prefers PyTorch (predict.IricaSolver) when it is
importable, and falls back to this module when it is not. The packaged bundle
ships onnxruntime (a few MB) instead of torch (~1.1 GB), so at runtime this is
the path that actually reads captchas.

It reuses the project's own preprocessing (preprocess.py), vocabulary (vocab.py)
and forced-length decoding logic (a numpy reimplementation of utils.forced_decode
that is verified byte-for-byte against the torch path by
lab/scripts/export_solver_onnx.py). The public surface matches IricaSolver:

    solver = OnnxSolver(onnx_path)
    solver.read_pil(pil_image)      -> 5-char string
    solver.read_array(bgr_ndarray)  -> 5-char string
    solver.read(path)               -> 5-char string
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from config import CAPTCHA_LEN, CKPT_DIR
from preprocess import prepare_line_for_model, to_model_gray, trim_whole
from vocab import VOCAB

_DEFAULT_ONNX = str(CKPT_DIR / "best_real_v2.onnx")


# --------------------------------------------------------------------------- #
# numpy port of utils.forced_decode / greedy_decode (no torch dependency).
# Parity with the torch implementation is asserted at build time.
# --------------------------------------------------------------------------- #
def _greedy(path: list[int]) -> str:
    return VOCAB.ctc_greedy_decode(path)


def _forced_decode(log_probs: np.ndarray, length: int = CAPTCHA_LEN) -> str:
    """log_probs: (T, 1, C). Mirror of solver.utils.forced_decode for one item."""
    lp = np.asarray(log_probs)
    T, B, C = lp.shape
    probs = np.exp(lp)
    argmax = lp.argmax(axis=2)              # (T, B)
    topval = probs.max(axis=2)             # (T, B)

    path = argmax[:, 0].tolist()
    greedy = _greedy(path)
    if len(greedy) == length:
        return greedy

    conf = topval[:, 0].tolist()
    runs = []                               # [start_t, symbol_idx, peak_conf, span]
    prev = None
    for t, idx in enumerate(path):
        if idx == VOCAB.blank_idx:
            prev = None
            continue
        if idx != prev:
            runs.append([t, idx, conf[t], 1])
        else:
            runs[-1][2] = max(runs[-1][2], conf[t])
            runs[-1][3] += 1
        prev = idx

    if len(runs) >= length:
        best = sorted(runs, key=lambda r: r[2], reverse=True)[:length]
        best.sort(key=lambda r: r[0])
        return "".join(VOCAB.itos[r[1]] for r in best)
    if runs:
        counts = [1] * len(runs)
        need = length - len(runs)
        for _ in range(need):
            j = max(range(len(runs)), key=lambda k: runs[k][3] / counts[k])
            counts[j] += 1
        return "".join(VOCAB.itos[runs[k][1]] * counts[k] for k in range(len(runs)))
    return greedy


class OnnxSolver:
    def __init__(self, onnx_path: str = None, device: str = "cpu"):
        import onnxruntime as ort

        onnx_path = onnx_path or _DEFAULT_ONNX
        if not Path(onnx_path).exists():
            raise FileNotFoundError(f"ONNX model not found: {onnx_path}")
        providers = ["CPUExecutionProvider"]
        self.sess = ort.InferenceSession(onnx_path, providers=providers)
        self.input_name = self.sess.get_inputs()[0].name
        self.output_name = self.sess.get_outputs()[0].name
        print(f"loaded ONNX solver: {onnx_path}")

    def _tensor_from_bgr(self, bgr: np.ndarray) -> np.ndarray:
        gray = to_model_gray(bgr)
        arr = prepare_line_for_model(trim_whole(gray))
        return arr[None, None, :, :].astype(np.float32)     # (1,1,H,W)

    def _infer(self, x: np.ndarray) -> np.ndarray:
        return self.sess.run([self.output_name], {self.input_name: x})[0]

    def read_array(self, bgr: np.ndarray, forced: bool = True) -> str:
        log_probs = self._infer(self._tensor_from_bgr(bgr))
        return _forced_decode(log_probs) if forced else _greedy(
            log_probs.argmax(axis=2)[:, 0].tolist())

    def read_pil(self, img, forced: bool = True) -> str:
        rgb = np.array(img.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        return self.read_array(bgr, forced=forced)

    def read(self, image_path: str, forced: bool = True) -> str:
        data = np.fromfile(str(image_path), dtype=np.uint8)
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(image_path)
        return self.read_array(bgr, forced=forced)
