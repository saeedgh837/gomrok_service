"""live_check_estelam_service_picker.py
Check-e ZENDE-ye morurgar baraye entekhab-e no'-e estelam dar safhe-ye /site-2:
se dokme (همتا / گمرک / همه), taghyir-e class-e on, taghyir-e sotoon-e natije,
matn-e dokme-ye radif, va payload-e /estelam-start (service).

POST-e vaghe'i be /estelam-start NEMIZANAD — fetch ra intercept mikonad ta
payload ra bebinad (bedun-e ejra-ye robot).
"""
import json
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


def chk(name, cond, extra=""):
    (ok if cond else fail).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" — {extra}" if extra else ""))


def build():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--disable-gpu")
    o.add_argument("--window-size=1400,1000")
    return webdriver.Chrome(service=ChromeService(executable_path=r"C:\chromedriver.exe"),
                            options=o)


def main():
    d = build()
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.5)

        # intercept-e fetch: /estelam-start ra nemifrestad, faghat zabt mikonad
        d.execute_script("""
            window.__calls=[];
            var of=window.fetch;
            window.fetch=function(u,o){
              if(String(u).indexOf('/estelam-start')>=0){
                window.__calls.push({url:String(u),body:(o&&o.body)||''});
                return Promise.resolve(new Response(
                  JSON.stringify({ok:true,pid:1234,count:1,service:JSON.parse((o&&o.body)||'{}').service}),
                  {status:200,headers:{'Content-Type':'application/json'}}));
              }
              return of.apply(this,arguments);
            };
        """)

        # 1) se dokme mojood-and ba label-haye dorost
        btns = d.execute_script("""
            return Array.prototype.map.call(
              document.querySelectorAll('#svc-btns .svc'),
              function(b){return {svc:b.getAttribute('data-svc'),
                                  text:(b.textContent||'').trim(),
                                  on:b.classList.contains('on')};});
        """)
        chk("3 dokme-ye entekhab-e servis", len(btns) == 3, btns)
        by = {b["svc"]: b for b in btns}
        chk("dokme-ye همتا", by.get("hamta", {}).get("text") == "استعلام همتا",
            by.get("hamta"))
        chk("dokme-ye گمرک", by.get("gomrok", {}).get("text") == "استعلام گمرک",
            by.get("gomrok"))
        chk("dokme-ye همه", by.get("all", {}).get("text") == "انجام همه استعلام‌ها",
            by.get("all"))
        chk("pishfarz = همتا (on)", by.get("hamta", {}).get("on") is True, btns)
        chk("SVC-e JS pishfarz hamta", d.execute_script("return SVC;") == "hamta")

        # 2) taghyir-e servis -> class-e on + SVC + matn-e dokme-ye radif
        # matn-e dokme-ye radif = 'استعلام '+SVC_FA[svc]; baraye 'all' -> «از گمرک و همتا»
        for svc, fa in (("gomrok", "گمرک"), ("all", "از گمرک و همتا"), ("hamta", "همتا")):
            d.execute_script("pickSvc(arguments[0]);", svc)
            time.sleep(0.35)
            state = d.execute_script("""
                var on=document.querySelector('#svc-btns .svc.on');
                var b=document.querySelector('#dev-list tbody tr td:last-child button.ghost');
                return {svc:SVC, on:on?on.getAttribute('data-svc'):null,
                        onCount:document.querySelectorAll('#svc-btns .svc.on').length,
                        rowBtn:b?(b.textContent||'').trim():null,
                        slabs:Array.prototype.map.call(
                          document.querySelectorAll('#dev-list tbody tr:first-child .stgrid .slab'),
                          function(s){return (s.textContent||'').trim();})};
            """)
            chk(f"pickSvc({svc}) -> SVC", state["svc"] == svc, state["svc"])
            chk(f"pickSvc({svc}) -> faghat yek dokme on", state["onCount"] == 1
                and state["on"] == svc, state)
            chk(f"pickSvc({svc}) -> matn-e dokme-ye radif", state["rowBtn"] ==
                f"استعلام {fa}", state["rowBtn"])
            chk(f"pickSvc({svc}) -> sotoon-e vaziat-e do-gane (همتا+گمرک)",
                "همتا" in state["slabs"] and "گمرک" in state["slabs"],
                state["slabs"])

        # 3) sotoon-e natije mote'aleq be servis-e entekhab-shode
        d.execute_script("pickSvc('hamta');")
        time.sleep(0.3)
        # be-jaye nth-child (ke ba ezafe shodan-e sotoon-ha jabe-ja mishavad)
        # az class-e sabet-e 'res' estefade mishavad
        # radif-e AVAL momken ast dastgah-e taze (bedun-e natije) bashad; pas
        # aval index-e yek radif ke natije-ye HAMTA-ye por darad ra peyda mikonim.
        row_idx = d.execute_script("""
            var trs=document.querySelectorAll('#dev-list tbody tr');
            for(var i=0;i<trs.length;i++){
              var top=trs[i].querySelector('.mono.sn1').textContent.trim();
              var dev=(STATE.devices||[]).filter(function(x){return (x.serial1||'')===top;})[0];
              if(dev && (dev.hamta_result||'') && (dev.result||'') &&
                 dev.hamta_result!==dev.result) return i;
            }
            return 0;
        """)
        def res_of(i):
            return d.execute_script(
                "var trs=document.querySelectorAll('#dev-list tbody tr');"
                "var t=trs[arguments[0]].querySelector('td.res');"
                "return t?(t.innerText||'').trim():'';", i)
        d.execute_script("pickSvc('hamta');")
        time.sleep(0.3)
        res_h = res_of(row_idx)
        d.execute_script("pickSvc('gomrok');")
        time.sleep(0.3)
        res_g = res_of(row_idx)
        d.execute_script("pickSvc('all');")
        time.sleep(0.3)
        res_a = res_of(row_idx)
        chk("natije-ye gomrok ba hamta yeki nist", res_h != res_g,
            f"hamta={res_h[:40]!r} gomrok={res_g[:40]!r}")
        chk("halat-e all har do natije ra neshan midahad",
            "گمرک:" in res_a and "همتا:" in res_a, res_a[:80])

        # 4) badge-e har do servis dar sotoon-e vaziat
        # NOKTE: az class-e .stgrid estefade mishavad (na nth-child ke ba ezafe
        # shodan-e sotoon-e '#' jabe-ja shod)
        labels = d.execute_script(
            "return Array.prototype.map.call("
            "document.querySelectorAll('#dev-list tbody tr:first-child .stgrid .slab'),"
            "function(s){return (s.textContent||'').trim();});")
        chk("label-haye همتا/گمرک dar sotoon-e vaziat",
            labels == ["همتا", "گمرک"], labels)

        # 5) payload-e /estelam-start baraye har servis
        for svc in ("hamta", "gomrok", "all"):
            d.execute_script("window.__calls=[];pickSvc(arguments[0]);startAll();", svc)
            time.sleep(0.6)
            calls = d.execute_script("return window.__calls;")
            body = json.loads(calls[0]["body"]) if calls else {}
            chk(f"POST /estelam-start service={svc}", body.get("service") == svc, body)
        # radif: startOne ham service mifrestad
        d.execute_script("""
            window.__calls=[];pickSvc('all');
            var b=document.querySelector('#dev-list tbody tr td:last-child button.ghost');
            b.click();
        """)
        time.sleep(0.6)
        calls = d.execute_script("return window.__calls;")
        body = json.loads(calls[0]["body"]) if calls else {}
        chk("startOne service+id mifrestad",
            body.get("service") == "all" and bool(body.get("id")), body)

        # 6) hich khata-ye JS
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
