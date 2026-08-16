"""
number_decode.py
Confidence-gated number decoding for the hamta 4-digit captcha OCR.

Turns the CRNN's raw log-probs into either a concrete integer (1000..9999) or
the sentinel string "hard" (meaning: not confident -> the solver should refresh
the captcha and try another). This is the right objective for a captcha solver:
we don't need to read every captcha, only to be RIGHT when we answer.

Key design insight
-------------------
A 4-digit number is up to 4 independent spelled word-slots joined by " و "
(thousands / hundreds / tens / ones). One wrong word => wrong number. So the
*mean* per-character confidence is the wrong aggregator (it dilutes one weak
slot across many easy chars — that is why a mean-confidence gate gave only ~31%
precision). The correctness-determining quantity is the WEAKEST word, so we gate
on `min_word_conf`, plus the strong structural prior that a prediction which is
already an exact canonical spelling (snap distance == 0) is hard to produce by
accident.

Pure module: numpy/torch + utils._edit_distance + vocab.VOCAB. No model, no cv2.
"""
from __future__ import annotations
from typing import List, Dict, Tuple

from utils import _edit_distance
from vocab import VOCAB

# --------------------------------------------------------------------------- #
# Vendored speller — copied verbatim from
# ai_projects/captcha_collector/labeler/label_server.py (spell + tables).
# Kept local so decoding never depends on the labeler package path. A drift
# guard in __main__ asserts a few known spellings.
# --------------------------------------------------------------------------- #
ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده",
         "هفده", "هجده", "نوزده"]
TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
HUND = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد",
        "هشتصد", "نهصد"]


def spell(n: int) -> str:
    if n == 0:
        return "صفر"
    parts = []
    thousands = n // 1000
    rest = n % 1000
    if thousands:
        parts.append(spell(thousands) + " هزار")
    h = rest // 100
    if h:
        parts.append(("یک" + HUND[h]) if h == 1 else HUND[h])
    t = (rest % 100) // 10
    o = rest % 10
    if t == 1:
        parts.append(TEENS[o])
    else:
        if t:
            parts.append(TENS[t])
        if o:
            parts.append(ONES[o])
    return " و ".join(parts)


# --------------------------------------------------------------------------- #
# Canonical tables (built once at import). Real hamta captchas are all 4-digit.
# --------------------------------------------------------------------------- #
DOMAIN = range(1000, 10000)
CANON: List[Tuple[int, str]] = [(n, VOCAB.normalize(spell(n))) for n in DOMAIN]
EXACT: Dict[str, int] = {t: n for n, t in CANON}


def nearest_number(text: str) -> Tuple[int, int]:
    """(number, edit_distance) of the closest canonical spelling.
    Fast path: exact dict hit -> distance 0. Else min edit distance over 9000."""
    text = VOCAB.normalize(text)
    hit = EXACT.get(text)
    if hit is not None:
        return hit, 0
    best_n, best_d = -1, 1 << 30
    for n, t in CANON:
        d = _edit_distance(text, t)
        if d < best_d:
            best_d, best_n = d, n
            if d == 1:  # can't beat 1 once exact (0) is ruled out
                break
    return best_n, best_d


# --------------------------------------------------------------------------- #
# Confidence signals from raw log-probs (T, C) for a single sample.
# --------------------------------------------------------------------------- #
def _emissions(logp_2d) -> List[dict]:
    """Reproduce CTC greedy collapse and attribute each EMITTED char to the
    timestep that produced it. Returns [{char, p1, margin}] in emit order.

    logp_2d: torch tensor (T, C) log-probs for one sample.
    """
    probs = logp_2d.exp()                       # (T, C)
    T, C = probs.shape
    argmax = probs.argmax(dim=1).tolist()
    # top-2 per timestep for the margin signal
    top2 = probs.topk(2, dim=1).values          # (T, 2)
    out = []
    prev = -1
    for t in range(T):
        a = argmax[t]
        if a != VOCAB.blank_idx and a != prev:
            p1 = float(top2[t, 0])
            p2 = float(top2[t, 1])
            out.append({"char": VOCAB.itos[a], "p1": p1, "margin": p1 - p2})
        prev = a
    return out


def confidence_signals(log_probs, b: int = 0) -> dict:
    """Aggregate per-word / per-char confidence for sample b of (T, B, C)."""
    emits = _emissions(log_probs[:, b, :])
    if not emits:
        return {"mean_conf": 0.0, "min_word_conf": 0.0, "min_char_conf": 0.0,
                "min_margin": 0.0, "n_words": 0, "n_chars": 0}
    # split emitted chars into words on the space token
    words: List[List[dict]] = [[]]
    for e in emits:
        if e["char"] == " ":
            if words[-1]:
                words.append([])
        else:
            words[-1].append(e)
    words = [w for w in words if w]
    all_p1 = [e["p1"] for e in emits if e["char"] != " "]
    all_mg = [e["margin"] for e in emits if e["char"] != " "]
    word_means = [sum(e["p1"] for e in w) / len(w) for w in words] or [0.0]
    return {
        "mean_conf": sum(all_p1) / len(all_p1) if all_p1 else 0.0,
        "min_word_conf": min(word_means),
        "min_char_conf": min(all_p1) if all_p1 else 0.0,
        "min_margin": min(all_mg) if all_mg else 0.0,
        "n_words": len(words),
        "n_chars": len(all_p1),
    }


# --------------------------------------------------------------------------- #
# Decision: accept (return number) or "hard" (refresh).
# --------------------------------------------------------------------------- #
def decide(snap_d: int, signals: dict, *, min_word_conf: float, d_max: int,
           min_margin: float = None, require_exact: bool = False) -> str:
    if require_exact and snap_d != 0:
        return "hard"
    if snap_d > d_max:
        return "hard"
    if signals["min_word_conf"] < min_word_conf:
        return "hard"
    if min_margin is not None and signals["min_margin"] < min_margin:
        return "hard"
    return "accept"


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # drift guard vs the labeler's canonical spelling
    assert spell(1291) == "یک هزار و دویست و نود و یک", spell(1291)
    assert spell(3300) == "سه هزار و سیصد", spell(3300)
    assert spell(9969) == "نه هزار و نهصد و شصت و نه", spell(9969)
    assert EXACT[VOCAB.normalize("سه هزار و سیصد")] == 3300
    assert nearest_number("سه هزار و سیصد")[1] == 0
    print(f"OK — CANON has {len(CANON)} spellings, self-tests pass")
