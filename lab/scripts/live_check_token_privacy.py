"""Live check: token-e bot dar UI-ye zende ghabel-e moshahede/copy nist.

Sharayet: panel roo-ye http://127.0.0.1:8750 bala bashad.
Ejra:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/scripts/live_check_token_privacy.py
"""
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

URL = "http://127.0.0.1:8750/site-1"
CHROMEDRIVER = r"C:\chromedriver.exe"

C = []


def ck(name, cond, extra=""):
    C.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:120]) if extra else ""))


def main():
    real_token = json.load(open(os.path.join(ROOT, "telegram_config.json"),
                                encoding="utf-8"))["bot_token"]

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1500,1000")
    driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)
    try:
        driver.get(URL)
        time.sleep(1.5)

        ck("token dar page_source nist", real_token not in driver.page_source)

        driver.execute_script("showView('telegram')")
        time.sleep(0.6)
        ck("token ba'd az baz kardan-e tab-e tanzimat ham nist",
           real_token not in driver.page_source)

        field = driver.find_element(By.ID, "tg_token")
        ck("input-e token type=password", field.get_attribute("type") == "password",
           field.get_attribute("type"))
        ck("input-e token khali ast (hich meghdari load nemishavad)",
           (field.get_attribute("value") or "") == "",
           repr(field.get_attribute("value")))
        ck("placeholder rahnama darad", bool(field.get_attribute("placeholder")),
           field.get_attribute("placeholder"))
        ck("copy gheyr-e momken (user-select:none)",
           driver.execute_script(
               "return window.getComputedStyle(document.getElementById('tg_token'))"
               ".webkitUserSelect || window.getComputedStyle("
               "document.getElementById('tg_token')).userSelect;") == "none")
        ck("text-security = disc (mahv-e basari)",
           driver.execute_script(
               "return window.getComputedStyle(document.getElementById('tg_token'))"
               ".webkitTextSecurity;") == "disc")

        badge = driver.execute_script(
            "var b=document.querySelector('.token-badge');return b?b.textContent:'';")
        ck("badge-e vaziat-e token neshan dade mishavad", "توکن" in badge, badge)

        ck("object-e TELEGRAM dar JS token nadarad",
           driver.execute_script("return !('bot_token' in TELEGRAM);"))
        ck("TELEGRAM.token_set = true", driver.execute_script("return TELEGRAM.token_set===true;"))

        # select-all + kopi: nabayad meghdari be dast biayad
        clipboard_like = driver.execute_script(
            "var e=document.getElementById('tg_token');e.focus();"
            "try{e.select();}catch(x){}"
            "return (window.getSelection?window.getSelection().toString():'')"
            " + (e.value||'');")
        ck("select/copy hich token-i nemidahad", real_token not in (clipboard_like or ""),
           repr(clipboard_like))

        # save bedun-e token nabayad token-e zakhire-shode ra pak konad
        driver.execute_script(
            "document.getElementById('tg_ids').value='565102812\\n981965565';"
            "document.getElementById('tg_token').value='';saveTelegram();")
        time.sleep(2.0)
        stored = json.load(open(os.path.join(ROOT, "telegram_config.json"), encoding="utf-8"))
        ck("save ba token-e khali token-ra hefz kard", stored["bot_token"] == real_token)
        ck("chat_ids zakhire shod", stored["chat_ids"] == ["565102812", "981965565"],
           stored["chat_ids"])
        ck("ba'd az save ham token dar safhe nist", real_token not in driver.page_source)
    finally:
        driver.quit()

    print(f"\n{sum(C)}/{len(C)} live checks passed")
    return 0 if all(C) else 1


if __name__ == "__main__":
    sys.exit(main())
