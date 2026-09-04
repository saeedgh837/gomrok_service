# irica v1.0.6 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.6-win64.zip`
SHA256: `PENDING`

## Why this release

On the customs login page, part of the form was outside the browser window and
nothing could be scrolled into view, so the page could not be checked by hand.

The robot's browser runs in mobile mode, and its page size was pinned to the
height of a real phone screen (932 points). A window of that height is not that
tall inside: the browser's own toolbars take 151 points, leaving 781 points of
page visible. Everything below that was permanently outside the frame — and
because the page itself was shorter than 932 points, the browser considered it
fully visible and offered no scrolling at all. The mouse wheel had nothing to
move.

## Fixed

- **The whole page now fits the window.** The page size follows the real window
  instead of a fixed phone height, so nothing is cut off, scrolling works when a
  page is longer than the window, and the page adjusts if the window is resized.
- **Scrolling by hand works.** In mobile mode the scrollbar is the thin overlay
  kind that cannot be grabbed with a mouse, so dragging was added: hold the left
  mouse button and move, exactly like swiping on a phone. The mouse wheel and the
  keyboard (Page Down, End, arrow keys) work as well.

Nothing about the sites, the login flow, or the automatic captcha reading
changes. The mobile layout is unchanged, and the captcha images the robot cuts
out are identical to before, down to the individual pixel.

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

Nothing needs to be reconfigured after this upgrade.

## Reviewing a page by hand

While a robot is running, press **👁 نمایش مرورگر** on its profile to bring the
browser window on screen. Inside that window you can:

- drag the page with the left mouse button (like a finger on a phone),
- use the mouse wheel,
- use Page Down / Page Up / End / Home / arrow keys.

Press **🙈 مخفی کردن مرورگر** to move the window back off screen. The robot keeps
working either way.

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.6-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer needed.
