"""
solve_live_test.py
Test-e zende-ye solver rooye site-e irica (BEDUN-E submit) — ba naskhe-ye JADID-e
collector (haman poushe).

Kar-e in script:
  1) site-e irica ra baz mikonad (haman mantegh-e mobile + crop + tashkhis-e khali
     + click_refresh-e event-based-e naskhe-ye jadid ke gir nemikonad).
  2) har captcha ra crop -> ba model (best_real.pt) mikhanad.
  3) aks ra dar poushe-ye JADID zakhire mikonad, vali NAAM-E FILE = javab-e model
     (ba prefix-e shomare ta file-ha rooye ham nanevisand):
        captchas_solved/001_g4xem.png
  4) baraye N captcha (pishfarz 100) refresh mikonad.

HICH CHIZ be site submit NEMISHAVAD — faghat aks migirad va mikhanad.

    python solve_live_test.py                 # 100 captcha -> captchas_solved/
    python solve_live_test.py -n 50 --out my_dir --delay 1.0
"""
import argparse
import os
import sys

# --- import-e toolkit-e collector-e JADID (haman poushe) ---
import captcha_collector as cc

# --- import-e solver az poushe-ye asli-ye solver/ ---
#     sakhtar:  irica/solver/{predict,live_solve}.py
#     in file:  irica/laptop/irica/solve_live_test.py  -> se sath bala ta irica/
_HERE = os.path.dirname(os.path.abspath(__file__))
_SOLVER_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "solver"))
sys.path.insert(0, _SOLVER_DIR)
from live_solve import load_solver  # noqa: E402

_DEFAULT_CKPT = os.path.join(_SOLVER_DIR, "checkpoints", "best_real.pt")


def sanitize(name: str) -> str:
    """Har chiz-e gheyr-e a-z0-9 ra hazf mikonad ta naam-e file-e salem bashad."""
    return "".join(c for c in name if c.isalnum()) or "empty"


def main():
    ap = argparse.ArgumentParser(description="Live solve test (no submit)")
    ap.add_argument("-n", "--count", type=int, default=100)
    ap.add_argument("--out", default="captchas_solved", help="poushe-ye khorooji")
    ap.add_argument("--delay", type=float, default=0.8, help="sabr ba'd az har refresh")
    ap.add_argument("--ckpt", default=_DEFAULT_CKPT)
    args = ap.parse_args()

    out_dir = os.path.join(_HERE, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(out_dir, exist_ok=True)
    print(f">>> Khorooji: {out_dir}")

    print(">>> Load-e model ...")
    solver = load_solver(ckpt=args.ckpt, device="cpu")

    ua = cc.USER_AGENTS[0]
    print(">>> Baz kardan-e site ...")
    driver, wait = cc.open_site(ua)

    solved = 0
    try:
        for i in range(1, args.count + 1):
            try:
                # captcha-ye vaghe'i (na kadr-e khali) ra amade kon
                captcha = cc.ensure_real_captcha(driver, wait, args.delay)

                # crop -> PIL.Image (haman tasvir-i ke model rooye an train shode)
                crop = cc.crop_captcha_with_padding(driver, captcha)

                # khandan ba model
                answer = solver.read_pil(crop)

                # zakhire ba naam = javab-e model (+ prefix-e shomare)
                fname = f"{i:03d}_{sanitize(answer)}.png"
                crop.save(os.path.join(out_dir, fname))
                solved += 1
                print(f"[{i:3d}/{args.count}] javab={answer!r} -> {fname}")

                # captcha-ye ba'di (refresh-e event-based-e naskhe-ye jadid)
                if i < args.count:
                    changed = cc.click_refresh(driver, wait, captcha)
                    if not changed:
                        print("      (captcha avaz nashod -> reload-e safhe)")
                        driver.get(cc.URL)
                        wait.until(cc.EC.presence_of_element_located(
                            (cc.By.CSS_SELECTOR, cc.CAPTCHA_CSS)))
                    cc.human_delay(args.delay)

            except Exception as e:
                print(f"    khata dar tekrar {i}: {e} -> restart-e morurgar")
                try:
                    driver.quit()
                except Exception:
                    pass
                cc.human_delay(2.0)
                driver, wait = cc.open_site(ua)
                continue

        print(f"\n>>> Payan. {solved}/{args.count} captcha zakhire shod dar '{out_dir}'.")
        print(">>> Naam-e har file = javab-i ke model dar avard. ba cheshm barresi kon.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
