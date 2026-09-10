"""panel_auth.py — login gate for the IRICA panel (v1.2.0).

Credential store + session store + throttle, all stdlib.

Credentials live in panel_users.json inside the per-user data directory
(app_paths.data_path) — PBKDF2-HMAC-SHA256 hashes only, never plaintext.
On first start the file is seeded with the operator's three fixed accounts;
there is deliberately NO user-management UI in this version.

Sessions are in-memory only: restarting the panel logs everyone out, which is
the desired failure mode for a single-operator local tool.
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

#: Operator-provisioned default accounts (v1.2.0). Hashed at seed time; the
#: plaintext exists only here and only until the seed file is written.
DEFAULT_USERS = [
    ("saeed", "Sgh@1994@"),
    ("hasan", "8080"),
    ("arsalan", "Aa1111"),
]

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")

_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()

_FAIL_THRESHOLD = 5
_FAIL_LOCKOUT_SECONDS = 30
_THROTTLE = {}
_THROTTLE_LOCK = threading.Lock()

# Uniform-timing dummy target for unknown usernames.
_DUMMY_SALT = "3f9a1c7d5b2e4f60819a2c3d4e5f6071"


def valid_username(username):
    return bool(isinstance(username, str) and _USERNAME_RE.match(username or ""))


def _hash_password(password, salt_hex, iters=PBKDF2_ITERATIONS):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex),
        int(iters)).hex()


def seed_default_users(path=None):
    """Create the users file with the 3 default accounts if missing.
    Idempotent: an existing file is left untouched."""
    path = path or USERS_PATH
    if os.path.exists(path):
        return False
    users = []
    for username, password in DEFAULT_USERS:
        salt = secrets.token_hex(16)
        users.append({
            "username": username,
            "salt": salt,
            "hash": _hash_password(password, salt),
            "iters": PBKDF2_ITERATIONS,
        })
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(users, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return True


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
        expected = _hash_password("definitely-not-a-real-password", salt_hex, iters)
    else:
        salt_hex = str(record.get("salt") or _DUMMY_SALT)
        iters = int(record.get("iters") or PBKDF2_ITERATIONS)
        expected = str(record.get("hash") or "")
    actual = _hash_password(password, salt_hex, iters)
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------- sessions
def create_session(username, ttl_seconds=SESSION_TTL_SECONDS):
    token = secrets.token_urlsafe(32)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {
            "user": username,
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
