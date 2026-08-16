"""probe_hamta_wrong_captcha.py
Probe: ba captcha-ye AMDAN GHALAT submit mikonad ta motn-e VAGHE'I-ye khata-ye
captcha-ye site (va sarnevesht-e modal ba'd az khata) sabt shavad.
Side-effect: hich — form-e omoomi-ye estelam ast, na login, na SMS, na account.
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

import estelam_hamta as H                                          # noqa: E402
from selenium.webdriver.common.by import By                        # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait            # noqa: E402

SERIAL = "350358333315545"
OUT = os.path.join(_ROOT, "lab", "final", "hamta_wrong_captcha_probe.json")


def main():
    d = H.build_driver()
    wait = WebDriverWait(d, H.MODAL_WAIT_SEC)
    rep = {}
    try:
        rep["page_healthy"] = H.open_page_healthy(d, wait)
        rep["modal_opened"] = H.open_modal(d, wait)
        H._set_input(d, H.SEL_M_IMEI, SERIAL)
        H._set_input(d, H.SEL_M_CAPTCHA_IN, "1111")   # amdan ghalat
        rep["imei_val"] = H._field_value(d, H.SEL_M_IMEI)
        rep["captcha_val"] = H._field_value(d, H.SEL_M_CAPTCHA_IN)
        cls, txt = H.submit_and_read(d, wait)
        rep["cls"] = cls
        rep["text"] = txt
        time.sleep(1.0)
        rep["after"] = d.execute_script("""
            return {
              modalCount: document.querySelectorAll('div.swal2-modal').length,
              formCaptchaPresent: !!document.querySelector('#device-inquiry img.captcha-image'),
              imeiPresent: !!document.querySelector('div.swal2-modal #ImeiNumber'),
              confirmText: (function(){var b=document.querySelector('button.swal2-confirm');
                                       return b?b.textContent.trim():null;})(),
              contentText: (function(){var c=document.querySelector('#swal2-content');
                                       return c?(c.innerText||'').trim().slice(0,300):null;})()
            };
        """)
        # ba'd az bastan-e swal-e khata, aya modal-e form bar migardad?
        try:
            b = d.find_element(By.CSS_SELECTOR, "button.swal2-confirm")
            d.execute_script("arguments[0].click();", b)
            time.sleep(1.5)
        except Exception as e:
            rep["dismiss_err"] = str(e)[:150]
        rep["after_dismiss"] = d.execute_script("""
            return {
              modalCount: document.querySelectorAll('div.swal2-modal').length,
              formCaptchaPresent: !!document.querySelector('#device-inquiry img.captcha-image'),
              imeiVal: (function(){var i=document.querySelector('div.swal2-modal #ImeiNumber');
                                   return i?i.value:null;})(),
              captchaVal: (function(){var i=document.querySelector('input.deviceInquiryCaptcha');
                                      return i?i.value:null;})()
            };
        """)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
