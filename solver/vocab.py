"""
vocab.py
Character vocabulary + CTC-friendly encode/decode for the IRICA captcha.

The alphabet is trivial compared to Persian OCR: lowercase a-z + 0-9. The CTC
"blank" token is reserved at index 0 (torch's CTCLoss default `blank=0`); real
characters start at index 1.
"""
from __future__ import annotations
from typing import List

from config import ALPHABET

BLANK = "<blank>"          # CTC blank, index 0
UNK = "<unk>"              # out-of-vocab fallback


class Vocab:
    def __init__(self, chars: str = ALPHABET):
        # Deduplicate while preserving order (defensive; ALPHABET has no dupes).
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
    # Normalization: the captcha is lowercase alphanumeric. Uppercase input
    # (e.g. a labeler typo) is folded to lowercase; anything else is dropped
    # so it can't poison a label.
    # ------------------------------------------------------------------ #
    @classmethod
    def normalize(cls, text: str) -> str:
        text = text.strip().lower()
        return "".join(ch for ch in text if ch in ALPHABET)

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
    s = "a3x9k"
    enc = v.encode(s)
    print("vocab size:", len(v))          # 36 + blank + unk = 38
    print("encoded   :", enc)
    print("roundtrip :", v.decode_indices(enc))
    assert v.decode_indices(enc) == s
    print("OK")
