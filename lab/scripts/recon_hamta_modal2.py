"""recon_hamta_modal2.py
Recon-e DAGHIGH: selector-ha be MODAL (#device-inquiry) scope mishavand chon
safhe-ye login ham hamin ID-ha (#CaptchaInputText, img.captcha-image) ra darad.
Crop-e captcha ba ravesh-e screenshot-e kamel (na element.screenshot) gerefte mishavad.
HICH submit-i zade nemishavad.
"""
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

from PIL import Image                                             # noqa: E402
from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.common.by import By                       # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait           # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402

URL = "https://hamta.ntsw.ir/"
OUT = os.path.join(_ROOT, "lab", "final")
METRICS = {"width": 430, "height": 932, "pixelRatio": 3.0, "mobile": True}
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

M_CAPTCHA_IMG = "#device-inquiry img.captcha-image"
M_CAPTCHA_IN = "input.deviceInquiryCaptcha"
M_IMEI = "div.swal2-modal #ImeiNumber"
M_CONFIRM = "button.swal2-confirm"


def crop_with_padding(driver, element, pad_x=14, pad_y=8, offset_y=0):
    info = driver.execute_script(
        "const r=arguments[0].getBoundingClientRect();"
        "return {x:r.left,y:r.top,w:r.width,h:r.height,"
        " vw:window.innerWidth,vh:window.innerHeight};", element)
    img = Image.open(io.BytesIO(driver.get_screenshot_as_png()))
    sx = img.width / info["vw"]
    sy = img.height / info["vh"]
    box = (max(0, int((info["x"] - pad_x) * sx)),
           max(0, int((info["y"] - pad_y + offset_y) * sy)),
           min(img.width, int((info["x"] + info["w"] + pad_x) * sx)),
           min(img.height, int((info["y"] + info["h"] + pad_y + offset_y) * sy)))
    return img.crop(box), info, (sx, sy)


def main():
    os.makedirs(OUT, exist_ok=True)
    o = Options()
    o.add_argument("--disable-gpu")
    o.add_experimental_option("mobileEmulation",
                             {"deviceMetrics": METRICS, "userAgent": UA})
    o.add_argument(f"--window-size={METRICS['width']},{METRICS['height']}")
    d = webdriver.Chrome(service=ChromeService(executable_path=r"C:\chromedriver.exe"),
                         options=o)
    rep = {}
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(
            lambda x: x.execute_script("return document.readyState") == "complete")
        time.sleep(1.2)

        # ID-haye tekrari roo-ye safhe-ye login (ghabl az baz kardan-e modal)
        rep["dup_ids_before_modal"] = d.execute_script(
            "return {captchaInput: document.querySelectorAll('#CaptchaInputText').length,"
            " captchaImg: document.querySelectorAll('img.captcha-image').length,"
            " imei: document.querySelectorAll('#ImeiNumber').length};")

        for e in d.find_elements(By.CSS_SELECTOR, ".imeiInquiry"):
            if e.is_displayed():
                d.execute_script("arguments[0].click();", e)
                break
        WebDriverWait(d, 25).until(EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "div.swal2-modal")))
        WebDriverWait(d, 25).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, M_CAPTCHA_IMG)))
        time.sleep(1.2)

        rep["dup_ids_after_modal"] = d.execute_script(
            "return {captchaInput: document.querySelectorAll('#CaptchaInputText').length,"
            " captchaImg: document.querySelectorAll('img.captcha-image').length,"
            " imei: document.querySelectorAll('#ImeiNumber').length,"
            " scopedImg: document.querySelectorAll(arguments[0]).length,"
            " scopedInput: document.querySelectorAll(arguments[1]).length};",
            M_CAPTCHA_IMG, M_CAPTCHA_IN)

        rep["modal_captcha"] = d.execute_script(
            "var i=document.querySelector(arguments[0]);"
            "var r=i.getBoundingClientRect();"
            "return {src:i.getAttribute('src'),nw:i.naturalWidth,nh:i.naturalHeight,"
            "w:r.width,h:r.height,x:r.left,y:r.top,complete:i.complete};", M_CAPTCHA_IMG)
        rep["login_captcha"] = d.execute_script(
            "var all=document.querySelectorAll('img.captcha-image');"
            "var out=[];for(var i=0;i<all.length;i++){var r=all[i].getBoundingClientRect();"
            "out.push({src:all[i].getAttribute('src'),x:r.left,y:r.top,w:r.width,h:r.height,"
            "inModal:!!all[i].closest('div.swal2-modal')});}return out;")
        rep["viewport"] = d.execute_script(
            "return {vw:innerWidth, vh:innerHeight, dpr:devicePixelRatio,"
            " sw:document.documentElement.scrollWidth};")

        img_el = d.find_element(By.CSS_SELECTOR, M_CAPTCHA_IMG)
        d.execute_script("arguments[0].scrollIntoView({block:'center'});", img_el)
        time.sleep(0.6)

        # A) element.screenshot (ravesh-e proje-ye beta)
        p_el = os.path.join(OUT, "hamta_cap_element.png")
        img_el.screenshot(p_el)
        rep["shot_element"] = {"path": p_el, "size": os.path.getsize(p_el)}

        # B) crop az screenshot-e kamel (ravesh-e captcha_collector)
        crop, info, scale = crop_with_padding(d, img_el)
        p_crop = os.path.join(OUT, "hamta_cap_crop.png")
        crop.save(p_crop)
        rep["shot_crop"] = {"path": p_crop, "size": os.path.getsize(p_crop),
                            "wh": list(crop.size), "rect": info, "scale": list(scale)}

        with open(os.path.join(OUT, "hamta_recon2.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
