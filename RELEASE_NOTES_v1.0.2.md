# irica v1.0.2 — Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.2-win64.zip` — 91.9 MB zipped, 200.9 MB unpacked
SHA256: `2660b2ab5fffa42f46c3ab116dd445142bbde034b7ae4bd233e06a7e5c044feb`

## Why this release

**v1.0.1 did not ship `profile_inquiry.py`.** The per-profile device-inquiry
button in the panel spawns that script as a subprocess, but it was absent from
the packaged file list, so the button would fail on a user's machine. This
release bundles it and adds a structural guard: the verifier now parses
`panel.py` for every script it spawns and refuses to pass if any of them is
missing from the bundle — the same class of bug as the v1.0.1 hamta solver,
caught by construction rather than by a hand-kept list.

Also new since v1.0.1: the per-profile device inquiry itself (HAMTA / customs /
both, from the profile's *استعلامات* tab) and a read-only profile overview.

## What is inside

A self-contained folder. It carries its own Python runtime and every library it
needs, so nothing is downloaded on first run — important on a filtered
connection. PyTorch is **not** included: both captcha solvers run on
`onnxruntime`, which is a few megabytes instead of a gigabyte.

```
irica-v1.0.2-win64/
  python/                  embedded CPython 3.10 x64 + libraries
  panel.py gomrok.py ...   application code
  profile_inquiry.py       per-profile inquiry runner
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

### Upgrading from v1.0.0 / v1.0.1

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
Get-FileHash .\irica-v1.0.2-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to this release.

## Known limitations

- Windows x64 only. The bundled runtime and the window-positioning code are
  Windows-specific.
- Chrome and chromedriver are not bundled; they must match each other.
- Both captcha solvers are ONNX exports of their PyTorch checkpoints, each
  verified to produce identical output on the validation set used at build time.
