"""Preview panel for the redesigned profile list (NEVER touches real data).

    python lab/scripts/run_preview_panel.py [port]      (default 8759)

Same isolation as run_test_panel.py (temp store dir), but seeds ~9 profiles
covering every visual state (ready / busy / success / blocked / disabled)
so the new profile-card design can be reviewed in a browser.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import panel  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8759
TMP = os.path.join(tempfile.gettempdir(), f"irica-preview-panel-{PORT}")
os.makedirs(TMP, exist_ok=True)

panel.PROFILES_PATH = os.path.join(TMP, "profiles.json")
panel.INPUTS_PATH = os.path.join(TMP, "inputs.json")
panel._STORE_LOCK_PATH = panel.PROFILES_PATH + ".lock"
panel.LOGS_PATH = os.path.join(TMP, "logs.jsonl")
panel.TELEGRAM_PATH = os.path.join(TMP, "telegram_config.json")
panel.TELEGRAM_HEALTH_PATH = os.path.join(TMP, "telegram_health.json")
panel.EXITBAN_ERRORS_PATH = os.path.join(TMP, "exitban_errors.jsonl")
panel.SYSTEM_DEBUG_CONFIG_PATH = os.path.join(TMP, "system_debug.json")
panel.SYSTEM_LOG_DIR = os.path.join(TMP, "logs", "system")
panel.ESTELAM_STORE_PATH = getattr(panel, "ESTELAM_STORE_PATH",
                                   os.path.join(TMP, "estelam_store.json"))
panel._LAST_GOOD_STORE.clear()

now = panel.time.strftime("%Y-%m-%d %H:%M:%S")
PROFILES = [
    {"id": "busy1", "title": "رضا محمدی", "national": "0012345678",
     "birth": "1375/04/29", "mobile": "09121234567",
     "imeis": ["352506099000001"],
     "run_status": "busy", "runner_id": "preview-runner",
     "runner_pid": os.getpid(), "heartbeat_at": now, "busy_since": now,
     "browser_visible": False},
    {"id": "ok1", "title": "فاطمه احمدی", "national": "0087654321",
     "birth": "1370/01/01", "mobile": "09129876543",
     "imeis": ["352506099000002"],
     "registration_status": "success",
     "receipt_screenshot": True, "payment_screenshot": True,
     "payment_link": "https://example.com/pay/preview"},
    {"id": "blocked1", "title": "حسن کریمی", "national": "0044556677",
     "birth": "1368/07/12", "mobile": "09351112233",
     "imeis": ["352506099000003"],
     "registration_status": "blocked",
     "runtime_error": "IMEI قبلاً توسط شخص دیگری ثبت شده است"},
    {"id": "off1", "title": "Sara Moradi", "national": "0022113344",
     "birth": "1372/03/08", "mobile": "09194445566",
     "imeis": ["352506099000004"], "enabled": False},
    {"id": "ready1", "title": "علی رضایی", "national": "0099887766",
     "birth": "1365/11/21", "mobile": "09127778899",
     "imeis": ["352506099000005"]},
    {"id": "ready2", "title": "مریم حسینی", "national": "0055667788",
     "birth": "1379/09/05", "mobile": "09363334455",
     "imeis": ["352506099000006", "352506099000007"]},
    {"id": "ready3", "title": "Nima Ahmadi", "national": "0011223344",
     "birth": "1366/02/17", "mobile": "09105556677",
     "imeis": ["352506099000008"]},
    {"id": "ready4", "title": "زهرا نوری", "national": "0077889900",
     "birth": "1374/06/30", "mobile": "09211112233",
     "imeis": ["352506099000009"]},
    {"id": "ready5", "title": "محمد صادقی", "national": "0033445566",
     "birth": "1361/12/02", "mobile": "09398887766",
     "imeis": ["352506099000010"]},
]
with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
    json.dump({"active_id": "ready1", "profiles": PROFILES}, f, ensure_ascii=False)
with open(panel.INPUTS_PATH, "w", encoding="utf-8") as f:
    json.dump({"active_id": "ready1"}, f, ensure_ascii=False)


def _heartbeat():
    while True:
        try:
            panel.heartbeat_profile("busy1", "preview-runner")
        except Exception:
            pass
        panel.time.sleep(20)


threading.Thread(target=_heartbeat, name="preview-heartbeat", daemon=True).start()

print(f"URL  http://127.0.0.1:{PORT}/site-1", flush=True)
print(f"TMP  {TMP}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), panel.Handler).serve_forever()
