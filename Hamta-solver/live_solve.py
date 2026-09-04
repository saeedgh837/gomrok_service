"""live_solve.py — pol-e ettesal-e solver-e captcha-ye HAMTA be robot-e estelam.

Chera in file: `solve.py` faghat masir-e file mikhanad (cv2.imread). Robot aks ra
dar RAM (PIL) darad; neveshtan-e har captcha roo-ye disk ham kond ast va ham
zobale misazad. Inja hamon pipeline-e solve.py ra roo-ye numpy/PIL ejra mikonim.

Karbord:
    from live_solve import load_solver
    solver = load_solver()                 # yek bar (weights dar RAM)
    r = solver.read_pil(pil_img)           # {'number','text','confidence','snap_distance'}
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cv2                      # noqa: E402
import numpy as np              # noqa: E402

try:
    import torch                # noqa: E402
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False

from config import CKPT_DIR                              # noqa: E402
from preprocess import to_model_gray, trim_whole, prepare_line_for_model  # noqa: E402
from vocab import VOCAB                                  # noqa: E402
import number_decode as nd                               # noqa: E402

DEFAULT_CKPT = os.path.join(_HERE, "checkpoints", "best.pt")
DEFAULT_ONNX = os.path.join(_HERE, "checkpoints", "best.onnx")

# `@torch.no_grad()` cannot decorate a method when torch is absent, so bind a
# no-op in that case. HamtaSolver is unusable without torch anyway; only the
# module-level import must keep working so the ONNX path can be reached.
_no_grad = torch.no_grad if _HAS_TORCH else (lambda: (lambda f: f))


def _module_importable(name: str) -> bool:
    """True when `name` can be found on sys.path WITHOUT importing it."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _torch_backend_available() -> bool:
    """True only when the torch backend can actually be constructed.

    torch being importable is NOT sufficient: `HamtaSolver.__init__` needs
    `model.py` (and `utils.greedy_decode`), and the packaged release deliberately
    ships neither -- it carries onnxruntime and the .onnx weights instead. On a
    machine that happens to have torch installed, checking only `import torch`
    selected a backend whose modules are missing and every inquiry died with
    `ModuleNotFoundError: No module named 'model'`. Verify the whole backend.
    """
    return _HAS_TORCH and _module_importable("model") and _module_importable("utils")


class HamtaSolver:
    """Model yek bar load mishavad; read_pil/read_bgr har bar seda zade mishavad."""

    def __init__(self, ckpt_path: str = None, device: str = "cpu"):
        # Imported lazily so this module stays importable without torch (the
        # packaged release has onnxruntime only). Bound to the instance here, at
        # construction time, because the caller may restore sys.path afterwards —
        # a later `from utils import ...` could then resolve to a DIFFERENT
        # package that happens to expose the same module name.
        from model import build_crnn
        from utils import greedy_decode

        self._greedy_decode = greedy_decode

        if device in (None, "auto"):
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.ckpt_path = ckpt_path or DEFAULT_CKPT or str(CKPT_DIR / "best.pt")
        self.model = build_crnn().to(self.device)
        ck = torch.load(self.ckpt_path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(ck["model"])
        self.model.eval()

    @_no_grad()
    def read_bgr(self, bgr: np.ndarray) -> dict:
        if bgr is None or getattr(bgr, "size", 0) == 0:
            return {"number": None, "text": "", "confidence": 0.0, "snap_distance": -1}
        gray = to_model_gray(bgr)
        arr = prepare_line_for_model(trim_whole(gray))
        batch = torch.from_numpy(arr).unsqueeze(0).unsqueeze(0).to(self.device)
        log_probs = self.model(batch)
        if log_probs is None:
            return {"number": None, "text": "", "confidence": 0.0, "snap_distance": -1}
        text = VOCAB.normalize(self._greedy_decode(log_probs)[0])
        snap_n, snap_d = nd.nearest_number(text)
        sig = nd.confidence_signals(log_probs, b=0)
        return {
            "number": snap_n,
            "text": text,
            "confidence": round(sig["min_word_conf"], 3),
            "snap_distance": snap_d,
        }

    def read_pil(self, pil_img) -> dict:
        """PIL.Image (RGB/RGBA/L) -> natije-ye khandan. Bedun-e neveshtan roo-ye disk."""
        img = pil_img.convert("RGB")
        rgb = np.array(img)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        return self.read_bgr(bgr)

    def read_file(self, path: str) -> dict:
        return self.read_bgr(cv2.imread(str(path), cv2.IMREAD_COLOR))


def load_solver(ckpt: str = None, device: str = "cpu"):
    """Load the HAMTA captcha solver once (weights stay in RAM).

    Backend choice mirrors the gomrok solver: use torch (HamtaSolver) when the
    torch backend is COMPLETE -- the development/training path, unchanged -- and
    fall back to the torch-free OnnxHamtaSolver otherwise, which is what the
    packaged release runs. Both backends return the identical dict; parity is
    proven at build time by lab/scripts/export_hamta_onnx.py.

    The check is `_torch_backend_available()`, not a bare `import torch`: the
    release ships no model.py, so a stray torch install must not select a
    backend that cannot load.
    """
    if _torch_backend_available():
        return HamtaSolver(ckpt_path=ckpt, device=device)
    from onnx_solve import OnnxHamtaSolver
    onnx_path = ckpt
    if onnx_path and onnx_path.endswith(".pt"):
        onnx_path = onnx_path[:-3] + ".onnx"
    return OnnxHamtaSolver(onnx_path=onnx_path or DEFAULT_ONNX, device=device)


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Hamta captcha solver (offline test)")
    ap.add_argument("--image", required=True)
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    s = load_solver(a.ckpt, a.device)
    print(json.dumps(s.read_file(a.image), ensure_ascii=False))
