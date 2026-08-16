"""
tools/eval_real.py
Evaluate a checkpoint on the labeled REAL IRICA captchas and print CER +
exact-match accuracy, plus the worst misses for inspection.

Usage
-----
    python tools/eval_real.py --ckpt checkpoints/best_real.pt
    python tools/eval_real.py --ckpt checkpoints/best_synth.pt \
                              --csv ../captchas_irica/labels_irica.csv \
                              --images ../captchas_irica
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cv2
import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import CKPT_DIR, REAL_LABELS_CSV, REAL_DIR  # noqa: E402
from model import build_crnn  # noqa: E402
from preprocess import to_model_gray, trim_whole, prepare_line_for_model  # noqa: E402
from utils import forced_decode, cer, corpus_metrics  # noqa: E402
from vocab import VOCAB  # noqa: E402


def load_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    gray = to_model_gray(bgr)
    return prepare_line_for_model(trim_whole(gray))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(CKPT_DIR / "best_real.pt"))
    ap.add_argument("--csv", default=str(REAL_LABELS_CSV))
    ap.add_argument("--images", default=str(REAL_DIR))
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--show", type=int, default=15, help="how many worst misses to print")
    args = ap.parse_args()

    dev = torch.device("cuda" if (args.device != "cpu" and torch.cuda.is_available()) else "cpu")
    model = build_crnn().to(dev)
    ck = torch.load(args.ckpt, map_location=dev, weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()
    print(f"loaded {args.ckpt} on {dev}")

    df = pd.read_csv(args.csv)
    df["text"] = df["text"].astype(str).map(VOCAB.normalize)
    images_dir = Path(args.images)

    preds, targets, misses = [], [], []
    with torch.no_grad():
        for _, row in df.iterrows():
            arr = load_image(images_dir / row["filename"])
            x = torch.from_numpy(arr).unsqueeze(0).unsqueeze(0).to(dev)
            pred = forced_decode(model(x))[0]
            preds.append(pred)
            targets.append(row["text"])
            if pred != row["text"]:
                misses.append((row["filename"], row["text"], pred, cer(pred, row["text"])))

    m = corpus_metrics(preds, targets)
    print(f"\nN={len(preds)}  exact-acc={m['acc']:.3f}  CER={m['cer']:.4f}")
    print(f"misses: {len(misses)}")
    misses.sort(key=lambda t: t[3], reverse=True)
    for fn, tgt, pr, c in misses[:args.show]:
        print(f"  {fn:22s} true={tgt!r} pred={pr!r} cer={c:.2f}")


if __name__ == "__main__":
    main()
