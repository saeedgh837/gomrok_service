from pathlib import Path
import json, sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8750"
out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "lab")
out_dir.mkdir(parents=True, exist_ok=True)
viewports = [(1440, 1000), (1024, 900), (820, 1000)]
results = []

for page in ("site-1", "site-2"):
    for width, height in viewports:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument(f"--window-size={width},{height}")
        options.add_argument("--force-device-scale-factor=1")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-gpu")
        driver = webdriver.Chrome(service=Service(r"C:\chromedriver.exe"), options=options)
        try:
            driver.set_window_size(width, height)
            driver.get(f"{base}/{page}?qa={width}")
            time.sleep(1.2)
            console = [x for x in driver.get_log("browser") if x["level"] == "SEVERE"]
            metrics = driver.execute_script("return {innerWidth,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,scrollHeight:document.documentElement.scrollHeight}")
            assert metrics["scrollWidth"] <= metrics["clientWidth"] + 1, (page, width, metrics)
            assert not console, (page, width, console)
            if page == "site-1":
                before = driver.find_element(By.ID, "view-form").get_attribute("class")
                driver.find_element(By.ID, "tab-logs").click()
                assert "hidden" not in driver.find_element(By.ID, "view-logs").get_attribute("class")
                assert "hidden" in driver.find_element(By.ID, "view-form").get_attribute("class")
                driver.find_element(By.ID, "tab-form").click()
                assert "hidden" not in driver.find_element(By.ID, "view-form").get_attribute("class")
                driver.find_element(By.ID, "theme-toggle").click()
                assert driver.execute_script("return document.documentElement.getAttribute('data-theme')") in ("light", "dark")
                search = driver.find_element(By.ID, "profile-search")
                search.send_keys("محمد")
                assert driver.find_elements(By.CSS_SELECTOR, "#prof-list .prof-item")
            else:
                driver.find_element(By.CSS_SELECTOR, "#filters [data-filter='success']").click()
                assert driver.find_elements(By.CSS_SELECTOR, "#profile-list .profile")
                search = driver.find_element(By.ID, "profile-search")
                search.send_keys("محمد")
                assert driver.find_elements(By.CSS_SELECTOR, "#profile-list .profile")
            shot = out_dir / f"{page}-{width}-final.png"
            driver.save_screenshot(str(shot))
            results.append({"page": page, "viewport": [width, height], "metrics": metrics, "console_errors": 0, "screenshot": str(shot.resolve())})
        finally:
            driver.quit()

print(json.dumps(results, ensure_ascii=False, indent=2))
