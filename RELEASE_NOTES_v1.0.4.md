# irica v1.0.4 - Windows x64

Packaged build of the IRICA registration panel and robots.
Bundle: `irica-v1.0.4-win64.zip`
SHA256: `PENDING`

## Why this release

In v1.0.3 every inquiry (HAMTA and customs) and every registration robot exited
immediately on the operator's machine, while the panel reported that the run had
started. The panel launched robots with a hardcoded interpreter path
(`C:\Program Files\Python310\python.exe`) instead of the Python shipped inside the
bundle. On a machine where that interpreter exists and has PyTorch installed, the
captcha solvers selected their PyTorch backend — whose model files are
deliberately not part of the release, because the bundle ships the same models in
ONNX form — so each robot stopped with `ModuleNotFoundError` before a browser
window ever opened.

This release makes robots run under the interpreter that owns the shipped
libraries, and closes the gap in the release checks that let the defect through.

## Fixed

- **Inquiries and registration start again.** Robots run under the panel's own
  interpreter, which in a release is the bundled `python\python.exe`. No Python
  installation on the machine can interfere with them any more.
- **Captcha models load reliably.** Both solvers now confirm that a PyTorch
  backend is complete before choosing it, and otherwise use the ONNX models that
  ship with the bundle.
- **Startup failures no longer depend on the environment.** The bundled runtime
  ignores external Python paths; the panel and the robot launcher now set up their
  own import path, so project modules resolve in every code path — including the
  exit-ban inquiry, which previously failed at request time.
- **Combined inquiries.** «همه استعلام‌ها» and the per-profile inquiry start
  their sub-robots the same way the panel does, so a failure inside them is
  captured in the debug log instead of being lost.

## Changed

- Release verification now starts a robot exactly the way the panel does. The
  previous check ran the same code in a slightly different way, which is why
  v1.0.3 passed verification and still failed for the operator.
- New automated regression suite covering interpreter selection, import-path
  setup, sub-robot spawning and captcha backend selection (14 tests).

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

Nothing needs to be reconfigured after this upgrade, and no Python installation
on the machine is required or used.

## Verify your download

```powershell
Get-FileHash .\irica-v1.0.4-win64.zip -Algorithm SHA256
```

Compare the result with `SHA256SUMS.txt` attached to the release.

## Known limitations

- Windows x64 only.
- Chrome and chromedriver are not bundled and must match.
- Debug logs may contain profile identifiers and site error messages. Share only
  the specific file needed for diagnosis and delete old logs when no longer needed.
