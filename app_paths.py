"""Where configuration, state and logs live.

The release is a folder the operator replaces wholesale on every update. As long
as `telegram_config.json`, `profiles.json` and friends sat inside that folder,
each upgrade meant copying them across by hand -- and missing one silently
switched off notifications or lost the profile list.

So they live in a per-user data directory instead, outside the release:

    Windows  %LOCALAPPDATA%\\irica
    POSIX    $XDG_DATA_HOME/irica  (default ~/.local/share/irica)
    override $IRICA_DATA_DIR       (any absolute or relative path)

`bootstrap()` copies any file still next to the application into that directory
on first run, so upgrading from a version that kept them locally needs no
manual step. Copies, not moves: a rollback to the old build keeps working.

Secrets never enter the repository or the release archive -- only the
`*.example.json` templates ship, and the release verifier fails the build if a
real config file appears inside the bundle.
"""
import os
import shutil

APP_DIR = os.path.dirname(os.path.abspath(__file__))

#: Everything the application persists. Anything added here is migrated out of
#: an old release folder on first run.
MIGRATED_FILES = (
    # settings
    "telegram_config.json",
    "upstream_proxy.json",
    "relay_config.json",
    "system_debug.json",
    # operator data
    "profiles.json",
    "profiles.json.bak",
    "inputs.json",
    "estelam_store.json",
    "estelam_store.json.bak",
    # runtime state and logs
    "site2_notif.json",
    "telegram_health.json",
    "logs.jsonl",
    "estelam_logs.jsonl",
    "hamta_logs.jsonl",
    "exitban_errors.jsonl",
    "cloudflare_public_url.txt",
)

_CACHE = []
_BOOTSTRAPPED = []


def _default_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "irica")


def data_dir():
    """The data directory, created if missing.

    Falls back to the application folder only if the resolved directory cannot
    be created -- a read-only home should degrade to the old behaviour rather
    than stop the panel from starting.
    """
    if _CACHE:
        return _CACHE[0]
    override = (os.environ.get("IRICA_DATA_DIR") or "").strip()
    resolved = os.path.abspath(override) if override else _default_dir()
    try:
        os.makedirs(resolved, exist_ok=True)
    except OSError:
        resolved = APP_DIR
    _CACHE.append(resolved)
    return resolved


def reset_cache():
    """Forget the resolved directory (tests change the environment)."""
    del _CACHE[:]
    del _BOOTSTRAPPED[:]


def data_path(name):
    """Absolute path of a single file inside the data directory.

    Rejects anything but a plain file name so a caller cannot accidentally
    escape the directory.
    """
    if not name or not name.strip():
        raise ValueError("data_path() needs a file name")
    if name != os.path.basename(name) or "/" in name or "\\" in name \
            or name in (".", ".."):
        raise ValueError("data_path() takes a plain file name, got %r" % (name,))
    return os.path.join(data_dir(), name)


def data_subdir(*parts):
    """Absolute path of a subdirectory inside the data directory, created."""
    if not parts:
        raise ValueError("data_subdir() needs at least one path segment")
    for part in parts:
        if not part or part != os.path.basename(part) or "/" in part \
                or "\\" in part or part in (".", ".."):
            raise ValueError("data_subdir() takes plain names, got %r" % (part,))
    path = os.path.join(data_dir(), *parts)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def migrate_legacy(app_dir=None, target=None):
    """Copy config/state left inside an old release folder into the data dir.

    Never overwrites a file that already exists at the destination, and never
    touches the `*.example.json` templates. Returns the names copied.
    """
    app_dir = app_dir or APP_DIR
    target = target or data_dir()
    if os.path.normcase(os.path.abspath(app_dir)) == \
            os.path.normcase(os.path.abspath(target)):
        return []
    moved = []
    for name in MIGRATED_FILES:
        if ".example." in name:
            continue
        source = os.path.join(app_dir, name)
        destination = os.path.join(target, name)
        if not os.path.isfile(source) or os.path.exists(destination):
            continue
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(source, destination)
        except OSError:
            continue
        moved.append(name)
    return moved


def bootstrap():
    """Resolve the data directory and migrate an old release folder once."""
    if _BOOTSTRAPPED:
        return []
    _BOOTSTRAPPED.append(True)
    return migrate_legacy(APP_DIR, data_dir())
