"""
solve.py
حل‌کننده‌ی نهاییِ کپچای فارسیِ همتا.

استراتژی: چون submitِ جوابِ غلط در سایتِ هدف جریمه‌ای ندارد و کپچا آزادانه
refresh می‌شود، solver همیشه «بهترین حدس» (snap به نزدیک‌ترین عددِ معتبرِ
۱۰۰۰–۹۹۹۹) را برمی‌گرداند — نه gateِ hard. اگر سایت رد کرد، صدازننده کپچا را
refresh و دوباره صدا می‌زند. با دقتِ ~۶۶٪ در هر تلاش، به‌طور متوسط ~۱.۵ تلاش
تا موفقیت.

فیلدِ `confidence` (min_word_conf) هم برمی‌گردد تا اگر خواستی می‌توانی تلاش‌های
کم‌اطمینان را مستقیم skip کنی (بدونِ submit) و سریع‌تر refresh بزنی.

کاربرد:
    python solve.py --image path/to/captcha.png
    python solve.py --dir path/to/folder            # همه‌ی captcha_*.png را حل کن
    python solve.py --dir folder --csv out.csv       # نتیجه را در CSV بریز
"""
from __future__ import annotations
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cv2  # noqa: E402
import torch  # noqa: E402

from config import CKPT_DIR  # noqa: E402
from model import build_crnn  # noqa: E402
from preprocess import to_model_gray, trim_whole, prepare_line_for_model  # noqa: E402
from utils import greedy_decode  # noqa: E402
from vocab import VOCAB  # noqa: E402
import number_decode as nd  # noqa: E402


class CaptchaSolver:
    """مدل را یک‌بار لود می‌کند؛ solve() را هر بار برای یک تصویر صدا بزن."""

    def __init__(self, ckpt_path: str = None, device: str = "auto"):
        if device in (None, "auto"):
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            dev = device
        self.device = torch.device(dev)
        ckpt_path = ckpt_path or str(CKPT_DIR / "best.pt")
        self.model = build_crnn().to(self.device)
        ck = torch.load(ckpt_path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(ck["model"])
        self.model.eval()
        self.ckpt_path = ckpt_path

    @torch.no_grad()
    def solve(self, image_path: str) -> dict:
        """
        یک تصویرِ کپچا -> بهترین حدسِ عددی.

        خروجی dict:
          number     : int  (بهترین حدس، همیشه عدد؛ هرگز None مگر تصویر خراب)
          text       : str  (متنِ خامِ خوانده‌شده)
          confidence : float (min_word_conf ∈ [0,1]، برای skipِ اختیاری)
          snap_distance : int (فاصله‌ی ویرایشی تا نزدیک‌ترین عددِ معتبر؛ 0=دقیق)
        """
        bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(image_path)
        gray = to_model_gray(bgr)
        arr = prepare_line_for_model(trim_whole(gray))
        batch = torch.from_numpy(arr).unsqueeze(0).unsqueeze(0).to(self.device)

        log_probs = self.model(batch)                 # (T, 1, C)
        if log_probs is None:
            return {"number": None, "text": "", "confidence": 0.0,
                    "snap_distance": -1}

        text = VOCAB.normalize(greedy_decode(log_probs)[0])
        snap_n, snap_d = nd.nearest_number(text)
        sig = nd.confidence_signals(log_probs, b=0)
        return {
            "number": snap_n,
            "text": text,
            "confidence": round(sig["min_word_conf"], 3),
            "snap_distance": snap_d,
        }


def main():
    ap = argparse.ArgumentParser(description="Persian hamta captcha solver")
    ap.add_argument("--image", help="یک تصویرِ کپچا")
    ap.add_argument("--dir", help="پوشه‌ای از captcha_*.png برای حلِ دسته‌ای")
    ap.add_argument("--glob", default="captcha_*.png",
                    help="الگوی فایل در حالتِ --dir (پیش‌فرض captcha_*.png)")
    ap.add_argument("--csv", default=None,
                    help="در حالتِ --dir: نتیجه را در این CSV بریز (filename,number)")
    ap.add_argument("--ckpt", default=None, help="پیش‌فرض: checkpoints/best.pt")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = ap.parse_args()

    if not args.image and not args.dir:
        ap.error("یکی از --image یا --dir را بده")

    solver = CaptchaSolver(ckpt_path=args.ckpt, device=args.device)

    if args.image:
        r = solver.solve(args.image)
        print(f"{r['number']}   (conf={r['confidence']}, d={r['snap_distance']}, "
              f"text='{r['text']}')")
        return

    # حالتِ دسته‌ای
    img_dir = Path(args.dir)
    files = sorted(
        img_dir.glob(args.glob),
        key=lambda p: [int(t) if t.isdigit() else t
                       for t in __import__("re").split(r"(\d+)", p.name)],
    )
    if not files:
        raise SystemExit(f"هیچ فایلی با الگوی {args.glob} در {img_dir} نبود")

    rows = []
    for p in files:
        try:
            r = solver.solve(str(p))
            rows.append((p.name, r["number"], r["confidence"]))
            print(f"{p.name}: {r['number']}  (conf={r['confidence']})")
        except Exception as e:
            print(f"{p.name}: ERROR {e}")
            rows.append((p.name, "", 0.0))

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["filename", "number", "confidence"])
            w.writerows(rows)
        print(f"\n-> {len(rows)} نتیجه در {args.csv} ذخیره شد")


if __name__ == "__main__":
    main()
