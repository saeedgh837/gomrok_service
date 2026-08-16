"""live_test_hamta_recovery.py
Test-e LIVE-e barkimport: yek captcha-ye ghalat ejbari mizanad ta masir-e
recovery (dismiss swal -> az no baz kardan-e modal -> por kardan-e IMEI ->
captcha-ye taze) tayid shavad, sepas edame ta natije-ye vaghe'i.
Bedun-e telegram. Serial-e test: 350358333315545.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

import estelam_hamta as H                                          # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait            # noqa: E402

SERIAL = "350358333315545"


def main():
    solver = H.load_solver()
    d = H.build_driver()
    wait = WebDriverWait(d, H.MODAL_WAIT_SEC)
    log = []
    try:
        assert H.open_page_healthy(d, wait), "page not healthy"
        assert H.open_modal(d, wait), "modal not opened"
        H._set_input(d, H.SEL_M_IMEI, SERIAL)

        # 1) ejbar-e captcha-ye ghalat
        H._set_input(d, H.SEL_M_CAPTCHA_IN, "9999")
        cls, txt = H.submit_and_read(d, wait)
        log.append({"phase": "forced_wrong", "cls": cls, "txt": txt})
        assert cls in ("captcha_wrong", "captcha_expired"), f"expected wrong, got {cls}: {txt}"

        # 2) recovery: dismiss + az no baz kardan-e modal
        H.dismiss_swal(d)
        reopened = H.open_modal(d, wait)
        log.append({"phase": "recovery_reopen", "reopened": reopened})
        assert reopened, "modal did not reopen after error"
        H._set_input(d, H.SEL_M_IMEI, SERIAL)
        imei_ok = H._field_value(d, H.SEL_M_IMEI).strip() == SERIAL
        cap_present = bool(d.find_elements(
            "css selector", H.SEL_M_CAPTCHA_IMG))
        log.append({"phase": "recovery_state", "imei_refilled": imei_ok,
                    "captcha_present": cap_present})
        assert imei_ok and cap_present, "recovery state invalid"

        # 3) edame ta natije-ye vaghe'i (solver, bedun-e telegram)
        res = H.inquire(d, wait, solver, SERIAL, telegram=False)
        log.append({"phase": "final", "result": res})
        assert res["status"] in ("good", "blocked", "success", "unknown"), \
            f"unexpected final status: {res}"

        print(json.dumps({"ok": True, "log": log}, ensure_ascii=False, indent=2))
    except AssertionError as e:
        print(json.dumps({"ok": False, "error": str(e), "log": log},
                         ensure_ascii=False, indent=2))
        sys.exit(1)
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
