"""Ad-hoc test runner for the panel/robot unit tests (no pytest needed).

Lives under lab/scripts/ because panel.clean_project_root() deletes any
`_verify_*.py` / `hermes-verify-*.py` left in the project root.

    python lab/scripts/run_panel_tests.py [test_module ...]
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TESTS = os.path.join(ROOT, "lab", "tests")
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

DEFAULT = [
    "test_profile_pool", "test_runtime_heartbeat", "test_profile_success",
    "test_clone_profile", "test_unusable_profile", "test_runtime_api",
    "test_identity_sections", "test_store_durability", "test_browser_visibility",
    "test_page_probe_and_telegram_timeout", "test_already_registered_profile",
    "test_telegram_delivery", "test_telegram_chain",
    "test_profile_device_inquiry", "test_profile_readonly_view", "test_v1_0_11_features",
    "test_v1_0_12_installer", "test_v1_0_13_stop_and_shutdown",
    "test_v1_0_14_launcher_ux",
    "test_v1_0_16_captcha_confidence",
    "test_crm_validation", "test_crm_store", "test_crm_api",
    "test_crm_inquiry_pool",
    "test_crm_scheduler",
    "test_v1_1_2_crm_updates",
    "test_v1_1_3_captcha_stall",
    "test_v1_4_finance_periodic",
    "test_v1_1_4_parallel_ux",
    "test_dashboard_rewrite",
    "test_system_debug_logs", "test_logging_and_stage_skip",
    "test_robot_interpreter", "test_manual_scroll", "test_app_paths",
    "test_v1_1_5_summary_rail",
    "test_v1_1_5_tabs_closed",
    "test_v1_2_0_login_gate",
    # v1.5 (B9): each module verified individually (exit 0) before being
    # registered here. test_v1_5_passport_robot deliberately absent — the
    # robot-side task was cancelled by the user.
    "test_crm_design_gates",
    "test_crm_v1_3_ui",
    "test_v1_4_0_tab_search",
    "test_v1_4_1_crm_width",
    "test_v1_4_2_jalali",
    "test_v1_4_2_fa_dates",
    "test_v1_5_inquiry_gate",
    "test_v1_5_forms_tabs",
    "test_v1_5_sections_wiring",
    "test_v1_5_case_list",
    "test_v1_5_dialog_chrome",
    "test_v1_5_profile_ready",
    "test_v1_5_review_fixes",
    "test_v1_5_handoff",
    "test_v1_5_finance_ui",
    "test_periodic_inquiry_tab",
    # Domain A contract modules (owner passport, ledger, stage machine).
    "test_crm_stage_transitions",
    "test_crm_case_integrity",
    "test_crm_watch_identity",
    "test_crm_owner_passport",
    "test_crm_ledger_integrity",
    # v1.5 backend slice (W1): finance report + owner detach.
    "test_v1_5_finance_report",
    "test_v1_5_owner_detach",
    # v1.6 accounting round (W2 frontend): statement / void / invoice UI.
    "test_v1_6_accounting_ui",
    "test_v1_6_profile_ensure_fix",
    # v1.6 accounting round (W1 backend): void-aware ledger + statement +
    # invoice + business settings.
    "test_v1_6_accounting",
    # v1.7 round: per-case fee (U+066C intake), inquiry-order fix, owner
    # archive, reporting periods, GET safety net, period-report routes.
    "test_v1_7",
    # v1.8 round (frontend): «دوره‌ها» tab, period detail dialog, icon
    # action buttons + tooltip layer, the per-case money predicate.
    "test_v1_8_periods_ui",
    # v1.8.1 round: busy state keyed to the action (survives the re-render),
    # period dialog return + live refresh, and the no-case-without-a-period
    # gate (button, dialog and route).
    "test_v1_8_1_ui_fixes",
    # entry-point naming + the device empty state's «پروندهٔ جدید» entry
    # (v1.8.1: the harness now authenticates — it had been reading the login
    # form since the v1.2.0 gate, and the empty-state pin had drifted onto
    # the periods tab).
    "test_entrypoint_ui_fixes",
    # v1.9.0 round: newest-first lists + passenger row numbering, the
    # related-sites box (left corner, dismissable, server-backed links) and
    # the UI sound engine (settings switch + header mirror + /sfx whitelist).
    "test_v1_9_0_ux",
    # v1.9.1 round: result-element capture in the inquiry robots (element
    # screenshot + page fallback, PNG validation, photo-first telegram with a
    # text fallback, store fields hamta_screenshot / gomrok_screenshot) and
    # the panel side (containment-checked /estelam-shot route, camera icon
    # per service, shot modal).
    "test_v1_9_1_capture",
    "test_v1_9_1_screenshot",
    # v1.9.1 round, second half: the /estelam-shot route and its containment
    # rule, and the CRM side (presence flags, per-service camera icon, shot
    # modal, screenshot cleanup on delete). A file listed here but absent is
    # reported as SKIPPED, so a dropped deliverable cannot pass unnoticed.
    "test_v1_9_1_routes",
    "test_v1_9_1_crm_shot",
    # v1.9.1 follow-up: two modules that had been sitting outside this list.
    # test_estelam_hamta needed no change; test_estelam_service is
    # function-style, so it carries an AsUnittest adapter (in the module) that
    # publishes its tests to unittest discovery — without it, listing the
    # module here would collect nothing and still print OK.
    "test_estelam_hamta",
    "test_estelam_service",
    # v1.10 round (operator requests): the period filter on the case lists
    # («پرونده‌های دورهٔ فعلی» / «لیست پرونده‌ها بر اساس دوره‌ها») — the page
    # functions are executed in node, not re-implemented — and the optional
    # Telegram report channel (additive delivery, exactly-one destination for
    # the registration handoff, gomrok.py untouched).
    "test_v1_10_period_filter",
    "test_v1_10_report_channel",
    # v1.10.1 round: the Telegram delivery fix in telegram_notify.py — each
    # destination gets its own slice of the caller's budget (the starved
    # receipt target), a missed destination is reported in one summary line
    # plus one JSONL record, an oversized receipt is shrunk once per call, and
    # a permanently dead chat is not retried. All three drive the real HTTP
    # path against lab/tests/_telegram_fake.py (loopback fake, no real token);
    # test_telegram_receipt_path is the slow one (~70 s).
    "test_telegram_delivery_fix",
    "test_telegram_fanout_budget",
    "test_telegram_receipt_path",
    # v1.11 round: automatic ChromeDriver update. chromedriver_updater.py
    # fetches the driver matching the installed Chrome from Chrome-for-Testing
    # through the Cloudflare proxy (never a direct connection), verifies the
    # archive and the binary's --version before an atomic replace, and is
    # covered offline here — fake catalogue payload, locally built zip, no
    # network and no live data dir.
    "test_chromedriver_updater",
    # v1.11 round, the hook side: which driver copy the updater refreshes
    # (bundled always, an existing override too, never a created one), the
    # one-attempt-per-process latch and the cross-process cooldown, and that
    # check_driver_compat clears its Persian warning once a swap succeeds.
    "test_driver_autoupdate_hook",
    # v1.11 round, the panel side: the launch page's «به‌روزرسانی درایور مرورگر»
    # button, its /driver-update route (behind the login gate like every other
    # POST), and captcha_collector.update_chromedriver_now — the manual entry
    # point that ignores the automatic attempt's latch and cooldown. Served by
    # the real handler over loopback; the updater itself is stubbed.
    "test_driver_update_panel",
]

names = sys.argv[1:] or DEFAULT

# Keep the whole run out of the operator's live data directory. Modules that
# patch their own paths are unaffected; a module that forgets to now writes
# into a throwaway directory instead of real data (test_crm_inquiry_pool was
# appending "inquiry started" records to the live estelam log this way). This
# must happen before anything imports app_paths, whose data_dir() is cached on
# its first call.
SCRATCH = tempfile.mkdtemp(prefix="irica_tests_")
os.environ["IRICA_DATA_DIR"] = SCRATCH
print("data dir for this run:", SCRATCH)

loader = unittest.TestLoader()
suite = unittest.TestSuite()
missing = []
hollow = []
for name in names:
    if not os.path.exists(os.path.join(TESTS, name + ".py")):
        missing.append(name)
        continue
    loaded = loader.loadTestsFromName(name)
    # A module that is listed but yields no test cases is a hole in the suite:
    # a script-style module (tests as plain functions, run by its own
    # _run_all()) silently collects zero here. Fail the run rather than print
    # OK over it — a dropped or mis-shaped module must not pass unnoticed.
    if loaded.countTestCases() == 0:
        hollow.append(name)
    suite.addTests(loaded)
if missing:
    print("MISSING (listed, file not present):", ", ".join(missing))
if hollow:
    print("EMPTY (listed, collected no tests):", ", ".join(hollow))
result = unittest.TextTestRunner(verbosity=2).run(suite)
ok = result.wasSuccessful() and not missing and not hollow
if ok:
    shutil.rmtree(SCRATCH, ignore_errors=True)
else:
    # Anything a test wrote while failing is still here to look at.
    print("kept for debugging:", SCRATCH)
sys.exit(0 if ok else 1)
