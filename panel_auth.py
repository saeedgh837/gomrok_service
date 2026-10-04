"""panel_auth.py — login gate for the IRICA panel (v1.2.0).

Credential store + session store + throttle, all stdlib.

Credentials live in panel_users.json inside the per-user data directory
(app_paths.data_path) — PBKDF2-HMAC-SHA256 hashes only, never plaintext.
On the FIRST start with no users file, the three operator accounts are seeded
with a random per-install password that is printed once to stdout and then
forgotten; there is deliberately NO user-management UI in this version. An
install that already has a users file is never touched, so its hashes survive
every upgrade.

Sessions are in-memory only: restarting the panel logs everyone out, which is
the desired failure mode for a single-operator local tool. Each session also
carries a CSRF token (never sent as a cookie, only as a request header).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

import app_paths

USERS_PATH = app_paths.data_path("panel_users.json")

PBKDF2_ITERATIONS = 390_000
SESSION_TTL_SECONDS = 7 * 24 * 3600

#: The three operator-provisioned accounts (v1.2.0). USERNAMES ONLY: no
#: password is defined anywhere in the source tree. A fresh install generates
#: one random password per account at seed time (see seed_default_users), so
#: no published credential can ever exist.
DEFAULT_USERNAMES = ("saeed", "hasan", "arsalan")

#: Entropy of a generated install password: 16 bytes of urlsafe base64 is 22
#: characters (~128 bits), long enough that guessing it is hopeless and short
#: enough to type once from the startup notice.
PASSWORD_BYTES = 16

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
#: Shape of a presented CSRF token before it is compared: bounded and ASCII so
#: a hostile header can never reach compare_digest as a non-ASCII string.
_CSRF_RE = re.compile(r"^[A-Za-z0-9_\-]{1,256}$")

_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()

_FAIL_THRESHOLD = 5
_FAIL_LOCKOUT_SECONDS = 30
_THROTTLE = {}
_THROTTLE_LOCK = threading.Lock()

# Uniform-timing dummy target for unknown usernames. The SECRET is generated
# once per process, never written down: a fixed literal here would be a
# published credential that satisfies verify_password() for EVERY username that
# does not exist — and _handle_login() has no second existence check.
_DUMMY_SALT = "3f9a1c7d5b2e4f60819a2c3d4e5f6071"
_DUMMY_SECRET = secrets.token_urlsafe(16)


def valid_username(username):
    return bool(isinstance(username, str) and _USERNAME_RE.match(username or ""))


def _hash_password(password, salt_hex, iters=PBKDF2_ITERATIONS):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex),
        int(iters)).hex()


def generate_password():
    """One random install password. Never stored, never logged, never derived
    from anything guessable: straight from the OS CSPRNG."""
    return secrets.token_urlsafe(PASSWORD_BYTES)


def seed_default_users(path=None):
    """Create the users file with the 3 default accounts if missing.

    Idempotent: an existing file is left untouched, so an operator's accounts
    and their hashes never change on upgrade.

    Returns the list of ``(username, password)`` pairs it generated so the one
    and only caller (the panel's first boot) can print them once, or ``False``
    when there was nothing to seed. The plaintext lives only in that return
    value: it is hashed before the file is written and is never persisted to
    the repo, a config file or a log.
    """
    path = path or USERS_PATH
    if os.path.exists(path):
        return False
    seeded = []
    users = []
    for username in DEFAULT_USERNAMES:
        password = generate_password()
        salt = secrets.token_hex(16)
        users.append({
            "username": username,
            "salt": salt,
            "hash": _hash_password(password, salt),
            "iters": PBKDF2_ITERATIONS,
            # The operator must pick their own password: these were shown once
            # on a console the installer's shortcut may not even have.
            "must_change_password": True,
        })
        seeded.append((username, password))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(users, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return seeded


def load_users(path=None):
    path = path or USERS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        users = [u for u in data
                 if isinstance(u, dict) and u.get("username") and u.get("hash")]
    except (OSError, ValueError):
        users = []
    return users


def verify_password(path, username, password):
    """Constant-shape verification: unknown users verify against a dummy hash
    so response timing does not reveal whether the account exists."""
    if not valid_username(username) or not isinstance(password, str):
        return False
    record = next((u for u in load_users(path)
                   if u.get("username") == username), None)
    if record is None:
        salt_hex = _DUMMY_SALT
        iters = PBKDF2_ITERATIONS
        expected = _hash_password(_DUMMY_SECRET, salt_hex, iters)
    else:
        salt_hex = str(record.get("salt") or _DUMMY_SALT)
        iters = int(record.get("iters") or PBKDF2_ITERATIONS)
        expected = str(record.get("hash") or "")
    actual = _hash_password(password, salt_hex, iters)
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------- sessions
def create_session(username, ttl_seconds=SESSION_TTL_SECONDS):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {
            "user": username,
            "csrf": csrf,
            "expires": time.time() + int(ttl_seconds),
        }
    return token


def session_user(token):
    if not isinstance(token, str) or not token:
        return None
    with _SESSIONS_LOCK:
        entry = _SESSIONS.get(token)
        if not entry:
            return None
        if entry["expires"] < time.time():
            del _SESSIONS[token]
            return None
        return entry["user"]


def session_csrf(token):
    """The CSRF token issued with this session, or None when there is no live
    session. Server-side only: the browser learns it from the readable
    irica_csrf cookie that login sets alongside the HttpOnly session cookie."""
    if not isinstance(token, str) or not token:
        return None
    with _SESSIONS_LOCK:
        entry = _SESSIONS.get(token)
        if not entry or entry["expires"] < time.time():
            return None
        return entry["csrf"]


def csrf_matches(token, presented):
    """True only when `presented` is the token issued for session `token`.

    Bounded and ASCII-checked first so a hostile header can never make
    compare_digest raise, and compare_digest so the comparison itself leaks
    nothing about the expected value.
    """
    expected = session_csrf(token)
    if not expected or not isinstance(presented, str):
        return False
    if not _CSRF_RE.match(presented):
        return False
    return hmac.compare_digest(expected, presented)


def destroy_session(token):
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def reset_sessions_for_tests():
    with _SESSIONS_LOCK:
        _SESSIONS.clear()


# ---------------------------------------------------------------- throttle
def is_locked_out(username):
    with _THROTTLE_LOCK:
        entry = _THROTTLE.get(username)
        if not entry:
            return False
        if entry["until"] and entry["until"] > time.time():
            return True
        if entry["until"] and entry["until"] <= time.time():
            entry["fails"] = 0
            entry["until"] = 0
        return False


def register_failed_login(username):
    with _THROTTLE_LOCK:
        entry = _THROTTLE.setdefault(username, {"fails": 0, "until": 0})
        entry["fails"] += 1
        if entry["fails"] >= _FAIL_THRESHOLD:
            entry["until"] = time.time() + _FAIL_LOCKOUT_SECONDS
            entry["fails"] = 0


def register_success(username):
    with _THROTTLE_LOCK:
        _THROTTLE.pop(username, None)


def reset_throttle_for_tests():
    with _THROTTLE_LOCK:
        _THROTTLE.clear()
