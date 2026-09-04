# irica v1.0.7 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.7-win64.zip`
SHA256: `PENDING`

## Why this release

Setting up the Telegram bot again after every update, and hand-copying profiles
and inquiries across, was slow and easy to get wrong: miss one file and
notifications silently stop or the profile list comes back empty.

The cause was where those files were kept. `telegram_config.json`,
`profiles.json`, `estelam_store.json` and the logs all lived **inside** the
program folder — the same folder that gets replaced on every update.

## Fixed

**Settings now live outside the program folder.** Everything you configure is
kept in:

```
%LOCALAPPDATA%\irica
```

To update from now on: unzip the new release into a new folder and run it. That
is all. Nothing needs to be copied, and the Telegram bot never needs to be set
up twice.

**Upgrading from v1.0.6 or older is automatic.** On the first run, the new
version finds the files in your old release folder and imports them — token,
chat IDs, profiles, device inquiries, inputs and logs. They are **copied**, not
moved, so if you go back to the old version it still works exactly as before.

The panel prints the location on startup, so you can always see where your data
is:

```
>>> Tanzimat va dade-ha: C:\Users\<you>\AppData\Local\irica
```

## Requirements

- Windows 10 or 11, 64-bit.
- Google Chrome installed.
- `C:\chromedriver.exe` matching the installed Chrome major version.

## Install or upgrade

Unzip into a new folder and run `run_panel.bat`, then open
<http://127.0.0.1:8750/site-1>.

Upgrading from v1.0.6 or earlier: unzip **next to** your old folder (do not
delete it until the first run has finished) and start the panel. Your settings
are imported automatically. After you have confirmed everything is there, the old
folder can be deleted.

The release archive contains no credentials, profiles or logs.

## Portable install

To keep the data next to the program instead — a USB drive, for instance — set
`IRICA_DATA_DIR` before starting:

```bat
set IRICA_DATA_DIR=%~dp0data
run_panel.bat
```

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.7-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer
  needed.
