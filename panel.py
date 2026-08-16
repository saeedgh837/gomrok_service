"""
panel.py — panel-e tahte-web baraye modiriyat-e PROFILE-ha-ye robot-e irica.

Faghat ba ketabkhane-ye standard-e Python kar mikonad (http.server).

Mafhoom-ha:
  - PROFILE: yek majmue-ye vorudi (title/national/birth/mobile/imeis + switch-ha).
    hame dar `profiles.json` zakhire mishavand.
  - profile-e ACTIVE be `inputs.json` sync mishavad ta gomrok.py (bedun-e taghyir)
    az an bekhanad.
  - LOG-ha: gomrok.py rvidaad-ha-ye mohem (khata/natije) ra dar `logs.jsonl`
    minevisad; in panel anha ra (ba serial/IMEI) namayesh midahad.

Ejra:
    python panel.py
sepas boro be:  http://127.0.0.1:8750
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROBOT_PYTHON = r"C:\Program Files\Python310\python.exe"
ROBOT_LOG_PATH = os.path.join(HERE, "gomrok_worker.log")
INPUTS_PATH = os.path.join(HERE, "inputs.json")
PROFILES_PATH = os.path.join(HERE, "profiles.json")
LOGS_PATH = os.path.join(HERE, "logs.jsonl")
SITE2_NOTIF_PATH = os.path.join(HERE, "site2_notif.json")
TELEGRAM_PATH = os.path.join(HERE, "telegram_config.json")
TELEGRAM_HEALTH_PATH = os.path.join(HERE, "telegram_health.json")
EXITBAN_ERRORS_PATH = os.path.join(HERE, "exitban_errors.jsonl")
HOST, PORT = "127.0.0.1", 8750
_STORE_LOCK = threading.RLock()
_STORE_LOCK_PATH = PROFILES_PATH + ".lock"


@contextmanager
def _locked_store():
    """Lock-e beyn-processi; chand gomrok.py yek profile ra claim nakonand."""
    import msvcrt
    with _STORE_LOCK:
        os.makedirs(os.path.dirname(_STORE_LOCK_PATH), exist_ok=True)
        with open(_STORE_LOCK_PATH, "a+b") as lock_file:
            if os.path.getsize(_STORE_LOCK_PATH) == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

PROFILE_DEFAULTS = {
    "id": "", "title": "", "national": "", "birth": "", "mobile": "", "telegram_chat_id": "",
    "first_name": "", "last_name": "", "father_name": "", "birth_certificate_no": "",
    "imeis": ["", ""], "no_confirm_check": False, "submit": False,
    "debugging": False, "manual_manage": False,
    "registration_status": "pending", "success_at": "", "payment_link": "",
    "runtime_error": "",
    "run_status": "idle", "runner_id": "", "busy_since": "",
    "receipt_screenshot": "", "payment_screenshot": "",
    "runner_pid": 0,
    "enabled": True,
    "heartbeat_at": "",
    "exitban_status": "not_checked",
    "exitban_result": "",
    "exitban_checked_at": "",
}


def _new_id():
    return uuid.uuid4().hex[:8]


def _clean_profile(p):
    """Yek profile-e vorudi ra tamiz/normal mikonad."""
    out = dict(PROFILE_DEFAULTS)
    out.update({k: p.get(k, v) for k, v in PROFILE_DEFAULTS.items()})
    out["id"] = (p.get("id") or "").strip() or _new_id()
    for k in ("title", "national", "birth", "mobile", "telegram_chat_id", "first_name",
              "last_name", "father_name", "birth_certificate_no", "registration_status",
              "success_at", "payment_link", "runtime_error", "run_status", "runner_id", "busy_since",
              "receipt_screenshot", "payment_screenshot", "heartbeat_at", "exitban_status",
              "exitban_result", "exitban_checked_at"):
        out[k] = str(out.get(k) or "").strip()
    imeis = out.get("imeis") or []
    if not isinstance(imeis, list):
        imeis = [str(imeis)]
    out["imeis"] = [str(x).strip() for x in imeis if x and str(x).strip()]
    out["no_confirm_check"] = bool(out.get("no_confirm_check"))
    out["submit"] = bool(out.get("submit"))
    out["debugging"] = bool(out.get("debugging"))
    out["manual_manage"] = bool(out.get("manual_manage"))
    out["enabled"] = bool(out.get("enabled", True))
    try:
        out["runner_pid"] = int(out.get("runner_pid") or 0)
    except (TypeError, ValueError):
        out["runner_pid"] = 0
    # title-e hushmand: agar khali bud -> kod-e melli ya 'profile-e no'
    if not out["title"]:
        out["title"] = out["national"] or "پروفایل جدید"
    return out


_EXITBAN_CLEAR_TEXT = "شخص با کد ملی ثبت‌شده ممنوع‌الخروج نمی‌باشد."


def _classify_exitban_result(text, status=""):
    status = str(status or "").strip().lower()
    if status in ("clear", "blocked"):
        return status
    normalized = " ".join(str(text or "").replace("‌", "").split())
    if "ممنوعالخروج نمیباشد" in normalized:
        return "clear"
    return "blocked"


def record_exitban_error(profile_id, message):
    message = str(message or "").strip()
    message = __import__("re").sub(r"(?<!\d)\d{10}(?!\d)", "[REDACTED]", message)
    row = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile_id": str(profile_id or ""),
        "message": message[:1000],
    }
    with open(EXITBAN_ERRORS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_exitban_result(profile_id, text, status=""):
    """Natije-ye estelam ra baraye haman profile atomically zakhire mikonad."""
    text = str(text or "").strip()
    if not text:
        return False
    with _locked_store():
        store = load_store()
        for profile in store.get("profiles", []):
            if profile.get("id") != str(profile_id or ""):
                continue
            profile["exitban_status"] = _classify_exitban_result(text, status)
            profile["exitban_result"] = text
            profile["exitban_checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_store(store)
            return True
    return False


def load_store():
    """profiles.json ra mikhanad: {profiles:[...], active_id:"..."}.
    Agar nabashad, az inputs.json-e ghadimi yek profile-e avvaliye misazad
    (mohajerat-e narm)."""
    if os.path.exists(PROFILES_PATH):
        try:
            with open(PROFILES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            profs = [_clean_profile(p) for p in (data.get("profiles") or [])]
            active = data.get("active_id") or (profs[0]["id"] if profs else "")
            if profs and active not in [p["id"] for p in profs]:
                active = profs[0]["id"]
            return {"profiles": profs, "active_id": active}
        except Exception:
            pass
    # mohajerat az inputs.json (agar bud)
    profs = []
    if os.path.exists(INPUTS_PATH):
        try:
            with open(INPUTS_PATH, encoding="utf-8") as f:
                old = json.load(f)
            if old:
                profs = [_clean_profile(old)]
        except Exception:
            pass
    if not profs:
        profs = [_clean_profile({})]
    store = {"profiles": profs, "active_id": profs[0]["id"]}
    save_store(store)
    return store


def save_store(store):
    profs = [_clean_profile(p) for p in store.get("profiles", [])]
    active = store.get("active_id") or (profs[0]["id"] if profs else "")
    if profs and active not in [p["id"] for p in profs]:
        active = profs[0]["id"]
    clean = {"profiles": profs, "active_id": active}
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    sync_active_to_inputs(clean)
    return clean


def mark_profile_success(title="", national="", imei="", payment_link=""):
    """Profile-e motabegh ra vaghti be pardakht resid movaffagh mikonad."""
    import time
    store = load_store()
    title, national, imei = str(title).strip(), str(national).strip(), str(imei).strip()
    target = None
    for p in store.get("profiles", []):
        if national and p.get("national") == national:
            target = p
            break
        if imei and imei in (p.get("imeis") or []):
            target = p
            break
        if title and p.get("title") == title:
            target = p
    if not target:
        return False
    target["registration_status"] = "success"
    target["success_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    target["payment_link"] = str(payment_link or "").strip()
    save_store(store)
    return True


def mark_profile_unusable(profile_id, error_text):
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", [])
                        if p.get("id") == str(profile_id)), None)
        if not profile:
            return False
        profile["registration_status"] = "blocked"
        profile["enabled"] = False
        profile["runtime_error"] = (
            "این پروفایل دیگر قابل ورود نیست: " + str(error_text or "").strip())
        profile["run_status"] = "idle"
        profile["runner_id"] = ""
        profile["busy_since"] = ""
        profile["runner_pid"] = 0
        profile["heartbeat_at"] = ""
        save_store(store)
        return True


def merge_profile_update(current, submitted):
    """Field-haye runtime/status ra dar edit-e form az dast nemidahad."""
    merged = dict(current or {})
    merged.update(submitted or {})
    return merged


def clone_profile(profile_id):
    with _locked_store():
        store = load_store()
        source = next((p for p in store.get("profiles", []) if p.get("id") == profile_id), None)
        if not source:
            return None
        base = source.get("title") or source.get("national") or "پروفایل"
        existing = {p.get("title") for p in store.get("profiles", [])}
        number = 2
        while f"{base} {number}" in existing:
            number += 1
        clone = dict(source)
        clone.update({
            "id": _new_id(), "title": f"{base} {number}",
            "registration_status": "pending", "success_at": "", "payment_link": "",
            "run_status": "idle", "runner_id": "", "busy_since": "", "runner_pid": 0,
            "heartbeat_at": "", "exitban_status": "not_checked", "exitban_result": "",
            "exitban_checked_at": "",
            "receipt_screenshot": "", "payment_screenshot": "",
        })
        clone = _clean_profile(clone)
        store["profiles"].append(clone)
        store["active_id"] = clone["id"]
        save_store(store)
        return clone


def claim_profile(profile_id=None):
    """Avalin profile-e azad va takmil-nashode ra atomically be yek robot midahad."""
    with _locked_store():
        store = load_store()
        profiles = store.get("profiles", [])
        active = store.get("active_id")
        ordered = ([p for p in profiles if p.get("id") == profile_id] if profile_id else
                   sorted(profiles, key=lambda p: p.get("id") != active))
        for p in ordered:
            if p.get("run_status") == "busy" and not _pid_alive(p.get("runner_pid")):
                p["run_status"], p["runner_id"], p["busy_since"], p["runner_pid"], p["heartbeat_at"] = "idle", "", "", 0, ""
            if (((not p.get("enabled", True)) and not profile_id) or p.get("run_status") == "busy" or
                    p.get("registration_status") == "success"):
                continue
            runner_id = uuid.uuid4().hex
            p["run_status"] = "busy"
            p["runner_id"] = runner_id
            p["busy_since"] = time.strftime("%Y-%m-%d %H:%M:%S")
            p["heartbeat_at"] = p["busy_since"]
            p["runner_pid"] = os.getpid()
            save_store(store)
            result = dict(p)
            result["runner_id"] = runner_id
            return result
    return None


def start_profile_process(profile_id):
    python_executable = ROBOT_PYTHON if os.path.isfile(ROBOT_PYTHON) else sys.executable
    command = [python_executable, os.path.join(HERE, "gomrok.py"), "--profile-id", str(profile_id)]
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid, "profile_id": str(profile_id)}


def start_profile2_process(profile_id):
    python_executable = ROBOT_PYTHON if os.path.isfile(ROBOT_PYTHON) else sys.executable
    command = [python_executable, os.path.join(HERE, "gomrok2.py"), "--profile-id", str(profile_id)]
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid, "profile_id": str(profile_id)}


def start_site2_manual_process():
    python_executable = ROBOT_PYTHON if os.path.isfile(ROBOT_PYTHON) else sys.executable
    command = [python_executable, os.path.join(HERE, "gomrok2.py"), "--no-panel"]
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid}


def start_profile2(profile_id):
    profile_id = str(profile_id or "").strip()
    with _locked_store():
        profile = next((p for p in load_store().get("profiles", []) if p.get("id") == profile_id), None)
        if not profile:
            return 404, {"error": "profile not found"}
        if profile.get("run_status") == "busy":
            return 409, {"error": "profile is already running"}
        if profile.get("registration_status") == "blocked":
            return 409, {"error": "این پروفایل قابل استفاده نیست"}
    try:
        runtime = start_profile2_process(profile_id)
    except (OSError, ValueError):
        return 500, {"error": "شروع ربات سایت دوم انجام نشد"}
    # profile-ra INJA busy nemikonim; khod-e gomrok2.py ba claim_profile() an-ra
    # busy + runner_id mikonad (mesl-e start_profile). faghat pid-ra sabt mikonim
    # ta ta zaman-e claim, clear_stale_busy_profiles process-e zende-ra nakoshad.
    set_profile_runtime(profile_id, runtime["pid"])
    return 200, {"ok": True, "profile_id": profile_id, "pid": runtime["pid"]}


def start_profile(profile_id):
    profile_id = str(profile_id or "").strip()
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", []) if p.get("id") == profile_id), None)
        if not profile:
            return 404, {"error": "profile not found"}
        if profile.get("run_status") == "busy":
            return 409, {"error": "profile is already running"}
        if profile.get("registration_status") == "success":
            return 409, {"error": "profile is already complete"}
        if profile.get("registration_status") == "blocked":
            return 409, {"error": "این پروفایل قابل استفاده نیست؛ خطای غیرقابل‌ورود ثبت شده است"}
        missing = []
        if not profile.get("manual_manage"):
            if not profile.get("national"):
                missing.append("کد ملی")
            if not profile.get("birth"):
                missing.append("تاریخ تولد")
            if not profile.get("imeis"):
                missing.append("سریال / IMEI")
        if missing:
            return 422, {"error": "اطلاعات پروفایل ناقص است", "missing": missing}
    try:
        runtime = start_profile_process(profile_id)
    except (OSError, ValueError):
        return 500, {"error": "شروع ربات انجام نشد"}
    set_profile_runtime(profile_id, runtime["pid"])
    return 200, {"ok": True, "profile_id": profile_id, "pid": runtime["pid"]}


def start_exitban_process(profile_id):
    log_path = os.path.join(HERE, "exitban_worker.log")
    command = [sys.executable, os.path.join(HERE, "exitban.py"),
               "--profile-id", str(profile_id), "--submit"]
    log_file = open(log_path, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid, "profile_id": str(profile_id)}


def stop_profile_process(pid):
    if not pid or not _pid_alive(pid):
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        os.kill(int(pid), signal.SIGTERM)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def stop_profile(profile_id):
    profile_id = str(profile_id or "").strip()
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", []) if p.get("id") == profile_id), None)
        if not profile:
            return 404, {"error": "profile not found"}
        pid = int(profile.get("runner_pid") or 0)
    stopped = stop_profile_process(pid) if pid else False
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", []) if p.get("id") == profile_id), None)
        if profile and profile.get("run_status") == "busy":
            profile["run_status"] = "idle"
            profile["runner_id"] = ""
            profile["busy_since"] = ""
            profile["runner_pid"] = 0
            profile["heartbeat_at"] = ""
            save_store(store)
    return 200, {"ok": True, "stopped": stopped, "profile_id": profile_id}


def set_profile_runtime(profile_id, pid, runner_id=""):
    with _locked_store():
        store = load_store()
        for profile in store.get("profiles", []):
            if profile.get("id") == profile_id:
                profile["runner_pid"] = int(pid or 0)
                if runner_id:
                    profile["runner_id"] = runner_id
                save_store(store)
                return True
    return False


def _pid_alive(pid):
    import ctypes
    if not pid:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _heartbeat_stale(value, max_age=120):
    try:
        stamp = time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
        return time.time() - stamp > max_age
    except (TypeError, ValueError):
        return True


def heartbeat_profile(profile_id, runner_id):
    with _locked_store():
        store = load_store()
        for p in store.get("profiles", []):
            if p.get("id") == profile_id and p.get("runner_id") == runner_id and p.get("run_status") == "busy":
                p["heartbeat_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                save_store(store)
                return True
    return False


def clear_stale_busy_profiles():
    """Lock-haye process-hayi ke digar zende nistand ra pak mikonad."""
    changed = False
    with _locked_store():
        store = load_store()
        for p in store.get("profiles", []):
            if p.get("run_status") != "busy":
                continue
            heartbeat = p.get("heartbeat_at") or p.get("busy_since")
            if (p.get("runner_pid") and _pid_alive(p["runner_pid"]) and
                    not _heartbeat_stale(heartbeat)):
                continue
            p["run_status"], p["runner_id"], p["busy_since"], p["runner_pid"], p["heartbeat_at"] = "idle", "", "", 0, ""
            changed = True
        if changed:
            save_store(store)
    return changed


def release_profile(profile_id, runner_id):
    with _locked_store():
        store = load_store()
        for p in store.get("profiles", []):
            if p.get("id") == profile_id and p.get("runner_id") == runner_id:
                p["run_status"], p["runner_id"], p["busy_since"], p["runner_pid"], p["heartbeat_at"] = "idle", "", "", 0, ""
                save_store(store)
                return True
    return False


def record_profile_output(profile_id, runner_id="", receipt="", payment="", link=""):
    with _locked_store():
        store = load_store()
        for p in store.get("profiles", []):
            if p.get("id") != profile_id:
                continue
            if runner_id and p.get("runner_id") not in ("", runner_id):
                return False
            p["registration_status"] = "success"
            p["success_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            p["receipt_screenshot"] = str(receipt or "")
            p["payment_screenshot"] = str(payment or "")
            p["payment_link"] = str(link or "")
            save_store(store)
            return True
    return False


def load_telegram_config():
    try:
        with open(TELEGRAM_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    ids = cfg.get("chat_ids") or ([cfg.get("chat_id")] if cfg.get("chat_id") else [])
    return {"api_base": cfg.get("api_base", "https://api.telegram.org"),
            "bot_token": cfg.get("bot_token", ""),
            "chat_ids": [str(x).strip() for x in ids if str(x).strip()]}


def save_telegram_config(cfg):
    """Token faghat vaghti ke karbar meghdar-e JADID bedahad avaz mishavad.
    (UI token-e zakhire-shode ra NEMIFRESTAD, pas khali = 'taghir nade')."""
    current = load_telegram_config()
    incoming_token = str(cfg.get("bot_token") or "").strip()
    clean = {"api_base": str(cfg.get("api_base") or "https://api.telegram.org").strip(),
             "bot_token": incoming_token or current.get("bot_token", ""),
             "chat_ids": [x.strip() for x in str(cfg.get("chat_ids") or "").replace(",", "\n").splitlines() if x.strip()]}
    with open(TELEGRAM_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    return public_telegram_config()


def public_telegram_config():
    """Nokhse-ye BEDUN-e token baraye UI — token hargez be morurgar nemiravad."""
    cfg = load_telegram_config()
    return {"api_base": cfg.get("api_base", "https://api.telegram.org"),
            "chat_ids": cfg.get("chat_ids", []),
            "token_set": bool(str(cfg.get("bot_token") or "").strip())}


TELEGRAM_LOCAL_BASE = "http://127.0.0.1:8752"
RELAY_PROXY_PORT = 18081
RELAY_API_PORT = 8752


def _port_listening(port, host="127.0.0.1", timeout=0.4):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, int(port))) == 0


def _process_running(image_name):
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
                             capture_output=True, text=True, check=False).stdout
    except OSError:
        return False
    return image_name.lower() in (out or "").lower()


def _spawn_detached(command, cwd=None):
    """Detached child (bedun-e panjere) ke ba baste shodan-e panel nemirad."""
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                 getattr(subprocess, "DETACHED_PROCESS", 0) |
                 getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(command, cwd=cwd or HERE, env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT,
                            creationflags=flags)


def _wait_port(port, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_listening(port):
            return True
        time.sleep(0.35)
    return False


def ensure_telegram_relay_chain(wait_tunnel=True):
    """Zanjire-ye ersal be telegram ra bala miavarad (idempotent):
        cloudflare_http_proxy.py :18081  ->  telegram_api_relay.py :8752
        -> cloudflared named tunnel (telegram.techbysaeed.ir)
    Har ghesmati ke az ghabl bala bashad dast nakhorde mimanad.
    """
    python_executable = ROBOT_PYTHON if os.path.isfile(ROBOT_PYTHON) else sys.executable
    started = []

    if not _port_listening(RELAY_PROXY_PORT):
        script = os.path.join(HERE, "cloudflare_http_proxy.py")
        if os.path.isfile(script):
            _spawn_detached([python_executable, "-u", script])
            started.append("http_proxy:18081")
            _wait_port(RELAY_PROXY_PORT, 15)

    if not _port_listening(RELAY_API_PORT):
        script = os.path.join(HERE, "telegram_api_relay.py")
        if os.path.isfile(script):
            _spawn_detached([python_executable, "-u", script])
            started.append("telegram_relay:8752")
            _wait_port(RELAY_API_PORT, 15)

    tunnel_needed = str(load_telegram_config().get("api_base") or "").startswith("https://")
    if tunnel_needed and not _process_running("cloudflared.exe"):
        cloudflared = os.path.join(HERE, ".tools", "cloudflared.exe")
        config = os.path.join(HERE, "cloudflare_named_tunnel.yml")
        if os.path.isfile(cloudflared) and os.path.isfile(config):
            log_path = os.path.join(HERE, "cloudflare_named_tunnel.log")
            try:
                with open(log_path, "w", encoding="utf-8") as log:
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
                        getattr(subprocess, "DETACHED_PROCESS", 0)
                    env = os.environ.copy()
                    env["TUNNEL_PROTOCOL"] = "http2"
                    subprocess.Popen([cloudflared, "tunnel", "--config", config, "run"],
                                     cwd=os.path.join(HERE, ".tools"), env=env,
                                     stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, creationflags=flags)
                started.append("cloudflared")
                if wait_tunnel:
                    _wait_tunnel_registered(log_path, timeout=30)
            except OSError:
                pass

    status = {
        "proxy": _port_listening(RELAY_PROXY_PORT),
        "relay": _port_listening(RELAY_API_PORT),
        "tunnel": _process_running("cloudflared.exe"),
        "started": started,
    }
    if started:
        print(f">>> Telegram relay chain: start shod -> {', '.join(started)}")
    print(f">>> Telegram relay chain: proxy={status['proxy']} relay={status['relay']} "
          f"tunnel={status['tunnel']}")
    return status


def _wait_tunnel_registered(log_path, timeout=30):
    """Sabr ta cloudflared aghal yek connection sabt konad (vagarna 502 midahad)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as handle:
                if "Registered tunnel connection" in handle.read():
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def test_telegram_api(attempts=3, ensure_chain=False):
    """Connectivity/auth test via getMe; does not send a message.

    Agar api_base-e omoomi (tunnel) javab nadahad, relay-e MOHALI (127.0.0.1:8752)
    emtehan mishavad — chon ersal-e vaghe'i ham az hamin masir mitavanad anjam
    shavad. `used_base` neshan midahad kodam masir javab dad.
    """
    import requests
    if ensure_chain:
        try:
            ensure_telegram_relay_chain()
        except Exception as exc:
            print(f">>> (ensure_telegram_relay_chain khata dad: {exc})")
    cfg = load_telegram_config()
    base = str(cfg.get("api_base") or "").rstrip("/")
    token = str(cfg.get("bot_token") or "").strip()
    result = {
        "ok": False,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_base": base,
        "used_base": "",
        "message": "تنظیمات API تلگرام کامل نیست.",
    }
    if not (base and token):
        _write_telegram_health(result)
        return result

    candidates = [base]
    if TELEGRAM_LOCAL_BASE.rstrip("/") != base:
        candidates.append(TELEGRAM_LOCAL_BASE)

    last_message = result["message"]
    for attempt in range(1, max(1, int(attempts)) + 1):
        for candidate in candidates:
            try:
                response = requests.get(f"{candidate}/bot{token}/getMe", timeout=20)
                payload = response.json()
                if response.ok and payload.get("ok"):
                    username = str((payload.get("result") or {}).get("username") or "ربات")
                    note = "" if candidate == base else " (از مسیر رله محلی)"
                    result.update(ok=True, used_base=candidate,
                                  message=f"ارتباط API تلگرام برقرار است — @{username}{note}")
                    result["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    _write_telegram_health(result)
                    return result
                last_message = str(payload.get("description") or
                                   f"پاسخ نامعتبر API ({response.status_code})")
            except Exception as exc:
                last_message = f"ارتباط API تلگرام ناموفق بود: {type(exc).__name__}"
        if attempt < attempts:
            time.sleep(2.0 * attempt)
    result["message"] = last_message
    result["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_telegram_health(result)
    return result


def _write_telegram_health(result):
    try:
        with open(TELEGRAM_HEALTH_PATH, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return result


def _startup_telegram_bootstrap():
    """Ba bala amadan-e panel: zanjire-ye relay ra bala biavar, sepas test kon.
    Agar bar-e aval javab nadad (tunnel taze sabt mishavad), dobare talash kon."""
    health = test_telegram_api(attempts=3, ensure_chain=True)
    if not health.get("ok"):
        time.sleep(6)
        health = test_telegram_api(attempts=3, ensure_chain=True)
    tag = "OK" if health.get("ok") else "FAIL"
    print(f">>> Telegram API health [{tag}]: {health.get('message')}")
    return health


def load_telegram_health():
    try:
        with open(TELEGRAM_HEALTH_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"ok": False, "ts": "", "message": "تست ارتباط API هنوز انجام نشده است."}


def sync_active_to_inputs(store):
    """profile-e active ra be inputs.json (format-i ke gomrok.py mikhanad) minevisad."""
    active = None
    for p in store.get("profiles", []):
        if p["id"] == store.get("active_id"):
            active = p
            break
    if not active and store.get("profiles"):
        active = store["profiles"][0]
    if not active:
        return
    inp = {
        "title": active["title"],
        "national": active["national"],
        "birth": active["birth"],
        "mobile": active["mobile"],
        "telegram_chat_id": active["telegram_chat_id"],
        "imeis": active["imeis"],
        "no_confirm_check": active["no_confirm_check"],
        "submit": active["submit"],
        "debugging": active["debugging"],
        "manual_manage": active["manual_manage"],
    }
    with open(INPUTS_PATH, "w", encoding="utf-8") as f:
        json.dump(inp, f, ensure_ascii=False, indent=2)


def load_logs(limit=200):
    """logs.jsonl ra mikhanad va list-e rvidaad-ha (jadidtar avval) barmigardanad."""
    if not os.path.exists(LOGS_PATH):
        return []
    out = []
    try:
        with open(LOGS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    out.reverse()  # jadidtar avval
    return out[:limit]


def log_site2_success(national=""):
    """Rvidaad-e movaffaghiat-e vorud-e site2: yek log dar logs.jsonl + yek
    notif-e jadid baraye panel-e site2 (dashboard-new.html an-ra flash mikonad)."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    national = str(national or "").strip()
    message = (f"کد ملی {national} در {ts} با موفقیت وارد حساب کاربری در سایت دوم شد"
               if national else f"در {ts} با موفقیت وارد حساب کاربری در سایت دوم شد")
    rec = {
        "ts": ts, "title": "", "national": national, "imei": "",
        "step": "ورود موفق", "status": "success", "message": message,
    }
    try:
        with open(LOGS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f">>> (log-e site2 na-shod: {e})")
    try:
        with open(SITE2_NOTIF_PATH, "w", encoding="utf-8") as f:
            json.dump({"ts": ts, "message": message}, f, ensure_ascii=False)
    except Exception as e:
        print(f">>> (notif-e site2 na-shod: {e})")
    return message


def load_site2_notif():
    if not os.path.exists(SITE2_NOTIF_PATH):
        return {}
    try:
        with open(SITE2_NOTIF_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------- servis-e ESTELAM-e dastgah (jaygozin-e site-2) ----------------
ESTELAM_PATH = os.path.join(HERE, "estelam_store.json")
ESTELAM_LOGS_PATH = os.path.join(HERE, "estelam_logs.jsonl")
_ESTELAM_LOCK = threading.RLock()

ESTELAM_STATUS_FA = {
    "pending": "در انتظار استعلام",
    "running": "در حال استعلام",
    "healthy": "سالم (شناسه در گمرک ورودی یافت نشد)",
    "problem": "دارای اشکال",
    "advanced": "هشدار: رفتن به مرحله بعد",
    "error": "خطای ربات",
}

# Badge-e KUTAH-e sotoon-e vaziat baraye GOMROK — ham-shakl-e badge-haye HAMTA.
# (motn-e kamel/tozihat dar sotoon-e «نتیجه» va modal-e «مشاهده کامل» miayad,
#  vagarna badge-e gomrok chand-khati mishavad va jadval ra mikeshad)
ESTELAM_BADGE_FA = {
    "pending": "در انتظار",
    "running": "در حال استعلام",
    "healthy": "مجاز (قابل ثبت)",
    "problem": "مسدود/خطر",
    "advanced": "مسدود/خطر",
    "error": "خطای ربات",
}


def estelam_load():
    with _ESTELAM_LOCK:
        data = {"person": {"national": "", "birth": "", "mobile": ""}, "devices": [],
                "run_pid": 0, "run_started": "", "run_device_id": ""}
        if os.path.exists(ESTELAM_PATH):
            try:
                with open(ESTELAM_PATH, encoding="utf-8") as f:
                    loaded = json.load(f) or {}
                person = dict(data["person"])
                person.update(loaded.get("person") or {})
                data.update(loaded)          # run_pid/run_started/... gom nashavand
                data["person"] = person
                data["devices"] = list(loaded.get("devices") or [])
            except Exception:
                pass
        _estelam_migrate_devices(data["devices"])
        return data


def _estelam_migrate_devices(devices):
    """Field-haye jadid (name / gomrok_at / hamta_at) ra roo-ye record-haye
    GHADIMI-ye estelam_store.json misazad ta UI khali nabinad. Baraye device-i
    ke estelam-e nahayi dashte vali zaman-ash sabt nashode, az 'updated' (ya
    'created') estefade mishavad."""
    for device in devices:
        device.setdefault("name", "")
        fallback = str(device.get("updated") or device.get("created") or "")
        if "gomrok_at" not in device:
            device["gomrok_at"] = (
                fallback if str(device.get("status") or "") in _ESTELAM_FINAL else "")
        if "hamta_at" not in device:
            device["hamta_at"] = (
                fallback if str(device.get("hamta_status") or "") in _ESTELAM_FINAL else "")


def estelam_save(data):
    with _ESTELAM_LOCK:
        with open(ESTELAM_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data


def validate_estelam_person(person):
    """Barmigardanad list-e khata-haye farsi (khali = motabar)."""
    errors = []
    national = str((person or {}).get("national") or "").strip()
    birth = str((person or {}).get("birth") or "").strip()
    mobile = str((person or {}).get("mobile") or "").strip()
    if not re.fullmatch(r"\d{10}", national):
        errors.append("کد ملی باید ۱۰ رقم عدد باشد")
    if not re.fullmatch(r"1[34]\d{2}/\d{2}/\d{2}", birth):
        errors.append("تاریخ تولد باید به شکل ۱۳۷۵/۰۴/۲۹ باشد")
    if not re.fullmatch(r"09\d{9}", mobile):
        errors.append("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود")
    return errors


def validate_estelam_device(device):
    errors = []
    name = str((device or {}).get("name") or "").strip()
    serial1 = str((device or {}).get("serial1") or "").strip()
    serial2 = str((device or {}).get("serial2") or "").strip()
    if not name:
        errors.append("نام دستگاه الزامی است")
    elif len(name) > 40:
        errors.append("نام دستگاه حداکثر ۴۰ کاراکتر باشد")
    if not serial1:
        errors.append("سریال اول الزامی است")
    for label, value in (("سریال اول", serial1), ("سریال دوم", serial2)):
        if value and not re.fullmatch(r"\d{15}", value):
            errors.append(f"{label} باید ۱۵ رقم عدد باشد")
    return errors


def estelam_save_person(person):
    data = estelam_load()
    data["person"] = {
        "national": str((person or {}).get("national") or "").strip(),
        "birth": str((person or {}).get("birth") or "").strip(),
        "mobile": str((person or {}).get("mobile") or "").strip(),
    }
    return estelam_save(data)


def estelam_add_device(device):
    data = estelam_load()
    record = {
        "id": _new_id(),
        "name": str((device or {}).get("name") or "").strip(),
        "serial1": str((device or {}).get("serial1") or "").strip(),
        "serial2": str((device or {}).get("serial2") or "").strip(),
        "status": "pending",
        "result": "",
        "messages": [],
        "hamta_status": "pending",
        "hamta_result": "",
        # zaman-e akharin estelam-e har servis ("" = hanooz estelam nashode)
        "gomrok_at": "",
        "hamta_at": "",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated": "",
    }
    data["devices"].append(record)
    estelam_save(data)
    return record


def estelam_delete_device(device_id):
    data = estelam_load()
    data["devices"] = [d for d in data["devices"] if str(d.get("id")) != str(device_id)]
    return estelam_save(data)


# Vaziat-haye NAHAYI (na 'pending'/'running') — residan be in-ha ya'ni yek estelam
# vaghe'an anjam shode, pas zaman-e estelam bayad mohr shavad.
_ESTELAM_FINAL = {"healthy", "problem", "advanced", "error",
                  "good", "registered", "blocked", "success", "unknown"}


def estelam_set_device(device_id, **fields):
    """Robot-ha (estelam_gomrok.py / estelam_hamta.py) natije ra ba in tabe'
    minevisand. Ba residan be yek vaziat-e NAHAYI, zaman-e estelam-e haman
    servis (gomrok_at / hamta_at) mohr mishavad."""
    data = estelam_load()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for device in data["devices"]:
        if str(device.get("id")) == str(device_id):
            device.update(fields)
            if str(fields.get("status") or "") in _ESTELAM_FINAL:
                device["gomrok_at"] = now
            if str(fields.get("hamta_status") or "") in _ESTELAM_FINAL:
                device["hamta_at"] = now
            device["updated"] = now
            break
    estelam_save(data)
    return data


def estelam_reset_pending():
    data = estelam_load()
    for device in data["devices"]:
        if device.get("status") == "running":
            device["status"] = "pending"
        if device.get("hamta_status") == "running":
            device["hamta_status"] = "pending"
    return estelam_save(data)


def estelam_reset_pending_stale():
    """Agar process-e estelam mordeh bashad, device-haye 'running'-e ja-mandeh
    ra be 'pending' bar migardanad (vagarna panel baraye hamishe running neshan
    midahad)."""
    data = estelam_load()
    stuck = [d for d in data["devices"]
             if d.get("status") == "running" or d.get("hamta_status") == "running"]
    if not stuck:
        return data
    pid = data.get("run_pid")
    if pid and _pid_alive(int(pid)):
        return data
    for device in stuck:
        if device.get("status") == "running":
            device["status"] = "pending"
        if device.get("hamta_status") == "running":
            device["hamta_status"] = "pending"
    return estelam_save(data)


# service -> (script, arg-haye ezafe baraye khandan-e device az store)
ESTELAM_SERVICES = {
    "gomrok": ("estelam_gomrok.py", []),
    "hamta": ("estelam_hamta.py", ["--from-store"]),
    "all": ("estelam_all.py", []),
}
ESTELAM_SERVICE_FA = {"gomrok": "گمرک", "hamta": "همتا", "all": "همه استعلام‌ها"}


def start_estelam_process(device_id=None, service="gomrok"):
    python_executable = ROBOT_PYTHON if os.path.isfile(ROBOT_PYTHON) else sys.executable
    script, base_args = ESTELAM_SERVICES.get(service, ESTELAM_SERVICES["gomrok"])
    command = [python_executable, os.path.join(HERE, script)] + list(base_args)
    if device_id:
        command += ["--device-id", str(device_id)]
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env,
                                   stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid}


def estelam_start(device_id=None, service="gomrok"):
    """Ghabl az ejra: etela'at-e shakhs bayad KAMEL va MOTABAR bashad."""
    if service not in ESTELAM_SERVICES:
        service = "gomrok"
    data = estelam_load()
    errors = validate_estelam_person(data.get("person"))
    # HAMTA faghat serial mikhahad (bedun-e login), pas etela'at-e shakhs lazem nist;
    # gomrok/all be kod-e melli va tarikh-e tavallod niaz darand.
    if errors and service != "hamta":
        return {"ok": False, "errors": ["اطلاعات شخص برای استعلام را پر کنید"] + errors}
    if estelam_is_running():
        return {"ok": False, "errors": ["یک اجرای استعلام در حال انجام است — ابتدا آن را متوقف کنید"]}
    targets = data.get("devices") or []
    if device_id:
        targets = [d for d in targets if str(d.get("id")) == str(device_id)]
    elif service == "hamta":
        targets = [d for d in targets
                   if str(d.get("hamta_status") or "pending") in ("pending", "error", "")]
    else:
        targets = [d for d in targets
                   if str(d.get("status") or "pending") in ("pending", "error", "")]
    if not targets:
        return {"ok": False, "errors": ["دستگاهی برای استعلام وجود ندارد"]}
    runtime = start_estelam_process(device_id, service=service)
    data = estelam_load()
    data["run_pid"] = runtime.get("pid")
    data["run_device_id"] = str(device_id or "")
    data["run_service"] = service
    data["run_started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    estelam_save(data)
    estelam_log("info", f"استعلام {ESTELAM_SERVICE_FA.get(service, service)} شروع شد "
                        f"({len(targets)} دستگاه) — pid {runtime.get('pid')}",
                step="مدیریت")
    return {"ok": True, "pid": runtime.get("pid"), "count": len(targets),
            "service": service}


def estelam_is_running():
    data = estelam_load()
    pid = data.get("run_pid")
    try:
        return bool(pid) and _pid_alive(int(pid))
    except (TypeError, ValueError):
        return False


def estelam_runtime():
    """Vaziat-e process-e estelam baraye bakhsh-e modiriat-e panel."""
    data = estelam_load()
    devices = data.get("devices") or []
    counts = {}
    hamta_counts = {}
    for device in devices:
        key = str(device.get("status") or "pending")
        counts[key] = counts.get(key, 0) + 1
        hkey = str(device.get("hamta_status") or "pending")
        hamta_counts[hkey] = hamta_counts.get(hkey, 0) + 1
    service = str(data.get("run_service") or "gomrok")
    return {
        "running": estelam_is_running(),
        "pid": data.get("run_pid") or 0,
        "started": data.get("run_started") or "",
        "device_id": data.get("run_device_id") or "",
        "service": service,
        "service_fa": ESTELAM_SERVICE_FA.get(service, service),
        "total": len(devices),
        "counts": counts,
        "hamta_counts": hamta_counts,
    }


def estelam_stop():
    """Process-e estelam (va morurgar-e farzandash) ra mibandad."""
    data = estelam_load()
    pid = int(data.get("run_pid") or 0)
    stopped = stop_profile_process(pid) if pid else False
    data = estelam_load()
    data["run_pid"] = 0
    for device in data["devices"]:
        if device.get("status") == "running":
            device["status"] = "pending"
            device["result"] = "توقف دستی توسط کاربر"
        if device.get("hamta_status") == "running":
            device["hamta_status"] = "pending"
            device["hamta_result"] = "توقف دستی توسط کاربر"
    estelam_save(data)
    estelam_log("warn" if stopped else "info",
                ("استعلام متوقف و مرورگر بسته شد" if stopped
                 else "اجرای فعالی برای توقف پیدا نشد"),
                step="مدیریت")
    return {"ok": True, "stopped": stopped, "runtime": estelam_runtime()}


def estelam_restart():
    """Stop + start-e dobare (hamun scope-e ejra-ye ghabli)."""
    prev = estelam_load()
    previous = prev.get("run_device_id") or ""
    service = prev.get("run_service") or "gomrok"
    estelam_stop()
    time.sleep(0.6)
    result = estelam_start(previous or None, service=service)
    result["restarted"] = True
    return result


def estelam_log(status, message, step="", serial="", device_id=""):
    """Yek rvidaad-e servis-e estelam (panel + robot har do minevisand)."""
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "step": step or "",
        "serial": serial or "",
        "device_id": device_id or "",
        "message": message or "",
    }
    try:
        with open(ESTELAM_LOGS_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return record


def load_estelam_logs(limit=300):
    out = []
    try:
        if not os.path.exists(ESTELAM_LOGS_PATH):
            return []
        with open(ESTELAM_LOGS_PATH, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        return []
    out.reverse()
    return out[:limit]


def clear_estelam_logs():
    try:
        if os.path.exists(ESTELAM_LOGS_PATH):
            os.remove(ESTELAM_LOGS_PATH)
    except OSError:
        pass
    return {"ok": True}


def filter_profile_logs(logs, profile):
    title = str(profile.get("title") or "")
    national = str(profile.get("national") or "")
    return [log for log in logs if (title and log.get("title") == title) or
            (national and log.get("national") == national)]


def sync_shared_identity(profile):
    """Fields-e moshtarak-e basic/identity yek source darند."""
    profile["national"] = str(profile.get("national") or "").strip()
    profile["birth"] = str(profile.get("birth") or "").strip()
    profile["identity_national"] = profile["national"]
    profile["identity_birth"] = profile["birth"]
    return profile


def clear_logs():
    try:
        if os.path.exists(LOGS_PATH):
            os.remove(LOGS_PATH)
    except Exception:
        pass


PAGE_HEAD = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>پنل ربات گمرک</title>
<link rel="icon" href="data:,">
<style>
  :root {
    --bg:#f2f4fb; --card:#ffffff; --card2:#f6f7fd; --line:#dfe3f2;
    --txt:#1b1f33; --muted:#6b7191; --brand:#4f6bef; --brand2:#8250e6;
    --ok:#12a05f; --danger:#e04555; --warn:#c97a10;
    --input:#ffffff;
  }
  html[data-theme="dark"] {
    --bg:#0d1220; --card:#151b2d; --card2:#1b2338; --line:#303a58;
    --txt:#edf1ff; --muted:#a7afc9; --brand:#758cff; --brand2:#a06cff;
    --ok:#3ad28b; --danger:#ff6678; --warn:#f6b94b; --input:#11182a;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; min-height:100vh; font-family:Tahoma,"Segoe UI",system-ui,sans-serif;
    background:radial-gradient(1200px 600px at 80% -10%,#dfe5ff 0%,transparent 60%),
               radial-gradient(900px 500px at -10% 110%,#ece1ff 0%,transparent 55%),
               var(--bg);
    color:var(--txt);
  }
  .layout { display:flex; flex-direction:row-reverse; min-height:100vh; gap:0; }
  /* --- SIDEBAR (right, chon RTL) --- */
  .sidebar {
    width:430px; flex:0 0 auto; background:linear-gradient(180deg,var(--card),transparent);
    border-inline-start:1px solid var(--line); padding:14px 12px; overflow:hidden;
    display:flex; flex-direction:column; gap:9px;
  }
  .side-sec-title { font-size:12px; color:var(--muted); margin:4px 2px 2px;
    display:flex; align-items:center; justify-content:space-between; }
  /* kadr-e SABET-e profile-ha (scroll dakheli) */
  #prof-list { flex:1 1 auto; overflow-y:auto; overflow-x:hidden; min-height:200px;
    padding:4px 4px 4px 2px; border:1px solid var(--line); border-radius:14px;
    background:color-mix(in srgb,var(--card2) 55%,transparent); }
  #prof-list::-webkit-scrollbar { width:9px; }
  #prof-list::-webkit-scrollbar-thumb { background:color-mix(in srgb,var(--brand) 35%,var(--line)); border-radius:9px; }
  .prof-item {
    background:var(--card2); border:1px solid var(--line); border-radius:12px;
    padding:12px 13px; cursor:pointer; transition:.15s; margin-bottom:8px;
  }
  .prof-item:hover { border-color:var(--brand); }
  .prof-item.active { border-color:var(--brand); box-shadow:0 0 0 2px rgba(109,139,255,.25);
    background:linear-gradient(135deg,rgba(109,139,255,.12),rgba(157,109,255,.08)); }
  .prof-title { font-size:14px; font-weight:bold; display:flex; align-items:center; gap:6px; }
  .prof-title-text { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .mini-btn.profile-run-primary { flex:0 0 auto; min-width:112px; padding:9px 12px; border-radius:10px; font-size:13px; font-weight:800; white-space:nowrap; }
  .prof-success { color:var(--ok); font-size:11px; font-weight:bold; margin-top:5px; }
  .prof-blocked { color:#e53935; font-size:12px; font-weight:800; margin-top:6px; }
  .prof-blocked-detail { color:#c62828; font-size:11px; line-height:1.6; margin-top:3px; }
  .prof-meta { font-size:11px; color:var(--muted); margin-top:3px; direction:ltr; text-align:right; }
  .profile-number { display:inline-flex; min-width:24px; height:24px; align-items:center; justify-content:center; border-radius:8px; background:var(--brand); color:#fff; font-size:11px; }
  .artifact-links { display:flex; flex-wrap:wrap; gap:6px; margin-top:7px; }
  .artifact-links a { color:var(--ok); text-decoration:none; font-size:10.5px; }
  .prof-actions { display:flex; flex-wrap:wrap; gap:5px; margin-top:7px; }
  .mini-btn {
    flex:1 1 60px; font-size:10.5px; padding:6px 5px; border-radius:7px; cursor:pointer;
    border:1px solid var(--line); background:var(--input); color:var(--muted);
    font-family:inherit; transition:.15s;
  }
  .mini-btn:hover { color:var(--txt); border-color:var(--brand); }
  .mini-btn.on { color:var(--ok); border-color:var(--ok); }
  .mini-btn.start { color:#fff; border-color:transparent; background:linear-gradient(135deg,#12a05f,#18bd75); }
  .mini-btn.stop { color:#fff; border-color:transparent; background:linear-gradient(135deg,#d73e50,#f05d6c); }
  .mini-btn.blocked { color:#b71c1c; border-color:#ef9a9a; background:#ffebee; cursor:not-allowed; opacity:.75; }
  .mini-btn.del { color:#fff; border-color:transparent; background:linear-gradient(135deg,#d92f45,#f05b6b); }
  .mini-btn.del:hover { color:#fff; border-color:#ff98a3; filter:brightness(1.08); }
  /* --- dokme-haye ICON-i ba tooltip-e hover --- */
  .prof-actions { display:flex; gap:7px; flex-wrap:wrap; margin-top:9px; }
  .ibtn { position:relative; width:38px; height:38px; flex:0 0 auto; display:inline-flex;
    align-items:center; justify-content:center; font-size:17px; line-height:1; cursor:pointer;
    border:1px solid var(--line); border-radius:11px; background:var(--input); color:var(--muted);
    transition:.16s; }
  .ibtn:hover { color:var(--txt); border-color:var(--brand); transform:translateY(-2px);
    box-shadow:0 6px 16px rgba(16,24,40,.14); }
  .ibtn.on { color:var(--ok); border-color:var(--ok); background:color-mix(in srgb,var(--ok) 12%,var(--input)); }
  .ibtn.danger:hover { color:#fff; border-color:transparent; background:linear-gradient(135deg,#d92f45,#f05b6b); }
  .ibtn.warn:hover { color:#20160a; border-color:transparent; background:var(--warn,#f6b94b); }
  .ibtn::after { content:attr(data-tip); position:absolute; bottom:calc(100% + 7px); right:50%;
    transform:translate(50%,4px); white-space:nowrap; font-size:11px; font-weight:700;
    padding:5px 9px; border-radius:8px; background:#111827; color:#fff; opacity:0;
    pointer-events:none; transition:.16s; z-index:40; box-shadow:0 8px 20px rgba(0,0,0,.28); }
  .ibtn::before { content:''; position:absolute; bottom:calc(100% + 2px); right:50%;
    transform:translateX(50%); border:5px solid transparent; border-top-color:#111827;
    opacity:0; pointer-events:none; transition:.16s; z-index:40; }
  .ibtn:hover::after,.ibtn:focus-visible::after { opacity:1; transform:translate(50%,0); }
  .ibtn:hover::before,.ibtn:focus-visible::before { opacity:1; }
  /* token-e bot: hargez namayesh dade nemishavad — faghat vaziat */
  .token-badge { display:inline-block; margin:0 0 7px; padding:5px 10px; border-radius:99px;
    font-size:11.5px; font-weight:700; border:1px solid var(--line); }
  .token-badge.ok { color:var(--ok); border-color:color-mix(in srgb,var(--ok) 45%,var(--line));
    background:color-mix(in srgb,var(--ok) 10%,transparent); }
  .token-badge.warn { color:var(--warn); border-color:color-mix(in srgb,var(--warn) 45%,var(--line));
    background:color-mix(in srgb,var(--warn) 10%,transparent); }
  #tg_token { -webkit-text-security:disc; user-select:none; }
  /* --- notification-e log-e zende (bala-chap) --- */
  .lognotif-wrap { position:fixed; top:14px; left:14px; z-index:70; display:flex;
    flex-direction:column; gap:8px; width:min(360px,86vw); pointer-events:none; }
  .lognotif { pointer-events:auto; background:var(--card); border:1px solid var(--line);
    border-inline-start:4px solid var(--brand); border-radius:12px; padding:10px 32px 10px 12px;
    box-shadow:0 16px 40px rgba(16,24,40,.22); position:relative; opacity:0;
    transform:translateX(-14px); transition:.26s; }
  .lognotif.show { opacity:1; transform:translateX(0); }
  .lognotif.s-success,.lognotif.s-healthy { border-inline-start-color:var(--ok); }
  .lognotif.s-error,.lognotif.s-problem { border-inline-start-color:var(--danger); }
  .lognotif.s-warn,.lognotif.s-retry,.lognotif.s-advanced { border-inline-start-color:#e8a33d; }
  .lognotif .t { font-size:12.5px; font-weight:800; display:flex; gap:6px; align-items:center; }
  .lognotif .m { font-size:11.5px; color:var(--muted); margin-top:4px; line-height:1.6;
    max-height:54px; overflow:hidden; }
  .lognotif .x { position:absolute; top:5px; left:7px; border:0; background:transparent;
    color:var(--muted); font-size:15px; cursor:pointer; padding:1px 5px; }
  .lognotif .sn { font-size:10.5px; color:var(--brand); direction:ltr; margin-top:3px;
    font-family:Consolas,monospace; }
  .notif-toggle { position:fixed; top:14px; left:14px; z-index:69; font-size:11.5px;
    padding:6px 10px; border-radius:99px; border:1px solid var(--line); cursor:pointer;
    background:var(--card); color:var(--muted); }
  .add-prof {
    background:linear-gradient(135deg,var(--brand),var(--brand2)); border:1px solid transparent; color:#fff;
    padding:13px; border-radius:12px; cursor:pointer; font-size:16px; font-weight:800; font-family:inherit;
    transition:.15s; width:100%; box-shadow:0 8px 20px rgba(79,107,239,.24);
  }
  .add-prof:hover { filter:brightness(1.08); transform:translateY(-1px); }
  .profile-search-wrap { position:relative; }
  .profile-search-icon { position:absolute; right:13px; top:50%; transform:translateY(-50%); pointer-events:none; }
  #profile-search { width:100%; padding:11px 40px 11px 34px; border:1px solid var(--line); border-radius:12px; background:var(--input); color:var(--txt); font:13px inherit; outline:none; }
  #profile-search:focus { border-color:var(--brand); box-shadow:0 0 0 3px rgba(79,107,239,.16); }
  .search-clear { position:absolute; left:9px; top:50%; transform:translateY(-50%); border:0; background:transparent; color:var(--muted); font-size:19px; cursor:pointer; display:none; }
  .search-clear.visible { display:block; }
  .profiles-empty { padding:24px 12px; border:1px dashed var(--line); border-radius:12px; text-align:center; color:var(--muted); font-size:13px; }
  .nav-tabs { display:flex; gap:6px; margin-top:6px; }
  .nav-tab {
    flex:1; text-align:center; padding:9px; border-radius:10px; cursor:pointer;
    font-size:13px; border:1px solid var(--line); background:var(--input); color:var(--muted);
    transition:.15s;
  }
  .nav-tab.active { color:#fff; background:linear-gradient(135deg,var(--brand),var(--brand2)); border-color:transparent; }
  /* --- MAIN --- */
  .main { flex:1; padding:28px; display:flex; justify-content:center; align-items:flex-start; }
  .card {
    width:100%; max-width:1100px; background:linear-gradient(180deg,var(--card),var(--card2));
    border:1px solid var(--line); border-radius:20px; padding:26px; box-shadow:0 18px 44px rgba(27,31,51,.10);
  }
  .head { position:relative; display:flex; flex-direction:column; align-items:center; text-align:center; gap:8px; margin-bottom:18px; }
  .logo { width:42px; height:42px; border-radius:12px; flex:0 0 auto;
    background:linear-gradient(135deg,var(--brand),var(--brand2));
    display:flex; align-items:center; justify-content:center; font-size:22px; }
  h1 { font-size:19px; margin:0; }
  .panel-title { font-size:30px; font-weight:900; letter-spacing:-.5px; }
  .theme-btn { position:absolute; left:0; top:0; border:1px solid var(--line); background:var(--input); color:var(--txt); border-radius:10px; padding:8px 11px; cursor:pointer; }
  .active-profile-banner { min-width:min(520px,100%); padding:11px 18px; border:1px solid var(--brand); border-radius:14px; background:linear-gradient(135deg,rgba(79,107,239,.16),rgba(130,80,230,.13)); color:var(--txt); font-size:17px; font-weight:bold; box-shadow:0 8px 24px rgba(79,107,239,.12); }
  .sub { color:var(--muted); font-size:12.5px; margin:2px 0; }
  label { display:block; font-size:13px; color:var(--muted); margin:14px 0 6px; }
  input[type=text] { width:100%; padding:12px 14px; border-radius:12px; border:1px solid var(--line);
    background:var(--input); color:var(--txt); font-size:15px; outline:none; transition:.15s; font-family:inherit; }
  input[type=text]:focus { border-color:var(--brand); box-shadow:0 0 0 3px rgba(109,139,255,.18); }
  textarea { width:100%; min-height:90px; padding:12px 14px; border-radius:12px;
    border:1px solid var(--line); background:var(--input); color:var(--txt); font-family:inherit; }
  input.ltr { direction:ltr; text-align:left; letter-spacing:1px; }
  .imei-row { display:flex; gap:8px; align-items:center; margin-bottom:8px; }
  .imei-row input { flex:1; }
  .icon-btn { width:42px; height:44px; border-radius:12px; border:1px solid var(--line);
    background:var(--input); color:var(--muted); font-size:20px; cursor:pointer; flex:0 0 auto; transition:.15s; }
  .icon-btn:hover { color:var(--danger); border-color:var(--danger); }
  .add-btn { margin-top:4px; background:transparent; border:1px dashed var(--line); color:var(--brand);
    padding:9px 12px; border-radius:12px; cursor:pointer; font-size:13px; font-family:inherit; transition:.15s; }
  .add-btn:hover { border-color:var(--brand); background:rgba(109,139,255,.08); }
  .switches { margin-top:18px; display:flex; flex-direction:column; gap:10px; }
  .switch { display:flex; align-items:center; justify-content:space-between; gap:10px;
    background:var(--card2); border:1px solid var(--line); border-radius:12px; padding:11px 14px; }
  .switch .t { font-size:13.5px; }
  .switch .d { font-size:11.5px; color:var(--muted); margin-top:2px; }
  .toggle { position:relative; width:46px; height:26px; flex:0 0 auto; }
  .toggle input { display:none; }
  .track { position:absolute; inset:0; background:#c9cee4; border-radius:20px; cursor:pointer; transition:.2s; }
  .track:before { content:""; position:absolute; width:20px; height:20px; border-radius:50%;
    background:#fff; top:3px; right:3px; transition:.2s; }
  .toggle input:checked + .track { background:linear-gradient(135deg,var(--brand),var(--brand2)); }
  .toggle input:checked + .track:before { right:23px; }
  .toggle.danger input:checked + .track { background:linear-gradient(135deg,#ff6b7a,#ff9166); }
  .save { margin-top:22px; width:100%; padding:14px; border:none; border-radius:14px;
    background:linear-gradient(135deg,var(--brand),var(--brand2)); color:#fff; font-size:15.5px;
    font-weight:bold; cursor:pointer; font-family:inherit; transition:.15s; }
  .save:hover { filter:brightness(1.08); }
  .toast { margin-top:14px; text-align:center; font-size:13.5px; color:var(--ok); opacity:0; transition:.3s; min-height:18px; }
  .toast.show { opacity:1; }
  .api-health-toast { position:fixed; top:16px; left:50%; transform:translate(-50%,-18px);
    z-index:2147483000; min-width:min(520px,calc(100vw - 28px)); max-width:720px;
    border:1px solid var(--line); border-inline-start:5px solid var(--warn); border-radius:8px;
    padding:12px 46px 12px 16px; background:var(--card); color:var(--txt);
    box-shadow:0 14px 42px #0004; opacity:0; pointer-events:none; transition:.22s;
    font-size:13.5px; line-height:1.7; }
  .api-health-toast.show { opacity:1; transform:translate(-50%,0); pointer-events:auto; }
  .api-health-toast.ok { border-inline-start-color:var(--ok); }
  .api-health-toast.fail { border-inline-start-color:var(--danger); }
  .api-health-toast .api-meta { display:none; color:var(--muted); font-size:11px; direction:ltr; text-align:left; }
  .api-health-toast:hover .api-meta { display:block; }
  .api-health-toast button { position:absolute; top:7px; left:9px; border:0; background:transparent;
    color:var(--muted); font-size:22px; cursor:pointer; }
  .hint { font-size:11px; color:var(--muted); margin-top:4px; }
  /* --- LOGS --- */
  .log-item { background:var(--card2); border:1px solid var(--line); border-radius:12px;
    padding:11px 13px; margin-bottom:9px; border-inline-start:4px solid var(--muted); }
  .log-item.error { border-inline-start-color:var(--danger); }
  .log-item.success { border-inline-start-color:var(--ok); }
  .log-item.warn { border-inline-start-color:var(--warn); }
  .log-top { display:flex; justify-content:space-between; gap:8px; font-size:11.5px; color:var(--muted); }
  .log-msg { font-size:13px; margin-top:5px; line-height:1.6; }
  .log-serial { direction:ltr; text-align:right; font-size:12px; color:var(--brand); margin-top:5px; letter-spacing:1px; }
  .log-empty { color:var(--muted); text-align:center; padding:30px; font-size:13px; }
  .hidden { display:none !important; }
  .section-title { margin:18px 0 10px; padding:10px 12px; border-inline-start:4px solid var(--brand); background:rgba(79,107,239,.08); border-radius:10px; font-weight:bold; color:var(--brand); }
  .profile-tabs { display:flex; gap:8px; margin:10px 0 16px; }
  .profile-tab { flex:1; border:1px solid var(--line); background:var(--input); color:var(--muted); padding:10px; border-radius:10px; cursor:pointer; font-family:inherit; }
  .profile-tab.active { color:#fff; background:linear-gradient(135deg,var(--brand),var(--brand2)); border-color:transparent; }
  .exitban-result-card { border-radius:16px; padding:16px 18px; margin:14px 0; border:1px solid var(--line); background:var(--card2); }
  .exitban-result-card.not_checked { border-color:#c9cee4; background:#f5f6fb; color:var(--muted); }
  .exitban-result-card.clear { border-color:#9bd8b8; background:#eafaf1; color:#147a43; }
  .exitban-result-card.blocked { border-color:#f2a5ad; background:#fff0f2; color:#b42333; }
  .exitban-result-head { display:flex; justify-content:space-between; gap:12px; align-items:center; font-weight:bold; }
  .exitban-result-text { margin-top:10px; line-height:1.8; font-size:14px; }
  .exitban-result-time { margin-top:8px; font-size:11px; opacity:.75; direction:ltr; text-align:right; }
  .settings-section,.inquiry-card { background:var(--card2);border:1px solid var(--line);border-radius:16px;padding:18px;margin:14px 0; }
  .settings-section h3,.inquiry-card h3 { margin:0 0 8px; }
  .soon { display:inline-block;color:var(--warn);background:#fff4df;border-radius:20px;padding:5px 10px;font-size:11px; }
  /* --- back-to-home button --- */
  .home-btn { position:absolute; right:0; top:0; border:1px solid var(--line); background:var(--input); color:var(--txt);
    border-radius:10px; padding:8px 12px; cursor:pointer; text-decoration:none; font-size:13px; font-family:inherit;
    transition:.15s; display:inline-flex; align-items:center; gap:6px; }
  .home-btn:hover { border-color:var(--brand); color:var(--brand); }
  /* --- stats bar --- */
  .stats { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin:2px 0 18px; }
  .stat { text-align:center; padding:13px 8px; border:1px solid var(--line); border-radius:14px; background:var(--card2);
    cursor:pointer; transition:.15s; user-select:none; }
  .stat:hover { border-color:var(--brand); transform:translateY(-2px); }
  .stat.active { border-color:var(--brand); box-shadow:0 0 0 2px rgba(109,139,255,.25);
    background:linear-gradient(135deg,rgba(109,139,255,.14),rgba(157,109,255,.08)); }
  .stat .n { font-size:25px; font-weight:900; line-height:1.1; }
  .stat .l { font-size:11.5px; color:var(--muted); margin-top:3px; }
  .stat.s-total .n{color:var(--brand)} .stat.s-running .n{color:var(--warn)} .stat.s-ready .n{color:var(--brand)}
  .stat.s-success .n{color:var(--ok)} .stat.s-blocked .n{color:var(--danger)}
  /* --- profile status badge (parity with site 2) --- */
  .p-badge { display:inline-flex; align-items:center; gap:5px; font-size:10.5px; font-weight:800; padding:3px 9px;
    border-radius:999px; border:1px solid transparent; white-space:nowrap; }
  .p-badge .d { width:6px; height:6px; border-radius:50%; background:currentColor; }
  .p-badge.b-ready{color:var(--brand); background:rgba(79,107,239,.12); border-color:rgba(79,107,239,.3)}
  .p-badge.b-busy{color:var(--warn); background:rgba(201,122,16,.14); border-color:rgba(201,122,16,.35)}
  .p-badge.b-success{color:var(--ok); background:rgba(18,160,95,.12); border-color:rgba(18,160,95,.3)}
  .p-badge.b-blocked{color:var(--danger); background:rgba(224,69,85,.12); border-color:rgba(224,69,85,.3)}
  .p-badge.b-off{color:var(--muted); background:rgba(107,113,145,.1); border-color:rgba(107,113,145,.25)}
  .b-busy .d{animation:pulse-dot 1s steps(2) infinite}
  @keyframes pulse-dot{50%{opacity:.3}}
  /* Premium product UI layer */
  :root {
    --bg:#f6f7f9; --card:#ffffff; --card2:#f8f9fb; --line:#e4e7ec;
    --txt:#17191f; --muted:#717784; --brand:#5b5bd6; --brand2:#5b5bd6;
    --ok:#16875f; --danger:#d14343; --warn:#ad6800; --input:#ffffff;
    --shadow:0 1px 2px rgba(16,24,40,.03),0 12px 32px rgba(16,24,40,.06);
  }
  html[data-theme="dark"] {
    --bg:#0b0c0f; --card:#111216; --card2:#16181d; --line:#272a31;
    --txt:#f3f4f6; --muted:#9298a5; --brand:#7c7cf2; --brand2:#7c7cf2;
    --ok:#34b583; --danger:#ef6a6a; --warn:#e4a43a; --input:#13151a;
    --shadow:0 1px 1px rgba(0,0,0,.2),0 18px 42px rgba(0,0,0,.22);
  }
  html { scrollbar-gutter:stable; }
  body { font-family:"Segoe UI",Tahoma,system-ui,sans-serif; background:var(--bg); font-size:14px; line-height:1.65; }
  button,input,textarea { font:inherit; }
  button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible { outline:2px solid var(--brand); outline-offset:2px; }
  .layout { min-height:100vh; background:var(--bg); }
  .sidebar { width:430px; padding:24px 18px; gap:12px; background:var(--card); border-inline-start:1px solid var(--line); overflow:hidden; }
  .side-sec-title { margin:0; font-size:12px; font-weight:700; letter-spacing:.01em; color:var(--muted); }
  .add-prof { padding:11px 14px; border-radius:9px; font-size:14px; font-weight:700; background:var(--txt); box-shadow:none; }
  html[data-theme="dark"] .add-prof { color:#111216; background:#f3f4f6; }
  .add-prof:hover { filter:none; transform:none; opacity:.88; }
  #profile-search { min-height:42px; padding:10px 38px 10px 32px; border-radius:9px; background:var(--card2); }
  #profile-search:focus { box-shadow:0 0 0 3px color-mix(in srgb,var(--brand) 16%,transparent); }
  .prof-item { padding:12px; margin-bottom:8px; border-radius:10px; background:var(--card); box-shadow:0 1px 1px rgba(16,24,40,.02); }
  .prof-item:hover { border-color:#c9cdd5; background:var(--card2); }
  html[data-theme="dark"] .prof-item:hover { border-color:#3a3e47; }
  .prof-item.active { border-color:var(--brand); background:color-mix(in srgb,var(--brand) 7%,var(--card)); box-shadow:0 0 0 2px color-mix(in srgb,var(--brand) 14%,transparent); }
  .prof-title { gap:7px; font-size:13px; }
  .profile-number { min-width:22px; height:22px; border-radius:6px; background:var(--card2); border:1px solid var(--line); color:var(--muted); }
  .prof-meta { margin-top:5px; font-size:10.5px; }
  #prof-list { flex:1 1 auto; overflow-y:auto; overflow-x:hidden; min-height:200px;
    padding:4px; border:1px solid var(--line); border-radius:14px; background:var(--card2); }
  .prof-actions { display:flex; flex-wrap:wrap; gap:7px; margin-top:9px; }
  .mini-btn { min-height:30px; border-radius:7px; font-size:10.5px; background:var(--card); }
  .mini-btn.profile-run-primary { min-width:100px; padding:7px 10px; border-radius:7px; font-size:11.5px; }
  .mini-btn.start { background:var(--ok); }
  .mini-btn.stop,.mini-btn.del { background:var(--danger); }
  .mini-btn.blocked { color:var(--danger); border-color:color-mix(in srgb,var(--danger) 35%,var(--line)); background:color-mix(in srgb,var(--danger) 8%,var(--card)); }
  .main { padding:32px clamp(24px,3vw,48px); }
  .card { max-width:1180px; padding:0; border:0; border-radius:0; background:transparent; box-shadow:none; }
  .head { display:grid; grid-template-columns:1fr auto; align-items:center; gap:18px; margin:0 0 24px; text-align:right; }
  .head-actions { grid-column:2; grid-row:1; display:flex; align-items:center; gap:8px; }
  .product-lockup { grid-column:1; grid-row:1; display:flex; align-items:center; gap:12px; }
  .logo { width:42px; height:42px; border-radius:11px; color:#fff; background:var(--brand); box-shadow:none; }
  .eyebrow { color:var(--muted); font-size:11px; font-weight:600; margin-bottom:1px; }
  .panel-title { font-size:24px; font-weight:700; letter-spacing:-.45px; }
  .home-btn,.theme-btn { position:static; min-height:38px; display:inline-flex; align-items:center; gap:6px; padding:8px 11px; border-radius:8px; font-size:12px; background:var(--card); box-shadow:0 1px 2px rgba(16,24,40,.03); }
  .active-profile-banner { grid-column:1/-1; min-width:0; width:100%; padding:11px 14px; border:1px solid var(--line); border-radius:9px; background:var(--card); color:var(--txt); font-size:13px; font-weight:600; box-shadow:none; text-align:right; }
  .active-profile-banner:before { content:""; display:inline-block; width:7px; height:7px; margin-left:8px; border-radius:50%; background:var(--ok); }
  .stats { gap:8px; margin:0 0 14px; }
  .stat { min-height:78px; display:flex; align-items:center; justify-content:center; gap:3px; padding:12px 8px; border-radius:10px; background:var(--card); box-shadow:0 1px 2px rgba(16,24,40,.025); }
  .stat:hover { transform:none; border-color:#c9cdd5; }
  .stat.active { border-color:var(--brand); background:color-mix(in srgb,var(--brand) 6%,var(--card)); box-shadow:0 0 0 2px color-mix(in srgb,var(--brand) 12%,transparent); }
  .stat .n { font-size:22px; font-weight:700; }
  .stat .l { font-size:11px; }
  .nav-tabs { gap:4px; margin:0 0 18px; padding:4px; border:1px solid var(--line); border-radius:10px; background:var(--card); }
  .nav-tab { min-height:38px; display:flex; align-items:center; justify-content:center; padding:8px 10px; border:0; border-radius:7px; background:transparent; font-size:12.5px; }
  .nav-tab:hover { color:var(--txt); background:var(--card2); }
  .nav-tab.active { color:var(--txt); background:var(--card2); box-shadow:0 1px 2px rgba(16,24,40,.06); }
  #view-form,#view-logs,#view-inquiries,#view-telegram { padding:24px; border:1px solid var(--line); border-radius:12px; background:var(--card); box-shadow:var(--shadow); }
  .profile-editor-empty { min-height:360px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; padding:40px; text-align:center; color:var(--muted); }
  .profile-editor-empty .empty-icon { width:46px; height:46px; display:grid; place-items:center; margin-bottom:4px; border:1px solid var(--line); border-radius:12px; background:var(--card2); color:var(--brand); font-size:22px; }
  .profile-editor-empty strong { color:var(--txt); font-size:16px; font-weight:650; }
  .profile-editor-empty p { max-width:420px; margin:0; font-size:12.5px; line-height:1.8; }
  .section-title { margin:0 0 14px; padding:0; border:0; background:none; color:var(--txt); font-size:17px; }
  .profile-tabs { max-width:520px; gap:4px; margin:0 0 22px; padding:4px; border-radius:9px; background:var(--card2); }
  .profile-tab { padding:8px 12px; border:0; border-radius:7px; background:transparent; font-size:12px; }
  .profile-tab.active { color:var(--txt); background:var(--card); box-shadow:0 1px 3px rgba(16,24,40,.08); }
  #view-base,#view-identity { display:block; max-width:920px; }
  #view-base > .section-title,#view-base > .profile-tabs,#view-base > #imeis,#view-base > .add-btn,#view-base > .switches,#view-identity > .section-title { width:100%; }
  label { margin:10px 0 5px; font-size:12px; font-weight:600; }
  input[type=text],textarea { padding:10px 12px; border-radius:8px; font-size:13px; background:var(--input); }
  input[type=text]:focus,textarea:focus { box-shadow:0 0 0 3px color-mix(in srgb,var(--brand) 15%,transparent); }
  .imei-row { margin-bottom:7px; }
  .icon-btn { width:40px; height:40px; border-radius:8px; font-size:18px; }
  .add-btn { width:max-content; padding:8px 12px; border-radius:8px; }
  .switches { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .switch { min-height:70px; padding:11px 12px; border-radius:9px; background:var(--card2); }
  .switch .t { font-size:12.5px; font-weight:600; }
  .switch .d { font-size:10.5px; }
  .save { width:auto; min-width:190px; margin:20px 0 0; padding:11px 18px; border-radius:8px; background:var(--brand); font-size:13px; }
  .save:hover { filter:none; opacity:.9; }
  .log-item,.settings-section,.inquiry-card,.exitban-result-card { border-radius:9px; background:var(--card2); }
  .api-health-toast { border-radius:10px; box-shadow:0 18px 50px rgba(16,24,40,.16); }
  .p-badge { font-size:10px; padding:3px 8px; font-weight:600; }
  @media(max-width:1100px){
    .sidebar{width:360px}.main{padding:24px}.switches{grid-template-columns:1fr}
  }
  @media(max-width:900px){
    .layout{display:block}.sidebar{width:auto;max-height:none;border-inline-start:0;border-bottom:1px solid var(--line);overflow:visible}
    #prof-list{max-height:60vh}
    .main{padding:20px 16px}.head{grid-template-columns:1fr}.product-lockup{grid-column:1}.head-actions{grid-column:1;grid-row:2;justify-content:flex-start}
    .active-profile-banner{grid-row:3}.stats{grid-template-columns:repeat(3,1fr)}
  }
  @media(max-width:640px){
    .panel-title{font-size:20px}.stats{grid-template-columns:repeat(2,1fr)}.stats .stat:first-child{grid-column:1/-1}
    #view-form,#view-logs,#view-inquiries,#view-telegram{padding:16px}.nav-tabs{overflow-x:auto;flex-wrap:nowrap}.nav-tab{min-width:120px}
    #view-base,#view-identity{grid-template-columns:1fr}.switches{grid-template-columns:1fr}.home-btn span{display:none}
  }
</style>
</head>
<body>
<div id="api-health-toast" class="api-health-toast" role="status" aria-live="polite">
  <button type="button" aria-label="بستن" onclick="this.parentElement.classList.remove('show')">×</button>
  <div id="api-health-message">در حال بررسی ارتباط API تلگرام…</div>
  <div id="api-health-meta" class="api-meta"></div>
</div>
<div class="layout">
  <div class="main">
    <div class="card">
      <header class="head">
        <div class="head-actions">
          <a href="/" class="home-btn" title="بازگشت به انتخاب سامانه" aria-label="بازگشت به انتخاب سامانه">⌂ <span>سامانه‌ها</span></a>
          <button type="button" id="theme-toggle" class="theme-btn" onclick="toggleTheme()">حالت تاریک</button>
        </div>
        <div class="product-lockup">
          <div class="logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 19h16M6 16V8m4 8V8m4 8V8m4 8V8M4 8h16L12 3 4 8Z"/></svg>
          </div>
          <div>
            <div class="eyebrow">مرکز عملیات گمرک</div>
            <h1 class="panel-title">داشبورد مدیریت پرونده‌ها</h1>
          </div>
        </div>
        <div class="active-profile-banner" id="subtitle">پروفایل فعال: در حال بارگذاری...</div>
      </header>
      <div class="stats" id="stats" aria-label="خلاصه وضعیت پروفایل‌ها">
        <div class="stat s-total"   data-filter="all"     title="نمایش همه"><div class="n" id="st-total">۰</div><div class="l">کل</div></div>
        <div class="stat s-running" data-filter="running" title="فیلتر در حال اجرا"><div class="n" id="st-running">۰</div><div class="l">در حال اجرا</div></div>
        <div class="stat s-ready"   data-filter="ready"   title="فیلتر آماده اجرا"><div class="n" id="st-ready">۰</div><div class="l">آماده</div></div>
        <div class="stat s-success" data-filter="success" title="فیلتر ثبت موفق"><div class="n" id="st-success">۰</div><div class="l">موفق</div></div>
        <div class="stat s-blocked" data-filter="blocked" title="فیلتر مسدود"><div class="n" id="st-blocked">۰</div><div class="l">مسدود</div></div>
      </div>
      <div class="nav-tabs">
        <div class="nav-tab active" id="tab-form" onclick="showView('form')">✏️ ویرایش پروفایل</div>
        <div class="nav-tab" id="tab-logs" onclick="showView('logs')">📋 لاگ‌ها</div>
        <div class="nav-tab" id="tab-inquiries" onclick="showView('inquiries')">🔎 استعلامات</div>
        <div class="nav-tab" id="tab-telegram" onclick="showView('telegram')">⚙️ تنظیمات کلی</div>
      </div>
      <div id="view-form"></div>
      <div id="view-logs" class="hidden"></div>
      <div id="view-inquiries" class="hidden"></div>
      <div id="view-telegram" class="hidden"></div>
    </div>
  </div>
  <div class="sidebar">
    <div class="side-sec-title"><span>پروفایل‌ها</span><span id="profile-count"></span></div>
    <button class="add-prof" onclick="newProfile()">+ پروفایل جدید</button>
    <div class="profile-search-wrap">
      <span class="profile-search-icon">🔎</span>
      <input id="profile-search" type="search" autocomplete="off" placeholder="جستجو: نام، کد ملی، موبایل یا سریال" oninput="filterProfiles(this.value)">
      <button type="button" id="profile-search-clear" class="search-clear" aria-label="پاک کردن جستجو" onclick="clearProfileSearch()">×</button>
    </div>
    <div id="prof-list"></div>
  </div>
</div>
<div id="lognotif-wrap" class="lognotif-wrap" aria-live="polite"></div>
<button type="button" id="notif-toggle" class="notif-toggle" onclick="toggleLogNotif()"></button>
<script>
"""

PAGE_JS = r"""
var STORE = __STORE_JSON__;
var LOGS = __LOGS_JSON__;
var TELEGRAM = __TELEGRAM_JSON__;
var activeId = STORE.active_id;
var logMode='profile';
var statusFilter='all';
var FA_DIGITS='۰۱۲۳۴۵۶۷۸۹';
function toFa(n){return String(n).replace(/\d/g,function(d){return FA_DIGITS[d];});}
function statusOf(p){
  if(p.run_status==='busy')             return {key:'running',cls:'b-busy',   label:'در حال اجرا'};
  if(p.registration_status==='success') return {key:'success',cls:'b-success',label:'موفق'};
  if(p.registration_status==='blocked') return {key:'blocked',cls:'b-blocked',label:'مسدود'};
  if(!p.enabled)                        return {key:'off',    cls:'b-off',    label:'غیرفعال'};
  return {key:'ready',cls:'b-ready',label:'آماده'};
}
function setStatusFilter(f){
  statusFilter=(statusFilter===f&&f!=='all')?'all':f;
  renderProfiles();
}
var savedTheme=localStorage.getItem('irica-theme')||'light';
document.documentElement.setAttribute('data-theme',savedTheme);
function toggleTheme(){
  var dark=document.documentElement.getAttribute('data-theme')==='dark';
  var next=dark?'light':'dark'; document.documentElement.setAttribute('data-theme',next);
  localStorage.setItem('irica-theme',next); updateThemeButton();
}
function updateThemeButton(){
  var btn=document.getElementById('theme-toggle'); if(!btn)return;
  btn.textContent=document.documentElement.getAttribute('data-theme')==='dark'?'☀️ حالت روشن':'🌙 حالت تاریک';
}
setInterval(function(){fetch('/state').then(function(r){return r.json();}).then(function(s){STORE=s;renderProfiles();});},3000);

function getProf(id){ return STORE.profiles.find(function(p){return p.id===id;}); }

function profileRunButton(p,extraClass){
  var cls='mini-btn '+(extraClass||'');
  if(p.registration_status==='blocked') return '<button class="'+cls+' blocked" disabled>⛔ غیرقابل ورود</button>';
  if(p.run_status==='busy') return '<button class="'+cls+' stop" onclick="stopProfile(\''+p.id+'\','+p.runner_pid+')">⏹ توقف ربات</button>';
  if(p.registration_status!=='success') return '<button class="'+cls+' start" onclick="startProfile(\''+p.id+'\')">▶ شروع به کار</button>';
  return '';
}

function normalizeProfileSearch(value){
  var fa='۰۱۲۳۴۵۶۷۸۹', ar='٠١٢٣٤٥٦٧٨٩';
  return String(value||'').trim().toLowerCase().replace(/[۰-۹]/g,function(c){return fa.indexOf(c);}).replace(/[٠-٩]/g,function(c){return ar.indexOf(c);});
}

function filterProfiles(value){
  var input=document.getElementById('profile-search');
  if(input && input.value!==value) input.value=value;
  var clear=document.getElementById('profile-search-clear'); if(clear)clear.classList.toggle('visible',Boolean(value));
  renderProfiles();
}

function clearProfileSearch(){
  var input=document.getElementById('profile-search'); if(input)input.value='';
  filterProfiles(''); if(input)input.focus();
}

function profileMatchesSearch(p,query){
  if(!query)return true;
  var fields=[p.title,p.national,p.mobile].concat(p.imeis||[]);
  return fields.some(function(value){return normalizeProfileSearch(value).indexOf(query)!==-1;});
}

function renderProfiles(){
  var el=document.getElementById('prof-list'); el.innerHTML='';
  var input=document.getElementById('profile-search');
  var query=normalizeProfileSearch(input?input.value:'');
  var all=STORE.profiles||[];
  // ---- stats ----
  var counts={all:all.length,running:0,ready:0,success:0,blocked:0,off:0};
  all.forEach(function(p){counts[statusOf(p).key]++;});
  [['st-total',counts.all],['st-running',counts.running],['st-ready',counts.ready],['st-success',counts.success],['st-blocked',counts.blocked]].forEach(function(pair){
    var node=document.getElementById(pair[0]); if(node)node.textContent=toFa(pair[1]);
  });
  document.querySelectorAll('#stats .stat').forEach(function(s){s.classList.toggle('active',s.getAttribute('data-filter')===statusFilter);});
  // ---- filter (status + search) ----
  var profiles=STORE.profiles.slice().reverse().filter(function(p){
    if(statusFilter!=='all' && statusOf(p).key!==statusFilter) return false;
    return profileMatchesSearch(p,query);
  });
  var count=document.getElementById('profile-count'); if(count)count.textContent=(profiles.length!==all.length)?(toFa(profiles.length)+' از '+toFa(all.length)):(toFa(all.length)+' مورد');
  if(!profiles.length){el.innerHTML='<div class="profiles-empty">پروفایلی با این مشخصات پیدا نشد.</div>';return;}
  profiles.forEach(function(p){
    var isActive=(p.id===activeId);
    var profileNumber=STORE.profiles.indexOf(p)+1;
    var st=statusOf(p);
    var d=document.createElement('div');
    d.className='prof-item'+(isActive?' active':'');
    d.setAttribute('data-profile-id',p.id);
    d.innerHTML='<div class="prof-title"><span class="profile-number">'+profileNumber+'</span>'+(isActive?'<span style="color:var(--ok)">●</span> ':'')+
      '<span class="prof-title-text">'+escHtml(p.title||p.national||'بدون عنوان')+'</span>'+
      '<span class="p-badge '+st.cls+'"><span class="d"></span>'+st.label+'</span>'+profileRunButton(p,'profile-run-primary')+'</div>'+
      '<div class="prof-meta">'+escHtml(p.national||'—')+(p.mobile?' • '+escHtml(p.mobile):'')+'</div>'+
      (p.registration_status==='blocked'?'<div class="prof-blocked">⛔ غیرقابل ورود — تلاش متوقف شد</div><div class="prof-blocked-detail">'+escHtml(p.runtime_error||'خطای غیرقابل ورود ثبت شده است')+'</div>':'')+
      (p.registration_status==='success'?'<div class="prof-success">✓ موفق — مرحله پرداخت</div>':'')+
      '<div class="artifact-links">'+
      (p.receipt_screenshot?'<a target="_blank" href="/artifact?id='+encodeURIComponent(p.id)+'&kind=receipt">🧾 مشاهده رسید</a>':'')+
      (p.payment_screenshot?'<a target="_blank" href="/artifact?id='+encodeURIComponent(p.id)+'&kind=payment">💳 مشاهده پرداخت</a>':'')+
      (p.payment_link?'<a target="_blank" href="'+escHtml(p.payment_link)+'">🔗 بازکردن لینک پرداخت</a>':'')+
      '</div>'+
      '<div class="prof-actions">'+
        '<button class="ibtn'+(p.enabled?' on':'')+'" data-tip="'+(p.enabled?'فعال است — کلیک: غیرفعال کردن':'غیرفعال است — کلیک: فعال کردن')+'" '+
          'onclick="toggleProfile(\''+p.id+'\','+(!p.enabled)+')">'+(p.enabled?'🟢':'⚪')+'</button>'+
        '<button class="ibtn'+(isActive?' on':'')+'" data-tip="'+(isActive?'پروفایل فعال جاری':'انتخاب به‌عنوان پروفایل فعال')+'" '+
          'onclick="activateProfile(\''+p.id+'\')">'+(isActive?'📌':'🎯')+'</button>'+
        '<button class="ibtn" data-tip="ویرایش اطلاعات پروفایل" onclick="editProfile(\''+p.id+'\')">✏️</button>'+
        '<button class="ibtn" data-tip="کپی گرفتن از این پروفایل" onclick="cloneProfile(\''+p.id+'\')">📋</button>'+
        '<button class="ibtn danger" data-tip="حذف کامل این پروفایل" onclick="deleteProfile(\''+p.id+'\')">🗑</button>'+
      '</div>';
    el.appendChild(d);
  });
}

function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function buildExitbanResult(p){
  var status=p.exitban_status||'not_checked';
  var titles={not_checked:'هنوز استعلامی انجام نشده است',clear:'ممنوع‌الخروج نیست',blocked:'ممنوع‌الخروج است'};
  var icons={not_checked:'○',clear:'✓',blocked:'!'};
  var text=p.exitban_result||'پس از انجام استعلام، نتیجه رسمی در این کادر نمایش داده می‌شود.';
  return '<div id="exitban-result-card" class="exitban-result-card '+status+'">'+
    '<div class="exitban-result-head"><span>'+icons[status]+' '+titles[status]+'</span></div>'+
    '<div class="exitban-result-text">'+escHtml(text)+'</div>'+
    (p.exitban_checked_at?'<div class="exitban-result-time">'+escHtml(p.exitban_checked_at)+'</div>':'')+'</div>';
}

function renderProfileEditorEmpty(){
  var el=document.getElementById('view-form'); if(!el)return;
  el.innerHTML='<div class="profile-editor-empty"><div class="empty-icon" aria-hidden="true">✎</div><strong>پروفایلی برای ویرایش انتخاب نشده</strong><p>برای ویرایش، یک پروفایل را از منوی سمت راست انتخاب کنید.</p></div>';
  var subtitle=document.getElementById('subtitle');
  if(subtitle)subtitle.textContent='برای شروع، یک پروفایل را انتخاب کنید';
}

function buildForm(p){
  var imeis=(p.imeis&&p.imeis.length)?p.imeis:[''];
  return '<form id="f" onsubmit="saveProfile(event)">'+
    '<div class="section-title">اطلاعات پایه</div>'+
    '<div class="profile-tabs">'+
      '<button type="button" aria-label="تب اطلاعات پایه" class="profile-tab active" onclick="showProfileSection(\'base\')">اطلاعات پایه</button>'+ 
      '<button type="button" aria-label="تب اطلاعات هویتی" class="profile-tab" onclick="showProfileSection(\'identity\')">اطلاعات هویتی</button>'+ 
      '<button type="button" aria-label="تب استعلامات" class="profile-tab" onclick="showProfileSection(\'inquiries\')">استعلامات</button>'+ 
    '</div>'+ 
    '<div id="view-base">'+
    '<input type="text" name="title" value="'+escHtml(p.title||'')+'" placeholder="مثلاً علی رضایی">'+
    '<label>کد ملی</label>'+
    '<input type="text" name="national" class="ltr" value="'+escHtml(p.national||'')+'" placeholder="۱۰ رقم" oninput="syncSharedField(\'identity_national\',this.value)">'+
    '<label>تاریخ تولد (شمسی)</label>'+
    '<input type="text" name="birth" class="ltr" value="'+escHtml(p.birth||'')+'" placeholder="۱۳۶۶/۱۰/۱" oninput="syncSharedField(\'identity_birth\',this.value)">'+
    '<label>شماره موبایل</label>'+
    '<input type="text" name="mobile" class="ltr" value="'+escHtml(p.mobile||'')+'" placeholder="۰۹۱۴...">'+
    '<label>کدهای IMEI</label>'+
    '<div id="imeis"></div>'+
    '<button type="button" class="add-btn" onclick="addImei(\'\')">+ افزودن IMEI</button>'+ 
    '<div class="switches">'+
      '<div class="switch"><div><div class="t">زدن تیک «قبول دارم»</div><div class="d">اگر خاموش باشد تیک زده نمی‌شود</div></div>'+
        '<label class="toggle"><input type="checkbox" id="confirm_on"'+(p.no_confirm_check?'':' checked')+'><span class="track"></span></label></div>'+
      '<div class="switch"><div><div class="t">submit خودکار (زدنِ «ادامه»)</div><div class="d">با احتیاط — فرم واقعاً ارسال می‌شود</div></div>'+
        '<label class="toggle danger"><input type="checkbox" id="submit_on"'+(p.submit?' checked':'')+'><span class="track"></span></label></div>'+
      '<div class="switch"><div><div class="t">🐞 حالت دیباگ</div><div class="d">قبل از هر مرحله مکث می‌کند</div></div>'+
        '<label class="toggle"><input type="checkbox" id="debug_on"'+(p.debugging?' checked':'')+'><span class="track"></span></label></div>'+
      '<div class="switch"><div><div class="t">✋ مدیریت دستی</div><div class="d">صفحه اول باز می‌شود؛ خودت تا کد ۴رقمی برو، تیک بزن، دکمه بالای صفحه را بزن → brute-force</div></div>'+
        '<label class="toggle"><input type="checkbox" id="manual_on"'+(p.manual_manage?' checked':'')+'><span class="track"></span></label></div>'+
    '</div>'+ 
    '</div>'+ 
    '<div id="view-identity" class="hidden">'+
    '<div class="section-title">اطلاعات هویتی</div>'+
    '<label>نام</label><input type="text" name="first_name" value="'+escHtml(p.first_name||'')+'">'+
    '<label>نام خانوادگی</label><input type="text" name="last_name" value="'+escHtml(p.last_name||'')+'">'+
    '<label>نام پدر</label><input type="text" name="father_name" value="'+escHtml(p.father_name||'')+'">'+
    '<label>تاریخ تولد</label><input type="text" name="identity_birth" class="ltr" value="'+escHtml(p.birth||'')+'" oninput="syncSharedField(\'birth\',this.value)">'+
    '<label>شماره شناسنامه</label><input type="text" name="birth_certificate_no" class="ltr" value="'+escHtml(p.birth_certificate_no||'')+'">'+
    '<label>کد ملی</label><input type="text" name="identity_national" class="ltr" value="'+escHtml(p.national||'')+'" oninput="syncSharedField(\'national\',this.value)">'+
    '</div>'+ 
    '<div id="view-profile-inquiries" class="hidden">'+
      '<div class="inquiry-card"><h3>استعلام ممنوع‌الخروجی</h3>'+buildExitbanResult(p)+'<button type="button" class="save" onclick="runExitbanInquiry(activeId)">استعلام آنلاین خروج از کشور</button><div id="exitban-status" class="hint"></div></div>'+ 
      '<div class="inquiry-card"><h3>استعلام اقساط و وام پرداخت‌نشده</h3><span class="soon">به‌زودی</span></div>'+ 
    '</div>'+ 
    '<button type="submit" class="save">💾 ذخیره پروفایل</button>'+
    '<div class="toast" id="toast"></div>'+
  '</form>';
}

function addImei(val){
  var wrap=document.getElementById('imeis');
  var row=document.createElement('div'); row.className='imei-row';
  row.innerHTML='<input type="text" class="ltr" placeholder="۱۵ رقم" value="'+escHtml(val||'')+'">'+
    '<button type="button" class="icon-btn" title="حذف">×</button>';
  row.querySelector('.icon-btn').onclick=function(){row.remove();};
  wrap.appendChild(row);
}

function showProfileSection(section){
  var target = section==='inquiries' ? 'profile-inquiries' : section;
  ['base','identity','profile-inquiries'].forEach(function(name){
    var el=document.getElementById('view-'+name); if(el) el.classList.toggle('hidden',name!==target);
  });
  document.querySelectorAll('.profile-tab').forEach(function(btn){btn.classList.toggle('active',btn.getAttribute('aria-label') === 'تب '+(section==='base'?'اطلاعات پایه':section==='identity'?'اطلاعات هویتی':'استعلامات'));});
}

function syncSharedField(name,value){
  var f=document.getElementById('f'); if(f&&f.elements[name]) f.elements[name].value=value;
}

function setActiveBanner(p,prefix){
  var el=document.getElementById('subtitle'); if(!el||!p)return;
  el.textContent=(prefix||'پروفایل فعال')+': '+(p.title||p.national||'بدون عنوان');
}

function editProfile(id){
  activeId=id;
  var p=getProf(id)||{};
  document.getElementById('view-form').innerHTML=buildForm(p);
  setActiveBanner(p,'ویرایش پروفایل');
  (p.imeis&&p.imeis.length?p.imeis:['']).forEach(addImei);
  renderProfiles();
  showView('form');
}

function activateProfile(id){
  fetch('/activate',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})})
  .then(function(r){return r.json();})
  .then(function(res){
    STORE=res; activeId=res.active_id;
    renderProfiles();
    var p=getProf(activeId);
    if(p) setActiveBanner(p);
  });
}

function startProfile(id){
  fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
    .then(function(r){return r.json().then(function(data){return {ok:r.ok,data:data};});})
    .then(function(res){
      if(!res.ok){alert((res.data.error||'خطا در شروع ربات')+(res.data.missing?' — '+res.data.missing.join('، '):''));return;}
      STORE=res.data;renderProfiles();
    }).catch(function(){alert('ارتباط با پنل برای شروع ربات برقرار نشد.');});
}

function stopProfile(id,pid){
  fetch('/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,pid:pid})})
    .then(function(r){return r.json().then(function(data){return {ok:r.ok,data:data};});})
    .then(function(res){if(!res.ok){alert(res.data.error||'توقف ربات انجام نشد');return;}STORE=res.data;renderProfiles();})
    .catch(function(){alert('ارتباط با پنل برای توقف ربات برقرار نشد.');});
}

function toggleProfile(id,enabled){
  fetch('/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,enabled:enabled})})
  .then(function(r){return r.json();}).then(function(res){STORE=res;renderProfiles();});
}

function deleteProfile(id){
  if(STORE.profiles.length<=1){alert('حداقل یک پروفایل باید باشد.');return;}
  if(!confirm('حذف شود؟')) return;
  fetch('/delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})})
  .then(function(r){return r.json();})
  .then(function(res){
    STORE=res; activeId=res.active_id;
    renderProfiles();
    if(id===activeId||!getProf(activeId)) editProfile(res.active_id);
  });
}

function newProfile(){
  fetch('/new',{method:'POST'})
  .then(function(r){return r.json();})
  .then(function(res){
    STORE=res; activeId=res.active_id;
    renderProfiles();
    editProfile(res.active_id);
  });
}

function cloneProfile(id){
  fetch('/clone',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
  .then(function(r){return r.json();}).then(function(res){
    STORE=res; activeId=res.active_id; renderProfiles(); editProfile(activeId);
  });
}

function saveProfile(e){
  e.preventDefault();
  var f=document.getElementById('f');
  var imeis=[];
  document.querySelectorAll('#imeis input').forEach(function(i){if(i.value.trim())imeis.push(i.value.trim());});
  var p=getProf(activeId)||{};
  var payload={
    id:activeId,
    title:f.title.value.trim(),
    national:f.national.value.trim(),
    birth:f.birth.value.trim(),
    mobile:f.mobile.value.trim(),
    first_name:f.first_name.value.trim(),
    last_name:f.last_name.value.trim(),
    father_name:f.father_name.value.trim(),
    birth_certificate_no:f.birth_certificate_no.value.trim(),
    imeis:imeis,
    no_confirm_check:!document.getElementById('confirm_on').checked,
    submit:document.getElementById('submit_on').checked,
    debugging:document.getElementById('debug_on').checked,
    manual_manage:document.getElementById('manual_on').checked
  };
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
  .then(function(r){return r.json();})
  .then(function(res){
    STORE=res; activeId=res.active_id;
    renderProfiles();
    var t=document.getElementById('toast');
    t.style.color='var(--ok)'; t.textContent='✓ ذخیره شد — حالا ربات را اجرا کن';
    t.classList.add('show'); setTimeout(function(){t.classList.remove('show');},3000);
  })
  .catch(function(){
    var t=document.getElementById('toast');
    t.style.color='var(--danger)'; t.textContent='✕ خطا در ذخیره';
    t.classList.add('show');
  });
}

function renderLogs(){
  var el=document.getElementById('view-logs'); el.innerHTML='';
  var p=getProf(activeId)||{};
  var own=filterLogsForProfile(LOGS,p);
  el.innerHTML='<div class="nav-tabs"><div class="nav-tab active" id="log-tab-profile" onclick="renderLogs(\'profile\')">لاگ پروفایل</div><div class="nav-tab" id="log-tab-all" onclick="renderLogs(\'all\')">لاگ کلی</div></div><div id="log-list"></div>';
  var mode=arguments[0]||logMode;
  logMode=mode;
  var list=mode==='all'?LOGS:own;
  var listEl=document.getElementById('log-list');
  if(!list.length){listEl.innerHTML='<div class="log-empty">هنوز لاگی ثبت نشده</div>';return;}
  list.forEach(function(log){
    var d=document.createElement('div'); d.className='log-item '+(log.status||'');
    var icon={'error':'❌','success':'✅','warn':'⚠️','info':'ℹ️'}[log.status]||'•';
    d.innerHTML='<div class="log-top"><span>'+icon+' '+escHtml(log.step||'')+'</span><span>'+escHtml(log.ts||'')+'</span></div><div class="log-msg">'+escHtml(log.message||'')+'</div>'+(log.title?'<div class="hint">👤 '+escHtml(log.title)+'</div>':'');
    listEl.appendChild(d);
  });
}

function filterLogsForProfile(logs,p){return logs.filter(function(x){return (p.title&&x.title===p.title)||(p.national&&x.national===p.national);});}

function refreshLogs(){
  fetch('/logs',{cache:'no-store'}).then(function(r){return r.json();}).then(function(logs){
    LOGS=logs;
    notifyNewLogs(logs);
    var view=document.getElementById('view-logs');
    if(view && !view.classList.contains('hidden')) renderLogs(logMode);
  });
}
setInterval(refreshLogs,10000);

/* ---- notification-e OPTIONAL baraye log-haye JADID (bala-chap) ---- */
var NOTIF_ON=localStorage.getItem('irica-lognotif')!=='0';
var NOTIF_SEEN={};
var NOTIF_READY=false;
var NOTIF_ICON={success:'✅',error:'❌',warn:'⚠️',info:'ℹ️'};

function logKey(l){return (l.ts||'')+'|'+(l.step||'')+'|'+(l.message||'').slice(0,60);}

function updateNotifToggle(){
  var b=document.getElementById('notif-toggle'); if(!b)return;
  b.textContent=NOTIF_ON?'🔔 اعلان لاگ: روشن':'🔕 اعلان لاگ: خاموش';
  b.style.top=(NOTIF_ON?'auto':'14px');
  b.style.bottom=(NOTIF_ON?'14px':'auto');
}
function toggleLogNotif(){
  NOTIF_ON=!NOTIF_ON;
  localStorage.setItem('irica-lognotif',NOTIF_ON?'1':'0');
  if(!NOTIF_ON) document.getElementById('lognotif-wrap').innerHTML='';
  updateNotifToggle();
}

function notifyNewLogs(logs){
  var fresh=[];
  (logs||[]).forEach(function(l){
    var k=logKey(l);
    if(!NOTIF_SEEN[k]){NOTIF_SEEN[k]=1;fresh.push(l);}
  });
  if(!NOTIF_READY){NOTIF_READY=true;return;}   // bar-e aval hame ghadimi hastand
  if(!NOTIF_ON)return;
  fresh.slice(0,4).forEach(function(l,i){setTimeout(function(){pushLogNotif(l);},i*220);});
}

function pushLogNotif(l){
  var wrap=document.getElementById('lognotif-wrap'); if(!wrap)return;
  var st=l.status||'info';
  var d=document.createElement('div');
  d.className='lognotif s-'+st;
  d.innerHTML='<button class="x" aria-label="بستن" onclick="this.parentNode.remove()">×</button>'+
    '<div class="t">'+(NOTIF_ICON[st]||'•')+' <span>'+escHtml(l.step||'رویداد جدید')+'</span>'+
    '<span style="margin-inline-start:auto;font-size:10.5px;color:var(--muted);direction:ltr">'+escHtml(l.ts||'')+'</span></div>'+
    (l.imei?'<div class="sn">سریال: '+escHtml(l.imei)+'</div>':'')+
    (l.title?'<div class="sn">👤 '+escHtml(l.title)+'</div>':'')+
    '<div class="m">'+escHtml(l.message||'')+'</div>';
  wrap.appendChild(d);
  requestAnimationFrame(function(){d.classList.add('show');});
  while(wrap.children.length>4) wrap.removeChild(wrap.firstChild);
  setTimeout(function(){
    d.classList.remove('show');
    setTimeout(function(){if(d.parentNode)d.remove();},300);
  },9000);
}

function renderInquiries(){
  var p=getProf(activeId)||{};
  document.getElementById('view-inquiries').innerHTML='<div class="inquiry-card"><h3>استعلام ممنوع‌الخروجی</h3><p class="hint">بررسی وضعیت ممنوع‌الخروجی پروفایل انتخاب‌شده</p>'+buildExitbanResult(p)+'<button type="button" class="save" onclick="runExitbanInquiry(activeId)">استعلام آنلاین خروج از کشور</button><div id="exitban-status" class="hint"></div></div><div class="inquiry-card"><h3>استعلام اقساط و وام پرداخت‌نشده</h3><span class="soon">به‌زودی</span></div>';
}

function refreshExitbanResult(id, startedAt){
  fetch('/state').then(function(r){return r.json();}).then(function(store){
    STORE=store; var p=getProf(id); var card=document.getElementById('exitban-result-card');
    if(card&&p){var wrap=document.createElement('div');wrap.innerHTML=buildExitbanResult(p);card.replaceWith(wrap.firstChild);}
    if(!p) return;
    if(p.exitban_status==='not_checked' && Date.now()-startedAt<130000){
      setTimeout(function(){refreshExitbanResult(id,startedAt);},1000);
      return;
    }
    var status=document.getElementById('exitban-status');
    if(status) status.textContent=p.exitban_status==='not_checked'?'نتیجه در زمان تعیین‌شده دریافت نشد.':'نتیجه در پنل ثبت شد.';
  }).catch(function(){
    if(Date.now()-startedAt<130000) setTimeout(function(){refreshExitbanResult(id,startedAt);},1500);
  });
}

function runExitbanInquiry(id){
  var status=document.getElementById('exitban-status');
  if(status) status.textContent='در حال بررسی اطلاعات پروفایل...';
  fetch('/exitban-inquiry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
    .then(function(r){return r.json().then(function(data){return {ok:r.ok,data:data};});})
    .then(function(res){
      if(!res.ok){ if(status) status.textContent=res.data.error+(res.data.missing?' — '+res.data.missing.join('، '):''); return; }
      if(status) status.textContent='فرم در Chrome باز شد؛ نتیجه پس از اتمام ربات اینجا نمایش داده می‌شود.';
      refreshExitbanResult(id,Date.now());
    }).catch(function(){if(status) status.textContent='خطا در شروع فرم استعلام';});
}


function showApiHealth(h){
  var box=document.getElementById('api-health-toast');
  if(!box||!h)return;
  box.classList.remove('ok','fail'); box.classList.add(h.ok?'ok':'fail','show');
  document.getElementById('api-health-message').textContent=(h.ok?'✓ ':'⚠ ')+(h.message||'نتیجه تست نامشخص است');
  document.getElementById('api-health-meta').textContent=(h.api_base||'')+(h.ts?' • '+h.ts:'');
  clearTimeout(window.__apiToastTimer);
  window.__apiToastTimer=setTimeout(function(){
    if(!box.matches(':hover'))box.classList.remove('show');
  },h.ok?9000:16000);
}
function refreshApiHealth(run){
  fetch(run?'/telegram-health?run=1':'/telegram-health',{cache:'no-store'})
    .then(function(r){return r.json();}).then(showApiHealth)
    .catch(function(){showApiHealth({ok:false,message:'ارتباط پنل با تست API برقرار نشد.'});});
}

function renderTelegram(){
  var tokenState = TELEGRAM.token_set
    ? '<span class="token-badge ok">🔒 توکن ذخیره شده و محافظت‌شده است</span>'
    : '<span class="token-badge warn">⚠ توکنی ثبت نشده است</span>';
  document.getElementById('view-telegram').innerHTML='<div class="settings-section"><h3>تنظیمات کلی</h3><p class="hint">تنظیمات عمومی پنل و ربات در این بخش قرار می‌گیرد.</p></div>'+
    '<label>API Base</label><input id="tg_api" type="text" class="ltr" value="'+escHtml(TELEGRAM.api_base||'https://api.telegram.org')+'">'+
    '<label>توکن ربات</label>'+tokenState+
    '<input id="tg_token" type="password" class="ltr" autocomplete="off" placeholder="'+(TELEGRAM.token_set?'برای تغییر، توکن جدید را وارد کنید (خالی = بدون تغییر)':'توکن ربات را وارد کنید')+'">'+
    '<label>Chat IDها (هر کدام یک خط)</label><textarea id="tg_ids" class="ltr">'+escHtml((TELEGRAM.chat_ids||[]).join('\n'))+'</textarea>'+
    '<button class="save" onclick="saveTelegram()">💾 ذخیره تنظیمات تلگرام</button><div class="toast" id="tg_toast"></div>';
}

function saveTelegram(){
  fetch('/telegram-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    api_base:document.getElementById('tg_api').value,bot_token:document.getElementById('tg_token').value,
    chat_ids:document.getElementById('tg_ids').value})}).then(function(r){return r.json();}).then(function(res){
      TELEGRAM=res; var t=document.getElementById('tg_toast'); t.textContent='✓ ذخیره شد'; t.classList.add('show');
      renderTelegram();   // input-e token dobare khali/masked shavad
      refreshApiHealth(true);
  });
}

function showView(v){
  document.getElementById('view-form').classList.toggle('hidden',v!=='form');
  document.getElementById('view-logs').classList.toggle('hidden',v!=='logs');
  document.getElementById('view-inquiries').classList.toggle('hidden',v!=='inquiries');
  document.getElementById('view-telegram').classList.toggle('hidden',v!=='telegram');
  document.getElementById('tab-form').classList.toggle('active',v==='form');
  document.getElementById('tab-logs').classList.toggle('active',v==='logs');
  document.getElementById('tab-inquiries').classList.toggle('active',v==='inquiries');
  document.getElementById('tab-telegram').classList.toggle('active',v==='telegram');
  if(v==='logs') renderLogs();
  if(v==='telegram') renderTelegram();
  if(v==='inquiries') renderInquiries();
}

// init
var statsBar=document.getElementById('stats');
if(statsBar) statsBar.addEventListener('click',function(e){
  var tile=e.target.closest('.stat'); if(tile) setStatusFilter(tile.getAttribute('data-filter'));
});
renderProfiles();
updateThemeButton();
refreshApiHealth(false);
renderProfileEditorEmpty();
updateNotifToggle();
notifyNewLogs(LOGS);   // baseline: hame-ye log-haye avvaliye seen mishavand
"""

PAGE_TAIL = """
</script>
</body>
</html>"""


def render_page():
    store = load_store()
    logs = load_logs()
    js = PAGE_JS
    js = js.replace("__STORE_JSON__", json.dumps(store, ensure_ascii=False))
    js = js.replace("__LOGS_JSON__", json.dumps(logs, ensure_ascii=False))
    js = js.replace("__TELEGRAM_JSON__", json.dumps(public_telegram_config(), ensure_ascii=False))
    html = PAGE_HEAD + js + PAGE_TAIL
    return html.encode("utf-8")


HOME_PAGE = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>انتخاب سامانه گمرک</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px 16px;
  background:radial-gradient(1100px 560px at 82% -12%,#1b2a52 0,transparent 60%),radial-gradient(820px 480px at 6% 4%,#241a49 0,transparent 55%),#0b1020;
  color:#eef2ff;font-family:Tahoma,"Segoe UI",sans-serif}
.wrap{width:min(960px,94vw)}
.top{text-align:center;margin-bottom:26px}
h1{font-size:34px;margin:0 0 8px}.sub{color:#9ca8c7;margin:0}
/* live stats strip */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 auto 30px;max-width:640px}
.stat{background:#141d33;border:1px solid #28385a;border-radius:16px;padding:14px 10px;text-align:center}
.stat .n{font-size:26px;font-weight:900;line-height:1.1}
.stat .l{font-size:11.5px;color:#98a8ca;margin-top:3px}
.stat.total .n{color:#6f83ff}.stat.running .n{color:#f2b34a}.stat.success .n{color:#1fb883}.stat.blocked .n{color:#ef5b6b}
.sites{display:grid;grid-template-columns:repeat(2,1fr);gap:22px}
.site{position:relative;overflow:hidden;display:block;min-height:250px;padding:28px;border:1px solid #2d3858;border-radius:24px;color:inherit;text-decoration:none;background:linear-gradient(145deg,#18213b,#11182c);box-shadow:0 24px 70px #0007;transition:.2s}
.site:hover{transform:translateY(-6px);border-color:#788cff;box-shadow:0 30px 80px #0009}
.site[data-site="2"]{background:linear-gradient(145deg,#12382f,#10251f);border-color:#276453}
.site[data-site="2"]:hover{border-color:#3fd39a}
.num{display:grid;place-items:center;width:56px;height:56px;border-radius:18px;background:#6477ee;font-size:24px;font-weight:900}
.site[data-site="2"] .num{background:#18a873}
.site h2{font-size:26px;margin:38px 0 10px}.site p{color:#aeb8d4;line-height:1.9;margin:0 0 14px}
.tags{display:flex;flex-wrap:wrap;gap:7px}
.tag{font-size:11px;color:#c3ccea;background:#ffffff12;border:1px solid #ffffff1a;border-radius:999px;padding:4px 10px}
.go{position:absolute;left:26px;bottom:24px;font-size:26px;transition:.2s}.site:hover .go{transform:translateX(-6px)}
.foot{margin-top:26px;text-align:center;color:#6c7c9e;font-size:12px}
.foot b{color:#9ca8c7;font-weight:700}
@media(max-width:700px){.sites{grid-template-columns:1fr}.site{min-height:200px}.stats{grid-template-columns:repeat(2,1fr)}h1{font-size:27px}}
</style></head><body><main class="wrap">
<div class="top"><h1>سامانه مدیریت گمرک</h1><p class="sub">سامانه موردنظر را انتخاب کنید</p></div>
<section class="stats" id="stats" aria-label="خلاصه وضعیت">
  <div class="stat total"><div class="n" id="s-total">—</div><div class="l">کل پروفایل‌ها</div></div>
  <div class="stat running"><div class="n" id="s-running">—</div><div class="l">در حال اجرا</div></div>
  <div class="stat success"><div class="n" id="s-success">—</div><div class="l">ثبت موفق</div></div>
  <div class="stat blocked"><div class="n" id="s-blocked">—</div><div class="l">مسدود</div></div>
</section>
<div class="sites">
<a class="site" data-site="1" href="/site-1"><span class="num">۱</span><h2>سایت اول گمرک</h2><p>پنل کامل: ویرایش پروفایل، لاگ‌ها، استعلامات و تنظیمات.</p><div class="tags"><span class="tag">ویرایش کامل</span><span class="tag">لاگ‌ها</span><span class="tag">استعلام خروج</span></div><span class="go">←</span></a>
<a class="site" data-site="2" href="/site-2"><span class="num">۲</span><h2>استعلام سلامت دستگاه</h2><p>استعلام وضعیت دستگاه از سایت گمرک (فقط صفحه اول) با یک پروفایل شخص و لیست دستگاه‌ها.</p><div class="tags"><span class="tag">استعلام سلامت</span><span class="tag">افزودن عمده</span><span class="tag">ترتیب تصادفی</span></div><span class="go">←</span></a>
</div>
<div class="foot">پنل روی <b>__PANEL_URL__</b> در حال اجراست · به‌روزرسانی خودکار هر ۵ ثانیه</div>
</main>
<script>
var FA='۰۱۲۳۴۵۶۷۸۹';
function toFa(n){return String(n).replace(/[0-9]/g,function(d){return FA[d];});}
function statusKey(p){
  if(p.run_status==='busy')return'running';
  if(p.registration_status==='success')return'success';
  if(p.registration_status==='blocked')return'blocked';
  return'ready';
}
function refresh(){
  fetch('/state',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
    var ps=s.profiles||[],c={running:0,success:0,blocked:0};
    ps.forEach(function(p){var k=statusKey(p);if(c[k]!==undefined)c[k]++;});
    document.getElementById('s-total').textContent=toFa(ps.length);
    document.getElementById('s-running').textContent=toFa(c.running);
    document.getElementById('s-success').textContent=toFa(c.success);
    document.getElementById('s-blocked').textContent=toFa(c.blocked);
  }).catch(function(){});
}
refresh();setInterval(refresh,5000);
</script>
</body></html>"""


def render_home_page():
    return HOME_PAGE.replace("__PANEL_URL__", f"{HOST}:{PORT}").encode("utf-8")


def render_site_two_page():
    with open(os.path.join(HERE, "dashboard-new.html"), encoding="utf-8") as f:
        return f.read().encode("utf-8")


ESTELAM_PAGE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>استعلام سلامت دستگاه</title>
<link rel="icon" href="data:,">
<style>
  /* THEME: pishfarz = ROSHAN (light). data-theme="dark" -> tire. */
  :root{
    --bg:#eef1f9;--card:#ffffff;--card2:#f4f6fc;--line:#d9deef;--txt:#1b2036;
    --muted:#5c6484;--brand:#4f6bef;--ok:#12a05f;--danger:#e0455a;--warn:#c97a10;
    --logbg:#f7f9ff;--scroll:#cdd4ec;--sn:#4257a8;--shadow:rgba(20,30,70,.14);
    --pill-neutral-bg:#e6eaf7;--pill-neutral-fg:#5c6484;
    --pill-ok-bg:#dbf3e6;--pill-ok-fg:#0f7d4a;
    --pill-danger-bg:#fbe0e5;--pill-danger-fg:#c22a3f;
    --pill-warn-bg:#fcefd6;--pill-warn-fg:#9a5c08;
  }
  html[data-theme="dark"]{
    --bg:#0d1220;--card:#151b2d;--card2:#1b2338;--line:#303a58;--txt:#edf1ff;
    --muted:#a7afc9;--brand:#758cff;--ok:#3ad28b;--danger:#ff6678;--warn:#f6b94b;
    --logbg:#0f1526;--scroll:#2c3350;--sn:#8ea2d8;--shadow:rgba(0,0,0,.45);
    --pill-neutral-bg:#243050;--pill-neutral-fg:#a9b7e0;
    --pill-ok-bg:#12321f;--pill-ok-fg:#3ad28b;
    --pill-danger-bg:#3a1620;--pill-danger-fg:#ff6678;
    --pill-warn-bg:#3a2f12;--pill-warn-fg:#f6b94b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:Tahoma,"Segoe UI",sans-serif;padding:22px 14px 60px}
  /* arz-e kadr-e asli: 50% bishtar az ghabl (1080 -> 1620) va INTERACTIVE:
     karbar ba dastgire-ha (kenar-e kadr) arz ra avaz mikonad va dar localStorage
     zakhire mishavad. --wrapw ba JS set mishavad. */
  .wrap{width:min(var(--wrapw,1620px),98vw);margin:0 auto;position:relative}
  /* dastgire-haye taghyir-e arz (chap va rast) */
  .wgrip{position:absolute;top:0;bottom:0;width:12px;cursor:ew-resize;z-index:5;
          display:flex;align-items:center;justify-content:center}
  .wgrip.l{inset-inline-start:-14px}
  .wgrip.r{inset-inline-end:-14px}
  .wgrip::before{content:"";width:4px;height:56px;border-radius:99px;background:var(--line);
                 transition:background .15s,height .15s}
  .wgrip:hover::before,.wgrip.drag::before{background:var(--brand);height:96px}
  body.wresize{cursor:ew-resize;user-select:none}
  /* nav-e arz: dokme-haye amade + reset */
  .wbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .wbar .wlab{color:var(--muted);font-size:12px}
  .wbar .wbtn{background:var(--card2);border:1px solid var(--line);color:var(--muted);
              border-radius:9px;padding:5px 10px;font-size:12px;cursor:pointer;font-family:inherit}
  .wbar .wbtn:hover{color:var(--txt);border-color:var(--brand)}
  .wbar .wbtn.on{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:700}
  /* layout: kadr-e etela'at-e shakhs SAMT-e RAST (sticky), baghiye chap */
  .cols{display:grid;grid-template-columns:340px minmax(0,1fr);gap:18px;align-items:start}
  @media(max-width:900px){.cols{grid-template-columns:1fr}}
  .side{position:sticky;top:14px}
  .side .grid{grid-template-columns:1fr}
  .side .card{margin-bottom:0}
  .top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px;flex-wrap:wrap}
  h1{font-size:24px;margin:0}
  a.back{color:var(--muted);text-decoration:none;font-size:13px;border:1px solid var(--line);
         padding:7px 12px;border-radius:10px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:18px}
  .card h2{margin:0 0 4px;font-size:17px}
  .hint{color:var(--muted);font-size:12.5px;margin:0 0 14px}
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  @media(max-width:760px){.grid{grid-template-columns:1fr}}
  label{display:block;font-size:12.5px;color:var(--muted);margin-bottom:5px}
  input{width:100%;padding:10px 12px;border-radius:11px;border:1px solid var(--line);
        background:var(--card2);color:var(--txt);font-family:inherit;font-size:14px;direction:ltr;text-align:left}
  input:focus{outline:2px solid var(--brand);outline-offset:1px}
  .row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
  button{border:0;border-radius:11px;padding:10px 16px;font-family:inherit;font-size:13.5px;
         font-weight:700;cursor:pointer;color:#fff;background:var(--brand)}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--txt)}
  button.ok{background:var(--ok)}
  button.warn{background:var(--warn);color:#20160a}
  button.danger{background:var(--danger)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .errbox{margin-top:12px;border-inline-start:3px solid var(--danger);background:var(--pill-danger-bg);
          border-radius:10px;padding:10px 12px;font-size:13px;display:none}
  .errbox.show{display:block}
  .errbox ul{margin:0;padding-inline-start:18px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:9px 8px;border-bottom:1px solid var(--line);text-align:right}
  th{color:var(--muted);font-weight:600;font-size:12px}
  td.mono{direction:ltr;text-align:left;font-family:Consolas,monospace}
  /* natije-ye HAMTA chand-khati va tolani ast: bayad shekaste shavad, na sarriz */
  td{vertical-align:top}
  td.res{white-space:pre-line;line-height:1.6;word-break:break-word;max-width:0}
  #dev-list table{table-layout:fixed}
  #dev-list col.c-idx{width:44px}
  #dev-list col.c-sn{width:190px}
  #dev-list col.c-st{width:230px}
  #dev-list col.c-act{width:210px}
  #dev-list td.c-num{text-align:center;color:var(--muted);font-weight:700}
  .dev-name{font-weight:700;font-size:13px;margin-bottom:4px;word-break:break-word}
  /* grid-e vaziat: (همتا/گمرک) x (label, badge, zaman-e estelam) — bedun-e table
     tudartu ta selector-e radif-e jadval-e asli kharab nashavad */
  .stgrid{display:grid;grid-template-columns:auto auto 1fr;gap:4px 8px;align-items:center}
  .stgrid .slab{color:var(--muted);font-size:10.5px;white-space:nowrap}
  .stgrid .when{color:var(--muted);font-size:11px;direction:ltr;text-align:left;white-space:nowrap}
  /* sariyal-e aval/dovom zir-e ham dar YEK sotoon (faza-ye kamtar) */
  .mono.sn1,.mono.sn2{direction:ltr;text-align:left;font-family:Consolas,monospace;line-height:1.5}
  .mono.sn2{color:var(--muted);font-size:12px;margin-top:2px}
  .mono.sn2::before{content:"↳ ";color:var(--line)}
  .badge{display:inline-block;padding:3px 9px;border-radius:99px;font-size:11.5px;font-weight:700}
  .b-pending{background:var(--pill-neutral-bg);color:var(--pill-neutral-fg)}
  .b-running{background:var(--pill-warn-bg);color:var(--pill-warn-fg)}
  .b-healthy{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .b-problem{background:var(--pill-danger-bg);color:var(--pill-danger-fg)}
  .b-advanced{background:var(--pill-warn-bg);color:var(--pill-warn-fg)}
  .b-error{background:var(--pill-danger-bg);color:var(--pill-danger-fg)}
  .b-good{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .b-registered{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .b-blocked{background:var(--pill-danger-bg);color:var(--pill-danger-fg)}
  .b-unknown{background:var(--pill-warn-bg);color:var(--pill-warn-fg)}
  .empty{color:var(--muted);text-align:center;padding:20px;font-size:13px}
  /* --- entekhab-e no'-e estelam (hamta / gomrok / hame) --- */
  .svc-pick{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
  .svc-pick label{margin:0;font-size:12.5px;color:var(--muted)}
  .svc-btns{display:flex;gap:8px;flex-wrap:wrap;background:var(--card2);
            border:1px solid var(--line);border-radius:13px;padding:5px}
  .svc-btns .svc{background:transparent;border:1px solid transparent;color:var(--muted);
                 border-radius:10px;padding:7px 13px;font-size:12.5px;font-weight:700;
                 cursor:pointer;font-family:inherit}
  .svc-btns .svc:hover{color:var(--txt)}
  .svc-btns .svc.on{background:var(--brand);border-color:var(--brand);color:#fff}
  /* sotoon-e vaziat: badge-e hamta va gomrok zir-e ham ba label-e kuchak */
  .stack{display:flex;align-items:center;gap:6px;margin-bottom:4px}
  .stack:last-child{margin-bottom:0}
  .slab{color:var(--muted);font-size:10.5px;min-width:26px}
  .res2{color:var(--muted);font-size:12px;margin-top:3px}
  /* --- modiriat-e ejra --- */
  .runbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:var(--card2);
          border:1px solid var(--line);border-radius:14px;padding:12px 14px}
  .dot{width:10px;height:10px;border-radius:99px;background:var(--muted);flex:0 0 auto}
  .dot.on{background:var(--ok);box-shadow:0 0 0 4px rgba(58,210,139,.16);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{box-shadow:0 0 0 9px rgba(58,210,139,0)}}
  .runbar .st{font-size:13.5px;font-weight:700}
  .runbar .meta{font-size:11.5px;color:var(--muted);direction:ltr}
  .chips{display:flex;gap:7px;flex-wrap:wrap;margin-inline-start:auto}
  .chip{font-size:11.5px;padding:3px 9px;border-radius:99px;
        background:var(--pill-neutral-bg);color:var(--pill-neutral-fg)}
  .chip.healthy{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .chip.problem,.chip.error{background:var(--pill-danger-bg);color:var(--pill-danger-fg)}
  .chip.running,.chip.advanced{background:var(--pill-warn-bg);color:var(--pill-warn-fg)}
  .chip.good,.chip.success{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .chip.registered{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .chip.blocked{background:var(--pill-danger-bg);color:var(--pill-danger-fg)}
  .chip.unknown{background:var(--pill-warn-bg);color:var(--pill-warn-fg)}
  /* --- log-e interactive dar kadr-e sabet --- */
  .logtools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
  .logtools input[type=search]{flex:1 1 180px;min-width:140px;direction:rtl;text-align:right}
  .filters{display:flex;gap:6px;flex-wrap:wrap}
  .f{font-size:11.5px;padding:5px 10px;border-radius:99px;border:1px solid var(--line);
     background:transparent;color:var(--muted);cursor:pointer}
  .f.on{background:var(--brand);border-color:var(--brand);color:#fff}
  .logbox{height:340px;overflow-y:auto;overflow-x:hidden;border:1px solid var(--line);
          border-radius:14px;background:var(--logbg);padding:8px}
  .logbox::-webkit-scrollbar{width:9px}
  .logbox::-webkit-scrollbar-thumb{background:var(--scroll);border-radius:9px}
  .li{border:1px solid var(--line);border-inline-start-width:3px;border-radius:11px;
      padding:9px 11px;margin-bottom:7px;background:var(--card);cursor:pointer;transition:.15s}
  .li:hover{background:var(--card2);transform:translateY(-1px)}
  .li.s-healthy{border-inline-start-color:var(--ok)}
  .li.s-problem,.li.s-error{border-inline-start-color:var(--danger)}
  .li.s-retry,.li.s-advanced,.li.s-warn,.li.s-running{border-inline-start-color:var(--warn)}
  .li.s-info{border-inline-start-color:var(--brand)}
  .li .h{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:12px}
  .li .h b{font-size:12.5px}
  .li .ts{color:var(--muted);font-size:11px;direction:ltr}
  .li .sn{color:var(--sn);font-size:11.5px;direction:ltr;margin-top:3px;font-family:Consolas,monospace}
  .li .msg{font-size:12.5px;margin-top:5px;line-height:1.55;max-height:36px;overflow:hidden}
  .li.open .msg{max-height:none}
  .li .more{font-size:11px;color:var(--muted);margin-top:4px}
  .logempty{color:var(--muted);text-align:center;padding:26px;font-size:13px}
  /* --- notification-e log-e zende (bala-chap) --- */
  .lognotif-wrap{position:fixed;top:14px;left:14px;z-index:70;display:flex;flex-direction:column;
    gap:8px;width:min(360px,86vw);pointer-events:none}
  .lognotif{pointer-events:auto;background:var(--card);border:1px solid var(--line);
    border-inline-start:4px solid var(--brand);border-radius:12px;padding:10px 32px 10px 12px;
    box-shadow:0 16px 40px var(--shadow);position:relative;opacity:0;
    transform:translateX(-14px);transition:.26s}
  .lognotif.show{opacity:1;transform:translateX(0)}
  .lognotif.s-healthy{border-inline-start-color:var(--ok)}
  .lognotif.s-problem,.lognotif.s-error{border-inline-start-color:var(--danger)}
  .lognotif.s-retry,.lognotif.s-advanced,.lognotif.s-warn,.lognotif.s-running{border-inline-start-color:var(--warn)}
  .lognotif .t{font-size:12.5px;font-weight:800;display:flex;gap:6px;align-items:center}
  .lognotif .m{font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.6;
    max-height:54px;overflow:hidden}
  .lognotif .sn{font-size:10.5px;color:var(--sn);direction:ltr;margin-top:3px;font-family:Consolas,monospace}
  .lognotif .x{position:absolute;top:5px;left:7px;border:0;background:transparent;
    color:var(--muted);font-size:15px;cursor:pointer;padding:1px 5px}
  .notif-toggle{position:fixed;bottom:14px;left:14px;z-index:69;font-size:11.5px;padding:6px 10px;
    border-radius:99px;border:1px solid var(--line);cursor:pointer;background:var(--card);
    color:var(--muted);font-family:inherit}
  .bulkbar{background:var(--card2);border:1px dashed var(--line);border-radius:14px;padding:14px;margin-top:14px;display:none}
  .bulkbar.show{display:block}
  .bulkbar .n{font-size:12.5px;color:var(--warn);margin-bottom:10px}
  .toast{position:fixed;top:16px;left:50%;transform:translate(-50%,-18px);z-index:60;
         min-width:min(420px,92vw);background:var(--card);border:1px solid var(--line);
         border-inline-start-width:4px;border-radius:12px;padding:12px 40px 12px 14px;
         box-shadow:0 20px 50px var(--shadow);opacity:0;pointer-events:none;transition:.28s}
  .toast.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}
  .toast.ok{border-inline-start-color:var(--ok)}
  .toast.fail{border-inline-start-color:var(--danger)}
  .toast .t{font-size:13.5px;font-weight:700}
  .toast .m{font-size:11.5px;color:var(--muted);display:none;direction:ltr;text-align:left;margin-top:5px}
  .toast:hover .m{display:block}
  .toast button{position:absolute;top:6px;left:8px;background:transparent;border:0;color:var(--muted);font-size:16px;padding:2px 6px}
  /* --- theme switch (roshan / tire) --- */
  .tbtn{background:var(--card2);border:1px solid var(--line);color:var(--muted);
        border-radius:9px;padding:5px 11px;font-size:12px;cursor:pointer;font-family:inherit}
  .tbtn:hover{color:var(--txt);border-color:var(--brand)}
  /* --- sotoon-e natije: kholase + modal-e motn-e kamel --- */
  .res-short{white-space:pre-line;line-height:1.6;word-break:break-word}
  .res-more{margin-top:6px;background:transparent;border:1px solid var(--line);
            color:var(--brand);border-radius:9px;padding:4px 10px;font-size:11.5px;
            font-weight:700;cursor:pointer;font-family:inherit}
  .res-more:hover{border-color:var(--brand);background:var(--card2)}
  .modal{position:fixed;inset:0;z-index:90;display:none;align-items:center;justify-content:center;
         background:rgba(8,12,26,.55);padding:20px}
  .modal.show{display:flex}
  .modal .box{background:var(--card);border:1px solid var(--line);border-radius:16px;
              width:min(760px,94vw);max-height:86vh;display:flex;flex-direction:column;
              box-shadow:0 24px 60px var(--shadow)}
  .modal .hd{display:flex;align-items:center;justify-content:space-between;gap:10px;
             padding:14px 16px;border-bottom:1px solid var(--line)}
  .modal .hd h3{margin:0;font-size:15px}
  .modal .hd .sn{font-size:11.5px;color:var(--muted);direction:ltr;font-family:Consolas,monospace}
  .modal .bd{padding:16px;overflow:auto;white-space:pre-line;line-height:1.75;font-size:13.5px}
  .modal .ft{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:8px;justify-content:flex-start}
  .modal .x{background:transparent;border:0;color:var(--muted);font-size:20px;cursor:pointer;padding:0 6px}
</style>
</head>
<body>
<div id="toast" class="toast" role="status" aria-live="polite">
  <button type="button" onclick="hideToast()" aria-label="بستن">×</button>
  <div class="t" id="toast-title">—</div>
  <div class="m" id="toast-meta"></div>
</div>
<div class="modal" id="res-modal" role="dialog" aria-modal="true" aria-labelledby="res-modal-title"
     onclick="if(event.target===this)closeResModal()">
  <div class="box">
    <div class="hd">
      <div>
        <h3 id="res-modal-title">متن کامل نتیجه</h3>
        <div class="sn" id="res-modal-sn"></div>
      </div>
      <button type="button" class="x" onclick="closeResModal()" aria-label="بستن">×</button>
    </div>
    <div class="bd" id="res-modal-body"></div>
    <div class="ft">
      <button type="button" class="ghost" onclick="copyResModal()">📋 کپی متن</button>
      <button type="button" onclick="closeResModal()">بستن</button>
    </div>
  </div>
</div>
<div class="wrap">
  <div class="top">
    <h1>استعلام سلامت دستگاه</h1>
    <div class="wbar">
      <span class="wlab">عرض کادر:</span>
      <button type="button" class="wbtn" data-w="1080" onclick="setWrapW(1080)">معمولی</button>
      <button type="button" class="wbtn" data-w="1620" onclick="setWrapW(1620)">عریض</button>
      <button type="button" class="wbtn" data-w="max" onclick="setWrapW('max')">تمام‌عرض</button>
      <button type="button" class="tbtn" id="theme-btn" onclick="toggleTheme()">🌙 حالت تاریک</button>
      <a class="back" href="/">← انتخاب سامانه</a>
    </div>
  </div>
  <div class="wgrip r" id="wgrip-r" title="کشیدن برای تغییر عرض"></div>
  <div class="wgrip l" id="wgrip-l" title="کشیدن برای تغییر عرض"></div>

  <div class="cols">
  <aside class="side">
  <div class="card">
    <h2>اطلاعات شخص</h2>
    <p class="hint">یک پروفایل ثابت؛ برای همه استعلام‌ها استفاده می‌شود.</p>
    <div class="grid">
      <div><label>کد ملی (۱۰ رقم)</label><input id="p-national" inputmode="numeric" maxlength="10" placeholder="0012345678"></div>
      <div><label>تاریخ تولد (۱۳۷۵/۰۴/۲۹)</label><input id="p-birth" maxlength="10" placeholder="1375/04/29"></div>
      <div><label>شماره موبایل (۰۹...)</label><input id="p-mobile" inputmode="numeric" maxlength="11" placeholder="09121234567"></div>
    </div>
    <div class="row"><button onclick="savePerson()">💾 ذخیره اطلاعات شخص</button></div>
    <div class="errbox" id="p-err"></div>
  </div>
  </aside>

  <div class="colmain">
  <div class="card">
    <h2>اطلاعات دستگاه</h2>
    <p class="hint">نام دستگاه + سریال ۱۵ رقمی. سریال دوم اختیاری است (دو سیم‌کارت).</p>
    <div class="row">
      <button onclick="startSingle()">➕ افزودن دستگاه</button>
      <button class="ghost" onclick="startBulk()">📦 افزودن عمده</button>
    </div>

    <div class="bulkbar" id="dev-form">
      <div class="n" id="dev-form-note"></div>
      <div class="grid">
        <div><label>نام دستگاه</label><input id="d-name" maxlength="40" placeholder="آیفون ۱۷ محمد" style="direction:rtl;text-align:right"></div>
        <div><label>سریال اول</label><input id="d-s1" inputmode="numeric" maxlength="15" placeholder="356938035643809"></div>
        <div><label>سریال دوم (اختیاری)</label><input id="d-s2" inputmode="numeric" maxlength="15" placeholder=""></div>
      </div>
      <div class="row" id="dev-form-actions"></div>
      <div class="errbox" id="d-err"></div>
    </div>
  </div>

  <div class="card">
    <h2>مدیریت اجرای استعلام</h2>
    <p class="hint">کنترل مرورگر و پروسه استعلام در حال اجرا.</p>
    <div class="svc-pick">
      <label>نوع استعلام:</label>
      <div class="svc-btns" id="svc-btns">
        <button type="button" class="svc on" data-svc="hamta" onclick="pickSvc('hamta')">استعلام همتا</button>
        <button type="button" class="svc" data-svc="gomrok" onclick="pickSvc('gomrok')">استعلام گمرک</button>
        <button type="button" class="svc" data-svc="all" onclick="pickSvc('all')">انجام همه استعلام‌ها</button>
      </div>
    </div>
    <div class="runbar">
      <span class="dot" id="run-dot"></span>
      <span class="st" id="run-state">در حال بارگذاری…</span>
      <span class="meta" id="run-meta"></span>
      <span class="chips" id="run-chips"></span>
    </div>
    <div class="row">
      <button class="ok" id="btn-start-all" onclick="startAll()">🚀 شروع استعلام (در انتظارها)</button>
      <button class="danger" onclick="stopRun()">⛔ توقف و بستن مرورگر</button>
      <button class="warn" onclick="restartRun()">🔄 راه‌اندازی مجدد</button>
      <button class="ghost" onclick="retryFailed()">↻ تلاش مجدد ناموفق‌ها</button>
      <button class="ghost" onclick="refreshAll()">⟳ به‌روزرسانی وضعیت</button>
    </div>
    <div class="errbox" id="run-err"></div>
  </div>

  <div class="card">
    <h2>دستگاه‌ها</h2>
    <p class="hint">نتیجه «سالم» یعنی پیام «شناسه ... در گمرک ورودی یافت نشد» گرفته شده است.</p>
    <div id="dev-list"></div>
  </div>

  <div class="card">
    <h2>لاگ اجراهای اخیر</h2>
    <p class="hint">روی هر لاگ کلیک کنید تا جزئیات کامل باز شود.</p>
    <div class="logtools">
      <div class="filters" id="log-filters"></div>
      <input type="search" id="log-q" placeholder="جستجو در پیام یا سریال…" oninput="renderLogs()">
      <button class="ghost" onclick="clearLogs()">🗑 پاک کردن لاگ</button>
    </div>
    <div class="logbox" id="log-box"></div>
  </div>
  </div>
  </div>
</div>
<div id="lognotif-wrap" class="lognotif-wrap" aria-live="polite"></div>
<button type="button" id="notif-toggle" class="notif-toggle" onclick="toggleLogNotif()"></button>
<script>
var STATE={person:{},devices:[]};
var MODE='';   // '' | 'single' | 'bulk'
var FA_STATUS=__STATUS_JSON__;          /* motn-e kamel (baraye tooltip/chips) */
var BADGE_FA=__BADGE_JSON__;            /* motn-e KUTAH-e badge-e gomrok */
var HAMTA_FA={pending:'در انتظار',running:'در حال استعلام',good:'مجاز (قابل ثبت)',
  registered:'ثبت‌شده (قانونی)',blocked:'مسدود/خطر',success:'موفق',
  unknown:'نامشخص',error:'خطای ربات'};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function digits(s){return String(s||'').replace(/[^0-9]/g,'');}

function showToast(ok,title,meta){
  var t=document.getElementById('toast');
  document.getElementById('toast-title').textContent=title;
  document.getElementById('toast-meta').textContent=meta||'';
  t.classList.remove('ok','fail');t.classList.add(ok?'ok':'fail');t.classList.add('show');
  clearTimeout(t._h);t._h=setTimeout(hideToast,ok?4500:8000);
}
function hideToast(){document.getElementById('toast').classList.remove('show');}

function showErrors(id,list){
  var box=document.getElementById(id);
  if(!list||!list.length){box.classList.remove('show');box.innerHTML='';return;}
  box.innerHTML='<ul>'+list.map(function(e){return '<li>'+esc(e)+'</li>';}).join('')+'</ul>';
  box.classList.add('show');
}

function fillPerson(){
  var p=STATE.person||{};
  document.getElementById('p-national').value=p.national||'';
  document.getElementById('p-birth').value=p.birth||'';
  document.getElementById('p-mobile').value=p.mobile||'';
}

function renderDevices(){
  var el=document.getElementById('dev-list');
  var ds=STATE.devices||[];
  if(!ds.length){el.innerHTML='<div class="empty">هنوز دستگاهی افزوده نشده است</div>';return;}
  // JADID-tarin dastgah BALA: tartib-e store bar asas-e 'created' ast, pas ma'koos
  // mishavad (fallback: agar created barabar bud, tartib-e vorud-e ma'koos).
  var sorted=ds.map(function(d,i){return {d:d,i:i};}).sort(function(a,b){
    var ca=String(a.d.created||''), cb=String(b.d.created||'');
    if(ca!==cb) return cb<ca?-1:1;
    return b.i-a.i;
  }).map(function(x){return x.d;});
  var rows=sorted.map(function(d,idx){
    var sn='<div class="mono sn1">'+esc(d.serial1||'')+'</div>'+
           (d.serial2?'<div class="mono sn2">'+esc(d.serial2)+'</div>':'');
    var nm='<div class="dev-name">'+esc(d.name||'—')+'</div>';
    var gs=d.status||'pending', hs=d.hamta_status||'pending';
    // badge-e gomrok KUTAH (مجاز/مسدود) mesl-e hamta; motn-e kamel dar tooltip + sotoon-e natije
    var gb='<span class="badge b-'+esc(gs)+'" title="'+esc(FA_STATUS[gs]||gs)+'">'+
           esc(BADGE_FA[gs]||FA_STATUS[gs]||gs)+'</span>';
    var hb='<span class="badge b-'+esc(hs)+'" title="'+esc(HAMTA_FA[hs]||hs)+'">'+
           esc(HAMTA_FA[hs]||hs)+'</span>';
    // zir-sotoon-e DOVVOM-e vaziat: zaman-e estelam ('--' agar anjam nashode)
    var ht=fmtWhen(d.hamta_at), gt=fmtWhen(d.gomrok_at);
    // sotoon-e natije: 80 character-e aval + dokme-ye «مشاهده کامل» (modal).
    // (natije-ye HAMTA momken ast sad-ha character bashad va jadval ra bekeshad)
    var res;
    if(SVC==='hamta') res=resCell(d,'hamta');
    else if(SVC==='gomrok') res=resCell(d,'gomrok');
    else res=resCell(d,'gomrok','گمرک: ')+
             '<div class="res2">'+resCell(d,'hamta','همتا: ')+'</div>';
    // NOKTE: inja NABAYAD <table> tudartu bezanim — vagarna selector-e
    // '#dev-list tbody tr' radif-haye dakheli ra ham migirad va shomaresh/
    // check-ha kharab mishavad. Pas grid-e CSS.
    return '<tr><td class="c-num">'+toFa(idx+1)+'</td>'+
      '<td>'+nm+sn+'</td>'+
      '<td><div class="stgrid">'+
          '<span class="slab">همتا</span>'+hb+'<span class="when">'+esc(ht)+'</span>'+
          '<span class="slab">گمرک</span>'+gb+'<span class="when">'+esc(gt)+'</span>'+
          '</div></td>'+
      '<td class="res">'+res+'</td>'+
      '<td><button class="ghost" onclick="startOne(\''+esc(d.id)+'\')">'+
      esc('استعلام '+(SVC_FA[SVC]||''))+'</button> '+
      '<button class="danger" onclick="delDevice(\''+esc(d.id)+'\')">حذف</button></td></tr>';
  }).join('');
  el.innerHTML='<table>'+
    '<colgroup><col class="c-idx"><col class="c-sn"><col class="c-st"><col><col class="c-act"></colgroup>'+
    '<thead><tr><th>#</th><th>نام / سریال‌ها</th>'+
    '<th>وضعیت و زمان استعلام</th>'+
    '<th>نتیجه</th><th></th></tr></thead><tbody>'+rows+'</tbody></table>';
}

/* zaman-e estelam: '--' agar hanooz estelam nashode. faghat MM-DD HH:MM neshan
   dade mishavad (sal ezafe ast va sotoon ra pahn mikonad). */
function fmtWhen(ts){
  ts=String(ts||'').trim();
  if(!ts) return '--';
  var m=ts.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if(!m) return ts;
  return toFa(m[2]+'/'+m[3]+' '+m[4]+':'+m[5]);
}

function refresh(){
  return fetch('/estelam-state',{cache:'no-store'}).then(function(r){return r.json();})
    .then(function(s){STATE=s;renderDevices();renderRuntime(s.runtime||{});return s;});
}

/* --- sotoon-e natije: kholase-ye 80-character + modal-e motn-e kamel ------- */
var RES_LIMIT=80;
function resText(d,svc){return (svc==='hamta'?d.hamta_result:d.result)||'';}
function resCell(d,svc,prefix){
  prefix=prefix||'';
  var full=resText(d,svc);
  if(!full) return esc(prefix)+'—';
  // faghat khat-e aval-e ma'nadar + kutah-shode be 80 character
  var flat=full.replace(/\s+/g,' ').trim();
  if(flat.length<=RES_LIMIT) return '<span class="res-short">'+esc(prefix+flat)+'</span>';
  var head=esc(prefix+flat.slice(0,RES_LIMIT))+'…';
  return '<div class="res-short">'+head+'</div>'+
    '<button class="res-more" onclick="showResModal(\''+esc(d.id)+'\',\''+svc+'\')">مشاهده کامل</button>';
}
function showResModal(id,svc){
  var d=(STATE.devices||[]).filter(function(x){return String(x.id)===String(id);})[0];
  if(!d)return;
  var full=resText(d,svc);
  document.getElementById('res-modal-title').textContent=
    'متن کامل نتیجه — '+(svc==='hamta'?'همتا':'گمرک');
  document.getElementById('res-modal-sn').textContent=
    (d.serial1||'')+(d.serial2?(' / '+d.serial2):'');
  document.getElementById('res-modal-body').textContent=full||'—';
  document.getElementById('res-modal').classList.add('show');
}
function closeResModal(){document.getElementById('res-modal').classList.remove('show');}
function copyResModal(){
  var t=document.getElementById('res-modal-body').textContent||'';
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(function(){showToast(true,'متن کپی شد');},
      function(){showToast(false,'کپی نشد');});
  }
}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeResModal();});

/* chips-e gomrok hamun vajegan-e badge ra estefade mikonad (yek-dast ba hamta) */
var RUN_STATE_FA=BADGE_FA;

function renderRuntime(rt){
  var dot=document.getElementById('run-dot');
  var st=document.getElementById('run-state');
  var meta=document.getElementById('run-meta');
  var chips=document.getElementById('run-chips');
  var running=!!rt.running;
  dot.className='dot'+(running?' on':'');
  st.textContent=running?('در حال اجرا'+(rt.service_fa?(' — '+rt.service_fa):'')):'متوقف';
  st.style.color=running?'var(--warn)':'var(--muted)';
  meta.textContent=running?('pid '+rt.pid+(rt.started?(' · '+rt.started):'')):
    (rt.started?('آخرین اجرا: '+rt.started):'');
  // chips ba servis-e entekhab-shode hamahang mishavad
  var c,fa;
  if(SVC==='hamta'){c=rt.hamta_counts||{};fa=HAMTA_FA;}
  else{c=rt.counts||{};fa=RUN_STATE_FA;}
  chips.innerHTML=Object.keys(c).map(function(k){
    return '<span class="chip '+esc(k)+'">'+esc(fa[k]||k)+': '+toFa(c[k])+'</span>';
  }).join('');
}

function toFa(n){var FA='۰۱۲۳۴۵۶۷۸۹';return String(n).replace(/[0-9]/g,function(d){return FA[d];});}

function refreshAll(){refresh();renderLogs();}

function stopRun(){
  fetch('/estelam-stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
    .then(function(r){return r.json();}).then(function(j){
      showToast(true,j.stopped?'استعلام متوقف و مرورگر بسته شد':'اجرای فعالی برای توقف نبود');
      refreshAll();
    });
}

function restartRun(){
  fetch('/estelam-restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
    .then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(res){
      if(!res.ok||!res.j.ok){showErrors('run-err',res.j.errors||['راه‌اندازی مجدد نشد']);
        showToast(false,'راه‌اندازی مجدد نشد',(res.j.errors||[]).join(' · '));return;}
      showErrors('run-err',[]);
      showToast(true,'راه‌اندازی مجدد شد ('+res.j.count+' دستگاه)','pid '+res.j.pid);
      refreshAll();
    });
}

function retryFailed(){startAll();}

function savePerson(){
  var body={national:digits(document.getElementById('p-national').value),
            birth:document.getElementById('p-birth').value.trim(),
            mobile:digits(document.getElementById('p-mobile').value)};
  fetch('/estelam-person',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(res){
      if(!res.ok||!res.j.ok){showErrors('p-err',res.j.errors||['ذخیره نشد']);
        showToast(false,'اطلاعات شخص ذخیره نشد');return;}
      showErrors('p-err',[]);STATE.person=res.j.state.person;
      showToast(true,'اطلاعات شخص ذخیره شد');
    });
}

function devFormActions(html){document.getElementById('dev-form-actions').innerHTML=html;}

function startSingle(){
  MODE='single';
  document.getElementById('dev-form').classList.add('show');
  document.getElementById('dev-form-note').textContent='افزودن یک دستگاه';
  clearDevForm();
  showErrors('d-err',[]);
  devFormActions('<button onclick="saveDevice(false)">💾 ذخیره</button>'+
    '<button class="ok" onclick="saveDevice(true)">💾 ذخیره و استعلام</button>'+
    '<button class="ghost" onclick="closeDevForm()">انصراف</button>');
  document.getElementById('d-name').focus();
}

function startBulk(){
  MODE='bulk';
  document.getElementById('dev-form').classList.add('show');
  clearDevForm();
  showErrors('d-err',[]);
  bulkNote();
  devFormActions('<button onclick="bulkNext()">💾 ذخیره و بعدی</button>'+
    '<button class="ok" onclick="bulkFinish()">✅ پایان و برو به استعلام</button>'+
    '<button class="ghost" onclick="closeDevForm()">انصراف</button>');
  document.getElementById('d-name').focus();
}

function bulkNote(){
  document.getElementById('dev-form-note').textContent=
    'حالت عمده — تا حالا '+((STATE.devices||[]).length)+' دستگاه ذخیره شده. سریال دستگاه بعدی را وارد کنید.';
}

function closeDevForm(){MODE='';document.getElementById('dev-form').classList.remove('show');}

function clearDevForm(){
  ['d-name','d-s1','d-s2'].forEach(function(id){document.getElementById(id).value='';});
}

function postDevice(){
  var body={name:(document.getElementById('d-name').value||'').trim(),
            serial1:digits(document.getElementById('d-s1').value),
            serial2:digits(document.getElementById('d-s2').value)};
  return fetch('/estelam-device',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});});
}

function saveDevice(thenRun){
  postDevice().then(function(res){
    if(!res.ok||!res.j.ok){showErrors('d-err',res.j.errors||['ذخیره نشد']);
      showToast(false,'دستگاه ذخیره نشد');return;}
    showErrors('d-err',[]);STATE=res.j.state;renderDevices();
    showToast(true,'دستگاه ذخیره شد');
    var id=res.j.device.id;
    closeDevForm();
    if(thenRun) startOne(id);
  });
}

function bulkNext(){
  postDevice().then(function(res){
    if(!res.ok||!res.j.ok){showErrors('d-err',res.j.errors||['ذخیره نشد']);return;}
    showErrors('d-err',[]);STATE=res.j.state;renderDevices();
    clearDevForm();
    document.getElementById('d-name').focus();
    bulkNote();showToast(true,'ذخیره شد — سریال دستگاه بعدی');
  });
}

function bulkFinish(){
  var s1=digits(document.getElementById('d-s1').value);
  var nm=(document.getElementById('d-name').value||'').trim();
  var done=function(){closeDevForm();startAll();};
  if(!s1&&!nm){done();return;}
  postDevice().then(function(res){
    if(!res.ok||!res.j.ok){showErrors('d-err',res.j.errors||['ذخیره نشد']);return;}
    STATE=res.j.state;renderDevices();done();
  });
}

function delDevice(id){
  fetch('/estelam-device-delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})}).then(function(r){return r.json();})
    .then(function(j){STATE=j.state;renderDevices();if(MODE==='bulk')bulkNote();});
}

function runEstelam(id){
  return fetch('/estelam-start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({service:SVC},id?{id:id}:{}))}).then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j};});})
    .then(function(res){
      if(!res.ok||!res.j.ok){showErrors('run-err',res.j.errors||['اجرا نشد']);
        showToast(false,'استعلام شروع نشد',(res.j.errors||[]).join(' · '));return;}
      showErrors('run-err',[]);
      showToast(true,'استعلام '+(SVC_FA[res.j.service||SVC]||'')+' شروع شد ('+res.j.count+' دستگاه)','pid '+res.j.pid);
      refresh();
    });
}
function startOne(id){runEstelam(id);}
function startAll(){runEstelam(null);}

/* --- entekhab-e no'-e estelam --- */
var SVC='hamta';
/* SVC_FA dar dokme-ye radif ('استعلام '+…) va toast estefade mishavad. baraye
   'all' motn-e sarih-e «از گمرک و همتا» (na «همه استعلام‌ها») khaste shode. */
var SVC_FA={hamta:'همتا',gomrok:'گمرک',all:'از گمرک و همتا'};
function pickSvc(s){
  SVC=s;
  document.querySelectorAll('#svc-btns .svc').forEach(function(b){
    b.classList.toggle('on', b.getAttribute('data-svc')===s);
  });
  // dokme-ye shoro' dar tab-e «همه» sarih migoyad az kodam do samane estelam migirad
  var sb=document.getElementById('btn-start-all');
  if(sb) sb.textContent = (s==='all')
    ? '🚀 استعلام از گمرک و همتا (در انتظارها)'
    : '🚀 شروع استعلام '+(SVC_FA[s]||'')+' (در انتظارها)';
  renderDevices();
  renderRuntime((STATE.runtime)||{});
}

/* --- arz-e INTERACTIVE-e kadr-e asli (drag + preset + localStorage) --------
   Chera: natije-ye HAMTA chand-khati va tolani ast; kadr-e 1080px faza-ye khali-ye
   ziad dar chap/rast migozasht. Pishfarz 1620px (50% bishtar). */
var WMIN=900, WMAX=3000, WKEY='estelam_wrap_w';
var TKEY='estelam_theme';

/* --- theme: pishfarz ROSHAN (light); entekhab dar localStorage mimanad ----- */
function applyTheme(t){
  var dark=(t==='dark');
  document.documentElement.setAttribute('data-theme', dark?'dark':'light');
  var b=document.getElementById('theme-btn');
  if(b) b.textContent = dark ? '☀️ حالت روشن' : '🌙 حالت تاریک';
}
function setTheme(t){applyTheme(t);try{localStorage.setItem(TKEY,t);}catch(e){}}
function toggleTheme(){
  setTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark');
}
function initTheme(){
  var t=null;try{t=localStorage.getItem(TKEY);}catch(e){}
  applyTheme(t==='dark'?'dark':'light');
}

function applyWrapW(v){
  var px = (v==='max') ? Math.max(WMIN, Math.round(window.innerWidth*0.98))
                       : Math.max(WMIN, Math.min(WMAX, Number(v)||1620));
  document.documentElement.style.setProperty('--wrapw', px+'px');
  document.querySelectorAll('.wbar .wbtn').forEach(function(b){
    b.classList.toggle('on', String(b.getAttribute('data-w'))===String(v));
  });
}
function setWrapW(v){
  applyWrapW(v);
  try{localStorage.setItem(WKEY,String(v));}catch(e){}
}
function initWrapW(){
  var v=null;
  try{v=localStorage.getItem(WKEY);}catch(e){}
  applyWrapW(v||1620);
  ['wgrip-r','wgrip-l'].forEach(function(id){
    var g=document.getElementById(id);
    if(!g)return;
    g.addEventListener('mousedown',function(ev){
      ev.preventDefault();
      var wrap=document.querySelector('.wrap');
      var startX=ev.clientX, startW=wrap.getBoundingClientRect().width;
      // dastgire-ye rast: keshidan be rast arz ra kam mikonad (RTL-agnostic:
      // az fasele-ye markaz hesab mishavad ta har do dastgire tabi'i bashand)
      var dir=(id==='wgrip-r')?-2:2;
      g.classList.add('drag');document.body.classList.add('wresize');
      function mv(e){
        var w=Math.round(startW+(e.clientX-startX)*dir);
        w=Math.max(WMIN,Math.min(WMAX,w));
        document.documentElement.style.setProperty('--wrapw',w+'px');
      }
      function up(){
        document.removeEventListener('mousemove',mv);
        document.removeEventListener('mouseup',up);
        g.classList.remove('drag');document.body.classList.remove('wresize');
        var w=Math.round(document.querySelector('.wrap').getBoundingClientRect().width);
        setWrapW(w);
      }
      document.addEventListener('mousemove',mv);
      document.addEventListener('mouseup',up);
    });
    // dobar-click roo-ye dastgire = bazgasht be pishfarz-e arizh
    g.addEventListener('dblclick',function(){setWrapW(1620);});
  });
  window.addEventListener('resize',function(){
    var v=null;try{v=localStorage.getItem(WKEY);}catch(e){}
    if(v==='max')applyWrapW('max');
  });
}

function checkApiHealth(){
  fetch('/telegram-health',{cache:'no-store'}).then(function(r){return r.json();}).then(function(h){
    if(!h||!h.ts)return;
    showToast(!!h.ok,h.ok?'ارتباط API تلگرام سالم است':'ارتباط API تلگرام برقرار نشد',
      (h.message||'')+(h.api_base?(' · '+h.api_base):''));
  }).catch(function(){});
}

/* ---------------- log-e interactive (kadr-e sabet + scroll) ---------------- */
var LOGS=[];
var LOG_FILTER='all';
var LOG_OPEN={};
var LOG_ICON={healthy:'✅',problem:'⛔',advanced:'⚠️',retry:'🔁',running:'▶️',
              error:'❌',warn:'⚠️',info:'ℹ️'};
var LOG_FA={healthy:'سالم',problem:'اشکال',advanced:'هشدار مهم',retry:'تلاش مجدد',
            running:'در حال اجرا',error:'خطا',warn:'هشدار',info:'اطلاع'};
var LOG_TABS=[['all','همه'],['healthy','سالم'],['problem','اشکال'],
              ['retry','تلاش مجدد'],['advanced','هشدار'],['error','خطا']];

function renderLogFilters(){
  document.getElementById('log-filters').innerHTML=LOG_TABS.map(function(t){
    return '<button class="f'+(LOG_FILTER===t[0]?' on':'')+'" onclick="setLogFilter(\''+t[0]+'\')">'+
      esc(t[1])+'</button>';
  }).join('');
}
function setLogFilter(f){LOG_FILTER=f;renderLogFilters();paintLogs();}

function renderLogs(){
  return fetch('/estelam-logs',{cache:'no-store'}).then(function(r){return r.json();})
    .then(function(j){LOGS=j.logs||[];notifyNewLogs(LOGS);paintLogs();});
}

function paintLogs(){
  var box=document.getElementById('log-box');
  var q=(document.getElementById('log-q').value||'').trim();
  var list=LOGS.filter(function(l){
    if(LOG_FILTER!=='all'&&(l.status||'')!==LOG_FILTER)return false;
    if(!q)return true;
    return ((l.message||'')+' '+(l.serial||'')+' '+(l.step||'')).indexOf(q)>=0;
  });
  if(!list.length){box.innerHTML='<div class="logempty">لاگی برای نمایش نیست</div>';return;}
  var top=box.scrollTop;
  box.innerHTML=list.map(function(l,i){
    var st=l.status||'info';
    var key=(l.ts||'')+'|'+i;
    var open=LOG_OPEN[key]?' open':'';
    return '<div class="li s-'+esc(st)+open+'" onclick="toggleLog(\''+esc(key)+'\')">'+
      '<div class="h"><b>'+(LOG_ICON[st]||'•')+' '+esc(LOG_FA[st]||st)+
      (l.step?(' · '+esc(l.step)):'')+'</b><span class="ts">'+esc(l.ts||'')+'</span></div>'+
      (l.serial?('<div class="sn">سریال: '+esc(l.serial)+'</div>'):'')+
      '<div class="msg">'+esc(l.message||'')+'</div>'+
      (LOG_OPEN[key]?'':'<div class="more">برای جزئیات کلیک کنید</div>')+
      '</div>';
  }).join('');
  box.scrollTop=top;
}

function toggleLog(key){LOG_OPEN[key]=!LOG_OPEN[key];paintLogs();}

function clearLogs(){
  fetch('/estelam-clear-logs',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
    .then(function(){LOG_OPEN={};renderLogs();showToast(true,'لاگ‌ها پاک شد');});
}

/* ---- notification-e OPTIONAL-e log-e jadid (bala-chap) ---- */
var NOTIF_ON=localStorage.getItem('estelam-lognotif')!=='0';
var NOTIF_SEEN={};
var NOTIF_READY=false;

function logKey(l){return (l.ts||'')+'|'+(l.status||'')+'|'+(l.message||'').slice(0,60);}

function updateNotifToggle(){
  var b=document.getElementById('notif-toggle'); if(!b)return;
  b.textContent=NOTIF_ON?'🔔 اعلان لاگ: روشن':'🔕 اعلان لاگ: خاموش';
}
function toggleLogNotif(){
  NOTIF_ON=!NOTIF_ON;
  localStorage.setItem('estelam-lognotif',NOTIF_ON?'1':'0');
  if(!NOTIF_ON) document.getElementById('lognotif-wrap').innerHTML='';
  updateNotifToggle();
}

function notifyNewLogs(logs){
  var fresh=[];
  (logs||[]).forEach(function(l){
    var k=logKey(l);
    if(!NOTIF_SEEN[k]){NOTIF_SEEN[k]=1;fresh.push(l);}
  });
  if(!NOTIF_READY){NOTIF_READY=true;return;}
  if(!NOTIF_ON)return;
  fresh.slice(0,4).forEach(function(l,i){setTimeout(function(){pushLogNotif(l);},i*220);});
}

function pushLogNotif(l){
  var wrap=document.getElementById('lognotif-wrap'); if(!wrap)return;
  var st=l.status||'info';
  var d=document.createElement('div');
  d.className='lognotif s-'+st;
  d.innerHTML='<button class="x" aria-label="بستن" onclick="this.parentNode.remove()">×</button>'+
    '<div class="t">'+(LOG_ICON[st]||'•')+' <span>'+esc(LOG_FA[st]||st)+'</span>'+
    '<span style="margin-inline-start:auto;font-size:10.5px;color:var(--muted);direction:ltr">'+
    esc(l.ts||'')+'</span></div>'+
    (l.serial?('<div class="sn">سریال: '+esc(l.serial)+'</div>'):'')+
    '<div class="m">'+esc(l.message||'')+'</div>';
  wrap.appendChild(d);
  requestAnimationFrame(function(){d.classList.add('show');});
  while(wrap.children.length>4) wrap.removeChild(wrap.firstChild);
  setTimeout(function(){
    d.classList.remove('show');
    setTimeout(function(){if(d.parentNode)d.remove();},300);
  },9000);
}

STATE=__ESTELAM_JSON__;
initTheme();
initWrapW();
fillPerson();renderDevices();renderRuntime(STATE.runtime||{});
renderLogFilters();updateNotifToggle();renderLogs();
setInterval(refresh,4000);
setInterval(renderLogs,5000);
checkApiHealth();
</script>
</body>
</html>
"""


def render_estelam_page():
    state = estelam_load()
    state["runtime"] = estelam_runtime()
    html = ESTELAM_PAGE
    html = html.replace("__ESTELAM_JSON__", json.dumps(state, ensure_ascii=False))
    html = html.replace("__STATUS_JSON__",
                        json.dumps(ESTELAM_STATUS_FA, ensure_ascii=False))
    html = html.replace("__BADGE_JSON__",
                        json.dumps(ESTELAM_BADGE_FA, ensure_ascii=False))
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        from urllib.parse import urlsplit
        route = urlsplit(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, render_home_page())
        elif route == "/site-1":
            self._send(200, render_page())
        elif route == "/site-2":
            self._send(200, render_estelam_page())
        elif route == "/estelam-state":
            estelam_reset_pending_stale()
            state = estelam_load()
            state["runtime"] = estelam_runtime()
            self._json(state)
        elif route == "/estelam-logs":
            self._json({"logs": load_estelam_logs()})
        elif route == "/state":
            clear_stale_busy_profiles()
            self._json(load_store())
        elif route == "/logs":
            self._json(load_logs())
        elif route == "/site2-notif":
            self._json(load_site2_notif())
        elif route == "/telegram-health":
            from urllib.parse import parse_qs
            query = parse_qs(urlsplit(self.path).query)
            if query.get("run") == ["1"]:
                self._json(test_telegram_api(attempts=2, ensure_chain=True))
            else:
                self._json(load_telegram_health())
        elif route == "/artifact":
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            pid, kind = (q.get("id") or [""])[0], (q.get("kind") or [""])[0]
            key = "receipt_screenshot" if kind == "receipt" else "payment_screenshot"
            profile = next((p for p in load_store().get("profiles", []) if p.get("id") == pid), None)
            path = os.path.realpath((profile or {}).get(key) or "")
            root = os.path.realpath(os.path.join(HERE, "screenshots"))
            try:
                allowed = os.path.commonpath([root, path]) == root
            except ValueError:
                allowed = False
            if profile and allowed and os.path.isfile(path):
                with open(path, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, b"not found", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        store = load_store()
        if self.path == "/save":
            data = self._read_json()
            pid = (data.get("id") or "").strip()
            found = False
            for i, p in enumerate(store["profiles"]):
                if p["id"] == pid:
                    data["id"] = pid
                    data = sync_shared_identity(merge_profile_update(p, data))
                    store["profiles"][i] = data
                    found = True
                    break
            if not found:
                data["id"] = pid or None
                store["profiles"].append(data)
            # profile-i ke zakhire shod ra ACTIVE mikonim
            store["active_id"] = pid or store["profiles"][-1].get("id") or ""
            clean = save_store(store)
            # active_id-e vaghe'i ba'd az tamiz-shodan
            print(f">>> Zakhire shod: profile={data.get('title')!r} "
                  f"active={clean['active_id']}")
            self._json(clean)

        elif self.path == "/activate":
            data = self._read_json()
            store["active_id"] = (data.get("id") or "").strip()
            clean = save_store(store)
            print(f">>> Faal shod: {clean['active_id']}")
            self._json(clean)

        elif self.path == "/toggle":
            data = self._read_json()
            for p in store["profiles"]:
                if p["id"] == str(data.get("id") or "") and p.get("run_status") != "busy":
                    p["enabled"] = bool(data.get("enabled"))
            self._json(save_store(store))

        elif self.path == "/start":
            data = self._read_json()
            status, payload = start_profile(str(data.get("id") or ""))
            self._json(payload if status != 200 else load_store(), status)

        elif self.path == "/start2":
            data = self._read_json()
            status, payload = start_profile2(str(data.get("id") or ""))
            self._json(payload if status != 200 else load_store(), status)

        elif self.path == "/start2-manual":
            try:
                runtime = start_site2_manual_process()
            except (OSError, ValueError):
                self._json({"error": "شروع ربات سایت دوم انجام نشد"}, 500)
            else:
                self._json({"ok": True, "pid": runtime["pid"]})

        elif self.path == "/exitban-inquiry":
            data = self._read_json()
            profile_id = str(data.get("id") or "").strip()
            try:
                import exitban
                identity = exitban.load_identity(profile_id)
                missing = exitban.missing_identity_fields(identity)
            except ValueError as exc:
                self._json({"error": str(exc)}, 404)
                return
            if missing:
                self._json({"error": "اطلاعات هویتی ناقص است", "missing": missing}, 422)
                return
            runtime = start_exitban_process(profile_id)
            self._json({"ok": True, "profile_id": profile_id, "pid": runtime["pid"], "submitted": False})

        elif self.path == "/exitban-result":
            data = self._read_json()
            profile_id = str(data.get("id") or data.get("profile_id") or "").strip()
            text = str(data.get("text") or data.get("result") or "").strip()
            status = str(data.get("status") or "").strip()
            if not profile_id or not text:
                self._json({"error": "profile id and result text are required"}, 422)
            elif record_exitban_result(profile_id, text, status):
                profile = next(p for p in load_store()["profiles"] if p["id"] == profile_id)
                self._json({"ok": True, "profile_id": profile_id,
                            "status": profile["exitban_status"],
                            "checked_at": profile["exitban_checked_at"]})
            else:
                self._json({"error": "profile not found"}, 404)

        elif self.path == "/stop":
            data = self._read_json()
            profile_id = str(data.get("id") or "").strip()
            if profile_id:
                status, payload = stop_profile(profile_id)
                self._json(payload if status != 200 else load_store(), status)
            else:
                pid = int(data.get("pid") or 0)
                stop_profile_process(pid)
                clear_stale_busy_profiles()
                self._json(load_store())

        elif self.path == "/new":
            store["profiles"].append(_clean_profile({}))
            store["active_id"] = store["profiles"][-1]["id"]
            clean = save_store(store)
            self._json(clean)

        elif self.path == "/clone":
            data = self._read_json()
            cloned = clone_profile(str(data.get("id") or ""))
            self._json(load_store() if cloned else {"error": "profile not found"}, 200 if cloned else 404)

        elif self.path == "/delete":
            data = self._read_json()
            pid = (data.get("id") or "").strip()
            store["profiles"] = [p for p in store["profiles"] if p["id"] != pid]
            if not store["profiles"]:
                store["profiles"] = [_clean_profile({})]
            if store.get("active_id") == pid:
                store["active_id"] = store["profiles"][0]["id"]
            clean = save_store(store)
            self._json(clean)

        elif self.path == "/clear-logs":
            clear_logs()
            self._json({"ok": True})

        elif self.path == "/telegram-config":
            self._json(save_telegram_config(self._read_json()))

        elif self.path == "/estelam-person":
            data = self._read_json()
            errors = validate_estelam_person(data)
            if errors:
                self._json({"ok": False, "errors": errors}, 400)
            else:
                self._json({"ok": True, "state": estelam_save_person(data)})

        elif self.path == "/estelam-device":
            data = self._read_json()
            errors = validate_estelam_device(data)
            if errors:
                self._json({"ok": False, "errors": errors}, 400)
            else:
                record = estelam_add_device(data)
                self._json({"ok": True, "device": record, "state": estelam_load()})

        elif self.path == "/estelam-device-delete":
            data = self._read_json()
            self._json({"ok": True,
                        "state": estelam_delete_device(str(data.get("id") or ""))})

        elif self.path == "/estelam-start":
            data = self._read_json()
            result = estelam_start(str(data.get("id") or "") or None,
                                   service=str(data.get("service") or "gomrok"))
            self._json(result, 200 if result.get("ok") else 400)

        elif self.path == "/estelam-stop":
            self._json(estelam_stop())

        elif self.path == "/estelam-restart":
            result = estelam_restart()
            self._json(result, 200 if result.get("ok") else 400)

        elif self.path == "/estelam-clear-logs":
            self._json(clear_estelam_logs())

        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *args):
        pass


def clean_project_root():
    """Harf-e mohandesi: az root-e proje fayl-ha-ye test/dbg/movaqat-ra
    (ke asl-e kar nistand) pak kon — har bar ke panel run mishavad."""
    patterns = (
        "_after_*.png", "_after_*.html", "_after_*.json",
        "_dbg_last.*", "_dbg_patched.*", "_dbg_step_*.html",
        "_last_captcha_*.png", "_scs_fast.js", "_tmp_*",
        "hermes-verify-*.py", "_verify_*.py", "verify_tmp_*.py",
    )
    import glob
    for pattern in patterns:
        for path in glob.glob(os.path.join(HERE, pattern)):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    print(f">>> Cleanup: {os.path.basename(path)}")
            except OSError:
                pass
    dbg_site2 = os.path.join(HERE, "_dbg_site2")
    if os.path.isdir(dbg_site2):
        import shutil
        try:
            shutil.rmtree(dbg_site2)
            print(">>> Cleanup: _dbg_site2/")
        except OSError:
            pass


def main():
    url = f"http://{HOST}:{PORT}"
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    load_store()  # motmaen sho profiles.json + inputs.json sakhte/sync shode-and
    clear_stale_busy_profiles()
    clean_project_root()
    threading.Thread(target=_startup_telegram_bootstrap, daemon=True,
                     name="telegram-api-health").start()
    print(f">>> Panel bala amad: {url}")
    print(f">>> Profile-ha: {PROFILES_PATH}")
    print(f">>> Profile-e active -> {INPUTS_PATH} (gomrok.py az inja mikhanad)")
    print(f">>> Log-ha: {LOGS_PATH}")
    print(">>> Baraye khamoosh kardan: Ctrl+C")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n>>> Panel baste shod.")
        srv.shutdown()


if __name__ == "__main__":
    main()



