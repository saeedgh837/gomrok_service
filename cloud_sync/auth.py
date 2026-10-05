# -*- coding: utf-8 -*-
"""Device and user credential handling for cloud sync (spec §5).

Two credential families, never mixed:

===============  ==========================================================
device (sync)    opaque 256-bit refresh token, rotated on every use, stored
               at ``auth.dat`` behind **DPAPI ``CurrentUser``**; the access
               token is a 15-minute JWT held **in RAM only**
user (login)     access + refresh tokens, **RAM only**, never written anywhere
===============  ==========================================================

There is no plaintext fallback. If DPAPI cannot protect a value this module
raises :class:`cloud_sync.errors.SecretStoreError` (code ``SECRET_UNAVAILABLE``)
and the file is left untouched — writing the token in the clear would be worse
than not syncing at all.

The DPAPI implementation uses ``ctypes`` against ``crypt32.dll`` directly, so
no new dependency is introduced and ``pywin32`` is not required. Tests inject
:class:`MemorySecretStore` and never touch the real credential store.

Log safety: ``__repr__`` of every object here is a fixed English label. No
token, header, password or file path is ever put into an exception message.
"""
import base64
import ctypes
import json
import os
import tempfile
import time

from ctypes import wintypes

import app_paths
from cloud_sync.errors import SECRET_UNAVAILABLE, SecretStoreError

__all__ = [
    "AUTH_FILE_NAME", "ACCESS_TTL_SECONDS", "SECRET_STORE_LABEL",
    "SecretStore", "MemorySecretStore", "DpapiSecretStore", "open_secret_store",
    "restrict_to_owner", "read_acl_sids",
    "current_user_sid_string", "TokenVault",
]

#: Where the protected device refresh token lives (spec §5).
AUTH_FILE_NAME = "auth.dat"

#: Access tokens are 15-minute JWTs (spec §5). The client refreshes when this
#: elapses instead of waiting for a 401.
ACCESS_TTL_SECONDS = 15 * 60

#: Human-readable label bound into the DPAPI blob. It is not a secret; it only
#: tells an operator what the blob is if they ever inspect it.
SECRET_STORE_LABEL = "IRICA cloud sync device refresh token"

#: The exact JSON shape on disk. ``store`` is checked on read so a file that
#: someone rewrote in the clear can never be mistaken for a protected token.
_FORMAT_VERSION = 1
_STORE_NAME = "dpapi"


# =====================================================================
# The secret-store interface
# =====================================================================
class SecretStore(object):
    """Protect / unprotect opaque bytes. Implementations never log their input."""

    def protect(self, data):
        """Return an opaque blob for `data`, or raise SecretStoreError."""
        raise NotImplementedError

    def unprotect(self, blob):
        """Return the original bytes for `blob`, or raise SecretStoreError."""
        raise NotImplementedError

    @property
    def label(self):
        """What this implementation stores the secret *with* (never the secret)."""
        return type(self).__name__


class MemorySecretStore(SecretStore):
    """In-process store for tests: nothing is ever written to disk.

    The "ciphertext" is deliberately recognisable (``MEM1:`` + base64) so a
    test that accidentally persists it can spot it in a file listing, while
    the plaintext token still never appears verbatim.
    """

    PREFIX = b"MEM1:"

    def __init__(self):
        self._slots = {}

    def protect(self, data):
        if not isinstance(data, (bytes, bytearray)):
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        blob = self.PREFIX + base64.b64encode(bytes(data))
        self._slots[blob] = bytes(data)
        return blob

    def unprotect(self, blob):
        value = self._slots.get(bytes(blob)) if isinstance(
            blob, (bytes, bytearray)) else None
        if value is None:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        return value

    @property
    def label(self):
        return "memory"


# =====================================================================
# DPAPI (Windows, CurrentUser)
# =====================================================================
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_MAX_BLOB = 1024 * 1024


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _as_blob(data):
    """A DATA_BLOB plus the buffer that owns its bytes (keep both alive)."""
    buffer = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _load(name):
    """Load a Windows DLL or raise SecretStoreError — never a fallback."""
    try:
        if name == "crypt32":
            return ctypes.WinDLL("crypt32.dll")
        return ctypes.WinDLL(name)
    except OSError:
        raise SecretStoreError(code=SECRET_UNAVAILABLE) from None


class DpapiSecretStore(SecretStore):
    """``CryptProtectData`` / ``CryptUnprotectData`` with ``CurrentUser`` scope.

    The blob is bound to the Windows account that produced it: copying
    ``auth.dat`` to another machine or another user yields an unprotect
    failure, which surfaces as ``SECRET_UNAVAILABLE``.
    """

    def __init__(self):
        if os.name != "nt":                    # pragma: no cover - non-Windows
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        self._crypt32 = _load("crypt32")
        self._kernel32 = _load("kernel32")
        self._configure()
        self._probe()

    def _configure(self):
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(_DataBlob)]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(_DataBlob)]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _probe(self):
        """Fail at construction if this machine cannot round-trip a value."""
        try:
            blob = self.protect(b"irica-dpapi-probe")
            if self.unprotect(blob) != b"irica-dpapi-probe":
                raise SecretStoreError(code=SECRET_UNAVAILABLE)
        except SecretStoreError:
            raise
        except Exception:
            raise SecretStoreError(code=SECRET_UNAVAILABLE) from None

    def protect(self, data):
        if not isinstance(data, (bytes, bytearray)):
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        if not data or len(data) > _MAX_BLOB:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        source, keep = _as_blob(bytes(data))
        out = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(source), SECRET_STORE_LABEL, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        del keep
        if not ok:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            if out.pbData:
                self._kernel32.LocalFree(out.pbData)

    def unprotect(self, blob):
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        source, keep = _as_blob(bytes(blob))
        out = _DataBlob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        del keep
        if not ok:
            # No detail: the failure may be "wrong user", "corrupt" or
            # "foreign blob" and the reason is not ours to tell a log.
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            if out.pbData:
                self._kernel32.LocalFree(out.pbData)

    @property
    def label(self):
        return "dpapi-current-user"


def open_secret_store():
    """The real store, or a typed failure — never a plaintext fallback."""
    return DpapiSecretStore()


# =====================================================================
# File ACL: current user SID + SYSTEM, no inheritance (spec §5)
# =====================================================================
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x0004
_PROTECTED_DACL = 0x80000000
_ACL_REVISION = 2
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_FILE_ALL_ACCESS = 0x001F019F
_SYSTEM_SID_STRING = "S-1-5-18"
#: Enough for the two ACEs this module writes.
_ACL_BUFFER = 1024


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", ctypes.c_uint32)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


def _configure(advapi32, kernel32, *, with_acl=True, with_read=False):
    """Declare the Win32 signatures this module uses.

    ctypes defaults every ``restype`` to ``c_int``, which silently truncates
    pointers on 64-bit Windows; nothing here is allowed to run on defaults.
    """
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if with_acl:
        advapi32.ConvertStringSidToSidW.argtypes = [
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
        advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
        advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                           wintypes.DWORD]
        advapi32.InitializeAcl.restype = wintypes.BOOL
        advapi32.AddAccessAllowedAce.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                 wintypes.DWORD, ctypes.c_void_p]
        advapi32.AddAccessAllowedAce.restype = wintypes.BOOL
        advapi32.SetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    if with_read:
        advapi32.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p)]
        advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
        advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(ctypes.c_void_p)]
        advapi32.GetAce.restype = wintypes.BOOL


def _current_user_sid(advapi32, kernel32):
    """(SID pointer, token-user buffer) for the account running this process.

    The buffer owns the SID bytes, so both must stay referenced for as long as
    the SID is used.
    """
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     _TOKEN_QUERY, ctypes.byref(token)):
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0,
                                     ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer,
                                            size.value, ctypes.byref(size)):
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        info = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not info.User.Sid:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        return info.User.Sid, buffer
    finally:
        kernel32.CloseHandle(token)


def _well_known_sid(advapi32, kernel32, string_sid):
    """A freshly allocated well-known SID (e.g. SYSTEM); caller frees it."""
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(string_sid, ctypes.byref(sid)):
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    return sid.value


def _build_acl(advapi32, *sids):
    """A new ACL granting FILE_ALL_ACCESS to each SID in order."""
    acl = ctypes.create_string_buffer(_ACL_BUFFER)
    if not advapi32.InitializeAcl(ctypes.byref(acl), _ACL_BUFFER, _ACL_REVISION):
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    for sid in sids:
        if not advapi32.AddAccessAllowedAce(ctypes.byref(acl), _ACL_REVISION,
                                            _FILE_ALL_ACCESS, sid):
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
    return acl


def restrict_to_owner(path):
    """Give `path` a **protected** DACL of exactly {current user, SYSTEM}.

    "Protected" removes inheritance from the parent directory, which is what
    spec section 5 asks for. Called on the temp file, before it is renamed over
    ``auth.dat``, so the credential file never exists even briefly with a wider
    DACL. Any failure raises: a half-restricted credential file must not look
    like a successful write.
    """
    if os.name != "nt":                        # pragma: no cover - non-Windows
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    advapi32 = _load("advapi32")
    kernel32 = _load("kernel32")
    _configure(advapi32, kernel32)

    system_sid = None
    keep = None
    try:
        user_sid, keep = _current_user_sid(advapi32, kernel32)
        system_sid = _well_known_sid(advapi32, kernel32, _SYSTEM_SID_STRING)
        acl = _build_acl(advapi32, user_sid, system_sid)
        status = advapi32.SetNamedSecurityInfoW(
            os.fspath(path), _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL,
            None, None, ctypes.byref(acl), None)
        if status != 0:                         # ERROR_SUCCESS is 0
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
    finally:
        del keep
        if system_sid:
            kernel32.LocalFree(system_sid)


def current_user_sid_string():
    """This account's SID as ``S-1-5-21-…`` — a diagnostic, never a secret.

    Used to state exactly who the ``auth.dat`` DACL allows (spec §5) without
    depending on display names, which are locale-dependent.
    """
    if os.name != "nt":                        # pragma: no cover - non-Windows
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    advapi32 = _load("advapi32")
    kernel32 = _load("kernel32")
    _configure(advapi32, kernel32, with_acl=False)
    sid, keep = _current_user_sid(advapi32, kernel32)
    text = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel32.LocalFree(text)
        del keep


class _AclHeader(ctypes.Structure):
    """``ACL`` as Windows declares it: revision, size, then the ACE count."""

    _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
                ("AclSize", ctypes.c_ushort), ("AceCount", ctypes.c_ushort),
                ("Sbz2", ctypes.c_ushort)]


def read_acl_sids(path):
    """The DACL's trustees as SID strings - a diagnostic, used by the tests.

    Returns a sorted list such as ``['S-1-5-18', 'S-1-5-21-...']``. Display
    names are locale-dependent; SIDs are not, which is why the assertion is
    made against them.
    """
    if os.name != "nt":                        # pragma: no cover - non-Windows
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    advapi32 = _load("advapi32")
    kernel32 = _load("kernel32")
    _configure(advapi32, kernel32, with_acl=False, with_read=True)

    p_owner = ctypes.c_void_p()
    p_group = ctypes.c_void_p()
    p_dacl = ctypes.c_void_p()
    p_sacl = ctypes.c_void_p()
    p_sd = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        os.fspath(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
        ctypes.byref(p_owner), ctypes.byref(p_group), ctypes.byref(p_dacl),
        ctypes.byref(p_sacl), ctypes.byref(p_sd))
    if status != 0 or not p_dacl.value:
        raise SecretStoreError(code=SECRET_UNAVAILABLE)
    try:
        acl = ctypes.cast(p_dacl, ctypes.POINTER(_AclHeader)).contents
        found = []
        for index in range(acl.AceCount):
            entry = ctypes.c_void_p()
            if not advapi32.GetAce(p_dacl, index, ctypes.byref(entry)):
                break
            if not entry.value:
                continue
            # ACCESS_ALLOWED_ACE: ACE_HEADER (4) + Mask (4) + the SID inline.
            sid_pointer = ctypes.c_void_p(entry.value + 8)
            text = ctypes.c_void_p()
            if advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
                found.append(ctypes.wstring_at(text))
                kernel32.LocalFree(text)
        return sorted(found)
    finally:
        if p_sd.value:
            kernel32.LocalFree(p_sd)


# =====================================================================
# The vault
# =====================================================================
class TokenVault(object):
    """Where credentials are allowed to live (spec §5).

    * device refresh token -> protected file ``auth.dat``
    * device access token  -> RAM
    * user access / refresh -> RAM

    Nothing here formats a token into a string that outlives the call: the
    accessors return the value and that is all.
    """

    def __init__(self, *, store=None, path=None, clock=None):
        self._store = store
        self._path = path
        self._clock = clock if clock is not None else time.time
        #: RAM only.
        self._access_token = None
        self._access_expires_at = None
        self._user_access = None
        self._user_refresh = None
        self._user_expires_at = None
        #: Set when the persisted store refused to open; reported, not hidden.
        self.restore_reason = ""

    # -- where it lives -------------------------------------------------
    @property
    def path(self):
        if self._path is not None:
            return self._path
        return app_paths.data_path(AUTH_FILE_NAME)

    @property
    def store(self):
        """The secret store, created on first use (DPAPI by default)."""
        if self._store is None:
            self._store = open_secret_store()
        return self._store

    # -- device refresh token ------------------------------------------
    @property
    def has_device_credentials(self):
        """Whether a protected refresh-token file exists.

        Deliberately a cheap existence check: it never decrypts, so a caller
        can ask "is this machine enrolled?" without risking a
        SECRET_UNAVAILABLE raise. The real read happens on use.
        """
        return os.path.exists(self.path)

    @property
    def device_refresh_token(self):
        """The stored device refresh token, or None when there is none."""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return None
        except OSError:
            self.restore_reason = "AUTH_FILE_UNREADABLE"
            raise SecretStoreError(code=SECRET_UNAVAILABLE) from None
        try:
            payload = json.loads(raw)
        except ValueError:
            self.restore_reason = "AUTH_FILE_UNREADABLE"
            raise SecretStoreError(code=SECRET_UNAVAILABLE) from None
        if not isinstance(payload, dict) \
                or payload.get("version") != _FORMAT_VERSION \
                or payload.get("store") != _STORE_NAME \
                or not isinstance(payload.get("data"), str):
            # A file that is not in our protected shape is refused outright:
            # reading it "anyway" would be the plaintext fallback in disguise.
            self.restore_reason = "AUTH_FILE_NOT_PROTECTED"
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        try:
            blob = base64.b64decode(payload["data"].encode("ascii"), validate=True)
        except (ValueError, TypeError):
            self.restore_reason = "AUTH_FILE_UNREADABLE"
            raise SecretStoreError(code=SECRET_UNAVAILABLE) from None
        try:
            secret = self.store.unprotect(blob)
        except SecretStoreError:
            self.restore_reason = "AUTH_FILE_UNREADABLE"
            raise
        try:
            return secret.decode("utf-8")
        except UnicodeDecodeError:
            self.restore_reason = "AUTH_FILE_UNREADABLE"
            raise SecretStoreError(code=SECRET_UNAVAILABLE) from None

    def save_device_refresh(self, token):
        """Protect and persist the device refresh token, durably.

        The protected blob is written to a temp file in the same directory,
        the DACL is narrowed to {user, SYSTEM} on that temp file, and only then
        is it renamed over ``auth.dat`` — so the token is never on disk in the
        clear, not even for the microseconds between two writes.
        """
        if not isinstance(token, str) or not token:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        blob = self.store.protect(token.encode("utf-8"))
        document = json.dumps({
            "version": _FORMAT_VERSION,
            "store": _STORE_NAME,
            "data": base64.b64encode(blob).decode("ascii"),
        }, sort_keys=True)
        target = self.path
        directory = os.path.dirname(target) or "."
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="auth", suffix=".tmp",
                                        dir=directory)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(document)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            restrict_to_owner(temp)
            os.replace(temp, target)
        except Exception:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        self.restore_reason = ""
        return target

    def revoke_local(self):
        """Drop every credential: RAM tokens and the persisted refresh token."""
        self.clear_ram()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError:
            raise SecretStoreError(code=SECRET_UNAVAILABLE) from None

    # -- device access token (RAM only) --------------------------------
    @property
    def access_token(self):
        """The in-memory device access token, or None once it has expired."""
        if self._access_token is None:
            return None
        if self._access_expires_at is not None \
                and self._clock() >= self._access_expires_at:
            return None
        return self._access_token

    @property
    def has_access_token(self):
        return self.access_token is not None

    def set_access(self, token, ttl=ACCESS_TTL_SECONDS):
        """Hold an access token in RAM for `ttl` seconds."""
        if not isinstance(token, str) or not token:
            raise SecretStoreError(code=SECRET_UNAVAILABLE)
        self._access_token = token
        self._access_expires_at = self._clock() + ttl if ttl else None
        return token

    def clear_access(self):
        self._access_token = None
        self._access_expires_at = None

    # -- user tokens (RAM only) ----------------------------------------
    @property
    def user_access_token(self):
        """The in-memory user access token, or None once it has expired."""
        if self._user_access is None:
            return None
        if self.user_access_expired:
            return None
        return self._user_access

    @property
    def user_refresh_token(self):
        return self._user_refresh

    def set_user_tokens(self, access, refresh=None, ttl=ACCESS_TTL_SECONDS):
        """Remember the logged-in user's tokens — in RAM, never on disk.

        Deliberately does **not** touch the device access slot: spec §5 keeps
        the two credential families apart, and a user token presented as a
        device token is answered with a 401 that would look like a dead
        session.
        """
        for value in (access, refresh):
            if value is not None and (not isinstance(value, str) or not value):
                raise SecretStoreError(code=SECRET_UNAVAILABLE)
        self._user_access = access
        self._user_refresh = refresh
        self._user_expires_at = (self._clock() + ttl
                                 if access and ttl else None)
        return access

    @property
    def user_access_expired(self):
        """True once the in-memory user access token is past its TTL."""
        if self._user_access is None:
            return True
        if self._user_expires_at is None:
            return False
        return self._clock() >= self._user_expires_at

    def clear_user(self):
        self._user_access = None
        self._user_refresh = None
        self._user_expires_at = None

    def clear_ram(self):
        """Forget every token held in memory (used on revoke / logout)."""
        self.clear_access()
        self.clear_user()

    # -- diagnostics ----------------------------------------------------
    def to_dict(self):
        """Non-secret status only — never a token, never a path fragment."""
        return {
            "store": self.store.label if self._store is not None else "unset",
            "device_credentials": self.has_device_credentials,
            "access_token_held": self._access_token is not None,
            "user_session_held": self._user_access is not None,
            "user_access_expired": self.user_access_expired,
            "restore_reason": self.restore_reason,
        }

    def __repr__(self):
        return "<TokenVault store=%s device=%s>" % (
            self._store.label if self._store is not None else "unset",
            self.has_device_credentials)
