# irica v1.0.10 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.10-win64.zip`
SHA256: `f0f8d9cde65a68be7915df14765b24b1a67db5fd4eace9269cd042570bd903fb`

## Why this release

A dual-SIM device on the customs login page was looping forever with the message:

> دستگاه شما دارای دو اسلات است لطفا شناسه دیگر را نیز وارد نمایید

Your profile **already has two IMEIs** stored (`['350912504141674', '350912504227069']`), but the robot never filled the second slot because this message was **unclassified** — not fatal, not retryable, not transient. It fell into the "other error" branch: logged, restarted the browser, tried again from the beginning, looped forever.

## Fixed

**The dual-SIM message is now fatal.** The marker `دو اسلات` is in `IMEI_FATAL_MARKERS`, so:

- The profile is blocked with the reason shown on the card
- The robot stops instead of looping
- The operator sees "⛔ غیرقابل ورود — دستگاه شما دارای دو اسلات است..." and knows exactly what happened
- To retry with both IMEIs, use **📄 کپی** on the profile card (the clone starts clean and the panel's IMEI fields accept both)

**The fill/verify path for two IMEIs was already correct.** When `len(imeis) >= 2`:
1. `fill_imeis` clicks the "+" button to add the second row
2. Fills both rows via JS (triggers only `input` event, no phantom rows)
3. `verify_imeis` reads back both rows and confirms they match
4. `cleanup_extra_empty_imei_rows` removes only truly empty extra rows, preserving the two filled ones

This applies to both the customs registration robot (`gomrok.py`) and the customs inquiry robot (`estelam_gomrok.py`) — both use the shared `fill_imeis` / `verify_imeis`.

## Requirements

- Windows 10 or 11, 64-bit.
- Google Chrome installed.
- `C:\chromedriver.exe` matching the installed Chrome major version.

## Install or upgrade

Unzip into a new folder, keep the old folder where it is, and run
`run_panel.bat`. Open <http://127.0.0.1:8750/site-1>.

Your settings, profiles and inquiries are kept outside the program folder
(`%LOCALAPPDATA%\irica`) and are imported automatically on first run if you are
coming from v1.0.6 or older. Nothing needs to be copied by hand.

The release archive contains no credentials, profiles or logs.

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.10-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- A profile blocked this way stays blocked. If you want to retry with both
  IMEIs, use **📄 کپی** on the profile card: the copy starts clean (pending,
  runnable) and you can put both IMEIs in it.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer
  needed.