"""Export the CRNN captcha solver to ONNX and prove it matches PyTorch exactly.

The packaged Windows release ships onnxruntime (~6 MB) instead of PyTorch
(~1.1 GB). That trade is only acceptable if the exported graph produces
CHARACTER-IDENTICAL captcha readings, so this script refuses to emit a usable
artifact unless parity holds on every sample it checks.

What it does
------------
1. loads the PyTorch checkpoint (default: solver/checkpoints/best_real_v2.pt)
2. exports it to ONNX with a dynamic width axis (the preprocessor pads to a fixed
   192px today, but a dynamic axis costs nothing and avoids a silent trap later)
3. builds a validation set:
     * real captcha PNGs from --dir, if given
     * otherwise synthetic samples drawn with solver/tools/make_synthetic.py,
       i.e. the same distribution the model was trained on
4. runs BOTH models over every sample through the identical preprocessing and
   the identical forced-CTC decoder, and compares the decoded strings
5. exits non-zero (and deletes the .onnx) unless every sample agrees

Usage
-----
    python lab/scripts/export_solver_onnx.py                    # synthetic check
    python lab/scripts/export_solver_onnx.py --dir path/to/pngs # real captchas
    python lab/scripts/export_solver_onnx.py --n 300 --keep-on-mismatch
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOLVER = ROOT / "solver"
# the solver package is written to be imported flat (config, model, vocab, ...)
sys.path.insert(0, str(SOLVER))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

from config import CFG  # noqa: E402
from model import build_crnn  # noqa: E402
from preprocess import prepare_line_for_model, to_model_gray, trim_whole  # noqa: E402
from utils import forced_decode  # noqa: E402
from vocab import VOCAB  # noqa: E402

DEFAULT_CKPT = SOLVER / "checkpoints" / "best_real_v2.pt"
DEFAULT_OUT = SOLVER / "checkpoints" / "best_real_v2.onnx"


# --------------------------------------------------------------------------- #
# shared preprocessing — both engines MUST see the exact same tensor
# --------------------------------------------------------------------------- #
def tensor_from_path(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    gray = to_model_gray(bgr)
    arr = prepare_line_for_model(trim_whole(gray))
    return arr[None, None, :, :].astype(np.float32)      # (1,1,H,W)


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
    export outright. That costs nothing here: prepare_line_for_model() always
    resizes and pads to exactly CFG.image.height x CFG.image.width, so the graph
    only ever sees 48x192. The time axis of the output stays dynamic-free too
    (T = width/4 = 48), which keeps the decoder identical.
    """
    dummy = torch.zeros(1, CFG.image.channels, height, width, dtype=torch.float32)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, str(out),
        input_names=["image"], output_names=["log_probs"],
        opset_version=17, do_constant_folding=True,
    )


def decode(log_probs: np.ndarray) -> str:
    """Reuse the PyTorch forced decoder so decoding can never be the difference."""
    return forced_decode(torch.from_numpy(np.asarray(log_probs)))[0]


def decode_numpy(log_probs: np.ndarray) -> str:
    """The torch-free decoder that the shipped bundle actually uses."""
    from onnx_solve import _forced_decode
    return _forced_decode(np.asarray(log_probs))


# --------------------------------------------------------------------------- #
# validation samples
# --------------------------------------------------------------------------- #
def synth_samples(count: int, workdir: Path) -> list[Path]:
    """Generate captchas with the project's own synthetic generator.

    The generator's CLI writes into solver/data/images and REWRITES the tracked
    solver/data/labels.csv, so it is imported and driven in-process instead, with
    every file landing in a temp directory.
    """
    sys.path.insert(0, str(SOLVER / "tools"))
    import make_synthetic as gen

    fonts = gen.find_fonts()
    if not fonts:
        raise SystemExit("no usable fonts found under C:/Windows/Fonts")
    import random
    random.seed(20260817)                      # deterministic sample set
    paths = []
    for i in range(count):
        text = gen.random_text()
        img = gen.make_one(text, random.choice(fonts))
        dest = workdir / f"synth_{i:05d}_{text}.png"
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            continue
        buf.tofile(str(dest))
        paths.append(dest)
    if not paths:
        raise SystemExit("synthetic generation produced no images")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(DEFAULT_CKPT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--dir", default=None,
                    help="directory of real captcha PNGs to validate against")
    ap.add_argument("--n", type=int, default=200, help="sample count")
    ap.add_argument("--keep-on-mismatch", action="store_true",
                    help="do not delete the .onnx when parity fails (debugging)")
    args = ap.parse_args()

    ckpt, out = Path(args.ckpt), Path(args.out)
    if not ckpt.exists():
        print(f"FAIL  checkpoint not found: {ckpt}")
        return 2

    print(f"checkpoint : {ckpt} ({ckpt.stat().st_size / 1048576:.1f} MB)")
    model = load_torch_model(ckpt)
    export(model, out, CFG.image.height, CFG.image.width)
    print(f"exported   : {out} ({out.stat().st_size / 1048576:.1f} MB)")

    import onnxruntime as ort
    sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])

    tmp = None
    if args.dir:
        samples = sorted(Path(args.dir).rglob("*.png"))[:args.n]
        origin = f"real captchas from {args.dir}"
    else:
        tmp = Path(tempfile.mkdtemp(prefix="onnx-parity-"))
        samples = synth_samples(args.n, tmp)
        origin = "synthetic samples (same generator the model trained on)"
    if not samples:
        print("FAIL  no validation samples found")
        return 2
    print(f"validating : {len(samples)} {origin}")

    mismatches = []
    try:
        for i, path in enumerate(samples, 1):
            x = tensor_from_path(path)
            with torch.no_grad():
                t_out = model(torch.from_numpy(x)).numpy()
            o_out = sess.run(["log_probs"], {"image": x})[0]
            # torch model + torch decoder  vs  onnx model + numpy decoder:
            # this is the ACTUAL production path (onnxruntime + onnx_solve), so a
            # match proves the whole shipped chain, not just the graph.
            t_str = decode(t_out)
            o_str = decode_numpy(o_out)
            if t_str != o_str:
                mismatches.append((path.name, t_str, o_str))
            max_abs = float(np.max(np.abs(t_out - o_out)))
            if i % 50 == 0 or i == len(samples):
                print(f"  {i}/{len(samples)} checked, "
                      f"mismatches={len(mismatches)}, last max|Δlogit|={max_abs:.2e}")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if mismatches:
        print(f"FAIL  {len(mismatches)}/{len(samples)} decoded strings differ")
        for name, t_str, o_str in mismatches[:10]:
            print(f"        {name}: torch={t_str!r} onnx={o_str!r}")
        if not args.keep_on_mismatch:
            out.unlink(missing_ok=True)
            print("      deleted the ONNX artifact — do NOT ship it")
        return 1

    print(f"PASS  {len(samples)}/{len(samples)} decoded strings identical "
          f"(torch {torch.__version__} vs onnxruntime {ort.__version__})")
    print(f"RESULT: ONNX export verified -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
