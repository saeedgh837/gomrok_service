# -*- coding: utf-8 -*-
"""Verify: gomrok.py closes the browser after successful receipt + panel save."""
import re
import sys
import os

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "gomrok.py")
src = open(SRC, encoding="utf-8").read()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


try:
    compile(src, SRC, "exec")
    check("gomrok.py compiles", True)
except SyntaxError as e:
    check("gomrok.py compiles", False, str(e))

# auto path: after handle_receipt_and_payment success -> driver.quit(), not input()
auto_ok = (
    "if found:" in src
    and "driver.quit()" in src
    and "morurgar baste shod" in src
)
check("auto path quits on success", auto_ok)

# manual path also quits on success
i_manual = src.rfind("manual_manage")
seg = src[i_manual:i_manual + 6000] if i_manual >= 0 else ""
check("manual path quits on success",
      "driver.quit()" in seg and "Panjere baz mimanad" in seg)

# 'found' initialized before loop (no NameError when marhale-5 never reached)
check("found initialized before loop",
      re.search(r"found = None  # kod-e amniati", src) is not None)

# panel record still happens before quit (handle_receipt_and_payment keeps _record_panel_output)
check("panel record intact", "_record_panel_output(_CLAIMED_PROFILE_ID" in src)

# input()-hold still exists for failure paths (reg_stopped, kod nashod, --no-submit)
holds = src.count("Panjere baz mimanad; Enter bezan ta baste shavad.")
check("failure holds remain", holds >= 3, f"count={holds}")

print("\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else "")
                for name, ok, detail in results))
bad = [x for x in results if not x[1]]
print(f"\n{len(results) - len(bad)}/{len(results)} checks passed (ad-hoc)")
sys.exit(1 if bad else 0)
