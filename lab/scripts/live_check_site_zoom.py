"""Live check: zoom-e 75% roo-ye SITE-e gomrok (robot) va zoom-e NORMAL dar panel.

- Site-e irica: `cc.install_site_zoom` bayad html{zoom:0.75} bezanad va roo-ye
  reload/navigation-e ba'di ham baghi bemanad; aks-e captcha bayad ba zoom-e
  ma'koos andaze-ye vaghe'i-ash ra hefz konad (crop/OCR kharab nashavad).
- Panel-e khodemoon (site-1 / site-2): zoom bayad NORMAL (1) bashad.

Ejra (panel bayad bala bashad):
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/scripts/live_check_site_zoom.py
"""
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

CHROMEDRIVER = r"C:\chromedriver.exe"
C = []


def ck(name, cond, extra=""):
    C.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:130]) if extra else ""))


def _driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)


def check_site_like_page(driver):
    """Yek safhe-ye mahalli mesl-e site-e gomrok (hamon class-e captcha) ta zoom
    bedun-e vabastegi be shabake-ye irica sanjide shavad."""
    import captcha_collector as cc

    cc.install_site_zoom(driver)

    html = (
        "data:text/html;charset=utf-8,"
        "<html><head><title>fake-irica</title></head><body>"
        "<img class='captchaImage' width='200' height='60' "
        "src='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'>"
        "<button id='b'>edame</button></body></html>"
    )
    driver.get(html)
    time.sleep(0.6)

    zoom = driver.execute_script(
        "return window.getComputedStyle(document.documentElement).zoom;")
    ck("site: html zoom = 0.75", str(zoom).startswith("0.75"), zoom)
    ck("site: style-e tazrigh-shode hozur darad",
       driver.execute_script("return !!document.getElementById('irica-site-zoom');"))
    cap_zoom = driver.execute_script(
        "return window.getComputedStyle(document.querySelector('img.captchaImage')).zoom;")
    ck("site: captcha zoom-e ma'koos darad (~1.333)",
       abs(float(cap_zoom) - (1 / 0.75)) < 0.02, cap_zoom)

    # captcha bayad andaze-ye VAGHE'I-ash ra hefz kone: 0.75 * 1.3333 = 1
    rect = driver.execute_script(
        "var r=document.querySelector('img.captchaImage').getBoundingClientRect();"
        "return [r.width, r.height];")
    ck("site: andaze-ye render-shode-ye captcha dast-nakhorde (200x60)",
       abs(rect[0] - 200) < 2 and abs(rect[1] - 60) < 2, rect)

    # dokme bayad kuchak-tar shode bashad (ya'ni zoom vaghe'an e'mal shode)
    btn_w = driver.execute_script(
        "return document.getElementById('b').getBoundingClientRect().width;")
    css_w = driver.execute_script(
        "return parseFloat(getComputedStyle(document.getElementById('b')).width);")
    ck("site: dokme 25% kuchak-tar render shod", btn_w < css_w - 1,
       f"rendered={round(btn_w, 1)} css={css_w}")

    # payda mandan ba'd az navigation-e jadid (CDP)
    driver.get(html + "<!--2-->")
    time.sleep(0.6)
    zoom2 = driver.execute_script(
        "return window.getComputedStyle(document.documentElement).zoom;")
    ck("site: zoom ba'd az navigation-e jadid ham baghi mand",
       str(zoom2).startswith("0.75"), zoom2)

    driver.refresh()
    time.sleep(0.6)
    zoom3 = driver.execute_script(
        "return window.getComputedStyle(document.documentElement).zoom;")
    ck("site: zoom ba'd az F5 ham baghi mand", str(zoom3).startswith("0.75"), zoom3)


def check_panel_normal(driver):
    for route, label in (("/site-1", "panel-e gomrok-e asli"), ("/site-2", "panel-e estelam")):
        driver.get("http://127.0.0.1:8750" + route)
        time.sleep(1.4)
        zoom = driver.execute_script(
            "return window.getComputedStyle(document.documentElement).zoom;")
        normal = str(zoom) in ("1", "1.0", "normal", "")
        ck(f"{label}: zoom NORMAL ast (bi-zoom)", normal, zoom)
        ck(f"{label}: overflow-e ofoghi nadarad",
           driver.execute_script(
               "return document.documentElement.scrollWidth <= "
               "document.documentElement.clientWidth + 2;"))


def main():
    # PITFALL: install_site_zoom az CDP addScriptToEvaluateOnNewDocument estefade
    # mikonad, pas roo-ye HAR safhe-ye HAMON driver e'mal mishavad. baraye check-e
    # panel bayad driver-e TAZE (bedun-e tazrigh) sakhte shavad.
    driver = _driver()
    try:
        check_site_like_page(driver)
    finally:
        driver.quit()

    clean = _driver()
    try:
        check_panel_normal(clean)
    finally:
        clean.quit()

    print(f"\n{sum(C)}/{len(C)} live checks passed")
    return 0 if all(C) else 1


if __name__ == "__main__":
    sys.exit(main())
