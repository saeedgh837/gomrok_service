"""estelam_hamta.py
Servis-e ESTELAM-e ASALAT-e dastgah roo-ye samane-ye HAMTA (https://hamta.ntsw.ir/).

Jarian (bedun-e login — form-e estelam roo-ye safhe-ye vorood-e omoomi ast):
  1) baz kardan-e safhe va control-e salamat-e load (dokme-ye "استعلام اصالت")
     - agar dokme nabood -> reload ta zaher shavad (site-e naghes/kond)
  2) click roo-ye dokme -> baz shodan-e modal-e SweetAlert "استعلام اصالت دستگاه"
  3) montazer mimanim ta dokme-ye "جستجو" + aks-e captcha + input-e IMEI kamel load shavand
  4) serial-e aval-e dastgah ra dar #ImeiNumber vared mikonim
  5) captcha-ye modal ra ba solver (Hamta-solver/) hall va dar input-e captcha mizanim
  6) verify-e meghdar-ha, sepas click-e "جستجو"
  7) natije ra mikhanim:
     - PAYAM-e KHUB (mesl: "شناسه دستگاه (IMEI) قانونی نیست و ... سرویس نخواهد گرفت")
       => salem / clear
     - KHATA-ye MASDOODI (motn-ash daghigh maloom nist) => KHATAR: log-e barjaste
     - baghi => unknown, log

NOKTE-ye selector: safhe-ye login KHODESH ham #CaptchaInputText va img.captcha-image
darad, pas HAME-ye selector-ha be MODAL scope mishavand (#device-inquiry / .swal2-modal).

Estefade:
  python estelam_hamta.py --serial 350358333315545        # yek estelam-e test
  python estelam_hamta.py --serial ... --no-telegram       # bedun-e telegram
  python estelam_hamta.py --serial ... --keep-open         # morurgar baz bemanad (debug)
  python estelam_hamta.py --from-store                     # hame-ye device-haye pending-e panel
  python estelam_hamta.py --from-store --device-id X       # faghat yek device-e panel
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import system_debug

_HERE = os.path.dirname(os.path.abspath(__file__))
_SOLVER_DIR = os.path.join(_HERE, "Hamta-solver")
# NOTE: Hamta-solver is intentionally NOT added to sys.path at import time.
# It shares module names (live_solve, onnx_solve, config, vocab, preprocess,
# utils) with the gomrok solver/ package; a permanent path entry here would let
# a later bare `from onnx_solve import ...` in the gomrok solver resolve to the
# WRONG package. load_solver() below inserts and removes the path in a scoped,
# self-contained way instead.

from PIL import Image                                             # noqa: E402
from selenium import webdriver                                    # noqa: E402
from selenium.webdriver.chrome.options import Options             # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.edge.options import Options as EdgeOptions  # noqa: E402
from selenium.webdriver.common.by import By                       # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait           # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402
from selenium.common.exceptions import (                          # noqa: E402
    TimeoutException, WebDriverException, StaleElementReferenceException)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------------------
URL = "https://hamta.ntsw.ir/"
CHROMEDRIVER_PATH = r"C:\chromedriver.exe"
USE_EDGE = False
# height=0 ya'ni "in mehvar ra override nakon": viewport hamandaze-ye nahiye-ye
# ghabel-e namayesh-e panjere mishavad. ba 932 (ghad-e vaghe'i-ye iPhone) 151 pixel-e
# paeen-e safhe birun-e kadr mimand va safhe-haye kootah HICH scroll-i nadarand.
# tozih-e kamel + andaze-giri: captcha_collector.MOBILE_METRICS va
# lab/tests/test_manual_scroll.py
MOBILE_METRICS = {"width": 430, "height": 0, "pixelRatio": 3.0, "mobile": True}
# Andaze-ye VAGHE'I-ye panjere; height-e emulation (0) NABAYAD inja beyad.
WINDOW_SIZE = (430, 932)
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def window_size_arg():
    """`--window-size=W,H` ba adad-haye vaghe'i (na height-e 0-e emulation)."""
    return f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}"


def install_manual_scroll(driver):
    """Scroll-e dasti ba DRAG-e mouse (mesl-e angosht rooye gooshi).

    Emulation-e mobile scrollbar-e overlay midahad ke ba mouse ghabel-e gereftan
    nist, pas baraye baznagari-ye dasti drag lazem ast. best-effort: agar CDP
    dar dastres nabashad robot bi-ekhtelal edame midahad.
    """
    try:
        driver.execute_cdp_cmd("Emulation.setEmitTouchEventsForMouse",
                               {"enabled": True, "configuration": "mobile"})
    except Exception as exc:
        print(f">>> (faal-kardan-e scroll-e dasti na-shod: {exc})")
    return driver


# --- selector-haye MODAL (hame scope-shode be modal ta ba safhe-ye login ghати nashavad)
SEL_INQUIRY_BTN = ".imeiInquiry"
SEL_MODAL = "div.swal2-modal"
SEL_M_IMEI = "div.swal2-modal #ImeiNumber"
SEL_M_CAPTCHA_IMG = "#device-inquiry img.captcha-image"
SEL_M_CAPTCHA_IN = "input.deviceInquiryCaptcha"
SEL_M_REFRESH = "#device-inquiry a.refresh-captcha"
SEL_M_CONFIRM = "button.swal2-confirm"
SEL_LOADER = "#ajloader"

PAGE_LOAD_SEC = 30
MODAL_WAIT_SEC = 25
RESULT_TIMEOUT_SEC = 40
MAX_CAPTCHA_TRIES = 12
MAX_PAGE_RELOAD = 4
REFRESH_DELAY = 1.5
# Refresh-e captcha ARZAN ast (faghat load-e aks), vali submit-e ghalat GERAN ast
# chon site swal-e khata ra jaygozin-e modal mikonad va bayad koll-e modal az no
# baz shavad. Pas hads-haye bi-etminan bedun-e submit refresh mishavand.
# Kalibrasion: dar live, conf=0.593 dorost bood -> astane-ye mohafezekaran-e pain.
MIN_CONFIDENCE_TO_SUBMIT = 0.45

# --- classification-e natije ------------------------------------------------
# PAYAM-e KHUB (dastgah asil/clear — az sharh-e karbar):
GOOD_MARKERS = [
    "قانونی نیست",
    "سرویس نخواهد گرفت",
    "سرويس نخواهد گرفت",
]
# DASTGAH-e SABT-shode/qanooni (natije-ye VAGHE'I-ye live): «جزء شناسه های قانونی
# ثبت شده است» + swal-e «تکمیل فرایند». IN masdoodi NIST — dastgah salem/sabt-shode.
# NOKTE-ye MOHEM: matn shamel-e hoshdar-e «... در صورت احراز تخلف مسدود خواهد شد»
# ast ke NABAYAD blocked telaghi shavad (sharti/ayande ast).
REGISTERED_MARKERS = [
    "جزء شناسه های قانونی",
    "جزء شناسه‌های قانونی",
    "قانونی ثبت شده",
    "تکمیل فرایند",
]
# MASDOODI-ye GHAT'I (motn-e VAGHE'I-ye samane, az karbar):
#   «ثبت این دستگاه به دلیل تخلف در فرآیندهای سامانه لغو شده است.
#     از خرید این دستگاه خودداری نمایید.»
# IN bala-tarin OLAVIAT ra darad — ghabl az registered/good check mishavad, chon
# momken ast dar hamun swal moshakhasat-e dastgah ham biyad.
CANCELLED_MARKERS = [
    "لغو شده است",
    "از خرید این دستگاه خودداری",
    "تخلف در فرآیندهای سامانه",
    "تخلف در فرایندهای سامانه",
]
# KHATA-ye MASDOODI (KHATAR) — masdoodi-ye VAGHE'I (na hoshdar-e sharti-ye «خواهد شد»).
BLOCKED_MARKERS = [
    "مسدود شده",
    "مسدود است",
    "مسدود می باشد",
    "مسدود می‌باشد",
    "مسدود گردیده",
    "مسدودی",
    "غیرمجاز",
    "غیر مجاز",
    "سرقتی",
    "قاچاق",
]
# hoshdar-e sharti ke NABAYAD blocked shavad (dar natije-ye sabt-shode miayad):
_BLOCKED_FALSE_POSITIVES = [
    "مسدود خواهد شد",
    "مسدود می شود",
    "مسدود می‌شود",
]
# captcha-ye ghalat (site pas mizanad, bayad captcha-ye taze) — bar asas-e beta project:
CAPTCHA_WRONG_MARKERS = [
    "کد امنیتی",
    "عبارت امنیتی",
    "کد امنيتي",
]
CAPTCHA_EXPIRED_MARKERS = [
    "منقضی",
]
# khata-ye gozara-ye ertebat:
TRANSIENT_MARKERS = [
    "برقراری ارتباط مقدور",
    "مقدور نمی باشد",
    "مقدور نمی‌باشد",
]

_LOG_PATH = os.path.join(_HERE, "hamta_logs.jsonl")
_STORE_PATH = os.environ.get("IRICA_ESTELAM_STORE") or os.path.join(_HERE, "estelam_store.json")

# naghshe-ye status-e hamta -> status-e log-e panel (rang/icon-e moshtarak ba gomrok)
_PANEL_LOG_STATUS = {
    "good": "healthy",        # sabz — dastgah asil/clear
    "registered": "healthy",  # sabz — shenase joz'-e shenase-haye qanooni-ye sabt-shode
    "blocked": "problem",     # ghermez — KHATAR-e masdoodi
    "success": "healthy",
    "unknown": "warn",
    "error": "error",
    "running": "running",
}


# --- panel bridge (hamun estelam_store.json-e servis-e gomrok) --------------
def _store_read():
    """estelam_store.json-e panel ra mikhanad (hamun manba'-e servis-e gomrok)."""
    if not os.path.exists(_STORE_PATH):
        return {"person": {}, "devices": []}
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            return json.load(f) or {"person": {}, "devices": []}
    except Exception as e:
        print(f">>> HOSHDAR: estelam_store.json khonde nashod: {e}")
        return {"person": {}, "devices": []}


def _panel_set_device(device_id, **fields):
    """Natije-ye HAMTA ra roo-ye device-e panel minevisad. NOKTE: field-haye
    hamta joda az field-haye gomrok hastand (hamta_status/hamta_result) ta do
    servis roo-ye ham nanevisand."""
    try:
        sys.path.insert(0, _HERE)
        import panel
        return panel.estelam_set_device(device_id, **fields)
    except Exception as e:
        print(f">>> (update-e device dar panel na-shod: {e})")
        return None


# --- solver -----------------------------------------------------------------
# Har do package-e solver (solver/ va Hamta-solver/) file-haye HAM-NAM daran:
# live_solve, config, vocab, preprocess, utils. Dar tolid har robot process-e
# joda darad pas tasadom peesh nemiayad, vali agar ye process har do ra import
# konad (masalan smoke test ya yek runner-e yekja), `from live_solve import ...`
# mitavanad solver-e GOMROK ra bardarad — ba vocab va andaze-ye tasvir-e ghalat
# va BEDUN-e khata. Pas inja sarih load mikonim va provenance ra check mikonim.
_HAMTA_SOLVER_MODULES = ("live_solve", "onnx_solve", "config", "vocab",
                         "preprocess", "utils", "number_decode", "model")


def load_solver():
    import importlib

    saved_path = list(sys.path)
    saved_mods = {name: sys.modules.pop(name)
                  for name in _HAMTA_SOLVER_MODULES if name in sys.modules}
    try:
        sys.path.insert(0, _SOLVER_DIR)
        live = importlib.import_module("live_solve")
        origin = os.path.dirname(os.path.abspath(live.__file__))
        if os.path.normcase(origin) != os.path.normcase(_SOLVER_DIR):
            raise ImportError(
                f"hamta solver resolved to the wrong package: {live.__file__}")
        return live.load_solver(device="cpu")
    finally:
        # Nagahdashtan-e module-haye hamta dar sys.modules ta import-haye ba'di
        # (masalan solver-e gomrok) hamin name-ha ra eshtebahi nagirand.
        for name in _HAMTA_SOLVER_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved_mods)
        sys.path[:] = saved_path


# --- log / telegram ---------------------------------------------------------
def _elog(status, message, serial="", extra=None):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "status": status,
           "serial": serial, "message": message}
    if extra:
        rec.update(extra)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f">>> (log na-shod: {e})")
    print(f">>> [{status}] {message}")
    # ham-zaman dar log-e panel (ta bakhsh-e log-e safhe-ye estelam in-ha ra
    # ham neshan bedahad). step = "همتا" ta az log-haye gomrok ghabel-e
    # tafkik bashad.
    try:
        sys.path.insert(0, _HERE)
        import panel
        panel.estelam_log(_PANEL_LOG_STATUS.get(status, status), message,
                          step="همتا", serial=serial,
                          device_id=(extra or {}).get("device_id", ""))
    except Exception:
        pass


def _telegram(text, enabled=True):
    if not enabled:
        return
    # timeout-e sakht: estelam nabayad rooye ersal-e telegram gir konad.
    try:
        sys.path.insert(0, _HERE)
        import telegram_notify as tg
        tg.send_message(text, timeout=15)
    except Exception as e:
        print(f">>> (telegram na-raft: {e})")


_STATUS_FA = {
    "good": "✅ مجاز — دستگاه قانونی نیست و در شبکه سرویس نمی‌گیرد (قابل ثبت)",
    "registered": "✅ ثبت‌شده — شناسه جزء شناسه‌های قانونی ثبت‌شده است",
    "blocked": "⛔️ خطر — ثبت دستگاه لغو/مسدود شده است (از خرید خودداری کنید)",
    "success": "✅ استعلام موفق (تکمیل فرایند)",
    "unknown": "❓ نتیجه نامشخص",
    "error": "⚠️ بدون پاسخ از سایت",
}


def _telegram_result(serial, status, result_text, enabled=True):
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    head = _STATUS_FA.get(status, status)
    lines = [
        "📟 نتیجه استعلام اصالت (همتا)",
        f"وضعیت: {head}",
        f"سریال: {serial or '—'}",
    ]
    if result_text:
        lines.append(f"پیام سامانه: {result_text}")
    lines.append(f"زمان استعلام: {when}")
    _telegram("\n".join(lines), enabled=enabled)


# --- driver -----------------------------------------------------------------
def build_driver():
    if USE_EDGE:
        o = EdgeOptions()
        o.add_argument("--disable-gpu")
        o.add_experimental_option("mobileEmulation",
                                  {"deviceMetrics": MOBILE_METRICS, "userAgent": UA})
        o.add_argument(window_size_arg())
        return install_manual_scroll(webdriver.Edge(options=o))
    o = Options()
    o.add_argument("--disable-gpu")
    o.add_experimental_option("mobileEmulation",
                              {"deviceMetrics": MOBILE_METRICS, "userAgent": UA})
    o.add_argument(window_size_arg())
    if CHROMEDRIVER_PATH:
        return install_manual_scroll(
            webdriver.Chrome(service=ChromeService(executable_path=CHROMEDRIVER_PATH),
                             options=o))
    return install_manual_scroll(webdriver.Chrome(options=o))


# --- captcha ----------------------------------------------------------------
def _wait_img_loaded(driver, el, timeout=10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if driver.execute_script(
                    "return arguments[0].complete && arguments[0].naturalWidth>0;", el):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def grab_captcha_pil(driver, img_el):
    """Aks-e captcha-ye modal ra be soorat-e PIL barmigardanad.
    Ravesh: element.screenshot (chon captcha-ye hamta dar kadr-e sefid-e tamiz ast va
    element.screenshot deghat-e behtar-i midahad — dar recon conf 0.80 vs 0.63)."""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", img_el)
    time.sleep(0.3)
    _wait_img_loaded(driver, img_el)
    png = img_el.screenshot_as_png
    return Image.open(io.BytesIO(png))


def refresh_captcha(driver):
    """Roo-ye link-e refresh-e modal click mikonad (src-e aks avaz mishavad)."""
    try:
        old = driver.execute_script(
            "var i=document.querySelector(arguments[0]);return i?i.getAttribute('src'):'';",
            SEL_M_CAPTCHA_IMG)
        el = driver.find_element(By.CSS_SELECTOR, SEL_M_REFRESH)
        driver.execute_script("arguments[0].click();", el)
        end = time.time() + 8
        while time.time() < end:
            new = driver.execute_script(
                "var i=document.querySelector(arguments[0]);return i?i.getAttribute('src'):'';",
                SEL_M_CAPTCHA_IMG)
            if new and new != old:
                break
            time.sleep(0.15)
        img = driver.find_element(By.CSS_SELECTOR, SEL_M_CAPTCHA_IMG)
        _wait_img_loaded(driver, img)
        return True
    except Exception as e:
        print(f">>> (refresh-e captcha na-shod: {e})")
        return False


# --- form -------------------------------------------------------------------
def _set_input(driver, sel, value):
    el = driver.find_element(By.CSS_SELECTOR, sel)
    driver.execute_script(
        "arguments[0].value=''; arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",
        el)
    el.send_keys(str(value))
    driver.execute_script(
        "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
        "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
    return el


def _field_value(driver, sel):
    try:
        return driver.execute_script(
            "var i=document.querySelector(arguments[0]);return i?i.value:'';", sel) or ""
    except Exception:
        return ""


# --- page health ------------------------------------------------------------
def open_page_healthy(driver, wait, max_reload=MAX_PAGE_RELOAD) -> bool:
    """Safhe ra baz mikonad va motmaen mishavad dokme-ye estelam hast (site salem load shode)."""
    for attempt in range(1, max_reload + 1):
        try:
            driver.get(URL)
        except WebDriverException as e:
            print(f">>> (get na-shod, talash {attempt}: {e})")
            time.sleep(2)
            continue
        try:
            WebDriverWait(driver, PAGE_LOAD_SEC).until(
                lambda d: d.execute_script("return document.readyState") == "complete")
        except TimeoutException:
            pass
        time.sleep(1.0)
        btns = [b for b in driver.find_elements(By.CSS_SELECTOR, SEL_INQUIRY_BTN)
                if b.is_displayed()]
        if btns:
            return True
        print(f">>> dokme-ye estelam na-bood (talash {attempt}/{max_reload}) -> reload")
        time.sleep(1.5)
    return False


def dismiss_swal(driver, timeout=6.0):
    """Swal-e fe'li (mesl-e swal-e khata-ye captcha) ra ba click-e 'تایید' mibandad."""
    end = time.time() + timeout
    try:
        b = driver.find_element(By.CSS_SELECTOR, SEL_M_CONFIRM)
        driver.execute_script("arguments[0].click();", b)
    except Exception:
        return
    while time.time() < end:
        if not driver.find_elements(By.CSS_SELECTOR, SEL_MODAL):
            return
        if not any(m.is_displayed()
                   for m in driver.find_elements(By.CSS_SELECTOR, SEL_MODAL)):
            return
        time.sleep(0.15)


def open_modal(driver, wait) -> bool:
    """Modal-e estelam ra baz mikonad va montazer-e load-e kamel (captcha+dokme) mimanad."""
    # agar modal-e FORM (dara-ye captcha-ye estelam) az ghabl baz ast, hamon-ra ghabool kon.
    # (yek swal-e KHATA momken ast baz bashad — an modal-e form NIST, pas rad mishavad.)
    try:
        if driver.find_elements(By.CSS_SELECTOR, SEL_M_CAPTCHA_IMG) and \
                driver.find_elements(By.CSS_SELECTOR, SEL_M_IMEI):
            return True
    except Exception:
        pass
    clicked = False
    for b in driver.find_elements(By.CSS_SELECTOR, SEL_INQUIRY_BTN):
        if b.is_displayed():
            driver.execute_script("arguments[0].click();", b)
            clicked = True
            break
    if not clicked:
        driver.execute_script(
            "if(typeof $!=='undefined'){$('.imeiInquiry').trigger('click');}")
    try:
        WebDriverWait(driver, MODAL_WAIT_SEC).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, SEL_MODAL)))
        WebDriverWait(driver, MODAL_WAIT_SEC).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SEL_M_CAPTCHA_IMG)))
        WebDriverWait(driver, MODAL_WAIT_SEC).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SEL_M_IMEI)))
        WebDriverWait(driver, MODAL_WAIT_SEC).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SEL_M_CONFIRM)))
        time.sleep(0.8)
        return True
    except TimeoutException:
        return False


# --- result -----------------------------------------------------------------
def _read_result_text(driver):
    """Motn-e swal-e natije ra mikhanad (ba'd az submit yek swal-e jadid miayad).
    Motn az .swal2-content ya .swal2-title-e swal-e fe'li khande mishavad."""
    raw = driver.execute_script("""
        var out = [];
        var c = document.querySelector('#swal2-content, .swal2-content');
        if (c && c.offsetParent !== null) out.push(c.innerText||'');
        var t = document.querySelector('#swal2-title, .swal2-title');
        if (t && t.offsetParent !== null) out.push(t.innerText||'');
        return out.join(' | ').trim();
    """) or ""
    # tamiz-kardan-e joda-konande-ha va faseleha-ye ezafe (mesl-e ' |' -e entehayi)
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    return " | ".join(parts)


def _loader_busy(driver):
    try:
        return driver.execute_script(
            "var l=document.querySelector(arguments[0]);"
            "return l ? getComputedStyle(l).display!=='none' : false;", SEL_LOADER)
    except Exception:
        return False


def classify(text):
    if not text:
        return "error"
    # 0) MASDOODI/LAGHV-e GHAT'I — bala-tarin olaviat (hatta agar moshakhasat-e
    #    dastgah ham dar hamun payam bashad)
    if any(m in text for m in CANCELLED_MARKERS):
        return "blocked"
    # 1) dastgah-e sabt-shode/qanooni (success swal) — OLAVIAT ta hoshdar-e sharti
    #    eshtebahan blocked nashavad
    if any(m in text for m in REGISTERED_MARKERS):
        return "registered"
    # 2) masdoodi-ye VAGHE'I (na hoshdar-e sharti-ye «مسدود خواهد شد»)
    stripped = text
    for fp in _BLOCKED_FALSE_POSITIVES:
        stripped = stripped.replace(fp, "")
    if any(m in stripped for m in BLOCKED_MARKERS):
        return "blocked"
    if any(m in text for m in GOOD_MARKERS):
        return "good"
    if any(m in text for m in TRANSIENT_MARKERS):
        return "transient"
    if any(m in text for m in CAPTCHA_EXPIRED_MARKERS):
        return "captcha_expired"
    if any(m in text for m in CAPTCHA_WRONG_MARKERS):
        return "captcha_wrong"
    return "unknown"


def submit_and_read(driver, wait):
    """Dokme-ye 'جستجو' ra mizanad va montazer-e natije mimanad.
    barmigardanad (classification, matn-e natije)."""
    # motn-e ghabl az submit (ta taghyir-e swal ra tashkhis bedhim)
    before = _read_result_text(driver)
    try:
        btn = driver.find_element(By.CSS_SELECTOR, SEL_M_CONFIRM)
        driver.execute_script("arguments[0].click();", btn)
    except Exception as e:
        print(f">>> (click-e jostoju na-shod: {e})")
        return "error", ""

    deadline = time.time() + RESULT_TIMEOUT_SEC
    last = ""
    while time.time() < deadline:
        time.sleep(0.4)
        if _loader_busy(driver):
            continue
        txt = _read_result_text(driver)
        if not txt:
            continue
        # swal-e natije bayad ba motn-e ghabli (form) motefavet bashad ya
        # marker-e ma'nadar dashte bashad
        cls = classify(txt)
        if cls != "unknown" or (txt != before and "ImeiNumber" not in txt):
            last = txt
            if cls in ("good", "blocked", "success", "captcha_wrong",
                       "captcha_expired", "transient"):
                return cls, txt
    return classify(last), last


# --- yek estelam ------------------------------------------------------------
def inquire(driver, wait, solver, serial, telegram=True):
    """Yek serial ra estelam migirad. natije: dict."""
    serial = str(serial).strip()
    if not open_page_healthy(driver, wait):
        _elog("error", "safhe-ye hamta salem load nashod (dokme-ye estelam peyda nashod)",
              serial=serial)
        _telegram_result(serial, "error", "صفحه همتا سالم باز نشد", enabled=telegram)
        return {"status": "error", "result": "", "serial": serial}

    if not open_modal(driver, wait):
        _elog("error", "modal-e estelam baz/kamel nashod", serial=serial)
        _telegram_result(serial, "error", "مودال استعلام باز نشد", enabled=telegram)
        return {"status": "error", "result": "", "serial": serial}

    # IMEI ra yek bar por mikonim (captcha momken ast chand bar refresh shavad)
    _set_input(driver, SEL_M_IMEI, serial)

    last_txt = ""
    for attempt in range(1, MAX_CAPTCHA_TRIES + 1):
        # captcha ra hall va por kon
        try:
            img = driver.find_element(By.CSS_SELECTOR, SEL_M_CAPTCHA_IMG)
            crop = grab_captcha_pil(driver, img)
        except (StaleElementReferenceException, WebDriverException) as e:
            print(f">>> (gereftan-e aks-e captcha na-shod: {e}); refresh")
            refresh_captcha(driver)
            continue
        r = solver.read_pil(crop)
        ans = r.get("number")
        print(f">>> [{attempt}] captcha hads={ans} conf={r.get('confidence')} "
              f"text={r.get('text')!r}")
        if ans is None or float(r.get("confidence") or 0) < MIN_CONFIDENCE_TO_SUBMIT:
            print(f"    (etminan < {MIN_CONFIDENCE_TO_SUBMIT} -> refresh-e captcha "
                  f"bedun-e submit)")
            refresh_captcha(driver)
            time.sleep(REFRESH_DELAY)
            continue

        # motmaen sho IMEI hanooz por ast (momken ast reset shode bashad)
        if _field_value(driver, SEL_M_IMEI).strip() != serial:
            _set_input(driver, SEL_M_IMEI, serial)
        _set_input(driver, SEL_M_CAPTCHA_IN, ans)

        # verify
        if _field_value(driver, SEL_M_CAPTCHA_IN).strip() != str(ans):
            print(">>> (verify-e captcha na-khord; dobare)")
            continue

        cls, txt = submit_and_read(driver, wait)
        last_txt = txt or last_txt
        print(f">>> natije: cls={cls} txt={txt!r}")

        if cls in ("good", "registered", "blocked", "success", "unknown"):
            status = cls
            if status == "blocked":
                _elog("blocked", f"KHATAR — dastgah masdood/gheyr-mojaz: {txt}",
                      serial=serial, extra={"raw": txt})
            elif status == "unknown":
                _elog("unknown", f"natije-ye naomaloom (log baraye barresi): {txt}",
                      serial=serial, extra={"raw": txt})
            else:
                _elog(status, txt, serial=serial)
            _telegram_result(serial, status, txt, enabled=telegram)
            return {"status": status, "result": txt, "serial": serial}

        if cls in ("captcha_wrong", "captcha_expired", "transient"):
            # VAGHE'IYAT-e live (probe): swal-e khata modal-e FORM ra kamelan
            # jaygozin mikonad — pas refresh-e captcha bi-ma'ni ast. Bayad swal-e
            # khata basteh va modal-e estelam AZ NO baz shavad (captcha-ye taze
            # khodkar miayad), sepas IMEI dobare por shavad.
            dismiss_swal(driver)
            time.sleep(REFRESH_DELAY)
            if not open_modal(driver, wait):
                if not (open_page_healthy(driver, wait) and open_modal(driver, wait)):
                    _elog("error", "ba'd az khata-ye captcha, modal-e estelam dobare "
                                   "baz nashod", serial=serial)
                    break
            _set_input(driver, SEL_M_IMEI, serial)
            continue

        # error/pending -> yek talash-e digar
        time.sleep(REFRESH_DELAY)

    _elog("error", f"ba'd az {MAX_CAPTCHA_TRIES} talash natije nagerftim: {last_txt}",
          serial=serial, extra={"raw": last_txt})
    _telegram_result(serial, "error", last_txt or "بدون پاسخ", enabled=telegram)
    return {"status": "error", "result": last_txt, "serial": serial}


def run_from_store(device_id=None, telegram=True):
    """Hame-ye device-haye panel (ya yeki) ra az HAMTA estelam migirad.
    Yek morurgar baraye hame (bar khalaf-e --serial ke yek-bar-masraf ast).
    Roo-ye har device: hamta_status/hamta_result-e panel update mishavad."""
    store = _store_read()
    devices = store.get("devices") or []
    if device_id:
        devices = [d for d in devices if str(d.get("id")) == str(device_id)]
    else:
        devices = [d for d in devices
                   if str(d.get("hamta_status") or "pending") in ("pending", "error", "")]
    if not devices:
        print(">>> hich device-i baraye estelam-e hamta nist.")
        return 0

    print(f">>> {len(devices)} device baraye estelam-e HAMTA.")
    print(">>> Load-e solver ...")
    solver = load_solver()
    driver = build_driver()
    wait = WebDriverWait(driver, MODAL_WAIT_SEC)
    try:
        for index, device in enumerate(devices, 1):
            did = device.get("id")
            serial = str(device.get("serial1") or "").strip()
            print(f"\n=== [{index}/{len(devices)}] hamta device {did} serial={serial} ===")
            if not serial:
                _panel_set_device(did, hamta_status="error",
                                  hamta_result="سریال اول خالی است")
                continue
            _panel_set_device(did, hamta_status="running", hamta_result="")
            _elog("running", f"شروع استعلام همتا ({index} از {len(devices)})",
                  serial=serial, extra={"device_id": did or ""})
            try:
                res = inquire(driver, wait, solver, serial, telegram=telegram)
            except Exception as e:
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                print(f">>> khata-ye robot: {msg}")
                res = {"status": "error", "result": f"خطای ربات: {msg}"}
            _panel_set_device(did, hamta_status=res.get("status") or "error",
                              hamta_result=res.get("result") or "")
            print(f">>> natije-ye hamta {did}: {res.get('status')}")
    finally:
        try:
            driver.quit()
            print(">>> morurgar baste shod (estelam-e hamta tamam).")
        except Exception:
            pass
    return 0


def main():
    system_debug.configure("inquiry-hamta")
    ap = argparse.ArgumentParser(description="Estelam-e asalat-e dastgah az HAMTA")
    ap.add_argument("--serial", help="serial/IMEI-e 15-raghmi (halat-e tak-estelam)")
    ap.add_argument("--from-store", action="store_true",
                    help="device-ha az estelam_store.json-e panel khande shavand")
    ap.add_argument("--device-id", help="ba --from-store: faghat hamin device")
    ap.add_argument("--no-telegram", action="store_true", help="bedun-e ersal-e telegram")
    ap.add_argument("--keep-open", action="store_true", help="morurgar baz bemanad (debug)")
    a = ap.parse_args()

    if a.from_store:
        return run_from_store(a.device_id, telegram=not a.no_telegram)
    if not a.serial:
        ap.error("ya --serial bede ya --from-store")

    print(">>> Load-e solver ...")
    solver = load_solver()
    driver = build_driver()
    wait = WebDriverWait(driver, MODAL_WAIT_SEC)
    try:
        res = inquire(driver, wait, solver, a.serial, telegram=not a.no_telegram)
        print(">>> NATIJE:", json.dumps(res, ensure_ascii=False))
    finally:
        if a.keep_open:
            input(">>> Enter baraye baste shodan ...")
        try:
            driver.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
