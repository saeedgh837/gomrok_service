"""
train.py
CTC training loop for the IRICA captcha CRNN, with exact-match accuracy eval and
best-model checkpointing.

Usage
-----
    # 1) pretrain on synthetic
    python train.py --epochs 60 --select acc

    # 2) fine-tune on real captchas (low LR, fresh optimizer)
    python train.py --csv ../captchas_irica/labels_irica.csv \
                    --images ../captchas_irica \
                    --init-weights checkpoints/best_synth.pt \
                    --lr 2e-4 --epochs 80 --select acc \
                    --out checkpoints/best_real.pt
"""
from __future__ import annotations
import argparse
import random
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tqdm import tqdm

from config import CFG, CKPT_DIR, LABELS_CSV, IMAGES_DIR
from dataset import IricaCaptchaDataset, ctc_collate
from model import build_crnn
from utils import forced_decode_conf, corpus_metrics
from vocab import VOCAB


def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def resolve_device(choice: str = "auto") -> torch.device:
    if choice == "cpu":
        return torch.device("cpu")
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("--device cuda requested but no CUDA GPU is visible.")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def compute_input_lengths(batch_size: int, time_steps: int, device) -> torch.Tensor:
    return torch.full((batch_size,), time_steps, dtype=torch.long, device=device)


@torch.no_grad()
def evaluate(model, loader, device) -> dict:
    model.eval()
    preds_all, targets_all = [], []
    val_loss, n_batches = 0.0, 0
    ctc = nn.CTCLoss(blank=VOCAB.blank_idx, zero_infinity=True)
    for imgs, targets, target_lengths, texts in loader:
        imgs = imgs.to(device)
        log_probs = model(imgs)                       # (T, B, C)
        T, B, _ = log_probs.shape
        input_lengths = compute_input_lengths(B, T, device)
        loss = ctc(log_probs, targets.to(device), input_lengths, target_lengths.to(device))
        val_loss += loss.item()
        n_batches += 1
        # Fixed-length forced decode is our real metric (text only — the
        # confidence half of the tuple is a runtime concern, not a metric).
        preds_all.extend(text for text, _conf in forced_decode_conf(log_probs))
        targets_all.extend(texts)
    metrics = corpus_metrics(preds_all, targets_all)
    metrics["val_loss"] = val_loss / max(1, n_batches)
    # a few samples for eyeballing
    metrics["_samples"] = list(zip(preds_all[:4], targets_all[:4]))
    return metrics


def _atomic_save(obj, path: Path):
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(obj, tmp)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=str, default=str(LABELS_CSV))
    ap.add_argument("--images", type=str, default=str(IMAGES_DIR))
    ap.add_argument("--epochs", type=int, default=CFG.train.epochs)
    ap.add_argument("--batch-size", type=int, default=CFG.train.batch_size)
    ap.add_argument("--lr", type=float, default=CFG.train.lr)
    ap.add_argument("--num-workers", type=int, default=CFG.train.num_workers)
    ap.add_argument("--limit", type=int, default=0,
                    help="use only the first N samples (0=all) — fast smoke run")
    ap.add_argument("--resume", type=str, default="")
    ap.add_argument("--init-weights", type=str, default="",
                    help="load ONLY model weights (fresh optimizer + epoch 0) — "
                         "for fine-tuning a synthetic-pretrained model on real data")
    ap.add_argument("--out", type=str, default=str(CKPT_DIR / "best.pt"),
                    help="path for the best checkpoint")
    ap.add_argument("--device", type=str, default="auto",
                    choices=["auto", "cpu", "cuda"])
    ap.add_argument("--select", type=str, default="acc",
                    choices=["val_loss", "acc"],
                    help="best.pt selection metric (default acc — our real goal)")
    args = ap.parse_args()

    set_seed(CFG.train.seed)
    device = resolve_device(args.device)

    if device.type == "cpu":
        import os
        n_cores = os.cpu_count() or 4
        torch.set_num_threads(n_cores)
        if os.name == "nt" and args.num_workers > 0:
            print("[cpu/windows] forcing num_workers=0 (spawn is slow here)")
            args.num_workers = 0
        print(f"[cpu] using {n_cores} threads")

    print(f"device: {device} | vocab: {len(VOCAB)} classes")

    # --- data ---------------------------------------------------------- #
    train_full = IricaCaptchaDataset(csv_path=Path(args.csv), images_dir=Path(args.images), augment=True)
    val_full = IricaCaptchaDataset(csv_path=Path(args.csv), images_dir=Path(args.images), augment=False)
    n_total = len(train_full)
    perm = torch.randperm(n_total, generator=torch.Generator().manual_seed(CFG.train.seed)).tolist()
    if args.limit and args.limit < n_total:
        perm = perm[:args.limit]
        n_total = args.limit
        print(f"[limit] using only {n_total} samples")
    n_val = max(1, int(n_total * CFG.train.val_split))
    n_train = n_total - n_val
    train_idx, val_idx = perm[:n_train], perm[n_train:]
    train_ds = torch.utils.data.Subset(train_full, train_idx)
    val_ds = torch.utils.data.Subset(val_full, val_idx)

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=args.num_workers, collate_fn=ctc_collate, drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.num_workers, collate_fn=ctc_collate)
    print(f"train: {n_train}  val: {n_val}")

    # --- model / optim ------------------------------------------------- #
    model = build_crnn().to(device)
    if args.init_weights:
        ck = torch.load(args.init_weights, map_location=device, weights_only=False)
        model.load_state_dict(ck["model"])
        print(f"init weights from {args.init_weights} (fresh optimizer)")
    optim = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=CFG.train.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optim, mode="min", factor=0.5, patience=5)
    ctc = nn.CTCLoss(blank=VOCAB.blank_idx, zero_infinity=True)

    start_epoch = 0
    best_metric = float("inf")       # early-stop tracks val_loss
    best_select = float("inf") if args.select == "val_loss" else -1.0
    if args.resume:
        ck = torch.load(args.resume, map_location=device, weights_only=False)
        model.load_state_dict(ck["model"])
        optim.load_state_dict(ck["optim"])
        start_epoch = ck.get("epoch", 0) + 1
        print(f"resumed from {args.resume} at epoch {start_epoch}")

    out_path = Path(args.out)
    epochs_no_improve = 0
    monitor = "val_loss"

    for epoch in range(start_epoch, args.epochs):
        model.train()
        running, t0 = 0.0, time.time()
        pbar = tqdm(train_loader, desc=f"epoch {epoch+1}/{args.epochs}", leave=False)
        for imgs, targets, target_lengths, texts in pbar:
            imgs = imgs.to(device)
            log_probs = model(imgs)
            T, B, _ = log_probs.shape
            input_lengths = compute_input_lengths(B, T, device)
            loss = ctc(log_probs, targets.to(device), input_lengths, target_lengths.to(device))
            optim.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), CFG.train.grad_clip)
            optim.step()
            running += loss.item()
            pbar.set_postfix(loss=f"{loss.item():.3f}")

        train_loss = running / max(1, len(train_loader))
        metrics = evaluate(model, val_loader, device)
        scheduler.step(metrics["val_loss"])

        print(f"[{epoch+1:03d}] train_loss={train_loss:.4f}  val_loss={metrics['val_loss']:.4f}  "
              f"CER={metrics['cer']:.4f}  acc={metrics['acc']:.3f}  ({time.time()-t0:.1f}s)")
        for p, t in metrics["_samples"]:
            print(f"      pred: {p!r}  true: {t!r}")

        current = metrics[monitor]
        ckpt = {
            "model": model.state_dict(), "optim": optim.state_dict(), "epoch": epoch,
            "vocab_itos": VOCAB.itos,
            "config": {"height": CFG.image.height, "width": CFG.image.width},
        }
        _atomic_save(ckpt, CKPT_DIR / "last.pt")

        if args.select == "acc":
            sel_val, improved = metrics["acc"], metrics["acc"] > best_select
        else:
            sel_val, improved = current, current < best_select
        if improved:
            best_select = sel_val
            _atomic_save(ckpt, out_path)
            print(f"      ✓ new best ({args.select}={best_select:.4f}) -> {out_path.name}")

        if current < best_metric:
            best_metric = current
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= CFG.train.patience:
                print(f"early stopping (no val_loss improvement for {CFG.train.patience} epochs)")
                break

    print(f"done. best {args.select} saved to {out_path}; final val_loss={best_metric:.4f}")


if __name__ == "__main__":
    main()
