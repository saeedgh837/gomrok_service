# Changelog

All notable changes to this project are documented here.
This project uses [Semantic Versioning](https://semver.org/).

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
