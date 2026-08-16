"""Live check-e faz-e UI: kadr-e sabet-e profile-ha + dokme-haye icon-i ba tooltip
+ notification-e log-e jadid dar HAR DO panel (site-1 va estelam).

Ejra (panel bayad roo-ye 127.0.0.1:8750 bala bashad):
  python lab/scripts/live_check_panels_ui_phase.py
"""
import json
import os
import sys
import time

ROOT = r"C:/Users/Saeed/ai_projects/irica/laptop/irica"
sys.path.insert(0, ROOT)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

BASE = "http://127.0.0.1:8750"
CHROMEDRIVER = r"C:\chromedriver.exe"
C = []


def ck(name, cond, extra=""):
    C.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:120]) if extra else ""))


def seed_estelam_logs():
    import panel
    panel.clear_estelam_logs()
    panel.estelam_log("running", "شروع استعلام دستگاه 1 از 2", step="استعلام",
                      serial="356938035643809")
    panel.estelam_log("healthy", "دستگاه سالم است — شناسه یافت نشد.", step="استعلام",
                      serial="356938035643809")


def new_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1100")
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)


# ------------------------------------------------------------------ site-1
def check_site1(driver):
    driver.get(BASE + "/site-1")
    time.sleep(1.6)

    box = json.loads(driver.execute_script("""
      var l=document.getElementById('prof-list'), s=document.querySelector('.sidebar');
      return JSON.stringify({listH:l.clientHeight, listScroll:getComputedStyle(l).overflowY,
        zoom:getComputedStyle(document.documentElement).zoom,
        sideW:s.getBoundingClientRect().width,
        sideOverflow:getComputedStyle(s).overflow,
        items:l.querySelectorAll('.prof-item').length});
    """))
    ck("site-1: kadr-e profile-ha scrollable", box["listScroll"] in ("auto", "scroll"),
       box["listScroll"])
    ck("site-1: kadr-e sabet (sidebar overflow hidden)", box["sideOverflow"] == "hidden",
       box["sideOverflow"])
    ck("site-1: zoom-e panel NORMAL ast", str(box["zoom"]) in ("1", "1.0", "normal", ""),
       box["zoom"])
    ck("site-1: arz-e sidebar bozorgtar (>=420px)", box["sideW"] >= 420, box["sideW"])
    ck("site-1: profile-ha render shodand", box["items"] >= 1, box["items"])

    icons = json.loads(driver.execute_script("""
      var b=[...document.querySelectorAll('.prof-item .ibtn')];
      return JSON.stringify({n:b.length, tips:b.map(function(x){return x.getAttribute('data-tip');}),
        txt:b.map(function(x){return x.textContent.trim();}),
        oldBtns:document.querySelectorAll('.prof-actions .mini-btn').length});
    """))
    ck("site-1: 5 dokme-ye icon-i", icons["n"] >= 5, icons["n"])
    ck("site-1: hame tooltip darand", all(icons["tips"][:5]), icons["tips"][:5])
    ck("site-1: matn-e dokme-ha icon shode", all(len(t) <= 3 for t in icons["txt"][:5]),
       icons["txt"][:5])
    ck("site-1: dokme-ye matni-ye ghadimi hazf shod", icons["oldBtns"] == 0, icons["oldBtns"])

    tip = json.loads(driver.execute_script("""
      var b=document.querySelector('.prof-item .ibtn');
      var cs=window.getComputedStyle(b,'::after');
      return JSON.stringify({content:cs.getPropertyValue('content'),
        pos:window.getComputedStyle(b).position, tip:b.getAttribute('data-tip')});
    """))
    ck("site-1: tooltip az data-tip miayad",
       tip["pos"] == "relative" and bool(tip["tip"]), (tip["pos"], tip["tip"]))

    # notification-e log-e jadid (site-1)
    ck("site-1: wrap-e notification hast",
       driver.execute_script("return !!document.getElementById('lognotif-wrap');"))
    ck("site-1: dokme-ye optional-e notification hast",
       driver.execute_script("return !!document.getElementById('notif-toggle');"))
    shown = driver.execute_script("""
      pushLogNotif({ts:'2026-01-01 10:00:00',status:'success',step:'مرحله ۵',
        message:'کد امنیتی پیدا شد',title:'محمد اصلانی',imei:'356938035643809'});
      return document.querySelectorAll('#lognotif-wrap .lognotif').length;
    """)
    time.sleep(0.5)
    detail = json.loads(driver.execute_script("""
      var n=document.querySelector('#lognotif-wrap .lognotif');
      var r=n.getBoundingClientRect();
      return JSON.stringify({txt:n.innerText, left:r.left, top:r.top,
        cls:n.className, shown:n.classList.contains('show')});
    """))
    ck("site-1: notification neshan dade shod", shown == 1 and detail["shown"], shown)
    ck("site-1: notification BALA-CHAP ast", detail["left"] < 60 and detail["top"] < 60,
       (detail["left"], detail["top"]))
    ck("site-1: notification zaman+serial darad",
       "2026-01-01" in detail["txt"] and "356938035643809" in detail["txt"],
       detail["txt"].replace("\n", " / ")[:90])

    off = driver.execute_script("""
      toggleLogNotif();               // -> khamoosh
      var wasOff=(NOTIF_ON===false);
      document.getElementById('lognotif-wrap').innerHTML='';
      NOTIF_READY=true;
      notifyNewLogs([{ts:'zz',status:'info',step:'q',message:'gated-out'}]);
      var n1=document.querySelectorAll('#lognotif-wrap .lognotif').length;
      toggleLogNotif();               // -> roshan dobare
      notifyNewLogs([{ts:'zz2',status:'info',step:'q2',message:'gated-in'}]);
      return JSON.stringify({off:wasOff, afterOff:n1, on:NOTIF_ON});
    """)
    o = json.loads(off)
    ck("site-1: khamoosh kardan-e notification kar mikonad",
       o["off"] and o["afterOff"] == 0 and o["on"] is True, o)


# ------------------------------------------------------------------ estelam
def check_estelam(driver):
    driver.get(BASE + "/site-2")
    time.sleep(1.6)

    layout = json.loads(driver.execute_script("""
      var side=document.querySelector('.side');
      var main=document.querySelector('.colmain');
      var p=document.getElementById('p-national');
      var sr=side.getBoundingClientRect(), mr=main.getBoundingClientRect();
      return JSON.stringify({sideRight:sr.right, mainRight:mr.right,
        sideSticky:getComputedStyle(side).position,
        personInSide:!!side.contains(p), cols:getComputedStyle(document.querySelector('.cols')).gridTemplateColumns});
    """))
    ck("estelam: form-e shakhs dar kadr-e kenari", layout["personInSide"])
    ck("estelam: kadr SAMT-e RAST ast (RTL)", layout["sideRight"] > layout["mainRight"],
       (layout["sideRight"], layout["mainRight"]))
    ck("estelam: kadr sticky ast", layout["sideSticky"] == "sticky", layout["sideSticky"])
    ck("estelam: layout do-sotooni", len(layout["cols"].split()) == 2, layout["cols"])

    ck("estelam: wrap-e notification hast",
       driver.execute_script("return !!document.getElementById('lognotif-wrap');"))
    ck("estelam: dokme-ye optional hast",
       driver.execute_script("return !!document.getElementById('notif-toggle');"))

    shown = driver.execute_script("""
      pushLogNotif({ts:'2026-02-02 11:11:11',status:'healthy',
        message:'دستگاه سالم است — شناسه یافت نشد.',serial:'356938035643809'});
      return document.querySelectorAll('#lognotif-wrap .lognotif').length;
    """)
    time.sleep(0.5)
    detail = json.loads(driver.execute_script("""
      var n=document.querySelector('#lognotif-wrap .lognotif');
      var r=n.getBoundingClientRect();
      return JSON.stringify({txt:n.innerText, left:r.left, top:r.top, cls:n.className});
    """))
    ck("estelam: notification neshan dade shod", shown == 1, shown)
    ck("estelam: BALA-CHAP", detail["left"] < 60 and detail["top"] < 60,
       (detail["left"], detail["top"]))
    ck("estelam: rang-e vaziat (s-healthy)", "s-healthy" in detail["cls"], detail["cls"])
    ck("estelam: zaman + serial dar notification",
       "2026-02-02" in detail["txt"] and "356938035643809" in detail["txt"],
       detail["txt"].replace("\n", " / ")[:90])

    # log-e VAGHE'I-ye jadid -> notification khodkar (bedun-e call-e dasti)
    import panel
    panel.estelam_log("problem", "نتیجه دارای اشکال — تست خودکار",
                      step="استعلام", serial="999888777666555")
    driver.execute_script("document.getElementById('lognotif-wrap').innerHTML='';renderLogs();")
    time.sleep(1.4)
    auto = json.loads(driver.execute_script("""
      var ns=[...document.querySelectorAll('#lognotif-wrap .lognotif')];
      return JSON.stringify({n:ns.length, txt:ns.map(function(x){return x.innerText;}).join(' | ')});
    """))
    ck("estelam: log-e jadid khodkar notification mishavad",
       auto["n"] >= 1 and "999888777666555" in auto["txt"], auto["txt"][:100])

    # kadr-e log hanooz sabet
    h = driver.execute_script("return document.getElementById('log-box').clientHeight;")
    ck("estelam: kadr-e log hanooz sabet", 300 <= h <= 380, h)

    driver.save_screenshot(os.path.join(ROOT, "lab", "final", "estelam-side-person.png"))


seed_estelam_logs()
drv = new_driver()
try:
    check_site1(drv)
    check_estelam(drv)
    drv.get(BASE + "/site-1")
    time.sleep(1.2)
    drv.save_screenshot(os.path.join(ROOT, "lab", "final", "site1-profiles-icons.png"))
finally:
    drv.quit()

print(f"\n{sum(C)}/{len(C)} live checks passed")
sys.exit(0 if all(C) else 1)
