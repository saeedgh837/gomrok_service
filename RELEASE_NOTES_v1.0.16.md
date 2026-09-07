IRICA v1.0.16 — Smarter captcha solving (fewer wrong submits)

What's new
----------
Both customs robots (inquiry AND registration) now read captchas with a
confidence score:
  * Low-confidence reads (below 0.6) trigger a free captcha refresh and
    re-read (up to 3 times) instead of submitting a wrong guess.
  * After a slow page load or F5, the robot now waits until the captcha
    image has completely stopped changing before reading it — closing the
    race that caused most "کد اعتبار سنجی صحیح نمی باشد" errors.
  * Net effect: noticeably fewer failed attempts per inquiry/registration,
    and each failed captcha costs a free refresh instead of a full form
    round-trip.

Tuning
------
Advanced users can lower the confidence bar by setting the environment
variable IRICA_CAPTCHA_MIN_CONF (e.g. 0.45 to submit more aggressively).

Upgrade
-------
Double-click IRICA-Setup-v1.0.16.exe. Your data in %LOCALAPPDATA%\irica
(profiles, settings, logs) is untouched. If the panel is running, the
installer closes it first. Close any manually-started panel yourself.

SmartScreen note
----------------
The installer is not code-signed. If Windows SmartScreen appears:
click "More info" -> "Run anyway".

Checksums (SHA-256)
-------------------
8ca151bbe2946b4ef71a4d5b7bbb03a1faa5ea89b1e78540a5941e4b9dc7a16a  irica-v1.0.16-win64.zip
4d056c8fa7aeca4bd25085976488dc11f4daea24b5c491c67308fab3cd8a1305  IRICA-Setup-v1.0.16.exe
