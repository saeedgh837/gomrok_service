"""
predict.py
End-to-end inference for the IRICA captcha:
    image path -> normalized grayscale -> CRNN -> forced 5-char CTC decode.

Usage
-----
    python predict.py --image ../captchas_irica/captcha_100.png
    python predict.py --image path.png --ckpt checkpoints/best_real.pt
"""
from __future__ import annotations
import argparse
from pathlib import Path

import cv2
import numpy as np
import torch

from config import CKPT_DIR
from model import build_crnn
from preprocess import to_model_gray, trim_whole, prepare_line_for_model
from utils import forced_decode, forced_decode_conf, greedy_decode
from vocab import VOCAB


class IricaSolver:
    def __init__(self, ckpt_path: str = None, device: str = "auto"):
        if device in (None, "auto"):
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        elif device == "cuda" and not torch.cuda.is_available():
            print("WARNING: --device cuda requested but no GPU visible; using CPU")
            dev = "cpu"
        else:
            dev = device
        self.device = torch.device(dev)
        ckpt_path = ckpt_path or str(CKPT_DIR / "best.pt")
        self.model = build_crnn().to(self.device)
        if Path(ckpt_path).exists():
            ck = torch.load(ckpt_path, map_location=self.device, weights_only=False)
            self.model.load_state_dict(ck["model"])
            print(f"loaded weights: {ckpt_path}")
        else:
            print(f"WARNING: checkpoint {ckpt_path} not found — using random weights")
        self.model.eval()

    def _tensor_from_bgr(self, bgr: np.ndarray) -> torch.Tensor:
        """Shared path: raw BGR array -> model-ready (1,1,H,W) tensor."""
        gray = to_model_gray(bgr)
        arr = prepare_line_for_model(trim_whole(gray))
        return torch.from_numpy(arr).unsqueeze(0).unsqueeze(0).to(self.device)

    def _load(self, image_path: str) -> torch.Tensor:
        data = np.fromfile(str(image_path), dtype=np.uint8)
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(image_path)
        return self._tensor_from_bgr(bgr)

    @torch.no_grad()
    def read(self, image_path: str, forced: bool = True) -> str:
        x = self._load(image_path)
        log_probs = self.model(x)                     # (T, 1, C)
        if forced:
            return forced_decode(log_probs)[0]
        return greedy_decode(log_probs)[0]

    @torch.no_grad()
    def read_array(self, bgr: np.ndarray, forced: bool = True) -> str:
        """Solve an in-memory BGR image (e.g. an OpenCV/numpy frame)."""
        x = self._tensor_from_bgr(bgr)
        log_probs = self.model(x)
        return forced_decode(log_probs)[0] if forced else greedy_decode(log_probs)[0]

    @torch.no_grad()
    def read_array_conf(self, bgr: np.ndarray) -> tuple:
        """(text, confidence) — see utils.forced_decode_conf."""
        x = self._tensor_from_bgr(bgr)
        log_probs = self.model(x)
        return forced_decode_conf(log_probs)[0]

    @torch.no_grad()
    def read_pil(self, img, forced: bool = True) -> str:
        """Solve a PIL.Image (as produced by the collector's crop step)."""
        rgb = np.array(img.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()                  # RGB -> BGR for cv2 path
        return self.read_array(bgr, forced=forced)

    @torch.no_grad()
    def read_pil_conf(self, img) -> tuple:
        rgb = np.array(img.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()                  # RGB -> BGR for cv2 path
        return self.read_array_conf(bgr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--ckpt", default=str(CKPT_DIR / "best.pt"))
    ap.add_argument("--device", type=str, default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--raw", action="store_true", help="plain greedy (no 5-char forcing)")
    args = ap.parse_args()

    solver = IricaSolver(ckpt_path=args.ckpt, device=args.device)
    text = solver.read(args.image, forced=not args.raw)
    print("\n=== RESULT ===")
    print(text)


if __name__ == "__main__":
    main()
