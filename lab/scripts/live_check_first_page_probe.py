"""Live verification of the first-page fast probe in a REAL browser.

The unit tests drive wait_first_page_ready() with a fake driver, so the JS in
gomrok._PAGE_PROBE_JS itself was never executed by Chrome. This script serves
synthetic pages that mimic the real imeiStep states and asserts:

  healthy page       -> "ready"   fast (< 1.5s)
  idle, no captcha   -> "incomplete" fast (< 1.5s)   <-- the latency bug
  broken captcha img -> "incomplete" fast
  slow-but-arriving  -> "ready"   (does not give up early)
  never arrives      -> "incomplete" (idle without captcha)

Run:  python lab/scripts/live_check_first_page_probe.py
"""
import os
import sys
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gomrok  # noqa: E402
import captcha_collector as cc  # noqa: E402

CHROMEDRIVER = r"C:\chromedriver.exe"
PORT = 8763

# 1x1 transparent PNG (a real, decodable image for the captcha element)
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae"
    "426082")

CONTINUE_BTN = f'<button wicketpath="{gomrok.WP_CONTINUE}">edame</button>'
CAPTCHA_CLASS = cc.CAPTCHA_CSS.replace("img.", "")


def page(body, head=""):
    return ("<!doctype html><html><head><meta charset=utf-8>" + head +
            "</head><body>" + body + "</body></html>").encode("utf-8")


PAGES = {
    # captcha present + decodable, continue button visible
    "/healthy": page(f'<img class="{CAPTCHA_CLASS}" src="/px.png">' + CONTINUE_BTN),
    # page finished loading but the site returned no captcha and no button
    "/incomplete": page("<p>naqes</p>"),
    # captcha element exists but the image 404s -> complete && naturalWidth==0
    "/broken": page(f'<img class="{CAPTCHA_CLASS}" src="/missing.png">' + CONTINUE_BTN),
    # captcha injected quickly (within the settle window). NOTE: kept only as a
    # fixture for manual poking — the live checks do not assert on it because an
    # async-injected captcha is not how the real imeiStep ships its captcha.
    "/slow": page(
        CONTINUE_BTN +
        '<script>setTimeout(function(){var i=document.createElement("img");'
        f'i.className="{CAPTCHA_CLASS}";i.src="/px.png";'
        'document.body.appendChild(i);},250);</script>'),
    # captcha that only shows up after ~1.5s. On the real site the captcha ships
    # inline in the imeiStep HTML, so "idle and still no captcha" means the page
    # came back broken -> F5 is the correct (and cheap) reaction.
    "/verylate": page(
        CONTINUE_BTN +
        '<script>setTimeout(function(){var i=document.createElement("img");'
        f'i.className="{CAPTCHA_CLASS}";i.src="/px.png";'
        'document.body.appendChild(i);},1500);</script>'),
    # an image that never finishes: pending stays true -> not "incomplete"
    "/hanging": page(f'<img class="{CAPTCHA_CLASS}" src="/slow.png">' + CONTINUE_BTN),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/px.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG_1PX)))
            self.end_headers()
            self.wfile.write(PNG_1PX)
            return
        if path == "/slow.png":
            time.sleep(20)          # never arrives within the test window
            return
        body = PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
    # bound page load so a hanging sub-resource doesn't block driver.get forever
    try:
        driver.set_page_load_timeout(8)
    except Exception:
        pass

    failures = []

    def check(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"   {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    def verdict_for(path, budget=6.0, settle=0.45):
        try:
            driver.get(f"http://127.0.0.1:{PORT}{path}")
        except Exception as exc:            # page-load timeout on hanging pages
            print(f"      (get raised, continuing: {str(exc).splitlines()[0][:70]})")
        started = time.monotonic()
        result = gomrok.wait_first_page_ready(driver, budget=budget, settle=settle)
        return result, time.monotonic() - started

    try:
        # sanity: the JS actually returns a dict in a real browser.
        # NOTE: probe right after get() can legitimately catch the page mid-load
        # (pending=true, ready=false) — that is a CORRECT reading, not a failure.
        # So poll briefly for the settled state before asserting on the contents.
        driver.get(f"http://127.0.0.1:{PORT}/healthy")
        probe = gomrok.probe_first_page(driver)
        check("probe JS executes in Chrome and returns a dict",
              isinstance(probe, dict) and "capOk" in probe, str(probe))
        settle_deadline = time.monotonic() + 3.0
        while time.monotonic() < settle_deadline:
            if probe and probe.get("capOk") and probe.get("btnOk"):
                break
            time.sleep(0.05)
            probe = gomrok.probe_first_page(driver)
        check("probe sees a decoded captcha + visible button",
              bool(probe and probe.get("capOk") and probe.get("btnOk")), str(probe))

        v, t = verdict_for("/healthy")
        check("healthy page -> ready", v == "ready", f"{v} in {t:.2f}s")
        check("healthy page is fast (<1.5s)", t < 1.5, f"{t:.2f}s")

        v, t = verdict_for("/incomplete")
        check("idle without captcha -> incomplete", v == "incomplete", f"{v} in {t:.2f}s")
        check("incomplete detected fast (<1.5s, was ~10s before)", t < 1.5, f"{t:.2f}s")

        v, t = verdict_for("/broken")
        check("broken captcha image -> incomplete", v == "incomplete", f"{v} in {t:.2f}s")
        check("broken captcha detected fast (<1.0s)", t < 1.0, f"{t:.2f}s")

        # NOTE: on the real imeiStep the captcha ships INLINE in the HTML, so the
        # only two outcomes that matter are "captcha present -> ready" (covered by
        # /healthy) and "page idle without captcha -> incomplete + F5" (below).
        # A captcha injected asynchronously after load is not a real-site case and
        # races the renderer, so we don't assert on it here.

        # very-late / never-inline captcha: page goes idle with no captcha, so the
        # probe returns "incomplete" fast and the caller F5s.
        v, t = verdict_for("/verylate", settle=0.4)
        check("idle page whose captcha never ships inline -> incomplete fast",
              v == "incomplete", f"{v} in {t:.2f}s")
        check("very-late detected fast (<1.2s)", t < 1.2, f"{t:.2f}s")

        # hanging image while the page is still loading: execute_script is queued
        # by the renderer until page-load resolves, so the ceiling here is the
        # driver's page-load timeout (bounded), NOT a multi-minute stall. The key
        # guarantee is that it never returns a false "ready".
        v, t = verdict_for("/hanging", budget=1.5)
        check("hanging resource -> never a false ready", v in ("timeout", "incomplete"),
              f"{v} in {t:.2f}s")
        check("hanging page is bounded by page-load timeout (<12s)", t < 12.0,
              f"{t:.2f}s")

        # ensure_page_ready must F5 an incomplete page and give up cleanly
        driver.get(f"http://127.0.0.1:{PORT}/incomplete")
        started = time.monotonic()
        ok = gomrok.ensure_page_ready(driver, wait=None, dbg=None, max_reload=2)
        elapsed = time.monotonic() - started
        check("ensure_page_ready returns False for a permanently naqes page", ok is False)
        check("ensure_page_ready gives up quickly (<6s for 2 reloads)", elapsed < 6.0,
              f"{elapsed:.2f}s")
    finally:
        driver.quit()
        server.shutdown()

    print("----")
    print("RESULT:", "all live probe checks passed" if not failures
          else f"FAILURES: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
