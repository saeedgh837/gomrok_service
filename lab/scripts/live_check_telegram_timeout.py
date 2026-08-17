"""Live end-to-end check: a Telegram outage must NOT stall the robot.

Simulates the step-4 (pre-payment) situation by pointing telegram_notify at a
local server that BLACKHOLES requests (accepts the connection, never answers).
Asserts that gomrok._telegram_deliver():

  * returns within roughly the configured budget (not minutes, not forever),
  * writes a WARN row into logs.jsonl saying it timed out and that the file was
    kept locally,
  * lets the caller continue.

Also checks the happy path against a stub that answers 200, and a permanent
"chat not found" which must fail fast without burning the whole budget.

Run:  python lab/scripts/live_check_telegram_timeout.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gomrok  # noqa: E402
import telegram_notify as tg  # noqa: E402

BLACKHOLE_PORT = 8764
STUB_PORT = 8765


def start_blackhole(port):
    """Accepts TCP connections and never replies (worst-case Telegram outage)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(16)
    held = []
    stop = threading.Event()

    def loop():
        srv.settimeout(0.4)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                held.append(conn)          # keep it open, send nothing
            except socket.timeout:
                continue
            except OSError:
                break

    threading.Thread(target=loop, daemon=True).start()
    return stop, srv, held


class Stub(BaseHTTPRequestHandler):
    MODE = "ok"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        if Stub.MODE == "ok":
            body = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
            code = 200
        else:
            body = json.dumps({"ok": False, "description": "chat not found"}).encode()
            code = 400
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def main():
    stop, srv, _held = start_blackhole(BLACKHOLE_PORT)
    stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()

    tmp = tempfile.mkdtemp(prefix="tg-timeout-")
    logs = os.path.join(tmp, "logs.jsonl")
    shot = os.path.join(tmp, "receipt.png")
    with open(shot, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")     # content is irrelevant, only the path is

    failures = []

    def check(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"   {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    def read_logs():
        if not os.path.exists(logs):
            return []
        rows = []
        with open(logs, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    original_logs_path = gomrok._LOGS_PATH
    original_cfg = tg._CFG_PATH
    cfg_path = os.path.join(tmp, "telegram_config.json")
    try:
        gomrok._LOGS_PATH = logs
        tg._CFG_PATH = cfg_path

        # ---- 1) total outage: connection accepted, no response ever ----------
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"api_base": f"http://127.0.0.1:{BLACKHOLE_PORT}",
                       "bot_token": "TESTTOKEN", "chat_ids": ["111"]}, f)

        gomrok.TELEGRAM_PHOTO_TIMEOUT_SEC = 6.0
        gomrok.TELEGRAM_TEXT_TIMEOUT_SEC = 4.0

        started = time.monotonic()
        ok = gomrok._telegram_deliver("photo", photo_path=shot, caption="receipt",
                                      step="telegram: rasid (aks)", title="LiveTest",
                                      national="0012345678", imei="352506099000001")
        elapsed = time.monotonic() - started
        check("photo send returns False during a total outage", ok is False)
        check("photo send is bounded (<25s for a 6s budget)", elapsed < 25.0,
              f"{elapsed:.1f}s")
        rows = read_logs()
        warn = [r for r in rows if r["status"] == "warn"]
        check("a WARN row was written to logs.jsonl", len(warn) == 1, str(rows))
        if warn:
            msg = warn[0]["message"]
            check("log says TIMEOUT", "TIMEOUT" in msg or "timeout" in msg.lower(), msg)
            check("log says the file was kept locally",
                  "mahali zakhire shod" in msg, msg)
            check("log names the local screenshot path", "receipt.png" in msg, msg)
            check("log carries the profile/IMEI for traceability",
                  warn[0]["imei"] == "352506099000001" and warn[0]["title"] == "LiveTest",
                  str(warn[0]))

        # ---- 2) text send during outage: shorter budget, still returns -------
        started = time.monotonic()
        ok = gomrok._telegram_deliver("text", text="pre-payment info",
                                      step="telegram: pish-pardakht (matn)",
                                      title="LiveTest")
        elapsed = time.monotonic() - started
        check("text send returns False during a total outage", ok is False)
        check("text send is bounded (<20s for a 4s budget)", elapsed < 20.0,
              f"{elapsed:.1f}s")

        # ---- 3) happy path against a stub that answers 200 -------------------
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"api_base": f"http://127.0.0.1:{STUB_PORT}",
                       "bot_token": "TESTTOKEN", "chat_ids": ["111"]}, f)
        Stub.MODE = "ok"
        before = len(read_logs())
        started = time.monotonic()
        ok = gomrok._telegram_deliver("photo", photo_path=shot, caption="receipt",
                                      step="telegram: rasid (aks)", title="LiveTest")
        elapsed = time.monotonic() - started
        check("photo send succeeds against a healthy endpoint", ok is True)
        check("success is fast (<5s)", elapsed < 5.0, f"{elapsed:.2f}s")
        check("success writes NO warn row", len(read_logs()) == before)

        # ---- 4) permanent error (chat not found) must fail fast --------------
        Stub.MODE = "chat"
        started = time.monotonic()
        ok = gomrok._telegram_deliver("text", text="x", step="telegram: test (matn)",
                                      title="LiveTest")
        elapsed = time.monotonic() - started
        check("permanent chat error returns False", ok is False)
        check("permanent chat error fails fast (<4s, no retry storm)", elapsed < 4.0,
              f"{elapsed:.2f}s")
    finally:
        gomrok._LOGS_PATH = original_logs_path
        tg._CFG_PATH = original_cfg
        stop.set()
        try:
            srv.close()
        except OSError:
            pass
        stub.shutdown()

    print("----")
    print("RESULT:", "all telegram-timeout checks passed" if not failures
          else f"FAILURES: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
