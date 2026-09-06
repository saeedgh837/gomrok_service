# Changelog

All notable changes to this project are documented here.
This project uses [Semantic Versioning](https://semver.org/).

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
