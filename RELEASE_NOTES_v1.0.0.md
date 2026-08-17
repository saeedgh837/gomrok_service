# irica v1.0.0 — Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.0-win64.zip` — 74.8 MB zipped, 182.5 MB unpacked
SHA256: `6179eed253f60a20555924523c46ba53cd518703c906d870dfd36f097d870598`

## What is inside

A self-contained folder. It carries its own Python runtime and every library it
needs, so nothing is downloaded on first run — important on a filtered
connection. PyTorch is **not** included: the captcha solver runs on
`onnxruntime`, which is a few megabytes instead of a gigabyte.

```
irica-v1.0.0-win64/
  python/                  embedded CPython 3.10 x64 + libraries
  panel.py gomrok.py ...   application code
  solver/                  captcha model (ONNX) + inference code
  dashboard-new.html       inquiry dashboard
  *.example.json           configuration templates
  run_panel.bat            start the panel
  README.md CHANGELOG.md
```

## Requirements

- Windows 10 or 11, 64-bit.
- **Google Chrome** installed.
- **chromedriver.exe matching your Chrome major version**, placed at
  `C:\chromedriver.exe`.
  Check your Chrome version at `chrome://settings/help`, then download the
  matching driver from
  <https://googlechromelabs.github.io/chrome-for-testing/>.
  A driver whose major version differs from Chrome will refuse to start.

## Install

1. Unzip anywhere with room to write, for example `C:\irica`.
   Avoid `C:\Program Files` — the app writes its state next to itself.
2. Copy the templates and fill them in:
   - `telegram_config.example.json` → `telegram_config.json`
     (bot token and chat IDs)
   - `upstream_proxy.example.json` → `upstream_proxy.json` (only if you route
     through a SOCKS5 proxy)
   - `relay_config.example.json` → `relay_config.json` (only if you expose the
     panel through the Cloudflare relay)
3. Double-click `run_panel.bat`, then open <http://127.0.0.1:8750/site-1>.

The zip ships **no** credentials and **no** profiles. `profiles.json` is created
on first use.

## Notes

- Profiles are stored in `profiles.json` beside the executable, written
  atomically with a `.bak` fallback. Back that file up if the profiles matter.
- Browsers start parked off-screen. Use the show/hide button on a running
  profile to bring its window into view.
- If Telegram is unreachable, screenshots are still saved under `screenshots/`
  and the run continues; the log records the timeout.

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.0-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to this release.

## Known limitations

- Windows x64 only. The bundled runtime and the window-positioning code are
  Windows-specific.
- Chrome and chromedriver are not bundled; they must match each other.
- The captcha solver is the ONNX export of the PyTorch checkpoint, verified to
  produce character-identical output on the validation set used at build time.
