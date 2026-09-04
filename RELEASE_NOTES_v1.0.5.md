# irica v1.0.5 - Windows x64

## Fixed

- Added `install.bat` to verify the bundled Python runtime before startup.
- `run_panel.bat` now runs the runtime verification automatically.
- Requests runtime dependencies are installed explicitly and kept inside the bundle:
  `urllib3`, `certifi`, `charset-normalizer`, and `idna`.
- Release builds disable user-site packages and verify the complete requests import
  closure before packaging and during bundle verification.

## Verification

- Bundle smoke test passed with the bundled Python and ONNX solvers.
- Release verifier passed for security, completeness, imports, and `/site-1` and
  `/site-2` panel startup.
