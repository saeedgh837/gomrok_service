# Release notes — v1.0.11

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.11-win64.zip`
SHA256: `6a620a249685c91a11606abae5c34f0c4a1e66a4df0dfdc37ecdb7b7726723a9`

## Why this release

Three operator-facing requests:

1. **Show the version in the panel.** The running version (from the bundled
   `VERSION` file) is now visible on all three pages — site picker, main
   dashboard header, and the device-inquiry page. This removes the guesswork
   about which build an install is running (a confusion that cost debugging
   time during the v1.0.9/v1.0.10 rollouts).
2. **Show the profile name on inquiry results.** Device-inquiry result rows
   previously showed only serials and statuses. The card heading now reads
   `استعلام وضعیت دستگاه‌ها — <پروفایل> — <سرویس>` and each result row carries
   `👤 پروفایل: <name>`, so results are attributable at a glance.
3. **A way back from a blocked profile.** Blocked profiles used to be a dead
   end in the edit form (the route back was the undocumented 📄 کپی clone).
   The edit form now shows `🔓 آزادسازی و شروع مجدد` for a blocked profile.
   `POST /release-blocked` atomically resets the profile to `pending`, drops
   the runtime error text, re-enables it, and clears runtime/inquiry
   bookkeeping. Busy profiles are refused (409) — stop the robot first.

## Verification

- `lab/scripts/run_panel_tests.py` → 184 OK (173 + 11 new feature tests,
  RED before implementation).
- Live panel probe on a throwaway data dir: version string rendered on all
  three pages; release button present in site-1 markup; `/release-blocked`
  unblocked a real blocked profile in the probe store (status → `pending`,
  error cleared, enabled) and refused an unknown id with 409.
- `lab/scripts/verify_windows_release.py` → bundle verified.

## Upgrade

Unzip into a NEW folder and run `run_panel.bat`. Config and state live in
`%LOCALAPPDATA%\irica` and are picked up automatically; do not copy data by
hand. The previous install folder can be kept for rollback.
