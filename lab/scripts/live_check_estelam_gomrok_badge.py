"""live_check_estelam_gomrok_badge.py
Check-e ZENDE: badge-e sotoon-e vaziat baraye GOMROK bayad KUTAH bashad va
ham-vajegan-e HAMTA:
  - healthy -> «مجاز (قابل ثبت)»   (na «سالم (شناسه در گمرک ورودی یافت نشد)»)
  - problem/advanced -> «مسدود/خطر»
  - motn-e KAMEL-e gomrok gom nashode: dar tooltip (title) + sotoon-e «نتیجه»
    (+ modal-e «مشاهده کامل» agar tolani bud)
  - badge-e gomrok TAK-KHATI bashad (ertefa'-ash ba hamta yeki)
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
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


def main():
    d = build()
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.3)

        # 1) BADGE_FA az server tazrigh shode va vajegan-e dorost darad
        bf = d.execute_script("return BADGE_FA;")
        chk("BADGE_FA tazrigh shode", isinstance(bf, dict) and bool(bf), bf)
        chk("healthy -> مجاز (قابل ثبت)", bf.get("healthy") == "مجاز (قابل ثبت)",
            bf.get("healthy"))
        chk("problem -> مسدود/خطر", bf.get("problem") == "مسدود/خطر", bf.get("problem"))
        chk("advanced -> مسدود/خطر", bf.get("advanced") == "مسدود/خطر", bf.get("advanced"))
        chk("pending -> در انتظار", bf.get("pending") == "در انتظار", bf.get("pending"))
        # vajegan-e gomrok ba hamta yek-dast bashad
        hf = d.execute_script("return HAMTA_FA;")
        chk("vajegan-e mojaz yek-dast ba hamta", bf.get("healthy") == hf.get("good"),
            f"{bf.get('healthy')} vs {hf.get('good')}")
        chk("vajegan-e masdood yek-dast ba hamta", bf.get("problem") == hf.get("blocked"),
            f"{bf.get('problem')} vs {hf.get('blocked')}")
        # FA_STATUS-e kamel hanooz baraye tooltip mojood ast
        fs = d.execute_script("return FA_STATUS;")
        chk("FA_STATUS-e kamel hanooz hast (motn-e tozihi)",
            "یافت نشد" in str(fs.get("healthy", "")), fs.get("healthy"))

        # 2) badge-haye render-shode-ye gomrok kutah-and
        rows = d.execute_script("""
            return Array.prototype.map.call(document.querySelectorAll('#dev-list tbody tr'),
              function(tr){
                // sotoon-e vaziat = .stgrid ba 6 khane: [slab,badge,when] x2
                var g=tr.querySelector('.stgrid');
                var slabs=g.querySelectorAll('.slab');
                var badges=g.querySelectorAll('.badge');
                function info(i){
                  var b=badges[i]; var r=b.getBoundingClientRect();
                  return {lab:(slabs[i].textContent||'').trim(),
                          txt:(b.textContent||'').trim(),
                          title:b.getAttribute('title')||'',
                          h:Math.round(r.height), w:Math.round(r.width),
                          cls:b.className};
                }
                return {hamta:info(0), gomrok:info(1),
                        res:(tr.querySelector('td.res').innerText||'').trim()};
              });
        """)
        chk("radif-ha render shodand", len(rows) >= 1, len(rows))

        long_words = ["یافت نشد", "دارای اشکال", "رفتن به مرحله بعد"]
        for i, r in enumerate(rows):
            g, h = r["gomrok"], r["hamta"]
            chk(f"radif{i}: label-e gomrok", g["lab"] == "گمرک", g["lab"])
            chk(f"radif{i}: badge-e gomrok kutah (bedun-e motn-e tozihi)",
                not any(w in g["txt"] for w in long_words), g["txt"])
            chk(f"radif{i}: badge-e gomrok jomle-ye BADGE_FA ast",
                g["txt"] in bf.values(), g["txt"])
            chk(f"radif{i}: badge-e gomrok TAK-KHATI (ertefa' ~ hamta)",
                abs(g["h"] - h["h"]) <= 4, f"gomrok={g['h']} hamta={h['h']}")
            chk(f"radif{i}: tooltip-e motn-e kamel roo-ye badge-e gomrok",
                len(g["title"]) >= len(g["txt"]), f"{g['title'][:40]!r}")
            # agar vaziat healthy/problem bud, kalame-ye dorost bashad
            if "b-healthy" in g["cls"]:
                chk(f"radif{i}: healthy -> مجاز", g["txt"] == "مجاز (قابل ثبت)", g["txt"])
                chk(f"radif{i}: tooltip-e healthy motn-e kamel darad",
                    "یافت نشد" in g["title"], g["title"])
            if "b-problem" in g["cls"]:
                chk(f"radif{i}: problem -> مسدود/خطر", g["txt"] == "مسدود/خطر", g["txt"])

        # 3) tozihat gom nashode: sotoon-e natije-ye gomrok motn darad
        d.execute_script("pickSvc('gomrok');")
        time.sleep(0.6)
        cells = d.execute_script("""
            return Array.prototype.map.call(document.querySelectorAll('#dev-list td.res'),
              function(td){return {txt:(td.innerText||'').trim(),
                                   more:!!td.querySelector('.res-more')};});
        """)
        with_text = [c for c in cells if c["txt"] and c["txt"] != "—"]
        chk("sotoon-e natije-ye gomrok tozihat darad", bool(with_text),
            [c["txt"][:40] for c in cells][:2])

        # dar halat-e all har do natije
        d.execute_script("pickSvc('all');")
        time.sleep(0.6)
        allres = d.execute_script(
            "var t=document.querySelector('#dev-list td.res');"
            "return t?(t.innerText||'').trim():'';")
        chk("halat-e all har do natije ra darad",
            "گمرک:" in allres and "همتا:" in allres, allres[:70])

        # 4) chips-e modiriat ham kutah shode
        d.execute_script("pickSvc('gomrok');")
        time.sleep(0.6)
        chips = d.execute_script(
            "return Array.prototype.map.call(document.querySelectorAll('#run-chips .chip'),"
            "function(c){return (c.textContent||'').trim();});")
        chk("chips-e gomrok bedun-e motn-e tozihi",
            not any(w in " ".join(chips) for w in long_words), chips)

        # 5) jadval/safhe sarriz nadarad
        of = d.execute_script("""
            var t=document.querySelector('#dev-list table');
            return {sw:t.scrollWidth,cw:t.clientWidth,
                    dsw:document.documentElement.scrollWidth,
                    dcw:document.documentElement.clientWidth};
        """)
        chk("jadval bedun-e sarriz", of["sw"] <= of["cw"] + 1, of)
        chk("safhe bedun-e scroll-e ofoghi", of["dsw"] <= of["dcw"] + 1, of)

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
