# irica v1.0.9 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.9-win64.zip`
SHA256: `PENDING`

## Why this release

When a device has already been registered and activated on the domestic network,
the customs login page answers:

> خطا در استعلام imei از سامانه همتا : شناسه (IMEI) وارد شده قبلا در شبکه داخلی
> کشور ثبت و فعال شده و امکان ورود آن وجود ندارد

That device can never be entered. But the robot kept trying: it read a new
captcha, submitted again, got the same answer, and repeated — with no limit.

The reason is that the site wraps this permanent rejection in the same sentence
it uses for a temporary HAMTA inquiry failure (`خطا در استعلام imei از سامانه
همتا`), and a temporary failure genuinely should be retried. The robot only saw
the wrapper, so it treated a dead end as a passing glitch.

## Fixed

**A permanently rejected IMEI now stops the robot and blocks the profile.** The
reason inside the wrapper is read, not just the wrapper:

- the profile is marked **⛔ غیرقابل ورود** in the panel,
- the reason from the site is stored and shown on the profile card,
- the start button is disabled, and starting it again is refused,
- the profile is released (no longer "busy"), and the robot exits cleanly.

**Temporary failures still retry, exactly as before.** `خطا در دریافت پاسخ از
سازمان مربوطه`, `لطفا مجددا تلاش کنید`, `Invalid timestamp` and a wrong captcha
are all still retried in the same browser, and they leave the profile runnable.

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
Get-FileHash .\irica-v1.0.9-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- A profile blocked this way stays blocked, because editing it keeps its status on
  purpose. To retry with a different device, use **📄 کپی** on the profile card:
  the copy starts clean (pending, runnable) and you can put the new IMEI in it.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer
  needed.
