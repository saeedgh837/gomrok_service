"""Start a panel instance on a TEST port with a TEMP store (never touches real data).

    python lab/scripts/run_test_panel.py [port]

Seeds two profiles (one idle, one pretending to run) so UI features that only
appear for a running profile — e.g. the browser show/hide button — are visible.
Keeps the fake runner's heartbeat fresh so clear_stale_busy_profiles() does not
release it mid-session.
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
TMP = os.path.join(tempfile.gettempdir(), f"irica-test-panel-{PORT}")
os.makedirs(TMP, exist_ok=True)

panel.PROFILES_PATH = os.path.join(TMP, "profiles.json")
panel.INPUTS_PATH = os.path.join(TMP, "inputs.json")
panel._STORE_LOCK_PATH = panel.PROFILES_PATH + ".lock"
panel.LOGS_PATH = os.path.join(TMP, "logs.jsonl")
panel.TELEGRAM_PATH = os.path.join(TMP, "telegram_config.json")
panel.TELEGRAM_HEALTH_PATH = os.path.join(TMP, "telegram_health.json")
panel.EXITBAN_ERRORS_PATH = os.path.join(TMP, "exitban_errors.jsonl")
panel.ESTELAM_STORE_PATH = getattr(panel, "ESTELAM_STORE_PATH",
                                   os.path.join(TMP, "estelam_store.json"))
panel._LAST_GOOD_STORE.clear()

now = panel.time.strftime("%Y-%m-%d %H:%M:%S")
with open(panel.PROFILES_PATH, "w", encoding="utf-8") as f:
    json.dump({"active_id": "idle1", "profiles": [
        {"id": "idle1", "title": "Idle Profile", "national": "0012345678",
         "birth": "1375/04/29", "mobile": "09121234567",
         "imeis": ["352506099000001"]},
        {"id": "busy1", "title": "Running Profile", "national": "0087654321",
         "birth": "1370/01/01", "mobile": "09129876543",
         "imeis": ["352506099000002"],
         "run_status": "busy", "runner_id": "test-runner",
         "runner_pid": os.getpid(), "heartbeat_at": now, "busy_since": now},
    ]}, f)


def _heartbeat():
    while True:
        try:
            panel.heartbeat_profile("busy1", "test-runner")
        except Exception:
            pass
        panel.time.sleep(20)


threading.Thread(target=_heartbeat, name="fake-heartbeat", daemon=True).start()

print(f"URL  http://127.0.0.1:{PORT}/site-1", flush=True)
print(f"URL  http://127.0.0.1:{PORT}/site-2", flush=True)
print(f"TMP  {TMP}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), panel.Handler).serve_forever()
