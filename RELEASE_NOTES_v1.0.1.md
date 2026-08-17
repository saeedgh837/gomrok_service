# irica v1.0.1 — Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.1-win64.zip` — 91.9 MB zipped, 200.8 MB unpacked
SHA256: `fb540887378b1b92383e59905e70831f33d5b15d4115d0434b709454db52f8f7`

## Why this release

**v1.0.0 could not solve the hamta inquiry captcha.** The hamta robot reads its
captcha through a second, separate solver package (`Hamta-solver/`) that was
still PyTorch-only, and the bundle shipped neither that package nor PyTorch — so
`estelam_hamta.py` failed with `ModuleNotFoundError: No module named
'live_solve'` as soon as it reached the captcha. The gomrok flow was unaffected.

The hamta model is now exported to ONNX as well (55.0 MB `.pt` → 18.3 MB
`.onnx`) with a torch-free backend, verified to return **identical** readings to
the PyTorch model. Upgrade if you use the hamta inquiry.

## What is inside

A self-contained folder. It carries its own Python runtime and every library it
needs, so nothing is downloaded on first run — important on a filtered
connection. PyTorch is **not** included: both captcha solvers run on
`onnxruntime`, which is a few megabytes instead of a gigabyte.

```
irica-v1.0.1-win64/
  python/                  embedded CPython 3.10 x64 + libraries
  panel.py gomrok.py ...   application code
  solver/                  gomrok captcha model (ONNX) + inference code
  Hamta-solver/            hamta captcha model (ONNX) + inference code
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

### Upgrading from v1.0.0

Unzip into a new folder and copy your `profiles.json`, `estelam_store.json` and
`*_config.json` files across. Nothing in the data format changed.

## Notes

- Profiles are stored in `profiles.json` beside the executable, written
  atomically with a `.bak` fallback. Back that file up if the profiles matter.
- Browsers start parked off-screen. Use the show/hide button on a running
  profile to bring its window into view.
- If Telegram is unreachable, screenshots are still saved under `screenshots/`
  and the run continues; the log records the timeout.

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.1-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to this release.

## Known limitations

- Windows x64 only. The bundled runtime and the window-positioning code are
  Windows-specific.
- Chrome and chromedriver are not bundled; they must match each other.
- Both captcha solvers are ONNX exports of their PyTorch checkpoints, each
  verified to produce identical output on the validation set used at build time.
