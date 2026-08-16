"""recon_hamta_modal.py
Shenasayi-ye READ-ONLY-e safhe-ye hamta.ntsw.ir va modal-e "estelam-e asalat-e dastgah".
HICH submit-i nemizanad — faghat modal ra baz mikonad, DOM va aks-e captcha ra dump mikonad.

Ejra:
    python lab/scripts/recon_hamta_modal.py
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.common.by import By                       # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait           # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402

URL = "https://hamta.ntsw.ir/"
OUT_DIR = os.path.join(_ROOT, "lab", "final")
MOBILE_METRICS = {"width": 430, "height": 932, "pixelRatio": 3.0, "mobile": True}
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def build():
    o = Options()
    o.add_argument("--disable-gpu")
    o.add_experimental_option("mobileEmulation",
                              {"deviceMetrics": MOBILE_METRICS, "userAgent": UA})
    o.add_argument(f"--window-size={MOBILE_METRICS['width']},{MOBILE_METRICS['height']}")
    return webdriver.Chrome(service=ChromeService(executable_path=r"C:\chromedriver.exe"),
                            options=o)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    d = build()
    rep = {}
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.5)
        rep["url"] = d.current_url
        rep["title"] = d.title
        rep["has_inquiry_link"] = bool(d.find_elements(By.CSS_SELECTOR, ".imeiInquiry"))
        rep["inquiry_links"] = [
            {"text": (e.text or "").strip(), "cls": e.get_attribute("class"),
             "disp": e.is_displayed()}
            for e in d.find_elements(By.CSS_SELECTOR, ".imeiInquiry")]
        with open(os.path.join(OUT_DIR, "hamta_home.html"), "w", encoding="utf-8") as f:
            f.write(d.page_source)

        # baz kardan-e modal (faghat click, bedun-e submit)
        opened = False
        for e in d.find_elements(By.CSS_SELECTOR, ".imeiInquiry"):
            if e.is_displayed():
                d.execute_script("arguments[0].click();", e)
                opened = True
                break
        if not opened:
            d.execute_script("if(typeof $!=='undefined'){$('.imeiInquiry').trigger('click');}")
        try:
            WebDriverWait(d, 25).until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "div.swal2-modal")))
            rep["modal_opened"] = True
        except Exception as exc:
            rep["modal_opened"] = False
            rep["modal_error"] = str(exc)[:200]
        time.sleep(1.5)

        rep["modal_title"] = d.execute_script(
            "var t=document.querySelector('#swal2-title,.swal2-title');"
            "return t?t.textContent.trim():'';")
        rep["confirm_btn"] = d.execute_script(
            "var b=document.querySelector('button.swal2-confirm');"
            "return b?{text:b.textContent.trim(),cls:b.className,"
            "vis:!!(b.offsetWidth||b.offsetHeight)}:null;")
        rep["imei_input"] = d.execute_script(
            "var i=document.querySelector('#ImeiNumber');"
            "return i?{ph:i.placeholder,maxlen:i.maxLength,cls:i.className,"
            "pattern:i.getAttribute('data-val-regex-pattern')}:null;")
        rep["captcha_input"] = d.execute_script(
            "var i=document.querySelector('#CaptchaInputText');"
            "return i?{ph:i.placeholder,cls:i.className}:null;")
        rep["captcha_img"] = d.execute_script(
            "var i=document.querySelector('img.captcha-image');"
            "return i?{src:i.getAttribute('src'),nw:i.naturalWidth,nh:i.naturalHeight,"
            "w:i.getBoundingClientRect().width,h:i.getBoundingClientRect().height,"
            "complete:i.complete}:null;")
        rep["refresh_link"] = d.execute_script(
            "return !!document.querySelector('a.refresh-captcha');")
        rep["loader"] = d.execute_script(
            "var l=document.querySelector('#ajloader');"
            "return l?getComputedStyle(l).display:null;")

        html = d.execute_script(
            "var m=document.querySelector('div.swal2-modal');return m?m.outerHTML:'';")
        with open(os.path.join(OUT_DIR, "hamta_modal.html"), "w", encoding="utf-8") as f:
            f.write(html)

        # aks-e captcha (baraye test-e offline-e solver)
        try:
            img = d.find_element(By.CSS_SELECTOR, "img.captcha-image")
            shot = os.path.join(OUT_DIR, "hamta_captcha_recon.png")
            img.screenshot(shot)
            rep["captcha_shot"] = shot
            rep["captcha_shot_size"] = os.path.getsize(shot)
        except Exception as exc:
            rep["captcha_shot_error"] = str(exc)[:200]

        with open(os.path.join(OUT_DIR, "hamta_recon.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
