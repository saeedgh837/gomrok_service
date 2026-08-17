"""Export the HAMTA captcha CRNN to ONNX and prove it matches PyTorch exactly.

Why: `estelam_hamta.py` solves the hamta modal captcha through
`Hamta-solver/live_solve.py`, which is a torch model. The packaged Windows
release ships onnxruntime instead of torch, so the hamta inquiry robot needs a
torch-free backend (`Hamta-solver/onnx_solve.py`). That substitution is only
acceptable if it reads captchas IDENTICALLY, so this script refuses to leave a
usable artifact behind unless every validation sample agrees on the full result
dict — number, text, confidence and snap distance.

What it compares
----------------
The real production paths, end to end, not just the raw graph:

    torch : HamtaSolver.read_bgr      (torch model + torch decoder + torch signals)
    onnx  : OnnxHamtaSolver.read_bgr  (onnxruntime + numpy decoder + numpy signals)

Validation samples
------------------
There is no labelled hamta captcha corpus on this machine, so the set is built
from what genuinely exists plus rendered Persian number spellings, which is what
the site shows: every canonical string comes from number_decode.CANON, so the
decoder's multi-word / space handling is exercised for real.

Usage
-----
    python lab/scripts/export_hamta_onnx.py
    python lab/scripts/export_hamta_onnx.py --dir path/to/real/pngs --n 300
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOLVER = ROOT / "Hamta-solver"
# the solver package is written to be imported flat (config, model, vocab, ...)
sys.path.insert(0, str(SOLVER))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from config import CFG  # noqa: E402
from model import build_crnn  # noqa: E402
import number_decode as nd  # noqa: E402

DEFAULT_CKPT = SOLVER / "checkpoints" / "best.pt"
DEFAULT_OUT = SOLVER / "checkpoints" / "best.onnx"

# Fonts that actually carry Persian glyphs on this machine.
PERSIAN_FONTS = [
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/times.ttf",
]


def load_torch_model(ckpt: Path):
    model = build_crnn()
    ck = torch.load(str(ckpt), map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()
    return model


def export(model, out: Path, height: int, width: int) -> None:
    """Export with a STATIC input shape.

    The CNN ends in AdaptiveMaxPool2d((1, None)); ONNX cannot express an adaptive
    pool whose output size is not constant, so a dynamic width axis fails to
    export outright. prepare_line_for_model() always resizes and pads to exactly
    CFG.image.height x CFG.image.width (48x512 here), so the graph only ever sees
    one shape and the CTC time axis stays fixed at width/4.
    """
    dummy = torch.zeros(1, CFG.image.channels, height, width, dtype=torch.float32)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, str(out),
        input_names=["image"], output_names=["log_probs"],
        opset_version=17, do_constant_folding=True,
    )


def usable_fonts() -> list[str]:
    fonts = []
    for path in PERSIAN_FONTS:
        if not Path(path).exists():
            continue
        try:
            f = ImageFont.truetype(path, 40)
            # Reject faces that cannot draw Persian at all.
            if f.getbbox("یک هزار")[2] > 0:
                fonts.append(path)
        except Exception:
            continue
    return fonts


def render_sample(text: str, font_path: str, rng: random.Random) -> np.ndarray:
    """A hamta-like line image: light background, dark Persian text, mild noise."""
    w, h = 380, 90
    img = Image.new("RGB", (w, h), (rng.randint(226, 246),) * 3)
    draw = ImageDraw.Draw(img)
    size = rng.randint(26, 36)
    font = ImageFont.truetype(font_path, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = max(2, (w - (bbox[2] - bbox[0])) // 2)
    y = max(2, (h - (bbox[3] - bbox[1])) // 2)
    ink = rng.randint(10, 60)
    draw.text((x, y), text, font=font, fill=(ink, ink, ink))
    arr = np.array(img)
    # a faint sweep line, like the real captcha's distractor
    if rng.random() < 0.7:
        y0, y1 = rng.randint(10, h - 10), rng.randint(10, h - 10)
        cv2.line(arr, (0, y0), (w, y1), (200, 60, 60), 1)
    noise = rng.randint(2, 10)
    arr = np.clip(arr.astype(np.int16) +
                  np.random.RandomState(rng.randint(0, 1 << 30)).randint(
                      -noise, noise + 1, arr.shape), 0, 255).astype(np.uint8)
    return arr[:, :, ::-1].copy()          # BGR for the solver pipeline


def synth_samples(count: int, workdir: Path, rng: random.Random) -> list[Path]:
    fonts = usable_fonts()
    if not fonts:
        raise SystemExit("no Persian-capable font found under C:/Windows/Fonts")
    canon = [t for _, t in nd.CANON]
    paths = []
    for i in range(count):
        text = rng.choice(canon)
        bgr = render_sample(text, rng.choice(fonts), rng)
        dest = workdir / f"hamta_{i:05d}.png"
        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            continue
        buf.tofile(str(dest))
        paths.append(dest)
    if not paths:
        raise SystemExit("synthetic generation produced no images")
    return paths


def real_samples() -> list[Path]:
    """Genuine hamta captcha crops captured from the site during development."""
    return sorted((ROOT / "lab" / "final").glob("hamta_*.png"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(DEFAULT_CKPT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--dir", default=None,
                    help="directory of real hamta captcha PNGs to validate against")
    ap.add_argument("--n", type=int, default=200, help="synthetic sample count")
    ap.add_argument("--keep-on-mismatch", action="store_true")
    args = ap.parse_args()

    ckpt, out = Path(args.ckpt), Path(args.out)
    if not ckpt.exists():
        print(f"FAIL  checkpoint not found: {ckpt}")
        return 2

    print(f"checkpoint : {ckpt} ({ckpt.stat().st_size / 1048576:.1f} MB)")
    model = load_torch_model(ckpt)
    export(model, out, CFG.image.height, CFG.image.width)
    print(f"exported   : {out} ({out.stat().st_size / 1048576:.1f} MB)")

    # Both production wrappers, each loading its own engine.
    from live_solve import HamtaSolver
    from onnx_solve import OnnxHamtaSolver
    t_solver = HamtaSolver(ckpt_path=str(ckpt), device="cpu")
    o_solver = OnnxHamtaSolver(onnx_path=str(out))

    rng = random.Random(20260817)             # deterministic sample set
    tmp = None
    samples = real_samples()
    n_real = len(samples)
    if args.dir:
        samples += sorted(Path(args.dir).rglob("*.png"))[:args.n]
        origin = f"{n_real} real + {len(samples) - n_real} from {args.dir}"
    else:
        tmp = Path(tempfile.mkdtemp(prefix="hamta-onnx-parity-"))
        samples += synth_samples(args.n, tmp, rng)
        origin = (f"{n_real} real captcha crops + {len(samples) - n_real} rendered "
                  "Persian number spellings")
    if not samples:
        print("FAIL  no validation samples found")
        return 2
    print(f"validating : {len(samples)} samples ({origin})")

    mismatches = []
    max_delta = 0.0
    try:
        for i, path in enumerate(samples, 1):
            data = np.fromfile(str(path), dtype=np.uint8)
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            t_res = t_solver.read_bgr(bgr)
            o_res = o_solver.read_bgr(bgr)
            if t_res != o_res:
                mismatches.append((path.name, t_res, o_res))
            if i % 50 == 0 or i == len(samples):
                print(f"  {i}/{len(samples)} checked, mismatches={len(mismatches)}")
        # raw logit delta, for the record
        probe = cv2.imdecode(np.fromfile(str(samples[0]), dtype=np.uint8),
                             cv2.IMREAD_COLOR)
        from preprocess import prepare_line_for_model, to_model_gray, trim_whole
        arr = prepare_line_for_model(trim_whole(to_model_gray(probe)))
        x = arr[None, None, :, :].astype(np.float32)
        with torch.no_grad():
            t_out = model(torch.from_numpy(x)).numpy()
        o_out = o_solver.sess.run([o_solver.output_name], {o_solver.input_name: x})[0]
        max_delta = float(np.max(np.abs(t_out - o_out)))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if mismatches:
        print(f"FAIL  {len(mismatches)}/{len(samples)} results differ")
        for name, t_res, o_res in mismatches[:10]:
            print(f"        {name}:\n          torch={t_res}\n          onnx ={o_res}")
        if not args.keep_on_mismatch:
            out.unlink(missing_ok=True)
            print("      deleted the ONNX artifact — do NOT ship it")
        return 1

    import onnxruntime as ort
    print(f"PASS  {len(samples)}/{len(samples)} results identical "
          f"(torch {torch.__version__} vs onnxruntime {ort.__version__}), "
          f"max|Δlogit|={max_delta:.2e}")
    print(f"RESULT: HAMTA ONNX export verified -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
