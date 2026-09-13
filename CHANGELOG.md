# Changelog

All notable changes to this project are documented here.
This project uses [Semantic Versioning](https://semver.org/).

## [1.3.3] - 2026-09-13

### Changed

- **Passenger passport is optional:** creating or editing a passenger no
  longer requires a passport number. When supplied, it accepts 1-20 Latin
  letters or digits and is stored uppercased.

## [1.3.2] - 2026-09-13

### Fixed

- **Toasts rendered dimmed underneath the case modal (operator report):**
  a `showModal()` dialog renders in the browser top layer, above every
  z-index, so the toast container could never overlay it. While a dialog
  is open the toast container now moves inside the dialog element (and
  back to the body on every close path — ×, Esc, programmatic), so
  notifications always render above the modal and its backdrop at full
  opacity. Fixed positioning is unchanged — the dialog has no
  transform/filter, so the container still anchors to the viewport.

## [1.3.1] - 2026-09-13

The case-modal UX update: three operator-reviewed refinement rounds on the
case-detail modal (live-verified on a scratch panel, independently
reviewed). Frontend-only — panel.py markup/JS plus marker tests.

### Fixed

- **Stale case-detail modal:** the modal now keeps itself in sync — every
  refresh re-renders it when the case's signature (stage, timestamps,
  inquiry/payment status) changes, reopens it after confirm flows that
  replaced it (inquiry, periodic remove/re-add, pay-stop, requeue, skip,
  rollback), and closes it with a named toast when the case is deleted.
  This removes the confusing «این کنش در مرحلهٔ فعلی مجاز نیست» errors
  caused by clicking buttons of an already-advanced stage.
- **Esc no longer resurrects the modal:** closing the dialog via the
  native Esc path disarms the sync machine (a close listener clears the
  modal tracking state), so a dismissed modal cannot pop back open on the
  next background refresh.
- Double-click guard on the «قدم بعدی» hero button: a second POST before
  the first one lands no longer produces a stage-mismatch error.
- Remaining anonymous toasts in the case flow and the periodic tab now
  name their row (case/passenger name), e.g. «...» از استعلام دوره‌ای
  حذف شد.

### Changed

- **Modal action redesign:** one clear primary per step — a «قدم بعدی»
  hero card under the rail (with visible unmet-requirement chips and the
  new destination-labelled continue buttons), stage tools grouped under
  «ابزار این گام», and low-frequency actions (edit, rollback, skip,
  close, delete) in a quiet «سایر اقدامات» row.
- **Step-scoped tools (operator request):** every tool now appears only
  on its own step — manual gomrok/hamta inquiries on step 2 (inquiry),
  the Telegram re-send on step 4 (handed), the periodic-watch toggle on
  step 5 (to_payment), pay controls on steps 5-7. The problem stage shows
  its «بازگشت به صف» as the hero card; the periodic tab still offers the
  watch toggle for every watched stage.
- Creating a case opens the new case's modal immediately; the modal body
  close button was removed (the × and Esc remain).

## [1.3.0] - 2026-09-13

The case-workflow update: a 14-item operator-driven CRM overhaul —
identity-pool inquiries at every stage, per-passenger passports, editable
and rollback-able completed cases, a case table with a detail modal, and a
Telegram registration handoff. Implemented through a reviewed multi-agent
pipeline (2 domains, per-task supervisor verification, 3 independent
reviews); the suite grew 511 -> 749 tests, all green.

### Added

- **Identity-pool inquiries at every stage (item 7):** watch-stage customs
  inquiries now run under the inquiry-identity pool exactly like intake —
  never under the passenger's national code — failing closed with a clear
  Persian error when the pool is empty or invalid. The site's
  "registered under another national code" message (متعلق به این کد ملی
  نیست, operator-provided) is classified as the `advanced` (registered)
  status, so the existing auto-advance join moves handed cases toward
  payment; «یافت نشد» keeps the case polling on its interval.
- **Per-passenger passport:** required (latin letters/digits, 5-20 chars,
  stored uppercased) on every passenger save; legacy records without one
  keep loading unchanged. The registration profile gains an OPTIONAL
  `passport` field (auto-filled from the passenger on create/link, never
  overwritten) with a site-1 form field and route-level validation.
- **Attach-time registration profiles (item 11):** the profile is created
  or linked the moment a passenger is connected to a case (idempotent,
  deduplicated by serial/national) with the handover/auto-advance ensure
  kept as a backstop. `/crm-start-payment` self-heals an empty or stale
  profile link and returns an actionable Persian error (naming the
  recovery) when no passenger is attached — the reported
  «پروفایل ثبت‌نام ساخته نشده» failure is regression-tested.
- **Telegram registration handoff (item 8):** after a successful handover
  tick the panel sends exactly one message
  (`kodmeli = … / imei1 = … / imei2 = … / passport = …`) to the new
  «registration channel/group numeric id» setting. Fail-closed when the
  destination is unset or the passenger's passport is missing; Telegram
  failures never affect the case state; a per-case «ارسال مجدد به تلگرام»
  button; logs carry the device id and destination only.
- **Case management:** a case/device edit modal (primary IMEI locked with
  delete-and-recreate guidance), editable completed cases with a
  strongly-confirmed rollback (done → payment_done, completion timestamps
  cleared), strict section separation (active/completed/problem/closed —
  each case in exactly one section), created/updated/completion timestamps
  on every case surface, and a consistent × close affordance on every
  dialog with ESC support.
- **Case table + detail modal (item 14):** the devices tab is now a clean
  table (id, passenger, device, masked serial, stage, status, created,
  updated) with all per-case actions living in a case-detail modal.

### Fixed

- Silent step-2 customs-inquiry failures (items 1+9): every gomrok inquiry
  affordance surfaces backend errors; gomrok buttons disable with a tooltip
  while the identity pool has no valid entry (hamta unaffected); bulk runs
  report per-device errors.
- "Back at step 5 completed the case" (item 12): no UI or backend path can
  complete a case except the deliver_code tick — the skip-to-done path is
  removed everywhere and an exhaustive 10-stage × 13-action invariant
  matrix pins the contract.
- **Registry-fee double-booking:** correcting a serial typo (delete +
  recreate, forced by the immutable primary IMEI) now books an automatic
  `base_fee_refund` reversal on the customer holding the original row, so
  the ledger stays fee-neutral end to end.
- Auto-advance gave up on a case after a rollback re-advance (stale local
  attempt counter) leaving it stuck at `to_payment` until a manual start.
- `retry_count` UnboundLocalError crashed the customs inquiry robot's
  first-try verdicts (no preceding transient error) before classification
  ran — a latent pre-existing crash, now guarded.
- cp1252 console crash: `/save` and `/activate` dropped their HTTP response
  when printing Persian/profile data on a non-UTF-8 console.
- Double-start guard: a device with a live inquiry run can no longer be
  started again (per-device in-flight refusal; the periodic-tab reset
  button disables while runs are live).
- Owner-record hardening: `update_owner` no longer crashes on hand-edited
  rows lacking `card_no`; spaced 16-digit cards store normalized.
- `last_inquiry_at` writes are under `STORE_LOCK`; the site-2 estelam page
  labels an `advanced` result «ثبت‌شده در گمرک» (success) instead of
  «مسدود/خطر»; the periodic tab marks payment-stage cases «فقط دستی»
  instead of a countdown for inquiries that never run; all copy claiming
  watch inquiries run under the passenger's identity corrected.

### Changed

- Passenger card number is optional (empty allowed; 16 digits still
  enforced when present).
- `telegram_notify.send_message` gained an additive `chat_id` parameter for
  single-destination sends (existing callers and fan-out behavior
  unchanged).
- The test runner's DEFAULT suite grew from 38 to 54 modules.

### Tests

- Suite 511 → 749, all green: 238 new tests across 13 new modules (stage
  transitions, case/ledger/owner-passport integrity, watch identity +
  not-owner classification, inquiry gate, forms/tabs, sections/wiring,
  case list, dialog chrome, profile-ready, review fixes, telegram
  handoff); 5 stale contract tests rewritten; 34 fixtures updated; the
  pre-existing `test_periodic_inquiry_tab` failure fixed and the module
  registered.

### Notes

- `gomrok.py` is untouched in this release (operator decision); passports
  are entered into the portal manually per the operator's workflow.
- Before first use: complete each passenger's passport in the passenger
  tab, and set the registration Telegram channel id in settings — both
  gates fail closed with guidance messages until then.

## [1.2.0] - 2026-09-10

### Added

- Login gate for the whole panel: every fresh visit lands on a login page
  (`/login`); every other route requires a valid session cookie. Unauthenticated
  page requests redirect to `/login`, API requests get HTTP 401 with a Persian
  message, and `/shutdown` is gated like everything else.
- Three operator-provisioned accounts (saeed, hasan, arsalan) seeded on first
  start into `panel_users.json` inside the data directory — PBKDF2-HMAC-SHA256
  (390k iterations, per-user salt), never plaintext. No user-management UI by
  design; the file is created outside the release tree and is excluded from
  bundles by the release verifier.
- Session handling: HttpOnly `SameSite=Lax` cookie with a 7-day sliding server
  session; «خروج (user)» in the CRM header and `POST /logout` destroy the
  session server-side. Restarting the panel logs everyone out (sessions are
  in-memory).
- Brute-force throttle: 5 failed attempts lock the username for 30 seconds
  (HTTP 429); unknown usernames verify against a dummy hash so response timing
  does not reveal account existence. Successful logins are recorded in the CRM
  event log (`login_ok` with the username only).
- Telegram attribution: every outbound Telegram message (reports, robot
  notifications, estelam/hamta/exitban workers) now starts with
  «کاربر: <name>». The acting user is stamped onto child processes at spawn
  time (`IRICA_ACTOR`); panel-initiated sends pass the user explicitly, and
  scheduled background runs are attributed to «سیستم».

### Removed

- The panel-access Cloudflare stack (cloudflare_auth_relay.py on :8751,
  start_cloudflare_relay.py, start_named_cloudflare_tunnel.py,
  cloudflare_named_tunnel.yml and the watchdog section that kept the tunnel
  alive): the panel is a localhost service again, reachable only on
  127.0.0.1:8750. The Telegram relay chain (telegram_api_relay.py +
  cloudflare_http_proxy.py) is untouched.

### Changed

- The release verifier gates page probes behind the login: with
  IRICA_VERIFY_USER/IRICA_VERIFY_PASS set it logs in and checks the pages;
  without credentials it verifies the gate shape (401/302 + login page 200).
- Test harnesses drive the real gate: a shared helper
  (`lab/tests/_auth_harness.py`) seeds a session and injects the cookie into
  every request, so the suite exercises the authenticated paths end to end.

## [1.1.5] - 2026-09-10

### Added

- CRM tabs reorganized around case outcomes: «تکمیل شده‌ها» (completed cases,
  read-only), «دارای اشکال» (problem cases with «بازگشت به صف» and close actions),
  and «مختومه‌ها» (terminated cases with their reason and date). The KPI tile
  now links to the completed tab.
- Terminal case state «مختومه» (closed): an operator can terminate a case from
  the inquiry or problem stage with a mandatory written reason (stored on the
  case with a timestamp). Closed cases reject every further action — the
  scheduler, watch runs, auto-advance, periodic re-inquiry and inquiry starts
  all exclude them — and they can never be rolled back.
- Left summary rail on the devices view: «جدیدترین مشتری‌ها» (newest customers
  with balance pills), «پرونده‌های اخیر» (recent cases; clicking scrolls to and
  flashes the device card), and «بیشترین بدهکاری» (top debtors). Pure
  client-side; no new server routes.
- Ledger trace for terminations: a `case_closed` event records the reason's
  LENGTH only, never the reason text (privacy §12).

### Tests

- Suite grew 409 → 465 (summary-rail 26, tabs/closed 30 including an
  HTTP end-to-end close-case flow); all green.

## [1.1.4] - 2026-09-09 (assets refreshed 2026-09-10)

### Fixed (asset refresh)

- Blank CRM body regression: a missing comma in the page's ICONS object literal
  (introduced with the transaction-detail icon) was a JS syntax error that killed
  the whole CRM page script, leaving only the server-rendered header. Fixed, and
  a permanent regression gate now runs `node --check` over every `<script>` block
  of every served page in the test suite.
- Stage model reworked into the operator's 8-step rail (problem state unnumbered,
  step-2 "not registered" = pass / "registered" = red danger, step-5 periodic
  customs re-inquiry with registered = pass). Published zip/exe/SHA256SUMS assets
  were rebuilt from the fix; the v1.1.4 tag moved to the fix commit.

### Added

- Parallel inquiry runs: several devices can be inquired at the same time,
  each with its own browser (cap 3 concurrent robot processes). The estelam
  store tracks every live run in a `runs` registry (legacy `run_pid` still
  honored); dead pids are pruned on read so the cap tracks reality.
- Per-device "افزودن به استعلام دوره‌ای / حذف از استعلام دوره‌ای" toggle on the
  CRM device card (watch stages).
- Periodic tab: "+ افزودن پرونده" opens a device-picker modal with search,
  10/20/30 page-size selector and pagination; checkboxes choose devices and
  the selected ones are re-added to the periodic inquiry list.
- Stage guide: every step on the numbered stage rail is a button; hovering
  shows a tooltip and clicking opens a modal explaining that stage (what it
  does, what it needs, what continues it).
- Transactions tab: each row has a جزئیات button opening a detail modal with
  customer, time, amount, kind, note, device name, device serial(s) and the
  attached image; the registry-fee note now carries the device name.

### Fixed

- CRM tab counts showed 0 for every tab after a page load until a tab was
  clicked: `refresh()` now re-renders the tab bar after state arrives.
- Stopping the inquiry kills ALL live runs (not just the legacy single pid)
  and resets only the devices those runs owned, so one run's stop no longer
  clobbers a concurrent run's in-flight status.
- Person identity is handed to each robot child through IRICA_PERSON_* env
  vars; with parallel starts the shared store's `person` could previously be
  overwritten before the child read it (wrong owner on the customs form).

## [1.1.3] - 2026-09-09

### Fixed

- Inquiry robot no longer stamps a device as "دارای اشکال" (problem) when the
  site kept rejecting the captcha: captcha/transient exhaustion is now
  status "error" (device NOT evaluated, automatically re-inquirable by the
  next periodic run) with a neutral telegram message, and only genuine
  non-retryable site verdicts remain "problem". Evidence: device e1a4e106
  was misclassified after 25 captcha rejects while the same serial inquired
  healthy on Hamta minutes later.
- Bounded captcha-stall abort: after 12 consecutive captcha-rejected submits
  the inquiry gives up that device for the run (~1.5 min saved per doomed
  device) instead of always burning all 25 attempts.
- Per-attempt telemetry: the retry loop reports every submit (answer,
  confidence, rejected) to the inquiry flow; the panel log now records the
  attempt count, rejected count, and the last 3 answers, so a bad solver
  streak is diagnosable from the log instead of invisible.

## [1.1.2] - 2026-09-08

### Added

- Inquiry gate on the CRM stage machine: advancing a device past the
  inquiry stage (`mark_ready`) now requires BOTH a healthy customs
  result AND a good hamta result recorded in the estelam store.
  Fail-closed: a device with missing or failed inquiries is rejected
  with a Persian action message instead of silently advancing.
- Per-device inquiry actions on the CRM device cards: run customs,
  hamta, or both inquiries for a single device directly from the CRM
  list (confirmation modal first), with last-inquiry time and result
  shown per device.

### Fixed

- Stage rollback (`stage_back`) works across multiple stages and is
  exempt from the inquiry gate.
- Duplicate serial guard: adding a second device with the same IMEI
  serial in one case is rejected.
- New-device modal form hygiene: optional telegram/instagram fields,
  mobile validated (11 digits, `09` prefix), customer picker dropdown
  with a "new customer" option, per-field error messages, styled
  checkboxes, and the device-notes field restored (it was dropped from
  the modal while still being read on submit).

## [1.1.0] - 2026-09-07

### Added

- Customer & case management (CRM) at `/crm`, built plan-first with an
  independent design review (20 findings merged) and TDD throughout
  (83 new tests; suite 233 -> 316):
  - `crm.py`: customers with a Toman ledger, owners (Iranian mod-11
    national checksum, shamsi age 15..99, 16-digit card), a random
    pre-registration identity pool, devices, and a 9-stage case machine
    with timestamped idempotent ticks. Store writes are atomic with a
    seeded `.bak` and corrupt-main recovery.
  - `/crm-*` API routes on the panel; the CRM page (6 tabs) with the
    panel-wide theme; `/crm-report` txt/html/PDF (headless Edge with an
    auto-print HTML fallback) and a telegram report that masks national
    codes and never carries card numbers.
  - Identity-scoped inquiries: pre-registration intake runs under a
    random VALID pool identity; the watch re-inquiry after handover runs
    under the device OWNER's identity (the customs site only reports
    registration to the registering code). Devices at watch stages
    without an owner are rejected, never silently re-identified.
  - Per-device, serialized runs only: CRM never starts the unscoped
    estelam sweep, and its queue sync leaves manual site-2 rows
    byte-identical. `estelam_store.json` saves are atomic with `.bak`.
  - A 60s scheduler beat (kill switch `IRICA_CRM_SCHEDULER_OFF=1`)
    re-inquires due watch devices every configured interval (1..72h,
    default 2) and auto-advances registered devices to payment ONLY on a
    confirmed robot start (attempts capped at 6).
  - Handover auto-links or auto-creates the site-1 registration profile
    (dedupe by IMEI or owner national code — pre-CRM manual devices are
    linked, not duplicated). `crm_store.json` joins the release
    migration list.

## [1.0.16] - 2026-09-07

### Fixed

- Fewer wrong-captcha submissions in BOTH the customs inquiry and the
  customs registration flows (inquiry logged at ~29% of submits). Two
  root causes addressed:
  - The OCR decoder now attaches a confidence score to every read; reads
    below 0.6 (tunable via `IRICA_CAPTCHA_MIN_CONF`) trigger a free captcha
    refresh + re-read (max 3) instead of submitting a fabricated guess.
  - Before reading, the captcha image must stay unchanged across 3
    consecutive fingerprints (bounded 10s), closing the race where the
    page swaps the image between OCR and submit after a slow load / F5
    (12 of 16 logged wrong-captcha retries followed such dirty loads).
- The confidence gate lives in `gomrok.py` (`solve_confidently`) and is
  shared by both robots; the registration robot's main read and its
  in-place retry loop now use it too.

## [1.0.15] - 2026-09-07

### Changed

- Softer dark theme across all panel pages: near-black backgrounds
  (`#0b0c0f` / `#0d1220` / `#0f1115` / `#0b1020`) replaced with a lighter
  graphite-indigo family (`#171a26` base, `#1f2333` cards, `#363d57`
  lines), with brighter muted text and lighter shadows for contrast.
  Applies to the launcher card, the customs panel (dark mode), the inquiry
  panel (dark mode), and the home picker.

## [1.0.14] - 2026-09-07

Visible launch feedback: the desktop shortcut now shows that the service
started, where its UI is, and how to stop it.

### Added

- `panel_tray.pyw` launcher: probes the panel, spawns it detached when down
  (25s bounded poll), fires a Windows toast ("سرویس رجیستری فعال شد"), and
  opens the new `/launch` page in the default browser. Falls back to
  `/site-1` on panels older than 1.0.14; MessageBox on start failure.
- `GET /launch`: self-contained Persian RTL launcher card — live status
  (profiles / running / success / blocked from `/state`), version badge,
  buttons for both panels and a "بستن سرویس" button that POSTs `/shutdown`
  and flips the card to a "service stopped" state.
- `GET /favicon.ico` (inline SVG) to quiet browser 404 noise.

### Changed

- Installer shortcuts and the post-install run now launch `panel_tray.pyw`;
  the autostart task intentionally stays silent (`panel_gui.pyw`).
- The bundle ships `panel_tray.pyw` alongside `panel_gui.pyw`.

## [1.0.13] - 2026-09-07

Inquiry stop button and whole-system shutdown.

### Added

- Device-inquiry card stop button: `POST /profile-device-stop` kills the
  running `profile_inquiry.py` process tree (tracked via
  `device_inquiry_pid`), resets the profile's inquiry state, and reports
  success/failure as a toast; the UI button switches between Run/Stop based
  on live state.
- "بستن سامانه" (close whole system) button on both panels (`/site-1`
  header actions and `/site-2` beside the width/theme buttons):
  `POST /shutdown` kills every running robot/profile process via
  `shutdown_everything()`, then sets `_SHUTDOWN_REQUESTED`; the serve loop
  (`serve_forever` on a helper thread, main thread polling the event) exits
  the panel process cleanly with code 0.
- `system-close-btn` styling shared across both pages.

### Changed

- Profile list redesign from the UI session: stable card anatomy (avatar
  with per-profile hue, identity block, status + run button, footer with
  receipt/payment chips), active-state accent bar, hover elevation, smooth
  inner scroll; `test_dashboard_rewrite.py` updated markers.

## [1.0.12] - 2026-09-06

The installable release: a per-user Windows installer replaces the
unzip-into-a-new-folder upgrade loop.

### Added

- `IRICA-Setup-vX.Y.Z.exe` built with Inno Setup (`lab/scripts/irica.iss`,
  `lab/scripts/build_windows_installer.py`): per-user install to
  `%LOCALAPPDATA%\Programs\IRICA`, no admin rights, in-place upgrades,
  desktop/Start-Menu shortcuts launching `panel_gui.pyw` (console-less), an
  optional autostart task, and an uninstaller that never touches
  `%LOCALAPPDATA%\irica` user data.
- Silent launcher `panel_gui.pyw`: runs the panel without a console window,
  logs crashes to `%LOCALAPPDATA%\irica\panel_crash.log` and shows a
  message box instead of dying silently.
- chromedriver is bundled into `.tools\` by the release build when a copy is
  available; the manual FIRST_RUN download step becomes optional.
- Shared chromedriver resolution in `captcha_collector.resolve_chromedriver()`:
  explicit override -> bundled `.tools` copy -> Selenium Manager. `gomrok2.py`,
  `estelam_hamta.py` and `exitban.py` now route through it instead of their
  own hardcoded paths.
- Chrome/chromedriver version-compat check (`check_driver_compat`): a Persian
  warning names both majors when the driver does not match the installed
  Chrome, so post-Chrome-update robot failures are diagnosable immediately.
- Single-instance gate in `panel.main()`: a second panel start detects the
  running panel via an HTTP probe (including pre-1.0.11 panels via markup
  markers), prints a clear message and opens the running panel instead of
  crashing on the bind.

### Notes

- The installer is unsigned; SmartScreen shows "More info → Run anyway" on
  first run.
- Inno Setup is a build-time-only dependency (installed via winget on demand);
  running the panel or the installer needs nothing beyond the bundle.

## [1.0.11] - 2026-09-06

Panel polish: version badge, profile names on inquiry results, and a release
button for blocked profiles.

### Added

- The panel version (from the bundled `VERSION` file) is now shown on all three
  pages: the site picker, the main dashboard header, and the device-inquiry
  page header (`نسخه x.y.z`).
- Inquiry result rows now carry the profile's own name. The device-inquiry card
  heading shows `پروفایل — سرویس` and every result row prints
  `👤 پروفایل: <name>`, so results are unambiguous when several profiles are
  checked in one session.
- Blocked profiles get a `🔓 آزادسازی و شروع مجدد` button in the edit form.
  `POST /release-blocked` resets `registration_status` to `pending`, drops the
  stored `runtime_error`, re-enables the profile, and clears runtime and
  inquiry bookkeeping (including `device_inquiry_pid` and the
  `running` inquiry statuses) so the profile can be edited and rerun.
  Refusals (busy, not blocked, missing id) return 409 with a Persian message
  and change nothing.

### Notes

- The release route refuses to touch a busy profile; stop the robot first.

## [1.0.10] - 2026-09-05

Dual-SIM devices on the customs login page now work correctly.

### Fixed

- The customs login page shows `دستگاه شما دارای دو اسلات است لطفا شناسه دیگر را نیز
  وارد نمایید` when a device has two SIM slots and the second IMEI is missing. This
  message was unclassified: not fatal, not retryable, not transient. The robot fell
  into the "other error" branch — logged, restarted the browser, and looped forever
  without ever filling the second slot. The marker `دو اسلات` is now in
  `IMEI_FATAL_MARKERS`, so the profile is blocked with the reason shown on the
  card (and the operator can use 📄 کپی to start clean with both IMEIs).
- `fill_imeis` already supports two IMEIs (the profile stores `imeis` as a list of
  up to 2). When `len(imeis) >= 2`, it clicks the "+" button to add the second
  row, fills both, and `verify_imeis` checks both rows. This path was already
  correct; the bug was only the missing fatal classification that caused the
  infinite restart loop before the second slot was ever filled.

## [1.0.9] - 2026-09-05

An IMEI that is already active on the domestic network now blocks its profile
instead of being retried forever.

### Fixed

- On the customs login page the site reports a permanently rejected device as
  `خطا در استعلام imei از سامانه همتا : شناسه (IMEI) وارد شده قبلا در شبکه داخلی
  کشور ثبت و فعال شده و امکان ورود آن وجود ندارد`. That wrapper is also how a
  genuine transient HAMTA inquiry failure arrives, so the message was classified
  as retryable: the robot refreshed the captcha and resubmitted without limit
  (`max_attempts=None`), never reached the fatal branch, and never marked the
  profile unusable. The permanent reason inside the wrapper now wins — the
  profile is marked `blocked`, the busy claim is released, the reason is stored,
  and the robot stops.
- Fatal detection is now a single function, `is_imei_fatal_error()`, consulted
  before every retry decision, so the retry classifier and the post-submit branch
  can no longer disagree. A stale captcha warning sitting on the page alongside a
  permanent rejection can no longer rescue it.
- A HAMTA inquiry failure with no permanent reason (`خطا در دریافت پاسخ از سازمان
  مربوطه`, `لطفا مجددا تلاش کنید`, `Invalid timestamp`) is still retried and
  leaves the profile runnable.
- Cloning a blocked profile now clears its `runtime_error` as well as its status.
  The clone was already reset to `pending`, but it inherited the old reason and
  the card kept showing "غیرقابل ورود" for a profile that was actually runnable.
  The clone is the intended route back to a usable profile, since editing one
  deliberately preserves runtime status.

## [1.0.8] - 2026-09-04

Completes v1.0.7: the settings import now works for the upgrade path the release
notes actually describe.

### Fixed

- v1.0.7 only imported settings that sat in the folder the new code ran from. The
  documented upgrade — unzip the new version into a NEW folder next to the old one
  — left the application folder empty, so nothing was imported and the Telegram
  token had to be entered again, the very problem v1.0.7 set out to remove.
  Sibling install folders are now searched as well: the most recently written one
  wins and is taken as a whole, so `profiles.json` and `estelam_store.json` can
  never come from different installs. A folder only counts as an install if it
  contains `panel.py`, and `IRICA_IMPORT_FROM` names a source explicitly for the
  case where the old install is on another drive.
- The panel prints which folder it imported from, so a wrong source is visible
  immediately instead of silently.

### Added

- `find_legacy_source()` / `imported_from()` in `app_paths.py`, with 12 tests
  covering sibling detection, install-marker filtering, the explicit override, a
  bad override, mtime ordering and the no-mixing rule.

### Fixed (build)

- The release build no longer depends on a single package index. It tries
  `pypi.org`, the runflare mirror and tsinghua in order, logs which one served the
  wheels, and only fails when all of them fail (`IRICA_PIP_INDEX` overrides the
  list). The mirror the build had been pinned to since v1.0.3 stopped serving
  packages — HTTP 200 on `/simple/` but "from versions: none" for every
  requirement — which broke the v1.0.8 build outright.

## [1.0.7] - 2026-09-04

Settings survive an update. The Telegram token, profiles, device inquiries and
logs no longer live inside the program folder, so replacing it never wipes them.

### Fixed

- Configuration, state and logs resolve to a per-user data directory outside the
  release: `%LOCALAPPDATA%\irica` on Windows, `$XDG_DATA_HOME/irica` on POSIX,
  overridable with `IRICA_DATA_DIR` for a portable install. Every module that
  persists anything follows it — the panel, `telegram_notify`, both estelam
  robots, `gomrok`, the Cloudflare relays and the SOCKS5 proxy reader.
- Upgrading needs no manual copying: files found inside an older release folder
  are imported on first run. Copies, not moves, so rolling back to the old build
  still works, and an existing file in the data directory is never overwritten.
- `save_telegram_config` now accepts a list of chat IDs as well as the UI's
  newline/comma text, so what `/telegram-config` returns can be posted straight
  back.
- The screenshot route accepts both the new and the pre-upgrade screenshot roots;
  containment is still enforced against each.

### Added

- `app_paths.py`: data-directory resolution, `data_path`/`data_subdir` (both
  reject path traversal) and one-shot legacy migration.
- The release verifier fails if any config or state path resolves inside the
  bundle, and the release's `FIRST_RUN.txt` documents where settings live.

## [1.0.6] - 2026-09-04

Makes the robot's browser window reviewable by hand: the whole page is now inside
the window, and it can be scrolled with the mouse.

### Fixed

- **Part of the page sat outside the window and could not be scrolled to.** The
  mobile emulation pinned the layout viewport to 932 CSS px (a real iPhone's
  height), but a 430x932 window is 516x932 in practice and 151 px of that is
  browser chrome, so only 781 CSS px of page were ever visible. Two consequences,
  both reported from the field on the customs login page: the bottom 151 CSS px
  were permanently off-window, and on any page shorter than 932 CSS px
  `scrollHeight` equalled `clientHeight`, so there was no scroll range at all --
  mouse wheel and keyboard had nothing to move. The emulated viewport height is
  now `0`, which CDP reads as "do not override this axis": the viewport equals the
  window's real content area, nothing is clipped, the scroll range is correct, and
  it follows a window resize. Layout width (430), pixel ratio (3.0) and the mobile
  user agent are unchanged, so the sites keep serving their mobile pages.

### Added

- **Drag-to-scroll with the mouse**, like a finger on a phone. Mobile emulation
  uses overlay scrollbars that occupy 0 px of layout and cannot be grabbed, and
  no CSS or Chrome flag brings a classic scrollbar back (four approaches were
  measured, all 0 px). `Emulation.setEmitTouchEventsForMouse` makes a mouse drag
  scroll the page instead. Mouse wheel and keyboard (PageDown/End/arrows) work as
  before.
- `captcha_collector.scroll_page(driver, "down"|"up"|"top"|"bottom")` for scripted
  or automated review scrolling.
- `lab/scripts/live_check_manual_scroll.py` (16 checks) and
  `lab/scripts/live_check_captcha_crop.py` (8 checks), which drive real Windows
  mouse input through `SendInput` against the actual robot drivers.

### Verified

- Captcha cropping is **bit-identical** before and after the change: same crop
  size and same SHA-256 of the pixel data (`lab/scripts/probe_crop_ab.py`). The
  crop scale stays 3.0.
- Every robot click path is unaffected with drag-to-scroll active: Selenium
  `.click()`, the project's own touch/pointer/mouse dispatch chain, `send_keys`,
  checkbox toggling and `scrollIntoView`
  (`lab/scripts/probe_click_with_dragscroll.py`).
- Real OS mouse wheel and real OS mouse drag both scroll the robot's window, for
  the customs and HAMTA drivers alike; the bottom of a login-page-sized document
  is fully reachable in both.

## [1.0.5] - 2026-09-04

Hardens the bundled Python runtime so a missing dependency cannot surface on the
operator's machine.

### Fixed

- Added `install.bat` to verify the bundled Python runtime before startup, and
  `run_panel.bat` now runs that verification automatically.
- The `requests` runtime dependencies are installed explicitly and kept inside the
  bundle: `urllib3`, `certifi`, `charset-normalizer`, `idna`.
- Release builds disable user-site packages and verify the complete `requests`
  import closure both before packaging and during bundle verification.

## [1.0.4] - 2026-09-04

Fixes a release-only defect that made every inquiry and registration robot exit
immediately on the user's machine while the panel reported "started".

### Fixed

- **Robots ran under a foreign interpreter.** The panel spawned every robot with
  a hardcoded `C:\Program Files\Python310\python.exe`. Where that install exists
  and carries PyTorch, the captcha solvers selected their torch backend, whose
  modules (`solver/predict.py`, `Hamta-solver/model.py`) are deliberately not
  shipped, so each robot died with `ModuleNotFoundError` before opening a
  browser. Robots now run under the panel's own interpreter — the bundled
  `python.exe` in a release — with `IRICA_ROBOT_PYTHON` as a development
  override. Same fix in the Cloudflare relay launchers.
- **Isolated runtime could not import project modules.** The bundled embedded
  CPython is isolated: `PYTHONPATH` is ignored and a script argument does not put
  its own directory on `sys.path`. `robot_launcher.py` and `panel.py` now repair
  `sys.path` before the first project import; without it the launcher itself, and
  the panel's lazy `import exitban` in `/exitban-inquiry`, failed at runtime.
- **Solver backend selection required only `torch`.** Both solvers now verify the
  whole torch backend (`predict` / `model` importable) before choosing it, so an
  unrelated torch install cannot select a backend that cannot load.
- **Child robots bypassed the launcher.** `estelam_all.py` and
  `profile_inquiry.py` spawn their children through `robot_launcher.py`, so the
  path repair and startup-traceback capture apply to them too.

### Changed

- Release gates run their probe **through `robot_launcher.py` with a script
  argument** — the exact shape the panel uses. The previous `python -c` probe
  supplied a manual `sys.path.insert(0, '.')`, which masked both failures above
  and let v1.0.3 ship green.
- New regression suite `lab/tests/test_robot_interpreter.py` (15 tests) covers
  interpreter resolution, launcher bootstrap under `-I`, child spawns, and solver
  backend selection.

## [1.0.3] - 2026-08-18

Adds optional per-run system diagnostics for troubleshooting robot startup and
runtime failures on other Windows machines.

### Added

- Debug log switch and log viewer under General Settings.
- Timestamped per-run logs under `logs/system/` when enabled.
- Common launcher that captures pre-import failures, environment, arguments,
  stdout/stderr and tracebacks.
- Safe system-log listing and bounded file-reading endpoints.

## [1.0.2] - 2026-08-17

Ships the per-profile device inquiries in the packaged release.

### Added

- **Per-profile device inquiry.** A profile's saved IMEIs can be checked against
  HAMTA, customs, or both from the profile's own *استعلامات* tab; results, status
  and errors are stored on the profile (`POST /profile-device-inquiry`, executed
  by `profile_inquiry.py` as a tracked subprocess).
- **Read-only profile overview.** Selecting a profile shows its data without
  editable inputs; editing is an explicit action.

### Fixed

- **`profile_inquiry.py` was missing from the bundle.** `panel.py` spawns it for
  every inquiry, but it was absent from the packaged file list, so the inquiry
  button would have failed on a user's machine. It now ships, and the verifier
  parses `panel.py` for every script it spawns and fails when any of them is not
  in the bundle — the same class of bug as the v1.0.1 hamta solver, now caught
  structurally rather than by name.
- Inquiry robots honour `IRICA_ESTELAM_STORE`, so a per-profile run uses its own
  temporary store instead of the shared `estelam_store.json`.

## [1.0.1] - 2026-08-17

Fixes the hamta inquiry captcha in the packaged release.

### Fixed

- **Hamta inquiry captcha was broken in the v1.0.0 bundle.** `estelam_hamta.py`
  reads the modal captcha through its own solver package (`Hamta-solver/`), which
  is separate from the gomrok one and was still torch-only; the v1.0.0 bundle
  shipped neither that package nor PyTorch, so the hamta robot raised
  `ModuleNotFoundError: No module named 'live_solve'` the moment it reached the
  captcha. The hamta CRNN is now exported to ONNX (55.0 MB `.pt` → 18.3 MB
  `.onnx`) and the bundle carries a torch-free backend for it.

### Added

- `Hamta-solver/onnx_solve.py` — `OnnxHamtaSolver`, an onnxruntime backend with
  the same surface as `HamtaSolver` (`read_pil` / `read_bgr` / `read_file`) and
  numpy ports of the CTC greedy decode and confidence signals.
- `Hamta-solver/live_solve.load_solver()` now selects a backend: torch when
  importable (development, unchanged), otherwise ONNX.
- `lab/scripts/export_hamta_onnx.py` — exports the hamta checkpoint and refuses
  to leave an artifact behind unless both engines return identical result dicts
  (number, text, confidence and snap distance) on every validation sample.
- Bundle build and verifier now ship and check the hamta solver, and the smoke
  test resolves it through `estelam_hamta.load_solver()` rather than the class,
  so a broken `sys.path` cannot reach a user.

### Changed

- `Hamta-solver/utils.py` and `live_solve.py` import torch lazily, so the modules
  stay importable in a torch-free runtime (`number_decode` only needs the
  pure-python edit distance from `utils`).

## [1.0.0] - 2026-08-17

First packaged Windows x64 release.

### Added

- **Per-profile browser visibility.** Browsers launch headed but parked
  off-screen, so several robots can run without cluttering the desktop and
  without the throttling a truly headless session would suffer. Each running
  profile gets a live show/hide button in the panel (`POST /browser-visibility`,
  Win32 `SetWindowPos`, independent of the Selenium session).
- **Paste button beside every profile input**, normalizing Persian/Arabic digits,
  stripping direction marks, with a select-and-hint fallback when the browser
  denies clipboard access.
- **Already-registered detection.** If the pre-payment invoice page appears right
  after the IMEI step, the profile has already been through the flow: the robot
  captures the invoice, saves the payment link, records the profile as *success*
  (with link and screenshot), reports to the panel log and Telegram, and stops
  instead of burning captcha attempts on a pointless retry.
- **Active-tab highlighting** across every tab strip (main navigation, log
  sub-tabs, profile form tabs, and the inquiry service picker): brand gradient
  fill, white label, and an underline, so the current tab is unambiguous.
- **ONNX captcha solver path.** The CRNN solver can run on `onnxruntime`, which is
  what the packaged release ships; PyTorch is no longer required at runtime.

### Changed

- **Durable profile store.** `profiles.json` is now written atomically
  (temp file + `fsync` + `os.replace`) with a `.bak` of the last good copy and an
  in-process cache. A corrupt or partially written file is never overwritten with
  an empty store, so profiles can no longer vanish under load.
- **imeiStep latency.** A one-shot page probe replaces the fixed captcha wait: a
  healthy page is confirmed in roughly a tenth of a second, and a page that
  loaded without a captcha is detected and refreshed almost immediately instead
  of after a ten-second wait.
- **Bounded Telegram delivery.** Every send carries a hard deadline. On timeout
  the robot logs that the screenshot was kept locally and continues to the next
  step, rather than stalling at the pre-payment stage when Telegram is
  unreachable.
- **Submit is always on.** The auto-submit toggle is gone from the UI; disabling
  it is a debugging-only CLI flag (`--no-submit`).

### Fixed

- `--brand2` was undefined in the inquiry page theme, which made the active
  button's gradient invalid and render blank.
- The log view hard-coded the active class on the profile sub-tab regardless of
  the selected mode.
- A blocked `execute_script` is bounded by the page-load timeout, not the script
  timeout; the page probe now clamps both, so a healthy page is never refreshed
  by mistake.
