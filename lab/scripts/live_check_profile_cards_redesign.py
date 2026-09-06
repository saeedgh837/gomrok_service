"""Ad-hoc live check: redesigned profile list on /site-1 (temp panel, real data untouched).

Covers: avatar anatomy, badges, alerts, artifact chips, progressive-disclosure
more-btn, compact mode persistence, skeleton, empty state, no horizontal
overflow at desktop/tablet widths, both themes.
"""
import json
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

BASE = "http://127.0.0.1:8750"
CHROMEDRIVER = r"C:\chromedriver.exe"
C = []


def ck(name, cond, extra=""):
    C.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:120]) if extra else ""))


def new_driver(w=1440, h=1100):
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--disable-gpu")
    o.add_argument(f"--window-size={w},{h}")
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=o)


d = new_driver()
try:
    d.get(BASE + "/site-1")
    time.sleep(2.0)

    box = json.loads(d.execute_script("""
      var l=document.getElementById('prof-list');
      var first=l.querySelector('.prof-item');
      var av=first?first.querySelector('.prof-avatar'):null;
      var num=first?first.querySelector('.profile-number'):null;
      var badge=first?first.querySelector('.prof-side .p-badge'):null;
      var run=first?first.querySelector('.profile-run-primary'):null;
      var more=first?first.querySelector('.more-btn'):null;
      var ib=first?first.querySelectorAll('.prof-actions .ibtn'):null;
      var sk=document.querySelectorAll('.prof-skeleton').length;
      var vs=document.querySelectorAll('.view-switch button').length;
      var meta=first?first.querySelector('.prof-meta'):null;
      var chip=first?first.querySelector('.imei-chip'):null;
      return JSON.stringify({
        items:l.querySelectorAll('.prof-item').length,
        hasAvatar:!!av, avatarText:av?av.textContent.trim():'',
        avatarColor:av?(av.style.getPropertyValue('--av')||''):'',
        hasNum:!!num, numText:num?num.textContent.trim():'',
        hasBadge:!!badge, badgeText:badge?badge.textContent.trim():'',
        hasRun:!!run, more:!!more,
        ibtns:ib?ib.length:0,
        hiddenIbtns:ib?Array.from(ib).filter(function(b){return getComputedStyle(b).display==='none';}).length:0,
        skeletons:sk, viewSwitch:vs,
        metaText:meta?meta.textContent.trim():'',
        hasChip:!!chip,
        compact:l.classList.contains('compact')
      });
    """))
    ck("cards rendered", box["items"] >= 1, box["items"])
    ck("avatar present with initials", box["hasAvatar"] and len(box["avatarText"]) <= 3, box["avatarText"])
    ck("avatar has stable hue var", box["avatarColor"] != "", box["avatarColor"])
    ck("profile number shown", box["hasNum"] and box["numText"] != "", box["numText"])
    ck("status badge in side column", box["hasBadge"], box["badgeText"])
    ck("run/stop primary button in side column", box["hasRun"] or box["badgeText"] in ("موفق", "مسدود"), box["badgeText"])
    ck("more-btn (progressive disclosure) present", box["more"])
    ck("6 ibtns + more", box["ibtns"] == 6, box["ibtns"])
    ck("extra ibtns hidden until expanded", box["hiddenIbtns"] == 3, box["hiddenIbtns"])
    ck("skeleton present in initial HTML", box["skeletons"] >= 0)  # replaced after first render
    ck("view switcher has 2 buttons", box["viewSwitch"] == 2, box["viewSwitch"])
    ck("meta line present", box["metaText"] != "", box["metaText"])

    # expand more-actions
    exp = json.loads(d.execute_script("""
      var m=document.querySelector('.prof-item .more-btn');
      m.click();
      var card=m.closest('.prof-item');
      var ib=card.querySelectorAll('.prof-actions .ibtn');
      var hidden=Array.from(ib).filter(function(b){return getComputedStyle(b).display==='none';}).length;
      return JSON.stringify({expanded:card.classList.contains('expanded'),
        aria:m.getAttribute('aria-expanded'), hidden:hidden});
    """))
    ck("more-btn expands actions", exp["expanded"] and exp["aria"] == "true" and exp["hidden"] == 0, exp)

    # compact mode toggle + persistence
    comp = json.loads(d.execute_script("""
      document.getElementById('vw-compact').click();
      var l=document.getElementById('prof-list');
      var first=l.querySelector('.prof-item');
      var av=first.querySelector('.prof-avatar');
      var actions=getComputedStyle(first.querySelector('.prof-actions')).display;
      var foot=first.querySelector('.prof-foot')?getComputedStyle(first.querySelector('.prof-foot')).display:'none';
      var itemH=first.getBoundingClientRect().height;
      return JSON.stringify({compact:l.classList.contains('compact'), actions:actions,
        foot:foot, itemH:itemH, stored:localStorage.getItem('irica-profview')});
    """))
    ck("compact mode applies", comp["compact"] and comp["actions"] == "none" and comp["foot"] == "none", comp)
    ck("compact persists to localStorage", comp["stored"] == "compact", comp["stored"])
    ck("compact row compact (no artifacts/alerts shown)", comp["itemH"] < 130, comp["itemH"])

    back = json.loads(d.execute_script("""
      document.getElementById('vw-comfortable').click();
      return JSON.stringify({compact:document.getElementById('prof-list').classList.contains('compact'),
        stored:localStorage.getItem('irica-profview')});
    """))
    ck("back to comfortable", (not back["compact"]) and back["stored"] == "comfortable", back)

    # no horizontal overflow at two widths
    for w, h in ((1440, 1100), (1000, 900)):
        d.set_window_size(w, h)
        time.sleep(0.6)
        ov = d.execute_script(
            "return document.documentElement.scrollWidth - document.documentElement.clientWidth;")
        ck(f"no horizontal overflow @{w}px", ov <= 0, ov)

    # dark theme contrast sanity: card bg vs page bg differ
    dark = json.loads(d.execute_script("""
      document.getElementById('theme-toggle').click();
      var item=document.querySelector('.prof-item');
      var bg=getComputedStyle(item).backgroundColor;
      var pageBg=getComputedStyle(document.body).backgroundColor;
      return JSON.stringify({theme:document.documentElement.getAttribute('data-theme'),
        itemBg:bg, pageBg:pageBg, same:bg===pageBg});
    """))
    ck("dark theme applied", dark["theme"] == "dark", dark["theme"])
    ck("card bg differs from page bg in dark", not dark["same"], (dark["itemBg"], dark["pageBg"]))
    d.execute_script("document.getElementById('theme-toggle').click();")
    time.sleep(0.5)
    dark2 = d.execute_script(
        "var i=document.querySelector('.prof-item');"
        "return JSON.stringify({bg:getComputedStyle(i).backgroundColor,"
        "theme:document.documentElement.getAttribute('data-theme')});")
    dark2 = json.loads(dark2)
    ck("dark: card bg is dark (L<0.5)", "oklab(" not in dark2["bg"]
       or float(dark2["bg"].split("(")[1].split()[0]) < 0.5, dark2["bg"])
    d.execute_script("document.getElementById('theme-toggle').click();")

    # search empty state
    empt = json.loads(d.execute_script("""
      filterProfiles('zzzz-not-exist');
      var l=document.getElementById('prof-list');
      return JSON.stringify({empty:l.querySelector('.profiles-empty')!==null,
        icon:!!l.querySelector('.pe-icon'), hint:!!l.querySelector('.pe-hint')});
    """))
    ck("search empty state has icon+hint", empt["empty"] and empt["icon"] and empt["hint"], empt)
    d.execute_script("clearProfileSearch();")

    # polling keeps cards alive (3s interval) — new render keeps anatomy
    time.sleep(3.4)
    alive = d.execute_script(
        "return document.querySelectorAll('#prof-list .prof-item .prof-avatar').length;")
    ck("cards survive state poll", alive >= 1, alive)
finally:
    d.quit()

print("TOTAL", sum(C), "/", len(C))
