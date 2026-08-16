"""Live check-e bakhsh-e MODIRIAT + LOG-e safhe-ye estelam (Chrome-e vaghe'i, headless).
Ejra: python lab/scripts/live_check_estelam_manage_logs.py
"""
import json
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

URL = "http://127.0.0.1:8750/site-2"
CHROMEDRIVER = r"C:\chromedriver.exe"
C = []


def _seed_state():
    """Device + log-e nemune misazad ta chip-haye runbar va filter-e log
    deterministic bashand."""
    sys.path.insert(0, r"C:/Users/Saeed/ai_projects/irica/laptop/irica")
    import panel
    data = panel.estelam_load()
    if not data.get("devices"):
        panel.estelam_add_device({"serial1": "356938035643809", "serial2": ""})
    panel.clear_estelam_logs()
    panel.estelam_log("running", "شروع استعلام دستگاه 1 از 2", step="استعلام",
                      serial="356938035643809", device_id="a")
    panel.estelam_log("retry", "خطای موقت — کپچای تازه و تلاش مجدد (3 تلاش)",
                      step="استعلام", serial="356938035643809")
    panel.estelam_log("healthy", "دستگاه سالم است — شناسه یافت نشد.",
                      step="استعلام", serial="356938035643809")
    panel.estelam_log("problem", "نتیجه دارای اشکال — قبلا ثبت شده",
                      step="استعلام", serial="111222333444555")


_seed_state()


def ck(name, cond, extra=""):
    C.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:120]) if extra else ""))


options = Options()
options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1280,1100")
driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)
try:
    driver.get(URL)
    time.sleep(1.6)

    labels = [b.text.strip() for b in driver.find_elements(By.TAG_NAME, "button")]
    for needed in ("توقف و بستن مرورگر", "راه‌اندازی مجدد",
                   "تلاش مجدد ناموفق‌ها", "به‌روزرسانی وضعیت"):
        ck(f"dokme-ye modiriat: {needed}", any(needed in x for x in labels))

    rb = driver.execute_script("""
      return JSON.stringify({state:document.getElementById('run-state').textContent.trim(),
        meta:document.getElementById('run-meta').textContent.trim(),
        chips:document.getElementById('run-chips').textContent.trim(),
        dot:document.getElementById('run-dot').className});
    """)
    info = json.loads(rb)
    ck("runbar vaziat-dar", info["state"] in ("متوقف", "در حال اجرا"), info["state"])
    # chips ba avvalin refresh (~4s) por mishavad — sabr mikonim
    driver.execute_script("refresh();")
    time.sleep(1.2)
    chips = driver.execute_script(
        "return document.getElementById('run-chips').textContent.trim();")
    ck("runbar chip-e shomaresh", ":" in chips, chips)

    # --- kadr-e log: sabet + scroll ---
    box = driver.execute_script("""
      var b=document.getElementById('log-box');
      return JSON.stringify({h:b.clientHeight, sh:b.scrollHeight,
        overflowY:getComputedStyle(b).overflowY,
        items:b.querySelectorAll('.li').length});
    """)
    lb = json.loads(box)
    ck("logbox ertefa-e sabet (~340px)", 300 <= lb["h"] <= 380, lb["h"])
    ck("logbox scrollable", lb["overflowY"] in ("auto", "scroll"), lb["overflowY"])
    ck("log-ha render shodand", lb["items"] >= 4, lb["items"])

    # jozyiat: zaman + serial dar har log
    detail = driver.execute_script("""
      var i=document.querySelector('#log-box .li');
      return JSON.stringify({ts:(i.querySelector('.ts')||{}).textContent||'',
        sn:(i.querySelector('.sn')||{}).textContent||'',
        head:(i.querySelector('.h b')||{}).textContent||''});
    """)
    d = json.loads(detail)
    ck("log: zaman namayan", len(d["ts"]) >= 16, d["ts"])
    ck("log: serial-e dastgah namayan", "سریال" in d["sn"], d["sn"].strip())
    ck("log: icon + vaziat", any(x in d["head"] for x in ("✅", "⛔", "🔁", "▶️", "⚠️")), d["head"])

    # interaktivi: click -> baz shodan
    before = driver.execute_script(
        "return document.querySelector('#log-box .li').className;")
    driver.execute_script("document.querySelector('#log-box .li').click();")
    time.sleep(0.4)
    after = driver.execute_script(
        "return document.querySelector('#log-box .li').className;")
    ck("click roo-ye log -> baz mishavad", "open" in after and "open" not in before,
       f"{before} -> {after}")

    # filter-e vaziat
    driver.execute_script("setLogFilter('healthy');")
    time.sleep(0.4)
    only_healthy = driver.execute_script("""
      var ls=[...document.querySelectorAll('#log-box .li')];
      return ls.length>0 && ls.every(function(x){return x.className.indexOf('s-healthy')>=0;});
    """)
    ck("filter-e 'سالم'", only_healthy)

    # jostojoo
    driver.execute_script("setLogFilter('all');")
    driver.execute_script("document.getElementById('log-q').value='111222333444555';renderLogs();")
    time.sleep(0.9)
    q_rows = driver.execute_script("return document.querySelectorAll('#log-box .li').length;")
    q_has = driver.execute_script(
        "return (document.getElementById('log-box').innerText||'').indexOf('111222333444555')>=0;")
    ck("jostojoo dar log", q_rows == 1 and q_has, q_rows)

    # safhe boland nashode (kadr-e log sabet ast)
    driver.execute_script("document.getElementById('log-q').value='';renderLogs();")
    time.sleep(0.8)
    h2 = driver.execute_script("return document.getElementById('log-box').clientHeight;")
    ck("kadr-e log ba'd az refresh ham sabet", 300 <= h2 <= 380, h2)

    driver.save_screenshot(
        r"C:/Users/Saeed/ai_projects/irica/laptop/irica/lab/final/estelam-manage-logs.png")
finally:
    driver.quit()

print(f"\n{sum(C)}/{len(C)} live checks passed")
sys.exit(0 if all(C) else 1)
