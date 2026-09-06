# Release notes — v1.0.12

Packaged build of the IRICA registration panel and robots.

| Asset | What it is | SHA256 |
|---|---|---|
| `IRICA-Setup-v1.0.12.exe` | **Recommended.** Click-to-install setup | `1eeb5788ad7fe20d8ec67477166d33b942eb14bc50d74d9734dff4541fa4c882` |
| `irica-v1.0.12-win64.zip` | Portable zip (previous method, still works) | `7421b3930a243d2cb71bec6fabc00cbb5ca5306828af3155d03e1202a8e782ba` |

## Why this release: a real installer

From now on, updating means **double-clicking one setup file** — no more
unzipping into a new folder per release (five old `irica-vX-win64` folders had
accumulated on the install drive).

### What the installer does

- Installs to `%LOCALAPPDATA%\Programs\IRICA` (per-user, **no admin rights,
  no UAC prompt**). The chosen folder is remembered for the next update.
- Creates a desktop + Start Menu shortcut ("IRICA") that starts the panel
  **without a console window** (`panel_gui.pyw` via `pythonw.exe`), plus a
  Start Menu link to the data folder and to uninstall.
- Optional "start with Windows" checkbox (off by default).
- **Upgrades in place**: run the new setup over the old install; files are
  replaced, your data is untouched.
- **Uninstaller never deletes user data.** Profiles, settings, and logs live
  in `%LOCALAPPDATA%\irica` (outside the install folder) and survive both
  updates and uninstall.

### First-run experience

`chromedriver.exe` (v152, matching the current Chrome) is now **bundled**
inside the install under `.tools\` — the old manual "download chromedriver"
step is gone. Resolution order stays: `C:\chromedriver.exe` override →
bundled `.tools` copy → Selenium Manager.

### Version-mismatch warning

Every robot start now compares the chromedriver version against the installed
Chrome's major version (registry). On a mismatch it prints/warns in Persian:
`هشدار نسخه: ChromeDriver نسخه X است اما Chrome نسخه Y — ربات ممکن است باز نشود…`
so the "robots suddenly stopped opening the browser" failure mode after a
Chrome auto-update is diagnosed in seconds, not hours.

### Single-instance guard

Starting the panel while another panel of this project is already running no
longer crashes with a bind error — it prints a clear message and opens the
running panel's browser tab instead.

## Verification (all real runs)

- 190 tests OK (184 existing + 6 new: start-gate logic, resolver order,
  compat warning, robot wiring).
- E2E installer test on this machine: silent install → 2619 files + uninstaller
  landed, shortcuts created → panel smoke-tested from the installed location
  (version badge, release button) → silent uninstall removed everything except
  bytecode leftovers and **kept the user-data canary file intact**.
- `verify_windows_release.py` → bundle verified (zip fallback path).

## Known limitations

- **SmartScreen**: the setup is unsigned (no code-signing certificate), so the
  first run may show "Windows protected your PC" — choose *More info → Run
  anyway*. This is expected and safe for this self-built binary.
- **Chrome auto-update** (major version change) still requires replacing
  `C:\chromedriver.exe` or letting Selenium Manager fetch a matching driver;
  the new warning makes the cause visible immediately.
- Uninstall leaves empty `__pycache__`/`python` dirs behind (bytecode created
  after install by design); no user data is involved.

## Upgrade from v1.0.10/v1.0.11

1. Download `IRICA-Setup-v1.0.12.exe`.
2. (If the panel is running, close it — the setup asks too.)
3. Double-click, keep the suggested folder, finish. Done — the panel opens.

The old zip method keeps working and needs nothing else.
