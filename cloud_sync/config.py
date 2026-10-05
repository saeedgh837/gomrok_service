# -*- coding: utf-8 -*-
"""``cloud_config.json`` — the transport's non-secret configuration (spec §5).

Everything this file holds is non-secret by contract: the API base URL, the
tenant, the enrolled device id, the installation id minted once per machine,
and the incremental pull cursor. **No credential ever goes here** — the device
refresh token lives in DPAPI-protected ``auth.dat`` (see :mod:`cloud_sync.auth`)
and the access tokens never leave RAM.

The cursor belongs here too: manager ruling 9 names ``cloud_config.json`` as
the transport's file, and the cursor is transport state that must survive a
restart so a pull resumes at ``seq`` instead of re-reading the change log from
zero (spec §6.2).

Safety rules enforced on load:

* a non-loopback base URL must be ``https`` (spec §2 — HTTP is redirected or
  refused, so accepting it here would only produce a confusing failure later);
* ``http`` is allowed for ``127.0.0.1`` / ``::1`` / ``localhost`` only, which
  is what the loopback test fake binds to;
* userinfo, query strings and fragments are rejected, so a token cannot be
  smuggled into a URL that later reaches a log line.
"""
import json
import uuid

from urllib.parse import urlsplit

import app_paths
from cloud_sync.errors import ConfigError, CONFIG_INVALID
from cloud_sync.state import write_json_atomic

__all__ = [
    "CONFIG_FILE_NAME", "SCHEMA_VERSION", "LOOPBACK_HOSTS", "CloudConfig",
    "load_config", "save_config",
]

#: The file inside the data directory (spec §5).
CONFIG_FILE_NAME = "cloud_config.json"

SCHEMA_VERSION = 1

#: Hosts that may be talked to over plain HTTP — the loopback fake only.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: The exact fields persisted. Anything else found in the file is dropped, so
#: an old or edited file cannot introduce a key nobody validates.
FIELDS = ("api_base", "tenant_id", "sync_device_id", "source_installation_id",
          "cursor", "dataset_epoch")

_MAX_ID_LENGTH = 64
_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _check_id(value, field, *, allow_none=True):
    """A tenant/device/installation id: short, printable, token-free."""
    if value is None:
        if allow_none:
            return None
        raise ConfigError(code=CONFIG_INVALID)
    if not isinstance(value, str) or not value or len(value) > _MAX_ID_LENGTH:
        raise ConfigError(code=CONFIG_INVALID)
    if any(ch not in _ID_CHARS for ch in value):
        # Deliberately un-echoed: a rejected value may be a credential that
        # was pasted into the wrong field.
        raise ConfigError(code=CONFIG_INVALID)
    return value


def normalize_api_base(value):
    """Validate and canonicalise an API base URL.

    Returns the base **without** a trailing slash. Raises :class:`ConfigError`
    rather than letting a malformed base fail later as a confusing network
    error.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(code=CONFIG_INVALID)
    text = value.strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        raise ConfigError(code=CONFIG_INVALID) from None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ConfigError(code=CONFIG_INVALID)
    if not parts.hostname:
        raise ConfigError(code=CONFIG_INVALID)
    if parts.username or parts.password:
        # Credentials in a URL end up in logs and in Referer headers.
        raise ConfigError(code=CONFIG_INVALID)
    if parts.query or parts.fragment:
        raise ConfigError(code=CONFIG_INVALID)
    host = parts.hostname.lower()
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        # Spec §2: the API is HTTPS-only. Plain HTTP is accepted for the
        # loopback test fake and for nothing else.
        raise ConfigError(code=CONFIG_INVALID)
    return "%s://%s%s" % (scheme, parts.netloc, parts.path.rstrip("/"))


class CloudConfig:
    """The on-disk transport configuration. Plain attributes, explicit I/O."""

    def __init__(self, *, api_base=None, tenant_id=None, sync_device_id=None,
                 source_installation_id=None, cursor=0, dataset_epoch=None,
                 path=None, minted=False):
        self.api_base = normalize_api_base(api_base) if api_base else None
        self.tenant_id = _check_id(tenant_id, "tenant_id")
        self.sync_device_id = _check_id(sync_device_id, "sync_device_id")
        self._path = path
        #: True when this instance minted the installation id in memory and
        #: the caller has not persisted it yet.
        self.minted = bool(minted)
        if source_installation_id is None:
            self.source_installation_id = str(uuid.uuid4())
            self.minted = True
        else:
            self.source_installation_id = _check_id(
                source_installation_id, "source_installation_id",
                allow_none=False)
        self.cursor = self._check_cursor(cursor)
        self.dataset_epoch = _check_id(dataset_epoch, "dataset_epoch")

    @staticmethod
    def _check_cursor(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(code=CONFIG_INVALID)
        return value

    # -- where it lives -------------------------------------------------
    @property
    def path(self):
        """Absolute path of the config file (explicit `path` wins)."""
        if self._path is not None:
            if not isinstance(self._path, str) or not self._path.strip():
                raise ConfigError(code=CONFIG_INVALID)
            return self._path
        return app_paths.data_path(CONFIG_FILE_NAME)

    def require_api_base(self):
        """The base URL, or ConfigError — a client without one cannot start."""
        if not self.api_base:
            raise ConfigError(code=CONFIG_INVALID)
        return self.api_base

    def url(self, route):
        """Absolute URL for a route such as ``/sync/bootstrap``."""
        if not isinstance(route, str) or not route.startswith("/")                 or route.startswith("//") or "://" in route:
            # A route is a path, never an origin: "//host" and "https://host"
            # would otherwise turn our base URL into somebody else's.
            raise ConfigError(code=CONFIG_INVALID)
        return self.require_api_base().rstrip("/") + route

    # -- the cursor -----------------------------------------------------
    def set_cursor(self, value, dataset_epoch=None):
        """Record the last applied change-log position (spec §6.2)."""
        self.cursor = self._check_cursor(value)
        if dataset_epoch is not None:
            self.dataset_epoch = _check_id(dataset_epoch, "dataset_epoch")

    # -- serialisation --------------------------------------------------
    def to_dict(self):
        """Exactly the persisted fields, in a stable order."""
        return {
            "version": SCHEMA_VERSION,
            "api_base": self.api_base,
            "tenant_id": self.tenant_id,
            "sync_device_id": self.sync_device_id,
            "source_installation_id": self.source_installation_id,
            "cursor": self.cursor,
            "dataset_epoch": self.dataset_epoch,
        }

    @classmethod
    def from_dict(cls, payload, *, path=None, mint=False):
        """Rebuild from parsed JSON. Anything unusable raises ConfigError."""
        if not isinstance(payload, dict):
            raise ConfigError(code=CONFIG_INVALID)
        version = payload.get("version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise ConfigError(code=CONFIG_INVALID)
        for key in FIELDS:
            if key not in payload:
                raise ConfigError(code=CONFIG_INVALID)
        known = {key: payload[key] for key in FIELDS}
        cursor = 0 if known["cursor"] is None else known["cursor"]
        return cls(api_base=known["api_base"], tenant_id=known["tenant_id"],
                   sync_device_id=known["sync_device_id"],
                   source_installation_id=known["source_installation_id"],
                   cursor=cursor, dataset_epoch=known["dataset_epoch"],
                   path=path)

    def save(self, path=None):
        """Write atomically (temp file + rename) so a crash cannot truncate it."""
        write_json_atomic(path or self.path, self.to_dict())
        self.minted = False
        return path or self.path


def load_config(path=None, *, create=True):
    """Read the config, or create a fresh one on first run.

    * no file at all -> a fresh config with a newly minted
      ``source_installation_id`` (spec §4), persisted immediately so the same
      triple always maps to the same UUID on a retry;
    * a file that exists but is malformed -> :class:`ConfigError`. Failing
      closed here is what stops the client from pushing a dataset with a
      guessed installation id.
    """
    target = path or app_paths.data_path(CONFIG_FILE_NAME)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        if not create:
            raise ConfigError(code=CONFIG_INVALID) from None
        config = CloudConfig(path=target)
        config.save()
        return config
    except OSError:
        raise ConfigError(code=CONFIG_INVALID) from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ConfigError(code=CONFIG_INVALID) from None
    return CloudConfig.from_dict(payload, path=target)


def save_config(config, path=None):
    """Persist `config`; a thin alias so callers need only one import."""
    return config.save(path)
