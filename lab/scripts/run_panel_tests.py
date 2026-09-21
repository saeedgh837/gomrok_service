"""Ad-hoc test runner for the panel/robot unit tests (no pytest needed).

Lives under lab/scripts/ because panel.clean_project_root() deletes any
`_verify_*.py` / `hermes-verify-*.py` left in the project root.

    python lab/scripts/run_panel_tests.py [test_module ...]
"""
import os
import sys
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
]

names = sys.argv[1:] or DEFAULT
loader = unittest.TestLoader()
suite = unittest.TestSuite()
missing = []
for name in names:
    if not os.path.exists(os.path.join(TESTS, name + ".py")):
        missing.append(name)
        continue
    suite.addTests(loader.loadTestsFromName(name))
if missing:
    print("SKIPPED (not present):", ", ".join(missing))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
