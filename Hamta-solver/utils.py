"""
utils.py
Metrics (CER / WER) and CTC decoders (greedy + optional beam search).
"""
from __future__ import annotations
from typing import List

import torch

from vocab import VOCAB

# Prefer the fast C implementation; fall back to a pure-python DP.
try:
    import Levenshtein  # python-Levenshtein
    _HAVE_LEV = True
except Exception:  # pragma: no cover
    _HAVE_LEV = False


# --------------------------------------------------------------------------- #
# Edit distance
# --------------------------------------------------------------------------- #
def _edit_distance(a: List, b: List) -> int:
    if _HAVE_LEV and isinstance(a, str) and isinstance(b, str):
        return Levenshtein.distance(a, b)
    # generic DP over token lists (used for word-level WER)
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


# --------------------------------------------------------------------------- #
# CER / WER
# --------------------------------------------------------------------------- #
def cer(pred: str, target: str) -> float:
    """Character Error Rate = edit_distance(chars) / len(target chars)."""
    target = VOCAB.normalize(target)
    pred = VOCAB.normalize(pred)
    if len(target) == 0:
        return 0.0 if len(pred) == 0 else 1.0
    return _edit_distance(pred, target) / len(target)


def wer(pred: str, target: str) -> float:
    """Word Error Rate = edit_distance(words) / num_target_words."""
    t_words = VOCAB.normalize(target).split()
    p_words = VOCAB.normalize(pred).split()
    if len(t_words) == 0:
        return 0.0 if len(p_words) == 0 else 1.0
    return _edit_distance(p_words, t_words) / len(t_words)


def corpus_metrics(preds: List[str], targets: List[str]) -> dict:
    """Aggregate CER/WER + exact-match accuracy over a list of pairs."""
    assert len(preds) == len(targets)
    tot_c_err = tot_c = tot_w_err = tot_w = 0
    exact = 0
    for p, t in zip(preds, targets):
        t_norm, p_norm = VOCAB.normalize(t), VOCAB.normalize(p)
        tot_c_err += _edit_distance(p_norm, t_norm)
        tot_c += max(1, len(t_norm))
        tw, pw = t_norm.split(), p_norm.split()
        tot_w_err += _edit_distance(pw, tw)
        tot_w += max(1, len(tw))
        exact += int(p_norm == t_norm)
    return {
        "cer": tot_c_err / tot_c,
        "wer": tot_w_err / tot_w,
        "acc": exact / len(preds) if preds else 0.0,
    }


# --------------------------------------------------------------------------- #
# CTC decoding
# --------------------------------------------------------------------------- #
@torch.no_grad()
def greedy_decode(log_probs: torch.Tensor) -> List[str]:
    """
    log_probs: (T, B, C). Returns a list of B decoded strings via best-path
    (argmax per timestep) + CTC collapse.
    """
    argmax = log_probs.argmax(dim=2)          # (T, B)
    argmax = argmax.permute(1, 0).cpu().tolist()  # (B, T)
    return [VOCAB.ctc_greedy_decode(seq) for seq in argmax]


@torch.no_grad()
def beam_search_decode(log_probs: torch.Tensor, beam_width: int = 10) -> List[str]:
    """
    Simple prefix beam search (no external LM). O(T * beam * C) per sample.
    log_probs: (T, B, C). Returns list of B strings.

    For most spelled-out-number tasks greedy is enough; beam helps when strokes
    are ambiguous. Kept dependency-free for portability.
    """
    T, B, C = log_probs.shape
    probs = log_probs.exp().cpu().numpy()
    blank = VOCAB.blank_idx
    results = []
    for b in range(B):
        # beam: dict prefix(tuple of idx) -> (p_blank, p_nonblank)
        beams = {(): (1.0, 0.0)}
        for t in range(T):
            next_beams = {}
            pt = probs[t, b]
            # prune to top-k symbols this step for speed
            top = pt.argsort()[-beam_width:]
            for prefix, (pb, pnb) in beams.items():
                for c in top:
                    p = pt[c]
                    if c == blank:
                        nb, nnb = next_beams.get(prefix, (0.0, 0.0))
                        next_beams[prefix] = (nb + (pb + pnb) * p, nnb)
                    else:
                        last = prefix[-1] if prefix else None
                        if c == last:
                            # extend collapse: same char after non-blank
                            nb, nnb = next_beams.get(prefix, (0.0, 0.0))
                            next_beams[prefix] = (nb, nnb + pnb * p)
                            newp = prefix + (c,)
                            nb2, nnb2 = next_beams.get(newp, (0.0, 0.0))
                            next_beams[newp] = (nb2, nnb2 + pb * p)
                        else:
                            newp = prefix + (c,)
                            nb2, nnb2 = next_beams.get(newp, (0.0, 0.0))
                            next_beams[newp] = (nb2, nnb2 + (pb + pnb) * p)
            # keep top beams
            beams = dict(
                sorted(next_beams.items(), key=lambda kv: kv[1][0] + kv[1][1], reverse=True)[:beam_width]
            )
        best = max(beams.items(), key=lambda kv: kv[1][0] + kv[1][1])[0]
        results.append(VOCAB.decode_indices(list(best)))
    return results


if __name__ == "__main__":
    print("CER:", cer("دو هزار و نهصد", "دو هزار و نهصد و پنج"))
    print("WER:", wer("دو هزار نهصد", "دو هزار و نهصد"))
    print(corpus_metrics(["سلام", "دنیا"], ["سلام", "دنیا"]))
