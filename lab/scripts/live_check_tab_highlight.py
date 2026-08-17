"""Live UI check: the SELECTED tab must be visually distinct on every tab strip.

Covers all four tab groups in the panel:
  * site-1 main nav tabs   (#main-tabs .nav-tab)           — ویرایش/لاگ/استعلامات/تنظیمات
  * site-1 log sub-tabs    (#view-logs .nav-tabs .nav-tab) — لاگ پروفایل/لاگ کلی
  * site-1 profile tabs    (.profile-tabs .profile-tab)    — پایه/هویتی/استعلامات
  * site-2 service picker  (#svc-btns .svc)                — همتا/گمرک/همه

For each group it selects every tab in turn and asserts that exactly one is
active, that the active one is filled with the brand gradient (inactive ones are
transparent), and that its text turns white — i.e. you can always tell which tab
you are on.

Usage (start a throwaway panel first so real data is untouched):
    python lab/scripts/run_test_panel.py 8759
    python lab/scripts/live_check_tab_highlight.py http://127.0.0.1:8759
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8759").rstrip("/")
CHROMEDRIVER = r"C:\chromedriver.exe"

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   {detail}" if detail else ""),
          flush=True)
    if not cond:
        failures.append(name)


def parse_rgb(value):
    nums = [int(float(x)) for x in
            value.replace("rgba(", "").replace("rgb(", "").rstrip(")").split(",")[:3]]
    return tuple(nums)


def is_transparent(value):
    return "rgba(0, 0, 0, 0)" in value or value in ("transparent", "none")


GROUP_STATE = r"""
return Array.from(document.querySelectorAll(arguments[0])).map(e => {
  const cs = getComputedStyle(e);
  return {
    id: e.id || e.getAttribute('data-svc') || e.textContent.trim().slice(0, 18),
    active: e.classList.contains('active') || e.classList.contains('on'),
    bg: cs.backgroundImage !== 'none' ? cs.backgroundImage : cs.backgroundColor,
    color: cs.color,
  };
});
"""


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,1000")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-first-run")
    driver = webdriver.Chrome(service=Service(CHROMEDRIVER), options=opts)
    driver.set_page_load_timeout(25)
    driver.set_script_timeout(15)

    def assert_group(label, selector, clicker, ids):
        for target in ids:
            clicker(target)
            time.sleep(0.35)
            state = driver.execute_script(GROUP_STATE, selector)
            actives = [s for s in state if s["active"]]
            inactives = [s for s in state if not s["active"]]
            ok_one = len(actives) == 1
            check(f"{label}: exactly one tab active ({target})", ok_one,
                  f"active={[s['id'] for s in actives]} of {len(state)}")
            if not ok_one or not inactives:
                continue
            a = actives[0]
            check(f"{label}: active tab is filled, not transparent ({target})",
                  ("gradient" in a["bg"]) or not is_transparent(a["bg"]), a["bg"][:60])
            check(f"{label}: active background differs from inactive ({target})",
                  all(a["bg"] != s["bg"] for s in inactives),
                  f"{a['bg'][:34]} vs {inactives[0]['bg'][:34]}")
            check(f"{label}: active tab text is white ({target})",
                  parse_rgb(a["color"]) == (255, 255, 255), a["color"])
            check(f"{label}: active text colour differs from inactive ({target})",
                  all(a["color"] != s["color"] for s in inactives),
                  f"{a['color']} vs {inactives[0]['color']}")

    try:
        # ---------------- site-1 ----------------
        driver.get(f"{BASE}/site-1")
        time.sleep(1.4)

        # main nav strip — scoped to the header strip so the log sub-tabs
        # (also .nav-tab) cannot pollute the count
        assert_group("main nav tabs", "#main-tabs .nav-tab",
                     lambda v: driver.execute_script(f"showView('{v}');"),
                     ["form", "logs", "inquiries", "telegram"])

        # log sub-tabs live inside #view-logs, rendered by renderLogs()
        driver.execute_script("showView('logs');")
        time.sleep(0.5)
        has_log_tabs = driver.execute_script(
            "return document.querySelectorAll('#view-logs .nav-tab').length >= 2;")
        check("log sub-tabs are rendered", has_log_tabs)
        if has_log_tabs:
            assert_group("log sub-tabs", "#view-logs .nav-tab",
                         lambda v: driver.execute_script(f"renderLogs('{v}');"),
                         ["profile", "all"])

        # profile tabs only exist once the editor form is built; the editor opens
        # via editProfile(id) (clicking the row itself does not open it)
        driver.execute_script("showView('form');")
        time.sleep(0.4)
        opened = driver.execute_script("""
          const row = document.querySelector('#prof-list .prof-item[data-profile-id]');
          if (!row) return false;
          editProfile(row.getAttribute('data-profile-id'));
          return true;
        """)
        check("a profile could be opened in the editor", bool(opened))
        time.sleep(0.9)
        has_editor = driver.execute_script(
            "return !!document.querySelector('.profile-tabs .profile-tab');")
        check("profile tabs are rendered after selecting a profile", has_editor)
        if has_editor:
            assert_group("profile tabs", ".profile-tabs .profile-tab",
                         lambda v: driver.execute_script(f"showProfileSection('{v}');"),
                         ["base", "identity", "inquiries"])

        # ---------------- site-2 ----------------
        driver.get(f"{BASE}/site-2")
        time.sleep(1.4)
        has_svc = driver.execute_script(
            "return !!document.querySelector('#svc-btns .svc');")
        check("service picker is rendered on /site-2", has_svc)
        if has_svc:
            assert_group("service picker", "#svc-btns .svc",
                         lambda v: driver.execute_script(f"pickSvc('{v}');"),
                         ["hamta", "gomrok", "all"])

        severe = [e for e in driver.get_log("browser") if e["level"] == "SEVERE"]
        check("no severe console errors on /site-2", not severe, str(severe)[:180])
    finally:
        driver.quit()

    print("----")
    print("RESULT:", "all tab-highlight checks passed" if not failures
          else f"FAILURES: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
