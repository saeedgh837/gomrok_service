"""
dataset.py
PyTorch Dataset for the IRICA captcha + light augmentation + CTC collate.

CSV format (filename,text):
    synth_00001.png,a3x9k
    ...
Images are looked up under `images_dir`. Real captchas and synthetic captchas
use the SAME normalized-grayscale pipeline (preprocess.image_to_model_input) so
the model sees a consistent input at train and fine-tune time.
"""
from __future__ import annotations
import random
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from config import IMAGES_DIR, LABELS_CSV
from vocab import VOCAB
from preprocess import to_model_gray, trim_whole, prepare_line_for_model


# --------------------------------------------------------------------------- #
# Augmentation (text-safe: geometry + stroke/elastic jitter, no flips).
# The red-line distractor is baked into the SYNTHETIC generator, not here, so
# augmentation only varies glyph shape/scale/noise.
# --------------------------------------------------------------------------- #
class LineAugmenter:
    def __init__(
        self,
        rotate_deg: float = 4.0,
        scale_range: Tuple[float, float] = (0.9, 1.1),
        perspective: float = 0.03,
        p: float = 0.5,
        stroke_jitter_p: float = 0.4,
        elastic_p: float = 0.3,
        elastic_sigma: float = 4.0,
        elastic_alpha: float = 6.0,
        noise_sigma: float = 0.03,
    ):
        self.rotate_deg = rotate_deg
        self.scale_range = scale_range
        self.perspective = perspective
        self.p = p
        self.stroke_jitter_p = stroke_jitter_p
        self.elastic_p = elastic_p
        self.elastic_sigma = elastic_sigma
        self.elastic_alpha = elastic_alpha
        self.noise_sigma = noise_sigma

    def _stroke_jitter(self, img: np.ndarray) -> np.ndarray:
        """Thicken/thin ink via morphology (text bright on dark)."""
        k = np.ones((2, 2), np.uint8)
        u8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
        if random.random() < 0.5:
            u8 = cv2.dilate(u8, k, iterations=1)
        else:
            u8 = cv2.erode(u8, k, iterations=1)
        return u8.astype(np.float32) / 255.0

    def _elastic(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape
        dx = np.random.uniform(-1, 1, (h, w)).astype(np.float32)
        dy = np.random.uniform(-1, 1, (h, w)).astype(np.float32)
        ks = int(self.elastic_sigma) * 4 + 1
        dx = cv2.GaussianBlur(dx, (ks, ks), self.elastic_sigma) * self.elastic_alpha
        dy = cv2.GaussianBlur(dy, (ks, ks), self.elastic_sigma) * self.elastic_alpha
        xx, yy = np.meshgrid(np.arange(w), np.arange(h))
        map_x = (xx + dx).astype(np.float32)
        map_y = (yy + dy).astype(np.float32)
        return cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)

    def __call__(self, img: np.ndarray) -> np.ndarray:
        """img: float32 (H, W) in [0,1]. Returns same shape/type."""
        h, w = img.shape

        if random.random() < self.stroke_jitter_p:
            img = self._stroke_jitter(img)

        if random.random() < self.elastic_p:
            img = self._elastic(img)

        if random.random() < self.p:
            angle = random.uniform(-self.rotate_deg, self.rotate_deg)
            scale = random.uniform(*self.scale_range)
            M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
            img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=0.0)

        if random.random() < self.p:
            d = self.perspective * min(h, w)
            src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
            jit = np.float32([[random.uniform(-d, d), random.uniform(-d, d)] for _ in range(4)])
            M = cv2.getPerspectiveTransform(src, src + jit)
            img = cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=0.0)

        if random.random() < self.p:
            img = np.clip(img + np.random.normal(0, self.noise_sigma, img.shape), 0, 1)

        return img.astype(np.float32)


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #
class IricaCaptchaDataset(Dataset):
    def __init__(
        self,
        csv_path: Path = LABELS_CSV,
        images_dir: Path = IMAGES_DIR,
        augment: bool = False,
    ):
        self.df = pd.read_csv(csv_path)
        assert {"filename", "text"}.issubset(self.df.columns), \
            "CSV must have columns: filename,text"
        self.images_dir = Path(images_dir)
        self.augment = LineAugmenter() if augment else None
        # Pre-normalize labels so metrics/decoding are consistent.
        self.df["text"] = self.df["text"].astype(str).map(VOCAB.normalize)
        # Drop rows whose label didn't survive normalization to the right length.
        from config import CAPTCHA_LEN
        bad = self.df["text"].str.len() != CAPTCHA_LEN
        if bad.any():
            print(f"[dataset] dropping {int(bad.sum())} rows with != {CAPTCHA_LEN} chars")
            self.df = self.df[~bad].reset_index(drop=True)

    def __len__(self) -> int:
        return len(self.df)

    def _load_array(self, path: Path) -> np.ndarray:
        # Unicode-safe read (paths may contain non-ASCII on Windows).
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(path)
        gray = to_model_gray(img)
        gray = trim_whole(gray)
        return prepare_line_for_model(gray)  # (H, W) float32 [0,1]

    def __getitem__(self, idx: int):
        row = self.df.iloc[idx]
        arr = self._load_array(self.images_dir / row["filename"])
        if self.augment is not None:
            arr = self.augment(arr)
        img_t = torch.from_numpy(arr).unsqueeze(0)                 # (1, H, W)
        target = torch.tensor(VOCAB.encode(row["text"]), dtype=torch.long)
        return img_t, target, row["text"]


# --------------------------------------------------------------------------- #
# Collate for CTC: flatten targets, record lengths.
# --------------------------------------------------------------------------- #
def ctc_collate(batch):
    imgs, targets, texts = zip(*batch)
    imgs = torch.stack(imgs, dim=0)                       # (B, 1, H, W)
    target_lengths = torch.tensor([len(t) for t in targets], dtype=torch.long)
    targets_concat = torch.cat(targets) if len(targets) else torch.tensor([], dtype=torch.long)
    return imgs, targets_concat, target_lengths, list(texts)


if __name__ == "__main__":
    ds = IricaCaptchaDataset(augment=True)
    print("items:", len(ds))
    if len(ds):
        img_t, tgt, txt = ds[0]
        print("img:", img_t.shape, "target len:", len(tgt), "text:", txt)
