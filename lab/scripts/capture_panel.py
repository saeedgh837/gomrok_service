from pathlib import Path
import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

url = sys.argv[1]
out = Path(sys.argv[2])
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
height = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
options = Options()
options.add_argument("--headless=new")
options.add_argument(f"--window-size={width},{height}")
options.add_argument("--force-device-scale-factor=1")
options.add_argument("--hide-scrollbars")
options.add_argument("--no-first-run")
options.add_argument("--disable-gpu")
driver = webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=options)
try:
    driver.set_window_size(width, height)
    driver.get(url)
    time.sleep(2)
    driver.execute_script("var t=document.getElementById('api-health-toast');if(t)t.classList.remove('show');")
    print({
        "title": driver.title,
        "url": driver.current_url,
        "ready": driver.execute_script("return document.readyState"),
        "width": driver.execute_script("return innerWidth"),
        "height": driver.execute_script("return innerHeight"),
        "scrollHeight": driver.execute_script("return document.documentElement.scrollHeight"),
        "bodyText": driver.find_element("tag name", "body").text[:600],
        "console": driver.get_log("browser"),
    })
    driver.save_screenshot(str(out))
    print(out.resolve())
finally:
    driver.quit()
