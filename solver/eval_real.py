"""
eval_real.py
Batch-evaluate a checkpoint over a labeled CSV (filename,text).
Reports exact-match accuracy, mean CER, and a list of misses.

    python eval_real.py --ckpt checkpoints/best_real.pt \
        --csv ../laptop/captchas_irica/labels_irica.csv \
        --images ../laptop/captchas_irica
"""
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from predict import IricaSolver
from vocab import VOCAB


def cer(pred: str, true: str) -> float:
    # Levenshtein / len(true)
    m, n = len(pred), len(true)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1,
                        prev + (pred[i - 1] != true[j - 1]))
            prev = cur
    return dp[n] / max(1, len(true))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="checkpoints/best_real.pt")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--show-misses", type=int, default=40)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    df["text"] = df["text"].astype(str).map(VOCAB.normalize)
    df = df[df["text"].str.len() == 5].reset_index(drop=True)

    solver = IricaSolver(ckpt_path=args.ckpt, device="cpu")
    imgdir = Path(args.images)

    n = len(df)
    exact = 0
    cer_sum = 0.0
    char_ok = char_tot = 0
    misses = []
    confusion = Counter()  # (true_ch, pred_ch) at same position

    for _, row in df.iterrows():
        true = row["text"]
        pred = solver.read(str(imgdir / row["filename"]), forced=True)
        cer_sum += cer(pred, true)
        if pred == true:
            exact += 1
        else:
            misses.append((row["filename"], true, pred))
        for i, tch in enumerate(true):
            char_tot += 1
            if i < len(pred) and pred[i] == tch:
                char_ok += 1
            elif i < len(pred):
                confusion[(tch, pred[i])] += 1

    print(f"\n=== EVAL: {args.ckpt} on {n} samples ===")
    print(f"exact-match accuracy: {exact}/{n} = {exact / n:.4f}")
    print(f"char accuracy (positional): {char_ok}/{char_tot} = {char_ok / char_tot:.4f}")
    print(f"mean CER: {cer_sum / n:.4f}")

    print(f"\n--- top char confusions (true->pred) ---")
    for (t, p), c in confusion.most_common(15):
        print(f"  {t} -> {p} : {c}")

    print(f"\n--- misses ({len(misses)} total, showing {min(args.show_misses, len(misses))}) ---")
    for fn, t, p in misses[:args.show_misses]:
        print(f"  {fn}: true={t}  pred={p}")


if __name__ == "__main__":
    main()
