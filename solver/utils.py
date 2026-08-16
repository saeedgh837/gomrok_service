"""
utils.py
Metrics (CER / exact-match accuracy) and CTC decoding for the IRICA solver.

Because the captcha is a FIXED-length 5-char string, exact-match accuracy is
the metric that matters (a captcha is only "solved" if all 5 chars are right).
We also expose a length-forced greedy decoder that snaps the CTC output to
exactly `CAPTCHA_LEN` characters, which meaningfully lifts accuracy when the
model is off by one blank.
"""
from __future__ import annotations
from typing import List

import torch

from config import CAPTCHA_LEN
from vocab import VOCAB

try:
    import Levenshtein  # python-Levenshtein
    _HAVE_LEV = True
except Exception:  # pragma: no cover
    _HAVE_LEV = False


# --------------------------------------------------------------------------- #
# Edit distance
# --------------------------------------------------------------------------- #
def _edit_distance(a: str, b: str) -> int:
    if _HAVE_LEV:
        return Levenshtein.distance(a, b)
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


def cer(pred: str, target: str) -> float:
    """Character Error Rate = edit_distance(chars) / len(target chars)."""
    target = VOCAB.normalize(target)
    pred = VOCAB.normalize(pred)
    if len(target) == 0:
        return 0.0 if len(pred) == 0 else 1.0
    return _edit_distance(pred, target) / len(target)


def corpus_metrics(preds: List[str], targets: List[str]) -> dict:
    """Aggregate CER + exact-match accuracy over a list of pairs."""
    assert len(preds) == len(targets)
    tot_c_err = tot_c = exact = 0
    for p, t in zip(preds, targets):
        t_norm, p_norm = VOCAB.normalize(t), VOCAB.normalize(p)
        tot_c_err += _edit_distance(p_norm, t_norm)
        tot_c += max(1, len(t_norm))
        exact += int(p_norm == t_norm)
    return {
        "cer": tot_c_err / tot_c,
        "acc": exact / len(preds) if preds else 0.0,
    }


# --------------------------------------------------------------------------- #
# CTC decoding
# --------------------------------------------------------------------------- #
@torch.no_grad()
def greedy_decode(log_probs: torch.Tensor) -> List[str]:
    """
    log_probs: (T, B, C). Returns B strings via best-path (argmax per timestep)
    + standard CTC collapse (no length constraint).
    """
    argmax = log_probs.argmax(dim=2)              # (T, B)
    argmax = argmax.permute(1, 0).cpu().tolist()  # (B, T)
    return [VOCAB.ctc_greedy_decode(seq) for seq in argmax]


@torch.no_grad()
def forced_decode(log_probs: torch.Tensor, length: int = CAPTCHA_LEN) -> List[str]:
    """
    Length-constrained greedy decode: always return exactly `length` chars.

    The captcha length is known and constant, so instead of trusting the CTC
    collapse to produce the right count we:
      1. take the plain greedy string;
      2. if it already has `length` chars, keep it;
      3. otherwise pick the `length` most-confident non-blank character
         positions from the per-timestep argmax path and read them left->right.

    This recovers the common failure where a doubled/dropped blank makes greedy
    emit 4 or 6 chars. Two directions:
      * too many chars  -> keep the `length` most-confident runs;
      * too few chars   -> a doubled letter (e.g. "ee", "55") was collapsed by
        CTC into one run; split the widest runs (longest time-span, so most
        likely to cover two glyphs) until we reach `length` characters.
    """
    T, B, C = log_probs.shape
    probs = log_probs.exp()                        # (T, B, C)
    argmax = log_probs.argmax(dim=2)               # (T, B)
    topval = probs.max(dim=2).values               # (T, B) confidence per step
    out = []
    for b in range(B):
        path = argmax[:, b].cpu().tolist()
        greedy = VOCAB.ctc_greedy_decode(path)
        if len(greedy) == length:
            out.append(greedy)
            continue
        # Collapse the path into runs of the same non-blank symbol, keeping the
        # peak confidence, start position, and time-span of each run.
        runs = []  # [start_t, symbol_idx, peak_conf, span]
        prev = None
        conf = topval[:, b].cpu().tolist()
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
            # keep the `length` most-confident runs, then restore left->right order
            best = sorted(runs, key=lambda r: r[2], reverse=True)[:length]
            best.sort(key=lambda r: r[0])
            s = "".join(VOCAB.itos[r[1]] for r in best)
        elif runs:
            # Too few runs: a doubled adjacent letter got collapsed. Repeatedly
            # duplicate the run whose span is largest relative to how many glyphs
            # we've already assigned it (span / count) — that's the run most
            # likely to actually cover multiple identical characters.
            counts = [1] * len(runs)
            need = length - len(runs)
            for _ in range(need):
                j = max(range(len(runs)),
                        key=lambda k: runs[k][3] / counts[k])
                counts[j] += 1
            s = "".join(VOCAB.itos[runs[k][1]] * counts[k]
                        for k in range(len(runs)))
        else:
            # no non-blank runs at all — nothing to recover
            s = greedy
        out.append(s)
    return out


if __name__ == "__main__":
    print("CER:", cer("a3x9k", "a3x8k"))
    print(corpus_metrics(["a3x9k", "bb112"], ["a3x9k", "bb113"]))
