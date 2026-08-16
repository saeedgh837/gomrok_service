"""
vocab.py
Persian character vocabulary + normalization + CTC-friendly encode/decode.

The CTC "blank" token is reserved at index 0 (required by torch's CTCLoss
default `blank=0`). Real characters start at index 1.
"""
from __future__ import annotations
import re
from typing import List

# --------------------------------------------------------------------------- #
# Character set
# --------------------------------------------------------------------------- #
# Persian letters (isolated base forms — reshaping is a *rendering* concern,
# the label text stays in logical/base characters).
PERSIAN_LETTERS = list("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیآئ")
# Persian digits + ASCII digits (spelled-out numbers usually need neither,
# but keeping digits makes the vocab reusable).
DIGITS = list("۰۱۲۳۴۵۶۷۸۹0123456789")
# Punctuation / separators that can appear in the labels.
PUNCT = list(" ‌،.-")   # note: includes ZWNJ (U+200C) and normal space

# Order is fixed & deterministic; do NOT reorder after training a model.
CHARS: List[str] = PERSIAN_LETTERS + DIGITS + PUNCT

BLANK = "<blank>"          # CTC blank, index 0
UNK = "<unk>"              # out-of-vocab fallback


class Vocab:
    def __init__(self, chars: List[str] = None):
        chars = chars if chars is not None else CHARS
        # Deduplicate while preserving order.
        seen, ordered = set(), []
        for c in chars:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        # index 0 = blank, then chars, then <unk> at the end
        self.itos = [BLANK] + ordered + [UNK]
        self.stoi = {c: i for i, c in enumerate(self.itos)}
        self.blank_idx = 0
        self.unk_idx = self.stoi[UNK]

    def __len__(self) -> int:
        return len(self.itos)

    # ------------------------------------------------------------------ #
    # Normalization: collapse Arabic look-alikes to canonical Persian.
    # ------------------------------------------------------------------ #
    _NORMALIZE_MAP = {
        "ي": "ی", "ك": "ک",            # Arabic yeh/kaf -> Persian
        "ة": "ه", "ۀ": "ه",
        "ﻻ": "لا",
        "٠": "۰", "١": "۱", "٢": "۲", "٣": "۳", "٤": "۴",
        "٥": "۵", "٦": "۶", "٧": "۷", "٨": "۸", "٩": "۹",
        "ي": "ی",
    }

    @classmethod
    def normalize(cls, text: str) -> str:
        text = text.strip()
        for a, b in cls._NORMALIZE_MAP.items():
            text = text.replace(a, b)
        # Remove Arabic diacritics / harakat.
        text = re.sub(r"[ً-ٰٟ]", "", text)
        # Collapse repeated whitespace to a single space.
        text = re.sub(r"\s+", " ", text)
        return text

    # ------------------------------------------------------------------ #
    # Encode / decode
    # ------------------------------------------------------------------ #
    def encode(self, text: str) -> List[int]:
        """Text -> list of class indices (no blanks)."""
        text = self.normalize(text)
        return [self.stoi.get(ch, self.unk_idx) for ch in text]

    def decode_indices(self, indices: List[int]) -> str:
        """Raw index list -> string (does NOT collapse CTC repeats)."""
        out = []
        for i in indices:
            if i == self.blank_idx:
                continue
            tok = self.itos[i]
            if tok in (BLANK, UNK):
                continue
            out.append(tok)
        return "".join(out)

    def ctc_greedy_decode(self, indices: List[int]) -> str:
        """
        Collapse repeated tokens and remove blanks (standard CTC greedy).
        `indices` is the per-timestep argmax over the full sequence.
        """
        out, prev = [], None
        for i in indices:
            if i != prev and i != self.blank_idx:
                tok = self.itos[i]
                if tok not in (BLANK, UNK):
                    out.append(tok)
            prev = i
        return "".join(out)


# Singleton used across the project.
VOCAB = Vocab()


if __name__ == "__main__":
    v = VOCAB
    s = "دو هزار و نهصد و هفتاد و پنج"
    enc = v.encode(s)
    print("vocab size:", len(v))
    print("encoded    :", enc)
    print("roundtrip  :", v.decode_indices(enc))
    assert v.decode_indices(enc) == v.normalize(s)
    print("OK")
