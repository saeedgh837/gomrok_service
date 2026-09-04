# irica v1.0.8 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.8-win64.zip`
SHA256: `c2090f41b6692bdd01801b55e5af2838ec40f8864b2c5a6f76021f99c5877405`

## Why this release

v1.0.8 finishes what v1.0.7 started.

v1.0.7 moved your settings out of the program folder so an update could not wipe
them, and imported the old ones automatically. But that import only looked
**inside the folder the new version runs from**. The upgrade the release notes
describe — unzip the new version into a **new** folder next to the old one —
leaves that folder empty, so nothing was found and the Telegram token still had
to be entered again.

## Fixed

**The settings import now finds your old install.** Whether you unzip over the
old folder or beside it, the previous install is located and its settings are
imported on first run. The panel prints exactly which folder it read from:

```
>>> Tanzimat va dade-ha: C:\Users\<you>\AppData\Local\irica
>>> Az nasKHe-ye ghabli montaghel shod (D:\irica-v1.0.4-win64): telegram_config.json, profiles.json, ...
```

If several old installs are sitting side by side, the most recently used one is
chosen — and taken as a whole, so profiles and device inquiries can never be
mixed from two different versions. A folder only counts as an install if it
contains the program itself, so an unrelated backup directory is never read.

## Requirements

- Windows 10 or 11, 64-bit.
- Google Chrome installed.
- `C:\chromedriver.exe` matching the installed Chrome major version.

## Install or upgrade

Unzip into a new folder, keep the old folder where it is, and run
`run_panel.bat`. Open <http://127.0.0.1:8750/site-1>.

Check the startup lines shown above: they tell you where your settings now live
and where they came from. Once you have confirmed the Telegram token and your
profiles are present, the old folder can be deleted.

If the old install is somewhere the new one cannot see (another drive, an unusual
path), name it once:

```bat
set IRICA_IMPORT_FROM=D:\irica-v1.0.4-win64
run_panel.bat
```

Your settings are **copied**, not moved, so the old version keeps working if you
decide to go back.

The release archive contains no credentials, profiles or logs.

## Portable install

To keep the data next to the program — a USB drive, for instance:

```bat
set IRICA_DATA_DIR=%~dp0data
run_panel.bat
```

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.8-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer
  needed.
