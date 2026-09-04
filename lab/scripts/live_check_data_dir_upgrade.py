"""Live check: an upgrade keeps the Telegram settings, with no manual copying.

This reproduces the operator's actual complaint end to end, using the SHIPPED
files and, where possible, the bundled interpreter:

  1. an "old release" folder with a Telegram token, profiles and an estelam
     store sitting inside it (the pre-v1.0.7 layout);
  2. first run of the NEW code from a DIFFERENT folder -> the settings are
     imported automatically, the token is intact, and nothing was moved;
  3. a SECOND upgrade into yet another folder -> the settings are still there
     and are read from the shared data directory, not re-imported;
  4. the token written through the panel's own save path is what
     `telegram_notify` later reads;
  5. the release archive itself never contains a real config file.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/live_check_data_dir_upgrade.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = "8123456789:LIVE-CHECK-TOKEN-do-not-use-abcdefghij"
CHAT_IDS = ["111222333", "444555666"]

# The modules that must follow the data directory. Kept in sync with
# build_windows_release.APP_FILES by the release verifier, not by hand.
APP_MODULES = [
    "app_paths.py", "panel.py", "telegram_notify.py", "gomrok.py",
    "estelam_hamta.py", "estelam_gomrok.py", "captcha_collector.py",
    "browser_window.py", "browser_visibility.py", "system_debug.py",
    "profile_inquiry.py", "robot_launcher.py", "estelam_all.py",
    "exitban.py", "gomrok2.py", "dashboard-new.html",
    "cloudflare_auth_relay.py", "cloudflare_http_proxy.py",
    "telegram_api_relay.py", "start_cloudflare_relay.py",
    "start_named_cloudflare_tunnel.py",
]

rows = []


def check(label, ok, detail=""):
    rows.append((label, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


def fake_release(target: Path) -> Path:
    """A folder shaped like an unzipped release, with the current code in it."""
    target.mkdir(parents=True, exist_ok=True)
    for name in APP_MODULES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, target / name)
    for package in ("solver", "Hamta-solver"):
        if (ROOT / package).is_dir():
            shutil.copytree(ROOT / package, target / package,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "*.pt", "data", "labeler", "tools", "__pycache__"))
    return target


def run_in(release: Path, data_dir: Path, code: str, extra_env=None):
    """Run a probe inside a release folder, exactly as a robot would be spawned."""
    script = release / "_tmp_live_probe.py"
    script.write_text(code, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME",
                        "IRICA_DATA_DIR", "IRICA_ESTELAM_STORE")}
    env["IRICA_DATA_DIR"] = str(data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run([sys.executable, str(release / "robot_launcher.py"),
                               str(script)],
                              cwd=str(release), capture_output=True, text=True,
                              timeout=300, env=env)
    finally:
        script.unlink(missing_ok=True)


PROBE = """
import json, os, sys
import app_paths
import panel

report = {
    "data_dir": app_paths.data_dir(),
    "telegram_path": panel.TELEGRAM_PATH,
    "profiles_path": panel.PROFILES_PATH,
    "estelam_path": panel.ESTELAM_PATH,
    "migrated": panel._MIGRATED_FILES,
    "telegram_cfg": panel.load_telegram_config(),
    "app_dir": os.path.dirname(os.path.abspath(panel.__file__)),
}
try:
    with open(panel.PROFILES_PATH, encoding="utf-8") as handle:
        report["profiles"] = json.load(handle)
except Exception as exc:
    report["profiles"] = "ERROR: %s" % exc
import telegram_notify as tn
report["notify_path"] = tn._CFG_PATH
report["notify_token"] = (tn._load_cfg() or {}).get("bot_token", "")
print("REPORT " + json.dumps(report, ensure_ascii=False))
"""


def parse(res):
    for line in (res.stdout or "").splitlines():
        if line.startswith("REPORT "):
            return json.loads(line[len("REPORT "):])
    raise SystemExit("probe produced no report:\n"
                     f"STDOUT:\n{res.stdout[-2000:]}\nSTDERR:\n{res.stderr[-2000:]}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="irica-upgrade-"))
    try:
        data_dir = tmp / "appdata" / "irica"

        # --- 1. the OLD release: settings live inside the release folder -----
        old = fake_release(tmp / "irica-v1.0.6-win64")
        (old / "telegram_config.json").write_text(json.dumps(
            {"api_base": "https://api.telegram.org", "bot_token": TOKEN,
             "chat_ids": CHAT_IDS}), encoding="utf-8")
        (old / "profiles.json").write_text(json.dumps(
            {"profiles": [{"id": "p-old", "title": "profile from the old release"}]}),
            encoding="utf-8")
        (old / "estelam_store.json").write_text(json.dumps(
            {"devices": [{"imei": "352506099000001"}], "person": {}}),
            encoding="utf-8")
        (old / "inputs.json").write_text('{"national": "0011223344"}', encoding="utf-8")

        print("=== 1. first run of the NEW code, from the OLD folder ===")
        first = parse(run_in(old, data_dir, PROBE))
        check("settings are imported on first run",
              sorted(first["migrated"]) == sorted(
                  ["telegram_config.json", "profiles.json",
                   "estelam_store.json", "inputs.json"]),
              str(sorted(first["migrated"])))
        check("the Telegram token survived intact",
              first["telegram_cfg"]["bot_token"] == TOKEN)
        check("the chat IDs survived intact",
              first["telegram_cfg"]["chat_ids"] == CHAT_IDS,
              str(first["telegram_cfg"]["chat_ids"]))
        check("profiles survived intact",
              first["profiles"]["profiles"][0]["id"] == "p-old")
        check("config now resolves OUTSIDE the release folder",
              not os.path.normcase(first["telegram_path"]).startswith(
                  os.path.normcase(first["app_dir"]) + os.sep),
              first["telegram_path"])
        check("the old copy is left in place for a rollback",
              (old / "telegram_config.json").exists())
        check("the robot reads the same file as the panel",
              os.path.normcase(first["notify_path"]) ==
              os.path.normcase(first["telegram_path"]))
        check("the robot sees the same token",
              first["notify_token"] == TOKEN)

        # --- 2. THE ACTUAL COMPLAINT: a NEW release folder, nothing copied ---
        print("\n=== 2. upgrade: brand-new release folder, nothing copied by hand ===")
        new = fake_release(tmp / "irica-v1.0.7-win64")
        check("the new release folder has NO config file in it",
              not (new / "telegram_config.json").exists())
        second = parse(run_in(new, data_dir, PROBE))
        check("the token is still there after the upgrade",
              second["telegram_cfg"]["bot_token"] == TOKEN)
        check("the chat IDs are still there after the upgrade",
              second["telegram_cfg"]["chat_ids"] == CHAT_IDS)
        check("profiles are still there after the upgrade",
              second["profiles"]["profiles"][0]["id"] == "p-old")
        check("nothing was imported the second time (already shared)",
              second["migrated"] == [], str(second["migrated"]))
        check("both releases resolve to the SAME data directory",
              os.path.normcase(second["data_dir"]) ==
              os.path.normcase(first["data_dir"]))

        # --- 3. a THIRD upgrade, to prove it is not a one-off ---------------
        print("\n=== 3. a third upgrade ===")
        third_dir = fake_release(tmp / "irica-v1.0.8-win64")
        third = parse(run_in(third_dir, data_dir, PROBE))
        check("the token survives repeated upgrades",
              third["telegram_cfg"]["bot_token"] == TOKEN)
        check("no config file is ever created inside a release folder",
              not (third_dir / "telegram_config.json").exists()
              and not (new / "telegram_config.json").exists())

        # --- 4. saving from the panel is what the robot reads ---------------
        print("\n=== 4. save through the panel -> robot reads it ===")
        save_probe = """
import json
import panel
import importlib
panel.save_telegram_config({"api_base": "https://api.telegram.org",
                            "bot_token": "9999999999:SAVED-THROUGH-THE-PANEL-xyzab",
                            "chat_ids": "777\\n888"})
import telegram_notify as tn
importlib.reload(tn)
cfg = tn._load_cfg()
print("REPORT " + json.dumps({"token": cfg.get("bot_token"),
                              "ids": cfg.get("chat_ids"),
                              "public": panel.public_telegram_config()},
                             ensure_ascii=False))
"""
        saved = parse(run_in(new, data_dir, save_probe))
        check("the robot reads the token the panel saved",
              saved["token"] == "9999999999:SAVED-THROUGH-THE-PANEL-xyzab",
              str(saved["token"])[:20] + "...")
        check("chat IDs from the textarea are parsed",
              saved["ids"] == ["777", "888"], str(saved["ids"]))
        check("the token is never sent to the browser",
              "SAVED-THROUGH-THE-PANEL" not in json.dumps(saved["public"]),
              str(saved["public"]))

        # and it is still readable from a DIFFERENT release folder
        after_save = parse(run_in(third_dir, data_dir, PROBE))
        check("the newly saved token is visible from another release folder",
              after_save["telegram_cfg"]["bot_token"] ==
              "9999999999:SAVED-THROUGH-THE-PANEL-xyzab")

        # --- 5. the archive must not carry secrets --------------------------
        print("\n=== 5. the release archive carries no real config ===")
        staged = ROOT / "dist" / f"irica-v{(ROOT / 'VERSION').read_text().strip()}-win64"
        if staged.is_dir():
            leaked = [p.name for p in staged.rglob("*")
                      if p.is_file() and p.name in
                      ("telegram_config.json", "profiles.json",
                       "estelam_store.json", "relay_config.json",
                       "upstream_proxy.json")]
            check("no real config file inside the staged bundle",
                  not leaked, str(leaked))
        else:
            print("SKIP  no staged bundle to inspect yet")

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
