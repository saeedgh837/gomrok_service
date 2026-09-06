"""Controlled exit-ban inquiry against the official SSAA form.

Identity data is loaded only through panel.load_store(). Submission is opt-in.
"""
import argparse
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://my.ssaa.ir/portal/executive/inquery-exitban"
DRIVER_PATH = os.path.join(HERE, ".tools", "chromedriver.exe")

import panel

IDENTITY_FIELDS = (
    "first_name",
    "last_name",
    "father_name",
    "birth",
    "birth_certificate_no",
    "national",
)
FIELD_MAP = {
    "first_name": "DebtorName",
    "last_name": "DebtorFamily",
    "father_name": "DebtorNameFather",
    "birth": "DebtorCreateDate",
    "birth_certificate_no": "DebtorIdentityNo",
    "national": "NationlityCode",
}
RESULT_TIMEOUT = 120
CLEAR_RESULT_TEXT = "شخص با کد ملی ثبت‌شده ممنوع‌الخروج نمی‌باشد."
BLOCKED_RESULT_TEXT = "شخص با کد ملی ثبت‌شده ممنوع‌الخروج می‌باشد."

class IncompleteIdentityError(ValueError):
    def __init__(self, fields):
        self.fields = list(fields)
        super().__init__("فیلدهای هویتی ناقص هستند: " + ", ".join(self.fields))


def load_identity(profile_id=None):
    store = panel.load_store()
    target_id = profile_id or store.get("active_id")
    profile = next(
        (item for item in store.get("profiles", []) if item.get("id") == target_id),
        None,
    )
    if profile is None:
        raise ValueError("پروفایل موردنظر پیدا نشد")
    return {
        key: str(profile.get(key, "") or "").strip()
        for key in IDENTITY_FIELDS
    }


def missing_identity_fields(identity):
    return [key for key in IDENTITY_FIELDS if not str(identity.get(key, "") or "").strip()]


def ensure_complete(identity):
    missing = missing_identity_fields(identity)
    if missing:
        raise IncompleteIdentityError(missing)


def _build_driver():
    import captcha_collector as cc

    # Shared resolution: the exitban-local .tools copy is only preferred when
    # it actually exists; otherwise the resolver picks the C:\ override or the
    # bundled copy, and Selenium Manager as the last resort (no hard failure).
    if os.path.isfile(DRIVER_PATH):
        cc.CHROMEDRIVER_PATH = DRIVER_PATH
    else:
        cc.CHROMEDRIVER_PATH = cc.resolve_chromedriver()
    cc.check_driver_compat(cc.CHROMEDRIVER_PATH or DRIVER_PATH)
    return cc.build_driver(cc.USER_AGENTS[0])


def fill_and_verify(driver, identity, wait_factory=None):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    wait = (wait_factory or WebDriverWait)(driver, 20)
    for key, site_name in FIELD_MAP.items():
        element = wait.until(EC.visibility_of_element_located((By.NAME, site_name))) if wait_factory is None else wait.find_element(By.NAME, site_name)
        if not element.is_enabled():
            raise RuntimeError(f"فیلد {key} غیرفعال است")
        element.clear()
        element.send_keys(identity[key])
        actual = (element.get_attribute("value") or "").strip()
        if actual != identity[key]:
            raise RuntimeError(f"اعتبارسنجی فیلد {key} ناموفق بود")


def _fill_and_verify(driver, identity):
    fill_and_verify(driver, identity)


def report_result(profile_id, text, status=""):
    return panel.record_exitban_result(profile_id, text, status)


def parse_result_text(text):
    normalized = " ".join(str(text or "").replace("‌", " ").split())
    compact = normalized.replace(" ", "")
    if "ممنوعالخروجنمیباشد" in compact:
        return CLEAR_RESULT_TEXT, "clear"
    if "ممنوعالخروجمیباشد" in compact:
        return BLOCKED_RESULT_TEXT, "blocked"
    return "", ""


def wait_for_result(driver, timeout=RESULT_TIMEOUT):
    from selenium.webdriver.common.by import By

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dialogs = driver.find_elements(
            By.CSS_SELECTOR, "[role='dialog'],.modal.show,.swal2-popup,.v-dialog"
        )
        for element in dialogs:
            if element.is_displayed():
                result = parse_result_text(element.text)
                if result[1]:
                    return result
        result = parse_result_text(driver.find_element(By.TAG_NAME, "body").text)
        if result[1]:
            return result
        time.sleep(0.5)
    raise TimeoutError("نتیجه استعلام در زمان تعیین‌شده دریافت نشد")


def run(profile_id=None, submit=False, keep_open=False):
    identity = load_identity(profile_id)
    ensure_complete(identity)
    driver = _build_driver()
    try:
        driver.set_page_load_timeout(20)
        driver.get(URL)
        _fill_and_verify(driver, identity)
        if submit:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
            )
            button.click()
            result_text, status = wait_for_result(driver)
            target_id = profile_id or panel.load_store().get("active_id")
            if not report_result(target_id, result_text, status):
                raise RuntimeError("ذخیره نتیجه استعلام در پنل ناموفق بود")
            return {"profile_id": target_id, "submitted": True,
                    "result": result_text, "status": status}
        return {"profile_id": profile_id or panel.load_store().get("active_id"),
                "submitted": submit}
    except Exception as exc:
        try:
            panel.record_exitban_error(profile_id, f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        if not keep_open:
            driver.quit()
        raise
    finally:
        if not keep_open and 'driver' in locals():
            try:
                driver.quit()
            except Exception:
                pass


def run_dry(profile_id=None):
    return run(profile_id=profile_id, submit=False, keep_open=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.profile_id, submit=args.submit)
    except IncompleteIdentityError as exc:
        print("اطلاعات هویتی ناقص است: " + ", ".join(exc.fields))
        raise SystemExit(2)
    except ValueError as exc:
        print(str(exc))
        raise SystemExit(2)
    print(f"صفحه برای پروفایل {result['profile_id']} آماده شد؛ ارسال: {'بله' if result['submitted'] else 'خیر'}")


if __name__ == "__main__":
    main()
