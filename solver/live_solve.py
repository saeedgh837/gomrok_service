"""
live_solve.py
Pol-e ettesal-e model-e OCR (best_real.pt) be jaryan-e collector.

Do karbord:
  1) be onvan-e module: collector (ya har code-e digar) `load_solver()` ra yek bar
     seda mizanad va sepas `solve_pil(img)` ya `solve_array(bgr)` ra rooye har
     captcha seda mizanad ta reshte-ye 5-harfi bargardad.
  2) be onvan-e script-e mostaghel: rooye poushe-ye aks-ha BEDUN-E morurgar ejra
     mishavad (test-e offline ke HANDOFF tosiye karde) ta motmaen shavim mesir-e
     in-memory salem ast va, agar CSV-ye label bood, deghat ra ham gozaresh mikonad.

        python live_solve.py --dir ../laptop/captchas_irica
        python live_solve.py --dir ../laptop/captchas_irica \
               --csv ../laptop/captchas_irica/labels_irica.csv

Nemune-ye ettesal be collector (dar halghe-ye asli, ba'd az crop):
--------------------------------------------------------------------
    from solver.live_solve import load_solver
    solver = load_solver()                       # yek bar, ghabl az halghe
    ...
    crop = crop_captcha_with_padding(driver, captcha)   # PIL.Image
    answer = solver.read_pil(crop)               # reshte-ye 5-harfi
    # sepas: field-e voroodi-ye captcha ra por kon va submit kon
    #        (selector-e field hanooz dar sites/irica.py nist -> bayad ezafe shavad)
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Default: fine-tune-shode rooye captcha-ye vaghe'i.
_DEFAULT_CKPT = "checkpoints/best_real.pt"


def _module_importable(name: str) -> bool:
    """True when `name` can be found on sys.path WITHOUT importing it."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _torch_backend_available() -> bool:
    """True only when the torch backend can actually be constructed.

    torch being importable is NOT sufficient: the torch path needs `predict.py`,
    which the packaged release deliberately does not ship (it carries
    onnxruntime and the .onnx weights instead). On a machine that happens to
    have torch installed, checking only `import torch` selected a backend whose
    modules are missing and every robot died with
    `ModuleNotFoundError: No module named 'predict'`. Verify the whole backend.
    """
    try:
        import torch  # noqa: F401
    except Exception:
        return False
    return _module_importable("predict")


def load_solver(ckpt: str = _DEFAULT_CKPT, device: str = "cpu"):
    """Solver ra yek bar load mikonad (weights-ha dar RAM mimanand).

    Do backend:
      * torch (predict.IricaSolver) — halat-e development/training.
      * onnxruntime (onnx_solve.OnnxSolver) — halat-e BASTE-ye release ke torch
        nadarad (~1.1GB sarfe-jui). Khoruji-ye har do EYNAN yeki ast; barabari
        dar zaman-e build ba lab/scripts/export_solver_onnx.py sanjide mishavad.

    Entekhab-e backend: agar backend-e torch KAMEL bud (torch + predict.py) az an
    estefade mishavad (raftar-e ghabli dast-nakhorde), vagarna be ONNX fallback
    mishavad. Faghat `import torch` kafi NIST: release predict.py nadarad, pas yek
    torch-e nasb-shode-ye bi-rabt nabayad backend-e naghes ra entekhab konad.
    Masir-e .onnx az hamun ckpt sakhte mishavad (پسوند .pt -> .onnx) ya .onnx-e default.
    """
    if not _torch_backend_available():
        return _load_onnx_solver(ckpt)
    from predict import IricaSolver
    return IricaSolver(ckpt_path=ckpt, device=device)


def _load_onnx_solver(ckpt: str):
    """Fallback-e bi-torch. ckpt-e .pt be hamnam-e .onnx tarjome mishavad."""
    from onnx_solve import OnnxSolver
    candidate = None
    if ckpt:
        p = Path(ckpt)
        if p.suffix.lower() == ".onnx":
            candidate = str(p)
        else:
            sibling = p.with_suffix(".onnx")
            if sibling.exists():
                candidate = str(sibling)
    return OnnxSolver(candidate)


def _natural_key(p: Path):
    # captcha_2.png < captcha_10.png (na tartib-e alefba-i)
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def _run_dir(args):
    solver = load_solver(ckpt=args.ckpt, device=args.device)
    img_dir = Path(args.dir)
    imgs = sorted([p for p in img_dir.glob("*.png")], key=_natural_key)
    if args.limit:
        imgs = imgs[:args.limit]
    if not imgs:
        print(f"hich aks-e png-i dar {img_dir} peyda nashod.")
        return

    # Agar CSV bud, label-ha ra baraye sanjesh-e deghat mikhanim.
    truth = {}
    if args.csv:
        import pandas as pd
        from vocab import VOCAB
        df = pd.read_csv(args.csv)
        df.columns = [c.strip().lstrip("﻿") for c in df.columns]
        for _, r in df.iterrows():
            truth[str(r["filename"])] = VOCAB.normalize(str(r["text"]))

    from PIL import Image
    exact = checked = 0
    for p in imgs:
        # Az read_pil estefade mikonim ta daghighan mesir-e in-memory-ye collector
        # test shavad (na mesir-e file-khani).
        pred = solver.read_pil(Image.open(p))
        line = f"{p.name}: {pred}"
        t = truth.get(p.name)
        if t is not None and len(t) == 5:
            checked += 1
            ok = (pred == t)
            exact += int(ok)
            line += f"   (true={t} {'OK' if ok else 'X'})"
        print(line)

    if checked:
        print(f"\n=== deghat rooye {checked} aks-e label-dar: "
              f"{exact}/{checked} = {exact / checked:.4f} ===")


def main():
    ap = argparse.ArgumentParser(description="Live/offline captcha solving bridge")
    ap.add_argument("--dir", help="poushe-ye aks-ha baraye hal-e daste-jami (offline)")
    ap.add_argument("--image", help="hal-e yek aks-e tanha")
    ap.add_argument("--csv", default="", help="CSV-ye label (filename,text) baraye sanjesh")
    ap.add_argument("--ckpt", default=_DEFAULT_CKPT)
    ap.add_argument("--device", default="cpu", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.image:
        solver = load_solver(ckpt=args.ckpt, device=args.device)
        print(solver.read(args.image))
    elif args.dir:
        _run_dir(args)
    else:
        ap.error("yeki az --dir ya --image ra bedahid")


if __name__ == "__main__":
    main()
