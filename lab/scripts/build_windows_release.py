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
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

PY_VERSION = "3.10.11"
PY_EMBED_URL = (f"https://www.python.org/ftp/python/{PY_VERSION}/"
                f"python-{PY_VERSION}-embed-amd64.zip")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# Package indexes, tried in order until one serves the wheels. Neither host is
# dependable from this network: files.pythonhosted.org was unreachable when the
# v1.0.3 bundle was built (hence the mirror), and the mirror stopped serving
# packages while v1.0.8 was being built (HTTP 200 on /simple/, "from versions:
# none" for every requirement) while pypi.org worked. So the build tries several
# and only fails when they all fail. IRICA_PIP_INDEX overrides the list.
PIP_INDEXES = [
    "https://pypi.org/simple/",
    "https://mirror-pypi.runflare.com/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
]

# Runtime dependencies only — derived from the imports the panel and robots
# actually execute. Deliberately excluded: torch, pandas, tqdm, Levenshtein
# (training/eval only) and full opencv (the headless wheel has no GUI deps).
RUNTIME_REQS = [
    "selenium>=4.10.0",
    "Pillow>=10.0.0",
    "requests>=2.31.0",
    "urllib3>=2.0.0",
    "certifi>=2023.7.22",
    "charset-normalizer>=3.2.0",
    "idna>=3.4",
    "numpy>=1.24,<2",
    "opencv-python-headless>=4.8",
    "onnxruntime>=1.16",
]

APP_FILES = [
    "panel.py", "gomrok.py", "gomrok2.py",
    "app_paths.py",
    "estelam_all.py", "estelam_gomrok.py", "estelam_hamta.py", "exitban.py",
    "profile_inquiry.py", "robot_launcher.py", "system_debug.py",
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

# estelam_hamta.py reads the hamta modal captcha through its OWN solver package
# (Hamta-solver/), which is separate from the gomrok one and has its own model.
# model.py is excluded for the same reason as above; utils.py and number_decode.py
# ship because number snapping and edit distance are pure-python.
HAMTA_SOLVER_FILES = ["config.py", "vocab.py", "preprocess.py", "onnx_solve.py",
                      "live_solve.py", "utils.py", "number_decode.py"]

RUN_PANEL_BAT = """@echo off
REM Start the irica panel using the bundled Python runtime.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
call "%~dp0install.bat"
if errorlevel 1 exit /b 1
echo Starting the panel on http://127.0.0.1:8750 ...
"%~dp0python\\python.exe" panel.py %*
if errorlevel 1 (
  echo.
  echo The panel exited with an error. Scroll up for the reason.
  pause
)
endlocal
"""

INSTALL_BAT = """@echo off
REM Verify the self-contained runtime before starting the panel.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
"%~dp0python\\python.exe" -c "import requests,urllib3,certifi,charset_normalizer,idna; print('Runtime OK')"
if errorlevel 1 (
  echo.
  echo Runtime is incomplete. Re-extract the release archive into a new folder.
  echo No system Python installation is required.
  pause
  exit /b 1
)
echo Runtime verified.
endlocal
"""

FIRST_RUN_TXT = """Before the first run
====================

1) Install Google Chrome.

2) Download the chromedriver.exe that matches your Chrome MAJOR version from
   https://googlechromelabs.github.io/chrome-for-testing/
   and save it as  C:\\chromedriver.exe
   (Check your Chrome version at chrome://settings/help.)

3) Double-click install.bat once to verify the bundled runtime.

4) Double-click run_panel.bat and open http://127.0.0.1:8750/site-1

5) Set the Telegram bot token and chat IDs INSIDE the panel, on its Telegram
   settings page. They are saved outside this folder, so future updates keep
   them.


Where your settings and data live
================================

Not in this folder. Everything you configure -- the Telegram token, profiles,
device inquiries, logs -- is stored in:

   %LOCALAPPDATA%\\irica

To upgrade, just unzip the new release into a new folder and run it. Nothing
needs to be copied by hand. Upgrading from v1.0.6 or older also needs nothing:
the settings in your previous install are imported automatically on first run,
whether you unzipped over the old folder or beside it (copied, not moved, so the
old version keeps working if you go back). The panel prints which folder it read
them from.

If the old install is somewhere the new one cannot find (another drive), name it
once:

   set IRICA_IMPORT_FROM=D:\\irica-v1.0.4-win64

To keep the data next to the program instead (a portable install on a USB
drive, for example), set IRICA_DATA_DIR before starting:

   set IRICA_DATA_DIR=%~dp0data

Advanced: relay_config.json and upstream_proxy.json also live in that data
folder. The *.example.json templates here show their shape; only fill them in if
you use a SOCKS5 proxy or the Cloudflare relay.

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
    install_requirements(python, py_dir / "Lib" / "site-packages")
    verify_runtime(python)
    return python


def pip_indexes() -> list[str]:
    override = (os.environ.get("IRICA_PIP_INDEX") or "").strip()
    if override:
        return [u.strip() for u in override.split(",") if u.strip()]
    return list(PIP_INDEXES)


def install_requirements(python: Path, target: Path) -> None:
    """Install the runtime deps, trying each index until one works.

    A failing index is not a build failure by itself: this network reaches
    different package hosts on different days (see PIP_INDEXES). Only the last
    index's error is raised, with every attempt named.
    """
    attempts = []
    for index in pip_indexes():
        try:
            run(python, "-m", "pip", "install", "--no-warn-script-location",
                "--retries", "2", "--timeout", "30",
                "--target", str(target), "--index-url", index, *RUNTIME_REQS)
        except SystemExit as exc:
            attempts.append(index)
            log(f"  index failed: {index}")
            last = exc
            continue
        if attempts:
            log(f"  index used  : {index}  (after {len(attempts)} failed)")
        else:
            log(f"  index       : {index}")
        return
    raise SystemExit("every package index failed: " + ", ".join(attempts)
                     + f"\n{last}")


def verify_runtime(python: Path) -> None:
    """Fail the build if the vendored requests closure is incomplete."""
    run(python, "-c", "import requests,urllib3,certifi,charset_normalizer,idna; print('RUNTIME_OK')")


def run(python: Path, *args, script: bool = False) -> None:
    cmd = [str(python)] + ([str(args[0])] + [str(a) for a in args[1:]] if script
                           else [str(a) for a in args])
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}
    env["PYTHONNOUSERSITE"] = "1"
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

    hamta_dst = stage / "Hamta-solver" / "checkpoints"
    hamta_dst.mkdir(parents=True, exist_ok=True)
    for name in HAMTA_SOLVER_FILES:
        src = ROOT / "Hamta-solver" / name
        if src.exists():
            shutil.copy2(src, stage / "Hamta-solver" / name)
        else:
            log(f"  WARN missing Hamta-solver/{name}")

    hamta_onnx = ROOT / "Hamta-solver" / "checkpoints" / "best.onnx"
    if not hamta_onnx.exists():
        raise SystemExit("HAMTA ONNX model missing — run "
                         "lab/scripts/export_hamta_onnx.py first (it also proves "
                         "parity with the torch model)")
    shutil.copy2(hamta_onnx, hamta_dst / hamta_onnx.name)

    (stage / "run_panel.bat").write_text(RUN_PANEL_BAT, encoding="utf-8")
    (stage / "install.bat").write_text(INSTALL_BAT, encoding="utf-8")
    (stage / "FIRST_RUN.txt").write_text(FIRST_RUN_TXT, encoding="utf-8")


def strip_caches(stage: Path) -> None:
    """Remove every bytecode cache from the staged tree.

    Must run again immediately before packaging: the smoke test and
    verify_windows_release.py import the app, which regenerates these. A shipped
    .pyc embeds the absolute path it was compiled from, so tracebacks on the
    operator's machine would name the BUILD directory instead of their install —
    directly undermining the debug-log workflow.
    """
    for cache in stage.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for junk in stage.rglob("*.pyc"):
        junk.unlink(missing_ok=True)


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
    strip_caches(stage)
    verify_runtime(stage / "python" / "python.exe")


def smoke(stage: Path) -> None:
    """Prove the bundled runtime imports the app and still reads a captcha.

    Runs with the SHIPPED python.exe against the SHIPPED files, THROUGH
    `robot_launcher.py` with a script argument -- exactly how the panel spawns a
    robot. This shape matters: the previous gate used `python -c` with a manual
    `sys.path.insert(0, '.')`, which silently supplied the one thing the real
    spawn path lacks. Two v1.0.3 field failures hid behind that difference:

    * the embedded interpreter is ISOLATED, so a script argument does NOT put the
      project directory on `sys.path` and every project import failed;
    * the panel spawned robots with a hardcoded foreign interpreter, whose torch
      install made the solvers pick a backend this bundle does not ship.
    """
    python = stage / "python" / "python.exe"
    probe_path = stage / "_tmp_release_smoke.py"
    probe = (
        # No sys.path bootstrap here on purpose: robot_launcher.py must provide
        # it, exactly as it does for a real robot.
        "import sys\n"
        "import importlib\n"
        "for m in ('panel','gomrok','gomrok2','telegram_notify','selenium','cv2',\n"
        "          'numpy','PIL','onnxruntime','requests','exitban',\n"
        "          'browser_window','browser_visibility','estelam_all'):\n"
        "    importlib.import_module(m)\n"
        "assert 'torch' not in sys.modules, 'torch leaked into the bundle'\n"
        "import numpy as np\n"
        # The interpreter the panel would hand a robot must be THIS bundle's
        # python, never a foreign install that may carry torch.
        "import panel\n"
        "assert panel.ROBOT_PYTHON == sys.executable, panel.ROBOT_PYTHON\n"
        "assert 'robot_launcher.py' in panel._robot_command(panel.ROBOT_PYTHON, 'gomrok.py')[1]\n"
        # Resolved through gomrok's own entry point rather than a bare
        # `from onnx_solve import ...`: importing estelam_hamta below puts
        # Hamta-solver on sys.path, and BOTH packages expose onnx_solve.
        "import gomrok\n"
        "gs = gomrok.load_solver(device='cpu')\n"
        "assert type(gs).__name__ == 'OnnxSolver', type(gs).__name__\n"
        "out = gs.read_array(np.zeros((48,192,3), np.uint8))\n"
        "assert isinstance(out, str) and len(out) == 5, out\n"
        # The hamta inquiry robot resolves its solver through its own package,
        # so exercise the real entry point (estelam_hamta.load_solver), not just
        # the class, or a broken sys.path would slip through to the user.
        "import estelam_hamta as eh\n"
        "hs = eh.load_solver()\n"
        "assert type(hs).__name__ == 'OnnxHamtaSolver', type(hs).__name__\n"
        "hres = hs.read_bgr(np.zeros((90,380,3), np.uint8))\n"
        "assert set(hres) == {'number','text','confidence','snap_distance'}, hres\n"
        "assert 'torch' not in sys.modules, 'torch leaked via the hamta solver'\n"
        # profile_inquiry.py is spawned by the panel's inquiry button, and it
        # spawns robots of its own -- through the launcher, like the panel does.
        "import profile_inquiry as pi\n"
        "assert 'robot_launcher.py' in pi.LAUNCHER, pi.LAUNCHER\n"
        "st = pi._store({'id':'p1','title':'t','imeis':['352506099000001'],"
        "'national':'1','birth':'2','mobile':'3'})\n"
        "assert len(st['devices']) == 1, st\n"
        "print('SMOKE_OK', out, type(hs).__name__)\n"
    )
    probe_path.write_text(probe, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME", "IRICA_ROBOT_PYTHON")}
    # Importing panel resolves the data directory and would otherwise touch the
    # developer's real %LOCALAPPDATA%\irica. Point it at a throwaway directory.
    smoke_data = Path(tempfile.mkdtemp(prefix="irica-smoke-data-"))
    env["IRICA_DATA_DIR"] = str(smoke_data)
    try:
        res = subprocess.run([str(python), str(stage / "robot_launcher.py"), str(probe_path)],
                             cwd=str(stage), capture_output=True, text=True,
                             timeout=300, env=env)
    finally:
        probe_path.unlink(missing_ok=True)
        shutil.rmtree(smoke_data, ignore_errors=True)
    if "SMOKE_OK" not in res.stdout:
        raise SystemExit("bundle smoke test FAILED after prune:\n"
                         f"{res.stdout[-1500:]}\n{res.stderr[-1500:]}")
    log(f"  smoke   {res.stdout.strip().splitlines()[-1]}")


def tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def package(stage: Path, dist: Path, version: str) -> Path:
    """Zip the staged tree and record its SHA256.

    The archive keeps the `irica-vX.Y.Z-win64/` top-level directory so unzipping
    lands in one folder, matching every previous release. Caches are stripped
    first: the smoke test regenerates .pyc files that embed the build path.
    """
    strip_caches(stage)
    archive = dist / f"{stage.name}.zip"
    archive.unlink(missing_ok=True)
    files = sorted(p for p in stage.rglob("*"))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            z.write(path, path.relative_to(dist).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    (ROOT / f"SHA256SUMS_v{version}.txt").write_text(
        f"{archive.name}  {digest}\n", encoding="utf-8")
    log(f"  zip     {archive.name}  {archive.stat().st_size / 1048576:.1f} MB")
    log(f"  sha256  {digest}")
    return archive


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    ap.add_argument("--clean", action="store_true", help="wipe dist/ first")
    ap.add_argument("--no-zip", action="store_true",
                    help="stage and smoke-test only, do not package")
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
    if not args.no_zip:
        log("package:")
        package(stage, dist, args.version)

    total = tree_size(stage)
    log("")
    log(f"tree size : {total / 1048576:.1f} MB  ({sum(1 for _ in stage.rglob('*') if _.is_file())} files)")
    for sub in ("python", "solver"):
        p = stage / sub
        if p.exists():
            log(f"  {sub:<8}: {tree_size(p) / 1048576:.1f} MB")
    log(f"RESULT: staged {stage}")
    log("        run lab/scripts/verify_windows_release.py before publishing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
