"""Live browser check for the system debug settings card."""
import json
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
URL = f"http://127.0.0.1:{PORT}/site-1?system-debug={time.time()}"

options = Options()
options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1440,1000")
driver = webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=options)
try:
    driver.get(URL)
    WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
    driver.execute_script("showView('telegram')")
    WebDriverWait(driver, 10).until(lambda d: d.find_element("id", "system-debug-toggle"))
    result = driver.execute_script("""
      var toggle=document.getElementById('system-debug-toggle');
      return {debugText:document.querySelector('.system-debug-card h3').textContent.trim(),
              list:!!document.getElementById('system-log-list'),
              viewer:!!document.getElementById('system-log-viewer'),
              routes:['/system-debug-config','/system-logs','/system-log'].every(function(x){return document.documentElement.innerHTML.indexOf(x)>=0}),
              checked:toggle.checked,
              overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth};
    """)
    assert result["debugText"] == "Debug log سیستمی", result
    assert result["list"] and result["viewer"] and result["routes"], result
    assert not result["checked"] and not result["overflow"], result
    print(json.dumps(result, ensure_ascii=False))
finally:
    driver.quit()
