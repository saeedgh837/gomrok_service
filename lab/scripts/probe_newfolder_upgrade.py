"""Does the REAL upgrade shape migrate? Unzip into a NEW folder, data dir empty.

`live_check_bundle_upgrade.py` proved the case where the new code runs FROM the
old install folder. The operator instruction shipped in v1.0.7 is different:

    "unzip the new release into a NEW folder next to the old one"

In that shape `app_paths.APP_DIR` is the NEW folder, and the state files sit in
the OLD folder. If migration only looks inside APP_DIR it finds nothing, and a
first-time upgrade lands on an EMPTY data directory -- the exact failure the
release was supposed to remove.

This probe answers it with a run, not an opinion. No assertions: it prints what
actually happens.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/probe_newfolder_upgrade.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN = "8123456789:NEWFOLDER-PROBE-token-abcdefghijkl"

CODE = ["app_paths.py", "panel.py", "telegram_notify.py", "gomrok.py",
        "gomrok2.py", "estelam_gomrok.py", "estelam_hamta.py", "estelam_all.py",
        "exitban.py", "captcha_collector.py", "browser_window.py",
        "browser_visibility.py", "system_debug.py", "profile_inquiry.py",
        "robot_launcher.py", "dashboard-new.html", "cloudflare_auth_relay.py",
        "cloudflare_http_proxy.py", "telegram_api_relay.py",
        "start_cloudflare_relay.py", "start_named_cloudflare_tunnel.py"]


def install(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for name in CODE:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    for package in ("solver", "Hamta-solver"):
        if (ROOT / package).is_dir():
            shutil.copytree(ROOT / package, target / package, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("*.pt", "data", "labeler",
                                                          "tools", "__pycache__"))
    return target


PROBE = """
import json, os
import app_paths, panel
import telegram_notify as tn
print("REPORT " + json.dumps({
    "app_dir": os.path.dirname(os.path.abspath(panel.__file__)),
    "data_dir": app_paths.data_dir(),
    "migrated": panel._MIGRATED_FILES,
    "token": panel.load_telegram_config().get("bot_token", ""),
    "token_from_robot": (tn._load_cfg() or {}).get("bot_token", "") if os.path.exists(tn._CFG_PATH) else "",
    "data_dir_contents": sorted(os.listdir(app_paths.data_dir())),
}, ensure_ascii=False))
"""


def run(folder: Path, data_dir: Path):
    script = folder / "_tmp_probe.py"
    script.write_text(PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME",
                        "IRICA_DATA_DIR", "IRICA_ESTELAM_STORE")}
    env["IRICA_DATA_DIR"] = str(data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        res = subprocess.run([sys.executable, str(folder / "robot_launcher.py"),
                              str(script)], cwd=str(folder), capture_output=True,
                             text=True, timeout=300, env=env)
    finally:
        script.unlink(missing_ok=True)
    for line in (res.stdout or "").splitlines():
        if line.startswith("REPORT "):
            return json.loads(line[len("REPORT "):])
    raise SystemExit(f"no report:\n{res.stdout[-1500:]}\n{res.stderr[-1500:]}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="irica-newfolder-"))
    try:
        # The operator's machine: an old install carrying the settings, and a
        # data directory that has never been written (first upgrade ever).
        old = install(tmp / "irica-v1.0.4-win64")
        (old / "telegram_config.json").write_text(json.dumps(
            {"api_base": "https://api.telegram.org", "bot_token": TOKEN,
             "chat_ids": ["555000111"]}), encoding="utf-8")
        (old / "profiles.json").write_text(json.dumps(
            {"profiles": [{"id": "p-on-disk", "title": "configured months ago"}]}),
            encoding="utf-8")
        data_dir = tmp / "AppData" / "Local" / "irica"

        print("=== the shape the release notes tell the operator to use ===")
        print(f"  old install : {old.name}   (has telegram_config.json)")
        new = install(tmp / "irica-v1.0.7-win64")
        print(f"  new install : {new.name}   (fresh, no config)")
        print(f"  data dir    : EMPTY (never written before)\n")

        report = run(new, data_dir)
        print("  migrated          :", report["migrated"])
        print("  token seen        :", repr(report["token"]))
        print("  token for robots  :", repr(report["token_from_robot"]))
        print("  data dir contents :", report["data_dir_contents"])
        print()
        if report["token"] == TOKEN:
            print("  => the upgrade DID import the settings")
        else:
            print("  => THE UPGRADE LOST THE SETTINGS: the operator has to set the")
            print("     token up again, which is what v1.0.7 was supposed to fix.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
