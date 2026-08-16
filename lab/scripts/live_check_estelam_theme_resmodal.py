"""live_check_estelam_theme_resmodal.py
Check-e ZENDE-ye do vizhegi-ye jadid dar /site-2:
  1) natije-ye tolani: 80 character-e aval + dokme-ye «مشاهده کامل» -> modal-e
     motn-e KAMEL (bedun-e kutah-shodan), Escape/backdrop mibandad.
  2) theme-e roshan/tire: PISHFARZ ROSHAN, toggle, mandegari dar localStorage,
     va inke rang-ha vaghe'an avaz mishavand (bg-e roshan vs tire) + badge-ha
     dar har do halat khanandeh bashand (hardcode-e tire naمانده bashad).
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.common.by import By                       # noqa: E402
from selenium.webdriver.common.keys import Keys                   # noqa: E402
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


def load(d, clear=False):
    d.get(URL)
    WebDriverWait(d, 30).until(
        lambda x: x.execute_script("return document.readyState") == "complete")
    if clear:
        d.execute_script("localStorage.clear();")
        d.refresh()
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
    time.sleep(1.2)


def lum(css_rgb):
    nums = [int(x) for x in css_rgb.replace("rgba", "").replace("rgb", "")
            .strip("() ").split(",")[:3]]
    return sum(nums) / 3.0


def main():
    d = build()
    try:
        load(d, clear=True)

        # ---------- 1) THEME ----------
        theme = d.execute_script("return document.documentElement.getAttribute('data-theme');")
        chk("theme-e PISHFARZ = روشن", theme == "light", theme)
        bg_light = d.execute_script("return getComputedStyle(document.body).backgroundColor;")
        txt_light = d.execute_script("return getComputedStyle(document.body).color;")
        chk("bg-e roshan (roshan-tar az matn)", lum(bg_light) > lum(txt_light),
            f"bg={bg_light} txt={txt_light}")
        chk("bg-e roshan vaghe'an roshan ast (>200)", lum(bg_light) > 200, bg_light)
        btn_txt = d.find_element(By.ID, "theme-btn").text.strip()
        chk("dokme-ye theme = 'حالت تاریک'", "حالت تاریک" in btn_txt, btn_txt)

        # toggle -> dark
        d.find_element(By.ID, "theme-btn").click()
        time.sleep(0.5)
        theme2 = d.execute_script("return document.documentElement.getAttribute('data-theme');")
        bg_dark = d.execute_script("return getComputedStyle(document.body).backgroundColor;")
        txt_dark = d.execute_script("return getComputedStyle(document.body).color;")
        chk("toggle -> تاریک", theme2 == "dark", theme2)
        chk("bg-e tire (tire-tar az matn)", lum(bg_dark) < lum(txt_dark),
            f"bg={bg_dark} txt={txt_dark}")
        chk("bg-e tire vaghe'an tire ast (<60)", lum(bg_dark) < 60, bg_dark)
        chk("dokme -> 'حالت روشن'",
            "حالت روشن" in d.find_element(By.ID, "theme-btn").text, 
            d.find_element(By.ID, "theme-btn").text.strip())
        chk("do theme rang-e bg-e motefavet darand", bg_light != bg_dark,
            f"{bg_light} vs {bg_dark}")

        # mandegari ba'd az reload
        d.refresh()
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.0)
        chk("theme ba'd az reload hefz shod (dark)",
            d.execute_script("return document.documentElement.getAttribute('data-theme');") == "dark")

        # badge-ha dar halat-e ROSHAN khanandeh bashand (contrast-e bg/fg)
        d.execute_script("setTheme('light');")
        time.sleep(0.5)
        badges = d.execute_script("""
            return Array.prototype.map.call(
              document.querySelectorAll('#dev-list .badge'),
              function(b){var s=getComputedStyle(b);
                return {cls:b.className,bg:s.backgroundColor,fg:s.color};});
        """)
        chk("badge-ha dar safhe hastand", len(badges) >= 2, len(badges))
        bad = [b for b in badges if abs(lum(b["bg"]) - lum(b["fg"])) < 40]
        chk("badge-ha dar halat-e roshan contrast darand", not bad, bad[:2])
        light_bg_ok = all(lum(b["bg"]) > 150 for b in badges)
        chk("badge-ha dar halat-e roshan pas-zamine-ye roshan darand",
            light_bg_ok, [b["bg"] for b in badges][:3])

        # ---------- 2) natije-ye 80-character + modal ----------
        d.execute_script("pickSvc('hamta');")
        time.sleep(0.6)
        cells = d.execute_script("""
            return Array.prototype.map.call(document.querySelectorAll('#dev-list td.res'),
              function(td){var b=td.querySelector('.res-more');
                return {text:(td.innerText||'').trim(),
                        hasBtn:!!b, btnText:b?(b.textContent||'').trim():null};});
        """)
        chk("sotoon-e natije render shod", len(cells) >= 1, len(cells))
        long_cells = [c for c in cells if c["hasBtn"]]
        chk("radif-e natije-ye tolani dokme-ye «مشاهده کامل» darad",
            bool(long_cells), [c["btnText"] for c in cells])
        if long_cells:
            c = long_cells[0]
            chk("matn-e dokme = مشاهده کامل", c["btnText"] == "مشاهده کامل", c["btnText"])
            head = c["text"].split("\n")[0]
            chk("kholase ~80 character (+ …)", len(head) <= 84 and head.endswith("…"),
                f"len={len(head)} {head[-12:]!r}")

        # modal baz shavad va motn-e KAMEL dashte bashad.
        # NOKTE: radif-ha ba jadid-tarin-BALA morattab shode-and, pas be-jaye
        # farz-e device-e STATE, HAMUN button-i ke click mishavad ra be device-ash
        # negasht mikonim ta tool-e motn daghigh moghayese shavad.
        expect_len = d.execute_script("""
            var btn=document.querySelector('#dev-list .res-more');
            if(!btn) return {len:0};
            var tr=btn.closest('tr');
            var top=tr.querySelector('.mono.sn1').textContent.trim();
            var dev=(STATE.devices||[]).filter(function(x){return (x.serial1||'')===top;})[0];
            return {len: dev?((dev.hamta_result||dev.result||'').length):0};
        """)["len"]
        chk("device-e ba natije-ye >80 character (radif-e aval-e res-more)",
            expect_len > 80, expect_len)
        d.execute_script("document.querySelector('#dev-list .res-more').click();")
        time.sleep(0.6)
        m = d.execute_script("""
            var mo=document.getElementById('res-modal');
            var bd=document.getElementById('res-modal-body');
            return {shown:mo.classList.contains('show'),
                    disp:getComputedStyle(mo).display,
                    title:(document.getElementById('res-modal-title').textContent||'').trim(),
                    sn:(document.getElementById('res-modal-sn').textContent||'').trim(),
                    len:(bd.textContent||'').length,
                    ws:getComputedStyle(bd).whiteSpace};
        """)
        chk("modal baz shod", m["shown"] and m["disp"] == "flex", m)
        chk("modal titr-e همتا darad", "همتا" in m["title"], m["title"])
        chk("modal serial neshan midahad", len(m["sn"]) >= 15, m["sn"])
        chk("modal motn-e KAMEL darad (bedun-e kutah-shodan)",
            m["len"] >= expect_len and m["len"] > 80,
            f"modal={m['len']} expect={expect_len}")
        chk("modal khat-ha ra hefz mikonad (pre-line)", m["ws"] == "pre-line", m["ws"])

        # Escape mibandad
        d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        time.sleep(0.4)
        chk("Escape modal ra mibandad",
            not d.execute_script("return document.getElementById('res-modal').classList.contains('show');"))
        # backdrop mibandad
        d.execute_script("document.querySelector('#dev-list .res-more').click();")
        time.sleep(0.4)
        d.execute_script("""
            var mo=document.getElementById('res-modal');
            mo.dispatchEvent(new MouseEvent('click',{bubbles:true}));
        """)
        time.sleep(0.4)
        chk("click-e backdrop modal ra mibandad",
            not d.execute_script("return document.getElementById('res-modal').classList.contains('show');"))

        # modal dar halat-e TIRE ham darost bashad
        d.execute_script("setTheme('dark');document.querySelector('#dev-list .res-more').click();")
        time.sleep(0.5)
        box = d.execute_script("""
            var b=document.querySelector('#res-modal .box');var s=getComputedStyle(b);
            return {bg:s.backgroundColor,fg:s.color};
        """)
        chk("modal dar halat-e tire tire ast", lum(box["bg"]) < 80, box)
        d.execute_script("closeResModal();setTheme('light');")
        time.sleep(0.3)

        # jadval sarriz nadarad ba vojood-e natije-ye tolani
        of = d.execute_script("""
            var t=document.querySelector('#dev-list table');
            return {sw:t.scrollWidth,cw:t.clientWidth,
                    dsw:document.documentElement.scrollWidth,
                    dcw:document.documentElement.clientWidth};
        """)
        chk("jadval bedun-e sarriz-e ofoghi", of["sw"] <= of["cw"] + 1, of)
        chk("safhe bedun-e scroll-e ofoghi", of["dsw"] <= of["dcw"] + 1, of)

        # hich khata-ye JS
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
