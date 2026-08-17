"""Build a self-contained Windows x64 bundle of the irica panel + robots.

Design decisions worth knowing:

* **Embedded CPython, not PyInstaller.** panel.py launches robots with
  `subprocess.Popen([python, "gomrok.py", ...])` from five call sites; a
  single-file PyInstaller exe would have to re-exec itself and rewrite all of
  them. Shipping a real `python.exe` keeps the process model identical to
  development.
* **No PyTorch.** The captcha model is shipped as ONNX and read through
  `solver/onnx_solve.py`, so `onnxruntime` (~6 MB) replaces torch (~1.1 GB).
  Run `lab/scripts/export_solver_onnx.py` first: it refuses to emit an artifact
  unless the decoded strings match torch exactly.
* **Wheels are vendored.** The target machines sit behind a filtered
  connection, so every dependency is installed into the bundle at build time and
  nothing is fetched on first run.
* **No secrets, no state.** Only `*.example.json` templates ship; profiles,
  tokens, logs and screenshots are created by the app at runtime.

Usage
-----
    python lab/scripts/build_windows_release.py
    python lab/scripts/build_windows_release.py --version 1.0.0 --clean
"""
from __future__ import annotations

import argparse
import compileall
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

PY_VERSION = "3.10.11"
PY_EMBED_URL = (f"https://www.python.org/ftp/python/{PY_VERSION}/"
                f"python-{PY_VERSION}-embed-amd64.zip")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# files.pythonhosted.org is unreachable from the build machine; this mirror works.
PIP_INDEX = "https://mirror-pypi.runflare.com/simple/"

# Runtime dependencies only — derived from the imports the panel and robots
# actually execute. Deliberately excluded: torch, pandas, tqdm, Levenshtein
# (training/eval only) and full opencv (the headless wheel has no GUI deps).
RUNTIME_REQS = [
    "selenium>=4.10.0",
    "Pillow>=10.0.0",
    "requests>=2.31.0",
    "numpy>=1.24,<2",
    "opencv-python-headless>=4.8",
    "onnxruntime>=1.16",
]

APP_FILES = [
    "panel.py", "gomrok.py", "gomrok2.py",
    "estelam_all.py", "estelam_gomrok.py", "estelam_hamta.py", "exitban.py",
    "captcha_collector.py", "telegram_notify.py",
    "browser_window.py", "browser_visibility.py",
    "cloudflare_auth_relay.py", "cloudflare_http_proxy.py",
    "start_cloudflare_relay.py", "start_named_cloudflare_tunnel.py",
    "telegram_api_relay.py",
    "dashboard-new.html",
    "telegram_config.example.json", "upstream_proxy.example.json",
    "relay_config.example.json",
    "README.md", "CHANGELOG.md", "VERSION",
]

# The torch-free subset of the solver package. predict.py/model.py/train.py stay
# behind: they import torch and are only needed to train or to re-export.
SOLVER_FILES = ["__init__.py", "config.py", "vocab.py", "preprocess.py",
                "onnx_solve.py", "live_solve.py", "utils.py"]

RUN_PANEL_BAT = """@echo off
REM Start the irica panel using the bundled Python runtime.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo Starting the panel on http://127.0.0.1:8750 ...
"%~dp0python\\python.exe" panel.py %*
if errorlevel 1 (
  echo.
  echo The panel exited with an error. Scroll up for the reason.
  pause
)
endlocal
"""

FIRST_RUN_TXT = """Before the first run
====================

1) Install Google Chrome.

2) Download the chromedriver.exe that matches your Chrome MAJOR version from
   https://googlechromelabs.github.io/chrome-for-testing/
   and save it as  C:\\chromedriver.exe
   (Check your Chrome version at chrome://settings/help.)

3) Copy telegram_config.example.json to telegram_config.json and fill in your
   bot token and chat IDs. Do the same for upstream_proxy.example.json and
   relay_config.example.json only if you use a SOCKS5 proxy or the Cloudflare
   relay.

4) Double-click run_panel.bat and open http://127.0.0.1:8750/site-1

Everything else (Python and all libraries) is already inside this folder.
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def download(url: str, dest: Path) -> Path:
    """Fetch a build input, preferring curl.

    python.org and bootstrap.pypa.io answer curl fine from this network but time
    out through urllib, so curl is the primary path and urllib the fallback.
    """
    if dest.exists() and dest.stat().st_size > 0:
        log(f"  cached  {dest.name}")
        return dest
    log(f"  fetch   {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which("curl")
    if curl:
        res = subprocess.run([curl, "-sL", "--max-time", "600", "-o", str(dest), url],
                             capture_output=True, text=True, timeout=660)
        if res.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return dest
        dest.unlink(missing_ok=True)
        log(f"  curl failed (rc={res.returncode}), trying urllib")
    req = urllib.request.Request(url, headers={"User-Agent": "irica-build"})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def build_runtime(stage: Path, cache: Path) -> Path:
    """Unpack embedded CPython, enable site-packages, install pip + deps."""
    py_dir = stage / "python"
    py_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(download(PY_EMBED_URL, cache / f"python-{PY_VERSION}.zip")) as z:
        z.extractall(py_dir)

    # The embeddable distribution disables site by default; `import site` and a
    # Lib\site-packages entry are what make pip-installed packages importable.
    pth = next(py_dir.glob("python*._pth"))
    lines = [l for l in pth.read_text().splitlines() if l.strip() != "#import site"]
    if "Lib\\site-packages" not in lines:
        lines.insert(1, "Lib\\site-packages")
    lines.append("import site")
    pth.write_text("\n".join(lines) + "\n")
    (py_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)

    python = py_dir / "python.exe"
    log("  install pip")
    run(python, download(GET_PIP_URL, cache / "get-pip.py"),
        "--no-warn-script-location", script=True)
    log(f"  install {len(RUNTIME_REQS)} runtime dependencies")
    run(python, "-m", "pip", "install", "--no-warn-script-location",
        "--index-url", PIP_INDEX, *RUNTIME_REQS)
    return python


def run(python: Path, *args, script: bool = False) -> None:
    cmd = [str(python)] + ([str(args[0])] + [str(a) for a in args[1:]] if script
                           else [str(a) for a in args])
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
    if res.returncode != 0:
        raise SystemExit(f"build step failed: {' '.join(cmd[:4])}\n"
                         f"{res.stdout[-1500:]}\n{res.stderr[-1500:]}")


def copy_app(stage: Path) -> None:
    for name in APP_FILES:
        src = ROOT / name
        if not src.exists():
            log(f"  WARN missing {name}")
            continue
        shutil.copy2(src, stage / name)

    solver_dst = stage / "solver" / "checkpoints"
    solver_dst.mkdir(parents=True, exist_ok=True)
    for name in SOLVER_FILES:
        src = ROOT / "solver" / name
        if src.exists():
            shutil.copy2(src, stage / "solver" / name)
        elif name == "__init__.py":
            (stage / "solver" / "__init__.py").write_text("")

    onnx = ROOT / "solver" / "checkpoints" / "best_real_v2.onnx"
    if not onnx.exists():
        raise SystemExit("ONNX model missing — run lab/scripts/export_solver_onnx.py "
                         "first (it also proves parity with the torch model)")
    shutil.copy2(onnx, solver_dst / onnx.name)

    (stage / "run_panel.bat").write_text(RUN_PANEL_BAT, encoding="utf-8")
    (stage / "FIRST_RUN.txt").write_text(FIRST_RUN_TXT, encoding="utf-8")


def prune(stage: Path) -> None:
    """Drop build-time-only and unused weight, then verify the trim was safe.

    Removed:
      * pip / setuptools / wheel — only needed while building.
      * sympy + mpmath — onnxruntime pulls them for SYMBOLIC shape inference on
        dynamic models; our graph has a static shape, so inference never touches
        them (verified by a post-prune captcha read).
      * cv2's bundled ffmpeg DLL — we only imdecode PNG bytes, never video.
      * cv2's haarcascade data — face-detection cascades we never load.
      * __pycache__ / *.pyc.
    """
    sp = stage / "python" / "Lib" / "site-packages"
    for pattern in ("pip", "pip-*", "setuptools", "setuptools-*", "pkg_resources",
                    "wheel", "wheel-*", "sympy", "sympy-*", "mpmath", "mpmath-*"):
        for path in sp.glob(pattern):
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
    cv2_dir = sp / "cv2"
    if cv2_dir.exists():
        for dll in cv2_dir.glob("opencv_videoio_ffmpeg*.dll"):
            dll.unlink(missing_ok=True)
        shutil.rmtree(cv2_dir / "data", ignore_errors=True)
    for cache in stage.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for junk in stage.rglob("*.pyc"):
        junk.unlink(missing_ok=True)


def smoke(stage: Path) -> None:
    """Prove the bundled runtime imports the app and still reads a captcha.

    Runs with the SHIPPED python.exe against the SHIPPED files, so a pruned-away
    dependency or a torch assumption surfaces here, not on the user's machine.
    """
    python = stage / "python" / "python.exe"
    probe = (
        "import sys, importlib\n"
        "sys.path.insert(0, '.')\n"
        "sys.path.insert(0, r'solver')\n"
        "for m in ('panel','gomrok','telegram_notify','selenium','cv2','numpy',"
        "'PIL','onnxruntime','requests'):\n"
        "    importlib.import_module(m)\n"
        "assert 'torch' not in sys.modules, 'torch leaked into the bundle'\n"
        "from onnx_solve import OnnxSolver\n"
        "import numpy as np\n"
        "s = OnnxSolver(r'solver/checkpoints/best_real_v2.onnx')\n"
        "out = s.read_array(np.zeros((48,192,3), np.uint8))\n"
        "assert isinstance(out, str) and len(out) == 5, out\n"
        "print('SMOKE_OK', out)\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}
    res = subprocess.run([str(python), "-c", probe], cwd=str(stage),
                         capture_output=True, text=True, timeout=300, env=env)
    if "SMOKE_OK" not in res.stdout:
        raise SystemExit("bundle smoke test FAILED after prune:\n"
                         f"{res.stdout[-1500:]}\n{res.stderr[-1500:]}")
    log(f"  smoke   {res.stdout.strip().splitlines()[-1]}")


def tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    ap.add_argument("--clean", action="store_true", help="wipe dist/ first")
    args = ap.parse_args()

    dist = ROOT / "dist"
    cache = dist / ".cache"
    stage = dist / f"irica-v{args.version}-win64"
    if args.clean and dist.exists():
        shutil.rmtree(dist, ignore_errors=True)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    log(f"building irica v{args.version} (win64) -> {stage}")
    log("runtime:")
    build_runtime(stage, cache)
    log("application:")
    copy_app(stage)
    log("prune:")
    prune(stage)
    log("smoke:")
    smoke(stage)

    total = tree_size(stage)
    log("")
    log(f"tree size : {total / 1048576:.1f} MB  ({sum(1 for _ in stage.rglob('*') if _.is_file())} files)")
    for sub in ("python", "solver"):
        p = stage / sub
        if p.exists():
            log(f"  {sub:<8}: {tree_size(p) / 1048576:.1f} MB")
    log(f"RESULT: staged {stage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
