"""Live check: the pre-payment invoice page short-circuits the run.

Serves synthetic pages in a real headless Chrome and asserts that
gomrok.detect_prepayment_page() + handle_already_registered() behave correctly
against real DOM (not a fake driver):

  invoice page (pay button only)      -> detected, link extracted from onclick
  imeiStep still present              -> NOT detected
  hamtaStep present                   -> NOT detected
  page with no pay button             -> NOT detected

and for the invoice page it then verifies the full side effects: screenshot file,
payment-link txt, logs.jsonl error row with the Persian message, a bounded
Telegram attempt, and the panel profile being blocked + disabled.

Run:  python lab/scripts/live_check_already_registered.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gomrok  # noqa: E402
import panel  # noqa: E402

CHROMEDRIVER = r"C:\chromedriver.exe"
PORT = 8766
PAY_LINK = "https://epl.irica.gov.ir/pay?id=live123"

PAY_BTN = (f'<a wicketpath="{gomrok.WP_PARDAKHT}" href="#" '
           f"onclick=\"window.location.href = '{PAY_LINK}';\">pardakht</a>")
IMEI_BTN = f'<button wicketpath="{gomrok.WP_CONTINUE}">edame</button>'
HAMTA_BTN = f'<button wicketpath="{gomrok.WP_HAMTA_CONTINUE}">edame hamta</button>'

PAGES = {
    "/invoice": "<h1>faktor-e pish pardakht</h1>" + PAY_BTN,
    "/invoice-plus-imei": PAY_BTN + IMEI_BTN,
    "/invoice-plus-hamta": PAY_BTN + HAMTA_BTN,
    "/no-pay": "<p>hich dokme-ye pardakhti nist</p>" + IMEI_BTN,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = ("<!doctype html><html><head><meta charset=utf-8></head><body>"
                   + body + "</body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-first-run")
    driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=opts)

    tmp = tempfile.mkdtemp(prefix="already-reg-live-")
    failures = []

    def check(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"   {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    saved = {
        "shots": gomrok._SCREENSHOTS_DIR,
        "logs": gomrok._LOGS_PATH,
        "pid": gomrok._CLAIMED_PROFILE_ID,
        "rid": gomrok._CLAIMED_RUNNER_ID,
        "profiles": panel.PROFILES_PATH,
        "inputs": panel.INPUTS_PATH,
        "lock": panel._STORE_LOCK_PATH,
    }
    delivered = []
    try:
        # ---- detection against real DOM ---------------------------------------
        driver.get(f"http://127.0.0.1:{PORT}/invoice")
        check("invoice page is detected", gomrok.detect_prepayment_page(driver) is True)
        check("payment link read from real onclick",
              gomrok._extract_payment_link(driver) == PAY_LINK,
              gomrok._extract_payment_link(driver))

        driver.get(f"http://127.0.0.1:{PORT}/invoice-plus-imei")
        check("not detected while imeiStep button is still present",
              gomrok.detect_prepayment_page(driver) is False)

        driver.get(f"http://127.0.0.1:{PORT}/invoice-plus-hamta")
        check("not detected when hamtaStep is present",
              gomrok.detect_prepayment_page(driver) is False)

        driver.get(f"http://127.0.0.1:{PORT}/no-pay")
        check("not detected without a pay button",
              gomrok.detect_prepayment_page(driver) is False)

        # ---- full handling on a real invoice page -----------------------------
        gomrok._SCREENSHOTS_DIR = tmp
        gomrok._LOGS_PATH = os.path.join(tmp, "logs.jsonl")
        gomrok._CLAIMED_PROFILE_ID = "p1"
        gomrok._CLAIMED_RUNNER_ID = "r1"
        panel.PROFILES_PATH = os.path.join(tmp, "profiles.json")
        panel.INPUTS_PATH = os.path.join(tmp, "inputs.json")
        panel._STORE_LOCK_PATH = panel.PROFILES_PATH + ".lock"
        panel._LAST_GOOD_STORE.clear()
        panel.save_store({"active_id": "p1", "profiles": [{
            "id": "p1", "title": "LiveAli", "national": "0012345678",
            "imeis": ["352506099000001"], "run_status": "busy",
            "runner_id": "r1", "runner_pid": os.getpid(),
        }]})

        original_deliver = gomrok._telegram_deliver

        def spy(kind, **kwargs):
            delivered.append((kind, kwargs))
            return False        # simulate telegram being down: must not block
        gomrok._telegram_deliver = spy

        driver.get(f"http://127.0.0.1:{PORT}/invoice")
        link = gomrok.handle_already_registered(
            driver, title="LiveAli", national="0012345678", imei="352506099000001")
        gomrok._telegram_deliver = original_deliver

        check("handler returns the payment link", link == PAY_LINK, str(link))

        shots = [n for n in os.listdir(tmp) if n.endswith("_already_paid.png")]
        check("invoice screenshot written by real CDP capture", len(shots) == 1, str(shots))
        if shots:
            size = os.path.getsize(os.path.join(tmp, shots[0]))
            check("screenshot is a non-trivial PNG (>1KB)", size > 1024, f"{size}B")

        links = [n for n in os.listdir(tmp) if n.startswith("payment-link-")]
        check("payment link persisted to a txt file", len(links) == 1, str(links))

        rows = []
        if os.path.exists(gomrok._LOGS_PATH):
            with open(gomrok._LOGS_PATH, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
        succ = [r for r in rows if r["status"] == "success"]
        check("a SUCCESS row was logged (not an error)", len(succ) == 1, str(rows))
        check("no error row was logged",
              not [r for r in rows if r["status"] == "error"], str(rows))
        if succ:
            msg = succ[0]["message"]
            check("log carries the Persian 'already got a payment link' message",
                  "قبلاً این پروفایل لینک پرداخت گرفته است" in msg, msg[:120])
            check("log carries the payment link", PAY_LINK in msg, msg[:160])
            check("log carries the IMEI", succ[0]["imei"] == "352506099000001",
                  str(succ[0]))

        check("exactly one telegram attempt was made", len(delivered) == 1, str(delivered))
        if delivered:
            kind, kwargs = delivered[0]
            caption = kwargs.get("caption") or kwargs.get("text") or ""
            check("telegram payload carries the message and link",
                  "قبلاً این پروفایل لینک پرداخت گرفته است" in caption and PAY_LINK in caption,
                  caption[:120])
            check("telegram send is the photo variant", kind == "photo", kind)

        profile = panel.load_store()["profiles"][0]
        check("panel profile is SUCCESS", profile["registration_status"] == "success",
              profile["registration_status"])
        check("panel profile is NOT blocked and stays enabled",
              profile["registration_status"] != "blocked" and profile["enabled"] is True,
              f"{profile['registration_status']}/{profile['enabled']}")
        check("panel stores the payment link on the profile",
              profile.get("payment_link") == PAY_LINK, str(profile.get("payment_link")))
        check("panel stores the invoice screenshot on the profile",
              str(profile.get("receipt_screenshot") or "").endswith("_already_paid.png"),
              str(profile.get("receipt_screenshot")))
        check("success_at stamped", bool(profile.get("success_at")),
              str(profile.get("success_at")))
        status, _ = panel.start_profile("p1")
        check("a completed profile can no longer be started (409)", status == 409, str(status))
        check("a completed profile is not claimable", panel.claim_profile() is None)
    finally:
        gomrok._SCREENSHOTS_DIR = saved["shots"]
        gomrok._LOGS_PATH = saved["logs"]
        gomrok._CLAIMED_PROFILE_ID = saved["pid"]
        gomrok._CLAIMED_RUNNER_ID = saved["rid"]
        panel.PROFILES_PATH = saved["profiles"]
        panel.INPUTS_PATH = saved["inputs"]
        panel._STORE_LOCK_PATH = saved["lock"]
        panel._LAST_GOOD_STORE.clear()
        driver.quit()
        server.shutdown()

    print("----")
    print("RESULT:", "all already-registered checks passed" if not failures
          else f"FAILURES: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
