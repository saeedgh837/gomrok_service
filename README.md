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

## Configuration and data

Configuration, profiles, state and logs live in a per-user data directory
**outside** the program folder, so replacing the program on an update never
touches them:

| Platform | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\irica` |
| POSIX | `$XDG_DATA_HOME/irica` (default `~/.local/share/irica`) |
| Override | `IRICA_DATA_DIR` (any path — use it for a portable install) |

Set the Telegram bot token and chat IDs in the panel itself; they are written
there. `relay_config.json` and `upstream_proxy.json` also belong in that
directory — the `*.example.json` templates in this repo show their shape and are
the only versions ever committed.

Files left in an older install are copied into the data directory automatically
on first run — the application folder and its siblings are both searched, since
an upgrade normally unzips into a new folder next to the old one, and
`IRICA_IMPORT_FROM` names a source explicitly. Copies, not moves, so a rollback
still works.

## Versioning

See `CHANGELOG.md`. Releases are tagged `vX.Y.Z` and follow
[Semantic Versioning](https://semver.org/).
