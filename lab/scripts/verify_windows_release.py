"""Verify a staged Windows bundle before it is zipped and published.

Three classes of check, all against the STAGED TREE (not the repo):

1. **Safety** — no credentials, no personal data, no runtime state. This is the
   check that matters most: the repo's real `profiles.json` holds national IDs and
   `telegram_config.json` holds a bot token, and neither may ever reach a release.
2. **Completeness** — the files the app cannot start without.
3. **Function** — the bundled `python.exe` imports the app, reads a captcha
   through ONNX without torch, and actually serves `/site-1` and `/site-2`.

Usage
-----
    python lab/scripts/verify_windows_release.py
    python lab/scripts/verify_windows_release.py --dist dist/irica-v1.0.0-win64
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

FORBIDDEN_NAMES = {
    "telegram_config.json", "upstream_proxy.json", "relay_config.json",
    "telegram_health.json", "profiles.json", "profiles.json.bak",
    "profiles.json.lock", "inputs.json", "estelam_store.json",
    "logs.jsonl", "estelam_logs.jsonl", "hamta_logs.jsonl",
    "site2_notif.json", "exitban_errors.jsonl", "cloudflare_public_url.txt",
    "system_debug.json",
}
FORBIDDEN_GLOBS = ["*.log", "*.pt", "secrets/*", "*-chrome-profile/*",
                   "_dbg_last.*", "_after_submit.*"]
# Secrets and PII, by shape rather than by value, so nothing sensitive is
# written into this script.
SECRET_PATTERNS = [
    (r"\b\d{9,10}:[A-Za-z0-9_-]{35}\b", "telegram bot token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}", "github fine-grained token"),
    (r"\bghp_[A-Za-z0-9]{36}\b", "github classic token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r'"password"\s*:\s*"(?!<)[^"]{3,}"', "hardcoded password"),
    (r'"bot_token"\s*:\s*"[0-9]', "populated bot_token"),
]
REQUIRED = [
    "panel.py", "gomrok.py", "telegram_notify.py", "dashboard-new.html",
    "run_panel.bat", "FIRST_RUN.txt", "README.md", "VERSION",
    "profile_inquiry.py", "robot_launcher.py", "system_debug.py",
    "telegram_config.example.json",
    "solver/onnx_solve.py", "solver/live_solve.py", "solver/preprocess.py",
    "solver/checkpoints/best_real_v2.onnx",
    "Hamta-solver/onnx_solve.py", "Hamta-solver/live_solve.py",
    "Hamta-solver/preprocess.py", "Hamta-solver/number_decode.py",
    "Hamta-solver/checkpoints/best.onnx",
    "python/python.exe",
]
TEXT_SUFFIXES = {".py", ".json", ".txt", ".md", ".bat", ".html", ".cfg", ".ini"}

rows: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    rows.append((label, ok))
    print(("PASS  " if ok else "FAIL  ") + label + (f"   {detail}" if detail else ""),
          flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def env_clean() -> dict:
    return {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME",
                         "IRICA_ROBOT_PYTHON")}


# --------------------------------------------------------------------------- #
def check_safety(dist: Path) -> None:
    names = [p for p in dist.rglob("*") if p.is_file()]
    bad = [p.relative_to(dist).as_posix() for p in names
           if p.name in FORBIDDEN_NAMES]
    for pattern in FORBIDDEN_GLOBS:
        bad += [p.relative_to(dist).as_posix() for p in dist.rglob(pattern)
                if p.is_file()]
    check("no credential or state files in the bundle", not bad, str(sorted(set(bad))[:6]))

    # example templates must stay empty of real values
    for tmpl in dist.glob("*.example.json"):
        try:
            data = json.loads(tmpl.read_text(encoding="utf-8"))
        except Exception as exc:
            check(f"{tmpl.name} is valid JSON", False, str(exc))
            continue
        token = str(data.get("bot_token", ""))
        check(f"{tmpl.name} carries no real bot_token",
              not re.match(r"^\d{9,10}:", token), token[:12])

    hits = []
    for p in names:
        if p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > 2_000_000:
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, what in SECRET_PATTERNS:
            if re.search(pattern, body):
                hits.append(f"{p.relative_to(dist).as_posix()}: {what}")
    check("no secret-shaped strings in any bundled text file", not hits, str(hits[:5]))

    # national IDs / mobiles from the operator's real profiles must not appear.
    store = ROOT / "profiles.json"
    if store.exists():
        try:
            profiles = json.loads(store.read_text(encoding="utf-8")).get("profiles", [])
        except Exception:
            profiles = []
        needles = {str(p.get(k, "")).strip() for p in profiles
                   for k in ("national", "mobile")} - {""}
        needles |= {str(i).strip() for p in profiles for i in (p.get("imeis") or [])}
        leaked = []
        for p in names:
            if p.suffix.lower() not in TEXT_SUFFIXES or p.stat().st_size > 2_000_000:
                continue
            body = p.read_text(encoding="utf-8", errors="ignore")
            leaked += [f"{p.relative_to(dist).as_posix()}" for n in needles
                       if len(n) >= 10 and n in body]
        check(f"no PII from the live store ({len(needles)} values) in the bundle",
              not leaked, str(sorted(set(leaked))[:4]))
    else:
        check("live store present to diff PII against", False,
              "profiles.json missing — PII check skipped")


def check_completeness(dist: Path) -> None:
    missing = [r for r in REQUIRED if not (dist / r).exists()]
    check("every required file is present", not missing, str(missing))

    # Root-cause guard, not another hand-maintained name: panel.py spawns robots
    # as subprocesses, so ANY script it names must be inside the bundle. This is
    # what caught profile_inquiry.py being absent while panel.py called it.
    panel_src = (dist / "panel.py").read_text(encoding="utf-8", errors="ignore")
    spawned = set(re.findall(r'HERE,\s*"([A-Za-z0-9_]+\.py)"', panel_src))
    spawned |= set(re.findall(r'\(\s*"([A-Za-z0-9_]+\.py)"\s*,\s*\[', panel_src))
    spawned |= set(re.findall(r'_robot_command\([^,]+,\s*"([A-Za-z0-9_]+\.py)"', panel_src))
    absent = sorted(s for s in spawned if not (dist / s).exists())
    check(f"every script panel.py spawns is bundled ({len(spawned)} found)",
          not absent, str(absent))
    check("no torch inside the bundle",
          not list((dist / "python" / "Lib" / "site-packages").glob("torch*")))
    check("no .pt checkpoint shipped (ONNX only)",
          not list(dist.rglob("*.pt")))
    version = (dist / "VERSION").read_text().strip() if (dist / "VERSION").exists() else ""
    check("VERSION matches the repo", version == (ROOT / "VERSION").read_text().strip(),
          version)


def check_function(dist: Path) -> None:
    python = dist / "python" / "python.exe"
    probe_path = dist / "_tmp_release_probe.py"
    # Run THROUGH robot_launcher.py with a script argument -- the exact shape the
    # panel uses to spawn a robot. The old gate used `python -c` plus a manual
    # `sys.path.insert(0, '.')`, which handed the probe the one thing the real
    # spawn path lacks, and so passed while every inquiry died in the field.
    probe = (
        "import sys\n"
        "import importlib\n"
        "mods = ('panel','gomrok','gomrok2','estelam_gomrok','estelam_hamta',\n"
        "        'estelam_all','profile_inquiry','robot_launcher','system_debug',\n"
        "        'exitban','telegram_notify','browser_window','browser_visibility',\n"
        "        'selenium','cv2','numpy','PIL','onnxruntime','requests')\n"
        "for m in mods: importlib.import_module(m)\n"
        "assert 'torch' not in sys.modules\n"
        "import numpy as np\n"
        # The interpreter the panel hands to a robot must be the bundled one.
        # A hardcoded foreign interpreter is what broke v1.0.3: it had torch, and
        # the solvers then chose a torch backend this bundle does not ship.
        "import panel\n"
        "assert panel.ROBOT_PYTHON == sys.executable, panel.ROBOT_PYTHON\n"
        "assert 'Program Files' not in panel.ROBOT_PYTHON or sys.executable == panel.ROBOT_PYTHON\n"
        "cmd = panel._robot_command(panel.ROBOT_PYTHON, 'estelam_hamta.py', '--from-store')\n"
        "assert cmd[0] == sys.executable and 'robot_launcher.py' in cmd[1], cmd\n"
        # Robots that spawn robots must go through the launcher too.
        "import estelam_all, profile_inquiry\n"
        "assert 'robot_launcher.py' in estelam_all._LAUNCHER\n"
        "assert 'robot_launcher.py' in profile_inquiry.LAUNCHER\n"
        # gomrok solver, through its own entry point (gomrok binds load_solver
        # from the solver/ package at import). A bare `from onnx_solve import`
        # would be ambiguous: importing estelam_hamta puts Hamta-solver on the
        # path too, and both packages expose a module named onnx_solve.
        "import gomrok\n"
        "gs = gomrok.load_solver(device='cpu')\n"
        "assert type(gs).__name__ == 'OnnxSolver', type(gs).__name__\n"
        "r = gs.read_array(np.zeros((48,192,3), np.uint8))\n"
        "assert isinstance(r, str) and len(r) == 5, r\n"
        # hamta inquiry: its solver lives in a separate package with its own
        # model, so it is resolved through the robot's real entry point.
        "import estelam_hamta as eh\n"
        "hs = eh.load_solver()\n"
        "assert type(hs).__name__ == 'OnnxHamtaSolver', type(hs).__name__\n"
        "hr = hs.read_bgr(np.zeros((90,380,3), np.uint8))\n"
        "assert set(hr) == {'number','text','confidence','snap_distance'}, hr\n"
        "assert 'torch' not in sys.modules, 'torch leaked via hamta'\n"
        # profile_inquiry.py is spawned by the panel's inquiry button; import it
        # and drive its store-building helper so a missing dependency inside the
        # bundle surfaces here rather than as a dead button on the user's machine.
        "import profile_inquiry as pi\n"
        "st = pi._store({'id':'p1','title':'t','imeis':['352506099000001'],"
        "'national':'1','birth':'2','mobile':'3'})\n"
        "assert len(st['devices']) == 1 and st['devices'][0]['serial1'], st\n"
        "assert st['person']['national'] == '1', st\n"
        "print('OK', len(mods), r, type(hs).__name__)\n"
    )
    probe_path.write_text(probe, encoding="utf-8")
    try:
        res = subprocess.run([str(python), str(dist / "robot_launcher.py"), str(probe_path)],
                             cwd=str(dist), capture_output=True, text=True,
                             timeout=300, env=env_clean())
    finally:
        probe_path.unlink(missing_ok=True)
    ok = res.stdout.strip().splitlines()[-1:] and \
        res.stdout.strip().splitlines()[-1].startswith("OK")
    check("robots launch under the bundled python and read a captcha", ok,
          res.stdout.strip()[-200:] or res.stderr.strip()[-200:])

    # Serve the panel from a temp CWD so the real profiles.json is never touched.
    port = free_port()
    work = Path(tempfile.mkdtemp(prefix="release-panel-"))
    runner = work / "serve.py"
    runner.write_text(
        "import sys, os\n"
        f"sys.path.insert(0, r'{dist}')\n"
        f"sys.path.insert(0, r'{dist / 'solver'}')\n"
        f"os.chdir(r'{work}')\n"
        "import panel\n"
        f"panel.PROFILES_PATH = r'{work / 'profiles.json'}'\n"
        f"panel.INPUTS_PATH = r'{work / 'inputs.json'}'\n"
        "panel._STORE_LOCK_PATH = panel.PROFILES_PATH + '.lock'\n"
        "from http.server import ThreadingHTTPServer\n"
        f"ThreadingHTTPServer(('127.0.0.1', {port}), panel.Handler).serve_forever()\n",
        encoding="utf-8")
    proc = subprocess.Popen([str(python), str(runner)], cwd=str(work),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env_clean())
    try:
        up = False
        for _ in range(60):
            if proc.poll() is not None:
                break
            s = socket.socket()
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                up = True
                s.close()
                break
            s.close()
            time.sleep(0.25)
        if not up:
            check("panel starts from the bundle", False,
                  (proc.stdout.read() if proc.stdout else "")[-300:])
            return
        check("panel starts from the bundle", True, f"port {port}")
        for route, needle in (("/site-1", "nav-tab"), ("/site-2", "svc-btns")):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{route}", timeout=20) as r:
                    body = r.read().decode("utf-8", "ignore")
                    check(f"{route} serves HTTP 200", r.status == 200, str(r.status))
                    check(f"{route} renders its markup", needle in body,
                          f"looked for {needle!r}")
            except Exception as exc:
                check(f"{route} serves HTTP 200", False, f"{type(exc).__name__}: {exc}")
        check("panel did not create files outside its own directory",
              not (ROOT / "profiles.json.lock").exists()
              or (ROOT / "profiles.json").exists())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    default = ROOT / "dist" / f"irica-v{(ROOT / 'VERSION').read_text().strip()}-win64"
    ap.add_argument("--dist", default=str(default))
    args = ap.parse_args()

    dist = Path(args.dist)
    if not dist.is_dir():
        print(f"FAIL  staged bundle not found: {dist}")
        print("      run lab/scripts/build_windows_release.py first")
        return 2

    size = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file())
    print(f"bundle: {dist}")
    print(f"size  : {size / 1048576:.1f} MB\n")

    check_safety(dist)
    check_completeness(dist)
    check_function(dist)

    print("----")
    bad = [n for n, ok in rows if not ok]
    print("RESULT:", "bundle verified" if not bad else f"FAILURES: {bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
