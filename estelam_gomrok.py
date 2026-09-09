"""
estelam_gomrok.py
Servis-e ESTELAM-e salamat-e dastgah roo-ye HAMUN site-e gomrok (epl.irica.gov.ir).

Tafavot ba gomrok.py: INJA faghat SAFHE-ye AVVAL (imeiStep) mohem ast. Ba'd az
click-e 'edame' NOE-e KHATA ra mikhanim va hamin natije-ye estelam ast:

  - "شناسه ... دریافت شده از گمرک ورودی یافت نشد."  -> dastgah SALEM (healthy)
  - khata-haye MOVAGHAT (captcha / hamta / ertebat)  -> captcha-ye taze + click-e
    dobare dar HAMUN morurgar (aynan mesl gomrok.py)
  - khata-haye ghat'i-ye digar (qablan sabt shode / ...) -> natije = problem
  - agar safhe JELO raft (marhale-2 amad) -> ROOYDAD-e MOHEM: log + telegram

Manba'-e vorudi: estelam_store.json (panel.py an-ra minevisad):
  {"person": {"national","birth","mobile"},
   "devices": [{"id","serial1","serial2","status","result",...}]}

Estefade:
  python estelam_gomrok.py                 # hame-ye device-haye pending, tartib-e RANDOM
  python estelam_gomrok.py --device-id X   # faghat yek device
  python estelam_gomrok.py --debugging     # mekas-e marhale-i (DebugController)
"""
import argparse
import os
import random
import sys
import time

import app_paths
import captcha_collector as cc
import gomrok as G
import system_debug
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

G.ensure_utf8_stdio()

# Shared stall exception: raised by the on_attempt guard to abort the captcha
# retry loop when the site keeps rejecting our captcha (solver/site problem,
# NOT a device verdict). Classifying such runs as 'problem' was the v1.1.2 bug.
CaptchaStalled = G.CaptchaStalled

# After this many CONSECUTIVE captcha-rejected submits, give up on the device
# for this run: the captcha solver/site is in a bad streak and burning more
# attempts only wastes ~8s each while the verdict would stay 'error' anyway.
CAPTCHA_STALL_LIMIT = 12

_HERE = os.path.dirname(os.path.abspath(__file__))
_STORE_PATH = os.environ.get("IRICA_ESTELAM_STORE") or app_paths.data_path("estelam_store.json")

PAGE_LOAD_SEC = 12
CAPTCHA_WAIT_SEC = 10
OPEN_GET_TRIES = 4
SUBMIT_DEADLINE_SEC = 120
RETRY_MAX_ATTEMPTS = 25
MAX_DEVICE_TRY = 4          # restart-e morurgar baraye YEK device

# javab-e KHUB: shenase dar gomrok-e vorudi nist -> dastgah salem ast
HEALTHY_MARKERS = ["یافت نشد", "پیدا نشد"]


def _store_read():
    import json
    if not os.path.exists(_STORE_PATH):
        return {"person": {}, "devices": []}
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            return json.load(f) or {"person": {}, "devices": []}
    except Exception as e:
        print(f">>> HOSHDAR: estelam_store.json khonde nashod: {e}")
        return {"person": {}, "devices": []}


def _panel_set_device(device_id, **fields):
    try:
        import panel
        return panel.estelam_set_device(device_id, **fields)
    except Exception as e:
        print(f">>> (update-e device dar panel na-shod: {e})")
        return None


def _telegram(text):
    # timeout-e sakht: estelam nabayad rooye ersal-e telegram gir konad.
    try:
        import telegram_notify as tg
        tg.send_message(text, timeout=15)
    except Exception as e:
        print(f">>> (telegram na-raft: {e})")


_RESULT_FA = {
    "healthy": "✅ سالم — شناسه در گمرک ورودی یافت نشد",
    "problem": "⚠️ دارای اشکال",
    "advanced": "❗️ شناسه در گمرک ورودی ثبت است (رفتن به مرحله بعد)",
    "error": "⛔️ بدون پاسخ از سایت",
}


def _telegram_result(device, person, status, result_text):
    """Natije-ye estelam-e har dastgah + serial-e aval/dovom + zaman ra be telegram mifrestad."""
    s1 = str((device or {}).get("serial1") or "").strip() or "—"
    s2 = str((device or {}).get("serial2") or "").strip()
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    head = _RESULT_FA.get(status, status)
    lines = [
        "📟 نتیجه استعلام سلامت دستگاه",
        f"وضعیت: {head}",
        f"سریال اول: {s1}",
    ]
    if s2:
        lines.append(f"سریال دوم: {s2}")
    if person and person.get("national"):
        lines.append(f"کد ملی: {person.get('national')}")
    if result_text:
        lines.append(f"پیام سامانه: {result_text}")
    lines.append(f"زمان استعلام: {when}")
    _telegram("\n".join(lines))


def _elog(status, message, serial="", device_id=""):
    """Log-e servis-e estelam (panel bakhsh-e log-ash-ra mikhanad)."""
    try:
        import panel
        panel.estelam_log(status, message, step="استعلام", serial=serial,
                          device_id=device_id)
    except Exception as e:
        print(f">>> (estelam-log na-shod: {e})")


def make_stall_guard(limit=CAPTCHA_STALL_LIMIT):
    """Build the on_attempt hook for the captcha retry loop.

    Counts CONSECUTIVE captcha-rejected submits; raises CaptchaStalled when
    the streak reaches `limit`. A non-rejected attempt (site advanced, or a
    transient/connection error that cleared) resets the streak. This bounds
    doomed runs (bad solver streak / broken captcha) while keeping normal
    retry behavior intact.
    """
    streak = {"n": 0}

    def guard(attempt, answer, conf, rejected):
        if not rejected:
            streak["n"] = 0
            return
        streak["n"] += 1
        if streak["n"] >= limit:
            print(f">>> CAPTCHA STALL: {streak['n']} talash-e posht-sareh rad shod "
                  f"(answer={answer!r} conf={conf:.2f}) — talash-ha motavaghef mishavad.")
            raise CaptchaStalled(
                f"{streak['n']} consecutive captcha rejects (limit={limit})")

    return guard


def classify_outcome(messages, advanced, attempts):
    """Final verdict ladder for the inquiry (v1.1.3).

    Order matters:
      advanced  -> the page moved to step-2 (device IS registered upstream)
      healthy   -> the site's own 'not found upstream' verdict
      retryable -> captcha/transient exhaustion: the DEVICE WAS NEVER
                   EVALUATED. status='error' (re-inquirable by the next
                   periodic run) — never 'problem', which is reserved for
                   genuine site verdicts about the device. This is the fix
                   for the mislabeled 'نتیجه دارای اشکال — کد اعتبار سنجی
                   صحیح نمی باشد' log entries.
      problem   -> genuine non-retryable site verdict about the device.
    """
    err = [str(m or "") for m in (messages or [])]
    err_text = " | ".join(err)
    if advanced:
        return {"status": "advanced", "messages": err,
                "result": "رفتن به مرحله بعد — شناسه در گمرک ورودی ثبت است"}
    if is_healthy_result(err):
        return {"status": "healthy", "messages": err, "result": err_text}
    if G.is_imei_retryable_error(err):
        return {
            "status": "error",
            "messages": err,
            "result": (f"استعلام ناموفق — کپچا پس از {attempts} تلاش رد شد؛ "
                       f"دستگاه بررسی نشد (تلاش مجدد خودکار)"),
        }
    if err:
        return {"status": "problem", "messages": err, "result": err_text}
    return {"status": "error", "messages": [], "result": "بدون پاسخ از سایت"}


def _serials(device):
    out = []
    for key in ("serial1", "serial2"):
        value = str(device.get(key) or "").strip()
        if value:
            out.append(value)
    return out[:2]


# --- helper-haye safhe-ye avval (mo'adel-e nested-haye gomrok.main) --------------
def page_advanced(driver):
    return driver.execute_script(r"""
        if (document.querySelector('[wicketpath="%s"]')) return true;
        var sels = [
          '[wicketpath$="feedBack_container_error"] li',
          '[wicketpath$="feedBack_container_error"] span[wicketpath$="_message"]',
          '.feedbackPanelERROR'
        ];
        for (var s=0;s<sels.length;s++){
          var fb = document.querySelectorAll(sels[s]);
          for (var i=0;i<fb.length;i++){
            if ((fb[i].innerText||'').trim()) return true;
          }
        }
        return false;
    """ % G.WP_HAMTA_CONTINUE)


def ajax_busy(driver):
    return driver.execute_script(r"""
        var wp = arguments[0];
        var btn = document.querySelector('[wicketpath="'+wp+'"]');
        if (btn && (btn.disabled ||
            (' '+(btn.className||'')+' ').indexOf(' disabled ')>=0))
            return true;
        var ind = document.querySelectorAll(
            '.wicket-ajax-indicator,'+
            '[wicketpath="form_indicator_indicatorImg"]');
        for (var j=0;j<ind.length;j++){
            if (ind[j].offsetParent !== null) return true;
        }
        return false;
    """, G.WP_CONTINUE)


def do_click(driver):
    btn = G.find(driver, G.WP_CONTINUE)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    cc.fire_refresh_events(driver, btn)


def read_errors(driver):
    try:
        return driver.execute_script(r"""
            const els = document.querySelectorAll(
              '[wicketpath$="feedBack_container_error"] li, '+
              '[wicketpath$="feedBack_container_error"] span[wicketpath$="_message"], '+
              '.feedbackPanelERROR li, .feedbackPanelERROR');
            const msgs=[];
            els.forEach(e=>{const t=(e.innerText||'').trim();
              if(t && msgs.indexOf(t)<0) msgs.push(t);});
            return msgs;
        """) or []
    except Exception:
        return []


def clear_errors(driver):
    try:
        driver.execute_script("""
            document.querySelectorAll(
              '[wicketpath$="feedBack_container_error"] li,'+
              '[wicketpath$="feedBack_container_error"] span[wicketpath$="_message"],'+
              '.feedbackPanelERROR li,.feedbackPanelERROR').forEach(function(e){e.textContent='';});
        """)
    except Exception:
        pass


def is_healthy_result(messages):
    text = " | ".join(str(x or "") for x in (messages or []))
    return any(marker in text for marker in HEALTHY_MARKERS)


def open_site(driver, wait):
    """get/F5-e sari' ta captcha biayad. False agar site baz nashod."""
    last_err = None
    for og in range(1, OPEN_GET_TRIES + 1):
        try:
            print(f">>>   open/get #{og}/{OPEN_GET_TRIES} (timeout={PAGE_LOAD_SEC}s) ...")
            try:
                driver.get(cc.URL)
            except Exception as ge:
                last_err = ge
                print(f">>>   (get exception: {str(ge).splitlines()[0][:120]})")
                try:
                    driver.execute_script("window.stop();")
                except Exception:
                    pass
            try:
                WebDriverWait(driver, CAPTCHA_WAIT_SEC).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, cc.CAPTCHA_CSS)))
                return True
            except Exception as we:
                last_err = we
                print(">>>   captcha nayamad — F5 sari' ...")
                try:
                    driver.refresh()
                except Exception:
                    pass
        except Exception as e:
            last_err = e
    print(f">>> site baz nashod ba'd az {OPEN_GET_TRIES} get/F5: {last_err}")
    return False


def fill_page_one(driver, person, serials, no_confirm_check=False):
    G.set_field_js(driver, G.find(driver, G.WP_BIRTH), person.get("birth") or "")
    time.sleep(0.5)
    G.type_field(driver, G.find(driver, G.WP_NATIONAL), person.get("national") or "")
    time.sleep(0.35)
    G.fill_imeis(driver, serials, dbg=None, tries=5)
    if not no_confirm_check:
        G.ensure_checked(driver, G.find(driver, G.WP_CONFIRM))
        time.sleep(0.6)
    ok_imei, cur = G.verify_imeis(driver, serials)
    if not ok_imei:
        G.fill_imeis(driver, serials, dbg=None, tries=5)
        time.sleep(0.5)
        ok_imei, cur = G.verify_imeis(driver, serials)
    print(f">>> VERIFY-e serial: ok={ok_imei} cur={cur}")
    G.cleanup_extra_empty_imei_rows(driver, keep=len(serials), dbg=None)


def submit_and_wait(driver, deadline_sec=SUBMIT_DEADLINE_SEC):
    """click-e 'edame' + entezar. barmigardanad (clicked_ok, conn_error, err)."""
    clicked_ok = False
    conn_error = False
    err = []
    did_click = 0
    try:
        do_click(driver)
        did_click += 1
        print(">>> click-e 'edame' ferestade shod — montazer-e vakonesh ...")
    except Exception as ce:
        print(f">>> khata dar click-e avval: {ce}")

    idle_since = None
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        time.sleep(0.35)
        try:
            early = read_errors(driver)
            if early and any(("ارتباط" in m) or ("دریافت پاسخ" in m) or
                             ("سازمان مربوطه" in m) or ("timed out" in m.lower())
                             for m in early):
                err = early
                conn_error = True
                print(">>> ⚠ khata-ye ertebati — captcha taze + retry dar hamun safhe")
                break
        except Exception:
            pass
        try:
            if page_advanced(driver) or not driver.find_elements(
                    By.CSS_SELECTOR, f'[wicketpath="{G.WP_CONTINUE}"]'):
                clicked_ok = True
                break
        except Exception:
            pass
        try:
            busy = ajax_busy(driver)
        except Exception:
            busy = False
        if busy:
            idle_since = None
            continue
        if idle_since is None:
            idle_since = time.time()
        elif time.time() - idle_since > 2.0 and did_click < 3:
            print(f">>> vakonesh nadad -> click-e dobare ({did_click + 1})")
            try:
                do_click(driver)
                did_click += 1
            except Exception as ce:
                print(f">>> khata dar click-e dobare: {ce}")
            idle_since = None
    return clicked_ok, conn_error, err


def hamta_present(driver):
    return bool(driver.find_elements(
        By.CSS_SELECTOR, f'[wicketpath="{G.WP_HAMTA_CONTINUE}"]'))


# solve_confidently lives in gomrok.py (shared with the registration flow).
solve_confidently = G.solve_confidently


def inquire_device(driver, wait, solver, person, device, no_confirm_check=False):
    """Wrapper: estelam ra anjam midahad va natije ra be telegram mifrestad
    (serial-e aval/dovom + zaman) — har chahar masir-e natije pooshesh dade shode."""
    outcome = _inquire_device(driver, wait, solver, person, device,
                              no_confirm_check=no_confirm_check)
    try:
        _telegram_result(device, person, outcome.get("status", ""),
                         outcome.get("result", ""))
    except Exception as exc:
        print(f">>> (telegram-e natije na-raft: {exc})")
    return outcome


def _inquire_device(driver, wait, solver, person, device, no_confirm_check=False):
    """Yek device ra estelam migirad. barmigardanad dict-e natije."""
    serials = _serials(device)
    title = f"estelam:{serials[0] if serials else device.get('id')}"

    G.ensure_page_ready(driver, wait, dbg=None, max_reload=8)
    fill_page_one(driver, person, serials, no_confirm_check=no_confirm_check)

    captcha = driver.find_element(By.CSS_SELECTOR, cc.CAPTCHA_CSS)
    try:
        cc.click_refresh(driver, wait, captcha)
        captcha = driver.find_element(By.CSS_SELECTOR, cc.CAPTCHA_CSS)
    except Exception as re_:
        print(f">>> (refresh-e captcha na-shod: {re_})")
    cc.prepare_captcha_for_shot(driver, captcha)
    answer, _conf = solve_confidently(driver, captcha, solver, wait)
    G.type_and_verify(driver, G.WP_CAPTCHA, answer)
    try:
        submitted_ref = cc.snapshot_captcha(driver, captcha)
    except Exception:
        submitted_ref = None
    print(f">>> javab-e captcha = {answer!r}")

    clicked_ok, conn_error, err = submit_and_wait(driver)
    time.sleep(1.0)
    if not err:
        err = read_errors(driver)

    if err:
        print(">>> PAYAM-HA-YE SITE:")
        for m in err:
            print("     -", m.encode("unicode_escape").decode("ascii"))

    # khata-haye MOVAGHAT/captcha/hamta -> captcha-ye taze + click-e dobare
    if (conn_error or G.is_imei_retryable_error(err)) and not hamta_present(driver):
        def _prepare_form():
            fill_page_one(driver, person, serials, no_confirm_check=no_confirm_check)

        def _submit():
            clear_errors(driver)
            do_click(driver)
            deadline2 = time.time() + 20
            while time.time() < deadline2:
                time.sleep(0.35)
                try:
                    if page_advanced(driver) or not driver.find_elements(
                            By.CSS_SELECTOR, f'[wicketpath="{G.WP_CONTINUE}"]'):
                        break
                except Exception:
                    pass
                try:
                    if not ajax_busy(driver):
                        break
                except Exception:
                    pass

        def _result():
            time.sleep(0.8)
            current = read_errors(driver)
            if not current and not hamta_present(driver):
                extra_dl = time.time() + 8
                while time.time() < extra_dl:
                    time.sleep(0.4)
                    if hamta_present(driver):
                        break
                    current = read_errors(driver)
                    if current:
                        break
            advanced = hamta_present(driver)
            if not advanced:
                advanced = not bool(driver.find_elements(
                    By.CSS_SELECTOR, f'[wicketpath="{G.WP_CONTINUE}"]'))
            return current, advanced

        old_ref = submitted_ref
        if old_ref is None:
            try:
                old_ref = cc.snapshot_captcha(
                    driver, driver.find_element(By.CSS_SELECTOR, cc.CAPTCHA_CSS))
            except Exception:
                old_ref = None
        # Telemetry + bounded stall abort: count consecutive captcha rejects,
        # bail out when the streak is clearly a solver/site problem (v1.1.3).
        attempts_log = []

        def _on_attempt(attempt, answer, conf, rejected):
            attempts_log.append((attempt, answer, round(conf, 2), rejected))

        retry_err, retry_advanced, retry_count = G.retry_imei_captcha_in_place(
            driver, wait, solver, old_ref, _submit, _result,
            max_attempts=RETRY_MAX_ATTEMPTS,
            retryable=G.is_imei_retryable_error,
            prepare_form=_prepare_form,
            read_conf=solve_confidently,
            on_attempt=make_stall_guard())
        print(f">>> retry tamam: advanced={retry_advanced} attempts={retry_count}")
        rejected_n = sum(1 for _a, _ans, _c, rej in attempts_log if rej)
        last_answers = attempts_log[-3:]
        sample = "; ".join(f"#{a}={ans!r}({c:.2f},{'رد' if rej else 'خیر'})"
                           for a, ans, c, rej in last_answers)
        _elog("retry",
              f"خطای موقت — کپچای تازه و تلاش مجدد در همان مرورگر "
              f"({retry_count} تلاش، {rejected_n} رد کپچا): "
              f"{' | '.join(err or [])}",
              serial=serials[0] if serials else "", device_id=device.get("id") or "")
        _elog("info", f"جزئیات تلاش‌ها (۳ تلاش آخر): {sample}",
              serial=serials[0] if serials else "", device_id=device.get("id") or "")
        err = retry_err or ([] if retry_advanced else err)
        clicked_ok = clicked_ok or retry_advanced

    # ROOYDAD-e MOHEM: safhe raft marhale-2 (nabayad — estelam faghat safhe-ye 1)
    if hamta_present(driver):
        message = (f"⚠️ MOHEM: estelam-e serial {serials} raft SAFHE-ye BA'D "
                   f"(marhale-2/hamtaStep) — ya'ni shenase dar gomrok-e vorudi "
                   f"THABT ast va emkan-e edame-ye sabt vojood darad.")
        G.log_event("error", message, step="estelam/imeiStep",
                    title=title, national=person.get("national") or "",
                    imei=serials[0] if serials else "")
        _elog("advanced", "هشدار مهم: صفحه به مرحله بعد رفت — شناسه در گمرک ورودی ثبت است",
              serial=serials[0] if serials else "", device_id=device.get("id") or "")
        # payam-e telegram dar wrapper-e inquire_device ferestade mishavad (yek payam, na do ta)
        return {"status": "advanced", "messages": err,
                "result": "رفتن به مرحله بعد — شناسه در گمرک ورودی ثبت است"}

    err_text = " | ".join(err or [])

    # Final verdict ladder (v1.1.3): captcha/transient exhaustion is an
    # 'error' (device NOT evaluated, re-inquirable) — never a 'problem'.
    outcome = classify_outcome(err, advanced=False, attempts=retry_count if err else 1)
    status = outcome["status"]

    if status == "healthy":
        G.log_event("success", f"estelam-e SALEM: {err_text}", step="estelam/imeiStep",
                    title=title, national=person.get("national") or "",
                    imei=serials[0] if serials else "")
        _elog("healthy", f"دستگاه سالم است — {err_text}",
              serial=serials[0] if serials else "", device_id=device.get("id") or "")
        return outcome

    if status == "problem":
        G.log_event("warn", f"estelam-e natije-dar: {err_text}", step="estelam/imeiStep",
                    title=title, national=person.get("national") or "",
                    imei=serials[0] if serials else "")
        _elog("problem", f"نتیجه دارای اشکال — {err_text}",
              serial=serials[0] if serials else "", device_id=device.get("id") or "")
        return outcome

    if status == "error" and err:
        # Captcha/transient exhaustion (or a stalled captcha streak): the
        # site never evaluated the device. Neutral 'error' log + telegram;
        # the store row is re-inquirable on the next periodic run.
        G.log_event("warn", f"estelam-e NAMOVAFFAGH (device arzyabi nashod): {err_text}",
                    step="estelam/imeiStep",
                    title=title, national=person.get("national") or "",
                    imei=serials[0] if serials else "")
        _elog("error", outcome["result"],
              serial=serials[0] if serials else "", device_id=device.get("id") or "")
        return outcome

    G.log_event("warn", "estelam bi-pasokh (hich payami roo-ye safhe nayamad)",
                step="estelam/imeiStep", title=title,
                national=person.get("national") or "",
                imei=serials[0] if serials else "")
    _elog("error", "سایت پاسخی نداد (هیچ پیامی روی صفحه نیامد)",
          serial=serials[0] if serials else "", device_id=device.get("id") or "")
    return {"status": "error", "messages": [], "result": "بدون پاسخ از سایت"}


def main():
    system_debug.configure("inquiry-customs")
    ap = argparse.ArgumentParser(description="Estelam-e salamat-e dastgah (safhe-ye 1-e gomrok)")
    ap.add_argument("--device-id", help="faghat hamin device ra estelam begir")
    ap.add_argument("--ckpt", default=G._DEFAULT_CKPT)
    ap.add_argument("--debugging", action="store_true")
    ap.add_argument("--no-confirm-check", action="store_true")
    args = ap.parse_args()

    store = _store_read()
    person = store.get("person") or {}
    devices = store.get("devices") or []
    if args.device_id:
        devices = [d for d in devices if str(d.get("id")) == str(args.device_id)]
    else:
        devices = [d for d in devices
                   if str(d.get("status") or "pending") in ("pending", "error", "")]
    if not (person.get("national") and person.get("birth")):
        print(">>> KHATA: etela'at-e shakhs (kod-e melli / tarikh-e tavallod) khali ast.")
        return 2
    if not devices:
        print(">>> hich device-e pending-i baraye estelam nist.")
        return 0

    random.shuffle(devices)   # tartib-e RANDOM (darkhast-e karbar)
    print(f">>> {len(devices)} device baraye estelam (tartib-e random).")

    print(">>> Load-e model ...")
    solver = G.load_solver(ckpt=args.ckpt, device="cpu")

    driver = None
    wait = None
    try:
        for index, device in enumerate(devices, 1):
            device_id = device.get("id")
            serials = _serials(device)
            print(f"\n=== [{index}/{len(devices)}] device {device_id} serial={serials} ===")
            _panel_set_device(device_id, status="running", result="")
            _elog("running", f"شروع استعلام دستگاه {index} از {len(devices)}",
                  serial=serials[0] if serials else "", device_id=device_id or "")
            outcome = None
            for attempt in range(1, MAX_DEVICE_TRY + 1):
                try:
                    if driver is None:
                        ua = cc.USER_AGENTS[(attempt - 1) % len(cc.USER_AGENTS)]
                        driver = cc.build_driver(ua)
                        try:
                            driver.set_page_load_timeout(PAGE_LOAD_SEC)
                        except Exception:
                            pass
                        wait = WebDriverWait(driver, CAPTCHA_WAIT_SEC)
                        G.install_profile_banner(driver, f"استعلام {serials[0] if serials else ''}")
                    if not open_site(driver, wait):
                        raise RuntimeError("site baz nashod")
                    try:
                        cc.ensure_real_captcha(driver, wait, 0.8)
                    except Exception:
                        pass
                    outcome = inquire_device(driver, wait, solver, person, device,
                                             no_confirm_check=args.no_confirm_check)
                    break
                except Exception as e:
                    msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                    print(f">>> [device {device_id} talash {attempt}] khata: {msg}")
                    try:
                        if driver:
                            driver.quit()
                    except Exception:
                        pass
                    driver = None
                    if attempt >= MAX_DEVICE_TRY:
                        outcome = {"status": "error", "messages": [],
                                   "result": f"khata-ye robot: {msg}"}
                    else:
                        time.sleep(1.0)
            _panel_set_device(device_id,
                              status=(outcome or {}).get("status") or "error",
                              result=(outcome or {}).get("result") or "",
                              messages=(outcome or {}).get("messages") or [])
            print(f">>> natije-ye device {device_id}: {(outcome or {}).get('status')}")
    finally:
        try:
            if driver:
                driver.quit()
                print(">>> morurgar baste shod (estelam tamam).")
        except Exception:
            pass
        _elog("info", "پایان اجرای استعلام — مرورگر بسته شد", serial="")
        try:
            import panel
            data = panel.estelam_load()
            data["run_pid"] = 0
            panel.estelam_save(data)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
