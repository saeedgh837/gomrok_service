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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# The packaged release runs on an embedded CPython that is ISOLATED (see
# python*._pth): PYTHONPATH is ignored and the script's directory is NOT added to
# sys.path. Top-level imports here are stdlib only, so the panel starts either
# way, but `app_paths` below and the lazy project imports further down
# (`import exitban` in the /exitban-inquiry route) would raise
# ModuleNotFoundError without this.
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import app_paths  # noqa: E402  (needs HERE on sys.path first)

# Config, state and logs live outside the release folder so an update does not
# require copying them by hand. Anything left inside an older release folder is
# copied in once, here, before any path constant is resolved.
_MIGRATED_FILES = app_paths.bootstrap()
DATA_DIR = app_paths.data_dir()


def _resolve_robot_python():
    """Interpreter that runs the robots.

    Robots MUST run under the interpreter that owns the dependencies shipped
    next to them. The packaged release ships its own embedded CPython (no torch,
    ONNX solver) and no torch-only solver modules; a foreign interpreter that
    happens to have torch installed makes the solver packages pick their torch
    backend and die with ModuleNotFoundError before the browser even opens --
    the v1.0.3 field failure. `sys.executable` is the panel's own interpreter,
    which is by construction the right one.

    `IRICA_ROBOT_PYTHON` overrides it for development, and only when it points
    at a real file, so a stale value can never break the robots.
    """
    override = str(os.environ.get("IRICA_ROBOT_PYTHON") or "").strip()
    if override and os.path.isfile(override):
        return override
    return sys.executable


ROBOT_PYTHON = _resolve_robot_python()

ROBOT_LOG_PATH = app_paths.data_path("gomrok_worker.log")
INPUTS_PATH = app_paths.data_path("inputs.json")
PROFILES_PATH = app_paths.data_path("profiles.json")
LOGS_PATH = app_paths.data_path("logs.jsonl")
SITE2_NOTIF_PATH = app_paths.data_path("site2_notif.json")
TELEGRAM_PATH = app_paths.data_path("telegram_config.json")
TELEGRAM_HEALTH_PATH = app_paths.data_path("telegram_health.json")
EXITBAN_ERRORS_PATH = app_paths.data_path("exitban_errors.jsonl")
SYSTEM_LOG_DIR = app_paths.data_subdir("logs", "system")
SYSTEM_DEBUG_CONFIG_PATH = app_paths.data_path("system_debug.json")
HOST, PORT = "127.0.0.1", 8750
_STORE_LOCK = threading.RLock()
_STORE_LOCK_PATH = PROFILES_PATH + ".lock"
# Akharin store-e salem (be tafkik-e masir-e file) ke movaffagh khonde/neveshte shode.
# Agar profiles.json be dalile write-e nimekare/harab-shode movaqatan ghabel-e khandan
# nabashad, load_store IN cache ra barmigardanad va HARGEZ file-e mojood ra ba yek
# profile-e khali baznevisi nemikonad (jelogiri az pak-shodan-e profile-ha zir-e bar).
_LAST_GOOD_STORE = {}


class StoreUnreadableError(RuntimeError):
    """profiles.json vojood darad ama na khodash na backup-ash ghabel-e parse nist."""


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
    "imeis": ["", ""], "no_confirm_check": False, "submit": True,
    "debugging": False, "manual_manage": False,
    # Morurgar be tore pishfarz makhfi (off-screen) ejra mishavad ta manabe-e
    # system zir-e bar-e chand robot-e hamzaman hedar naravad. Panel in flag ra
    # taghir midahad va robot-e dar-hal-e-ejra an-ra donbal mikonad.
    "browser_visible": False,
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
    "device_inquiry_pid": 0,
    "device_inquiry_service": "",
    "device_inquiry_started_at": "",
    "gomrok_inquiry_status": "not_checked",
    "gomrok_inquiry_results": [],
    "gomrok_inquiry_error": "",
    "gomrok_inquiry_checked_at": "",
    "hamta_inquiry_status": "not_checked",
    "hamta_inquiry_results": [],
    "hamta_inquiry_error": "",
    "hamta_inquiry_checked_at": "",
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
              "exitban_result", "exitban_checked_at", "device_inquiry_service",
              "device_inquiry_started_at", "gomrok_inquiry_status", "gomrok_inquiry_error",
              "gomrok_inquiry_checked_at", "hamta_inquiry_status", "hamta_inquiry_error",
              "hamta_inquiry_checked_at"):
        out[k] = str(out.get(k) or "").strip()
    for key in ("gomrok_inquiry_results", "hamta_inquiry_results"):
        out[key] = list(out.get(key) or [])
    imeis = out.get("imeis") or []
    if not isinstance(imeis, list):
        imeis = [str(imeis)]
    out["imeis"] = [str(x).strip() for x in imeis if x and str(x).strip()]
    out["no_confirm_check"] = bool(out.get("no_confirm_check"))
    # Auto-submit (zadan-e 'edame') digar switch-e UI nadarad va HAMISHE roshan ast.
    # Halat-haye debugging va manual_manage tanzimat-e khodeshan ra dast-nakhorde
    # negah midarand (in do az panel be robot pas mishavand va rooye
    # jarayan-e submit dar gomrok.py asar-e khodeshan ra darand).
    out["submit"] = True
    out["debugging"] = bool(out.get("debugging"))
    out["manual_manage"] = bool(out.get("manual_manage"))
    out["browser_visible"] = bool(out.get("browser_visible"))
    out["enabled"] = bool(out.get("enabled", True))
    try:
        out["runner_pid"] = int(out.get("runner_pid") or 0)
    except (TypeError, ValueError):
        out["runner_pid"] = 0
    try:
        out["device_inquiry_pid"] = int(out.get("device_inquiry_pid") or 0)
    except (TypeError, ValueError):
        out["device_inquiry_pid"] = 0
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


_DEVICE_INQUIRY_SERVICES = {"hamta", "gomrok", "all"}
_DEVICE_INQUIRY_FINAL = {"healthy", "problem", "advanced", "good", "registered", "blocked"}


def _device_inquiry_summary(results, error=""):
    if error:
        return "error"
    statuses = {str(item.get("status") or "error") for item in results}
    if not statuses:
        return "error"
    return "complete" if statuses <= _DEVICE_INQUIRY_FINAL else "error"


def record_profile_device_inquiry(profile_id, service, results, error=""):
    service = str(service or "").strip().lower()
    if service not in ("hamta", "gomrok") or not isinstance(results, list):
        return False
    clean = []
    for item in results:
        if isinstance(item, dict):
            clean.append({"serial": str(item.get("serial") or "").strip(),
                          "serial2": str(item.get("serial2") or "").strip(),
                          "status": str(item.get("status") or "error").strip(),
                          "result": str(item.get("result") or "").strip(),
                          "checked_at": str(item.get("checked_at") or "").strip()})
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", [])
                        if p.get("id") == str(profile_id or "")), None)
        if not profile:
            return False
        profile[f"{service}_inquiry_results"] = clean
        profile[f"{service}_inquiry_status"] = _device_inquiry_summary(clean, error)
        profile[f"{service}_inquiry_error"] = str(error or "").strip()
        profile[f"{service}_inquiry_checked_at"] = now
        requested = str(profile.get("device_inquiry_service") or "")
        if requested == service or (requested == "all" and
                all(profile.get(f"{name}_inquiry_status") not in ("running", "not_checked")
                    for name in ("gomrok", "hamta"))):
            profile["device_inquiry_pid"] = 0
        save_store(store)
        return True
    return False


def _backup_path():
    return PROFILES_PATH + ".bak"


# Set by shutdown_everything(); main()'s serve loop polls it and exits cleanly.
_SHUTDOWN_REQUESTED = threading.Event()


def stop_profile_inquiry(profile_id):
    """Stop the profile's device-inquiry robot (profile_inquiry.py tree).

    Kills the tracked pid (device_inquiry_pid) when alive, resets the running
    inquiry bookkeeping, and reports a calm no-op when nothing is running.
    Returns (ok, message) — ok is False only when the profile does not exist.
    """
    profile_id = str(profile_id or "").strip()
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", [])
                        if p.get("id") == profile_id), None)
        if not profile:
            return False, "پروفایل پیدا نشد"
        pid = int(profile.get("device_inquiry_pid") or 0)
        running = bool(pid and _pid_alive(pid))
        services = [name for name in ("gomrok", "hamta")
                    if profile.get(f"{name}_inquiry_status") == "running"]
        if running:
            profile["device_inquiry_pid"] = 0
            profile["device_inquiry_service"] = ""
            for name in services or ["gomrok", "hamta"]:
                profile[f"{name}_inquiry_status"] = "not_checked"
                profile[f"{name}_inquiry_error"] = "توقف دستی توسط کاربر"
            save_store(store)
    if running:
        stop_profile_process(pid)
        return True, "استعلام متوقف شد و مرورگر بسته شد"
    return True, "استعلامی برای این پروفایل در حال اجرا نیست"


def shutdown_everything():
    """Stop every robot this panel spawned, then ask main() to exit.

    Kills: busy gomrok/gomrok2 runners, per-profile device inquiries and the
    estelam (site-2) run. Returns a small summary for the HTTP response.
    """
    stopped = []
    with _locked_store():
        store = load_store()
        for profile in store.get("profiles", []):
            pids = []
            if profile.get("run_status") == "busy" and profile.get("runner_pid"):
                pids.append(int(profile["runner_pid"]))
            inquiry_pid = int(profile.get("device_inquiry_pid") or 0)
            if inquiry_pid:
                pids.append(inquiry_pid)
                profile["device_inquiry_pid"] = 0
                profile["device_inquiry_service"] = ""
                for name in ("gomrok", "hamta"):
                    if profile.get(f"{name}_inquiry_status") == "running":
                        profile[f"{name}_inquiry_status"] = "not_checked"
                        profile[f"{name}_inquiry_error"] = "بستن سامانه توسط کاربر"
            if profile.get("run_status") == "busy":
                profile["run_status"] = "idle"
                profile["runner_id"] = ""
                profile["busy_since"] = ""
                profile["runner_pid"] = 0
                profile["heartbeat_at"] = ""
            if pids:
                stopped.extend(pids)
        save_store(store)
    estelam_pid = int(estelam_load().get("run_pid") or 0)
    if estelam_pid:
        stopped.append(estelam_pid)
        estelam_stop()
    for pid in stopped:
        stop_profile_process(pid)
    _SHUTDOWN_REQUESTED.set()
    return {"ok": True, "stopped": stopped}


def _parse_store_file(path):
    """File ra parse mikonad. Barmigardanad store-e tamiz ya None (nabood/harab)."""
    if not os.path.exists(path):
        return None
    try:
        if os.path.getsize(path) == 0:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("profiles")
    if not isinstance(raw, list):
        return None
    profs = [_clean_profile(p) for p in raw if isinstance(p, dict)]
    active = data.get("active_id") or (profs[0]["id"] if profs else "")
    if profs and active not in [p["id"] for p in profs]:
        active = profs[0]["id"]
    return {"profiles": profs, "active_id": active}


def load_store():
    """profiles.json ra mikhanad: {profiles:[...], active_id:"..."}.

    Tartib-e manba'-ha (mohem baraye jelogiri az pak-shodan-e profile-ha):
      1) khod-e profiles.json
      2) profiles.json.bak (nokhse-ye salem-e ghabli)
      3) cache-e in process (_LAST_GOOD_STORE)
    Agar file VOJOOD DARAD vali hich-kodam ghabel-e khandan nabood,
    StoreUnreadableError partab mishavad — DAR IN HALAT file BAZNEVISI NEMISHAVAD.
    Faghat vaghti file asan nabashad, mohajerat az inputs.json anjam mishavad.
    """
    store = _parse_store_file(PROFILES_PATH)
    if store is not None:
        _LAST_GOOD_STORE[PROFILES_PATH] = store
        return store

    file_exists = os.path.exists(PROFILES_PATH)
    if file_exists:
        recovered = _parse_store_file(_backup_path())
        if recovered is not None:
            print(">>> HOSHDAR: profiles.json kharab bood — az profiles.json.bak barmigardanim.")
            _LAST_GOOD_STORE[PROFILES_PATH] = recovered
            return recovered
        cached = _LAST_GOOD_STORE.get(PROFILES_PATH)
        if cached is not None:
            print(">>> HOSHDAR: profiles.json kharab bood — az cache-e process estefade shod.")
            return {"profiles": [dict(p) for p in cached["profiles"]],
                    "active_id": cached["active_id"]}
        raise StoreUnreadableError(
            "profiles.json ghabel-e khandan nist va backup/cache ham vojood nadarad")

    # File asan nist: mohajerat-e narm az inputs.json-e ghadimi.
    profs = []
    if os.path.exists(INPUTS_PATH):
        try:
            with open(INPUTS_PATH, encoding="utf-8") as f:
                old = json.load(f)
            if old:
                profs = [_clean_profile(old)]
        except (OSError, ValueError):
            pass
    if not profs:
        profs = [_clean_profile({})]
    store = {"profiles": profs, "active_id": profs[0]["id"]}
    save_store(store)
    return store


def save_store(store):
    """Store ra ATOMIC minevisad: tmp + fsync + os.replace, ba yek backup-e salem.

    Rooye Windows write-e mostaghim rooye profiles.json ba'd az truncate,
    agar process/system zir-e bar ghat' shavad, file-e khali/nimekare baja
    migozarad — hamin baes-e 'khali shodan-e profile-ha' mishod.
    """
    profs = [_clean_profile(p) for p in store.get("profiles", [])]
    active = store.get("active_id") or (profs[0]["id"] if profs else "")
    if profs and active not in [p["id"] for p in profs]:
        active = profs[0]["id"]
    clean = {"profiles": profs, "active_id": active}
    payload = json.dumps(clean, ensure_ascii=False, indent=2)

    # backup-e nokhse-ye salem-e ghabli (faghat agar khod-esh salem bashad)
    if _parse_store_file(PROFILES_PATH) is not None:
        try:
            shutil.copyfile(PROFILES_PATH, _backup_path())
        except OSError as exc:
            print(f">>> HOSHDAR: backup-e profiles.json anjam nashod: {exc}")

    directory = os.path.dirname(os.path.abspath(PROFILES_PATH)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".profiles-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, PROFILES_PATH)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    _LAST_GOOD_STORE[PROFILES_PATH] = clean
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


def app_version_text():
    """Version of the running panel, read once from VERSION beside this file."""
    global _APP_VERSION
    if not _APP_VERSION:
        try:
            with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as f:
                _APP_VERSION = f.read().strip()
        except OSError:
            _APP_VERSION = ""
    return _APP_VERSION


_APP_VERSION = ""


def release_blocked_profile_with_reason(profile_id):
    """Unblock a blocked profile so the operator can edit and rerun it.

    Only a profile that is actually blocked AND not busy is reset. Everything
    the start gate refuses on is cleared: registration_status -> pending,
    runtime_error dropped, runtime bookkeeping reset, inquiry bookkeeping back
    to not_checked, and the profile re-enabled (mark_profile_unusable disabled
    it). Returns (ok, message) — message is Persian for the panel UI.
    """
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", [])
                        if p.get("id") == str(profile_id or "")), None)
        if not profile:
            return False, "پروفایلی با این شناسه پیدا نشد"
        if profile.get("run_status") == "busy":
            return False, "پروفایل در حال اجراست؛ اول ربات را متوقف کنید"
        if profile.get("registration_status") != "blocked":
            return False, "این پروفایل مسدود نیست؛ نیازی به آزادسازی ندارد"
        profile["registration_status"] = "pending"
        profile["runtime_error"] = ""
        profile["run_status"] = "idle"
        profile["runner_id"] = ""
        profile["busy_since"] = ""
        profile["runner_pid"] = 0
        profile["heartbeat_at"] = ""
        profile["device_inquiry_pid"] = 0
        profile["device_inquiry_service"] = ""
        profile["device_inquiry_started_at"] = ""
        profile["gomrok_inquiry_status"] = "not_checked"
        profile["hamta_inquiry_status"] = "not_checked"
        profile["enabled"] = True
        save_store(store)
        return True, f"پروفایل «{profile.get('title') or profile_id}» آزاد شد"


def release_blocked_profile(profile_id):
    """Boolean wrapper kept for callers that ignore the reason message."""
    return release_blocked_profile_with_reason(profile_id)[0]


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
            "runtime_error": "",
            "run_status": "idle", "runner_id": "", "busy_since": "", "runner_pid": 0,
            "heartbeat_at": "", "exitban_status": "not_checked", "exitban_result": "",
            "exitban_checked_at": "", "device_inquiry_pid": 0, "device_inquiry_service": "",
            "device_inquiry_started_at": "", "gomrok_inquiry_status": "not_checked",
            "gomrok_inquiry_results": [], "gomrok_inquiry_error": "",
            "gomrok_inquiry_checked_at": "", "hamta_inquiry_status": "not_checked",
            "hamta_inquiry_results": [], "hamta_inquiry_error": "",
            "hamta_inquiry_checked_at": "",
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
            # har ejra ba morurgar-e makhfi shoru mishavad (off-screen).
            p["browser_visible"] = False
            save_store(store)
            result = dict(p)
            result["runner_id"] = runner_id
            return result
    return None


def _robot_command(python_executable, script, *args):
    return [python_executable, os.path.join(HERE, "robot_launcher.py"),
            os.path.join(HERE, script)] + [str(arg) for arg in args]


def start_profile_process(profile_id):
    command = _robot_command(ROBOT_PYTHON, "gomrok.py", "--profile-id", profile_id)
    child_env = _robot_child_env()
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid, "profile_id": str(profile_id)}


def start_profile2_process(profile_id):
    command = _robot_command(ROBOT_PYTHON, "gomrok2.py", "--profile-id", profile_id)
    child_env = _robot_child_env()
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=log_file)
    finally:
        log_file.close()
    return {"pid": process.pid, "profile_id": str(profile_id)}


def start_site2_manual_process():
    command = _robot_command(ROBOT_PYTHON, "gomrok2.py", "--no-panel")
    child_env = _robot_child_env()
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


def start_profile_device_inquiry(profile_id, service):
    profile_id = str(profile_id or "").strip()
    service = str(service or "").strip().lower()
    if service not in _DEVICE_INQUIRY_SERVICES:
        return 400, {"error": "نوع استعلام نامعتبر است"}
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", []) if p.get("id") == profile_id), None)
        if not profile:
            return 404, {"error": "پروفایل پیدا نشد"}
        missing = []
        if not profile.get("imeis"):
            missing.append("سریال / IMEI")
        if service in ("gomrok", "all"):
            if not profile.get("national"):
                missing.append("کد ملی")
            if not profile.get("birth"):
                missing.append("تاریخ تولد")
        if missing:
            return 422, {"error": "اطلاعات پروفایل ناقص است", "missing": missing}
        old_pid = int(profile.get("device_inquiry_pid") or 0)
        if old_pid and _pid_alive(old_pid):
            return 409, {"error": "یک استعلام برای این پروفایل در حال اجرا است"}

    command = _robot_command(ROBOT_PYTHON, "profile_inquiry.py",
                             "--profile-id", profile_id, "--service", service)
    child_env = _robot_child_env()
    log_file = open(ROBOT_LOG_PATH, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=child_env,
                                   stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file)
    except OSError:
        return 500, {"error": "شروع ربات استعلام انجام نشد"}
    finally:
        log_file.close()
    with _locked_store():
        store = load_store()
        profile = next(p for p in store["profiles"] if p["id"] == profile_id)
        profile["device_inquiry_pid"] = process.pid
        profile["device_inquiry_service"] = service
        profile["device_inquiry_started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        for name in (("gomrok", "hamta") if service == "all" else (service,)):
            profile[f"{name}_inquiry_status"] = "running"
            profile[f"{name}_inquiry_error"] = ""
        save_store(store)
    return 200, {"ok": True, "profile_id": profile_id, "service": service, "pid": process.pid}


def start_exitban_process(profile_id):
    log_path = app_paths.data_path("exitban_worker.log")
    command = _robot_command(ROBOT_PYTHON, "exitban.py",
                             "--profile-id", profile_id, "--submit")
    log_file = open(log_path, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=HERE, env=_robot_child_env(), stdin=subprocess.DEVNULL,
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
            # robot morde ast: darkhast-e namayesh-e morurgar ham bi-ma'ni ast.
            p["browser_visible"] = False
            changed = True
        if changed:
            save_store(store)
    return changed


def get_browser_visibility(profile_id):
    """Flag-e namayesh-e morurgar baraye in profile (None = profile peyda nashod).

    Robot-e dar-hal-e-ejra in-ra har chand sanie mikhanad; pas SABOK negah dashte
    mishavad (bedun-e lock-e beyn-processi) va dar soorat-e kharabi-ye movaqat-e
    file, None barmigardanad ta robot vaziat-e fe'li-ye khod ra hefz konad.
    """
    profile_id = str(profile_id or "").strip()
    if not profile_id:
        return None
    try:
        store = load_store()
    except StoreUnreadableError:
        return None
    for p in store.get("profiles", []):
        if p.get("id") == profile_id:
            return bool(p.get("browser_visible"))
    return None


def set_browser_visibility(profile_id, visible):
    """Darkhast-e namayesh/makhfi-kardan-e morurgar ra rooye profile sabt mikonad."""
    profile_id = str(profile_id or "").strip()
    if not profile_id:
        return 422, {"error": "profile id is required"}
    with _locked_store():
        store = load_store()
        profile = next((p for p in store.get("profiles", []) if p.get("id") == profile_id), None)
        if not profile:
            return 404, {"error": "profile not found"}
        if profile.get("run_status") != "busy":
            return 409, {"error": "این پروفایل در حال اجرا نیست"}
        profile["browser_visible"] = bool(visible)
        save_store(store)
    return 200, {"ok": True, "profile_id": profile_id, "browser_visible": bool(visible)}


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


def load_system_debug_config():
    try:
        with open(SYSTEM_DEBUG_CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        data = {}
    return {"enabled": bool(data.get("enabled"))}


def save_system_debug_config(data):
    clean = {"enabled": bool((data or {}).get("enabled"))}
    os.makedirs(os.path.dirname(SYSTEM_DEBUG_CONFIG_PATH), exist_ok=True)
    with open(SYSTEM_DEBUG_CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(clean, handle, ensure_ascii=False, indent=2)
    return clean


def list_system_logs():
    os.makedirs(SYSTEM_LOG_DIR, exist_ok=True)
    logs = []
    for name in os.listdir(SYSTEM_LOG_DIR):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.log", name):
            continue
        path = os.path.join(SYSTEM_LOG_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        logs.append({"name": name, "size": stat.st_size,
                     "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))})
    logs.sort(key=lambda item: item["modified"], reverse=True)
    return logs[:200]


def read_system_log(name, max_bytes=512000):
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.log", name):
        raise ValueError("invalid log name")
    root = os.path.realpath(SYSTEM_LOG_DIR)
    path = os.path.realpath(os.path.join(root, name))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise FileNotFoundError(name)
    with open(path, "rb") as handle:
        size = os.path.getsize(path)
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        raw = handle.read(max_bytes)
    return raw.decode("utf-8", errors="replace")


def _robot_child_env():
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    child_env["IRICA_SYSTEM_DEBUG"] = "1" if load_system_debug_config()["enabled"] else "0"
    child_env["IRICA_SYSTEM_LOG_DIR"] = SYSTEM_LOG_DIR
    return child_env


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
    (UI token-e zakhire-shode ra NEMIFRESTAD, pas khali = 'taghir nade').

    `chat_ids` ham reshte (textarea-ye UI: har khat/comma) va ham list ra
    mipazirad — /telegram-config dar khandan list barmigardanad, pas neveshtan
    bayad haman shekl-ra ghabul konad.
    """
    current = load_telegram_config()
    incoming_token = str(cfg.get("bot_token") or "").strip()
    incoming_ids = cfg.get("chat_ids") or ""
    if isinstance(incoming_ids, (list, tuple)):
        raw_ids = [str(x) for x in incoming_ids]
    else:
        raw_ids = str(incoming_ids).replace(",", "\n").splitlines()
    clean = {"api_base": str(cfg.get("api_base") or "https://api.telegram.org").strip(),
             "bot_token": incoming_token or current.get("bot_token", ""),
             "chat_ids": [x.strip() for x in raw_ids if x.strip()]}
    with open(TELEGRAM_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    return public_telegram_config()


def public_telegram_config():
    """Nokhse-ye BEDUN-e token baraye UI — token hargez be morurgar nemiravad."""
    cfg = load_telegram_config()
    return {"api_base": cfg.get("api_base", "https://api.telegram.org"),
            "chat_ids": cfg.get("chat_ids", []),
            "token_set": bool(str(cfg.get("bot_token") or "").strip())}



def test_telegram_api(attempts=3):
    """Connectivity/auth test via getMe; does not send a message."""
    import requests
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

    last_message = result["message"]
    for attempt in range(1, max(1, int(attempts)) +1):
        try:
            response = requests.get(f"{base}/bot{token}/getMe", timeout=20)
            payload = response.json()
            if response.ok and payload.get("ok"):
                username = str((payload.get("result") or {}).get("username") or "ربات")
                result.update(ok=True, used_base=base,
                              message=f"ارتباط API تلگرام برقرار است — @{username}")
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
    health = test_telegram_api(attempts=3)
    if not health.get("ok"):
        time.sleep(6)
        health = test_telegram_api(attempts=3)
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
    """Rvidaad-e movaffaghiat-e vorud-e site2: yek log dar logs.jsonl +yek
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
ESTELAM_PATH = os.environ.get("IRICA_ESTELAM_STORE") or app_paths.data_path("estelam_store.json")
ESTELAM_LOGS_PATH = app_paths.data_path("estelam_logs.jsonl")
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
        for candidate in (ESTELAM_PATH, ESTELAM_PATH + ".bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, encoding="utf-8") as f:
                    loaded = json.load(f) or {}
                person = dict(data["person"])
                person.update(loaded.get("person") or {})
                data.update(loaded)          # run_pid/run_started/... gom nashavand
                data["person"] = person
                data["devices"] = list(loaded.get("devices") or [])
                if candidate == ESTELAM_PATH and \
                        not os.path.exists(ESTELAM_PATH + ".bak"):
                    # Seed the fallback so a future corrupt main never leaves
                    # us with zero copies (review F6).
                    try:
                        with open(ESTELAM_PATH + ".bak", "w",
                                  encoding="utf-8") as bak:
                            json.dump(data, bak, ensure_ascii=False, indent=2)
                    except OSError:
                        pass
                break                        # main read fine — skip the .bak
            except Exception:
                continue                     # corrupt main -> try the .bak
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
    """Atomic save with one .bak generation (review F6): the old main file
    becomes the backup; on the very first save the backup is seeded from the
    new content so a corrupt main NEVER leaves zero fallbacks. A corrupt
    main falls back to the .bak on read."""
    with _ESTELAM_LOCK:
        tmp_path = ESTELAM_PATH + ".tmp"
        bak_path = ESTELAM_PATH + ".bak"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            if os.path.exists(ESTELAM_PATH):
                os.replace(ESTELAM_PATH, bak_path)
            else:
                with open(bak_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        os.replace(tmp_path, ESTELAM_PATH)
        return data


def set_profile_enabled(profile_id, enabled):
    """Enable/disable a profile unless it is busy (used by CRM device delete)."""
    with _locked_store():
        store = load_store()
        changed = False
        for profile in store.get("profiles", []):
            if profile.get("id") == str(profile_id) and profile.get("run_status") != "busy":
                profile["enabled"] = bool(enabled)
                changed = True
        if changed:
            save_store(store)
        return changed


def load_crm_logs(limit=300):
    """CRM JSONL event log tail (newest last), same shape as estelam logs."""
    import crm as crm_mod
    out = []
    path = crm_mod.log_path()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
    except OSError:
        return []
    return out[-limit:]


def crm_state_payload():
    """crm store + joined per-device estelam results for /crm-state."""
    import crm as crm_mod
    data = crm_mod.store_read()
    est = estelam_load()
    est_by_id = {str(d.get("id")): d for d in est.get("devices", [])}
    enriched = []
    for device in data.get("devices", []):
        row = dict(device)
        est_row = est_by_id.get(str(device.get("id"))) or {}
        row["estelam"] = {k: est_row.get(k, "") for k in
                          ("status", "result", "hamta_status", "hamta_result",
                           "gomrok_at", "hamta_at", "updated")}
        enriched.append(row)
    out = dict(data)
    out["devices"] = enriched
    out["estelam_running"] = estelam_is_running()
    return out


def crm_sync_queue(device_id):
    """Upsert the CRM device's estelam queue row and reset its status to
    pending (review F2b — the robots skip rows carrying a final status).
    ALL reads/writes go through estelam_load/estelam_save (_ESTELAM_LOCK);
    non-CRM rows stay byte-identical (review F18)."""
    import crm as crm_mod
    data = crm_mod.store_read()
    device = next((d for d in data.get("devices", [])
                   if d.get("id") == device_id), None)
    if not device:
        return ["دستگاه پیدا نشد"]
    customer = next((c for c in data.get("customers", [])
                     if c.get("id") == device.get("customer_id")), None)
    est = estelam_load()
    row = next((d for d in est.get("devices", [])
                if str(d.get("id")) == str(device_id)), None)
    if row:
        row["status"] = "pending"
        row["customer_id"] = device.get("customer_id") or ""
    else:
        row = {
            "id": str(device_id),
            "name": device.get("name") or "",
            "serial1": device.get("serial1") or "",
            "serial2": device.get("serial2") or "",
            "status": "pending",
            "result": "",
            "hamta_status": "pending",
            "hamta_result": "",
            "customer_id": device.get("customer_id") or "",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "customer_name": (customer or {}).get("full_name") or "",
        }
        est.setdefault("devices", []).append(row)
    estelam_save(est)
    return []


def crm_start_inquiry(device_id, service="gomrok", origin="manual"):
    """CRM-scoped inquiry start (plan section 2):
      - pick the identity FOR THIS STAGE: random valid pool entry for
        pre-registration intake, the device OWNER for the watch stages (F1);
      - materialize it into store['person'] (F3 — no new store keys);
      - sync + reset the CRM row and start ONE device (F2 — never the
        unscoped sweep that would swallow the operator's manual site-2 rows).
    """
    import crm as crm_mod
    if estelam_is_running():
        return {"ok": False, "errors": ["یک اجرای استعلام در حال انجام است — بعداً تلاش می‌شود"]}
    if service not in ESTELAM_SERVICES:
        service = "gomrok"
    person = {"national": "", "birth": "", "mobile": ""}
    if service != "hamta":
        data = crm_mod.store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not device:
            return {"ok": False, "errors": ["دستگاه پیدا نشد"]}
        person, errors = crm_mod.identity_for_stage(device_id)
        if errors:
            crm_mod.crm_log("warn", "inquiry_identity_missing",
                            device_id=device_id, errors="; ".join(errors))
            return {"ok": False, "errors": errors}
        person_errors = validate_estelam_person(person)
        if person_errors:
            return {"ok": False, "errors": person_errors}
        estelam_save_person(person)
    errors = crm_sync_queue(device_id)
    if errors:
        return {"ok": False, "errors": errors}
    result = estelam_start(device_id=device_id, service=service)
    if result.get("ok"):
        data = crm_mod.store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if device:
            device["last_inquiry_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            crm_mod.store_write(data)
        crm_mod.crm_log("info", "inquiry_started", device_id=device_id,
                        service=service, origin=origin,
                        person_masked=crm_mod.mask_national(person.get("national")))
    else:
        crm_mod.crm_log("warn", "inquiry_start_failed", device_id=device_id,
                        service=service, detail=str(result))
    return result


def crm_ensure_registration_profile(device_id):
    """At handover: link the site-1 profile or create one (review F9).
    Dedupe first — an operator-registered device links to its EXISTING
    profile instead of creating a duplicate. Returns the profile id or "".
    """
    import crm as crm_mod
    data = crm_mod.store_read()
    device = next((d for d in data.get("devices", [])
                   if d.get("id") == device_id), None)
    if not device:
        return ""
    if device.get("profile_id"):
        store = load_store()
        if next((p for p in store.get("profiles", [])
                 if p.get("id") == device["profile_id"]), None):
            return device["profile_id"]          # already linked (idempotent)
    owner = next((o for o in data.get("owners", [])
                  if o.get("id") == device.get("owner_id")), None)
    serial1 = str(device.get("serial1") or "").strip()
    store = load_store()
    profile = None
    if serial1 or (owner and owner.get("national")):
        profile = next((p for p in store.get("profiles", [])
                        if serial1 and serial1 in (p.get("imeis") or []))
                       or (owner and str(p.get("national") or "") ==
                           str(owner.get("national") or "")), None)
    if profile is None:
        profile = {
            "id": uuid.uuid4().hex[:12],
            "title": f"{device.get('name')} — "
                     f"{(owner or {}).get('first_name', '')} "
                     f"{(owner or {}).get('last_name', '')}".strip(" —"),
            "national": (owner or {}).get("national", ""),
            "birth": (owner or {}).get("birth", ""),
            "mobile": (owner or {}).get("mobile", ""),
            "imeis": [s for s in (serial1, device.get("serial2") or "") if s],
            "enabled": False,
            "registration_status": "pending",
        }
        store.setdefault("profiles", []).append(profile)
        save_store(store)
        crm_mod.crm_log("info", "profile_created", device_id=device_id,
                        profile_id=profile["id"])
    else:
        crm_mod.crm_log("info", "profile_linked", device_id=device_id,
                        profile_id=profile["id"])
    with crm_mod.STORE_LOCK:
        data = crm_mod.store_read()
        dev = next((d for d in data.get("devices", [])
                    if d.get("id") == device_id), None)
        if dev:
            dev["profile_id"] = profile["id"]
            dev["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            crm_mod.store_write(data)
    return profile["id"]


def _crm_is_due(device, settings):
    last = str(device.get("last_inquiry_at") or "")
    if not last:
        return True
    try:
        last_ts = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True
    hours = int(settings.get("watch_interval_hours") or 2)
    return (time.time() - last_ts) >= hours * 3600


def crm_html_to_pdf(html, timeout_s=60):
    """Render HTML to PDF with headless Edge (--print-to-pdf); None when Edge
    is unavailable so callers fall back to the printable HTML (plan §6)."""
    import shutil
    import subprocess
    import tempfile
    edge = shutil.which("msedge")
    if not edge:
        for candidate in (
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""),
                             "Microsoft\\Edge\\Application\\msedge.exe"),
                os.path.join(os.environ.get("PROGRAMFILES", ""),
                             "Microsoft\\Edge\\Application\\msedge.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             "Microsoft\\Edge\\Application\\msedge.exe")):
            if candidate and os.path.isfile(candidate):
                edge = candidate
                break
    if not edge:
        return None
    fd, html_path = tempfile.mkstemp(suffix=".html")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(html)
        pdf_path = html_path + ".pdf"
        command = [edge, "--headless", "--disable-gpu",
                   "--no-pdf-header-footer",
                   f"--print-to-pdf={pdf_path}", html_path]
        try:
            subprocess.run(command, timeout=timeout_s,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return None
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 200:
            return None
        with open(pdf_path, "rb") as handle:
            return handle.read()
    finally:
        for path in (html_path, html_path + ".pdf"):
            try:
                os.remove(path)
            except OSError:
                pass


_CRM_SCHEDULER_THREAD = None
_CRM_SCHEDULER_STOP = threading.Event()


def _crm_scheduler_loop():
    """60s cadence scheduler beat (plan section 5). Daemon thread; IRICA_CRM_
    SCHEDULER_OFF=1 is the operator kill-switch. Exceptions never kill the
    loop — they are logged and the beat is skipped."""
    while not _CRM_SCHEDULER_STOP.wait(60):
        try:
            crm_scheduler_tick()
            crm_auto_advance_scan()
        except Exception as exc:
            try:
                crm.crm_log("error", "scheduler_exception", detail=str(exc))
            except Exception:
                pass


def crm_scheduler_start():
    global _CRM_SCHEDULER_THREAD
    if os.environ.get("IRICA_CRM_SCHEDULER_OFF") == "1":
        return None
    if _CRM_SCHEDULER_THREAD and _CRM_SCHEDULER_THREAD.is_alive():
        return _CRM_SCHEDULER_THREAD
    _CRM_SCHEDULER_STOP.clear()
    _CRM_SCHEDULER_THREAD = threading.Thread(target=_crm_scheduler_loop,
                                             name="crm-scheduler", daemon=True)
    _CRM_SCHEDULER_THREAD.start()
    return _CRM_SCHEDULER_THREAD


def crm_scheduler_stop():
    _CRM_SCHEDULER_STOP.set()


def crm_scheduler_tick(origin="scheduler"):
    """One scheduler beat: start at most ONE due watch-stage device per tick
    (runs are serialized by the estelam is-running gate)."""
    import crm as crm_mod
    data = crm_mod.store_read()
    if not (data.get("settings") or {}).get("scheduler_enabled"):
        return {"ok": False, "started": []}
    if estelam_is_running():
        crm_mod.crm_log("info", "scheduler_skipped_running")
        return {"ok": False, "started": []}
    watch = {"handed", "to_payment"}
    started = []
    for device in data.get("devices", []):
        if device.get("stage") not in watch:
            continue
        if not _crm_is_due(device, data.get("settings") or {}):
            continue
        result = crm_start_inquiry(device["id"], service="gomrok",
                                   origin=origin)
        if result.get("ok"):
            started.append({"device_id": device["id"],
                            "pid": result.get("pid")})
            crm_mod.crm_log("info", "scheduler_started",
                            device_id=device["id"])
        else:
            crm_mod.crm_log("warn", "scheduler_start_failed",
                            device_id=device["id"],
                            detail="; ".join(result.get("errors") or []))
        break                      # one device per tick — serialized runs
    return {"ok": bool(started), "started": started}


def crm_auto_advance_scan():
    """Join 'advanced' watch rows to to_payment, and (when the device has
    auto_advance) start payment. The stage moves to payment ONLY on a 200
    from start_profile (review F5); failures keep the device in to_payment
    with advance_attempts capped at 6 (operator-visible give-up). Retry
    scans pick to_payment devices with attempts>0 back up (F5)."""
    import crm as crm_mod
    moved, started, errors = [], [], []
    if estelam_is_running():
        return {"ok": False, "moved": moved, "started": started}
    est = estelam_load()
    advanced_ids = {str(d.get("id")) for d in est.get("devices", [])
                    if str(d.get("status") or "") == "advanced"}
    data = crm_mod.store_read()
    for device in data.get("devices", []):
        stage = device.get("stage")
        attempts = int(device.get("advance_attempts") or 0)
        is_new = stage == "handed" and str(device.get("id")) in advanced_ids
        is_retry = stage == "to_payment" and attempts > 0 and attempts < 6
        if is_new:
            with crm_mod.STORE_LOCK:
                fresh = crm_mod.store_read()
                dev = next((d for d in fresh.get("devices", [])
                            if d.get("id") == device.get("id")), None)
                if not dev:
                    continue
                dev["stage"] = "to_payment"
                dev["advance_attempts"] = 0
                dev["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                crm_mod.store_write(fresh)
            moved.append(device["id"])
            crm_mod.crm_log("info", "registered_detected", device_id=device["id"])
        elif not is_retry:
            continue
        if not device.get("auto_advance"):
            continue
        if attempts >= 6:
            crm_mod.crm_log("error", "auto_advance_gave_up",
                            device_id=device["id"])
            continue
        profile_id = crm_ensure_registration_profile(device["id"])
        if not profile_id:
            errors.append({"device_id": device["id"],
                           "error": "پروفایل ثبت‌نام ساخته نشد"})
            continue
        status, payload = start_profile(profile_id)
        if status == 200:
            with crm_mod.STORE_LOCK:
                fresh = crm_mod.store_read()
                dev = next((d for d in fresh.get("devices", [])
                            if d.get("id") == device.get("id")), None)
                if dev:
                    dev["stage"] = "payment"
                    dev["advance_attempts"] = 0
                    dev["payment_started_at"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S")
                    crm_mod.store_write(fresh)
            started.append(device["id"])
            crm_mod.crm_log("info", "auto_payment_started",
                            device_id=device["id"], profile_id=profile_id)
        else:
            detail = str((payload or {}).get("error") or status) \
                if isinstance(payload, dict) else str(status)
            with crm_mod.STORE_LOCK:
                fresh = crm_mod.store_read()
                dev = next((d for d in fresh.get("devices", [])
                            if d.get("id") == device.get("id")), None)
                if dev:
                    dev["advance_attempts"] = int(
                        dev.get("advance_attempts") or 0) + 1
                    crm_mod.store_write(fresh)
            crm_mod.crm_log("warn", "auto_payment_failed",
                            device_id=device["id"], detail=detail,
                            attempt=attempts + 1)
            errors.append({"device_id": device["id"], "error": detail})
    return {"ok": True, "moved": moved, "started": started, "errors": errors}


def crm_advance_logged(device_id, action, **payload):
    """Stage machine + CRM event log (plan section 12)."""
    import crm as crm_mod
    device, errors = crm_mod.advance(device_id, action, **payload)
    if not errors:
        crm_mod.crm_log("info", "stage", device_id=device_id, action=action,
                        to_stage=str((device or {}).get("stage") or ""))
    else:
        crm_mod.crm_log("warn", "stage_rejected", device_id=device_id,
                        action=action, errors="; ".join(errors))
    return device, errors


def crm_update_settings(fields):
    import crm as crm_mod
    data = crm_mod.store_read()
    settings = data.get("settings") or {}
    errors = []
    if "watch_interval_hours" in fields:
        try:
            hours = int(crm_mod.normalize_digits(fields["watch_interval_hours"]))
        except (TypeError, ValueError):
            hours = 0
        if not 1 <= hours <= 72:
            errors.append("بازهٔ استعلام باید بین ۱ تا ۷۲ ساعت باشد")
        else:
            settings["watch_interval_hours"] = hours
    for key in ("scheduler_enabled", "auto_advance_default", "telegram_report_full_data"):
        if key in fields:
            settings[key] = bool(fields[key])
    if errors:
        return None, errors
    data["settings"] = settings
    crm_mod.store_write(data)
    crm_mod.crm_log("info", "settings", interval=settings.get("watch_interval_hours"),
                    scheduler=settings.get("scheduler_enabled"))
    return settings, []


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
    script, base_args = ESTELAM_SERVICES.get(service, ESTELAM_SERVICES["gomrok"])
    command = _robot_command(ROBOT_PYTHON, script, *base_args)
    if device_id:
        command += ["--device-id", str(device_id)]
    child_env = _robot_child_env()
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
        return {"ok": False, "errors": ["اطلاعات شخص برای استعلام را پر کنید"] +errors}
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
        counts[key] = counts.get(key, 0) +1
        hkey = str(device.get("hamta_status") or "pending")
        hamta_counts[hkey] = hamta_counts.get(hkey, 0) +1
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
    """Stop +start-e dobare (hamun scope-e ejra-ye ghabli)."""
    prev = estelam_load()
    previous = prev.get("run_device_id") or ""
    service = prev.get("run_service") or "gomrok"
    estelam_stop()
    time.sleep(0.6)
    result = estelam_start(previous or None, service=service)
    result["restarted"] = True
    return result


def estelam_log(status, message, step="", serial="", device_id=""):
    """Yek rvidaad-e servis-e estelam (panel +robot har do minevisand)."""
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
    --bg:#171a26; --card:#1f2333; --card2:#262b3f; --line:#363d57;
    --txt:#eef1fb; --muted:#aeb6d0; --brand:#7d95ff; --brand2:#a878ff;
    --ok:#3ad28b; --danger:#ff6678; --warn:#f6b94b; --input:#1a1e2e;
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
    padding:6px 4px 6px 2px; border:1px solid var(--line); border-radius:14px;
    background:color-mix(in srgb,var(--card2) 55%,transparent); scroll-behavior:smooth; }
  #prof-list::-webkit-scrollbar { width:9px; }
  #prof-list::-webkit-scrollbar-thumb { background:color-mix(in srgb,var(--brand) 35%,var(--line)); border-radius:9px; }
  /* --- profile cards: stable anatomy (avatar / identity / status+run / footer) --- */
  .prof-item { position:relative; background:var(--card); border:1px solid var(--line);
    border-radius:12px; padding:12px; transition:.16s; margin-bottom:9px;
    box-shadow:0 1px 2px rgba(16,24,40,.04); }
  .prof-item:hover { border-color:#c9cdd5; box-shadow:0 5px 16px rgba(16,24,40,.09); transform:translateY(-1px); }
  html[data-theme="dark"] .prof-item:hover { border-color:#3a3e47; }
  .prof-item:focus-within { border-color:var(--brand); }
  .prof-item.active { border-color:var(--brand);
    background:color-mix(in srgb,var(--brand) 6%,var(--card));
    box-shadow:0 0 0 2px color-mix(in srgb,var(--brand) 14%,transparent); }
  .prof-item.active::before { content:""; position:absolute; inset-inline-start:-1px; top:10px; bottom:10px;
    width:3px; border-radius:3px; background:linear-gradient(180deg,var(--brand),var(--brand2)); }
  .prof-head { display:flex; align-items:center; gap:10px; min-width:0; }
  .prof-avatar { flex:0 0 auto; width:38px; height:38px; border-radius:50%; cursor:pointer;
    display:flex; align-items:center; justify-content:center; color:#fff; font-size:16px; font-weight:800;
    background:linear-gradient(135deg,hsl(var(--av,220) 62% 52%),hsl(calc(var(--av,220) + 42) 62% 42%));
    user-select:none; }
  .prof-item.active .prof-avatar { box-shadow:0 0 0 2px var(--card),0 0 0 4px color-mix(in srgb,var(--ok) 55%,transparent); }
  .prof-id { flex:1 1 auto; min-width:0; }
  .prof-title { font-size:13.5px; font-weight:bold; display:flex; align-items:center; gap:6px; min-width:0; }
  .prof-title-text { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:pointer; }
  .prof-title-text:hover { color:var(--brand); }
  .profile-number { display:inline-flex; min-width:22px; height:22px; padding:0 4px; align-items:center; justify-content:center;
    border-radius:6px; background:var(--card2); border:1px solid var(--line); color:var(--muted); font-size:10.5px; font-weight:700; }
  .prof-meta { font-size:11px; color:var(--muted); margin-top:3px; direction:ltr; text-align:right;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .mdot { opacity:.55; }
  .imei-chip { display:inline-block; font-size:9.5px; font-weight:700; padding:1px 7px; border-radius:99px;
    border:1px solid var(--line); background:var(--card2); color:var(--muted); }
  .prof-side { flex:0 0 auto; display:flex; flex-direction:column; align-items:flex-end; gap:6px; }
  .prof-alert { display:flex; gap:8px; align-items:flex-start; margin-top:9px; padding:8px 11px;
    border-radius:9px; font-size:11.5px; line-height:1.6; }
  .prof-alert .ai { flex:0 0 auto; font-size:13px; }
  .prof-alert b { font-size:11.5px; }
  .prof-alert .ad { margin-top:2px; opacity:.85; }
  .prof-alert.danger { border:1px solid color-mix(in srgb,var(--danger) 35%,var(--line));
    background:color-mix(in srgb,var(--danger) 8%,var(--card)); color:var(--danger); }
  .prof-alert.ok { border:1px solid color-mix(in srgb,var(--ok) 35%,var(--line));
    background:color-mix(in srgb,var(--ok) 9%,var(--card)); color:var(--ok); }
  .mini-btn.profile-run-primary { flex:0 0 auto; min-width:112px; padding:9px 12px; border-radius:10px; font-size:13px; font-weight:800; white-space:nowrap; }
  .prof-foot { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:10px; flex-wrap:wrap; }
  .artifact-links { display:flex; flex-wrap:wrap; gap:5px; margin-top:0; }
  .artifact-links a { display:inline-flex; align-items:center; gap:4px; padding:3px 9px; border-radius:99px;
    border:1px solid color-mix(in srgb,var(--ok) 35%,var(--line));
    background:color-mix(in srgb,var(--ok) 8%,var(--card));
    color:var(--ok); text-decoration:none; font-size:10.5px; font-weight:700; transition:.15s; }
  .artifact-links a:hover { background:color-mix(in srgb,var(--ok) 16%,var(--card)); }
  /* --- dokme-ye namayesh/makhfi-kardan-e morurgar-e robot-e dar hal-e ejra --- */
  .prof-browser-row { margin-top:7px; }
  .browser-btn { position:relative; width:100%; display:inline-flex; align-items:center;
    justify-content:center; gap:6px; min-height:32px; padding:7px 10px; border-radius:9px;
    cursor:pointer; font-family:inherit; font-size:11px; font-weight:600; transition:.15s;
    border:1px dashed var(--brand); background:rgba(109,139,255,.08); color:var(--brand); }
  .browser-btn:hover { background:rgba(109,139,255,.16); border-style:solid; }
  .browser-btn.visible { border-style:solid; border-color:var(--warn,#e5a13a);
    background:rgba(229,161,58,.12); color:var(--warn,#e5a13a); }
  .browser-btn[disabled] { opacity:.55; cursor:progress; }
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
  .ibtn::after { content:attr(data-tip); position:absolute; bottom:calc(100% +7px); right:50%;
    transform:translate(50%,4px); white-space:nowrap; font-size:11px; font-weight:700;
    padding:5px 9px; border-radius:8px; background:#111827; color:#fff; opacity:0;
    pointer-events:none; transition:.16s; z-index:40; box-shadow:0 8px 20px rgba(0,0,0,.28); }
  .ibtn::before { content:''; position:absolute; bottom:calc(100% +2px); right:50%;
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
  .profiles-empty .pe-icon { font-size:26px; margin-bottom:6px; }
  .profiles-empty .pe-hint { font-size:11px; margin-top:5px; opacity:.8; }
  /* view switcher (compact/comfortable) */
  .view-switch { display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  .view-switch button { border:0; background:var(--card); color:var(--muted); font-size:13px;
    padding:4px 9px; cursor:pointer; transition:.15s; }
  .view-switch button+button { border-inline-start:1px solid var(--line); }
  .view-switch button.active { background:color-mix(in srgb,var(--brand) 14%,var(--card)); color:var(--brand); font-weight:700; }
  /* compact list mode */
  #prof-list.compact .prof-item { padding:8px 10px; margin-bottom:6px; }
  #prof-list.compact .prof-avatar { width:30px; height:30px; font-size:13px; }
  #prof-list.compact .prof-actions,
  #prof-list.compact .prof-foot { display:none; }
  #prof-list.compact .mini-btn.profile-run-primary { min-width:82px; padding:6px 9px; font-size:11px; }
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
  /* field ba dokme-ye paste-e kenari */
  .field-row { display:flex; gap:8px; align-items:stretch; }
  .field-row > input[type=text] { flex:1 1 auto; min-width:0; }
  .paste-btn { flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; gap:5px;
    min-width:46px; padding:0 11px; border-radius:12px; cursor:pointer; font-family:inherit; font-size:12px;
    border:1px solid var(--line); background:var(--card2); color:var(--muted); transition:.15s; }
  .paste-btn .paste-ico { font-size:14px; line-height:1; }
  .paste-btn .paste-label { font-size:11.5px; }
  .paste-btn:hover { color:var(--brand); border-color:var(--brand); background:rgba(109,139,255,.10); }
  .paste-btn:focus-visible { outline:2px solid var(--brand); outline-offset:2px; }
  .paste-btn.ok { color:var(--ok); border-color:var(--ok); background:rgba(46,204,113,.12); }
  .paste-btn.err { color:var(--danger); border-color:var(--danger); background:rgba(255,107,122,.12); }
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
  .toggle input:checked +.track { background:linear-gradient(135deg,var(--brand),var(--brand2)); }
  .toggle input:checked +.track:before { right:23px; }
  .toggle.danger input:checked +.track { background:linear-gradient(135deg,#ff6b7a,#ff9166); }
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
  .system-debug-card { display:grid; gap:12px; }
  .system-debug-row { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .system-debug-help { color:var(--muted); font-size:11.5px; line-height:1.8; }
  .system-log-list { display:grid; gap:7px; max-height:260px; overflow:auto; }
  .system-log-item { display:flex; align-items:center; gap:8px; border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:var(--input); }
  .system-log-name { direction:ltr; text-align:left; font:700 11px Consolas,monospace; flex:1; overflow:hidden; text-overflow:ellipsis; }
  .system-log-meta { color:var(--muted); font-size:10px; white-space:nowrap; direction:ltr; }
  .system-log-open { border:1px solid var(--brand); color:var(--brand); background:transparent; border-radius:7px; padding:5px 9px; cursor:pointer; font:700 11px inherit; }
  .system-log-viewer { min-height:230px; max-height:440px; overflow:auto; white-space:pre-wrap; word-break:break-word; direction:ltr; text-align:left; padding:12px; border:1px solid var(--line); border-radius:8px; background:#080b12; color:#d9e2f2; font:11px/1.65 Consolas,monospace; }
  .inquiry-picker { display:grid; grid-template-columns:repeat(3,1fr); gap:7px; margin:14px 0; }
  .inquiry-picker button { min-height:42px; border:1px solid var(--line); background:var(--input); color:var(--txt); border-radius:9px; cursor:pointer; font:700 12px inherit; }
  .inquiry-picker button.active { background:linear-gradient(135deg,var(--brand),var(--brand2)); color:#fff; border-color:transparent; }
  .device-results { display:grid; gap:10px; margin:12px 0 16px; }
  .device-result { border:1px solid var(--line); border-inline-start:4px solid var(--muted); background:var(--card); border-radius:8px; padding:12px; }
  .device-result.good { border-inline-start-color:var(--ok); }
  .device-result.bad { border-inline-start-color:var(--danger); }
  .device-result.running { border-inline-start-color:var(--warn); }
  .device-result-head { display:flex; align-items:center; gap:8px; justify-content:space-between; }
  .device-result-head .serial { direction:ltr; font:700 12px Consolas,monospace; }
  .device-result-text { color:var(--muted); font-size:12px; line-height:1.8; white-space:pre-line; word-break:break-word; margin-top:7px; }
  .device-result-time { font-size:10.5px; color:var(--muted); direction:ltr; margin-top:6px; }
  @media(max-width:620px){.inquiry-picker{grid-template-columns:1fr}.device-result-head{align-items:flex-start;flex-direction:column}}
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
    --bg:#171a26; --card:#1f2333; --card2:#262b3f; --line:#363d57;
    --txt:#eef1fb; --muted:#aeb6d0; --brand:#7d8cff; --brand2:#7d8cff;
    --ok:#34b583; --danger:#ef6a6a; --warn:#e4a43a; --input:#1a1e2e;
    --shadow:0 1px 1px rgba(0,0,0,.14),0 18px 42px rgba(0,0,0,.16);
  }
  html { scrollbar-gutter:stable; }
  body { font-family:"Segoe UI",Tahoma,system-ui,sans-serif; background:var(--bg); font-size:14px; line-height:1.65; }
  button,input,textarea { font:inherit; }
  button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible { outline:2px solid var(--brand); outline-offset:2px; }
  .layout { min-height:100vh; background:var(--bg); }
  .sidebar { width:430px; padding:24px 18px; gap:12px; background:var(--card); border-inline-start:1px solid var(--line); overflow:hidden; }
  .side-sec-title { margin:0; font-size:12px; font-weight:700; letter-spacing:.01em; color:var(--muted); }
  .add-prof { padding:11px 14px; border-radius:9px; font-size:14px; font-weight:700; background:var(--txt); box-shadow:none; }
  html[data-theme="dark"] .add-prof { color:#1f2333; background:#f3f4f6; }
  .add-prof:hover { filter:none; transform:none; opacity:.88; }
  #profile-search { min-height:42px; padding:10px 38px 10px 32px; border-radius:9px; background:var(--card2); }
  #profile-search:focus { box-shadow:0 0 0 3px color-mix(in srgb,var(--brand) 16%,transparent); }
  .prof-item { padding:12px; margin-bottom:9px; border-radius:10px; background:var(--card); box-shadow:0 1px 1px rgba(16,24,40,.02); }
  .prof-item:hover { border-color:#c9cdd5; background:var(--card); }
  html[data-theme="dark"] .prof-item:hover { border-color:#3a3e47; }
  .prof-item.active { border-color:var(--brand); background:color-mix(in srgb,var(--brand) 7%,var(--card)); box-shadow:0 0 0 2px color-mix(in srgb,var(--brand) 14%,transparent); }
  .prof-title { gap:7px; font-size:13px; }
  .profile-number { min-width:22px; height:22px; border-radius:6px; background:var(--card2); border:1px solid var(--line); color:var(--muted); }
  .prof-avatar { cursor:pointer; }
  /* --- premium profile-card polish: skeleton rows + compact actions --- */
  .prof-skeleton { display:flex; align-items:center; gap:10px; padding:12px; margin-bottom:9px;
    border:1px solid var(--line); border-radius:10px; background:var(--card); }
  .prof-skeleton .sk-av, .prof-skeleton .sk-l1, .prof-skeleton .sk-l2 { border-radius:7px;
    background:linear-gradient(100deg,var(--card2) 40%,color-mix(in srgb,var(--brand) 8%,var(--card2)) 50%,var(--card2) 60%);
    background-size:220% 100%; animation:skpulse 1.3s linear infinite; }
  .prof-skeleton .sk-av { width:38px; height:38px; border-radius:50%; flex:0 0 auto; }
  .prof-skeleton .sk-lines { flex:1; min-width:0; }
  .prof-skeleton .sk-l1 { height:12px; width:55%; margin-bottom:7px; }
  .prof-skeleton .sk-l2 { height:9px; width:80%; }
  @keyframes skpulse { from{background-position:120% 0} to{background-position:-120% 0} }
  @media (prefers-reduced-motion: reduce){ .prof-skeleton .sk-av,.prof-skeleton .sk-l1,.prof-skeleton .sk-l2{animation:none} }
  .prof-actions { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
  .ibtn { position:relative; width:34px; height:34px; flex:0 0 auto; display:inline-flex;
    align-items:center; justify-content:center; font-size:15px; line-height:1; cursor:pointer;
    border:1px solid var(--line); border-radius:9px; background:var(--card); color:var(--muted);
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
  .prof-foot { margin-top:9px; }
  .prof-actions .ibtn:nth-child(n+4) { display:none; }
  .prof-item.expanded .prof-actions .ibtn:nth-child(n+4) { display:inline-flex; }
  .more-btn { display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px;
    border:1px solid var(--line); border-radius:9px; background:var(--card); color:var(--muted);
    cursor:pointer; font-size:14px; transition:.16s; }
  .more-btn:hover { color:var(--txt); border-color:var(--brand); }
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
  .system-close-btn { min-height:38px; display:inline-flex; align-items:center; gap:6px; padding:8px 13px; border-radius:8px; font-size:12px; font-weight:bold;
    border:1px solid color-mix(in srgb,var(--danger) 45%,var(--line)); background:color-mix(in srgb,var(--danger) 8%,var(--card)); color:var(--danger); cursor:pointer; transition:.15s; }
  .system-close-btn:hover { background:var(--danger); color:#fff; border-color:var(--danger); }
  .inquiry-actions { display:flex; gap:10px; align-items:stretch; flex-wrap:wrap; }
  .inquiry-actions .save { flex:1; min-width:170px; margin-top:0; }
  .inquiry-stop { flex:0 0 auto; min-width:150px; margin-top:0; padding:11px 18px; border-radius:8px; border:0; font-size:13px; font-weight:bold;
    background:var(--danger); color:#fff; cursor:pointer; font-family:inherit; }
  .inquiry-stop:disabled { opacity:.45; cursor:not-allowed; }
  .active-profile-banner { grid-column:1/-1; min-width:0; width:100%; padding:11px 14px; border:1px solid var(--line); border-radius:9px; background:var(--card); color:var(--txt); font-size:13px; font-weight:600; box-shadow:none; text-align:right; }
  .active-profile-banner:before { content:""; display:inline-block; width:7px; height:7px; margin-left:8px; border-radius:50%; background:var(--ok); }
  .stats { gap:8px; margin:0 0 14px; }
  .stat { min-height:78px; display:flex; align-items:center; justify-content:center; gap:3px; padding:12px 8px; border-radius:10px; background:var(--card); box-shadow:0 1px 2px rgba(16,24,40,.025); }
  .stat:hover { transform:none; border-color:#c9cdd5; }
  .stat.active { border-color:var(--brand); background:color-mix(in srgb,var(--brand) 6%,var(--card)); box-shadow:0 0 0 2px color-mix(in srgb,var(--brand) 12%,transparent); }
  .stat .n { font-size:22px; font-weight:700; }
  .stat .l { font-size:11px; }
  .nav-tabs { gap:4px; margin:0 0 18px; padding:4px; border:1px solid var(--line); border-radius:10px; background:var(--card); }
  .nav-tab { position:relative; min-height:38px; display:flex; align-items:center; justify-content:center; padding:8px 10px; border:0; border-radius:7px; background:transparent; font-size:12.5px; font-weight:600; }
  .nav-tab:hover { color:var(--txt); background:color-mix(in srgb,var(--brand) 10%,transparent); }
  /* tab-e ENTEKHAB-SHODE: rang-e brand-e por +noar-e paeen -> ja-ye fe'li roshan ast */
  .nav-tab.active {
    color:#fff; background:linear-gradient(135deg,var(--brand),var(--brand2));
    box-shadow:0 2px 10px color-mix(in srgb,var(--brand) 32%,transparent);
  }
  .nav-tab.active:hover { color:#fff; background:linear-gradient(135deg,var(--brand),var(--brand2)); filter:brightness(1.05); }
  .nav-tab.active::after {
    content:""; position:absolute; inset-inline:22%; bottom:-5px; height:3px;
    border-radius:3px; background:var(--brand);
  }
  #view-form,#view-logs,#view-telegram { padding:24px; border:1px solid var(--line); border-radius:12px; background:var(--card); box-shadow:var(--shadow); }
  .profile-editor-empty { min-height:360px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; padding:40px; text-align:center; color:var(--muted); }
  .profile-editor-empty .empty-icon { width:46px; height:46px; display:grid; place-items:center; margin-bottom:4px; border:1px solid var(--line); border-radius:12px; background:var(--card2); color:var(--brand); font-size:22px; }
  .profile-editor-empty strong { color:var(--txt); font-size:16px; font-weight:650; }
  .profile-editor-empty p { max-width:420px; margin:0; font-size:12.5px; line-height:1.8; }
  .profile-overview { display:grid; gap:18px; }
  .profile-overview-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:18px; border:1px solid var(--line); border-radius:10px; background:var(--card2); }
  .profile-overview-title { margin:0; font-size:20px; }
  .profile-overview-meta { margin-top:5px; color:var(--muted); font-size:12px; }
  .profile-overview-edit { border:1px solid var(--brand); background:transparent; color:var(--brand); border-radius:8px; padding:9px 13px; cursor:pointer; font:700 12px inherit; white-space:nowrap; }
  .readonly-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
  .readonly-field { min-height:76px; padding:12px 14px; border:1px solid var(--line); border-radius:9px; background:var(--card2); }
  .readonly-field.wide { grid-column:1/-1; }
  .readonly-label { display:block; color:var(--muted); font-size:10.5px; margin-bottom:7px; }
  .readonly-value { display:block; color:var(--txt); font-size:14px; font-weight:700; line-height:1.7; word-break:break-word; }
  .readonly-value.ltr { direction:ltr; text-align:right; font-family:Consolas,monospace; letter-spacing:0; }
  .readonly-imeis { display:flex; flex-wrap:wrap; gap:7px; }
  .readonly-imei { direction:ltr; padding:7px 10px; border:1px solid var(--line); border-radius:7px; background:var(--input); color:var(--txt); font:700 12px Consolas,monospace; }
  .readonly-flags { display:flex; flex-wrap:wrap; gap:7px; margin-top:2px; }
  .readonly-flag { padding:6px 9px; border-radius:7px; border:1px solid var(--line); color:var(--muted); font-size:11px; }
  .section-title { margin:0 0 14px; padding:0; border:0; background:none; color:var(--txt); font-size:17px; }
  .profile-tabs { max-width:520px; gap:4px; margin:0 0 22px; padding:4px; border-radius:9px; background:var(--card2); }
  .profile-tab { padding:8px 12px; border:0; border-radius:7px; background:transparent; font-size:12px; font-weight:600; }
  .profile-tab.active { color:#fff; background:linear-gradient(135deg,var(--brand),var(--brand2)); box-shadow:0 2px 8px color-mix(in srgb,var(--brand) 30%,transparent); }
  #view-base,#view-identity { display:block; max-width:920px; }
  #view-base > .section-title,#view-base > .profile-tabs,#view-base > #imeis,#view-base > .add-btn,#view-base > .switches,#view-identity > .section-title { width:100%; }
  label { margin:10px 0 5px; font-size:12px; font-weight:600; }
  input[type=text],textarea { padding:10px 12px; border-radius:8px; font-size:13px; background:var(--input); }
  input[type=text]:focus,textarea:focus { box-shadow:0 0 0 3px color-mix(in srgb,var(--brand) 15%,transparent); }
  .imei-row { margin-bottom:7px; }
  .field-row { gap:7px; }
  .paste-btn { min-width:44px; padding:0 10px; border-radius:8px; font-size:11.5px; }
  .paste-btn .paste-ico { font-size:13px; }
  .paste-btn .paste-label { font-size:11px; }
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
  .release-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:14px; padding:12px 14px;
    border:1px solid color-mix(in srgb,var(--danger) 35%,var(--line)); border-radius:10px;
    background:color-mix(in srgb,var(--danger) 6%,var(--card)); }
  .release-btn { background:var(--danger); color:#fff; border:0; border-radius:9px; padding:10px 16px;
    font-weight:700; cursor:pointer; }
  .release-btn:hover { filter:brightness(1.07); }
  .release-hint { color:var(--muted); font-size:11.5px; line-height:1.7; }
  .irica-version { display:inline-block; margin-top:6px; padding:2px 10px; border:1px solid var(--line);
    border-radius:999px; color:var(--muted); font-size:10.5px; direction:ltr; }
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
    #view-form,#view-logs,#view-telegram{padding:16px}.nav-tabs{overflow-x:auto;flex-wrap:nowrap}.nav-tab{min-width:120px}
    #view-base,#view-identity{grid-template-columns:1fr}.readonly-grid{grid-template-columns:1fr}.readonly-field.wide{grid-column:auto}.profile-overview-head{flex-direction:column}.switches{grid-template-columns:1fr}.home-btn span{display:none}
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
          <a href="/crm" class="home-btn" title="مدیریت مشتریان و پرونده‌ها" aria-label="مدیریت مشتریان و پرونده‌ها">👥 <span>CRM</span></a>
          <button type="button" id="theme-toggle" class="theme-btn" onclick="toggleTheme()">حالت تاریک</button>
          <button type="button" class="system-close-btn" onclick="closeWholeSystem()" title="همهٔ ربات‌ها متوقف و پنل خاموش می‌شود">⏻ بستن سامانه</button>
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
        <div class="irica-version">نسخه __VERSION__</div>
      </header>
      <div class="stats" id="stats" aria-label="خلاصه وضعیت پروفایل‌ها">
        <div class="stat s-total"   data-filter="all"     title="نمایش همه"><div class="n" id="st-total">۰</div><div class="l">کل</div></div>
        <div class="stat s-running" data-filter="running" title="فیلتر در حال اجرا"><div class="n" id="st-running">۰</div><div class="l">در حال اجرا</div></div>
        <div class="stat s-ready"   data-filter="ready"   title="فیلتر آماده اجرا"><div class="n" id="st-ready">۰</div><div class="l">آماده</div></div>
        <div class="stat s-success" data-filter="success" title="فیلتر ثبت موفق"><div class="n" id="st-success">۰</div><div class="l">موفق</div></div>
        <div class="stat s-blocked" data-filter="blocked" title="فیلتر مسدود"><div class="n" id="st-blocked">۰</div><div class="l">مسدود</div></div>
      </div>
      <div class="nav-tabs" id="main-tabs" role="tablist">
        <div class="nav-tab active" id="tab-form" onclick="viewProfile(activeId)">پروفایل انتخاب‌شده</div>
        <div class="nav-tab" id="tab-logs" onclick="showView('logs')">📋 لاگ‌ها</div>
        <div class="nav-tab" id="tab-telegram" onclick="showView('telegram')">⚙️ تنظیمات کلی</div>
      </div>
      <div id="view-form"></div>
      <div id="view-logs" class="hidden"></div>
      <div id="view-telegram" class="hidden"></div>
    </div>
  </div>
  <div class="sidebar">
    <div class="side-sec-title"><span>پروفایل‌ها</span><span style="display:inline-flex;align-items:center;gap:8px"><span id="profile-count"></span><span class="view-switch" role="group" aria-label="حالت نمایش لیست"><button type="button" id="vw-comfortable" aria-pressed="true" title="نمایش کارت کامل" onclick="setProfView('comfortable')">☰</button><button type="button" id="vw-compact" aria-pressed="false" title="نمایش فشرده" onclick="setProfView('compact')">≡</button></span></span></div>
    <button class="add-prof" onclick="newProfile()">+ پروفایل جدید</button>
    <div class="profile-search-wrap">
      <span class="profile-search-icon">🔎</span>
      <input id="profile-search" type="search" autocomplete="off" placeholder="جستجو: نام، کد ملی، موبایل یا سریال" oninput="filterProfiles(this.value)">
      <button type="button" id="profile-search-clear" class="search-clear" aria-label="پاک کردن جستجو" onclick="clearProfileSearch()">×</button>
    </div>
    <div id="prof-list"><div class="prof-skeleton" aria-hidden="true"><div class="sk-av"></div><div class="sk-lines"><div class="sk-l1"></div><div class="sk-l2"></div></div></div><div class="prof-skeleton" aria-hidden="true"><div class="sk-av"></div><div class="sk-lines"><div class="sk-l1"></div><div class="sk-l2"></div></div></div><div class="prof-skeleton" aria-hidden="true"><div class="sk-av"></div><div class="sk-lines"><div class="sk-l1"></div><div class="sk-l2"></div></div></div></div>
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
setInterval(function(){fetch('/state').then(function(r){return r.json();}).then(function(s){if(s&&s.error){return;}STORE=s;renderProfiles();}).catch(function(){});},3000);

function getProf(id){ return STORE.profiles.find(function(p){return p.id===id;}); }

function profileRunButton(p,extraClass){
  var cls='mini-btn '+(extraClass||'');
  if(p.registration_status==='blocked') return '<button class="'+cls+' blocked" disabled>⛔ غیرقابل ورود</button>';
  if(p.run_status==='busy') return '<button class="'+cls+' stop" onclick="stopProfile(\''+p.id+'\','+p.runner_pid+')">⏹ توقف ربات</button>';
  if(p.registration_status!=='success') return '<button class="'+cls+' start" onclick="startProfile(\''+p.id+'\')">▶ شروع به کار</button>';
  return '';
}

/* Dokme-ye namayesh/makhfi-kardan-e morurgar (faghat baraye profile-e dar-hal-e-ejra).
   Morurgar hamishe headed ast vali be tore pishfarz off-screen negah dashte mishavad. */
function profileBrowserButton(p){
  if(p.run_status!=='busy') return '';
  var visible=Boolean(p.browser_visible);
  return '<button type="button" class="browser-btn'+(visible?' visible':'')+'" '+
    'data-tip="'+(visible?'مرورگر روی صفحه است — کلیک: مخفی کردن برای صرفه‌جویی منابع':'مرورگر مخفی است — کلیک: نمایش برای مدیریت زنده')+'" '+
    'onclick="setBrowserVisible(\''+p.id+'\','+(!visible)+')">'+
    (visible?'🙈 مخفی کردن مرورگر':'👁 نمایش مرورگر')+'</button>';
}

function setBrowserVisible(id,visible){
  var buttons=document.querySelectorAll('[data-profile-id="'+id+'"] .browser-btn');
  buttons.forEach(function(b){b.disabled=true;});
  fetch('/browser-visibility',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id,visible:visible})})
  .then(function(r){return r.json().then(function(d){return {ok:r.ok,data:d};});})
  .then(function(res){
    if(!res.ok){alert(res.data.error||'تغییر وضعیت مرورگر انجام نشد');buttons.forEach(function(b){b.disabled=false;});return;}
    var p=getProf(id); if(p)p.browser_visible=Boolean(res.data.browser_visible);
    renderProfiles();
  })
  .catch(function(){buttons.forEach(function(b){b.disabled=false;});});
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
  el.classList.toggle('compact',profView==='compact');
  updateProfViewButtons();
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
  // ---- filter (status +search) ----
  var profiles=STORE.profiles.slice().reverse().filter(function(p){
    if(statusFilter!=='all' && statusOf(p).key!==statusFilter) return false;
    return profileMatchesSearch(p,query);
  });
  var count=document.getElementById('profile-count'); if(count)count.textContent=(profiles.length!==all.length)?(toFa(profiles.length)+' از '+toFa(all.length)):(toFa(all.length)+' مورد');
  if(!profiles.length){
    el.innerHTML=(all.length?'<div class="profiles-empty"><div class="pe-icon" aria-hidden="true">🔍</div>پروفایلی با این مشخصات پیدا نشد.<div class="pe-hint">عبارت جستجو یا فیلتر وضعیت را تغییر دهید.</div></div>'
                           :'<div class="profiles-empty"><div class="pe-icon" aria-hidden="true">👤</div>هنوز پروفایلی ثبت نشده است.<div class="pe-hint">با دکمه «+پروفایل جدید» اولین پروفایل را بسازید.</div></div>');
    return;
  }
  profiles.forEach(function(p){
    var isActive=(p.id===activeId);
    var profileNumber=STORE.profiles.indexOf(p)+1;
    var st=statusOf(p);
    var imei=(p.imeis&&p.imeis[0])||'';
    var d=document.createElement('div');
    d.className='prof-item'+(isActive?' active':'');
    d.setAttribute('data-profile-id',p.id);
    // stable anatomy: avatar+identity+side(status,run) -> alerts -> footer(artifacts) -> actions
    var artifacts=(p.receipt_screenshot?'<a target="_blank" href="/artifact?id='+encodeURIComponent(p.id)+'&kind=receipt">🧾 رسید</a>':'')+
      (p.payment_screenshot?'<a target="_blank" href="/artifact?id='+encodeURIComponent(p.id)+'&kind=payment">💳 پرداخت</a>':'')+
      (p.payment_link?'<a target="_blank" href="'+escHtml(p.payment_link)+'">🔗 لینک پرداخت</a>':'');
    var alerts=
      (p.registration_status==='blocked'?'<div class="prof-alert danger"><span class="ai">⛔</span><div><b>غیرقابل ورود — تلاش متوقف شد</b><div class="ad">'+escHtml(p.runtime_error||'خطای غیرقابل ورود ثبت شده است')+'</div></div></div>':'')+
      (p.registration_status==='success'?'<div class="prof-alert ok"><span class="ai">✓</span><div><b>موفق — مرحله پرداخت</b></div></div>':'');
    var browserRow=(p.run_status==='busy'?'<div class="prof-browser-row">'+profileBrowserButton(p)+'</div>':'');
    d.innerHTML=
      '<div class="prof-head">'+
        '<div class="prof-avatar" style="--av:'+avatarColorOf(p)+'" aria-hidden="true" onclick="viewProfile(\''+p.id+'\')">'+escHtml(initialsOf(p))+'</div>'+
        '<div class="prof-id">'+
          '<div class="prof-title"><span class="profile-number" title="شماره پروفایل">'+toFa(profileNumber)+'</span>'+
          '<span class="prof-title-text" onclick="viewProfile(\''+p.id+'\')" title="'+escHtml(p.title||p.national||'بدون عنوان')+'">'+escHtml(p.title||p.national||'بدون عنوان')+'</span></div>'+
          '<div class="prof-meta">'+escHtml(p.national||'—')+(p.mobile?'<span class="mdot"> • </span>'+escHtml(p.mobile):'')+
          (imei?'<span class="mdot"> • </span><span class="imei-chip" title="سریال اول: '+escHtml(imei)+'">'+escHtml(imei)+'</span>':'')+'</div>'+
        '</div>'+
        '<div class="prof-side"><span class="p-badge '+st.cls+'"><span class="d"></span>'+st.label+'</span>'+profileRunButton(p,'profile-run-primary')+'</div>'+
      '</div>'+
      alerts+browserRow+
      (artifacts?'<div class="prof-foot"><div class="artifact-links">'+artifacts+'</div></div>':'')+
      '<div class="prof-actions">'+
        '<button class="ibtn'+(p.enabled?' on':'')+'" data-tip="'+(p.enabled?'فعال است — کلیک: غیرفعال کردن':'غیرفعال است — کلیک: فعال کردن')+'" '+
          'onclick="toggleProfile(\''+p.id+'\','+(!p.enabled)+')">'+(p.enabled?'🟢':'⚪')+'</button>'+
        '<button class="ibtn'+(isActive?' on':'')+'" data-tip="'+(isActive?'پروفایل فعال جاری':'انتخاب به‌عنوان پروفایل فعال')+'" '+
          'onclick="activateProfile(\''+p.id+'\')">'+(isActive?'📌':'🎯')+'</button>'+
        '<button class="ibtn" data-tip="مشاهده اطلاعات پروفایل" onclick="viewProfile(\''+p.id+'\')">◉</button>'+
        '<button class="ibtn" data-tip="ویرایش اطلاعات پروفایل" onclick="editProfile(\''+p.id+'\')">✏️</button>'+
        '<button class="ibtn" data-tip="کپی گرفتن از این پروفایل" onclick="cloneProfile(\''+p.id+'\')">📋</button>'+
        '<button class="ibtn danger" data-tip="حذف کامل این پروفایل" onclick="deleteProfile(\''+p.id+'\')">🗑</button>'+
        '<button type="button" class="more-btn" data-tip="اکشن‌های بیشتر" aria-expanded="false" onclick="toggleCardMore(this)">⋯</button>'+
      '</div>';
    el.appendChild(d);
  });
}

/* avatar: رنگ پایدار از کدملی/نام + حروف اول نام */
function avatarColorOf(p){
  var s=String(p.national||p.title||p.id||'x');
  var h=0; for(var i=0;i<s.length;i++){h=(h*31+s.charCodeAt(i))>>>0;}
  return h%360;
}
function initialsOf(p){
  var t=String(p.title||'').trim();
  if(t)return t.slice(0,2);
  var n=String(p.national||'').trim();
  return n?n.slice(0,2):'؟';
}
/* progressive disclosure: ویرایش/کپی/حذف پشت دکمه ⋯ */
function toggleCardMore(btn){
  var card=btn.closest('.prof-item'); if(!card)return;
  card.classList.toggle('expanded');
  var open=card.classList.contains('expanded');
  btn.textContent=open?'×':'⋯';
  btn.setAttribute('aria-expanded',open?'true':'false');
  btn.setAttribute('data-tip',open?'بستن اکشن‌ها':'اکشن‌های بیشتر');
}
/* حالت نمایش لیست: کارت کامل یا فشرده (persist) */
var profView=localStorage.getItem('irica-profview')||'comfortable';
function setProfView(v){
  profView=(v==='compact')?'compact':'comfortable';
  localStorage.setItem('irica-profview',profView);
  renderProfiles();
}
function updateProfViewButtons(){
  ['vw-comfortable','vw-compact'].forEach(function(id){
    var b=document.getElementById(id); if(!b)return;
    var on=(id==='vw-compact')===(profView==='compact');
    b.classList.toggle('active',on);
    b.setAttribute('aria-pressed',on?'true':'false');
  });
}

function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

/* ---- paste-e kenar-e har kadar-e vorudi ---------------------------------- */
function withPaste(inputHtml){
  return '<div class="field-row">'+inputHtml+
    '<button type="button" class="paste-btn" title="پیست از کلیپ‌بورد" aria-label="پیست کردن در این کادر" onclick="pasteInto(this)">'+
    '<span class="paste-ico" aria-hidden="true">📋</span><span class="paste-label">پیست</span></button></div>';
}

/* Text-e clipboard ra tamiz mikonad: raqam-haye farsi/arabi -> ASCII,
   hazf-e mark-haye jahat/ZWNJ va faseleha (baraye field-haye adadi-ye .ltr). */
function normalizePasted(text,isNumericField){
  var out=String(text==null?'':text).replace(/[\u200b-\u200f\u202a-\u202e\ufeff]/g,'');
  out=out.replace(/[۰-۹]/g,function(c){return String('۰۱۲۳۴۵۶۷۸۹'.indexOf(c));})
         .replace(/[٠-٩]/g,function(c){return String('٠١٢٣٤٥٦٧٨٩'.indexOf(c));});
  out=out.replace(/[\r\n\t]+/g,' ').trim();
  if(isNumericField) out=out.replace(/\s+/g,'');
  return out;
}

function flashPasteBtn(btn,cls,label){
  var labelEl=btn.querySelector('.paste-label'); var previous=labelEl?labelEl.textContent:'';
  btn.classList.remove('ok','err'); btn.classList.add(cls);
  if(labelEl&&label)labelEl.textContent=label;
  setTimeout(function(){btn.classList.remove(cls); if(labelEl&&label)labelEl.textContent=previous;},1400);
}

function applyPastedValue(input,btn,raw){
  var isNumeric=input.classList.contains('ltr');
  var value=normalizePasted(raw,isNumeric);
  if(!value){ flashPasteBtn(btn,'err','خالی'); return; }
  input.value=value;
  input.dispatchEvent(new Event('input',{bubbles:true}));
  input.focus();
  flashPasteBtn(btn,'ok','✓');
}

function pasteInto(btn){
  var row=btn.closest('.field-row')||btn.parentNode;
  var input=row?row.querySelector('input[type=text]'):null;
  if(!input)return;
  if(!navigator.clipboard||!navigator.clipboard.readText){
    input.focus(); input.select();
    btn.title='مرورگر اجازه خواندن کلیپ‌بورد نمی‌دهد — با Ctrl+V پیست کنید';
    flashPasteBtn(btn,'err','Ctrl+V'); return;
  }
  navigator.clipboard.readText().then(function(text){
    applyPastedValue(input,btn,text);
  }).catch(function(){
    input.focus(); input.select();
    btn.title='دسترسی به کلیپ‌بورد رد شد — با Ctrl+V پیست کنید';
    flashPasteBtn(btn,'err','Ctrl+V');
  });
}

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

function readonlyField(label,value,cls,wide){
  return '<div class="readonly-field'+(wide?' wide':'')+'"><span class="readonly-label">'+escHtml(label)+'</span><span class="readonly-value '+(cls||'')+'">'+escHtml(value||'ثبت نشده')+'</span></div>';
}
function buildProfileOverview(p){
  var imeis=(p.imeis||[]).map(function(v){return '<span class="readonly-imei">'+escHtml(v)+'</span>';}).join('')||'<span class="readonly-value">سریالی ثبت نشده است</span>';
  return '<div class="profile-overview">'+
    '<div class="profile-overview-head"><div><h2 class="profile-overview-title">'+escHtml(p.title||p.national||'بدون عنوان')+'</h2><div class="profile-overview-meta">نمایش اطلاعات ثبت‌شده این پروفایل</div></div><button type="button" class="profile-overview-edit" onclick="editProfile(\''+p.id+'\')">✏️ ویرایش اطلاعات</button></div>'+
    '<div class="profile-tabs">'+
      '<button type="button" aria-label="تب اطلاعات پایه" class="profile-tab active" onclick="showProfileSection(\'base\')">اطلاعات پایه</button>'+
      '<button type="button" aria-label="تب اطلاعات هویتی" class="profile-tab" onclick="showProfileSection(\'identity\')">اطلاعات هویتی</button>'+
      '<button type="button" aria-label="تب استعلامات" class="profile-tab" onclick="showProfileSection(\'inquiries\')">استعلامات</button>'+
    '</div>'+
    '<div id="view-base"><div class="readonly-grid">'+
      readonlyField('عنوان پروفایل',p.title||'')+readonlyField('کد ملی',p.national||'','ltr')+
      readonlyField('تاریخ تولد',p.birth||'','ltr')+readonlyField('شماره موبایل',p.mobile||'','ltr')+
      '<div class="readonly-field wide"><span class="readonly-label">سریال‌های IMEI</span><div class="readonly-imeis">'+imeis+'</div></div>'+
    '</div><div class="readonly-flags"><span class="readonly-flag">'+(p.no_confirm_check?'تأیید خودکار خاموش':'تأیید خودکار روشن')+'</span><span class="readonly-flag">'+(p.debugging?'حالت دیباگ روشن':'حالت دیباگ خاموش')+'</span><span class="readonly-flag">'+(p.manual_manage?'مدیریت دستی روشن':'مدیریت دستی خاموش')+'</span></div></div>'+
    '<div id="view-identity" class="hidden"><div class="section-title">اطلاعات هویتی</div><div class="readonly-grid">'+
      readonlyField('نام',p.first_name||'')+readonlyField('نام خانوادگی',p.last_name||'')+
      readonlyField('نام پدر',p.father_name||'')+readonlyField('شماره شناسنامه',p.birth_certificate_no||'','ltr')+
      readonlyField('کد ملی',p.national||'','ltr')+readonlyField('تاریخ تولد',p.birth||'','ltr')+
    '</div></div>'+
    '<div id="view-profile-inquiries" class="hidden">'+buildDeviceInquiryPanel(p)+'<div class="inquiry-card"><h3>استعلام ممنوع‌الخروجی</h3>'+buildExitbanResult(p)+'<button type="button" class="save" onclick="runExitbanInquiry(activeId)">استعلام آنلاین خروج از کشور</button><div id="exitban-status" class="hint"></div></div><div class="inquiry-card"><h3>استعلام اقساط و وام پرداخت‌نشده</h3><span class="soon">به‌زودی</span></div></div>'+
  '</div>';
}
function viewProfile(id){
  activeId=id;
  var p=getProf(id); if(!p){renderProfileEditorEmpty();return;}
  document.getElementById('view-form').innerHTML=buildProfileOverview(p);
  setActiveBanner(p,'پروفایل انتخاب‌شده');
  renderProfiles();
  showView('form');
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
    withPaste('<input type="text" name="title" value="'+escHtml(p.title||'')+'" placeholder="مثلاً علی رضایی">')+
    '<label>کد ملی</label>'+
    withPaste('<input type="text" name="national" class="ltr" value="'+escHtml(p.national||'')+'" placeholder="۱۰ رقم" oninput="syncSharedField(\'identity_national\',this.value)">')+
    '<label>تاریخ تولد (شمسی)</label>'+
    withPaste('<input type="text" name="birth" class="ltr" value="'+escHtml(p.birth||'')+'" placeholder="۱۳۶۶/۱۰/۱" oninput="syncSharedField(\'identity_birth\',this.value)">')+
    '<label>شماره موبایل</label>'+
    withPaste('<input type="text" name="mobile" class="ltr" value="'+escHtml(p.mobile||'')+'" placeholder="۰۹۱۴...">')+
    '<label>کدهای IMEI</label>'+
    '<div id="imeis"></div>'+
    '<button type="button" class="add-btn" onclick="addImei(\'\')">+افزودن IMEI</button>'+
    '<div class="switches">'+
      '<div class="switch"><div><div class="t">زدن تیک «قبول دارم»</div><div class="d">اگر خاموش باشد تیک زده نمی‌شود</div></div>'+
        '<label class="toggle"><input type="checkbox" id="confirm_on"'+(p.no_confirm_check?'':' checked')+'><span class="track"></span></label></div>'+
      '<div class="switch"><div><div class="t">🐞 حالت دیباگ</div><div class="d">قبل از هر مرحله مکث می‌کند</div></div>'+
        '<label class="toggle"><input type="checkbox" id="debug_on"'+(p.debugging?' checked':'')+'><span class="track"></span></label></div>'+
      '<div class="switch"><div><div class="t">✋ مدیریت دستی</div><div class="d">صفحه اول باز می‌شود؛ خودت تا کد ۴رقمی برو، تیک بزن، دکمه بالای صفحه را بزن → brute-force</div></div>'+
        '<label class="toggle"><input type="checkbox" id="manual_on"'+(p.manual_manage?' checked':'')+'><span class="track"></span></label></div>'+
    '</div>'+
    '</div>'+
    '<div id="view-identity" class="hidden">'+
    '<div class="section-title">اطلاعات هویتی</div>'+
    '<label>نام</label>'+withPaste('<input type="text" name="first_name" value="'+escHtml(p.first_name||'')+'">')+
    '<label>نام خانوادگی</label>'+withPaste('<input type="text" name="last_name" value="'+escHtml(p.last_name||'')+'">')+
    '<label>نام پدر</label>'+withPaste('<input type="text" name="father_name" value="'+escHtml(p.father_name||'')+'">')+
    '<label>تاریخ تولد</label>'+withPaste('<input type="text" name="identity_birth" class="ltr" value="'+escHtml(p.birth||'')+'" oninput="syncSharedField(\'birth\',this.value)">')+
    '<label>شماره شناسنامه</label>'+withPaste('<input type="text" name="birth_certificate_no" class="ltr" value="'+escHtml(p.birth_certificate_no||'')+'">')+
    '<label>کد ملی</label>'+withPaste('<input type="text" name="identity_national" class="ltr" value="'+escHtml(p.national||'')+'" oninput="syncSharedField(\'national\',this.value)">')+
    '</div>'+
    '<div id="view-profile-inquiries" class="hidden">'+
      buildDeviceInquiryPanel(p)+
      '<div class="inquiry-card"><h3>استعلام ممنوع‌الخروجی</h3>'+buildExitbanResult(p)+'<button type="button" class="save" onclick="runExitbanInquiry(activeId)">استعلام آنلاین خروج از کشور</button><div id="exitban-status" class="hint"></div></div>'+
      '<div class="inquiry-card"><h3>استعلام اقساط و وام پرداخت‌نشده</h3><span class="soon">به‌زودی</span></div>'+
    '</div>'+
    (p.registration_status==='blocked'?'<div class="release-row"><button type="button" class="release-btn" onclick="releaseBlockedProfile(activeId)">🔓 آزادسازی و شروع مجدد</button><span class="release-hint">پروفایل آزاد می‌شود و بعد از ذخیرهٔ ویرایش می‌توانید دوباره ربات را اجرا کنید</span></div>':'')+
    '<button type="submit" class="save">💾 ذخیره پروفایل</button>'+
    '<div class="toast" id="toast"></div>'+
  '</form>';
}

function releaseBlockedProfile(id){
  if(!confirm('آزادسازی این پروفایل و پاک شدن دلیل مسدودی؟')) return;
  fetch('/release-blocked',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
    .then(function(r){return r.json().then(function(data){return {ok:r.ok,data:data};});})
    .then(function(res){
      if(!res.ok){alert(res.data.message||res.data.error||'آزادسازی انجام نشد');return;}
      STORE=res.data.store||STORE;
      if(res.data.store){STORE=res.data.store;activeId=STORE.active_id;}
      var t=document.getElementById('toast');
      if(t){t.style.color='var(--ok)';t.textContent='✓ '+res.data.message;t.classList.add('show');
        setTimeout(function(){t.classList.remove('show');},3500);}
      renderProfiles();
      var p=getProf(id); if(p){editProfile(id);}
    })
    .catch(function(){alert('ارتباط با پنل برقرار نشد');});
}

function addImei(val){
  var wrap=document.getElementById('imeis');
  var row=document.createElement('div'); row.className='imei-row';
  row.innerHTML='<input type="text" class="ltr" placeholder="۱۵ رقم" value="'+escHtml(val||'')+'">'+
    '<button type="button" class="paste-btn" title="پیست از کلیپ‌بورد" aria-label="پیست کردن در این کادر" onclick="pasteInto(this)"><span class="paste-ico" aria-hidden="true">📋</span><span class="paste-label">پیست</span></button>'+
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
    if(p) viewProfile(activeId);
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
    if(id===activeId||!getProf(activeId)) viewProfile(res.active_id);
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
  var mode=arguments[0]||logMode;
  logMode=mode;
  el.innerHTML='<div class="nav-tabs"><div class="nav-tab'+(mode!=='all'?' active':'')+'" id="log-tab-profile" onclick="renderLogs(\'profile\')">لاگ پروفایل</div><div class="nav-tab'+(mode==='all'?' active':'')+'" id="log-tab-all" onclick="renderLogs(\'all\')">لاگ کلی</div></div><div id="log-list"></div>';
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

var PROFILE_INQUIRY_SERVICE='hamta';
var PROFILE_INQUIRY_FA={hamta:'استعلام همتا',gomrok:'استعلام گمرک',all:'استعلام گمرک و همتا'};
var PROFILE_RESULT_FA={not_checked:'انجام نشده',running:'در حال استعلام',complete:'تکمیل شده',error:'نیازمند بررسی',healthy:'قابل ثبت در گمرک',good:'فاقد ثبت قانونی در شبکه',registered:'قانونی ثبت شده',problem:'مسدود/خطر',advanced:'مسدود/خطر',blocked:'مسدود/خطر'};

function profileResultClass(status){
  if(status==='running'||status==='not_checked')return 'running';
  return ['healthy','good','registered','complete'].indexOf(status)>=0?'good':'bad';
}
function profileInquiryLabel(p,service){
  var svc=service==='hamta'?'همتا':service==='gomrok'?'گمرک':'گمرک و همتا';
  return (p.title||p.national||'بدون عنوان')+' — '+svc;
}
function buildServiceResults(p,service){
  var state=p[service+'_inquiry_status']||'not_checked';
  var rows=p[service+'_inquiry_results']||[];
  var error=p[service+'_inquiry_error']||'';
  if(state==='not_checked'&&!rows.length)return '<div class="device-result running"><strong>'+profileInquiryLabel(p,service)+'</strong><div class="device-result-text">هنوز استعلامی برای سریال‌های این پروفایل ثبت نشده است.</div></div>';
  if(state==='running'&&!rows.length)return '<div class="device-result running"><strong>در حال استعلام '+profileInquiryLabel(p,service)+'</strong><div class="device-result-text">نتیجه هر سریال پس از پایان ربات در همین بخش نمایش داده می‌شود.</div></div>';
  var html=rows.map(function(row){
    var st=row.status||'error';
    var serialDisplay = escHtml(row.serial||'--');
    if (row.serial2) {
        serialDisplay += ' / ' + escHtml(row.serial2);
    }
    return '<div class="device-result '+profileResultClass(st)+'"><div class="device-result-head"><span class="serial">'+serialDisplay+'</span><strong>'+(PROFILE_RESULT_FA[st]||escHtml(st))+'</strong></div><div class="device-result-profile">👤 پروفایل: '+escHtml(p.title||p.national||'بدون عنوان')+'</div><div class="device-result-text">'+escHtml(row.result||'بدون توضیح')+'</div>'+(row.checked_at?'<div class="device-result-time">'+escHtml(row.checked_at)+'</div>':'')+'</div>';
  }).join('');
  if(error)html+='<div class="device-result bad"><strong>خطای اجرا — '+profileInquiryLabel(p,service)+'</strong><div class="device-result-text">'+escHtml(error)+'</div></div>';
  return html;
}
function buildDeviceInquiryPanel(p){
  var service=PROFILE_INQUIRY_SERVICE;
  var results=service==='all'?buildServiceResults(p,'gomrok')+buildServiceResults(p,'hamta'):buildServiceResults(p,service);
  var running=p.device_inquiry_pid&&((p.gomrok_inquiry_status==='running')||(p.hamta_inquiry_status==='running'));
  return '<div class="inquiry-card" id="device-inquiry-card"><h3>استعلام وضعیت دستگاه‌ها'+(p?' — '+profileInquiryLabel(p,service):'')+'</h3><p class="hint">سریال‌های ذخیره‌شده در همین پروفایل بررسی می‌شوند.</p><div class="inquiry-picker" role="group" aria-label="نوع استعلام دستگاه"><button type="button" class="'+(service==='hamta'?'active':'')+'" onclick="selectProfileInquiry(\'hamta\')">استعلام همتا</button><button type="button" class="'+(service==='gomrok'?'active':'')+'" onclick="selectProfileInquiry(\'gomrok\')">استعلام گمرک</button><button type="button" class="'+(service==='all'?'active':'')+'" onclick="selectProfileInquiry(\'all\')">استعلام گمرک و همتا</button></div><div class="device-results">'+results+'</div><div class="inquiry-actions"><button type="button" class="save" id="profile-inquiry-start" '+(running?'disabled':'')+' onclick="runProfileDeviceInquiry(activeId)">'+(running?'در حال استعلام…':PROFILE_INQUIRY_FA[service])+'</button><button type="button" class="inquiry-stop" id="profile-inquiry-stop" '+(running?'':'disabled')+' onclick="stopProfileDeviceInquiry(activeId)">⛔ توقف استعلام</button></div><div id="profile-inquiry-status" class="hint"></div></div>';
}
function selectProfileInquiry(service){
  PROFILE_INQUIRY_SERVICE=service;
  var p=getProf(activeId)||{};
  var card=document.getElementById('device-inquiry-card');
  if(card){var wrap=document.createElement('div');wrap.innerHTML=buildDeviceInquiryPanel(p);card.replaceWith(wrap.firstChild);}
}

function refreshProfileDeviceInquiry(id,startedAt){
  fetch('/state',{cache:'no-store'}).then(function(r){return r.json();}).then(function(store){
    if(store.error)return;
    STORE=store; var p=getProf(id); if(!p)return;
    var card=document.getElementById('device-inquiry-card');
    if(card){var wrap=document.createElement('div');wrap.innerHTML=buildDeviceInquiryPanel(p);card.replaceWith(wrap.firstChild);}
    var running=p.device_inquiry_pid&&((p.gomrok_inquiry_status==='running')||(p.hamta_inquiry_status==='running'));
    if(running&&Date.now()-startedAt<900000)setTimeout(function(){refreshProfileDeviceInquiry(id,startedAt);},1500);
  }).catch(function(){if(Date.now()-startedAt<900000)setTimeout(function(){refreshProfileDeviceInquiry(id,startedAt);},2500);});
}
function runProfileDeviceInquiry(id){
  var status=document.getElementById('profile-inquiry-status');
  if(status)status.textContent='در حال آماده‌سازی استعلام سریال‌های پروفایل…';
  fetch('/profile-device-inquiry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,service:PROFILE_INQUIRY_SERVICE})})
    .then(function(r){return r.json().then(function(data){return {ok:r.ok,data:data};});})
    .then(function(res){
      if(!res.ok){if(status)status.textContent=(res.data.error||'شروع استعلام انجام نشد')+(res.data.missing?' — '+res.data.missing.join('، '):'');return;}
      if(status)status.textContent='استعلام شروع شد؛ نتایج در همین بخش به‌روز می‌شوند.';
      refreshProfileDeviceInquiry(id,Date.now());
    }).catch(function(){if(status)status.textContent='خطا در ارتباط با پنل';});
}

function stopProfileDeviceInquiry(id){
  var status=document.getElementById('profile-inquiry-status');
  if(!confirm('استعلام این پروفایل متوقف شود؟ مرورگر و فرآیند استعلام بسته می‌شوند.'))return;
  if(status)status.textContent='در حال توقف استعلام…';
  fetch('/profile-device-stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})})
    .then(function(r){return r.json().then(function(data){return {ok:r.ok,data:data};});})
    .then(function(res){
      if(!res.ok){if(status)status.textContent=res.data.message||res.data.error||'توقف استعلام انجام نشد';return;}
      if(status)status.textContent=res.data.message||'استعلام متوقف شد.';
      STORE=res.data.store||STORE;
      renderProfiles();
      var p=getProf(id);
      if(p){var card=document.getElementById('device-inquiry-card');
        if(card){var wrap=document.createElement('div');wrap.innerHTML=buildDeviceInquiryPanel(p);card.replaceWith(wrap.firstChild);}}
    }).catch(function(){if(status)status.textContent='خطا در ارتباط با پنل';});
}

function closeWholeSystem(){
  if(!confirm('کل سامانه بسته شود؟ همهٔ ربات‌ها و استعلام‌های در حال اجرا متوقف می‌شوند و پنل خاموش می‌شود.'))return;
  fetch('/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
    .catch(function(){});
  var banner=document.createElement('div');
  banner.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9999;background:#b91c1c;color:#fff;'+
    'padding:14px;text-align:center;font-family:Tahoma,sans-serif;font-size:14px;font-weight:bold';
  banner.textContent='در حال بستن سامانه… همهٔ ربات‌ها متوقف شدند؛ این صفحه تا چند ثانیه دیگر در دسترس نخواهد بود.';
  document.body.appendChild(banner);
  setTimeout(function(){
    banner.textContent='سامانه بسته شد. برای اجرای مجدد، میان‌بر IRICA را باز کنید.';
    setTimeout(function(){location.reload();},4000);
  },1500);
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

var SYSTEM_DEBUG={enabled:false};
var SYSTEM_LOGS=[];
function renderSystemLogs(){
  var list=document.getElementById('system-log-list'); if(!list)return;
  if(!SYSTEM_LOGS.length){list.innerHTML='<div class="hint">هنوز لاگ سیستمی ساخته نشده است.</div>';return;}
  list.innerHTML=SYSTEM_LOGS.map(function(item){return '<div class="system-log-item"><span class="system-log-name">'+escHtml(item.name)+'</span><span class="system-log-meta">'+escHtml(item.modified)+'</span><button type="button" class="system-log-open" onclick="openSystemLog(\''+escHtml(item.name)+'\')">مشاهده</button></div>';}).join('');
}
function loadSystemLogs(){
  fetch('/system-logs',{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){SYSTEM_LOGS=data.logs||[];renderSystemLogs();}).catch(function(){});
}
function openSystemLog(name){
  fetch('/system-log?name='+encodeURIComponent(name),{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){
    var viewer=document.getElementById('system-log-viewer'); if(viewer)viewer.textContent=data.content||data.error||'لاگ خالی است';
  }).catch(function(){var viewer=document.getElementById('system-log-viewer');if(viewer)viewer.textContent='خطا در خواندن لاگ';});
}
function toggleSystemDebug(enabled){
  fetch('/system-debug-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enabled})}).then(function(r){return r.json();}).then(function(data){SYSTEM_DEBUG=data;renderTelegram();});
}
function renderTelegram(){
  var tokenState = TELEGRAM.token_set
    ? '<span class="token-badge ok">🔒 توکن ذخیره شده و محافظت‌شده است</span>'
    : '<span class="token-badge warn">⚠ توکنی ثبت نشده است</span>';
  document.getElementById('view-telegram').innerHTML='<div class="settings-section"><h3>تنظیمات کلی</h3><p class="hint">تنظیمات عمومی پنل و ربات در این بخش قرار می‌گیرد.</p></div>'+
    '<div class="settings-section system-debug-card"><div class="system-debug-row"><div><h3>Debug log سیستمی</h3><div class="system-debug-help">با فعال‌سازی، هر اجرای ربات یک فایل مستقل و تاریخ‌دار در پوشه logs/system داخل پوشه‌ی داده می‌سازد.</div></div><label class="toggle"><input id="system-debug-toggle" type="checkbox" '+(SYSTEM_DEBUG.enabled?'checked':'')+' onchange="toggleSystemDebug(this.checked)"><span class="track"></span></label></div><div class="system-debug-row"><strong>لاگ‌های موجود</strong><button type="button" class="system-log-open" onclick="loadSystemLogs()">به‌روزرسانی فهرست</button></div><div id="system-log-list" class="system-log-list"></div><pre id="system-log-viewer" class="system-log-viewer">یک فایل لاگ را برای مشاهده انتخاب کنید.</pre></div>'+
    '<label>API Base</label><input id="tg_api" type="text" class="ltr" value="'+escHtml(TELEGRAM.api_base||'https://api.telegram.org')+'">'+
    '<label>توکن ربات</label>'+tokenState+
    '<input id="tg_token" type="password" class="ltr" autocomplete="off" placeholder="'+(TELEGRAM.token_set?'برای تغییر، توکن جدید را وارد کنید (خالی = بدون تغییر)':'توکن ربات را وارد کنید')+'">'+
    '<label>Chat IDها (هر کدام یک خط)</label><textarea id="tg_ids" class="ltr">'+escHtml((TELEGRAM.chat_ids||[]).join('\n'))+'</textarea>'+
    '<button class="save" onclick="saveTelegram()">💾 ذخیره تنظیمات تلگرام</button><div class="toast" id="tg_toast"></div>'+
    '<p class="hint">این تنظیمات بیرون از پوشه‌ی برنامه ذخیره می‌شوند، پس با نصب نسخه‌ی جدید پاک نمی‌شوند و لازم نیست دوباره وارد شوند.</p>';
  renderSystemLogs();
  loadSystemLogs();
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
  document.getElementById('view-telegram').classList.toggle('hidden',v!=='telegram');
  document.getElementById('tab-form').classList.toggle('active',v==='form');
  document.getElementById('tab-logs').classList.toggle('active',v==='logs');
  document.getElementById('tab-telegram').classList.toggle('active',v==='telegram');
  if(v==='logs') renderLogs();
  if(v==='telegram'){
    fetch('/system-debug-config',{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){SYSTEM_DEBUG=data;renderTelegram();}).catch(function(){renderTelegram();});
  }
}

// init
var statsBar=document.getElementById('stats');
if(statsBar) statsBar.addEventListener('click',function(e){
  var tile=e.target.closest('.stat'); if(tile) setStatusFilter(tile.getAttribute('data-filter'));
});
renderProfiles();
updateThemeButton();
refreshApiHealth(false);
if(getProf(activeId)) viewProfile(activeId); else renderProfileEditorEmpty();
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
    version = app_version_text() or "—"
    head = PAGE_HEAD.replace("__VERSION__", version)
    tail = PAGE_TAIL.replace("__VERSION__", version)
    html = head + js + tail
    return html.encode("utf-8")


HOME_PAGE = """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>انتخاب سامانه گمرک</title><style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px 16px;
  background:radial-gradient(1100px 560px at 82% -12%,#25386b 0,transparent 60%),radial-gradient(820px 480px at 6% 4%,#31245c 0,transparent 55%),#131830;
  color:#eef2ff;font-family:Tahoma,"Segoe UI",sans-serif}
.wrap{width:min(960px,94vw)}
.top{text-align:center;margin-bottom:26px}
h1{font-size:34px;margin:0 0 8px}.sub{color:#9ca8c7;margin:0}
/* live stats strip */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 auto 30px;max-width:640px}
.stat{background:#1c2540;border:1px solid #28385a;border-radius:16px;padding:14px 10px;text-align:center}
.stat .n{font-size:26px;font-weight:900;line-height:1.1}
.stat .l{font-size:11.5px;color:#98a8ca;margin-top:3px}
.stat.total .n{color:#6f83ff}.stat.running .n{color:#f2b34a}.stat.success .n{color:#1fb883}.stat.blocked .n{color:#ef5b6b}
.sites{display:grid;grid-template-columns:repeat(2,1fr);gap:22px}
.site{position:relative;overflow:hidden;display:block;min-height:250px;padding:28px;border:1px solid #38445f;border-radius:24px;color:inherit;text-decoration:none;background:linear-gradient(145deg,#202a44,#1a2338);box-shadow:0 24px 70px #0007;transition:.2s}
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
.irica-version{margin-top:10px;font-size:11.5px;color:#6c7c9e;border:1px solid #ffffff1a;border-radius:999px;padding:3px 12px;display:inline-block}
@media(max-width:700px){.sites{grid-template-columns:1fr}.site{min-height:200px}.stats{grid-template-columns:repeat(2,1fr)}h1{font-size:27px}}
</style></head><body><main class="wrap">
<div class="top"><h1>سامانه مدیریت گمرک</h1><p class="sub">سامانه موردنظر را انتخاب کنید</p><div class="irica-version">نسخه __VERSION__</div></div>
<section class="stats" id="stats" aria-label="خلاصه وضعیت">
  <div class="stat total"><div class="n" id="s-total">—</div><div class="l">کل پروفایل‌ها</div></div>
  <div class="stat running"><div class="n" id="s-running">—</div><div class="l">در حال اجرا</div></div>
  <div class="stat success"><div class="n" id="s-success">—</div><div class="l">ثبت موفق</div></div>
  <div class="stat blocked"><div class="n" id="s-blocked">—</div><div class="l">مسدود</div></div>
</section>
<div class="sites">
<a class="site" data-site="crm" href="/crm"><span class="num">۱</span><h2>پنل یکپارچه رجیستری</h2><p>چرخهٔ کامل دستگاه: استعلام، اتصال مالک، تحویل، پرداخت و ثبت‌نام — با دفتر حساب مشتری و گزارش‌ها.</p><div class="tags"><span class="tag">CRM</span><span class="tag">پرونده</span><span class="tag">گزارش</span></div><span class="go">←</span></a>
<a class="site" data-site="2" href="/site-2"><span class="num">۲</span><h2>استعلام سلامت دستگاه</h2><p>استعلام وضعیت دستگاه از سایت گمرک (فقط صفحه اول) با یک پروفایل شخص و لیست دستگاه‌ها.</p><div class="tags"><span class="tag">استعلام سلامت</span><span class="tag">افزودن عمده</span><span class="tag">ترتیب تصادفی</span></div><span class="go">←</span></a>
<a class="site" data-site="1" href="/site-1"><span class="num">۳</span><h2>پنل قدیمی رجیستری</h2><p>پنل کامل: ویرایش پروفایل، لاگ‌ها، استعلامات و تنظیمات.</p><div class="tags"><span class="tag">ویرایش کامل</span><span class="tag">لاگ‌ها</span><span class="tag">استعلام خروج</span></div><span class="go">←</span></a>
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
    if(!s||s.error)return;
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


LAUNCH_PAGE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IRICA — سرویس رجیستری</title>
<link rel="icon" href="/favicon.ico">
<style>
  :root{
    --bg:#171a26; --card:#1f2333; --card2:#262b3f; --line:#363d57;
    --fg:#eef1fb; --muted:#a5adc6; --brand:#6d8bff; --brand2:#9d6dff;
    --ok:#3ba776; --danger:#e5484d; --warn:#f5a524;
  }
  html[data-theme="light"]{
    --bg:#f4f5f8; --card:#ffffff; --card2:#eef0f4; --line:#d9dde5;
    --fg:#1a1d24; --muted:#5d6470;
  }
  *{box-sizing:border-box; margin:0; padding:0;}
  body{
    font-family:"Segoe UI",Tahoma,sans-serif; background:var(--bg); color:var(--fg);
    min-height:100vh; display:flex; align-items:center; justify-content:center;
    padding:24px; transition:background .3s;
  }
  .card{
    width:100%; max-width:430px; background:var(--card); border:1px solid var(--line);
    border-radius:18px; padding:28px 26px; box-shadow:0 18px 50px rgba(0,0,0,.22);
  }
  .logo{width:56px; height:56px; border-radius:14px; margin:0 auto 14px;
    background:linear-gradient(135deg,var(--brand),var(--brand2));
    display:flex; align-items:center; justify-content:center;
    color:#fff; font-size:26px; font-weight:800;}
  h1{font-size:17px; text-align:center; margin-bottom:4px;}
  .status{
    display:flex; align-items:center; justify-content:center; gap:8px;
    font-size:13.5px; font-weight:700; margin:10px 0 20px; padding:9px 12px;
    border-radius:10px; background:color-mix(in srgb,var(--ok) 12%,transparent);
    color:var(--ok); border:1px solid color-mix(in srgb,var(--ok) 35%,transparent);
  }
  .status.off{background:color-mix(in srgb,var(--danger) 12%,transparent);
    color:var(--danger); border-color:color-mix(in srgb,var(--danger) 35%,transparent);}
  .dot{width:9px; height:9px; border-radius:50%; background:var(--ok);
    box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent);
    animation:pulse 2s infinite;}
  .status.off .dot{background:var(--danger); animation:none;
    box-shadow:0 0 0 3px color-mix(in srgb,var(--danger) 25%,transparent);}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
  .stats{display:flex; gap:8px; margin-bottom:20px;}
  .stat{flex:1; text-align:center; background:var(--card2); border:1px solid var(--line);
    border-radius:12px; padding:10px 6px;}
  .stat b{display:block; font-size:19px;}
  .stat span{font-size:10.5px; color:var(--muted);}
  .btn{width:100%; display:flex; align-items:center; justify-content:center; gap:9px;
    border:none; border-radius:12px; padding:13px; font-size:14.5px; font-weight:800;
    cursor:pointer; margin-bottom:10px; color:#fff; transition:.15s;
    font-family:inherit;}
  .btn:hover{filter:brightness(1.12); transform:translateY(-1px);}
  .btn:disabled{opacity:.55; cursor:wait; transform:none;}
  .b-open{background:linear-gradient(135deg,var(--brand),var(--brand2));}
  .b-estelam{background:var(--card2); color:var(--fg); border:1px solid var(--line);}
  .b-open2{background:var(--card2); color:var(--fg); border:1px solid var(--line);}
  .b-stop{background:color-mix(in srgb,var(--danger) 88%,black);}
  .foot{margin-top:14px; display:flex; align-items:center; justify-content:space-between;
    font-size:11px; color:var(--muted); direction:ltr;}
  .badge{padding:2px 9px; border-radius:99px; border:1px solid var(--line);
    background:var(--card2); font-weight:700;}
  .off-msg{display:none; text-align:center; font-size:13px; color:var(--muted);
    line-height:1.9; padding:12px 0 4px;}
</style>
</head>
<body>
<div class="card">
  <div class="logo">I</div>
  <h1>سرویس رجیستری IRICA</h1>
  <div class="status" id="status"><span class="dot"></span><span id="status-text">در حال بررسی…</span></div>
  <div class="stats">
    <div class="stat"><b id="s-total">—</b><span>پروفایل</span></div>
    <div class="stat"><b id="s-run">—</b><span>در حال کار</span></div>
    <div class="stat"><b id="s-success">—</b><span>موفق</span></div>
    <div class="stat"><b id="s-blocked">—</b><span>مسدود</span></div>
  </div>
  <button class="btn b-open" id="btn-crm" onclick="location.href='/crm'">
    🗂️ پنل یکپارچه رجیستری</button>
  <button class="btn b-estelam" id="btn-estelam" onclick="location.href='/site-2'">
    🔍 پنل استعلام‌ها</button>
  <button class="btn b-open2" id="btn-open" onclick="location.href='/site-1'">
    🏦 پنل قدیمی رجیستری</button>
  <button class="btn b-stop" id="btn-stop">⏻ بستن سرویس</button>
  <div class="off-msg" id="off-msg">سرویس خاموش شد.<br>این صفحه را ببندید؛ برای اجرای دوباره، میان‌بر دسکتاپ را اجرا کنید.</div>
  <div class="foot">
    <span>127.0.0.1:8750</span>
    <span class="badge">نسخه <span id="v">…</span></span>
  </div>
</div>
<script>
var toFa=function(n){return String(n).replace(/\d/g,function(d){return '۰۱۲۳۴۵۶۷۸۹'[d]})};
function fill(s){
  var ps=s.profiles||[];
  var running=ps.filter(function(p){return p.runner_pid||p.device_inquiry_pid}).length;
  var ok=ps.filter(function(p){return p.registration_status==='success'}).length;
  var bl=ps.filter(function(p){return p.registration_status==='blocked'}).length;
  document.getElementById('s-total').textContent=toFa(ps.length);
  document.getElementById('s-run').textContent=toFa(running);
  document.getElementById('s-success').textContent=toFa(ok);
  document.getElementById('s-blocked').textContent=toFa(bl);
}
fetch('/state').then(function(r){return r.json()}).then(fill).catch(function(){});
fetch('/site-1').then(function(r){return r.text()}).then(function(t){
  var m=t.match(/نسخه\s*([0-9.]+)/);
  document.getElementById('v').textContent=m?m[1]:'?';
  document.getElementById('status-text').textContent='سرویس رجیستری فعال است';
}).catch(function(){
  document.getElementById('status').classList.add('off');
  document.getElementById('status-text').textContent='سرویس در دسترس نیست';
});
document.getElementById('btn-stop').onclick=function(){
  var b=this; b.disabled=true; b.textContent='در حال بستن…';
  fetch('/shutdown',{method:'POST'}).then(function(r){return r.json()}).then(function(){
    b.textContent='سرویس بسته شد ✓'; b.style.filter='grayscale(.4)';
    document.getElementById('status').classList.add('off');
    document.getElementById('status-text').textContent='سرویس خاموش شد';
    document.getElementById('off-msg').style.display='block';
  }).catch(function(){
    b.disabled=false; b.textContent='⏻ بستن سرویس';
  });
};
</script>
</body></html>"""


def render_launch_page():
    return LAUNCH_PAGE.encode("utf-8")


def render_home_page():
    html = HOME_PAGE.replace("__PANEL_URL__", f"{HOST}:{PORT}")
    html = html.replace("__VERSION_TAG__",
                        f'<span class="irica-version">نسخه {app_version_text() or "—"}</span>')
    return html.replace("__VERSION__", app_version_text() or "—").encode("utf-8")


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
    --muted:#5c6484;--brand:#4f6bef;--brand2:#8250e6;--ok:#12a05f;--danger:#e0455a;--warn:#c97a10;
    --logbg:#f7f9ff;--scroll:#cdd4ec;--sn:#4257a8;--shadow:rgba(20,30,70,.14);
    --pill-neutral-bg:#e6eaf7;--pill-neutral-fg:#5c6484;
    --pill-ok-bg:#dbf3e6;--pill-ok-fg:#0f7d4a;
    --pill-danger-bg:#fbe0e5;--pill-danger-fg:#c22a3f;
    --pill-warn-bg:#fcefd6;--pill-warn-fg:#9a5c08;
  }
  html[data-theme="dark"]{
    --bg:#171a26;--card:#1f2333;--card2:#262b3f;--line:#363d57;--txt:#eef1fb;
    --muted:#aeb6d0;--brand:#7d95ff;--brand2:#a878ff;--ok:#3ad28b;--danger:#ff6678;--warn:#f6b94b;
    --logbg:#1b2033;--scroll:#3a4260;--sn:#8ea2d8;--shadow:rgba(0,0,0,.32);
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
  /* nav-e arz: dokme-haye amade +reset */
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
  .irica-version{font-size:11.5px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:3px 12px;direction:ltr;align-self:center}
  .system-close-btn{display:inline-flex;align-items:center;gap:6px;padding:9px 14px;border-radius:10px;font-size:12.5px;font-weight:700;
    border:1px solid color-mix(in srgb,var(--danger) 45%,var(--line));background:color-mix(in srgb,var(--danger) 8%,var(--card));
    color:var(--danger);cursor:pointer;font-family:inherit;transition:.15s;align-self:center}
  .system-close-btn:hover{background:var(--danger);color:#fff;border-color:var(--danger)}
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
  .svc-btns .svc:hover{color:var(--txt);background:color-mix(in srgb,var(--brand) 10%,transparent)}
  /* gozine-ye ENTEKHAB-SHODE: rang-e brand-e por +noar-e paeen (mesl-e tab-haye digar) */
  .svc-btns .svc.on{position:relative;background:linear-gradient(135deg,var(--brand),var(--brand2));
                    border-color:transparent;color:#fff;
                    box-shadow:0 2px 10px color-mix(in srgb,var(--brand) 32%,transparent)}
  .svc-btns .svc.on:hover{color:#fff;filter:brightness(1.05)}
  .svc-btns .svc.on::after{content:"";position:absolute;inset-inline:22%;bottom:-5px;height:3px;
                           border-radius:3px;background:var(--brand)}
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
  /* --- sotoon-e natije: kholase +modal-e motn-e kamel --- */
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
    <div class="irica-version">نسخه __VERSION__</div>
    <div class="wbar">
      <span class="wlab">عرض کادر:</span>
      <button type="button" class="wbtn" data-w="1080" onclick="setWrapW(1080)">معمولی</button>
      <button type="button" class="wbtn" data-w="1620" onclick="setWrapW(1620)">عریض</button>
      <button type="button" class="wbtn" data-w="max" onclick="setWrapW('max')">تمام‌عرض</button>
      <button type="button" class="tbtn" id="theme-btn" onclick="toggleTheme()">🌙 حالت تاریک</button>
      <a class="back" href="/">← انتخاب سامانه</a>
      <a class="back" href="/crm">👥 مدیریت پرونده‌ها</a>
      <button type="button" class="system-close-btn" onclick="closeWholeSystem()" title="همهٔ ربات‌ها متوقف و پنل خاموش می‌شود">⏻ بستن سامانه</button>
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
    <p class="hint">نام دستگاه +سریال ۱۵ رقمی. سریال دوم اختیاری است (دو سیم‌کارت).</p>
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
    // badge-e gomrok KUTAH (مجاز/مسدود) mesl-e hamta; motn-e kamel dar tooltip +sotoon-e natije
    var gb='<span class="badge b-'+esc(gs)+'" title="'+esc(FA_STATUS[gs]||gs)+'">'+
           esc(BADGE_FA[gs]||FA_STATUS[gs]||gs)+'</span>';
    var hb='<span class="badge b-'+esc(hs)+'" title="'+esc(HAMTA_FA[hs]||hs)+'">'+
           esc(HAMTA_FA[hs]||hs)+'</span>';
    // zir-sotoon-e DOVVOM-e vaziat: zaman-e estelam ('--' agar anjam nashode)
    var ht=fmtWhen(d.hamta_at), gt=fmtWhen(d.gomrok_at);
    // sotoon-e natije: 80 character-e aval +dokme-ye «مشاهده کامل» (modal).
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

/* --- sotoon-e natije: kholase-ye 80-character +modal-e motn-e kamel ------- */
var RES_LIMIT=80;
function resText(d,svc){return (svc==='hamta'?d.hamta_result:d.result)||'';}
function resCell(d,svc,prefix){
  prefix=prefix||'';
  var full=resText(d,svc);
  if(!full) return esc(prefix)+'—';
  // faghat khat-e aval-e ma'nadar +kutah-shode be 80 character
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

function closeWholeSystem(){
  if(!confirm('کل سامانه بسته شود؟ همهٔ ربات‌ها و استعلام‌های در حال اجرا متوقف می‌شوند و پنل خاموش می‌شود.'))return;
  fetch('/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
    .catch(function(){});
  var banner=document.createElement('div');
  banner.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9999;background:#b91c1c;color:#fff;'+
    'padding:14px;text-align:center;font-family:Tahoma,sans-serif;font-size:14px;font-weight:bold';
  banner.textContent='در حال بستن سامانه… همهٔ ربات‌ها متوقف شدند؛ این صفحه تا چند ثانیه دیگر در دسترس نخواهد بود.';
  document.body.appendChild(banner);
  setTimeout(function(){
    banner.textContent='سامانه بسته شد. برای اجرای مجدد، میان‌بر IRICA را باز کنید.';
    setTimeout(function(){location.reload();},4000);
  },1500);
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

/* --- arz-e INTERACTIVE-e kadr-e asli (drag +preset +localStorage) --------
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

/* ---------------- log-e interactive (kadr-e sabet +scroll) ---------------- */
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


CRM_PAGE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>مدیریت مشتریان و پرونده‌ها (CRM)</title>
<link rel="icon" href="data:,">
<style>
  /* Bundled Vazirmatn — offline, no external fetch (frontend-design skill:
     typography carries the identity; here a Persian-first humanist face). */
  @font-face{
    font-family:'Vazirmatn';src:url('/fonts/Vazirmatn-var.woff2') format('woff2-variations');
    font-weight:100 900;font-style:normal;font-display:swap;
  }
  @font-face{
    font-family:'Vazirmatn';src:url('/fonts/Vazirmatn-Bold.woff2') format('woff2');
    font-weight:700;font-style:normal;font-display:swap;
  }

  :root{
    --bg:#f4f6fb;--card:#ffffff;--card2:#f7f8fd;--line:rgba(27,32,54,.10);
    --line-strong:rgba(27,32,54,.16);--txt:#1b2036;--muted:#5c6484;--faint:#8a91ad;
    --brand:#4f6bef;--brand-hover:#3d59d9;--brand-soft:rgba(79,107,239,.10);
    --on-brand:#ffffff;
    --ok:#0c7d4f;--ok-soft:rgba(15,157,99,.12);
    --danger:#d93752;--danger-soft:rgba(217,55,82,.10);
    --warn:#b9770e;--warn-soft:rgba(185,119,14,.13);
    --pill-neutral-bg:rgba(27,32,54,.06);--pill-neutral-fg:#5c6484;
    --pill-ok-bg:rgba(15,157,99,.13);--pill-ok-fg:#0b7a4d;
    --pill-danger-bg:rgba(217,55,82,.12);--pill-danger-fg:#b72740;
    --pill-warn-bg:rgba(185,119,14,.14);--pill-warn-fg:#92590a;
    --shadow-1:0 1px 2px rgba(16,20,40,.05),0 1px 6px rgba(16,20,40,.04);
    --shadow-2:0 4px 14px rgba(16,20,40,.10),0 1px 3px rgba(16,20,40,.06);
    --ring:0 0 0 3px rgba(79,107,239,.28);
  }
  html[data-theme="dark"]{
    --bg:#101322;--card:#181c2e;--card2:#1f2438;--line:rgba(255,255,255,.08);
    --line-strong:rgba(255,255,255,.14);--txt:#eef1fb;--muted:#aeb6d0;--faint:#7c84a3;
    --brand:#7d95ff;--brand-hover:#93a7ff;--brand-soft:rgba(125,149,255,.12);
    --on-brand:#0e1330;
    --ok:#3ad28b;--ok-soft:rgba(58,210,139,.13);
    --danger:#ff6678;--danger-soft:rgba(255,102,120,.13);
    --warn:#f6b94b;--warn-soft:rgba(246,185,75,.13);
    --pill-neutral-bg:rgba(255,255,255,.07);--pill-neutral-fg:#a9b7e0;
    --pill-ok-bg:rgba(58,210,139,.14);--pill-ok-fg:#3ad28b;
    --pill-danger-bg:rgba(255,102,120,.14);--pill-danger-fg:#ff6678;
    --pill-warn-bg:rgba(246,185,75,.14);--pill-warn-fg:#f6b94b;
    --shadow-1:0 1px 2px rgba(0,0,0,.35),0 1px 6px rgba(0,0,0,.25);
    --shadow-2:0 6px 18px rgba(0,0,0,.45),0 2px 5px rgba(0,0,0,.3);
    --ring:0 0 0 3px rgba(125,149,255,.30);
  }
  *{box-sizing:border-box}
  html{color-scheme:light}
  html[data-theme="dark"]{color-scheme:dark}
  body{margin:0;background:var(--bg);color:var(--txt);
    font-family:Vazirmatn,"Segoe UI",Tahoma,"Segoe UI",sans-serif;
    font-size:14px;line-height:1.6;padding:24px 18px 70px;
    -webkit-font-smoothing:antialiased}
  .wrap{width:min(1240px,98vw);margin:0 auto}
  a.back{color:var(--muted);text-decoration:none;font-size:13px;padding:6px 2px;border-radius:6px}
  a.back:hover{color:var(--brand)}
  :is(a,button,input,select,textarea,summary):focus-visible{outline:none;box-shadow:var(--ring)}
  /* header */
  .head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:18px}
  .head h1{font-size:20px;font-weight:750;margin:0;flex:1;letter-spacing:-.3px;
    display:flex;align-items:center;gap:11px}
  .head h1 .mono{width:38px;height:38px;border-radius:11px;flex:none;
    background:var(--brand);color:var(--on-brand);display:grid;place-items:center;
    font-size:16px;font-weight:800;letter-spacing:0}
  .head h1 .sub{font-size:12px;color:var(--muted);margin-top:2px;font-weight:400;
    letter-spacing:0}
  .iconbtn{border:1px solid var(--line);background:var(--card);color:var(--txt);
    border-radius:9px;padding:7px 12px;cursor:pointer;font-family:inherit;font-size:13px}
  .iconbtn:hover{border-color:var(--line-strong);background:var(--card2)}
  /* buttons */
  .btn{border:1px solid var(--line);background:var(--card);color:var(--txt);border-radius:9px;
    padding:8px 16px;cursor:pointer;font-family:inherit;font-size:13px;font-weight:500;
    transition:background .15s,border-color .15s,color .15s;min-height:32px}
  .btn:hover{border-color:var(--line-strong);background:var(--card2)}
  .btn.primary{background:var(--brand);border-color:transparent;color:var(--on-brand)}
  .btn.primary:hover{background:var(--brand-hover);border-color:transparent}
  .btn.danger{color:var(--danger);border-color:var(--danger-soft);background:var(--danger-soft)}
  .btn.danger:hover{border-color:var(--danger)}
  .btn.ghost{background:transparent;border-color:transparent;color:var(--brand)}
  .btn.ghost:hover{background:var(--brand-soft);border-color:transparent}
  .btn.mini{padding:4px 11px;font-size:12px;border-radius:8px;min-height:28px}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  /* KPI — "دفتر ثبت" ledger strip: one surface, hairline dividers, not 4
     clone cards (frontend-design: structure carries the information). */
  .kpis{display:grid;grid-template-columns:repeat(4,1fr);background:var(--card);
    border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow-1);
    margin-bottom:16px;overflow:hidden}
  .kpi{position:relative;background:transparent;border:none;border-inline-start:1px solid var(--line);
    padding:14px 18px;cursor:pointer;text-align:right;font-family:inherit;transition:background .15s}
  .kpi:first-child{border-inline-start:none}
  .kpi:hover{background:var(--brand-soft)}
  .kpi:focus-visible{box-shadow:inset var(--ring)}
  .kpi::after{content:'';position:absolute;inset-inline:0;bottom:0;height:2px;
    background:transparent;transition:background .15s}
  .kpi:hover::after{background:var(--brand)}
  .kpi .n{display:block;font-size:27px;font-weight:800;line-height:1.25;
    font-variant-numeric:tabular-nums;letter-spacing:-.3px}
  .kpi .l{font-size:12px;color:var(--muted);font-weight:500}
  .kpi.attention .n{color:var(--warn)}
  .kpi.k-problem .n{color:var(--danger)}
  .kpi.k-ok .n{color:var(--ok)}
  /* tabs */
  .tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:16px;border-bottom:1px solid var(--line);padding-bottom:0}
  .tab{background:transparent;border:none;border-bottom:2px solid transparent;border-radius:8px 8px 0 0;
    padding:9px 15px 7px;cursor:pointer;font-family:inherit;font-size:13.5px;color:var(--muted);font-weight:500}
  .tab:hover{color:var(--txt);background:var(--brand-soft)}
  .tab.active{color:var(--brand);border-bottom-color:var(--brand);background:transparent}
  .tab .cnt{font-size:11px;background:var(--pill-neutral-bg);color:var(--pill-neutral-fg);
    border-radius:999px;padding:1px 7px;margin-inline-start:5px;font-variant-numeric:tabular-nums}
  /* cards & tables */
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;
    margin-bottom:14px;box-shadow:var(--shadow-1)}
  .card .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:right;vertical-align:top}
  th{color:var(--muted);font-weight:600;font-size:11.5px;white-space:nowrap;text-transform:none}
  tbody tr:hover td{background:var(--card2)}
  tr:last-child td{border-bottom:none}
  .pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:11.5px;white-space:nowrap;font-weight:500}
  .pill.n{background:var(--pill-neutral-bg);color:var(--pill-neutral-fg)}
  .pill.ok{background:var(--pill-ok-bg);color:var(--pill-ok-fg)}
  .pill.bad{background:var(--pill-danger-bg);color:var(--pill-danger-fg)}
  .pill.warn{background:var(--pill-warn-bg);color:var(--pill-warn-fg)}
  .muted{color:var(--muted);font-size:12.5px}
  .kbd{font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
  .chip{display:inline-block;background:var(--pill-neutral-bg);color:var(--pill-neutral-fg);
    border-radius:7px;padding:2px 9px;font-size:11.5px}
  /* device cards */
  .devgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
  .dev{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 15px 12px;
    box-shadow:var(--shadow-1);display:flex;flex-direction:column;gap:10px;
    border-inline-start:3px solid transparent}
  .dev.needs-action{border-inline-start-color:var(--warn)}
  .dev .devhead{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  .dev .devhead b{font-size:15px;letter-spacing:-.2px}
  .dev .devhead .ic{color:var(--faint);align-self:center;flex:none}
  .dev .ser{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:var(--faint);direction:ltr;unicode-bidi:embed;
    letter-spacing:.4px}
  .dev .meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  /* 9-step stage rail — the page's signature: numbered nodes, current node
     enlarged (frontend-design: one bold element, not decoration). */
  .rail{display:flex;gap:0;align-items:center;margin:4px 0 2px}
  .rail .step{flex:1;height:4px;border-radius:2px;background:var(--pill-neutral-bg);position:relative}
  .rail .step::before{counter-increment:stage;content:counter(stage);
    position:absolute;top:-13px;inset-inline-start:calc(100% - 7px);
    width:14px;height:14px;border-radius:50%;background:var(--pill-neutral-bg);
    color:var(--faint);font-size:8.5px;font-weight:700;display:grid;place-items:center;
    font-variant-numeric:tabular-nums}
  .rail{counter-reset:stage}
  .rail .step.done{background:var(--ok)}
  .rail .step.done::before{background:var(--ok-soft);color:var(--ok)}
  .rail .step.cur{background:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
  .rail .step.cur::before{background:var(--brand);color:var(--on-brand);
    width:17px;height:17px;top:-15px;font-size:9.5px}
  .rail .step.bad{background:var(--danger)}
  .rail .step.bad::before{background:var(--danger-soft);color:var(--danger)}
  .rail .step.warn{background:var(--warn)}
  .rail .step.warn::before{background:var(--warn-soft);color:var(--warn)}
  .stagebar{display:flex;justify-content:space-between;align-items:center;gap:8px}
  .stagebar .st{font-size:12.5px;font-weight:600}
  .dev .acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto;padding-top:2px}
  .dev .subrow{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px}
  .dev .subrow .cell b{font-size:11px;color:var(--muted);font-weight:600;display:block}
  /* forms & dialog */
  input,select,textarea{font-family:inherit;font-size:13.5px;border:1px solid var(--line-strong);
    border-radius:9px;padding:8px 11px;background:var(--card);color:var(--txt);min-height:34px}
  select{cursor:pointer}
  input:disabled,select:disabled{background:var(--card2);color:var(--muted)}
  label.f{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted);font-weight:500}
  label.f input,label.f select,label.f textarea{color:var(--txt)}
  label.f .ferr{color:var(--danger);font-size:11.5px;min-height:0}
  input[aria-invalid="true"],select[aria-invalid="true"],textarea[aria-invalid="true"]{border-color:var(--danger)}
  /* checkbox control — custom, aligned, keyboard-visible (ux-guidelines) */
  label.f.check{flex-direction:row;align-items:center;gap:9px;cursor:pointer;
    padding:7px 11px;border:1px solid var(--line-strong);border-radius:9px;background:var(--card);
    min-height:34px;user-select:none}
  label.f.check input{position:absolute;opacity:0;width:0;height:0}
  label.f.check .box{flex:none;width:17px;height:17px;border:1.5px solid var(--line-strong);
    border-radius:5px;background:var(--card2);position:relative;transition:background .12s,border-color .12s}
  label.f.check .box::after{content:'';position:absolute;inset:2px 3px 4px;
    border-inline-start:2px solid var(--on-brand);border-bottom:2px solid var(--on-brand);
    transform:rotate(-45deg) scale(0);transform-origin:center;transition:transform .12s}
  label.f.check input:checked+.box{background:var(--brand);border-color:var(--brand)}
  label.f.check input:checked+.box::after{transform:rotate(-45deg) scale(1)}
  label.f.check input:focus-visible+.box{box-shadow:var(--ring)}
  label.f.check .ft{font-size:12.5px;color:var(--txt);font-weight:500}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
  dialog{border:1px solid var(--line);border-radius:16px;background:var(--card);color:var(--txt);
    padding:20px;width:min(600px,94vw);box-shadow:var(--shadow-2)}
  dialog::backdrop{background:rgba(8,10,25,.55)}
  dialog h3{margin:0 0 12px;font-size:16px;font-weight:650}
  .err{color:var(--danger);font-size:12.5px;white-space:pre-line}
  /* states */
  .empty{padding:34px 16px;text-align:center;color:var(--muted)}
  .empty b{display:block;font-size:14.5px;color:var(--txt);margin-bottom:4px;font-weight:600}
  .skel{height:84px;border-radius:14px;border:1px solid var(--line);
    background:linear-gradient(100deg,var(--card) 40%,var(--card2) 50%,var(--card) 60%);
    background-size:200% 100%;animation:sh 1.1s infinite linear;margin-bottom:10px}
  @keyframes sh{to{background-position:-200% 0}}
  @media (prefers-reduced-motion:reduce){.skel{animation:none}}
  /* toast */
  #toasts{position:fixed;bottom:18px;inset-inline-start:18px;display:flex;flex-direction:column;gap:8px;z-index:99}
  .toast{background:var(--ok);color:#fff;padding:10px 18px;border-radius:10px;font-size:13px;
    box-shadow:var(--shadow-2);animation:tin .18s ease-out}
  .toast.bad{background:var(--danger)}
  @keyframes tin{from{opacity:0;transform:translateY(6px)}}
  @media (prefers-reduced-motion:reduce){.toast{animation:none}}
  /* log console */
  #logBox{max-height:320px;overflow:auto;border:1px solid var(--line);border-radius:10px;
    background:var(--card2);padding:10px;font-size:11.5px;display:flex;flex-direction:column;gap:3px}
  @media (max-width:640px){.devgrid{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}.kpi:nth-child(3){border-inline-start:none}}

</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1><span class="mono">R</span><span>مدیریت مشتریان و پرونده‌ها<div class="sub">چرخهٔ کامل دستگاه: استعلام ← مالک ← تحویل ← پرداخت ← کد فعال‌سازی</div></span></h1>
    <a class="back" href="/site-2">پنل استعلام‌ها →</a>
    <a class="back" href="/site-1">→ سامانه مدیریت گمرک</a>
    <button class="iconbtn" id="themeBtn">پوسته</button>
  </div>
  <div class="tabs" id="tabs"></div>
  <div id="view"></div>
</div>

<dialog id="dlg"><div id="dlgBody"></div></dialog>
<div id="toasts"></div>

<script>
"use strict";
/* ================= state ================= */
var STATE = null, TAB = (new URLSearchParams(location.search).get('tab')||'devices'), LOGS = [], LOADFAIL = false;
var STAGE_ORDER = ['new','inquiry','problem','ready_owner','handed','to_payment','payment','payment_done','done'];
var STAGE_FA = {
  'new':'جدید','inquiry':'در انتظار استعلام','problem':'دارای اشکال',
  'ready_owner':'سالم — نیازمند مالک','handed':'تحویل به مسئول سایت',
  'to_payment':'ثبت‌شده — در انتظار پرداخت','payment':'در حال پرداخت',
  'payment_done':'پرداخت تأییدشده','done':'تکمیل‌شده'
};
var STAGE_TONE = {'new':'n','inquiry':'n','problem':'bad','ready_owner':'warn','handed':'n',
  'to_payment':'ok','payment':'warn','payment_done':'ok','done':'ok'};
var EST_FA = {'pending':'در انتظار','running':'در حال استعلام','healthy':'سالم',
  'advanced':'ثبت‌شده در گمرک','problem':'دارای اشکال','error':'بدون پاسخ','':'—'};
var EST_PILL = {'pending':'n','running':'warn','healthy':'ok','advanced':'ok','problem':'bad','error':'bad','':'n'};
var TABS = [
  ['devices','دستگاه‌ها'],['customers','مشتریان'],['owners','مالکان'],
  ['identities','استخر هویت'],['ledger','تراکنش‌ها'],['reports','گزارش‌ها']
];

/* ================= plumbing ================= */
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function money(n){n=Number(n||0);return n.toLocaleString('fa-IR')+' تومان';}
function fa(n){return Number(n||0).toLocaleString('fa-IR');}
async function api(path,payload){
  var opt = payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {};
  var res = await fetch(path,opt);
  var body = null; try{ body = await res.json(); }catch(e){}
  if(!res.ok){ throw ((body&&body.errors)||['خطای '+res.status]).join('\n'); }
  return body;
}
function toast(msg, ok){
  var el=document.createElement('div');
  el.className='toast'+(ok===false?' bad':'');
  el.textContent=msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(()=>el.remove(), 3200);
}
function openDlg(html){document.getElementById('dlgBody').innerHTML=html;document.getElementById('dlg').showModal();}
function closeDlg(){document.getElementById('dlg').close();}
/* Confirm dialog (native confirm() is blocking & unstyled): a small dialog
   reusing #dlg with a confirm/cancel pair — used by stage rollback. */
var _confirmCb=null;
function confirmDlg(title,text,okLabel,cb){
  _confirmCb=cb;
  openDlg('<h3>'+esc(title)+'</h3><p style="margin:0 0 14px;color:var(--muted);font-size:13.5px;line-height:1.9">'
    +esc(text)+'</p>'
    +'<div class="row" style="margin-top:10px"><button class="btn danger" onclick="confirmYes()">'+esc(okLabel||'تأیید')+'</button>'
    +'<button class="btn" onclick="closeDlg()">انصراف</button></div>');
}
function confirmYes(){var cb=_confirmCb;_confirmCb=null;closeDlg();if(cb)cb();}
var urlTheme=new URLSearchParams(location.search).get('theme');
var savedTheme=urlTheme||localStorage.getItem('irica-theme')||'light';
document.documentElement.setAttribute('data-theme',savedTheme);
document.getElementById('themeBtn').onclick=function(){
  var dark=document.documentElement.getAttribute('data-theme')==='dark';
  var next=dark?'light':'dark';
  document.documentElement.setAttribute('data-theme',next);
  localStorage.setItem('irica-theme',next);
};

/* ================= lookups ================= */
function findCustomer(id){return (STATE&&STATE.customers||[]).find(c=>c.id===id)||null;}
function findOwner(id){return (STATE&&STATE.owners||[]).find(o=>o.id===id)||null;}
function custName(id){var c=findCustomer(id);return c?c.full_name:'—';}
function ownerName(id){var o=findOwner(id);return o?(o.first_name+' '+o.last_name):'—';}
function bal(id){var c=findCustomer(id);if(!c)return 0;return (c.transactions||[]).reduce((s,t)=>s+Number(t.amount||0),0);}
function v(id){return document.getElementById(id).value.trim();}

/* ================= stage actions (server contract) ================= */
/* ================= inline SVG icons (no emoji per ux-guidelines) ================= */
var ICONS = {
  user:'<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"/></svg>',
  id:'<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="2.5"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16c.6-1.6 1.7-2.3 3-2.3s2.4.7 3 2.3M14.5 9.5H19M14.5 13H19"/></svg>',
  dev:'<svg class="ic" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M10.5 18.5h3"/></svg>',
  search:'<svg class="ic" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.8-3.8"/></svg>',
  swap:'<svg class="ic" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M7 16V4m0 0L3.5 7.5M7 4l3.5 3.5M17 8v12m0 0 3.5-3.5M17 20l-3.5-3.5"/></svg>',
  stop:'<svg class="ic" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>'
};
function stageActions(d){
  var a=[];
  var st=d.stage;
  if(st==='new') a.push(['queue_inquiry','ارسال به صف استعلام','primary']);
  if(st==='inquiry'){a.push(['mark_ready','ثبت نتیجهٔ سالم','primary']);a.push(['mark_problem','ثبت اشکال','danger']);}
  if(st==='problem') a.push(['requeue_inquiry','بازگشت به صف','primary']);
  if(st==='ready_owner') a.push(['handover','تحویل به مسئول سایت','primary']);
  if(st==='handed'&&!d.auto_advance) a.push(['__start_payment','شروع پرداخت','primary']);
  if(st==='to_payment'&&!d.auto_advance) a.push(['__start_payment','شروع پرداخت','primary']);
  if(st==='payment') a.push(['confirm_payment','تأیید پرداخت','primary']);
  if(st==='payment_done') a.push(['deliver_code','تحویل کد و تکمیل','primary']);
  if(st==='ready_owner') a.push(['__attach','اتصال مالک','']);
  if(st==='handed'&&d.auto_advance) a.push(['','خودکار','n']);
  if(STAGE_BACK_FA[st]) a.push(['__stage_back','↩ برگشت به «'+STAGE_BACK_FA[st]+'»','']);
  return a;
}
var STAGE_BACK_FA = {
  'inquiry':'جدید','ready_owner':'در انتظار استعلام','handed':'سالم — نیازمند مالک',
  'to_payment':'تحویل به مسئول سایت','payment':'ثبت‌شده — در انتظار پرداخت',
  'payment_done':'در حال پرداخت'
};
function devTick(id, action){
  if(action==='__attach'){dlgAttach(id);return;}
  if(action==='__stage_back'){
    api('/crm-state').then(function(state){
      var d=(state.devices||[]).find(x=>x.id===id);if(!d)return;
      var target=STAGE_BACK_FA[d.stage]||'مرحلهٔ قبل';
      confirmDlg('برگشت به مرحلهٔ قبل','مطمئنی می‌خوای به گام «'+target+'» برگردی؟',
        'برگشت',function(){
          api('/crm-tick',{device_id:id,action:'stage_back'}).then(async r=>{
            toast('به «'+(STAGE_FA[r.device.stage]||r.device.stage)+'» برگشت');await refresh();})
            .catch(e=>toast(e,false));
        });
    });
    return;
  }
  if(action==='__start_payment'){
    api('/crm-start-payment',{device_id:id}).then(async r=>{toast(r.ok?'ربات پرداخت شروع شد':'انجام نشد');await refresh();})
      .catch(e=>toast(e,false));
    return;
  }
  api('/crm-tick',{device_id:id,action:action}).then(async r=>{
      toast('مرحله: '+(STAGE_FA[r.device.stage]||r.device.stage));await refresh();})
    .catch(e=>toast(e,false));
}
function needsAction(d){
  return ['problem','ready_owner','to_payment','payment'].includes(d.stage);
}
function railHtml(d){
  var cur = STAGE_ORDER.indexOf(d.stage);
  var steps = STAGE_ORDER.map(function(st,i){
    var cls='step';
    if(d.stage==='problem' && st==='problem') cls+=' bad';
    else if(i<cur) cls+=' done';
    else if(i===cur) cls+= (st==='problem'?' bad':(STAGE_TONE[st]==='ok'?' done':' cur'));
    return '<span class="'+cls+'" title="'+esc(STAGE_FA[st])+'"></span>';
  }).join('');
  return '<div class="rail" aria-hidden="true">'+steps+'</div>';
}

/* ================= KPI strip ================= */
function kpisHtml(){
  var ds=(STATE.devices||[]);
  var c={all:ds.length,need:0,ok:0,debt:0};
  ds.forEach(d=>{ if(needsAction(d))c.need++; if(d.stage==='done')c.ok++; });
  (STATE.customers||[]).forEach(x=>{ if(bal(x.id)<0)c.debt++; });
  function k(cls,label,n,tab){
    return '<button type="button" class="kpi '+cls+'" onclick="setTab(\''+tab+'\')">'
      +'<span class="n">'+fa(n)+'</span><span class="l">'+label+'</span></button>';
  }
  return '<div class="kpis">'
    +k('','همهٔ پرونده‌ها',c.all,'devices')
    +k('attention','نیازمند اقدام',c.need,'devices')
    +k('k-ok','تکمیل‌شده',c.ok,'devices')
    +k('k-problem','مشتری بدهکار',c.debt,'customers')
    +'</div>';
}

/* ================= devices (card grid) ================= */
function devicesHtml(){
  var ds=(STATE.devices||[]).slice().sort((a,b)=>
    (needsAction(b)-needsAction(a)) || String(b.updated||'').localeCompare(String(a.updated||'')));
  var runningBanner=(STATE.estelam_running)
    ?'<div class="row" style="margin:0 0 12px;align-items:center">'
      +'<span class="pill warn">ربات استعلام در حال اجراست</span>'
      +'<button class="btn mini danger" onclick="crmStopRun()">'+ICONS.stop+' توقف ربات استعلام</button></div>'
    :'';
  if(!ds.length) return runningBanner+'<div class="row" style="margin-bottom:12px">'
    +'<button class="btn primary" onclick="dlgDevice()">+ پروندهٔ جدید</button>'
    +'<span class="muted">اولین پرونده را بسازید: سریال دستگاه، مشتری و در ادامه مالک.</span></div>'
    +emptyHtml('پرونده‌ای ثبت نشده','هنوز پرونده‌ای نیست — با دکمهٔ «+ پروندهٔ جدید» بالا شروع کنید.');
  var cards=ds.map(function(d){
    var est=d.estelam||{};
    var acts=stageActions(d).map(a=>a[0]?
      '<button class="btn mini '+(a[2]==='danger'?'danger':(a[2]==='primary'?'primary':'ghost'))+'" onclick="devTick(\''+d.id+'\',\''+a[0]+'\')">'+a[1]+'</button>':'').join('');
    return '<div class="dev'+(needsAction(d)?' needs-action':'')+'">'
      +'<div class="devhead">'+ICONS.dev+'<b>'+esc(d.name)+'</b>'
      +'<span class="pill '+(STAGE_TONE[d.stage]||'n')+'">'+(STAGE_FA[d.stage]||esc(d.stage))+'</span>'
      +(d.auto_advance?'<span class="chip">پرداخت خودکار</span>':'')+'</div>'
      +'<div class="ser">'+esc(d.serial1)+(d.serial2?' / '+esc(d.serial2):'')+'</div>'
      +railHtml(d)
      +'<div class="meta">'
      +ICONS.user+'<span class="chip">'+esc(custName(d.customer_id))+'</span>'
      +ICONS.id+'<span class="chip">'+esc(ownerName(d.owner_id))+'</span>'
      +'<span class="pill '+(EST_PILL[est.status]||'n')+'">استعلام: '+(EST_FA[est.status]||esc(est.status||'—'))+'</span>'
      +(est.hamta_status?'<span class="pill n">همتا: '+esc(est.hamta_status)+'</span>':'')
      +'</div>'
      +(est.result?'<div class="kbd">نتیجه: '+esc(est.result)+'</div>':'')
      +'<div class="kbd">آخرین استعلام: '+esc(d.last_inquiry_at||'—')+'</div>'
      +'<div class="acts">'
      +'<button class="btn mini ghost" onclick="devInquire(\''+d.id+'\',\'gomrok\')">'+ICONS.search+' استعلام گمرک</button>'
      +'<button class="btn mini ghost" onclick="devInquire(\''+d.id+'\',\'hamta\')">'+ICONS.swap+' استعلام همتا</button>'
      +(STATE.estelam_running?'<button class="btn mini danger" onclick="crmStopRun()">'+ICONS.stop+' توقف ربات استعلام</button>':'')
      +(acts?'':'<span class="muted">—</span>')
      +(acts?'<span style="flex:1"></span>'+acts:'')
      +' <button class="btn mini danger" onclick="devDelete(\''+d.id+'\')">حذف</button></div>'
      +'</div>';
  }).join('');
  return '<div class="row" style="margin-bottom:12px">'
    +'<button class="btn primary" onclick="dlgDevice()">+ پروندهٔ جدید</button>'
    +'<span class="muted">مرتب‌شده: نیازمند اقدام در بالا · استعلام‌ها خودکار و یک‌بار-per-device اجرا می‌شوند</span></div>'
    +'<div class="devgrid">'+cards+'</div>';
}
function emptyHtml(title,sub){
  return '<div class="card"><div class="empty"><b>'+esc(title)+'</b>'+esc(sub)+'</div></div>';
}

/* ================= customers ================= */
function customersHtml(){
  var rows=(STATE.customers||[]).map(c=>
    '<tr><td><b>'+esc(c.full_name)+'</b></td><td class="kbd">'+esc(c.mobile||'—')+'</td>'
    +'<td class="kbd">'+esc(c.telegram_id||'—')+' / '+esc(c.instagram_id||'—')+'</td>'
    +'<td class="kbd">'+esc(c.address||'—')+'</td>'
    +'<td>'+(bal(c.id)<0?'<span class="pill bad">بدهکار '+money(bal(c.id))+'</span>'
             :(bal(c.id)>0?'<span class="pill ok">'+money(bal(c.id))+'</span>':'<span class="pill n">تسویه</span>'))+'</td>'
    +'<td><button class="btn mini" onclick="dlgCustomer(\'edit\',\''+c.id+'\')">ویرایش</button> '
    +'<button class="btn mini" onclick="dlgTx(\''+c.id+'\')">تراکنش</button> '
    +'<button class="btn mini danger" onclick="custDelete(\''+c.id+'\')">حذف</button></td></tr>').join('');
  return '<div class="card"><div class="row" style="margin-bottom:10px">'
    +'<button class="btn primary" onclick="dlgCustomer(\'add\')">+ مشتری جدید</button></div>'
    +'<table><thead><tr><th>نام</th><th>موبایل</th><th>تلگرام / اینستا</th><th>آدرس</th><th>مانده</th><th></th></tr></thead>'
    +'<tbody>'+(rows||'<tr><td colspan="6"><div class="empty"><b>مشتری ثبت نشده است</b>مشتری‌ها را از تلگرام، اینستاگرام یا تماس تلفنی اضافه کنید.</div></td></tr>')+'</tbody></table></div>';
}

/* ================= owners ================= */
function ownersHtml(){
  var rows=(STATE.owners||[]).map(o=>{
    var dev=(STATE.devices||[]).find(d=>d.owner_id===o.id);
    return '<tr><td><b>'+esc(o.first_name+' '+o.last_name)+'</b></td>'
    +'<td class="kbd">'+esc(o.national)+'</td><td class="kbd">'+esc(o.birth)+'</td>'
    +'<td class="kbd">'+esc(o.mobile)+'</td><td class="kbd">'+esc(o.card_no)+'</td>'
    +'<td>'+(dev?'<span class="pill n">'+esc(dev.name)+'</span>':'<span class="pill warn">آزاد</span>')+'</td>'
    +'<td><button class="btn mini" onclick="dlgOwner(\'edit\',\''+o.id+'\')">ویرایش</button> '
    +(dev?'':'<button class="btn mini danger" onclick="ownerDelete(\''+o.id+'\')">حذف</button>')+'</td></tr>';
  }).join('');
  return '<div class="card"><div class="row" style="margin-bottom:10px">'
    +'<button class="btn primary" onclick="dlgOwner(\'add\')">+ مالک جدید</button>'
    +'<span class="muted">کد ملی با رقم کنترلی بررسی می‌شود؛ سن ۱۵ تا ۹۹ سال مجاز است.</span></div>'
    +'<table><thead><tr><th>نام</th><th>کد ملی</th><th>تولد</th><th>موبایل</th><th>کارت</th><th>دستگاه</th><th></th></tr></thead>'
    +'<tbody>'+(rows||'<tr><td colspan="7"><div class="empty"><b>مالکی ثبت نشده است</b>مشخصات مالک برای استعلام نظارتی و ثبت‌نام لازم است.</div></td></tr>')+'</tbody></table></div>';
}

/* ================= identity pool ================= */
function identitiesHtml(){
  var rows=(STATE.identities||[]).map(i=>
    '<tr><td><b>'+esc(i.label)+'</b></td><td class="kbd">'+esc(i.national)+'</td>'
    +'<td class="kbd">'+esc(i.birth)+'</td><td class="kbd">'+esc(i.mobile)+'</td>'
    +'<td><button class="btn mini" onclick="dlgIdent(\'edit\',\''+i.id+'\')">ویرایش</button> '
    +'<button class="btn mini danger" onclick="identDelete(\''+i.id+'\')">حذف</button></td></tr>').join('');
  return '<div class="card"><div class="row" style="margin-bottom:10px">'
    +'<button class="btn primary" onclick="dlgIdent(\'add\')">+ مشخصات جدید</button>'
    +'<span class="muted">برای استعلام‌های ورودی یک مشخصات تصادفی معتبر انتخاب می‌شود؛ استعلام نظارتی پس از تحویل با هویت مالک اجرا می‌شود.</span></div>'
    +'<table><thead><tr><th>عنوان</th><th>کد ملی</th><th>تولد</th><th>موبایل</th><th></th></tr></thead>'
    +'<tbody>'+(rows||'<tr><td colspan="5"><div class="empty"><b>استخر هویت خالی است</b>بدون حداقل یک مشخصات معتبر، استعلام ورودی اجرا نمی‌شود.</div></td></tr>')+'</tbody></table></div>';
}

/* ================= ledger ================= */
function ledgerHtml(){
  var rows='';
  (STATE.customers||[]).forEach(c=>{
    (c.transactions||[]).slice().reverse().forEach(t=>{
      rows+='<tr><td><b>'+esc(c.full_name)+'</b></td><td class="kbd">'+esc(t.at)+'</td>'
      +'<td>'+(t.amount<0?'<span class="pill bad">'+money(t.amount)+'</span>':'<span class="pill ok">'+money(t.amount)+'</span>')+'</td>'
      +'<td class="kbd">'+esc(t.note||'—')+'</td></tr>';
    });
  });
  return '<div class="card"><table><thead><tr><th>مشتری</th><th>زمان</th><th>مبلغ</th><th>شرح</th></tr></thead>'
    +'<tbody>'+(rows||'<tr><td colspan="4"><div class="empty"><b>تراکنشی ثبت نشده است</b>از تب مشتریان روی «تراکنش» بزنید.</div></td></tr>')+'</tbody></table></div>';
}

/* ================= reports ================= */
function reportsHtml(){
  return '<div class="card">'
    +'<div class="row"><button class="btn" onclick="location.href=\'/crm-report?fmt=txt\'">دانلود گزارش متنی</button>'
    +'<button class="btn" onclick="window.open(\'/crm-report?fmt=html\',\'_blank\')">گزارش چاپی</button>'
    +'<button class="btn" onclick="location.href=\'/crm-report?fmt=pdf\'">PDF</button>'
    +'<button class="btn primary" onclick="sendTelegram()">ارسال به تلگرام</button></div>'
    +'<p class="muted">گزارش شامل وضعیت تمام پرونده‌هاست؛ در تلگرام کد ملی‌ها ماسک می‌شوند و شمارهٔ کارت هرگز ارسال نمی‌شود.</p>'
    +'<h3 style="font-size:14px;margin:14px 0 8px">لاگ رویدادها</h3>'
    +'<div id="logBox"></div>'
    +'</div>';
}

/* ================= dialogs ================= */
function showFieldErrors(errText){
  /* ux-guidelines (Forms/Error Placement): identify each invalid field under
     the input, not only a top-level blob; server sends "field: message" lines
     where it can (English field names from crm.py validation). */
  var box=document.querySelector('.err:not([hidden])');
  if(!box)return;
  box.textContent=errText;
  var lines=String(errText).split('\n');
  lines.forEach(function(line){
    var m=line.match(/^\s*([a-z_]+)\s*[:：]\s*(.+)$/i);
    if(!m)return;
    var input=document.querySelector('#dlgBody [id], #dlgBody input, #dlgBody select');
    var el=null;
    try{el=document.getElementById('dlgBody').querySelector('[id^="'+m[1].slice(0,4)+'"], [id$="'+m[1].slice(-4)+'"]');}catch(e){}
    if(el){el.setAttribute('aria-invalid','true');}
  });
}
function dlgDevice(){
  var custOpts='<option value="">— مشتری جدید —</option>'+(STATE.customers||[]).map(c=>'<option value="'+c.id+'">'+esc(c.full_name)+'</option>').join('');
  openDlg('<h3>پروندهٔ دستگاه جدید</h3>'
    +'<div class="grid" style="margin-bottom:8px">'
    +'<label class="f">نام دستگاه *<input id="dvName" placeholder="iPhone 13"></label>'
    +'<label class="f">سریال اول * (IMEI ۱۵ رقمی)<input id="dvS1" inputmode="numeric" maxlength="15" placeholder="356938035643809"></label>'
    +'<label class="f">سریال دوم (اختیاری)<input id="dvS2" inputmode="numeric" maxlength="15" placeholder=""></label>'
    +'</div>'
    +'<div class="row"><label class="f">مشتری *<select id="dvCust" onchange="document.getElementById(\'dvNewCustRow\').style.display=this.value?\'none\':\'block\'">'+custOpts+'</select></label>'
    +'<label class="f check"><input type="checkbox" id="dvAuto"><span class="box" aria-hidden="true"></span><span class="ft">پرداخت خودکار پس از ثبت در گمرک</span></label></div>'
    +'<div id="dvNewCustRow" style="display:block;margin-top:8px"><div class="grid">'
    +'<label class="f">نام مشتری *<input id="dvCn"></label>'
    +'<label class="f">موبایل (۰۹… ۱۱ رقمی)<input id="dvCm" inputmode="tel" maxlength="11" placeholder="09121234567"></label>'
    +'<label class="f">تلگرام (اختیاری)<input id="dvTg" dir="ltr" placeholder="@username"></label>'
    +'<label class="f">اینستاگرام (اختیاری)<input id="dvIg" dir="ltr" placeholder="@username"></label>'
    +'<label class="f">آدرس (اختیاری)<input id="dvCa"></label>'
    +'<label class="f">یادداشت دستگاه (اختیاری)<input id="dvNotes"></label>'
    +'</div></div>'
    +'<div class="err" id="dvErr"></div>'
    +'<div class="row" style="margin-top:10px"><button class="btn primary" onclick="doAddDevice()">ثبت پرونده</button><button class="btn" onclick="closeDlg()">انصراف</button></div>');
}
function doAddDevice(){
  var mobile=digitsFa(v('dvCm'));
  if(mobile && !/^09\d{9}$/.test(mobile)){
    document.getElementById('dvErr').textContent='موبایل باید ۱۱ رقم و با ۰۹ شروع شود';
    var el=document.getElementById('dvCm');if(el)el.setAttribute('aria-invalid','true');
    return;
  }
  var el=document.getElementById('dvCm');if(el)el.removeAttribute('aria-invalid');
  var payload={action:'add',name:v('dvName'),serial1:v('dvS1'),serial2:v('dvS2'),
    notes:v('dvNotes'),auto_advance:document.getElementById('dvAuto').checked};
  var custSel=v('dvCust');
  if(custSel){payload.customer_id=custSel;}
  else payload.customer={full_name:v('dvCn'),mobile:mobile,telegram_id:v('dvTg'),
    instagram_id:v('dvIg'),address:v('dvCa')};
  api('/crm-device',payload).then(async()=>{closeDlg();toast('پرونده ثبت شد');await refresh();})
    .catch(e=>{showFieldErrors(String(e));});
}
function digitsFa(s){return String(s||'').replace(/[۰-۹]/g,d=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/[^0-9]/g,'');}
function dlgAttach(id){
  var opts=(STATE.owners||[]).map(o=>'<option value="'+o.id+'">'+esc(o.first_name+' '+o.last_name)+' — '+esc(o.national)+'</option>').join('');
  openDlg('<h3>اتصال مالک به دستگاه</h3><div class="row"><select id="attOwner">'+opts+'</select>'
    +'<button class="btn primary" onclick="doAttach(\''+id+'\')">اتصال</button></div>'
    +'<p class="muted">هر مالک فقط به یک دستگاه متصل می‌شود (نسبت یک‌به‌یک).</p>');
}
function doAttach(id){
  api('/crm-tick',{device_id:id,action:'attach_owner',owner_id:document.getElementById('attOwner').value})
    .then(async()=>{closeDlg();toast('مالک متصل شد');await refresh();})
    .catch(e=>toast(e,false));
}
function devInquire(id, service){
  var labels={gomrok:'گمرک',hamta:'همتا',all:'همهٔ استعلام‌ها'};
  confirmDlg('اجرای استعلام','استعلام «'+(labels[service]||service)+'» برای این دستگاه اجرا شود؟',
    'اجرا',function(){
      api('/crm-inquire',{device_id:id,service:service}).then(async r=>{
        toast('استعلام شروع شد'+(r.pid?' (pid '+r.pid+')':''));await refresh();})
        .catch(e=>toast(e,false));
    });
}
function crmStopRun(){
  confirmDlg('توقف ربات استعلام','ربات استعلام متوقف شود؟ مرورگرِ در حال اجرا بسته می‌شود و ردیف‌های در جریان به «در صف» برمی‌گردند.',
    'توقف',function(){
      api('/crm-stop',{}).then(async r=>{
        toast(r.stopped?'استعلام متوقف و مرورگر بسته شد':'اجرای فعالی برای توقف نبود');await refresh();})
        .catch(e=>toast(e,false));
    });
}
function devDelete(id){
  if(!confirm('این پرونده حذف شود؟ ردیف استعلام آن نیز حذف می‌شود و برگشت‌پذیر نیست.'))return;
  api('/crm-device',{action:'delete',id:id}).then(async()=>{toast('حذف شد');await refresh();}).catch(e=>toast(e,false));
}
function dlgCustomer(mode,id){
  var c = id?findCustomer(id):{};
  openDlg('<h3>'+(id?'ویرایش مشتری':'مشتری جدید')+'</h3>'
    +'<div class="grid">'
    +'<label class="f">نام و نام خانوادگی *<input id="cName" value="'+esc(c.full_name||'')+'"></label>'
    +'<label class="f">موبایل<input id="cMobile" value="'+esc(c.mobile||'')+'" inputmode="tel"></label>'
    +'<label class="f">تلگرام<input id="cTg" value="'+esc(c.telegram_id||'')+'"></label>'
    +'<label class="f">اینستاگرام<input id="cIg" value="'+esc(c.instagram_id||'')+'"></label>'
    +'</div>'
    +'<label class="f" style="margin-top:8px">آدرس<textarea id="cAddr" style="width:100%">'+esc(c.address||'')+'</textarea></label>'
    +'<label class="f" style="margin-top:8px">یادداشت<textarea id="cNotes" style="width:100%">'+esc(c.notes||'')+'</textarea></label>'
    +'<div class="err" id="cErr"></div>'
    +'<div class="row" style="margin-top:10px"><button class="btn primary" onclick="doSaveCustomer(\''+(id||'')+'\')">ذخیره</button><button class="btn" onclick="closeDlg()">انصراف</button></div>');
}
function doSaveCustomer(id){
  var payload={action:id?'update':'add',full_name:v('cName'),mobile:v('cMobile'),
    telegram_id:v('cTg'),instagram_id:v('cIg'),address:v('cAddr'),notes:v('cNotes')};
  if(id)payload.id=id;
  api('/crm-customer',payload).then(async()=>{closeDlg();toast('ذخیره شد');await refresh();})
    .catch(e=>{document.getElementById('cErr').textContent=e;showFieldErrors(String(e));});
}
function custDelete(id){
  var hasDevices=(STATE.devices||[]).some(d=>d.customer_id===id);
  var msg=hasDevices?'این مشتری دستگاه دارد؛ دستگاه‌ها فقط از اتصال مشتری آزاد می‌شوند. حذف شود؟'
                    :'مشتری حذف شود؟';
  if(!confirm(msg))return;
  api('/crm-customer',{action:'delete',id:id,force:true}).then(async()=>{toast('حذف شد');await refresh();}).catch(e=>toast(e,false));
}
function dlgTx(cid){
  openDlg('<h3>تراکنش جدید — '+esc(custName(cid))+'</h3>'
    +'<div class="row"><label class="f">مبلغ (تومان؛ بدهی منفی)<input id="txAmt" inputmode="numeric" style="width:170px"></label>'
    +'<label class="f">شرح<input id="txNote" style="flex:1"></label></div>'
    +'<div class="err" id="txErr"></div>'
    +'<div class="row" style="margin-top:10px"><button class="btn primary" onclick="doTx(\''+cid+'\')">ثبت</button><button class="btn" onclick="closeDlg()">انصراف</button></div>');
}
function doTx(cid){
  api('/crm-customer',{action:'tx',id:cid,amount:v('txAmt'),note:v('txNote')})
    .then(async()=>{closeDlg();toast('ثبت شد');await refresh();})
    .catch(e=>{document.getElementById('txErr').textContent=e;showFieldErrors(String(e));});
}
function dlgOwner(mode,id){
  var o = id?findOwner(id):{};
  openDlg('<h3>'+(id?'ویرایش مالک':'مالک جدید')+'</h3><div class="grid">'
    +'<label class="f">نام *<input id="oFn" value="'+esc(o.first_name||'')+'"></label>'
    +'<label class="f">نام خانوادگی *<input id="oLn" value="'+esc(o.last_name||'')+'"></label>'
    +'<label class="f">کد ملی *<input id="oNat" value="'+esc(o.national||'')+'" inputmode="numeric"></label>'
    +'<label class="f">تاریخ تولد * (۱۳۷۵/۰۴/۲۹)<input id="oBirth" value="'+esc(o.birth||'')+'"></label>'
    +'<label class="f">موبایل *<input id="oMob" value="'+esc(o.mobile||'')+'" inputmode="tel"></label>'
    +'<label class="f">شماره کارت ۱۶ رقمی *<input id="oCard" value="'+esc(o.card_no||'')+'" inputmode="numeric"></label>'
    +'</div><div class="err" id="oErr"></div>'
    +'<div class="row" style="margin-top:10px"><button class="btn primary" onclick="doSaveOwner(\''+(id||'')+'\')">ذخیره</button><button class="btn" onclick="closeDlg()">انصراف</button></div>');
}
function doSaveOwner(id){
  var payload={action:id?'update':'add',first_name:v('oFn'),last_name:v('oLn'),
    national:v('oNat'),birth:v('oBirth'),mobile:v('oMob'),card_no:v('oCard')};
  if(id)payload.id=id;
  api('/crm-owner',payload).then(async()=>{closeDlg();toast('ذخیره شد');await refresh();})
    .catch(e=>{document.getElementById('oErr').textContent=e;showFieldErrors(String(e));});
}
function ownerDelete(id){
  if(!confirm('این مالک حذف شود؟'))return;
  api('/crm-owner',{action:'delete',id:id}).then(async()=>{toast('حذف شد');await refresh();}).catch(e=>toast(e,false));
}
function dlgIdent(mode,id){
  var i = id?(STATE.identities||[]).find(x=>x.id===id)||{}:{};
  openDlg('<h3>'+(id?'ویرایش مشخصات':'مشخصات استعلام جدید')+'</h3><div class="grid">'
    +'<label class="f">عنوان<input id="iLabel" value="'+esc(i.label||'')+'"></label>'
    +'<label class="f">کد ملی *<input id="iNat" value="'+esc(i.national||'')+'" inputmode="numeric"></label>'
    +'<label class="f">تاریخ تولد * (۱۳۷۵/۰۴/۲۹)<input id="iBirth" value="'+esc(i.birth||'')+'"></label>'
    +'<label class="f">موبایل *<input id="iMob" value="'+esc(i.mobile||'')+'" inputmode="tel"></label>'
    +'</div><div class="err" id="iErr"></div>'
    +'<div class="row" style="margin-top:10px"><button class="btn primary" onclick="doSaveIdent(\''+(id||'')+'\')">ذخیره</button><button class="btn" onclick="closeDlg()">انصراف</button></div>');
}
function doSaveIdent(id){
  var payload={action:id?'update':'add',label:v('iLabel'),national:v('iNat'),
    birth:v('iBirth'),mobile:v('iMob')};
  if(id)payload.id=id;
  api('/crm-identity',payload).then(async()=>{closeDlg();toast('ذخیره شد');await refresh();})
    .catch(e=>{document.getElementById('iErr').textContent=e;showFieldErrors(String(e));});
}
function identDelete(id){
  if(!confirm('مشخصات از استخر حذف شود؟'))return;
  api('/crm-identity',{action:'delete',id:id}).then(async()=>{toast('حذف شد');await refresh();}).catch(e=>toast(e,false));
}
function sendTelegram(){
  api('/crm-report-telegram',{full_data:document.documentElement.dataset.full==='1'})
    .then(r=>toast('ارسال شد ('+fa(r.chunks)+' بخش)')).catch(e=>toast(e,false));
}

/* ================= render ================= */
function renderTabs(){
  var counts={devices:(STATE&&STATE.devices||[]).length,customers:(STATE&&STATE.customers||[]).length,
    owners:(STATE&&STATE.owners||[]).length,identities:(STATE&&STATE.identities||[]).length,ledger:null,reports:null};
  document.getElementById('tabs').innerHTML = TABS.map(t=>
    '<button class="tab'+(TAB===t[0]?' active':'')+'" onclick="setTab(\''+t[0]+'\')">'+t[1]
    +(counts[t[0]]!=null?'<span class="cnt">'+fa(counts[t[0]])+'</span>':'')+'</button>').join('');
}
function setTab(t){TAB=t;renderTabs();renderView();}
function renderLogs(){
  var box=document.getElementById('logBox');
  if(!box)return;
  box.innerHTML=(LOGS||[]).slice().reverse().map(l=>
    '<div class="kbd">'+esc(l.at)+' — <b>'+esc(l.event)+'</b> '+esc(Object.entries(l).filter(([k])=>!['at','event','level'].includes(k)).map(([k,v])=>k+'='+v).join(' '))+'</div>').join('');
}
function renderView(){
  var el=document.getElementById('view');
  if(!STATE){ el.innerHTML='<div class="skel"></div><div class="skel"></div><div class="skel"></div>';return; }
  var head='<div id="kpis">'+kpisHtml()+'</div>';
  var body;
  if(TAB==='devices')body=devicesHtml();
  else if(TAB==='customers')body=customersHtml();
  else if(TAB==='owners')body=ownersHtml();
  else if(TAB==='identities')body=identitiesHtml();
  else if(TAB==='ledger')body=ledgerHtml();
  else body=reportsHtml();
  el.innerHTML=head+body;
  renderLogs();
}
async function refresh(){
  try{
    var r=await Promise.all([fetch('/crm-state'),fetch('/crm-logs')]);
    if(!r[0].ok||!r[1].ok)throw 0;
    STATE=await r[0].json();
    LOGS=(await r[1].json()).logs||[];
    LOADFAIL=false;
  }catch(e){ LOADFAIL=true; }
  renderView();
  if(LOADFAIL)toast('ارتباط با پنل برقرار نشد — تلاش مجدد…',false);
}
renderTabs();
refresh();
setInterval(refresh, 5000);
</script>

</body>
</html>"""



def render_crm_page():
    """CRM page (full UI in phase E); minimal functional shell for now."""
    return CRM_PAGE


def render_estelam_page():
    state = estelam_load()
    state["runtime"] = estelam_runtime()
    html = ESTELAM_PAGE
    html = html.replace("__ESTELAM_JSON__", json.dumps(state, ensure_ascii=False))
    html = html.replace("__STATUS_JSON__",
                        json.dumps(ESTELAM_STATUS_FA, ensure_ascii=False))
    html = html.replace("__BADGE_JSON__",
                        json.dumps(ESTELAM_BADGE_FA, ensure_ascii=False))
    html = html.replace("__VERSION__", app_version_text() or "—")
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

    def _crm_entity(self, route, data):
        """Dispatch add/update/delete for the four CRM entity routes."""
        import crm as crm_mod
        action = str(data.get("action") or "").strip().lower()
        record_id = str(data.get("id") or "").strip()
        entity = route.rsplit("-", 1)[1]          # customer|owner|identity|device
        record, errors, extra = None, [], {}
        if entity == "customer":
            if action == "add":
                record, errors = crm_mod.add_customer(data)
            elif action == "update":
                record, errors = crm_mod.update_customer(record_id, data)
            elif action == "tx":
                record, errors = crm_mod.add_transaction(
                    record_id, data.get("amount"), data.get("note") or "")
            elif action == "delete":
                ok, errors = crm_mod.delete_customer(record_id, force=bool(data.get("force")))
                record = {"ok": ok} if ok else None
            else:
                errors = ["کنش نامعتبر است"]
        elif entity == "owner":
            if action == "add":
                record, errors = crm_mod.add_owner(data)
            elif action == "update":
                record, errors = crm_mod.update_owner(record_id, data)
            elif action == "delete":
                ok, errors = crm_mod.delete_owner(record_id)
                record = {"ok": ok} if ok else None
            else:
                errors = ["کنش نامعتبر است"]
        elif entity == "identity":
            if action == "add":
                record, errors = crm_mod.add_identity(data)
            elif action == "update":
                record, errors = crm_mod.update_identity(record_id, data)
            elif action == "delete":
                ok, errors = crm_mod.delete_identity(record_id)
                record = {"ok": ok} if ok else None
            else:
                errors = ["کنش نامعتبر است"]
        else:  # device
            if action == "add":
                inline = data.get("customer") if isinstance(data.get("customer"), dict) else None
                record, errors = crm_mod.add_device(data, customer=inline)
            elif action == "update":
                record, errors = crm_mod.update_device(record_id, data)
            elif action == "delete":
                ok, errors = crm_mod.delete_device(record_id)
                record = {"ok": ok} if ok else None
            else:
                errors = ["کنش نامعتبر است"]
        if errors:
            crm_mod.crm_log("warn", f"{entity}_{action}_rejected",
                            entity_id=record_id, errors="; ".join(errors))
            self._json({"ok": False, "errors": errors}, 400)
            return
        crm_mod.crm_log("info", f"{entity}_{action}",
                        entity_id=str((record or {}).get("id") or record_id))
        payload = {"ok": True, "record": record}
        if entity == "device" and action == "add":
            payload["device"] = record
        payload.update(extra)
        self._json(payload)

    def _crm_start_payment(self, device_id):
        """Advance to payment. Explicit linked profile_id ONLY (review F18/F9):
        start_profile() with no id pool-claims ANY free profile."""
        import crm as crm_mod
        data = crm_mod.store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not device:
            self._json({"ok": False, "errors": ["دستگاه پیدا نشد"]}, 404)
            return
        profile_id = str(device.get("profile_id") or "").strip()
        if not profile_id:
            self._json({"ok": False,
                        "errors": ["پروفایل ثبت‌نام برای این دستگاه ساخته نشده است"]}, 400)
            return
        status, payload = start_profile(profile_id)
        if status != 200:
            crm_mod.crm_log("error", "payment_start_failed", device_id=device_id,
                            profile_id=profile_id, detail=str(payload))
            message = "شروع ربات انجام نشد"
            if isinstance(payload, dict):
                message = str(payload.get("error") or message)
            self._json({"ok": False, "errors": [message]}, status)
            return
        device, errors = crm_mod.advance(device_id, "start_payment")
        crm_mod.crm_log("info", "payment_started", device_id=device_id,
                        profile_id=profile_id)
        self._json({"ok": True, "device": device, "pid": payload.get("pid")})

    def _crm_report_telegram(self, full_data):
        import crm as crm_mod
        state = crm_state_payload()
        allow_full = bool((state.get("settings") or {})
                          .get("telegram_report_full_data", True)) \
            and bool(full_data)
        text = crm_mod.build_report(state, fmt="txt",
                                    full_data=allow_full, plain=True)
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
        sent = 0
        try:
            import telegram_notify as tg
            for chunk in chunks:
                if tg.send_message(chunk, timeout=20):
                    sent += 1
        except Exception as exc:
            crm_mod.crm_log("error", "report_telegram_failed", detail=str(exc))
            self._json({"ok": False, "errors": ["ارسال تلگرام ناموفق بود"]}, 502)
            return
        crm_mod.crm_log("info", "report_telegram_sent", chunks=sent)
        self._json({"ok": True, "chunks": sent})

    def _crm_report(self, query):
        """Devices report; fmt=txt (download) | html (printable) | pdf
        (headless Edge --print-to-pdf; HTML fallback with a print note)."""
        import crm as crm_mod
        fmt = (query.get("fmt") or ["txt"])[0]
        stages = (query.get("stage") or [""])[0]
        stage_filter = [s for s in stages.split(",") if s] or None
        state = crm_state_payload()
        report = crm_mod.build_report(state, stage_filter=stage_filter,
                                      fmt="html" if fmt in ("html", "pdf") else "txt")
        if fmt == "txt":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="crm-report.txt"')
            self.end_headers()
            self.wfile.write(report.encode("utf-8"))
            return
        if fmt == "pdf":
            pdf_bytes = crm_html_to_pdf(report)
            if pdf_bytes:
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 'inline; filename="crm-report.pdf"')
                self.send_header("Content-Length", str(len(pdf_bytes)))
                self.end_headers()
                self.wfile.write(pdf_bytes)
                return
            report = report.replace(
                "</body>",
                "<script>window.onload=()=>setTimeout(()=>window.print(),300)"
                "</script></body>")
            self._send(200, report.encode("utf-8"), "text/html; charset=utf-8")
            return
        self._send(200, report.encode("utf-8"), "text/html; charset=utf-8")

    def _state_json(self, code=200):
        """load_store-e amn baraye route-haye read: agar file lahze-i kharab bood,
        be jaye 500/khali, akharin store-e salem barmigardanad (UI khali nashavad)."""
        try:
            self._json(load_store(), code)
        except StoreUnreadableError:
            cached = _LAST_GOOD_STORE.get(PROFILES_PATH)
            if cached is not None:
                self._json(cached, code)
            else:
                # hich manba'-e salemi nist: 503 ba flag ta JS store-e fe'li ra negah darad.
                self._json({"error": "store temporarily unreadable", "retry": True}, 503)

    def do_GET(self):
        from urllib.parse import urlsplit
        route = urlsplit(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, render_home_page())
        elif route == "/launch":
            self._send(200, render_launch_page())
        elif route in ("/fonts/Vazirmatn-var.woff2",
                       "/fonts/Vazirmatn-Bold.woff2"):
            path = os.path.join(HERE, route.lstrip("/").replace("/", os.sep))
            try:
                with open(path, "rb") as fh:
                    self._send(200, fh.read(), "font/woff2")
            except OSError:
                self._send(404, b"not found", "text/plain")
        elif route == "/favicon.ico":
            svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
                   "<rect width='32' height='32' rx='7' fill='%236d8bff'/>"
                   "<text x='16' y='22' font-size='16' font-family='sans-serif' "
                   "font-weight='bold' fill='white' text-anchor='middle'>I</text></svg>")
            self._send(200, svg.encode("utf-8"), "image/svg+xml")
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
            self._state_json()
        elif route == "/logs":
            self._json(load_logs())
        elif route == "/site2-notif":
            self._json(load_site2_notif())
        elif route == "/telegram-health":
            from urllib.parse import parse_qs
            query = parse_qs(urlsplit(self.path).query)
            if query.get("run") == ["1"]:
                self._json(test_telegram_api(attempts=2))
            else:
                self._json(load_telegram_health())
        elif route == "/system-debug-config":
            self._json(load_system_debug_config())
        elif route == "/system-logs":
            self._json({"logs": list_system_logs()})
        elif route == "/system-log":
            from urllib.parse import parse_qs
            query = parse_qs(urlsplit(self.path).query)
            name = (query.get("name") or [""])[0]
            try:
                self._json({"name": name, "content": read_system_log(name)})
            except ValueError:
                self._json({"error": "نام فایل لاگ نامعتبر است"}, 400)
            except FileNotFoundError:
                self._json({"error": "فایل لاگ پیدا نشد"}, 404)
        elif route == "/artifact":
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            pid, kind = (q.get("id") or [""])[0], (q.get("kind") or [""])[0]
            key = "receipt_screenshot" if kind == "receipt" else "payment_screenshot"
            try:
                profiles = load_store().get("profiles", [])
            except StoreUnreadableError:
                profiles = (_LAST_GOOD_STORE.get(PROFILES_PATH) or {}).get("profiles", [])
            profile = next((p for p in profiles if p.get("id") == pid), None)
            path = os.path.realpath((profile or {}).get(key) or "")
            # Screenshots now live in the data directory. Paths recorded by an
            # older release point inside that release folder, so both roots are
            # accepted -- containment is still enforced against each of them.
            roots = [os.path.realpath(app_paths.data_subdir("screenshots")),
                     os.path.realpath(os.path.join(HERE, "screenshots"))]
            allowed = False
            for root in roots:
                try:
                    if os.path.commonpath([root, path]) == root:
                        allowed = True
                        break
                except ValueError:
                    continue
            if profile and allowed and os.path.isfile(path):
                with open(path, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, b"not found", "text/plain")
        elif route == "/crm":
            self._send(200, render_crm_page().encode("utf-8"))
        elif route == "/crm-state":
            self._json(crm_state_payload())
        elif route == "/crm-logs":
            self._json({"logs": load_crm_logs()})
        elif route == "/crm-report":
            from urllib.parse import parse_qs
            query = parse_qs(urlsplit(self.path).query)
            self._crm_report(query)
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        try:
            store = load_store()
        except StoreUnreadableError:
            # profiles.json lahze-i ghabel-e khandan nist. Az cache estefade mikonim
            # ta write-e badi rooye dade-ye salem anjam shavad (na baznevisi-ye khali).
            cached = _LAST_GOOD_STORE.get(PROFILES_PATH)
            if cached is None:
                self._json({"error": "store temporarily unreadable", "retry": True}, 503)
                return
            store = {"profiles": [dict(p) for p in cached["profiles"]],
                     "active_id": cached["active_id"]}
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

        elif self.path == "/profile-device-inquiry":
            data = self._read_json()
            status, payload = start_profile_device_inquiry(
                str(data.get("id") or ""), str(data.get("service") or ""))
            self._json(payload, status)

        elif self.path == "/profile-device-stop":
            data = self._read_json()
            ok, message = stop_profile_inquiry(str(data.get("id") or ""))
            payload = {"ok": ok, "message": message}
            if ok:
                payload["store"] = load_store()
            self._json(payload, 200 if ok else 404)

        elif self.path == "/shutdown":
            self._json(shutdown_everything())

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

        elif self.path == "/release-blocked":
            data = self._read_json()
            ok, message = release_blocked_profile_with_reason(str(data.get("id") or ""))
            payload = {"ok": ok, "message": message}
            if ok:
                payload["store"] = load_store()
            self._json(payload, 200 if ok else 409)

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

        elif self.path == "/browser-visibility":
            data = self._read_json()
            status, payload = set_browser_visibility(
                str(data.get("id") or ""), bool(data.get("visible")))
            self._json(payload, status)

        elif self.path == "/clear-logs":
            clear_logs()
            self._json({"ok": True})

        elif self.path == "/system-debug-config":
            self._json(save_system_debug_config(self._read_json()))

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

        elif self.path in ("/crm-customer", "/crm-owner", "/crm-identity",
                           "/crm-device"):
            self._crm_entity(self.path, self._read_json())

        elif self.path == "/crm-inquire":
            data = self._read_json()
            service = str(data.get("service") or "")
            if service not in ESTELAM_SERVICES:
                self._json({"ok": False,
                            "errors": ["نوع استعلام نامعتبر است"]}, 400)
                return
            result = crm_start_inquiry(str(data.get("device_id") or ""),
                                       service=service, origin="manual")
            self._json(result, 200 if result.get("ok") else 400)

        elif self.path == "/crm-stop":
            self._json(estelam_stop())

        elif self.path == "/crm-tick":
            data = self._read_json()
            device, errors = crm_advance_logged(str(data.get("device_id") or ""),
                                                str(data.get("action") or ""),
                                                owner_id=data.get("owner_id") or "")
            if errors:
                self._json({"ok": False, "errors": errors}, 400)
            else:
                self._json({"ok": True, "device": device})

        elif self.path == "/crm-start-payment":
            self._crm_start_payment(str(self._read_json().get("device_id") or ""))

        elif self.path == "/crm-inquiry":
            data = self._read_json()
            service = str(data.get("service") or "gomrok")
            ids = data.get("device_ids") or []
            if not isinstance(ids, list) or not ids:
                self._json({"ok": False, "errors": ["دستگاهی انتخاب نشده است"]}, 400)
                return
            results = []
            queued = 0
            for entry_id in ids:
                result = crm_start_inquiry(str(entry_id), service=service,
                                           origin="manual")
                results.append({"device_id": str(entry_id), "ok": result.get("ok"),
                                "errors": result.get("errors")})
                if result.get("ok"):
                    queued += 1
                    if not estelam_is_running():
                        continue
                    break   # a run is live; the rest go through the scheduler
            self._json({"ok": queued > 0, "queued": queued, "results": results})

        elif self.path == "/crm-settings":
            data = self._read_json()
            settings, errors = crm_update_settings(data)
            if errors:
                self._json({"ok": False, "errors": errors}, 400)
            else:
                self._json({"ok": True, "settings": settings})

        elif self.path == "/crm-report-telegram":
            self._crm_report_telegram(bool(self._read_json().get("full_data")))

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


def start_gate_decision(probe_ok, probe_version):
    """Pure decision for the single-instance gate.

    (probe_ok, probe_version) is what the port probe observed. Returns
    ("already-running", version) when another panel of this project answers
    on the port, ("proceed", "") otherwise. Kept pure so tests cover the
    logic without sockets."""
    if probe_ok:
        return ("already-running", str(probe_version or ""))
    return ("proceed", "")


def _panel_already_running(timeout=1.5):
    """True when a panel of THIS project already answers on HOST:PORT.

    New panels embed 'irica-version'; panels from releases older than 1.0.11
    are still recognized via their Persian site-picker markers, so upgrading
    over a running old install still refuses instead of crashing on bind.
    An unrelated service on the port (different markup) does not block us.
    """
    probe_url = f"http://{HOST}:{PORT}/"
    try:
        import urllib.request
        with urllib.request.urlopen(probe_url, timeout=timeout) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
    except Exception:
        return False
    if "irica-version" in body or app_version_text() in body:
        return True
    # Pre-1.0.11 marker: the site picker's title + a site-1 link together
    # are specific enough to be this project and nothing else.
    return "سامانه مدیریت گمرک" in body and "/site-1" in body


def main():
    if _panel_already_running():
        url = f"http://{HOST}:{PORT}"
        print(f">>> Panel-e digari hamin port ast: {url}")
        print(">>> Baraye start-e nuskhe-ye jadid, aval panel-e fe'li ra beband.")
        try:
            import webbrowser
            webbrowser.open(url + "/site-1")
        except Exception:
            pass
        return
    url = f"http://{HOST}:{PORT}"
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    load_store()  # motmaen sho profiles.json +inputs.json sakhte/sync shode-and
    clear_stale_busy_profiles()
    clean_project_root()
    crm_scheduler_start()
    threading.Thread(target=_startup_telegram_bootstrap, daemon=True,
                     name="telegram-api-health").start()
    print(f">>> Panel bala amad: {url}")
    print(f">>> Tanzimat va dade-ha: {DATA_DIR}")
    if _MIGRATED_FILES:
        source = app_paths.imported_from()
        print(f">>> Az nasKHe-ye ghabli montaghel shod ({source}): "
              f"{', '.join(_MIGRATED_FILES)}")
    print(f">>> Profile-ha: {PROFILES_PATH}")
    print(f">>> Profile-e active -> {INPUTS_PATH} (gomrok.py az inja mikhanad)")
    print(f">>> Log-ha: {LOGS_PATH}")
    print(">>> Baraye khamoosh kardan: Ctrl+C")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        # serve_forever runs on a helper thread; the main thread polls the
        # /shutdown flag so "بستن سامانه" can stop the panel cleanly
        # (robots already killed by shutdown_everything()).
        serve_thread = threading.Thread(target=srv.serve_forever,
                                        name="panel-serve", daemon=True)
        serve_thread.start()
        while not _SHUTDOWN_REQUESTED.is_set():
            _SHUTDOWN_REQUESTED.wait(timeout=0.5)
        print(">>> Panel ba dastoor-e karbar (بستن سامانه) khamoosh shod.")
        srv.shutdown()
        serve_thread.join(timeout=5)
    except KeyboardInterrupt:
        print("\n>>> Panel baste shod.")
        srv.shutdown()


if __name__ == "__main__":
    main()



