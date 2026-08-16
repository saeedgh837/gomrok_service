import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import captcha_collector as cc

URL = "https://my.ssaa.ir/portal/executive/inquery-exitban"
OUT = os.path.join(HERE, "exitban_probe.json")

cc.CHROMEDRIVER_PATH = os.path.join(HERE, ".tools", "chromedriver.exe")

driver = None
try:
    driver = cc.build_driver(cc.USER_AGENTS[0])
    driver.set_page_load_timeout(20)
    navigation_error = ""
    try:
        driver.get(URL)
    except Exception as exc:
        navigation_error = str(exc)
        try:
            driver.execute_script("window.stop()")
        except Exception:
            pass

    time.sleep(8)
    data = driver.execute_script(
        """
        const clean = (el) => ({
          tag: el.tagName.toLowerCase(),
          type: el.getAttribute('type') || '',
          id: el.id || '',
          name: el.getAttribute('name') || '',
          placeholder: el.getAttribute('placeholder') || '',
          ariaLabel: el.getAttribute('aria-label') || '',
          text: (el.innerText || el.textContent || '').trim().slice(0, 300),
          visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
          disabled: !!el.disabled
        });
        return {
          url: location.href,
          title: document.title,
          body: (document.body?.innerText || '').slice(0, 5000),
          inputs: [...document.querySelectorAll('input')].map(clean),
          selects: [...document.querySelectorAll('select')].map(clean),
          textareas: [...document.querySelectorAll('textarea')].map(clean),
          buttons: [...document.querySelectorAll('button,input[type=button],input[type=submit],a[role=button]')].map(clean)
        };
        """
    )
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        "navigation_error": navigation_error.splitlines()[0] if navigation_error else "",
        "url": data["url"],
        "title": data["title"],
        "input_count": len(data["inputs"]),
        "select_count": len(data["selects"]),
        "button_count": len(data["buttons"]),
        "body": data["body"][:1000]
    }, ensure_ascii=False))
finally:
    if driver is not None:
        driver.quit()
