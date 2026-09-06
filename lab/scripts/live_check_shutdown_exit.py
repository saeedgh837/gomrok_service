"""E2E: real panel.main() subprocess must exit after POST /shutdown."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 18761
DATA = tempfile.mkdtemp(prefix="irica-shutdown-e2e-")

env = os.environ.copy()
env["IRICA_DATA_DIR"] = DATA
env["IRICA_PANEL_ALREADY_RUNNING_BYPASS"] = "1"

code = (
    "import sys, os, threading, time\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    # Defeat the single-instance HTTP probe: the operator's real panel runs.
    "import panel\n"
    "panel._panel_already_running = lambda timeout=2.0: False\n"
    f"panel.PORT = {PORT}\n"
    # main() prints via print(); keep stdout alive so pipes do not fill.
    "panel.main()\n"
)
proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")

# Wait for the panel to come up; if it dies, show why.
up = False
for _ in range(40):
    time.sleep(0.5)
    if proc.poll() is not None:
        print("panel exited early with code", proc.returncode)
        print("---- panel output ----")
        print(proc.stdout.read()[-2000:])
        sys.exit(1)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
        up = True
        break
    except Exception:
        continue
assert up, "panel did not come up"

req = urllib.request.Request(f"http://127.0.0.1:{PORT}/shutdown",
                             data=b"{}", headers={"Content-Type": "application/json"},
                             method="POST")
result = json.loads(urllib.request.urlopen(req, timeout=5).read())
print("shutdown response:", result)

try:
    rc = proc.wait(timeout=15)
    print("panel process exited with code:", rc)
except subprocess.TimeoutExpired:
    proc.kill()
    print("FAIL: panel still running after /shutdown")
    sys.exit(1)
