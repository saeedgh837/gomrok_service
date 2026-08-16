from pathlib import Path
import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

url = sys.argv[1]
out = Path(sys.argv[2])
options = Options()
options.add_argument("--headless=new")
options.add_argument("--window-size=1440,1000")
options.add_argument("--force-device-scale-factor=1")
options.add_argument("--no-first-run")
options.add_argument("--disable-gpu")
driver = webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=options)
try:
    driver.get(url)
    time.sleep(1.5)
    severe = [item for item in driver.get_log("browser") if item["level"] == "SEVERE"]
    assert not severe, severe
    editor = driver.find_element(By.ID, "view-form")
    assert "برای ویرایش، یک پروفایل را از منوی سمت راست انتخاب کنید" in editor.text
    assert not driver.find_elements(By.CSS_SELECTOR, '#view-form [name="telegram_chat_id"]')
    card = driver.find_element(By.CSS_SELECTOR, '#prof-list [data-profile-id]')
    profile_id = card.get_attribute("data-profile-id")
    card.find_element(By.XPATH, './/button[contains(normalize-space(.), "ویرایش")]').click()
    time.sleep(.3)
    assert driver.find_elements(By.ID, "f")
    assert not driver.find_elements(By.CSS_SELECTOR, '#view-form [name="telegram_chat_id"]')
    assert profile_id == driver.execute_script("return activeId")
    driver.execute_script("var t=document.getElementById('api-health-toast');if(t)t.classList.remove('show');")
    driver.save_screenshot(str(out))
    print({"empty_state": True, "selected_profile": profile_id, "telegram_field_absent": True, "console_errors": 0, "screenshot": str(out.resolve())})
finally:
    driver.quit()
