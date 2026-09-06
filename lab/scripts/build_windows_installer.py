"""Build the user-facing Windows installer (IRICA-Setup-vX.Y.Z.exe).

Pipeline:
  1. reuse/refresh the staged bundle produced by build_windows_release.py
  2. locate Inno Setup's ISCC.exe (installed on demand via winget -- the ONE
     step that may pop a UAC/admin prompt; everything else is per-user)
  3. render lab/scripts/irica.iss with the current VERSION
  4. compile the installer into dist/, print its SHA256

The release zip stays the fallback distribution; the installer is additive.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
DIST = ROOT / "dist"
STAGE = DIST / f"irica-v{(ROOT / 'VERSION').read_text().strip()}-win64"


def log(msg=""):
    print(msg, flush=True)


def find_iscc():
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        # winget's per-user install location (no admin involved)
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    found = shutil.which("ISCC")
    return Path(found) if found else None


def install_inno_via_winget():
    """One-time Inno Setup install. Returns the ISCC path or None."""
    log("ISCC not found — installing Inno Setup via winget (one UAC prompt "
        "may appear; installer builds themselves need no admin afterwards)")
    res = subprocess.run(
        ["winget", "install", "--id", "JRSoftware.InnoSetup", "-e",
         "--accept-source-agreements", "--accept-package-agreements"],
        capture_output=True, text=True, timeout=600)
    log((res.stdout or "")[-800:])
    if res.returncode != 0:
        log((res.stderr or "")[-800:])
        return None
    return find_iscc()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    version = (ROOT / "VERSION").read_text().strip()
    if not STAGE.is_dir():
        log(f"staged bundle missing: {STAGE}")
        log("run lab/scripts/build_windows_release.py first")
        return 2

    iscc = find_iscc() or install_inno_via_winget()
    if not iscc:
        log("FAIL: Inno Setup (ISCC.exe) is not available and winget install "
            "did not succeed. Install Inno Setup 6 manually and re-run.")
        return 3
    log(f"ISCC: {iscc}")

    icon = SCRIPTS / "irica.ico"
    if not icon.is_file():
        log("WARN: lab/scripts/irica.ico missing — building without a custom "
            "icon (SetupIconFile/IconFilename lines are tolerated absent by "
            "ISCC only when removed; writing a no-icon variant)")
        iss_text = (SCRIPTS / "irica.iss").read_text(encoding="utf-8")
        iss_text = re.sub(r"^SetupIconFile=.*$", "", iss_text, flags=re.M)
        iss_text = re.sub(r",?\s*IconFilename: \"\{app\}\\\\irica\.ico\"", "",
                          iss_text, flags=re.M)
    else:
        # ISCC resolves SetupIconFile relative to the rendered .iss (dist/),
        # and the [Icons] entries reference {app}\irica.ico — so the icon is
        # both placed beside the rendered script and shipped into the bundle.
        iss_text = (SCRIPTS / "irica.iss").read_text(encoding="utf-8")
        shutil.copy2(icon, DIST / "irica.ico")
        stage_icon = STAGE / "irica.ico"
        if not stage_icon.is_file():
            log("WARN: irica.ico not in the staged bundle — [Icons] entries "
                "reference {app}\\irica.ico; re-run build_windows_release.py")

    iss_text = iss_text.replace("__VERSION__", version)
    rendered = DIST / f"_irica-{version}.iss"
    rendered.write_text(iss_text, encoding="utf-8")

    res = subprocess.run(
        [str(iscc), "/Qp", f"/DStageDir={STAGE}", str(rendered)],
        capture_output=True, text=True, timeout=1200, cwd=str(SCRIPTS))
    # /Qp quiets the pep talk; still show errors when they happen.
    if res.returncode != 0:
        log(res.stdout[-2000:])
        log(res.stderr[-2000:])
        log("FAIL: ISCC returned " + str(res.returncode))
        return 4

    # irica.iss writes dist\IRICA-Setup-v<version>.exe (OutputDir=..).
    setup = DIST / f"IRICA-Setup-v{version}.exe"
    if not setup.is_file():
        log("FAIL: installer not produced where expected: " + str(setup))
        return 5
    digest = sha256_of(setup)
    log(f"installer : {setup}  ({setup.stat().st_size:,} bytes)")
    log(f"sha256    : {digest}")
    (DIST / f"IRICA-Setup-v{version}.sha256").write_text(
        f"{digest}  {setup.name}\n", encoding="utf-8")
    rendered.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
