"""Live check-e safhe-ye estelam ba Chrome-e vaghe'i (headless) roo-ye panel-e zende.
Ejra: python lab/scripts/live_check_estelam_page.py
"""
import json
import sys
import time

sys.path.insert(0, r"C:/Users/Saeed/ai_projects/irica/laptop/irica")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

URL = "http://127.0.0.1:8750/site-2"
CHROMEDRIVER = r"C:\chromedriver.exe"

checks = []


def _seed_device():
    """Yek device-e nemune misazad ta check-e jadval deterministic bashad."""
    import panel
    data = panel.estelam_load()
    if not data.get("devices"):
        panel.estelam_add_device({"serial1": "356938035643809", "serial2": ""})


_seed_device()


def check(name, cond, extra=""):
    checks.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)) if extra else ""))


options = Options()
options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1280,1000")
driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)
try:
    driver.get(URL)
    time.sleep(1.2)
    check("title", "استعلام" in driver.title, driver.title)
    check("person form", driver.find_elements(By.ID, "p-national"))
    labels = [b.text.strip() for b in driver.find_elements(By.TAG_NAME, "button")]
    check("dokme afzudan dastgah", any("افزودن دستگاه" in x for x in labels), labels[:6])
    check("dokme afzudan omde", any("افزودن عمده" in x for x in labels))

    # 1) single flow: dokme-ha zaher shavand
    driver.execute_script("startSingle();")
    time.sleep(0.3)
    single = driver.execute_script(
        "return [...document.querySelectorAll('#dev-form-actions button')].map(b=>b.textContent.trim());")
    check("single: zakhire + zakhire va estelam", 
          any("ذخیره" == x or "💾 ذخیره" == x for x in single) and
          any("استعلام" in x for x in single), single)

    # 2) bulk flow
    driver.execute_script("startBulk();")
    time.sleep(0.3)
    bulk = driver.execute_script(
        "return [...document.querySelectorAll('#dev-form-actions button')].map(b=>b.textContent.trim());")
    check("bulk: zakhire va ba'di + payan va boro be estelam",
          any("بعدی" in x for x in bulk) and any("پایان" in x for x in bulk), bulk)

    # 3) validator-e client->server: serial-e kootah
    driver.execute_script("document.getElementById('d-s1').value='123';")
    driver.execute_script("bulkNext();")
    time.sleep(1.0)
    err = driver.execute_script("return document.getElementById('d-err').innerText.trim();")
    check("validator-e serial", "۱۵ رقم" in err, err)

    # 4) validator-e shakhs
    driver.execute_script("""
      document.getElementById('p-national').value='12';
      document.getElementById('p-birth').value='bad';
      document.getElementById('p-mobile').value='0812';
      savePerson();
    """)
    time.sleep(1.0)
    perr = driver.execute_script("return document.getElementById('p-err').innerText.trim();")
    check("validator-e shakhs", "کد ملی" in perr and "موبایل" in perr, perr.replace("\n", " / "))

    # 5) toast-e salamat-e API (hover-e balaye safhe)
    driver.execute_script("checkApiHealth();")
    time.sleep(1.5)
    toast = driver.execute_script("""
      var t=document.getElementById('toast');
      return JSON.stringify({show:t.classList.contains('show'),
        ok:t.classList.contains('ok'), fail:t.classList.contains('fail'),
        title:document.getElementById('toast-title').textContent,
        meta:document.getElementById('toast-meta').textContent});
    """)
    info = json.loads(toast)
    check("toast-e API namayan", info["show"], info["title"])
    check("toast-e API vaziat-dar", info["ok"] or info["fail"], info["meta"][:90])

    # 6) list-e dastgah-ha az server
    rows = driver.execute_script("return document.querySelectorAll('#dev-list tbody tr').length;")
    check("jadval-e dastgah-ha render shod", rows >= 1, rows)

    driver.save_screenshot(r"C:/Users/Saeed/ai_projects/irica/laptop/irica/lab/final/estelam-page-live.png")
finally:
    driver.quit()

failed = [c for c in checks if not c[1]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} live checks passed")
sys.exit(1 if failed else 0)
