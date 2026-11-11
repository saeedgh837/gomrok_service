"""Compatible front door for isolated panel/robot unittest modules.

Use the bundled interpreter; each selected module runs in a fresh guarded child.

    python -I -B lab/scripts/run_panel_tests.py [test_module ...]
"""
import argparse
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

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
    # cloud-sync batch 1 (workstream S): no published password, the Origin /
    # Host / CSRF checks on every state-changing request, and the two CRM
    # read-modify-write sites under crm.STORE_LOCK (spec §10).
    "test_auth_hardening",
    # cloud-sync mission modules (workstreams A, B, C-client, J, K, D, E, G, F, C-host):
    # state machine, mapper, transport/outbox, apply/pull, one-shot import, UI,
    # conflict UI, integration/consistency suite, host schema, host API.
    "test_cloud_state",
    "test_cloud_mapper",
    "test_cloud_transport",
    "test_cloud_apply",
    "test_cloud_import",
    "test_cloud_ui",
    "test_cloud_conflicts",
    "test_cloud_integration",
    "test_server_crm_v2_authority",
    # v1.15.1: the panel's own finance transport producers. The v1.15.0
    # outbox tests built their operations by hand, so nothing noticed that the
    # route could never stamp the marker (both branches read the ledger entry
    # instead of the customer). This module drives the real POST /crm-customer.
    "test_server_crm_v2_finance_route",
    "test_host_schema",
    "test_host_api",
    "test_private_accounts",
    "test_desktop_private_accounts",
    "test_legacy_shared_reset",
    "test_app_updater",
    "test_direct_job_admission",
    "test_driver_consent",
    "test_installer_handoff",
    "test_isolated_test_runner",
    "test_maintenance_api",
    "test_maintenance_gate",
    "test_maintenance_packaging",
    "test_maintenance_preview",
    "test_maintenance_service",
    "test_maintenance_shutdown",
    "test_maintenance_ui",
    "test_registration_attempts",
    "test_registration_identity",
    "test_release_notes",
    "test_release_notes_api",
    "test_release_probe",
    "test_service_health",
]

def _load_supervisor():
    """Load only the fixed stdlib-only supervisor, never an application module."""
    script = HERE / "run_isolated_tests.py"
    spec = importlib.util.spec_from_file_location("_irica_isolated_supervisor", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("The isolated test supervisor could not be loaded")
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    return supervisor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", help="Bare test module names; default: full panel suite")
    parser.add_argument("--timeout", type=float, default=180.0, help="Hard timeout per module, seconds")
    parser.add_argument("--report", type=Path, help="Optional sanitized JSON result outside app data")
    arguments = parser.parse_args(argv)
    forwarded = [*(arguments.modules or DEFAULT), "--timeout", str(arguments.timeout)]
    if arguments.report is not None:
        forwarded.extend(["--report", str(arguments.report)])
    supervisor = _load_supervisor()
    previous_argv = sys.argv
    try:
        sys.argv = [str(supervisor.SCRIPT), *forwarded]
        # The supervisor imports no product code and launches every module with
        # this interpreter and -I -B. It owns missing/empty-suite failure checks.
        return supervisor.main()
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    raise SystemExit(main())
