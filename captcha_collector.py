"""
Jam'avari-ye tasavir-e captcha az safhe-ye sabt-e IMEI-ye epl.irica.gov.ir
va zakhire be soorat-e file PNG.

Pishniaz-ha (nasb-e ketabkhane-ha):
    pip install selenium pillow

Nokte: az Selenium 4 be ba'd niazi be download-e dasti-ye chromedriver nist;
Selenium Manager be soorat-e khodkar driver-e Chrome ra modiriat mikonad.
Faghat kafist morurgar-e Google Chrome roo-ye system nasb bashad.
Agar server-e Google filter bood (Iran), CHROMEDRIVER_PATH ra dasti tanzim konid.
"""

import argparse
import hashlib
import io
import os
import random
import re
import subprocess
import time

from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------------------------------------------------------------------------
# Tanzimat-e site-e hadaf: epl.irica.gov.ir (safhe-ye sabt-e IMEI)
# ---------------------------------------------------------------------------
# Address-e safhe-i ke captcha roo-ye an ast
URL = "https://epl.irica.gov.ir/ImeiRegister?1"

# Poushe-ye khorooji baraye zakhire-ye aks-ha
OUTPUT_DIR = "captchas_irica"

# Selector-e CSS-e tasvir-e captcha
#   <img class="captchaImage" wicketpath="..._capchaImage" ...>
CAPTCHA_CSS = "img.captchaImage"

# Selector-e CSS-e dokme/link-e refresh-e captcha.
# In site (Apache Wicket) class-e sabet nadarad, pas az attribute-e paydar-e
# `wicketpath` estefade mikonim:
#   <a wicketpath="form_imeiStep_captchaPanel_wmc_refreshCapcha" href="javascript:;">
REFRESH_CSS = 'a[wicketpath="form_imeiStep_captchaPanel_wmc_refreshCapcha"]'

# --- Tashkhis-e captcha-ye KHALI (in site ba avvalin voroud/ba'zi reload-ha
#     captcha ra namayesh nemidahad va faghat yek kadr-e sefid ya aks-e shekaste
#     neshan midahad) ---
# Meyar: nesbat-e pixel-ha-ye TIRE (matn-e captcha) dar tasvir-e boride-shode.
#   - captcha-ye VAGHE'I: ~0.19 ta 0.24 (kadr por az harf va khat-e ghermez ast)
#   - kadr-e KHALI       : ~0.12 (faghat hashiye-ye nazok)
#   - aks-e shekaste     : ~0.07
# Har chizi ba nesbat-e KAMTAR az in astane "khali" telaghi mishavad -> refresh.
MIN_DARK_RATIO = 0.15

# ---------------------------------------------------------------------------
# Tanzimat-e driver-e morurgar
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
# Masir-e dasti-ye chromedriver.
# Agar Selenium natavanest khodkar driver ra download konad (server-e Google filter ast),
# file chromedriver.exe motenaseb ba version-e Chrome khod ra download konid va masir-ash ra
# inja vared konid. mesal:  CHROMEDRIVER_PATH = r"C:\chromedriver.exe"
# Khali bemanad -> resolve_chromedriver() khodkar entekhab mikonad (override ->
# nuskhe-ye bundel-shode dar .tools\ -> Selenium Manager).
CHROMEDRIVER_PATH = r"C:\chromedriver.exe"

# Agar mikhahid mostaghiman az morurgar-e Edge estefade shavad, in ra True konid:
USE_EDGE = False

# --- Emulation-e mobile (baraye site-e irica ke roo-ye desktop naghes load mishavad) ---
# In hamon kari ast ke DevTools ba entekhab-e resolution-e mobile anjam midahad:
# yek viewport-e mobile + device pixel ratio + user-agent-e mobile.
# Ba in kar site naskhe-ye mobile-e khod ra (ke doros load mishavad) namayesh midahad.
MOBILE_EMULATION = True

# Andaze-ye viewport-e mobile-e shabih-sazi-shode.
#   width      = arze-ye logic-e iPhone 14 Pro Max (site naskhe-ye mobile midahad)
#   height = 0 = "in mehvar ra override NAKON" -> viewport daghighan hamandaze-ye
#                nahiye-ye ghabel-e namayesh-e panjere mishavad.
#
# CHERA height=0 va na 932 (ghad-e vaghe'i-ye iPhone)?
#   panjere-ye 430x932 dar amal 516x932 mishavad ke 151px-ash chrome-e morurgar
#   ast, pas faghat 781 CSS-pixel az safhe DIDE mishavad. ba height=932:
#     * 151 pixel-e paeen-e viewport HAMISHE birun-e kadr mimanad (moshkel-e
#       gozaresh-shode: bakhshi az form-e login dide nemishavad), va
#     * baraye safhe-i kootah-tar az 932 (safhe-ye login) scrollHeight ba
#       clientHeight barabar mishavad -> HICH range-e scroll-i nist, pas na
#       charkh-e mouse na keyboard hich kari nemikonand.
#   ba height=0 har do moshkel az bein miravand va viewport ba resize-e panjere
#   ham hamahang mimanad. arze/DPR/UA dast-nakhorde -> layout-e mobile hefz mishavad
#   va meghyas-e screenshot (baraye crop-e captcha) ham 3.0 mimanad.
MOBILE_METRICS = {"width": 430, "height": 0, "pixelRatio": 3.0, "mobile": True}

# Andaze-ye VAGHE'I-ye panjere (--window-size). height-e MOBILE_METRICS sefr ast va
# NABAYAD inja estefade shavad, vagarna panjere-ye bi-ertefa sakhte mishavad.
WINDOW_SIZE = (430, 932)


def window_size_arg():
    """`--window-size=W,H` ba adad-haye VAGHE'I (na height-e 0-e emulation)."""
    return f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}"


# --- zoom-e safhe-ye SITE-e gomrok (na panel-e ma) --------------------------------
# Site-e irica dar zoom-e 100% dokme/matn-hash biroon mizanad; 25% kamtar (0.75)
# hame chiz ra dar yek safhe ja mikonad. IN faghat roo-ye morurgar-e robot ast.
# NOKTE-ye MOHEM: aks-e captcha ba zoom-e MA'KOOS neveshte mishavad ta andaze-ye
# vaghe'i-ash taghir nakonad — vagarna crop/OCR kharab mishavad.
SITE_ZOOM = 0.75
_ZOOM_CSS_ID = "irica-site-zoom"
_ZOOM_BOOTSTRAP = """
(function(){
  var Z = %(zoom)s, ID = "%(id)s", INV = (1 / Z);
  function apply(){
    var d = document.documentElement;
    if (!d) return;
    var s = document.getElementById(ID);
    if (!s) {
      s = document.createElement("style");
      s.id = ID;
      (document.head || d).appendChild(s);
    }
    s.textContent = "html{zoom:" + Z + "}" +
      "img.captchaImage{zoom:" + INV + "}";
  }
  apply();
  document.addEventListener("DOMContentLoaded", apply);
})();
"""


def zoom_bootstrap_js(zoom=SITE_ZOOM):
    return _ZOOM_BOOTSTRAP % {"zoom": zoom, "id": _ZOOM_CSS_ID}


def install_site_zoom(driver, zoom=SITE_ZOOM):
    """Zoom ra ba CDP roo-ye HAR navigation/reload paydar mikonad va roo-ye
    safhe-ye fe'li ham foran e'mal mikonad. Agar CDP dar dastres nabashad,
    faghat safhe-ye fe'li zoom migirad (bi-khata rad mishavad)."""
    source = zoom_bootstrap_js(zoom)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                               {"source": source})
    except Exception as exc:
        print(f">>> (install-e zoom ba CDP na-shod: {exc})")
    try:
        driver.execute_script(source)
    except Exception:
        pass
    return driver


# --- scroll-e DASTI-ye karbar (baznagari-ye safhe dar panjere-ye robot) ------------
# Emulation-e mobile scrollbar-e overlay midahad: 0 pixel az layout migirad va ba
# mouse ghabel-e gereftan NIST (ba CSS-e ::-webkit-scrollbar ya flag-haye
# --disable-*overlay*scrollbar ham bar nemigardad; har 4 ravesh sanjide shod va
# hame 0px dadand). pas baraye baznagari-ye dasti do rah migozarim:
#   1) DRAG ba mouse — mesl-e angosht rooye gooshi. `setEmitTouchEventsForMouse`
#      input-e mouse ra be touch tabdil mikonad va safhe ba keshidan scroll mishavad.
#   2) charkh-e mouse va keyboard (PageDown/End/arrow) ke hamishe kar mikonand.
# Har do rooye panjere-ye robot sanjide shodand (lab/scripts/probe_dragscroll_safety.py):
# drag 3/3, click-e vaghe'i salem mimanad, va ba'd az har navigation baghi mimanad.
def install_manual_scroll(driver):
    """Drag-ba-mouse baraye scroll-e dasti ra faal mikonad (best effort)."""
    try:
        driver.execute_cdp_cmd("Emulation.setEmitTouchEventsForMouse",
                               {"enabled": True, "configuration": "mobile"})
    except Exception as exc:
        print(f">>> (faal-kardan-e scroll-e dasti na-shod: {exc})")
    return driver


# Nesbat-e viewport ke har bar scroll mishavad (0.85 = kami hamposhani ta
# matn/dokme-i bein-e do scroll ja nayoftad).
_SCROLL_STEP = 0.85


def scroll_page(driver, direction="down"):
    """Safhe-ye robot ra scroll mikonad: down | up | top | bottom.

    Baraye baznagari-ye dasti va script-haye kontrol. False barmigardanad agar
    session dar dastres nabashad (HARGEZ exception be biroon nemidahad).
    """
    if direction in ("down", "up"):
        script = ("window.scrollBy({top: window.innerHeight * arguments[0],"
                  " behavior: 'instant'});")
        amount = _SCROLL_STEP if direction == "down" else -_SCROLL_STEP
        args = (script, amount)
    elif direction == "bottom":
        args = ("window.scrollTo({top: document.documentElement.scrollHeight,"
                " behavior: 'instant'});",)
    elif direction == "top":
        args = ("window.scrollTo({top: 0, behavior: 'instant'});",)
    else:
        raise ValueError(f"jahat-e na-motabar: {direction!r}")
    try:
        driver.execute_script(*args)
        return True
    except Exception:
        return False



# Hashiye-ye ezafe (be pixel-e CSS) ke az har taraf-e element-e captcha bishtar
# borش mikhorad. chon khat-e ghermez va harf-ha kami az labe-ye element birun
# mizanand, screenshot-e ma'mooli-ye element an-ha ra boride zakhire mikonad.
# Ba in hashiye, aks az chap/rast/bala/paeen kami bozorgtar gerefte mishavad.
#   PAD_X: hashiye-ye afqi (chap va rast)  |  PAD_Y: hashiye-ye amudi (bala va paeen)
PAD_X = 12
PAD_Y = 6

# Jابه-jaیi-ye amudi-ye koll-e kadr (be CSS-pixel). meghdar-e MANFI kadr ra
# bala-tar mibarad va meghdar-e MOSBAT paeen-tar. baraye tanzim-e daghigh-e
# markaz-e captcha (agar kami bala/paeen bood).
OFFSET_Y = -8

# Baraye jelogiri az spam/mased shodan: har chand aks yek bar morurgar baste va
# ba yek User-Agent (agent) va session-e taze dobare baz mishavad.
RESTART_EVERY = 15

# Liste User-Agent-ha (agent = moshakhasat-e morurgar). Har bar ke morurgar taze
# baz mishavad, yeki az inha be tor-e gardeshi entekhab mishavad ta asar-e angosht
# (fingerprint) sabet nabashad va server sadegi natavanad mased konad.
# NOKTE: chon in naskhe mobile ast, UA-ha ham UA-ye gooshi hastand ta ba
# emulation-e mobile hamahang bashand.
# NOKTE: chon viewport iPhone 14 Pro Max ast, hame-ye UA-ha ham iPhone/Safari
# hastand ta ba emulation hamahang bashand (DevTools ham UA va viewport ra ba
# ham iPhone mikonad). faghat versiyon-e iOS/Safari fargh mikonad baraye gardesh.
USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]


def ask_repeat_count():
    """Gereftan-e te'dad-e daf'aat-e download az karbar va tabdil be adad-e sahih."""
    while True:
        raw = input("Chand bar mikhahid aks-e captcha download shavad? ")
        try:
            count = int(raw.strip())
            if count <= 0:
                print("Lotfan yek adad-e bozorgtar az sefr vared konid.")
                continue
            return count
        except ValueError:
            print("Voroodi na-motabar ast. Lotfan yek adad-e sahih vared konid.")


def ask_refresh_delay():
    """Gereftan-e zaman-e ta'khir (saniye) ba'd az refresh az karbar."""
    while True:
        raw = input("Chand saniye ba'd az har refresh sabr shavad? (ashari majaz, mesal 0.1 - pishfarz 3) ")
        raw = raw.strip()
        if raw == "":
            return 3.0
        try:
            delay = float(raw)
            if delay < 0:
                print("Lotfan yek adad-e mosbat vared konid.")
                continue
            return delay
        except ValueError:
            print("Voroodi na-motabar ast. Lotfan yek adad vared konid.")


def ask_periodic_restart():
    """Miporsad ke aya restart-e DORE-I-ye morurgar (har chand aks yek bar KOLL-e
    safhe az no baz shavad) faal bashad ya na.

    Barmigardanad: (enabled: bool, every: int)
      - enabled=False -> aslan restart-e dore-i nemishavad (faghat refresh-e captcha)
      - enabled=True  -> har `every` aks yek bar morurgar restart mishavad
    """
    while True:
        raw = input("Restart-e dore-i-ye morurgar faal bashad? "
                    "(har chand aks yek bar safhe az no baz shavad) [y/N] ").strip().lower()
        if raw in ("", "n", "no", "na", "kheyr"):
            return False, 0
        if raw in ("y", "yes", "bale", "are"):
            break
        print("Lotfan y (bale) ya n (na) vared konid.")
    while True:
        raw = input(f"Har chand aks yek bar restart shavad? (pishfarz {RESTART_EVERY}): ").strip()
        if raw == "":
            return True, RESTART_EVERY
        try:
            every = int(raw)
            if every > 0:
                return True, every
        except ValueError:
            pass
        print("Lotfan yek adad-e bozorgtar az sefr vared konid.")


def human_delay(base: float, jitter: float = 0.6) -> None:
    """
    Sabr-e shabih-e ensan: yek ta'khir-e tasadofi around-e `base` saniye.
    In kar (be jaye sleep-e sabet) alagoo-ye darkhast ra tabi'i-tar mikonad va
    ehtemal-e block/mask shodan tavassot-e server ra kam mikonad.
    """
    lo = max(0.0, base * (1 - jitter))
    hi = base * (1 + jitter)
    time.sleep(random.uniform(lo, hi))


def _captcha_fingerprint(driver, element) -> str:
    """
    Yek "asar-e angosht" az mohteva-ye tasvir-e captcha barmigardanad.

    Cherā: dar site-e irica ba'd az refresh, attribute-e `src` ma'moolan
    taghir NEMIKONAD (Wicket hamon URL-e dakheli ra hefz mikonad va faghat
    pixel-ha-ye tasvir avaz mishavand). pas faghat chek-e `src` kafi nist.

    Ravesh: element ra dar yek canvas mikeshim va base64-e PNG-ash ra hash mikonim.
    Agar canvas be dalil-e tainted (CORS) kar nakonad, be `src` fallback mikonim.
    Roshte-ye barghashti yekta baraye har mohteva-ye tasvir ast.
    """
    try:
        data_url = driver.execute_script(
            """
            const el = arguments[0];
            if (!el || !el.complete || !el.naturalWidth) return '';
            try {
                const c = document.createElement('canvas');
                c.width = el.naturalWidth;
                c.height = el.naturalHeight;
                c.getContext('2d').drawImage(el, 0, 0);
                return c.toDataURL('image/png');
            } catch (e) {
                return '';
            }
            """,
            element,
        )
        if data_url:
            return hashlib.md5(data_url.encode("utf-8")).hexdigest()
    except Exception:
        pass
    # fallback: faghat src
    try:
        return "src:" + (element.get_attribute("src") or "")
    except Exception:
        return ""


def snapshot_captcha(driver, element):
    """
    Yek snapshot az halat-e fe'li-ye captcha barmigardanad be shekl-e tuple (src, fp).

    Bayad GHABL az click-e refresh estefade shavad ta ba'd betavanim ba
    `wait_for_new_captcha` motmaen shavim ke aks vaghe'an taghir karde ast.
    """
    try:
        src = element.get_attribute("src")
    except Exception:
        src = None
    fp = _captcha_fingerprint(driver, element)
    return (src, fp)


def wait_for_new_captcha(driver, old_ref, timeout: float = 10.0) -> bool:
    """
    Ba'd az click-e refresh, sabr mikonad ta aks-e captcha VAGHE'AN avaz shavad.

    `old_ref` mitavanad chand chiz bashad:
      - tuple `(old_src, old_fp)`  — tarjih dade mishavad; hardo chek mishavand.
        (dar irica `src` ma'moolan sabet mimand va faghat pixel avaz mishavad)
      - element-e captcha-ye ghabli — az roo-yash tuple sakhte mishavad
        (agar hanooz stale nashode bashad).
      - roshte-ye `src`-e ghabli — faghat taghir-e `src` chek mishavad (samazegari-ye ghadim).

    True agar aks avaz shod, False agar timeout shod.
    """
    # tashkhis-e halat-e voroodi
    old_src = None
    old_fp = None
    if isinstance(old_ref, tuple) and len(old_ref) == 2:
        old_src, old_fp = old_ref
    elif isinstance(old_ref, str) or old_ref is None:
        old_src = old_ref
    else:
        # element
        try:
            old_src = old_ref.get_attribute("src")
        except Exception:
            old_src = None
        try:
            old_fp = _captcha_fingerprint(driver, old_ref)
        except Exception:
            old_fp = None

    end = time.time() + timeout
    while time.time() < end:
        try:
            el = driver.find_element(By.CSS_SELECTOR, CAPTCHA_CSS)
            cur_src = el.get_attribute("src")
            # 1) taghir-e src (agar URL-e tasvir avaz shod)
            if cur_src and old_src is not None and cur_src != old_src:
                return True
            # 2) taghir-e pixel (dar irica src ma'moolan sabet mimand)
            if old_fp:
                cur_fp = _captcha_fingerprint(driver, el)
                if cur_fp and cur_fp != old_fp:
                    return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def wait_image_loaded(driver, element, timeout: float = 10.0) -> bool:
    """
    Sabr mikonad ta tasvir-e captcha VAGHE'AN dar morurgar kamel load shavad.

    Meyar-ha (har do bayad bargharar bashand):
      - element.complete == True  (morurgar load ra tamam karde)
      - element.naturalWidth > 0  (tasvir-e vaghe'i darad, na aks-e khali/nim-load)

    Bedun-e in, screenshot momken ast az aks-e nim-load ya khali gerefte shavad
    ke boride/nagos zakhire mishavad (makhsusan dar safhe-ha-ye AJAX mesl-e irica).
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            ready = driver.execute_script(
                "return arguments[0].complete && arguments[0].naturalWidth > 0;",
                element,
            )
            if ready:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def prepare_captcha_for_shot(driver, element, timeout: float = 10.0) -> bool:
    """
    Element-e captcha ra baraye screenshot-e salem amade mikonad.

    Marahel:
      1) element ra be markaz-e safhe scroll mikonad (agar element kamel dar view
         nabashad, screenshot-e element boride/naghes mishavad).
      2) sabr mikonad ta tasvir kamel load shavad (complete + naturalWidth > 0).

    True agar tasvir kamel amade shod, False agar timeout shod.
    """
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
            element,
        )
    except Exception:
        pass
    # kami sabr ba'd az scroll ta render sabet shavad
    time.sleep(0.2)

    return wait_image_loaded(driver, element, timeout=timeout)


def wait_captcha_stable(driver, element, settle: float = 0.6,
                        attempts: int = 3) -> bool:
    """True when the captcha image has STOPPED changing.

    After a reload/F5 the Wicket page keeps swapping the captcha img for a
    short while (AJAX bind + progressive render). OCR on a mid-swap frame
    reads image A while the server accepts image B -> guaranteed 'کد اعتبار
    سنجی صحیح نمی باشد'. We fingerprint the image, wait `settle` seconds, and
    only pass when the fingerprint is unchanged for `attempts` consecutive
    checks (bounded, never infinite).
    """
    last = None
    same = 0
    import time as _time
    deadline = _time.time() + 10.0
    while _time.time() < deadline:
        try:
            fp = _captcha_fingerprint(driver, element)
        except Exception:
            _time.sleep(0.3)
            continue
        if fp == last:
            same += 1
            if same >= attempts - 1:
                return True
        else:
            same = 0
        last = fp
        _time.sleep(settle)
    return False


def dark_pixel_ratio(img, threshold: int = 110) -> float:
    """
    Nesbat-e pixel-ha-ye TIRE (kamtar az `threshold`) dar tasvir ra barmigardanad.

    In moadel-e "cheghadr matn/khat dar tasvir hast" ast:
      - captcha-ye VAGHE'I por az harf-e kaj va khat-e ghermez ast -> nesbat-e bala (~0.20)
      - kadr-e KHALI faghat yek hashiye-ye nazok darad -> nesbat-e paeen (~0.12)
      - aks-e shekaste/icon -> nesbat-e kheyli paeen (~0.07)

    Ba in meyar mishavad ghabl az zakhire tashkhis dad ke captcha vaghe'i amade
    ast ya kadr-e khali (ke in site ba'zi vaghtha neshan midahad).
    """
    g = img.convert("L")
    px = g.getdata()
    n = len(px)
    if n == 0:
        return 0.0
    dark = sum(1 for p in px if p < threshold)
    return dark / n


def crop_captcha_with_padding(driver, element,
                              pad_x: int = PAD_X, pad_y: int = PAD_Y):
    """
    Nahiye-ye captcha ra ba hashiye-ye ezafe az har taraf az screenshot bor mizanad
    va tasvir-e Pillow-ye boride-shode ra barmigardanad (bedun-e zakhire).

    Cherā: `element.screenshot()` daghighan be andaze-ye ja'be-ye element bor mizanad,
    va chon khat-e ghermez/harf-ha kami az labe birun mizanand, kenare-ha boride mishavand.
    Rah-hal: az KOLL-e safhe screenshot migirim va nahiye-ye captcha ra ba `pad_x`/`pad_y`
    pixel hashiye-ye bishtar az har taraf bor mizanim.

    Nokte-ye mohem: dar halat-e mobile-emulation, `devicePixelRatio` (DPR) ma'moolan 1
    nist (mesl-e 3). mokhtasat-e element bar hasb-e CSS-pixel ast vali aks-e screenshot
    bar hasb-e pixel-e vaghe'i, pas hame-ye mokhtasat ra dar DPR zarb mikonim.
    """
    # 1) mokhtasat va andaze-ye element (bar hasb-e CSS-pixel) + arze-ye viewport
    info = driver.execute_script(
        "const r = arguments[0].getBoundingClientRect();"
        "return {x: r.left, y: r.top, w: r.width, h: r.height,"
        " vw: window.innerWidth, vh: window.innerHeight};",
        element,
    )

    # 2) screenshot az koll-e viewport -> tabdil be tasvir-e Pillow
    png = driver.get_screenshot_as_png()
    img = Image.open(io.BytesIO(png))

    # 3) zarib-e meghyas ra MOSTAGHIM az roo-ye khod-e screenshot hesab mikonim
    #    (na az devicePixelRatio; dar mobile-emulation, andaze-ye screenshot momken
    #     ast ba CSS-pixel yeki bashad na zarb-dar-DPR). in ravesh dar har do halat
    #    dorost kar mikonad: arze-ye vaghe'i-ye aks taghsim bar arze-ye viewport.
    scale_x = img.width / info["vw"]
    scale_y = img.height / info["vh"]

    # 4) mahdude-ye bor ba hashiye (hashiye be CSS-pixel, sepas zarb dar meghyas).
    #    OFFSET_Y koll-e kadr ra bala/paeen jabe-ja mikonad (be har do top va bottom).
    left   = int((info["x"] - pad_x) * scale_x)
    top    = int((info["y"] - pad_y + OFFSET_Y) * scale_y)
    right  = int((info["x"] + info["w"] + pad_x) * scale_x)
    bottom = int((info["y"] + info["h"] + pad_y + OFFSET_Y) * scale_y)

    # 5) clamp be marz-ha-ye tasvir ta az kadr birun nazanad
    left   = max(0, left)
    top    = max(0, top)
    right  = min(img.width,  right)
    bottom = min(img.height, bottom)

    return img.crop((left, top, right, bottom))


def is_captcha_empty(driver, element) -> bool:
    """
    Chek mikonad ke element-e captcha KHALI ast ya na (kadr-e sefid bedun-e matn).

    Agar MIN_DARK_RATIO tarif nashode bashad (None), hamishe False barmigardanad
    (yani tashkhis-e khali gheyr-fa'al ast va hame-ye aks-ha vaghe'i telaghi mishavand).

    True  = khali (bayad refresh/reload shavad)
    False = vaghe'i (bayad zakhire shavad)
    """
    if MIN_DARK_RATIO is None:
        return False
    try:
        crop = crop_captcha_with_padding(driver, element)
        ratio = dark_pixel_ratio(crop)
        return ratio < MIN_DARK_RATIO
    except Exception:
        # agar be har dalili natavanestim chek konim, mohafezekaraneh khali farz nemikonim
        return False


def save_captcha_with_padding(driver, element, file_path: str,
                              pad_x: int = PAD_X, pad_y: int = PAD_Y) -> None:
    """
    Aks-e captcha ra ba hashiye-ye ezafe az har taraf zakhire mikonad.
    (bor be ohde-ye crop_captcha_with_padding ast.)
    """
    crop = crop_captcha_with_padding(driver, element, pad_x=pad_x, pad_y=pad_y)
    crop.save(file_path)


def resolve_chromedriver():
    """One shared chromedriver resolution for every robot in this project.

    Order (first existing file wins):
      1. Explicit CHROMEDRIVER_PATH override (e.g. C:\\chromedriver.exe) —
         kept for machines that manage the driver by hand.
      2. The bundled copy in <install>\\.tools\\chromedriver.exe (shipped by
         the installer) so a fresh install needs no manual download.
      3. Empty string -> Selenium Manager resolves/downloads the driver.

    The order matters: the override must stay first or a hand-managed newer
    driver would be shadowed by the bundled one after Chrome updates.
    """
    override = str(globals().get("CHROMEDRIVER_PATH", "") or "").strip()
    if override and os.path.isfile(override):
        return override
    bundled = os.path.join(HERE, ".tools", "chromedriver.exe")
    if os.path.isfile(bundled):
        return bundled
    return ""


def _read_major_version(exe_path):
    """Chrome/ChromeDriver 'XXX.YY.ZZZZ.NNN' major from --version output.

    Returns an int, or None when the version cannot be read (missing exe,
    unreadable output). Never raises."""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    try:
        out = subprocess.run([exe_path, "--version"], capture_output=True,
                             text=True, timeout=15)
        text = (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+)\.\d+\.\d+(\.\d+)?", text)
    return int(match.group(1)) if match else None


def _chrome_major_from_registry_or_path():
    """Installed Chrome's major version on Windows (best effort, never raises)."""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive,
                                r"Software\Google\Chrome\BLBeacon") as key:
                value, _ = winreg.QueryValueEx(key, "version")
                match = re.match(r"(\d+)\.", str(value))
                if match:
                    return int(match.group(1))
        except OSError:
            continue
    return None


def driver_compat_problem(driver_version="", browser_version=""):
    """Persian warning when chromedriver and Chrome majors do not line up.

    Arguments accept either a version STRING ("152.0.7977.83") or a path to
    an exe whose --version output carries the number. Empty/unknown versions
    never produce a false alarm, but a readable driver against a DIFFERENT
    readable browser major does — that mismatch is what kills the robots
    with SessionNotCreatedException after a Chrome auto-update.
    """
    driver_version, browser_version = str(driver_version or ""), str(browser_version or "")
    if not driver_version or not browser_version:
        return ""
    if driver_version.lower().endswith(".exe"):
        driver_major = _read_major_version(driver_version)
    else:
        match = re.match(r"(\d+)\.", driver_version)
        driver_major = int(match.group(1)) if match else None
    browser_match = re.match(r"(\d+)\.", browser_version)
    if driver_major is None or not browser_match:
        return ""
    browser_major = int(browser_match.group(1))
    if driver_major == browser_major:
        return ""
    return (f"هشدار نسخه: ChromeDriver نسخه {driver_major} است اما Chrome "
            f"نسخه {browser_major} — ربات ممکن است باز نشود. لطفاً chromedriver "
            f"متناسب با Chrome {browser_major} را جایگزین کنید.")


def check_driver_compat(driver_path=""):
    """Log (and return) the compat warning for a resolved chromedriver path.

    Browser version is read from the Windows registry; on failure the check
    stays silent — this is a warning channel, never a blocker."""
    problem = ""
    driver_path = driver_path or resolve_chromedriver()
    if driver_path:
        browser_major = _chrome_major_from_registry_or_path()
        if browser_major:
            driver_major = _read_major_version(driver_path)
            if driver_major and driver_major != browser_major:
                problem = driver_compat_problem(str(driver_major),
                                                str(browser_major))
    if problem:
        print("!!! " + problem)
    return problem


def build_chrome(user_agent=None):
    """Sakht-e driver-e Chrome (ba driver-e dasti dar soorat-e tanzim-e CHROMEDRIVER_PATH)."""
    options = Options()
    # baraye ejra bedun-e namayesh-e panjere, khat-e zir ra az comment kharej konid:
    # options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")

    resolved_driver = resolve_chromedriver()
    check_driver_compat(resolved_driver)

    if MOBILE_EMULATION:
        # Emulation-e mobile-e Chrome (daghighan hamon-e DevTools ba resolution-e mobile).
        # deviceMetrics viewport va tarakom-e pixel ra mobile mikonad va userAgent ham
        # UA-ye gooshi ra tanzim mikonad ta site naskhe-ye mobile ra load konad.
        mobile_emulation = {"deviceMetrics": MOBILE_METRICS}
        if user_agent:
            mobile_emulation["userAgent"] = user_agent
        options.add_experimental_option("mobileEmulation", mobile_emulation)
        # dar halat-e mobile panjere-ra maximize nemikonim ta viewport dast-nakhorde bemanad.
        options.add_argument(window_size_arg())
    else:
        options.add_argument("--start-maximized")
        if user_agent:
            options.add_argument(f"--user-agent={user_agent}")

    if CHROMEDRIVER_PATH:
        service = ChromeService(executable_path=CHROMEDRIVER_PATH)
        return webdriver.Chrome(service=service, options=options)
    return webdriver.Chrome(options=options)


def build_edge(user_agent=None):
    """Sakht-e driver-e Edge be onvan-e jaygozin (driver-ash ma'moolan dar Iran ghabel-e download ast)."""
    from selenium.webdriver.edge.options import Options as EdgeOptions
    options = EdgeOptions()
    # options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")

    if MOBILE_EMULATION:
        # Edge ham Chromium ast va hamon mobileEmulation ra pshtibani mikonad.
        mobile_emulation = {"deviceMetrics": MOBILE_METRICS}
        if user_agent:
            mobile_emulation["userAgent"] = user_agent
        options.add_experimental_option("mobileEmulation", mobile_emulation)
        options.add_argument(window_size_arg())
    else:
        options.add_argument("--start-maximized")
        if user_agent:
            options.add_argument(f"--user-agent={user_agent}")

    return webdriver.Edge(options=options)


def build_driver(user_agent=None):
    """
    Sakht-e driver-e morurgar ba modiriat-e khata-ye download-e driver.
    Ebteda Chrome, va dar soorat-e shekast be tor-e khodkar Edge emtehan mishavad.
    Zoom-e site (SITE_ZOOM) roo-ye har navigation e'mal mishavad va scroll-e
    dasti (drag ba mouse) baraye baznagari-ye karbar faal mishavad.
    """
    def prepare(driver):
        return install_manual_scroll(install_site_zoom(driver))

    if USE_EDGE:
        return prepare(build_edge(user_agent))

    try:
        return prepare(build_chrome(user_agent))
    except Exception as e:
        print("!!! Sakht-e driver-e Chrome namovaffagh bood:")
        print(f"    {e}")
        print(">>> Talash baraye estefade az morurgar-e Edge be onvan-e jaygozin ...")
        try:
            return prepare(build_edge(user_agent))
        except Exception as e2:
            print("!!! Sakht-e driver-e Edge ham namovaffagh bood:")
            print(f"    {e2}")
            raise SystemExit(
                "\n=== Hich driver-i dar dastres nist. Rah-hal-ha ===\n"
                "  1) VPN/proxy ra roshan konid ta Selenium betavanad driver ra download konad.\n"
                "  2) ya chromedriver motenaseb ba version-e Chrome khod ra dasti download konid,\n"
                "     va dar bala-ye file meghdar-e CHROMEDRIVER_PATH ra tanzim konid.\n"
            )


def open_site(user_agent=None):
    """Morurgar ra ba User-Agent-e dade shode baz mikonad, site ra load mikonad va (driver, wait) barmigardanad."""
    driver = build_driver(user_agent)
    wait = WebDriverWait(driver, 20)
    driver.get(URL)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CAPTCHA_CSS)))
    human_delay(2.0)  # kami sabr-e ezafe baraye etminan az load-e kamel-e safhe
    return driver, wait


def diagnose_refresh(driver, wait, rounds: int = 10, delay: float = 3.0) -> None:
    """
    Tabe-ye tashkhis: 10 bar (ya har cheghadr ke `rounds` bashad) dokme-ye refresh
    ra mizanad va JOZIYAAT-e daghigh ra chap mikonad ta bebinim daghigh chi mishavad:

      - Aya dokme-ye refresh peyda mishavad?
      - Aya `src` ba'd az click taghir mikonad?
      - Aya `fingerprint` (asar-e angosht-e pixel) taghir mikonad?
      - Chand ravesh-e click emtehan shod?

    In tabe HICH aks-i zakhire nemikonad — faghat baraye eshkalyabi ast.
    """
    print("=" * 70)
    print(f">>> DIAGNOSE MODE: {rounds} bar refresh ba {delay}s ta'khir")
    print("=" * 70)

    # aval dokme ra peyda konim va etela'at-esh ra chap konim
    try:
        btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, REFRESH_CSS)))
        btn_info = driver.execute_script(
            """
            const b = arguments[0];
            const r = b.getBoundingClientRect();
            return {
                tag: b.tagName,
                href: b.getAttribute('href'),
                onclick: b.getAttribute('onclick'),
                wicketpath: b.getAttribute('wicketpath'),
                classes: b.className,
                visible: !!(r.width && r.height),
                x: r.x, y: r.y, w: r.width, h: r.height,
                displayed: getComputedStyle(b).display,
            };
            """,
            btn,
        )
        print(f"[dokme peyda shod] {btn_info}")
    except Exception as e:
        print(f"[KHATA] Dokme-ye refresh peyda nashod: {e}")
        return

    # captcha-ye avval
    captcha = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, CAPTCHA_CSS)))
    prepare_captcha_for_shot(driver, captcha)
    prev_src = captcha.get_attribute("src")
    prev_fp = _captcha_fingerprint(driver, captcha)
    print(f"[shoro'] src={_short(prev_src)} fp={prev_fp[:12]}...")
    print("-" * 70)

    for i in range(1, rounds + 1):
        print(f"\n--- Refresh #{i} ---")
        try:
            btn = driver.find_element(By.CSS_SELECTOR, REFRESH_CSS)
            fire_refresh_events(driver, btn)
            print(f"  ravesh-e click: fire_refresh_events (touch+pointer+mouse dispatch)")
        except Exception as e:
            print(f"  [KHATA] fire_refresh_events namovaffagh: {e}")
            continue

        # sabr baraye taghir
        time.sleep(delay)

        # halat-e jadid ra bekhon
        try:
            new_cap = driver.find_element(By.CSS_SELECTOR, CAPTCHA_CSS)
            new_src = new_cap.get_attribute("src")
            new_fp = _captcha_fingerprint(driver, new_cap)
        except Exception as e:
            print(f"  [KHATA] natavanestim captcha-ye jadid ra bekhaanim: {e}")
            continue

        src_changed = (new_src != prev_src)
        fp_changed = (new_fp and prev_fp and new_fp != prev_fp)

        print(f"  src ghabli:  {_short(prev_src)}")
        print(f"  src jadid:   {_short(new_src)}      {'AVAZ SHOD' if src_changed else 'THABIT'}")
        print(f"  fp ghabli:   {prev_fp[:16] if prev_fp else '(khali)'}")
        print(f"  fp jadid:    {new_fp[:16] if new_fp else '(khali)'}   {'AVAZ SHOD' if fp_changed else 'THABIT'}")

        if src_changed or fp_changed:
            print(f"  ==> Captcha VAGHE'AN avaz shod")
        else:
            print(f"  ==> Captcha AVAZ NASHOD (na src na pixel)")

        prev_src = new_src
        prev_fp = new_fp

    print("\n" + "=" * 70)
    print(">>> Payan-e diagnose")
    print("=" * 70)


def _short(s: str, n: int = 60) -> str:
    """Roshte-ye tulani ra kootah mikonad baraye chap-e tamiztar."""
    if s is None:
        return "(None)"
    if len(s) <= n:
        return s
    return s[:n // 2] + "..." + s[-(n // 2 - 3):]


def fire_refresh_events(driver, refresh_btn) -> None:
    """
    Event-ha-ye touch/pointer/mouse ra be tor-e mostaghim be dokme-ye refresh
    dispatch mikonad.

    Cherā: dar halat-e mobile-emulation, `refresh_btn.click()`-e Selenium roo-ye
    in dokme kar NEMIKONAD (tashkhis-e amali roo-ye site neshan dad ke 9 az 10
    bar bi-asar bood). Sayet Wicket-e irica roo-ye event-ha-ye touch/pointer
    goosh midahad na click-e mouse-i-ye Selenium.

    In tabe hameye zanjire-ye event-ha-ye zaruri (touch + pointer + mouse) ra
    ba mokhtasat-e vaghe'i-ye dokme dispatch mikonad ta site fekr konad
    angoshtan-i vaghe'i roo-ye safhe zade shod.
    """
    driver.execute_script(
        """
        const b = arguments[0];
        const r = b.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const common = {bubbles: true, cancelable: true, view: window,
                        clientX: cx, clientY: cy};

        // 1) Touch events — mohem-tarin baraye Wicket-e mobile
        try {
            const t = new Touch({identifier: 1, target: b, clientX: cx, clientY: cy,
                                 pageX: cx, pageY: cy, radiusX: 1, radiusY: 1});
            const tOpts = {bubbles: true, cancelable: true, view: window,
                           touches: [t], targetTouches: [t], changedTouches: [t]};
            b.dispatchEvent(new TouchEvent('touchstart', tOpts));
            b.dispatchEvent(new TouchEvent('touchend',
                {...tOpts, touches: [], targetTouches: []}));
        } catch (e) { /* TouchEvent momken ast dar hameye halat-ha modern nabashad */ }

        // 2) Pointer events (jaygozin-e modern-e touch)
        try {
            b.dispatchEvent(new PointerEvent('pointerdown',
                {...common, pointerType: 'touch', pointerId: 1, isPrimary: true}));
            b.dispatchEvent(new PointerEvent('pointerup',
                {...common, pointerType: 'touch', pointerId: 1, isPrimary: true}));
        } catch (e) {}

        // 3) Mouse events + click (fallback baraye handler-ha-ye mouse-i)
        b.dispatchEvent(new MouseEvent('mousedown', common));
        b.dispatchEvent(new MouseEvent('mouseup', common));
        b.dispatchEvent(new MouseEvent('click', common));
        """,
        refresh_btn,
    )


def click_refresh(driver, wait, captcha_element, max_attempts: int = 5,
                  per_attempt_timeout: float = 3.0):
    """Dokme-ye refresh-e captcha ra mizanad va sabr mikonad ta captcha avaz shavad.

    Ravesh: dispatch-e mostaghim-e touch/pointer/mouse events (na
    `refresh_btn.click()`-e Selenium ke dar mobile-emulation kar nemikonad).

    Nokte-ye mohem: ba'd az avvalin load-e safhe, Wicket ta chand saniye
    handler-ha ra kamel bind nemikonad. Pas TA `max_attempts` bar
    dispatch mikonim ta yeki asar konad. Har tekrar ta `per_attempt_timeout`
    saniye sabr mikonad.

    Barmigardanad: True agar captcha vaghe'an avaz shod, False agar hameye
    talash-ha bi-natije mand.
    """
    old_snap = snapshot_captcha(driver, captcha_element)

    for attempt in range(1, max_attempts + 1):
        try:
            refresh_btn = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, REFRESH_CSS))
            )
            fire_refresh_events(driver, refresh_btn)
        except Exception:
            return False

        if wait_for_new_captcha(driver, old_snap, timeout=per_attempt_timeout):
            return True
        # avaz nashod — Wicket hanooz bind nashode; dobare emtehan mikonim
        if attempt < max_attempts:
            print(f"      (refresh #{attempt} bi-asar bood, dobare emtehan mikonam)")

    return False


def ensure_real_captcha(driver, wait, refresh_delay, max_reload: int = 5):
    """
    Motmaen mishavad ke captcha-ye roo-ye safhe VAGHE'I ast, na kadr-e KHALI.

    In site ba avvalin voroud ya ba'zi reload-ha captcha ra namayesh nemidahad
    (faghat yek kadr-e sefid). Rahborde ma (tabegh-e raftar-e karbar-e vaghe'i):
    agar captcha KHALI bood, MOSTAGHIM koll-e safhe ra reload mikonim (na click-e
    refresh — chon dar in halat dokme-ye refresh ham asari nadarad va faghat
    reload-e safhe kar mikonad). Ta `max_reload` bar tekrar mishavad.

    Element-e captcha-ye amade (va vaghe'i) ra barmigardanad.
    """
    def current_captcha():
        el = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, CAPTCHA_CSS))
        )
        prepare_captcha_for_shot(driver, el)
        return el

    captcha = current_captcha()

    # agar tashkhis-e khali gheyr-fa'al ast, hich kari nemikonim
    if MIN_DARK_RATIO is None:
        return captcha

    for reload_i in range(1, max_reload + 1):
        if not is_captcha_empty(driver, captcha):
            return captcha  # captcha-ye vaghe'i amade ast
        print(f"      (captcha khali bood -> reload-e safhe #{reload_i})")
        driver.get(URL)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CAPTCHA_CSS)))
        human_delay(2.0)
        captcha = current_captcha()

    # ba'd az hame-ye reload-ha; har chi hast bargardan (momken ast baz ham khali bashad)
    if is_captcha_empty(driver, captcha):
        print(f"      (hoshdar: ba'd az {max_reload} reload captcha hanooz khali ast)")
    return captcha


def main():
    # 0) Parse-e argument-ha (baraye ejra bedun-e voroodi-ye dasti)
    parser = argparse.ArgumentParser(
        description="Danalod-e daste-jami-ye captcha az safhe-ye sabt-e IMEI-ye irica"
    )
    parser.add_argument("-n", "--count", type=int, default=None,
                        help="te'dad-e aks (agar nadid, soaal mishavad)")
    parser.add_argument("-d", "--delay", type=float, default=None,
                        help="ta'khir-e paaye ba'd az refresh (saniye); pishfarz 3")
    parser.add_argument("--start", type=int, default=None,
                        help="shomare-ye shoru' baraye naam-e file (edame-ye dataset-e ghabli)")
    parser.add_argument("--restart-every", type=int, default=None,
                        help="har chand aks yek bar morurgar restart shavad "
                             "(0 = gheyr-faal; agar nadid, soaal mishavad)")
    parser.add_argument("--diagnose", action="store_true",
                        help="halat-e tashkhis: 10 bar refresh mizanad va joziyaat ra chap mikonad "
                             "(hich aksi zakhire nemishavad). baraye eshkalyabi-ye dokme-ye refresh.")
    parser.add_argument("--diagnose-rounds", type=int, default=10,
                        help="te'dad-e refresh-ha dar halat-e diagnose (pishfarz 10)")
    parser.add_argument("--diagnose-delay", type=float, default=3.0,
                        help="ta'khir bein-e refresh-ha dar halat-e diagnose (pishfarz 3 saniye)")
    args = parser.parse_args()

    print(f">>> Site-e hadaf: {URL}")

    # Halat-e tashkhis (diagnose): faghat 10 bar refresh miznad va joziyaat chap mishavad
    if args.diagnose:
        current_ua = USER_AGENTS[0]
        print(f">>> DIAGNOSE MODE — baz kardan-e morurgar ...")
        driver, wait = open_site(current_ua)
        try:
            diagnose_refresh(driver, wait,
                             rounds=args.diagnose_rounds,
                             delay=args.diagnose_delay)
        finally:
            try:
                driver.quit()
            except Exception:
                pass
        return

    # 1) Gereftan-e te'dad-e tekrar va zaman-e ta'khir (CLI ya soaal az karbar)
    repeat_count = args.count if args.count is not None else ask_repeat_count()
    refresh_delay = args.delay if args.delay is not None else ask_refresh_delay()

    # Restart-e dore-i-ye morurgar (ekhtiyari). agar az CLI dade nashode, soaal mishavad.
    if args.restart_every is not None:
        restart_enabled = args.restart_every > 0
        restart_every = args.restart_every
    else:
        restart_enabled, restart_every = ask_periodic_restart()
    if restart_enabled:
        print(f">>> Restart-e dore-i faal ast: har {restart_every} aks yek bar.")
    else:
        print(">>> Restart-e dore-i gheyr-faal ast (faghat refresh-e captcha).")

    # Sakht-e poushe-ye khorooji dar soorat-e naboodan
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Agar --start dade nashode, khodkar ba'd az akharin file-e mojood edame bede
    # (ta file-ha rooye ham nanevisand).
    if args.start is not None:
        start_num = args.start
    else:
        existing = [f for f in os.listdir(OUTPUT_DIR) if f.startswith("captcha_")]
        nums = []
        for f in existing:
            try:
                nums.append(int(f.replace("captcha_", "").replace(".png", "")))
            except ValueError:
                pass
        start_num = (max(nums) + 1) if nums else 1
    print(f">>> Shoru' az shomare {start_num}, te'dad {repeat_count} aks.")

    # Avvalin User-Agent ra entekhab va morurgar ra baz mikonim
    ua_index = 0
    current_ua = USER_AGENTS[ua_index]
    print(f">>> Baz kardan-e morurgar ba agent #{ua_index + 1} ...")
    driver, wait = open_site(current_ua)

    try:
        # 6) Halghe-ye asli
        for i in range(1, repeat_count + 1):
            file_num = start_num + i - 1
            try:
                # 3) Peyda kardan va zakhire-ye aks-e captcha.
                #    Ghabl az zakhire motmaen mishavim captcha VAGHE'I ast (na kadr-e
                #    khali); dar soorat-e khali budan, refresh/reload mishavad.
                captcha = ensure_real_captcha(driver, wait, refresh_delay)

                file_name = f"captcha_{file_num}.png"
                file_path = os.path.join(OUTPUT_DIR, file_name)

                # Screenshot ba hashiye-ye ezafe az har taraf (ta kenare-ha boride nashavad)
                save_captcha_with_padding(driver, captcha, file_path)

                print(f"Aks {i} az {repeat_count} zakhire shod -> {file_path}")

                # Agar akharin tekrar nist, aks-e ba'di ra amade kon
                if i < repeat_count:
                    if restart_enabled and i % restart_every == 0:
                        # Har RESTART_EVERY aks: morurgar ra kamel mibandim va ba
                        # yek agent-e jadid va session-e taze dobare baz mikonim.
                        # (in kar cookie-ha va fingerprint ra reset mikonad ta
                        #  server natavanad be khatere spam mased konad)
                        ua_index = (ua_index + 1) % len(USER_AGENTS)
                        current_ua = USER_AGENTS[ua_index]
                        print(f">>> Restart-e morurgar ba'd az {i} aks (agent-e jadid #{ua_index + 1}) ...")
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        human_delay(2.0)  # kami sabr bein-e baste va baz shodan
                        driver, wait = open_site(current_ua)
                        human_delay(refresh_delay)
                    else:
                        # 4) Refresh-e captcha: click_refresh ta 5 bar talash
                        #    mikonad ta captcha vaghe'an avaz shavad (Wicket
                        #    dar avvalin refresh-ha ba'd az load-e safhe momken
                        #    ast handler-ha ra hanooz bind nakarde bashad).
                        changed = click_refresh(driver, wait, captcha)
                        if not changed:
                            print(f"      (hoshdar: ba'd az 5 talash captcha "
                                  f"avaz nashod; shayad bayad safhe reload shavad)")
                        # kami sabr-e ezafe-ye shabih-e ensan (jitter) baraye tabi'i budan
                        human_delay(refresh_delay)

            except Exception as e:
                # Modiriat-e khata dar sath-e har tekrar ta kol-e barname motevaghef nashavad
                print(f"Khata dar tekrar {i}: {e}")
                # Ehtemalan safhe freeze shode; morurgar ra restart mikonim va edame midahim
                print(">>> Talash baraye restart-e morurgar ba'd az khata ...")
                try:
                    driver.quit()
                except Exception:
                    pass
                human_delay(2.0)
                try:
                    ua_index = (ua_index + 1) % len(USER_AGENTS)
                    current_ua = USER_AGENTS[ua_index]
                    driver, wait = open_site(current_ua)
                except Exception as e2:
                    print(f"Restart ham namovaffagh bood: {e2}")
                    human_delay(2.0)
                continue

        print("Payan-e kar. Hame-ye aks-ha dar poushe-ye 'captchas' zakhire shodand.")

    except Exception as e:
        print(f"Khata-ye kolli dar ejra-ye barname: {e}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
