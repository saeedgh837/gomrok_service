"""Run HAMTA/customs inquiries for one profile from the main panel."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import system_debug

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(HERE, "robot_launcher.py")


def _child_env(store_path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["IRICA_ESTELAM_STORE"] = store_path
    return env


def _run(script, args, store_path):
    # Through robot_launcher.py, exactly like the panel's own spawns: it repairs
    # sys.path for the isolated embedded interpreter and captures a startup
    # traceback into the system debug log.
    command = [sys.executable, LAUNCHER, os.path.join(HERE, script)] + args
    return subprocess.call(command, cwd=HERE, env=_child_env(store_path),
                           stdin=subprocess.DEVNULL)


def _profile(profile_id):
    import panel
    return next((p for p in panel.load_store().get("profiles", [])
                 if p.get("id") == profile_id), None)


def _store(profile):
    now = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
    imei_list = [str(s).strip() for s in (profile.get("imeis") or []) if s and str(s).strip()]
    # Build ONE device with both IMEIs (dual-SIM). The customs page expects
    # both serials on the same form; splitting them into separate devices
    # causes "دستگاه شما دارای دو اسلات است" on the second attempt.
    devices = []
    if imei_list:
        devices.append({
            "id": f"{profile['id']}-device",
            "name": profile.get("title") or "",
            "serial1": imei_list[0] if len(imei_list) > 0 else "",
            "serial2": imei_list[1] if len(imei_list) > 1 else "",
            "status": "pending", "result": "",
            "messages": [], "hamta_status": "pending", "hamta_result": "",
            "created": now, "updated": now, "gomrok_at": "", "hamta_at": "",
        })
    return {"person": {"national": profile.get("national") or "",
                       "birth": profile.get("birth") or "",
                       "mobile": profile.get("mobile") or ""},
            "devices": devices, "run_pid": 0, "run_started": "", "run_device_id": ""}


def _record(profile_id, service, data, error=""):
    import panel
    devices = data.get("devices") or []
    if service in ("gomrok", "all"):
        panel.record_profile_device_inquiry(
            profile_id, "gomrok",
            [{"serial": d.get("serial1"), "serial2": d.get("serial2"),
              "status": d.get("status"), "result": d.get("result"),
              "checked_at": d.get("gomrok_at")} for d in devices],
            error=error,
        )
    if service in ("hamta", "all"):
        panel.record_profile_device_inquiry(
            profile_id, "hamta",
            [{"serial": d.get("serial1"), "serial2": d.get("serial2"),
              "status": d.get("hamta_status"), "result": d.get("hamta_result"),
              "checked_at": d.get("hamta_at")} for d in devices],
            error=error,
        )


def main():
    system_debug.configure("profile-inquiry")
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--service", choices=("hamta", "gomrok", "all"), required=True)
    args = parser.parse_args()
    profile = _profile(args.profile_id)
    if not profile:
        return 2
    data = _store(profile)
    if not data["devices"]:
        return 3

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json",
                                         prefix="irica-profile-inquiry-", delete=False) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            temp_path = handle.name
        scripts = {
            "gomrok": ("estelam_gomrok.py", []),
            "hamta": ("estelam_hamta.py", ["--from-store"]),
            "all": ("estelam_all.py", []),
        }
        script, extra = scripts[args.service]
        rc = _run(script, extra, temp_path)
        with open(temp_path, encoding="utf-8") as handle:
            data = json.load(handle)
        _record(args.profile_id, args.service, data,
                error="" if rc == 0 else f"فرآیند استعلام با کد {rc} پایان یافت")
        return rc
    except Exception as exc:
        _record(args.profile_id, args.service, data, error=f"خطای اجرای استعلام: {type(exc).__name__}")
        return 1
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
