"""Live check: serial-e aval/dovom zir-e ham dar YEK sotoon + zoom-e NORMAL-e panel.

(zoom-e 75% faghat roo-ye SITE-e gomrok e'mal mishavad — check-ash dar
lab/scripts/live_check_site_zoom.py ast.)

Sharayet: panel roo-ye http://127.0.0.1:8750 bala bashad + hadd-e aghal yek device
ba serial-e dovom dar estelam_store.json.

Ejra:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/scripts/live_check_serial_zoom.py
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


def _seed():
    import panel
    data = panel.estelam_load()
    has_two = any(str(d.get("serial2") or "").strip() for d in data.get("devices", []))
    if not has_two:
        panel.estelam_add_device({"serial1": "356938035643809", "serial2": "356938035643817"})


def main():
    _seed()
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=options)
    try:
        # ---------- estelam page ----------
        driver.get("http://127.0.0.1:8750/site-2")
        time.sleep(1.6)

        zoom = driver.execute_script(
            "return window.getComputedStyle(document.documentElement).zoom;")
        ck("estelam: zoom-e panel NORMAL ast", str(zoom) in ("1", "1.0", "normal", ""), zoom)

        headers = driver.execute_script(
            "return Array.from(document.querySelectorAll('#dev-list th'))"
            ".map(function(t){return t.textContent.trim();});")
        ck("estelam: sotoon-e serial yeki shod", "سریال‌ها" in headers, headers)
        ck("estelam: 'سریال دوم' sotoon-e jodagane nadarad",
           "سریال دوم" not in headers, headers)
        ck("estelam: tedad-e sotoon 4 shod (az 5)", len(headers) == 4, len(headers))

        stacked = driver.execute_script(
            "var r=document.querySelector('#dev-list tbody tr');"
            "if(!r) return null;"
            "var c=r.querySelector('td');"
            "var a=c.querySelector('.sn1'), b=c.querySelector('.sn2');"
            "if(!a||!b) return {two:false};"
            "var ra=a.getBoundingClientRect(), rb=b.getBoundingClientRect();"
            "return {two:true, sameCell:true, below: rb.top >= ra.bottom - 1,"
            " sameLeft: Math.abs(ra.left-rb.left) < 2, s1:a.textContent, s2:b.textContent};")
        ck("estelam: har do serial dar YEK cell hastand",
           bool(stacked) and stacked.get("two") is True, stacked)
        if stacked and stacked.get("two"):
            ck("estelam: serial-e dovom ZIR-e serial-e aval", stacked["below"] is True, stacked)
            ck("estelam: har do dar yek sotoon (ham-tarazi-ye chap)",
               stacked["sameLeft"] is True, stacked)

        widths = driver.execute_script(
            "var t=document.querySelector('#dev-list table');"
            "return t? t.getBoundingClientRect().width : 0;")
        ck("estelam: jadval hanooz render mishavad", widths > 0, widths)
        ck("estelam: safhe overflow-e ofoghi nadarad",
           driver.execute_script(
               "return document.documentElement.scrollWidth <= "
               "document.documentElement.clientWidth + 2;"),
           driver.execute_script("return [document.documentElement.scrollWidth,"
                                 "document.documentElement.clientWidth];"))

        # ---------- site-1 ----------
        driver.get("http://127.0.0.1:8750/site-1")
        time.sleep(1.6)
        zoom1 = driver.execute_script(
            "return window.getComputedStyle(document.documentElement).zoom;")
        ck("site-1: zoom-e panel NORMAL ast", str(zoom1) in ("1", "1.0", "normal", ""), zoom1)
        ck("site-1: safhe overflow-e ofoghi nadarad",
           driver.execute_script(
               "return document.documentElement.scrollWidth <= "
               "document.documentElement.clientWidth + 2;"),
           driver.execute_script("return [document.documentElement.scrollWidth,"
                                 "document.documentElement.clientWidth];"))
        ck("site-1: dokme-haye icon-i hanooz clickable hastand",
           driver.execute_script(
               "var b=document.querySelector('.ibtn');"
               "if(!b) return false; var r=b.getBoundingClientRect();"
               "return r.width>0 && r.height>0;"))
    finally:
        driver.quit()

    print(f"\n{sum(C)}/{len(C)} live checks passed")
    return 0 if all(C) else 1


if __name__ == "__main__":
    sys.exit(main())
