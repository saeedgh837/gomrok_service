"""Live check for read-only profile view and removed top inquiries tab."""
import json
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8764
URL = f"http://127.0.0.1:{PORT}/site-1?readonly={time.time()}"


def build(width, height):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={width},{height}")
    return webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=options)


def run(width, height):
    driver = build(width, height)
    try:
        driver.get(URL)
        WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
        WebDriverWait(driver, 10).until(lambda d: d.find_elements("css selector", ".profile-overview"))
        initial = driver.execute_script("""
          return {
            topTabs:Array.from(document.querySelectorAll('#main-tabs .nav-tab')).map(x=>x.textContent.trim()),
            topInquiry:document.querySelectorAll('#tab-inquiries,#view-inquiries').length,
            overview:document.querySelectorAll('.profile-overview').length,
            form:document.querySelectorAll('#view-form form#f').length,
            readonlyFields:document.querySelectorAll('#view-form .readonly-field').length,
            innerTabs:Array.from(document.querySelectorAll('#view-form .profile-tab')).map(x=>x.textContent.trim()),
            overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth
          };
        """)
        assert initial["topTabs"] == ["پروفایل انتخاب‌شده", "📋 لاگ‌ها", "⚙️ تنظیمات کلی"], initial
        assert initial["topInquiry"] == 0 and initial["overview"] == 1, initial
        assert initial["form"] == 0 and initial["readonlyFields"] >= 5, initial
        assert initial["innerTabs"] == ["اطلاعات پایه", "اطلاعات هویتی", "استعلامات"], initial
        assert not initial["overflow"], initial

        driver.execute_script("showProfileSection('identity')")
        identity = driver.execute_script("""
          var view=document.getElementById('view-identity');
          return {visible:!view.classList.contains('hidden'), inputs:view.querySelectorAll('input,textarea').length,
                  fields:view.querySelectorAll('.readonly-field').length};
        """)
        assert identity["visible"] and identity["inputs"] == 0 and identity["fields"] >= 6, identity

        driver.find_element("css selector", ".profile-overview-edit").click()
        WebDriverWait(driver, 10).until(lambda d: d.find_elements("css selector", "#view-form form#f"))
        edit = driver.execute_script("return {form:document.querySelectorAll('#view-form form#f').length, inputs:document.querySelectorAll('#view-form input[type=text]').length, overview:document.querySelectorAll('.profile-overview').length}")
        assert edit["form"] == 1 and edit["inputs"] > 0 and edit["overview"] == 0, edit
        return {"viewport":[width,height], "initial":initial, "identity":identity, "edit":edit}
    finally:
        driver.quit()


print(json.dumps([run(1440, 1000), run(430, 900)], ensure_ascii=False))
