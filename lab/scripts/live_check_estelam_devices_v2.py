"""live_check_estelam_devices_v2.py
Check-e ZENDE-ye 4 taghyir-e jadid dar tab-e dastgah-ha (/site-2):
  1) tab-e «همه»: dokme-ye shoro' motn-e «استعلام از گمرک و همتا» darad (na «همه استعلام‌ها»)
  2) radif-ha: JADID-tarin dastgah BALA + sotoon-e shomare-ye radif (#) ba raqam-e farsi
  3) sotoon-e vaziat: 2 zir-sotoon — badge + zaman-e estelam ('--' agar anjam nashode)
  4) har dastgah name-e ejbari; zaman-e estelam roo-ye natije mohr mishavad
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait           # noqa: E402

URL = "http://127.0.0.1:8750/site-2"
ok, fail = [], []
_FA = "۰۱۲۳۴۵۶۷۸۹"


def toFa(n):
    return "".join(_FA[int(c)] if c.isdigit() else c for c in str(n))


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
    # --- backend: validate + set_device zaman ra mohr mikonad -----------------
    import panel as P
    errs = P.validate_estelam_device({"serial1": "123456789012345"})
    chk("BE:name-e khali rad mishavad", any("نام" in e for e in errs), errs)
    chk("BE:ba name motabar ast",
        not P.validate_estelam_device({"name": "x", "serial1": "123456789012345"}))
    rec = P.estelam_add_device({"name": "تست‌دستگاه", "serial1": "111111111111111"})
    chk("BE:record name/at-haye khali darad",
        rec["name"] == "تست‌دستگاه" and rec["gomrok_at"] == "" and rec["hamta_at"] == "", rec)
    P.estelam_set_device(rec["id"], hamta_status="good", hamta_result="ok")
    dev = [d for d in P.estelam_load()["devices"] if d["id"] == rec["id"]][0]
    chk("BE:hamta_at ba'd az natije mohr shod", bool(dev["hamta_at"]), dev.get("hamta_at"))
    chk("BE:gomrok_at hanooz khali (estelam nagerft)", dev["gomrok_at"] == "", dev.get("gomrok_at"))
    P.estelam_set_device(rec["id"], status="healthy", result="ok")
    dev = [d for d in P.estelam_load()["devices"] if d["id"] == rec["id"]][0]
    chk("BE:gomrok_at ba'd az natije-ye gomrok mohr shod", bool(dev["gomrok_at"]))
    P.estelam_delete_device(rec["id"])  # cleanup

    # --- UI zende ------------------------------------------------------------
    d = build()
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.3)

        # 1) tab-e «همه»: matn-e dokme
        d.execute_script("pickSvc('all');")
        time.sleep(0.4)
        btn = d.execute_script(
            "var b=document.getElementById('btn-start-all');return b?b.textContent.trim():'';")
        chk("tab-e all: dokme «از گمرک و همتا»", "از گمرک و همتا" in btn, btn)
        chk("tab-e all: dokme «همه استعلام‌ها» NADARAD", "همه استعلام" not in btn, btn)
        row_btn = d.execute_script(
            "var b=document.querySelector('#dev-list tbody tr td:last-child button.ghost');"
            "return b?b.textContent.trim():'';")
        chk("dokme-ye radif dar all «از گمرک و همتا»", "از گمرک و همتا" in row_btn, row_btn)
        # hamta tab -> dokme farق mikonad
        d.execute_script("pickSvc('hamta');")
        time.sleep(0.3)
        chk("tab-e hamta: dokme motefavet",
            "همتا" in d.execute_script("return document.getElementById('btn-start-all').textContent;"))

        # 2) tartib-e jadid-tarin bala + shomare-ye radif
        info = d.execute_script("""
            var trs=document.querySelectorAll('#dev-list tbody tr');
            var nums=Array.prototype.map.call(trs,function(tr){
              return (tr.querySelector('td.c-num').textContent||'').trim();});
            var serials=Array.prototype.map.call(trs,function(tr){
              var sn=tr.querySelector('.mono.sn1');return sn?sn.textContent.trim():'';});
            return {nums:nums, serials:serials, count:trs.length};
        """)
        chk("sotoon-e shomare-ye radif hast", len(info["nums"]) >= 1, info["nums"])
        chk("shomare ba raqam-e farsi ('۱' aval)", info["nums"][0] == "۱", info["nums"][0])
        chk("shomare-ye radif poshte-sar-ham (۱,۲,۳,...)",
            info["nums"] == [toFa(i + 1) for i in range(len(info["nums"]))], info["nums"][:5])
        # jadid-tarin bala: serial-e radif-e aval == serial-e device-e ba bishtarin created
        order_ok = d.execute_script("""
            var ds=(STATE.devices||[]).slice();
            if(!ds.length) return {top:'',expect:''};
            var newest=ds.slice().sort(function(a,b){
              var ca=String(a.created||''),cb=String(b.created||'');
              return ca<cb?1:(ca>cb?-1:0);})[0];
            var top=document.querySelector('#dev-list tbody tr .mono.sn1');
            return {top:top?top.textContent.trim():'', expect:newest.serial1||''};
        """)
        chk("jadid-tarin dastgah dar radif-e aval",
            order_ok["top"] == order_ok["expect"], order_ok)

        # 3) sotoon-e vaziat: 2 zir-sotoon (badge + zaman)
        st = d.execute_script("""
            var tr=document.querySelector('#dev-list tbody tr');
            var g=tr.querySelector('td:nth-child(3) .stgrid');
            if(!g) return null;
            var c=g.children;
            function cell(i){return {lab:(c[i].textContent||'').trim(),
                                     badge:(c[i+1].textContent||'').trim(),
                                     when:(c[i+2].textContent||'').trim()};}
            return {n:c.length/3, hamta:cell(0), gomrok:cell(3)};
        """)
        chk("sotoon-e vaziat 2 zir-radif darad (همتا/گمرک)",
            st and st["n"] == 2, st)
        chk("radif-e همتا label darad", st["hamta"]["lab"] == "همتا", st["hamta"])
        chk("radif-e گمرک label darad", st["gomrok"]["lab"] == "گمرک", st["gomrok"])
        chk("zir-sotoon-e zaman-e همتا por/'--'",
            bool(st["hamta"]["when"]), st["hamta"]["when"])
        chk("zir-sotoon-e zaman-e گمرک por/'--'",
            bool(st["gomrok"]["when"]), st["gomrok"]["when"])

        # yek device-e estelam-nashode zaman-ash '--' ast
        dash = d.execute_script("""
            var trs=document.querySelectorAll('#dev-list tbody tr');
            for(var i=0;i<trs.length;i++){
              var whens=trs[i].querySelectorAll('td:nth-child(3) .when');
              for(var j=0;j<whens.length;j++){
                if((whens[j].textContent||'').trim()==='--') return true;
              }
            }
            return false;
        """)
        # (momken ast hame estelam shode bashand; pas faghat check-e format)
        fmt_ok = d.execute_script("""
            var whens=document.querySelectorAll('#dev-list td:nth-child(3) .when');
            for(var i=0;i<whens.length;i++){
              var t=(whens[i].textContent||'').trim();
              if(t!=='--' && !/^[۰-۹]{2}\\/[۰-۹]{2} [۰-۹]{2}:[۰-۹]{2}$/.test(t)) return t;
            }
            return true;
        """)
        chk("format-e zaman: '--' ya 'MM/DD HH:MM'-e farsi", fmt_ok is True, fmt_ok)

        # 4) name dar sotoon-e dastgah namayesh dade mishavad
        nm = d.execute_script(
            "var e=document.querySelector('#dev-list .dev-name');"
            "return e?(e.textContent||'').trim():null;")
        chk("nam-e dastgah dar jadval namayesh dade mishavad", nm is not None, nm)

        # 4b) form-e afzoodan: field-e nam ejbari ast va dastgah-e jadid BALA miayad
        d.execute_script("startSingle();")
        time.sleep(0.4)
        chk("form-e afzoodan field-e nam darad",
            d.execute_script("return !!document.getElementById('d-name');"))
        # bedun-e nam -> khata
        d.execute_script("""
            document.getElementById('d-name').value='';
            document.getElementById('d-s1').value='222222222222222';
            saveDevice(false);
        """)
        time.sleep(1.0)
        err = d.execute_script(
            "var e=document.getElementById('d-err');"
            "return {show:e.classList.contains('show'),txt:(e.innerText||'').trim()};")
        chk("bedun-e nam khata neshan dade mishavad",
            err["show"] and "نام" in err["txt"], err)
        # ba nam -> zakhire va radif-e AVAL
        uniq = "تست-" + str(int(time.time()))[-5:]
        d.execute_script("""
            document.getElementById('d-name').value=arguments[0];
            document.getElementById('d-s1').value='222222222222222';
            saveDevice(false);
        """, uniq)
        time.sleep(1.4)
        top = d.execute_script("""
            var tr=document.querySelector('#dev-list tbody tr');
            return {num:(tr.querySelector('td.c-num').textContent||'').trim(),
                    name:(tr.querySelector('.dev-name').textContent||'').trim(),
                    hamtaWhen:(tr.querySelectorAll('td:nth-child(3) .when')[0].textContent||'').trim(),
                    gomrokWhen:(tr.querySelectorAll('td:nth-child(3) .when')[1].textContent||'').trim()};
        """)
        chk("dastgah-e JADID dar radif-e aval ba shomare ۱",
            top["num"] == "۱" and top["name"] == uniq, top)
        chk("zaman-e estelam-e dastgah-e taze '--' ast",
            top["hamtaWhen"] == "--" and top["gomrokWhen"] == "--", top)
        # cleanup: dastgah-e test hazf shavad.
        # NOKTE: arguments[0] dakhel-e callback-e filter be arg-e HAMAN callback
        # eshare mikonad, na be arg-e script — pas aval dar yek moteghayer zakhire.
        new_id = d.execute_script("""
            var want=arguments[0];
            var x=(STATE.devices||[]).filter(function(v){return v.name===want;})[0];
            return x?x.id:null;
        """, uniq)
        if new_id:
            d.execute_script("delDevice(arguments[0]);", new_id)
            time.sleep(0.8)
            gone = d.execute_script("""
                var want=arguments[0];
                return (STATE.devices||[]).filter(
                  function(v){return v.name===want;}).length===0;
            """, uniq)
            chk("cleanup: dastgah-e test hazf shod", gone)
        else:
            chk("cleanup: dastgah-e test peyda shod baraye hazf", False, uniq)

        # jadval/safhe sarriz nadarad
        of = d.execute_script("""
            var t=document.querySelector('#dev-list table');
            return {sw:t.scrollWidth,cw:t.clientWidth,
                    dsw:document.documentElement.scrollWidth,
                    dcw:document.documentElement.clientWidth};
        """)
        chk("jadval bedun-e sarriz", of["sw"] <= of["cw"] + 1, of)
        chk("safhe bedun-e scroll-e ofoghi", of["dsw"] <= of["dcw"] + 1, of)

        errs2 = [e for e in d.get_log("browser")
                 if e.get("level") == "SEVERE"
                 and "favicon" not in e.get("message", "")
                 # 400-e AMDI: test-e bala bedun-e nam submit kard ta khata-ye
                 # validation ra bebinad — in log-e shabake ast, na bug.
                 and "/estelam-device" not in e.get("message", "")]
        chk("bedun-e khata-ye JS", not errs2, errs2[:2])

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
