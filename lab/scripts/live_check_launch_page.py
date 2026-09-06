"""Debug pass 3: real-browser screenshot + interaction test of /launch."""
import sys
import time

sys.path.insert(0, r"C:\Users\Saeed\ai_projects\irica\laptop\irica\lab\scripts")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

SHOTS = r"C:\Users\Saeed\AppData\Local\Temp"

o = Options()
o.add_argument("--headless=new")
o.add_argument("--disable-gpu")
o.add_argument("--window-size=520,760")
d = webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=o)
try:
    d.get("http://127.0.0.1:8750/launch")
    time.sleep(2.5)
    ok = []
    def ck(name, cond):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name)

    ck("title RTL", d.find_element("tag name", "html").get_attribute("dir") == "rtl")
    status = d.find_element("id", "status-text").text
    ck("status active: " + status, "فعال" in status)
    total = d.find_element("id", "s-total").text
    ck("stats filled (persian digits): " + total, total not in ("—", ""))
    ver = d.find_element("id", "v").text
    ck("version badge: " + ver, ver.startswith("1.0"))
    # click پنل گمرک -> navigates to /site-1
    d.find_element("id", "btn-open").click()
    time.sleep(1.5)
    ck("btn-open -> /site-1", "/site-1" in d.current_url)
    d.back()
    time.sleep(1)
    # click بستن سرویس -> status flips to off
    d.find_element("id", "btn-stop").click()
    time.sleep(3)
    st = d.find_element("id", "status-text").text
    off_msg = d.find_element("id", "off-msg")
    ck("after stop status: " + st, "خاموش" in st)
    ck("off message visible", off_msg.is_displayed())
    d.save_screenshot(SHOTS + r"\irica-launch-after-stop.png")
    print("screenshot:", SHOTS + r"\irica-launch-after-stop.png")
    print("RESULT:", "ALL PASS" if all(ok) else f"{ok.count(False)} FAILURES")
finally:
    d.quit()
