"""Where configuration, state and logs live.

The release is a folder the operator replaces wholesale on every update. As long
as `telegram_config.json`, `profiles.json` and friends sat inside that folder,
each upgrade meant copying them across by hand -- and missing one silently
switched off notifications or lost the profile list.

So they live in a per-user data directory instead, outside the release:

    Windows  %LOCALAPPDATA%\\irica
    POSIX    $XDG_DATA_HOME/irica  (default ~/.local/share/irica)
    override $IRICA_DATA_DIR       (any absolute or relative path)

`bootstrap()` imports any settings from a previous install on first run, so
upgrading needs no manual step. It looks in the application folder AND in
sibling folders (the release tells the operator to unzip the new version into a
NEW folder, so the old settings are usually one directory over), preferring the
most recently written install and taking it as a whole. `IRICA_IMPORT_FROM`
names a folder explicitly. Copies, not moves: a rollback to the old build keeps
working.

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
    "crm_store.json",
    "crm_store.json.bak",
    "crm_logs.jsonl",
    # login accounts (PBKDF2 hashes only). Missing here, an upgrade from a
    # release folder that still held it silently dropped the operator's three
    # accounts and re-seeded fresh ones on the next start.
    "panel_users.json",
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
_IMPORTED_FROM = []
#: A folder is only treated as a previous install if it looks like one.
_INSTALL_MARKER = "panel.py"


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
    del _IMPORTED_FROM[:]


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


def _state_files_in(folder):
    """The migratable files present in a folder, newest mtime first."""
    found = []
    for name in MIGRATED_FILES:
        if ".example." in name:
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            try:
                found.append((os.path.getmtime(path), name))
            except OSError:
                continue
    found.sort(reverse=True)
    return found


def find_legacy_source(app_dir=None, target=None):
    """The folder to import settings from, or None.

    Three cases, in order:

    1. `IRICA_IMPORT_FROM` names a folder holding state -> use it, no questions.
    2. The application folder itself holds state (the operator unzipped over the
       old install, or ran the new code from the old folder) -> use it.
    3. A SIBLING folder holds state. This is the normal upgrade: the release
       notes say to unzip the new version into a NEW folder, so the settings are
       one directory over, not in `app_dir`. The most recently written install
       wins, and it is chosen as a WHOLE -- `profiles.json` and
       `estelam_store.json` cross-reference each other by profile id, so mixing
       files from different installs would produce a broken store.

    A sibling only counts as an install if it contains `panel.py`; a stray
    `profiles.json` in some unrelated directory is never picked up.
    """
    app_dir = os.path.abspath(app_dir or APP_DIR)
    target = os.path.abspath(target or data_dir())

    override = (os.environ.get("IRICA_IMPORT_FROM") or "").strip()
    if override:
        candidate = os.path.abspath(override)
        if os.path.isdir(candidate) \
                and os.path.normcase(candidate) != os.path.normcase(target) \
                and _state_files_in(candidate):
            return candidate

    if _state_files_in(app_dir):
        return app_dir

    parent = os.path.dirname(app_dir)
    if not parent or os.path.normcase(parent) == os.path.normcase(app_dir):
        return None
    best, best_mtime = None, -1.0
    try:
        entries = sorted(os.listdir(parent))
    except OSError:
        return None
    for entry in entries:
        candidate = os.path.join(parent, entry)
        if not os.path.isdir(candidate):
            continue
        if os.path.normcase(candidate) in (os.path.normcase(app_dir),
                                           os.path.normcase(target)):
            continue
        if not os.path.isfile(os.path.join(candidate, _INSTALL_MARKER)):
            continue
        state = _state_files_in(candidate)
        if state and state[0][0] > best_mtime:
            best, best_mtime = candidate, state[0][0]
    return best


def migrate_legacy(app_dir=None, target=None):
    """Copy config/state from a previous install into the data directory.

    Never overwrites a file that already exists at the destination, and never
    touches the `*.example.json` templates. Returns the names copied.
    """
    target = os.path.abspath(target or data_dir())
    source = find_legacy_source(app_dir, target)
    if source is None:
        return []
    if os.path.normcase(source) == os.path.normcase(target):
        return []
    moved = []
    for name in MIGRATED_FILES:
        if ".example." in name:
            continue
        origin = os.path.join(source, name)
        destination = os.path.join(target, name)
        if not os.path.isfile(origin) or os.path.exists(destination):
            continue
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(origin, destination)
        except OSError:
            continue
        moved.append(name)
    if moved:
        del _IMPORTED_FROM[:]
        _IMPORTED_FROM.append(source)
    return moved


def imported_from():
    """The folder the last migration read from, or '' — for the startup log."""
    return _IMPORTED_FROM[0] if _IMPORTED_FROM else ""


def bootstrap():
    """Resolve the data directory and import a previous install once."""
    if _BOOTSTRAPPED:
        return []
    _BOOTSTRAPPED.append(True)
    return migrate_legacy(APP_DIR, data_dir())


def account_profile_key(api_base, tenant_id, user_id):
    """Deterministic hash namespace for an authenticated account profile."""
    import hashlib
    clean_base = str(api_base or "").strip().rstrip("/")
    clean_tenant = str(tenant_id or "").strip()
    clean_user = str(user_id or "").strip()
    if not clean_base or not clean_tenant or not clean_user:
        raise ValueError("account_profile_key requires api_base, tenant_id and user_id")
    canonical = f"{clean_base}:{clean_tenant}:{clean_user}".encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def account_profile_dir(api_base, tenant_id, user_id, base_dir=None):
    """Absolute path to the isolated profile directory for this account."""
    key = account_profile_key(api_base, tenant_id, user_id)
    parent = os.path.abspath(base_dir or data_dir())
    path = os.path.join(parent, "accounts", "v1", key)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def write_account_manifest(profile_dir, *, api_base, tenant_id, user_id, username):
    """Persist identity manifest inside the account directory."""
    import json
    import time
    manifest = {
        "api_base": str(api_base or "").strip().rstrip("/"),
        "tenant_id": str(tenant_id or "").strip(),
        "user_id": str(user_id or "").strip(),
        "username": str(username or "").strip(),
        "profile_key": account_profile_key(api_base, tenant_id, user_id),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = os.path.join(profile_dir, "manifest.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return manifest
