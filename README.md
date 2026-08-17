# gomrok_service

Automated registration service for the IRICA (gomrok) website: a local web panel
that drives one or more browser robots through device registration, reads the
site captcha with an OCR model, and reports each step to a Telegram bot.

## Components

- **panel.py** — local web panel (default `http://127.0.0.1:8750`). Manages
  profiles, launches robots, shows logs, and hosts the device-inquiry dashboard.
- **gomrok.py** — the registration robot for one profile.
- **solver/** — CRNN captcha model and inference code (PyTorch for training,
  ONNX for the packaged runtime).
- **estelam_*.py** — device health-inquiry robots (gomrok / hamta / all).
- **telegram_notify.py** — bounded Telegram reporting.

## Install from a release (recommended for operators)

Download the latest `irica-vX.Y.Z-win64.zip` from the
[Releases](../../releases) page and follow the bundled
`RELEASE_NOTES_vX.Y.Z.md`. The bundle is self-contained (its own Python + all
libraries); you only need Google Chrome and a matching `chromedriver.exe` at
`C:\chromedriver.exe`.

## Install from source (developers)

```bat
install.bat
```

Installs the dependencies from `requirements.txt` into your system Python, then:

```bat
python panel.py
```

## Configuration

Copy each template and fill it in. None of these are committed:

- `telegram_config.example.json` → `telegram_config.json`
- `upstream_proxy.example.json` → `upstream_proxy.json`
- `relay_config.example.json` → `relay_config.json`

## Versioning

See `CHANGELOG.md`. Releases are tagged `vX.Y.Z` and follow
[Semantic Versioning](https://semver.org/).
