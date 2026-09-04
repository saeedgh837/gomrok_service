"""Live check: a real bundle-to-bundle upgrade keeps the Telegram settings.

Stronger than `live_check_data_dir_upgrade.py`: that one ran the repo's modules
under the developer's interpreter. This one uses the STAGED RELEASE BUNDLE and
its OWN embedded, isolated python.exe -- the exact artifact the operator
downloads -- and performs the actual sequence they complained about:

    v1.0.6-style install with the token inside its folder
        -> unzip a new version into a DIFFERENT folder
            -> is the token still there, with nothing copied by hand?

Every assertion runs inside the bundle through `robot_launcher.py`, the same
spawn path the panel uses for a robot.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/live_check_bundle_upgrade.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
STAGED = ROOT / "dist" / f"irica-v{VERSION}-win64"

TOKEN = "8123456789:BUNDLE-UPGRADE-CHECK-token-abcdefgh"
CHAT_IDS = ["101010101", "202020202"]
NATIONAL = "0079999991"

rows = []


def check(label, ok, detail=""):
    rows.append((label, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


def run_probe(install: Path, data_dir: Path, code: str):
    """Run a probe with the BUNDLE'S OWN python, through robot_launcher.py."""
    python = install / "python" / "python.exe"
    script = install / "_tmp_bundle_probe.py"
    script.write_text(code, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME",
                        "IRICA_DATA_DIR", "IRICA_ESTELAM_STORE",
                        "IRICA_ROBOT_PYTHON")}
    env["IRICA_DATA_DIR"] = str(data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    try:
        res = subprocess.run([str(python), str(install / "robot_launcher.py"),
                              str(script)],
                             cwd=str(install), capture_output=True, text=True,
                             timeout=300, env=env)
    finally:
        script.unlink(missing_ok=True)
    for line in (res.stdout or "").splitlines():
        if line.startswith("REPORT "):
            return json.loads(line[len("REPORT "):])
    raise SystemExit("probe produced no report:\n"
                     f"STDOUT:\n{res.stdout[-2000:]}\nSTDERR:\n{res.stderr[-2000:]}")


REPORT_PROBE = """
import json, os
import app_paths, panel
import telegram_notify as tn
out = {
    "python": os.path.abspath(__import__("sys").executable),
    "app_dir": os.path.dirname(os.path.abspath(panel.__file__)),
    "data_dir": app_paths.data_dir(),
    "telegram_path": panel.TELEGRAM_PATH,
    "migrated": panel._MIGRATED_FILES,
    "cfg": panel.load_telegram_config(),
    "notify_path": tn._CFG_PATH,
    "notify_token": (tn._load_cfg() or {}).get("bot_token", ""),
    "public": panel.public_telegram_config(),
}
try:
    with open(panel.PROFILES_PATH, encoding="utf-8") as fh:
        out["profiles"] = json.load(fh).get("profiles", [])
except Exception as exc:
    out["profiles"] = "ERROR: %s" % exc
try:
    with open(panel.ESTELAM_PATH, encoding="utf-8") as fh:
        out["estelam"] = json.load(fh)
except Exception as exc:
    out["estelam"] = "ERROR: %s" % exc
print("REPORT " + json.dumps(out, ensure_ascii=False))
"""


def main():
    if not STAGED.is_dir():
        print(f"FAIL  staged bundle not found: {STAGED}")
        print("      run lab/scripts/build_windows_release.py first")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="irica-bundle-upg-"))
    try:
        data_dir = tmp / "AppData" / "Local" / "irica"

        print(f"=== copying the staged bundle twice (this is slow: {VERSION}) ===")
        old = tmp / "irica-v1.0.6-win64"
        shutil.copytree(STAGED, old)
        print(f"  old install: {old.name}")

        # The pre-upgrade layout: settings sitting INSIDE the install folder.
        (old / "telegram_config.json").write_text(json.dumps(
            {"api_base": "https://api.telegram.org", "bot_token": TOKEN,
             "chat_ids": CHAT_IDS}, ensure_ascii=False), encoding="utf-8")
        (old / "profiles.json").write_text(json.dumps(
            {"profiles": [{"id": "p-legacy", "title": "set up before the upgrade",
                           "national": NATIONAL}]}, ensure_ascii=False),
            encoding="utf-8")
        (old / "estelam_store.json").write_text(json.dumps(
            {"devices": [{"imei": "352506099000001", "status": "good"}],
             "person": {"national": NATIONAL}}, ensure_ascii=False),
            encoding="utf-8")

        print("\n=== 1. first run of the bundle from the OLD folder ===")
        first = run_probe(old, data_dir, REPORT_PROBE)
        check("the bundle's OWN embedded python ran the probe",
              first["python"].lower().startswith(str(old).lower()),
              first["python"])
        check("settings were imported on first run",
              sorted(first["migrated"]) == sorted(
                  ["telegram_config.json", "profiles.json", "estelam_store.json"]),
              str(sorted(first["migrated"])))
        check("the token is intact", first["cfg"]["bot_token"] == TOKEN)
        check("the chat IDs are intact", first["cfg"]["chat_ids"] == CHAT_IDS,
              str(first["cfg"]["chat_ids"]))
        check("config resolves OUTSIDE the install folder",
              not os.path.normcase(first["telegram_path"]).startswith(
                  os.path.normcase(first["app_dir"]) + os.sep),
              first["telegram_path"])
        check("the old copy stays in place for a rollback",
              (old / "telegram_config.json").exists())

        print("\n=== 2. THE UPGRADE: a new install folder, nothing copied ===")
        new = tmp / "irica-v1.0.7-win64"
        shutil.copytree(STAGED, new)
        check("the new install folder ships NO config file",
              not (new / "telegram_config.json").exists()
              and not (new / "profiles.json").exists()
              and not (new / "estelam_store.json").exists())
        second = run_probe(new, data_dir, REPORT_PROBE)
        check("the token is still there after the upgrade",
              second["cfg"]["bot_token"] == TOKEN)
        check("the chat IDs are still there after the upgrade",
              second["cfg"]["chat_ids"] == CHAT_IDS)
        check("profiles are still there after the upgrade",
              isinstance(second["profiles"], list) and second["profiles"]
              and second["profiles"][0]["id"] == "p-legacy",
              str(second["profiles"])[:60])
        check("device inquiries are still there after the upgrade",
              isinstance(second["estelam"], dict)
              and second["estelam"]["devices"][0]["imei"] == "352506099000001")
        check("nothing was re-imported (already shared)",
              second["migrated"] == [], str(second["migrated"]))
        check("both installs resolve to the SAME data directory",
              os.path.normcase(second["data_dir"]) ==
              os.path.normcase(first["data_dir"]))
        check("the robot reads exactly the file the panel writes",
              os.path.normcase(second["notify_path"]) ==
              os.path.normcase(second["telegram_path"]))
        check("the robot sees the same token after the upgrade",
              second["notify_token"] == TOKEN)
        check("the token is never exposed to the browser",
              TOKEN not in json.dumps(second["public"]),
              str(second["public"]))

        print("\n=== 3. change the token in the NEW install, read from the OLD ===")
        save_probe = """
import json, importlib
import panel
panel.save_telegram_config({"api_base": "https://api.telegram.org",
                            "bot_token": "7777777777:CHANGED-IN-THE-NEW-INSTALL",
                            "chat_ids": "303\\n404"})
import telegram_notify as tn
importlib.reload(tn)
print("REPORT " + json.dumps({"token": (tn._load_cfg() or {}).get("bot_token"),
                              "ids": (tn._load_cfg() or {}).get("chat_ids")}))
"""
        saved = run_probe(new, data_dir, save_probe)
        check("saving through the panel is what the robot reads",
              saved["token"] == "7777777777:CHANGED-IN-THE-NEW-INSTALL")
        check("chat IDs typed as text are parsed into a list",
              saved["ids"] == ["303", "404"], str(saved["ids"]))
        back = run_probe(old, data_dir, REPORT_PROBE)
        check("the change is visible from the OTHER install too",
              back["cfg"]["bot_token"] == "7777777777:CHANGED-IN-THE-NEW-INSTALL")

        print("\n=== 4. no install folder was ever polluted ===")
        for label, folder in (("old", old), ("new", new)):
            leaked = sorted(p.name for p in folder.glob("*.json")
                            if p.name in ("telegram_config.json", "profiles.json",
                                          "estelam_store.json", "inputs.json")
                            and not (label == "old" and p.name in
                                     ("telegram_config.json", "profiles.json",
                                      "estelam_store.json")))
            check(f"the {label} install gained no new state file", not leaked,
                  str(leaked))

        print("\n----")
        bad = [n for n, ok in rows if not ok]
        print(f"RESULT: {len(rows) - len(bad)}/{len(rows)} checks passed")
        if bad:
            print("FAILURES:", bad)
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
