# -*- coding: utf-8 -*-
"""
tst_easy_gomrok.py — ravesh-e "asan" baraye por kardan-e form-e irica.

IDE-ye asli (kashf-e karbar, ba tst-e dasti):
    Site-e gomrak ba Apache Wicket sakhte shode va HALAT-e form ra samt-e SERVER
    (session) negah midarad. Pas be-jaye jang ba AJAX-e radif-haye IMEI (ke flaky
    bud), yek-bar hame-chiz ra migozarim va faghat captcha ra hal mikonim:

      1. Site ra DORUST baz kon (open + captcha-ye salem).
      2. Source-e amade (src1.txt) ra jaygozin-e DOM kon.
      3. Etela'at-e profile-e moshtari (national/birth/imei/confirm) ra tush
         jaygozin kon.
      4. Yek-bar F5 (refresh). Wicket safhe ra ZENDE ba etela'at-e fe'li barmigardanad.
      5. Faghat captcha ra hal kon va (ekhtiyari) beraw marhale-ye ba'd.

    HOSHDAR: in barnamе be site-e vaghe'i vasl mishavad; sandbox-e man nemitavanad
    ejraye zende konad. Test-haye offline (parse, jaygozini-ye value) anjam shode,
    vali ejraye zende ra bayad KHODET bezani.

Estefade:
    python tst_easy_gomrok.py                 # az inputs.json + src1.txt
    python tst_easy_gomrok.py --submit        # ba zadan-e 'edame'
    python tst_easy_gomrok.py --debugging     # ba n0var-e Next action
    python tst_easy_gomrok.py --src OTHER.txt # source-e digar
"""
import argparse
import os
import time

# az gomrok.py estefade-ye mojaddad mikonim (import main()-ash ra ejra nemikonad
# chon guard-e __name__ darad).
import gomrok as g
import captcha_collector as cc
from selenium.webdriver.common.by import By

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_DEFAULT = os.path.join(_HERE, "src1.txt")

# name-attribute-haye field-ha (paydar-tar az wicketpath baraye set kardan ba JS,
# chon Wicket wicketpath ra sabet negah midarad vali id-ha avaz mishavand).
NAME_NATIONAL = "imeiStep:passengerNationalCodeField"
NAME_BIRTH    = "imeiStep:passengerBirthDatePanel:input-append:date"
NAME_CONFIRM  = "imeiStep:confirmCheck"
# IMEI-ha: name mesl 'imeiStep:imeiListView:0:codeField'
CSS_IMEI = 'input[name^="imeiStep:imeiListView:"][name$=":codeField"]'


def load_html(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def inject_source(driver, html, dbg=None):
    """Marhale 2: source-e amade ra jaygozin-e DOM mikonad.

    az documentElement.innerHTML estefade mikonim (na document.write ke ba'd az
    load momken ast script-ha ra dobare ejra konad/kharab konad). faghat mohtava-ye
    <html> ra migozarim."""
    if dbg:
        dbg.step("inject: jaygozini-ye source-e amade (src1) dar DOM")
    # <html ...> ... </html> -> faghat dakhelesh ra migozarim
    import re
    m = re.search(r"<html[^>]*>(.*)</html>", html, re.S | re.I)
    inner = m.group(1) if m else html
    driver.execute_script("document.documentElement.innerHTML = arguments[0];", inner)
    time.sleep(0.5)


def fill_profile(driver, national, birth, imeis, confirm, dbg=None):
    """Marhale 3: etela'at-e profile ra dar DOM-e tazrigh-shode jaygozin mikonad
    (bar asas-e name-attribute). event-haye input/change ham dispatch mishavad ta
    agar Wicket listener-i dashte bashad khabardar shavad."""
    if dbg:
        dbg.step(f"fill: national={national}, birth={birth}, imeis={imeis}, confirm={confirm}")

    js = r"""
    const nat = arguments[0], birth = arguments[1], imeis = arguments[2],
          confirm = arguments[3];
    function setVal(el, v){
      if(!el) return false;
      el.value = v;
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return true;
    }
    const report = {national:false, birth:false, imeis:[], confirm:false};
    report.national = setVal(document.querySelector('[name="%(NAT)s"]'), nat);
    report.birth    = setVal(document.querySelector('[name="%(BIRTH)s"]'), birth);
    const rows = document.querySelectorAll('%(IMEI)s');
    for(let i=0;i<imeis.length;i++){
      report.imeis.push(i < rows.length ? setVal(rows[i], imeis[i]) : false);
    }
    const cb = document.querySelector('[name="%(CONFIRM)s"]');
    if(cb){ cb.checked = !!confirm;
            cb.dispatchEvent(new Event('change',{bubbles:true}));
            report.confirm = true; }
    return report;
    """ % {"NAT": NAME_NATIONAL, "BIRTH": NAME_BIRTH, "IMEI": CSS_IMEI,
           "CONFIRM": NAME_CONFIRM}

    report = driver.execute_script(js, national, birth, imeis, bool(confirm))
    print(f">>> fill_profile report: {report}")
    # hoshdar agar radif-e IMEI kam bud
    n_rows = driver.execute_script(
        f"return document.querySelectorAll('{CSS_IMEI}').length;")
    if n_rows < len(imeis):
        print(f">>> HOSHDAR: source faghat {n_rows} radif-e IMEI darad vali "
              f"{len(imeis)} IMEI dade shode. src1.txt bayad be-tedad-e IMEI radif "
              f"dashte bashad.")
    return report


def refresh_page(driver, wait, dbg=None):
    """Marhale 4: F5. Wicket safhe-ra ZENDE ba etela'at-e session barmigardanad."""
    if dbg:
        dbg.step("F5: refresh-e safhe (Wicket state-ra zende barmigardanad)")
    driver.refresh()
    # sabr ta safhe-ye zende + captcha-ye salem
    cc.ensure_real_captcha(driver, wait, 0.8)


def solve_captcha(driver, solver, dbg=None):
    """Marhale 5: captcha-ye ZENDE ra mikhanad, dar field migozarad va verify mikonad."""
    if dbg:
        dbg.step("captcha: khandan ba model + gozashtan + verify")
    captcha = driver.find_element(By.CSS_SELECTOR, cc.CAPTCHA_CSS)
    cc.prepare_captcha_for_shot(driver, captcha)
    crop = cc.crop_captcha_with_padding(driver, captcha)
    answer = solver.read_pil(crop)
    ok = g.type_and_verify(driver, g.WP_CAPTCHA, answer)
    # zakhire-ye axe baraye moghayese-ye cheshmi
    shot_path = os.path.join(_HERE, f"_last_captcha_{answer}.png")
    try:
        crop.save(shot_path)
        print(f">>> Axe-e captcha zakhire shod: {shot_path}")
    except Exception as e:
        print(f">>> (zakhire-ye axe na-shod: {e})")
    print(f">>> Javab-e captcha (model): {answer!r} | dar field neshast: {ok}")
    return answer, ok


def click_continue(driver, dbg=None):
    if dbg:
        dbg.step("click: dokme-ye 'edame' (SUBMIT) — zud, captcha monghazi mishavad!")
    btn = g.find(driver, g.WP_CONTINUE)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    try:
        btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", btn)


def main():
    ap = argparse.ArgumentParser(
        description="Ravesh-e asan: tazrigh-e source + F5 + hal-e captcha")
    ap.add_argument("--national")
    ap.add_argument("--birth")
    ap.add_argument("--imei", action="append", default=None)
    ap.add_argument("--src", default=_SRC_DEFAULT, help="masir-e source-e amade")
    ap.add_argument("--ckpt", default=g._DEFAULT_CKPT)
    ap.add_argument("--no-confirm-check", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--debugging", action="store_true")
    args = ap.parse_args()

    panel = g.load_panel_inputs()
    national = args.national or panel.get("national")
    birth    = args.birth    or panel.get("birth")
    imeis = args.imei or panel.get("imeis") or []
    imeis = [str(x).strip() for x in imeis if x and str(x).strip()]
    confirm = not args.no_confirm_check and not panel.get("no_confirm_check")
    if not args.submit and panel.get("submit"):
        args.submit = True
    if not args.debugging and panel.get("debugging"):
        args.debugging = True

    print(f">>> Vorudi-ha: melli={national!r} birth={birth!r} imeis={imeis} "
          f"confirm={confirm} submit={args.submit} debugging={args.debugging}")
    if not (national and birth and imeis):
        print(">>> KHATA: vorudi-ha kamel nist. Aval panel.py ra por/zakhire kon.")
        return
    if not os.path.exists(args.src):
        print(f">>> KHATA: source peyda nashod: {args.src}")
        return

    html = load_html(args.src)
    print(f">>> source load shod: {args.src} ({len(html)} char)")

    print(">>> Load-e model ...")
    solver = g.load_solver(ckpt=args.ckpt, device="cpu")

    MAX_TRY = 6
    driver = None
    for attempt in range(1, MAX_TRY + 1):
        try:
            ua = cc.USER_AGENTS[(attempt - 1) % len(cc.USER_AGENTS)]
            print(f">>> [talash {attempt}/{MAX_TRY}] Baz kardan-e site ...")
            driver, wait = cc.open_site(ua)
            dbg = g.DebugController(driver, args.debugging)

            print(">>> Sabr baraye captcha-ye salem (safhe-ye avvaliye) ...")
            cc.ensure_real_captcha(driver, wait, 0.8)
            dbg.step("safhe-ye avvaliye amade shod — shoru'-e ravesh-e asan")

            # marhale 2 + 3: tazrigh-e source va por kardan-e profile
            inject_source(driver, html, dbg=dbg)
            fill_profile(driver, national, birth, imeis, confirm, dbg=dbg)

            # marhale 4: F5
            refresh_page(driver, wait, dbg=dbg)

            # ba'd az F5 verify kon ke etela'at moond (agar Wicket state negah dashte)
            def val_by_name(name):
                return driver.execute_script(
                    "var e=document.querySelector('[name=\"'+arguments[0]+'\"]');"
                    "return e? (e.value||'') : null;", name)
            imei_now = driver.execute_script(
                f"return Array.from(document.querySelectorAll('{CSS_IMEI}'))"
                f".map(e=>e.value);")
            print(f">>> BA'D az F5 -> melli={val_by_name(NAME_NATIONAL)!r} "
                  f"birth={val_by_name(NAME_BIRTH)!r} imeis={imei_now}")

            # marhale 5: captcha
            answer, cap_ok = solve_captcha(driver, solver, dbg=dbg)

            print("\n>>> Amade shod. javab-e captcha =", repr(answer))
            if not args.submit:
                print(">>> Dokme-ye 'edame' ZADE NASHOD (--submit dade nashode).")
                print(">>> Panjere baz mimanad; Enter bezan ta baste shavad.")
                try:
                    input()
                except EOFError:
                    time.sleep(300)
                break

            click_continue(driver, dbg=dbg)
            time.sleep(4)
            page_shot = os.path.join(_HERE, "_after_submit_easy.png")
            try:
                driver.save_screenshot(page_shot)
                print(f">>> screenshot-e ba'd az submit: {page_shot}")
            except Exception:
                pass
            print(">>> Enter bezan ta baste shavad.")
            try:
                input()
            except EOFError:
                time.sleep(300)
            break

        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            print(f">>> [talash {attempt}] khata: {msg}")
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            driver = None
            time.sleep(2)
            print(">>> Restart-e morurgar va talash-e dobare ...")

    if driver:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
