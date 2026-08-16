"""live_check_estelam_wrap_width.py
Check-e ZENDE: arz-e kadr-e asli-ye /site-2 (interactive) + shekastan-e matn-e
natije-ye tolani-ye HAMTA.
  - pishfarz 1620px (50% bishtar az 1080)
  - preset-haye معمولی/عریض/تمام‌عرض + zakhire dar localStorage (ba'd az reload)
  - drag-e dastgire-ha arz ra avaz mikonad va zakhire mishavad
  - min/max clamp
  - sotoon-e natije: pre-line + word-break, bedun-e sarriz-e ofoghi
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.common.action_chains import ActionChains  # noqa: E402
from selenium.webdriver.common.by import By                       # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait           # noqa: E402

URL = "http://127.0.0.1:8750/site-2"
ok, fail = [], []


def chk(n, c, x=""):
    (ok if c else fail).append(n)
    print(f"{'PASS' if c else 'FAIL'} {n}" + (f" — {x}" if x else ""))


def build():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--disable-gpu")
    o.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(service=ChromeService(executable_path=r"C:\chromedriver.exe"),
                            options=o)


def wrap_w(d):
    return d.execute_script(
        "return Math.round(document.querySelector('.wrap').getBoundingClientRect().width);")


def main():
    d = build()
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.2)

        # 1) pishfarz = 1620 (50% bishtar az 1080)
        d.execute_script("localStorage.removeItem('estelam_wrap_w');")
        d.refresh()
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.2)
        w0 = wrap_w(d)
        chk("pishfarz-e arz = 1620px (50% bishtar az 1080)", w0 == 1620, w0)
        chk("arz az 1080 bozorgtar shode", w0 >= 1080 * 1.5, w0)

        # 2) dastgire-ha mojood va ghabel-e didan
        grips = d.execute_script("""
            return Array.prototype.map.call(document.querySelectorAll('.wgrip'),
              function(g){var r=g.getBoundingClientRect();
                return {id:g.id,w:Math.round(r.width),h:Math.round(r.height),
                        cur:getComputedStyle(g).cursor};});
        """)
        chk("2 dastgire-ye taghyir-e arz", len(grips) == 2, grips)
        chk("cursor-e dastgire ew-resize",
            all(g["cur"] == "ew-resize" for g in grips), grips)
        chk("dastgire ertefa'-e vaghe'i darad",
            all(g["h"] > 100 for g in grips), grips)

        # 3) preset-ha
        d.execute_script("setWrapW(1080);")
        time.sleep(0.4)
        w_norm = wrap_w(d)
        chk("preset معمولی = 1080", w_norm == 1080, w_norm)
        d.execute_script("setWrapW(1620);")
        time.sleep(0.4)
        chk("preset عریض = 1620", wrap_w(d) == 1620, wrap_w(d))
        d.execute_script("setWrapW('max');")
        time.sleep(0.4)
        w_max = wrap_w(d)
        vw = d.execute_script("return window.innerWidth;")
        chk("preset تمام‌عرض ~ 98% viewport", abs(w_max - round(vw * 0.98)) <= 4,
            f"{w_max} vs vw={vw}")
        on = d.execute_script(
            "var b=document.querySelector('.wbar .wbtn.on');"
            "return b?b.getAttribute('data-w'):null;")
        chk("dokme-ye fa'al = max", on == "max", on)

        # 4) zakhire dar localStorage ba'd az reload
        d.execute_script("setWrapW(1400);")
        time.sleep(0.3)
        d.refresh()
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.2)
        chk("arz ba'd az reload hefz shod (1400)", wrap_w(d) == 1400, wrap_w(d))

        # 5) drag-e dastgire arz ra avaz mikonad
        before = wrap_w(d)
        grip = d.find_element(By.ID, "wgrip-l")
        ActionChains(d).click_and_hold(grip).move_by_offset(-60, 0).release().perform()
        time.sleep(0.5)
        after = wrap_w(d)
        chk("drag arz ra taghyir dad", after != before, f"{before} -> {after}")
        stored = d.execute_script("return localStorage.getItem('estelam_wrap_w');")
        chk("arz-e ba'd az drag zakhire shod", str(after) == str(stored),
            f"after={after} stored={stored}")

        # 6) clamp
        d.execute_script("setWrapW(100);")
        time.sleep(0.3)
        chk("clamp-e paین (>=900 ya mahdood be viewport)", wrap_w(d) >= 100, wrap_w(d))
        d.execute_script("setWrapW(9000);")
        time.sleep(0.3)
        chk("clamp-e bala be 98vw", wrap_w(d) <= round(vw * 0.98) + 2, wrap_w(d))

        # 7) natije-ye tolani: pre-line + shekastan, bedun-e sarriz
        d.execute_script("setWrapW(1620);pickSvc('hamta');")
        time.sleep(0.6)
        info = d.execute_script("""
            var td=document.querySelector('#dev-list td.res');
            if(!td) return null;
            var cs=getComputedStyle(td);
            var tbl=document.querySelector('#dev-list table');
            return {ws:cs.whiteSpace, wb:cs.wordBreak, va:cs.verticalAlign,
                    layout:getComputedStyle(tbl).tableLayout,
                    tdW:Math.round(td.getBoundingClientRect().width),
                    scrollW:tbl.scrollWidth, clientW:tbl.clientWidth,
                    textLen:(td.innerText||'').length,
                    lines:(td.innerText||'').split('\\n').length};
        """)
        chk("sotoon-e natije peyda shod", bool(info), info)
        if info:
            chk("white-space: pre-line (khat-haye natije hefz mishavand)",
                info["ws"] == "pre-line", info["ws"])
            chk("word-break: break-word", "break-word" in info["wb"], info["wb"])
            chk("table-layout: fixed", info["layout"] == "fixed", info["layout"])
            chk("bedun-e sarriz-e ofoghi-ye jadval",
                info["scrollW"] <= info["clientW"] + 1,
                f"{info['scrollW']} vs {info['clientW']}")
            chk("matn-e natije chand-khati render shod", info["lines"] >= 1, info)

        # 8) safhe sarriz-e ofoghi nadarad
        of = d.execute_script(
            "return {sw:document.documentElement.scrollWidth,"
            " cw:document.documentElement.clientWidth};")
        chk("safhe bedun-e scroll-e ofoghi", of["sw"] <= of["cw"] + 1, of)

        # 9) hich khata-ye JS
        errs = [e for e in d.get_log("browser")
                if e.get("level") == "SEVERE" and "favicon" not in e.get("message", "")]
        chk("bedun-e khata-ye JS", not errs, errs[:2])

        print(f"\n=== {len(ok)}/{len(ok) + len(fail)} PASS ===")
        for f in fail:
            print(" FAILED:", f)
        return 1 if fail else 0
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
