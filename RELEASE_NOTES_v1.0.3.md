# irica v1.0.3 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.3-win64.zip`
SHA256: populated after the verified archive is built.

## Why this release

Release 1.0.2 could report that an inquiry process exited with code 1 without
preserving enough startup diagnostics to identify the failure on another Windows
machine. Version 1.0.3 adds optional per-run system diagnostics that capture the
launcher environment, Python executable, arguments, stdout/stderr, SystemExit and
full uncaught tracebacks, including failures that happen before a robot module can
finish importing.

## Added

- A `Debug log` switch under General Settings. It is off by default.
- One timestamped log per robot run under `logs/system/` when enabled.
- A searchable list of generated logs and an in-panel text viewer.
- Safe log-file API endpoints with filename validation and a 500 KB read limit.
- A common `robot_launcher.py` used by profile registration, customs/HAMTA
  inquiries, combined profile inquiries and exit-ban checks.

## Diagnostics workflow

1. Open General Settings.
2. Enable `Debug log`.
3. Start the failing robot or inquiry again.
4. Return to General Settings, refresh the log list and open the newest file.

Each file includes the service name, PID, current working directory, Python path,
arguments, normal output and the complete traceback when a process fails.

## Requirements

- Windows 10 or 11, 64-bit.
- Google Chrome installed.
- `C:\chromedriver.exe` matching the installed Chrome major version.

## Install or upgrade

Unzip into a new writable folder. For an upgrade, copy your local runtime files
from the old folder, especially `profiles.json`, `estelam_store.json`, and the
required `*_config.json` files. The release archive contains no credentials,
profiles or runtime logs.

Run `run_panel.bat`, then open <http://127.0.0.1:8750/site-1>.

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.3-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer needed.
