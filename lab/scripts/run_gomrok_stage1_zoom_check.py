"""Ejra-ye VAGHE'I-ye gomrok.py ta MARHALE-1 + andaze-giri-ye zoom.

Chera in wrapper: gomrok.py QOFL ast (md5 sabet). pas an-ra dast nemizanim va
faghat `cc.ensure_real_captcha` ra HOOK mikonim ta dar hamon lahze-ye marhale-1
roo-ye SITE-e VAGHE'I zoom + andaze-ye captcha ra bekhanim va dar JSON benevisim.

PITFALL: `--submit` ra nadadan KAFI NIST — gomrok.py switch-e `submit`-e
PROFILE-e panel ra override mikonad (`if not args.submit and panel.get("submit")`).
Pas ba default (`--stop-after-probe`) foran ba'd az probe morurgar baste mishavad
va hich chizi ersal NEMISHAVAD. ba `--continue-run` masir-e adi edame miyabad.

Ejra:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/scripts/run_gomrok_stage1_zoom_check.py --profile-id 01bdffce

Khorooji: lab/final/gomrok_stage1_zoom.json  (+ screenshot-e safhe)
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT_DIR = os.path.join(ROOT, "lab", "final")
OUT_JSON = os.path.join(OUT_DIR, "gomrok_stage1_zoom.json")
OUT_PNG = os.path.join(OUT_DIR, "gomrok_stage1_zoom.png")

PROBE_JS = r"""
var d = document.documentElement;
var cap = document.querySelector('img.captchaImage');
var btn = document.querySelector('[wicketpath="form_imeiStep_continueButton"]')
       || document.querySelector('button, input[type=submit]');
function rect(el){ if(!el) return null; var r=el.getBoundingClientRect();
  return {w:+r.width.toFixed(2), h:+r.height.toFixed(2), x:+r.left.toFixed(2), y:+r.top.toFixed(2)}; }
function cssw(el){ return el ? parseFloat(getComputedStyle(el).width) : null; }
return {
  url: location.href,
  htmlZoom: getComputedStyle(d).zoom,
  zoomStyleTagPresent: !!document.getElementById('irica-site-zoom'),
  captchaZoom: cap ? getComputedStyle(cap).zoom : null,
  captchaNatural: cap ? [cap.naturalWidth, cap.naturalHeight] : null,
  captchaRect: rect(cap),
  buttonRect: rect(btn),
  buttonCssWidth: cssw(btn),
  viewport: [window.innerWidth, window.innerHeight],
  scrollWH: [d.scrollWidth, d.scrollHeight],
  clientWH: [d.clientWidth, d.clientHeight],
  dpr: window.devicePixelRatio,
  bodyScrollable: d.scrollWidth > d.clientWidth + 2
};
"""


class ProbeDone(Exception):
    """Ba'd az probe ejra ra ghat mikonad ta hich chizi ersal nashavad."""


def _release_profile(G):
    """os._exit atexit-ha ra seda NEMIZANAD, pas profile-ra dasti azad mikonim
    ta 'busy' nachasbad."""
    try:
        if getattr(G, "_CLAIMED_PROFILE_ID", ""):
            G._release_panel_profile(G._CLAIMED_PROFILE_ID, G._CLAIMED_RUNNER_ID)
    except Exception as exc:
        print(f">>> (release-e profile na-shod: {exc})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-id", required=True)
    ap.add_argument("--continue-run", action="store_true",
                    help="ba'd az probe masir-e adi-ye gomrok edame yabad "
                         "(momken ast submit-e profile fa'al bashad)")
    ap.add_argument("--hold", type=float, default=0.0,
                    help="chand sanie ba'd az probe morurgar baz bemanad")
    args, rest = ap.parse_known_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    for path in (OUT_JSON, OUT_PNG):
        if os.path.exists(path):
            os.remove(path)

    import captcha_collector as cc
    import gomrok as G  # noqa: F401  (gomrok.py dast-nakhorde bargozari mishavad)

    original = cc.ensure_real_captcha
    state = {"done": False}

    def hooked(driver, wait, refresh_delay, max_reload=5):
        element = original(driver, wait, refresh_delay, max_reload=max_reload)
        if not state["done"]:
            probe = None
            try:
                # element momken ast ba'd az reload-haye dakheli STALE bashad -> taze migirim
                from selenium.webdriver.common.by import By
                element = driver.find_element(By.CSS_SELECTOR, cc.CAPTCHA_CSS)
                cc.prepare_captcha_for_shot(driver, element)
                probe = driver.execute_script(PROBE_JS)
                # PITFALL: gahi safhe naghes miayad (naturalWidth=0). dar in halat
                # probe ra SABT NEMIKONIM va migozarim gomrok reload konad ta
                # dafe-ye ba'd ba captcha-ye SALEM andaze-giri konim.
                if not (probe.get("captchaNatural") or [0])[0]:
                    print(">>> [ZOOM-PROBE] captcha-ye naghes — probe be dafe-ye ba'd")
                    return element
                crop = cc.crop_captcha_with_padding(driver, element)
                probe["cropSize"] = list(crop.size)
                probe["cropDarkRatio"] = round(cc.dark_pixel_ratio(crop), 4)
                probe["captchaEmpty"] = bool(cc.is_captcha_empty(driver, element))
                probe["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(OUT_JSON, "w", encoding="utf-8") as handle:
                    json.dump(probe, handle, ensure_ascii=False, indent=2)
                try:
                    driver.save_screenshot(OUT_PNG)
                except Exception:
                    pass
                print(">>> [ZOOM-PROBE] " + json.dumps(probe, ensure_ascii=False))
                state["done"] = True
            except Exception as exc:
                print(f">>> [ZOOM-PROBE] khata: {exc}")
                return element
            if args.hold:
                time.sleep(args.hold)
            if not args.continue_run:
                # QAT'I: driver ra mibandim va foran KHAREJ mishavim ta gomrok
                # be marhale-ye submit naresad (raise-e adi ra gomrok mibalad).
                try:
                    driver.quit()
                except Exception:
                    pass
                sys.stdout.flush()
                _release_profile(G)
                os._exit(0 if os.path.isfile(OUT_JSON) else 3)
        return element

    cc.ensure_real_captcha = hooked

    # faghat ta marhale-1: probe migirim va (dar halat-e default) foran ghat mikonim
    sys.argv = ["gomrok.py", "--profile-id", args.profile_id] + rest
    try:
        G.main()
    except (SystemExit, ProbeDone):
        pass
    finally:
        cc.ensure_real_captcha = original

    if os.path.isfile(OUT_JSON):
        print(f">>> probe zakhire shod: {OUT_JSON}")
        return 0
    print(">>> probe anjam NASHOD (marhale-1 be captcha naresid)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
