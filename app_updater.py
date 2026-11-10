"""Isolated, consent-driven updates from the public official GitHub repository.

AppUpdater(current_version=..., session=None, platform=None, installable=False,
           clock=None, installer=None, proxies=None, proxy_provider=None,
           staging_parent=None, install_dir=None, data_dir=None)

check(force=False) and status() return detached JSON-compatible snapshots.
confirm(offer_id) starts one background download or retries a waiting handoff.
installer(pathlib.Path, VerifiedOffer) must promptly return a mapping whose
status is busy, blocked, launched, or failed. The verified file stays locked
against writes/deletion throughout this callback on Windows. A launched
handoff owns eventual staging cleanup; waiting payloads are retained for retry.
No application bootstrap, filesystem access, network, or threads run on import.
Automatic handoff additionally requires explicit install_dir and data_dir roots;
installable must only be true for a coordinator-validated installed Windows copy.
An injected session is exclusively owned here and has ambient credentials cleared.
A trusted proxy_provider is sampled once, lazily, for each job that uses HTTP.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit


_REPO = "saeedgh837/gomrok_service"
_API = "https://api.github.com/repos/" + _REPO + "/releases/latest"
_HOSTS = frozenset({"github.com", "api.github.com",
                    "release-assets.githubusercontent.com", "objects.githubusercontent.com"})
_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z", re.ASCII)
_DIGEST = re.compile(r"sha256:([0-9a-fA-F]{64})\Z", re.ASCII)
_MAX_META = 1024 * 1024
_MAX_EXE = 256 * 1024 * 1024
_META_SECONDS = 20.0
_DOWNLOAD_SECONDS = 300.0
_MESSAGES = {
    "not_checked": "No official release check has run.",
    "checking": "Checking the official stable release.",
    "current": "Your version matches the latest official stable release.",
    "available": "A verified official update is available for confirmation.",
    "downloading": "Downloading the confirmed official installer.",
    "verifying": "Verifying the installer before handoff.",
    "busy": "The verified update is waiting for active work to finish.",
    "blocked": "The verified update is waiting for installation to be permitted.",
    "launched": "The verified installer handoff has launched.",
    "manual_install_required": "Automatic installation is unavailable here. Install the official release manually.",
    "invalid_offer": "This update offer is invalid or no longer current. Check again.",
    "invalid_release": "The official release metadata is invalid or inconsistent.",
    "downgrade": "The official release is older than your version. No downgrade is permitted.",
    "invalid_asset": "The exact official installer asset could not be verified.",
    "invalid_digest": "An official asset is missing a valid SHA256 digest.",
    "checksum_mismatch": "The versioned checksum does not verify the official installer.",
    "unsafe_url": "An update URL or redirect is outside the approved official sources.",
    "http_error": "The official update request did not return a complete successful response.",
    "invalid_length": "The update response size or encoding is invalid.",
    "size_limit": "The update response exceeds its permitted size.",
    "integrity_error": "The downloaded installer did not pass integrity verification.",
    "timeout": "The official update operation exceeded its time budget.",
    "metadata_failed": "The official release check failed. Your update status is unknown.",
    "download_failed": "The confirmed update download failed. No installer was launched.",
    "installer_failed": "The verified installer handoff failed or returned an invalid result.",
    "unsafe_staging": "A private update staging directory outside installation and data storage is required.",
    "cleanup_failed": "Update staging cleanup failed. Manual cleanup is required before another update.",
}


class _Failure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _UnsafeStage(OSError):
    pass


@dataclass(frozen=True)
class VerifiedOffer:
    offer_id: str
    release_id: int
    asset_id: int
    version: str
    name: str
    size: int
    sha256: str
    url: str


@dataclass
class _Job:
    kind: str
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    cleanup_thread: threading.Thread | None = None
    timer: threading.Timer | None = None
    response: object = None
    proxies: dict | None = None
    worker_finished: bool = False
    closing: bool = False
    metadata_bytes: int = 0
    handoff: bool = False


def _version(value):
    if not isinstance(value, str) or len(value) > 64 or not _SEMVER.fullmatch(value):
        raise ValueError("Expected an exact numeric major.minor.patch version")
    return tuple(int(part) for part in value.split("."))


def _proxy_mapping(value):
    message = "Explicit proxies must map http or https to nonempty proxy URLs"
    if not isinstance(value, Mapping):
        raise ValueError(message)
    result = dict(value)
    if any(key not in {"http", "https"} or not isinstance(proxy, str) or not proxy
           for key, proxy in result.items()):
        raise ValueError(message)
    return result


def _positive_int(value, maximum=None):
    return type(value) is int and value > 0 and (maximum is None or value <= maximum)


def _digest(value):
    match = _DIGEST.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise _Failure("invalid_digest")
    return match.group(1).lower()


def _validate_url(url):
    if (not isinstance(url, str) or len(url) > 8192 or "\\" in url
            or any(ord(char) < 33 or ord(char) > 126 for char in url)):
        raise _Failure("unsafe_url")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in _HOSTS
                or parts.username is not None or parts.password is not None
                or parts.port is not None or parts.netloc != parts.hostname or parts.fragment):
            raise _Failure("unsafe_url")
    except ValueError:
        raise _Failure("unsafe_url") from None
    if parts.hostname in {"api.github.com", "github.com"} and (
            "%" in parts.path or any(segment in {".", ".."} for segment in parts.path.split("/"))):
        raise _Failure("unsafe_url")
    if parts.hostname == "api.github.com" and (
            not parts.path.startswith("/repos/" + _REPO + "/releases/") or parts.query):
        raise _Failure("unsafe_url")
    if parts.hostname == "github.com" and (
            not parts.path.startswith("/" + _REPO + "/releases/download/") or parts.query):
        raise _Failure("unsafe_url")
    return url


def _header(response, name):
    for key, value in response.headers.items():
        if key.lower() == name.lower():
            if not isinstance(value, str):
                raise _Failure("invalid_length")
            return value
    return None


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Failure("invalid_release")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise _Failure("invalid_release")


def _windows_private_directory(path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    token, sid_text, descriptor = wintypes.HANDLE(), wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        length = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not length.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(length.value)
        if not security.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = "D:P(A;OICI;FA;;;" + sid_text.value + ")(A;OICI;FA;;;SY)"
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not security.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


@contextmanager
def _verification_handle(path):
    """Deny Windows payload writes/deletion and staging-directory replacement."""
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            yield stream
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    invalid = ctypes.c_void_p(-1).value
    directory = kernel.CreateFileW(str(path.parent), 0x81, 1, None, 3, 0x02200000, None)
    if directory == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    handle = None
    try:
        info = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(directory, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or info.tag or not info.attributes & 0x10:
            raise _UnsafeStage("The verification directory must not be a reparse point")
        handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x08200000, None)
        if handle == invalid:
            handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None
        with os.fdopen(fd, "rb") as stream:
            yield stream
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
        kernel.CloseHandle(directory)


class AppUpdater:
    def __init__(self, *, current_version, session=None, platform=None,
                 installable=False, clock=None, installer=None, proxies=None,
                 proxy_provider=None, staging_parent=None, install_dir=None, data_dir=None):
        self._current = _version(current_version)
        self._current_text = current_version
        self._session = session
        self._platform = sys.platform if platform is None else platform
        self._can_install = (self._platform == "win32" and sys.platform == "win32"
                             and installable is True and callable(installer)
                             and install_dir is not None and data_dir is not None)
        self._clock = time.monotonic if clock is None else clock
        self._installer = installer
        self._proxies = _proxy_mapping(dict(proxies or {}))
        self._proxy_provider = proxy_provider
        self._staging_parent = staging_parent
        self._install_dir = install_dir
        self._data_dir = data_dir
        self._lock = threading.RLock()
        self._job = None
        self._checked_at = None
        self._last_started = None
        self._state, self._code = "current", "not_checked"
        self._offer = None
        self._confirmed = False
        self._stage = None
        self._stage_identity = None
        self._stage_quarantined = False
        self._payload = None
        self._downloaded = 0
        self._cleanup_pending = False

    def _snapshot(self):
        return {"state": self._state, "code": self._code,
                "message": _MESSAGES[self._code], "current_version": self._current_text,
                "checked": self._checked_at is not None, "can_install": self._can_install,
                "offer": asdict(self._offer) if self._offer else None,
                "downloaded": self._downloaded, "total": self._offer.size if self._offer else 0,
                "cleanup_pending": self._cleanup_pending}

    def status(self) -> dict:
        with self._lock:
            return self._snapshot()

    def check(self, force=False) -> dict:
        with self._lock:
            now = self._clock()
            if self._job is not None or self._state in {"waiting_for_idle", "installing"} or self._cleanup_pending:
                return self._snapshot()
            if ((not force and self._checked_at is not None and now - self._checked_at < 900)
                    or (self._last_started is not None and now - self._last_started < 10)):
                return self._snapshot()
            self._last_started = now
            self._state, self._code = "checking", "checking"
            self._offer, self._confirmed, self._downloaded = None, False, 0
            job = self._start_job("metadata", self._check_release, _META_SECONDS)
        if not job.done.wait(_META_SECONDS):
            self._expire(job)
        return self.status()

    def confirm(self, offer_id) -> dict:
        with self._lock:
            if (not isinstance(offer_id, str) or not offer_id.isascii() or self._offer is None
                    or len(offer_id) != len(self._offer.offer_id)
                    or not hmac.compare_digest(offer_id, self._offer.offer_id)):
                result = self._snapshot()
                result.update(state="failed", code="invalid_offer", message=_MESSAGES["invalid_offer"])
                return result
            if self._job is not None or (self._confirmed and self._state != "waiting_for_idle"):
                return self._snapshot()
            if not self._can_install:
                self._code = "manual_install_required"
                return self._snapshot()
            retry = self._state == "waiting_for_idle"
            self._confirmed = True
            self._state = self._code = "verifying" if retry else "downloading"
            self._start_job("download", self._handoff if retry else self._download, _DOWNLOAD_SECONDS)
            return self._snapshot()

    def _start_job(self, kind, work, budget):
        job = _Job(kind, self._clock() + budget)
        self._job = job
        job.thread = threading.Thread(target=self._run, args=(job, work),
                                      name="irica-app-update-" + kind, daemon=True)
        job.timer = threading.Timer(budget, self._expire, args=(job,))
        job.timer.daemon = True
        try:
            job.timer.start()
            job.thread.start()
        except Exception:
            job.timer.cancel()
            self._state, self._code = "failed", kind + "_failed"
            self._job = None
            job.done.set()
        return job

    def _run(self, job, work):
        try:
            work(job)
        except _Failure as exc:
            self._fail(job, exc.code)
        except Exception:
            self._fail(job, job.kind + "_failed")
        finally:
            job.timer.cancel()
            with self._lock:
                keep = (not job.cancelled.is_set() and self._state in {"waiting_for_idle", "installing"})
            if job.kind == "download" and not keep:
                self._cleanup()
            with self._lock:
                if job.kind == "metadata":
                    self._checked_at = self._clock()
                job.worker_finished = True
                if self._job is job and not job.closing:
                    self._job = None
                job.done.set()

    def _expire(self, job):
        closer = None
        with self._lock:
            if self._job is not job or job.handoff or job.done.is_set():
                return
            job.cancelled.set()
            self._state, self._code = "failed", "timeout"
            job.done.set()
            if job.response is not None:
                job.closing = True
                closer = job.cleanup_thread = threading.Thread(
                    target=self._finish_expiry, args=(job, job.response),
                    name="irica-app-update-cleanup", daemon=True)
        if closer is not None:
            try:
                closer.start()
            except Exception:
                with self._lock:
                    self._cleanup_pending = True
                    self._state, self._code = "failed", "cleanup_failed"
                    job.closing = False
                    if self._job is job and job.worker_finished:
                        self._job = None

    def _finish_expiry(self, job, response):
        try:
            self._close_response(response)
        finally:
            with self._lock:
                job.closing = False
                if self._job is job and job.worker_finished:
                    self._job = None

    def _close_response(self, response):
        try:
            response.close()
        except Exception:
            with self._lock:
                self._cleanup_pending = True
                self._state, self._code = "failed", "cleanup_failed"
            return False
        return True

    def _checkpoint(self, job):
        if self._cleanup_pending:
            raise _Failure("cleanup_failed")
        if job.cancelled.is_set() or self._clock() >= job.deadline:
            raise _Failure("timeout")

    def _fail(self, job, code):
        with self._lock:
            if self._job is job and not job.cancelled.is_set():
                self._state, self._code = "failed", code

    def _phase(self, job, state, code=None):
        with self._lock:
            self._checkpoint(job)
            self._state, self._code = state, code or state

    def _http_session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        self._session.trust_env = False
        if hasattr(self._session, "proxies"):
            self._session.proxies.clear()
        if hasattr(self._session, "auth"):
            self._session.auth = None
        if hasattr(self._session, "cert"):
            self._session.cert = None
        if hasattr(self._session, "cookies"):
            self._session.cookies.clear()
        if hasattr(self._session, "params"):
            self._session.params.clear()
        return self._session

    def _stream(self, url, job, *, limit, expected=None):
        url = _validate_url(url)
        response = None
        redirects = 0
        try:
            self._checkpoint(job)
            if job.proxies is None:
                route = self._proxies if self._proxy_provider is None else self._proxy_provider()
                self._checkpoint(job)
                job.proxies = _proxy_mapping(route)
            while True:
                self._checkpoint(job)
                remaining = job.deadline - self._clock()
                response = self._http_session().get(
                    url, allow_redirects=False, stream=True, verify=True,
                    timeout=(min(5.0, remaining), min(10.0, remaining)),
                    headers={"Accept-Encoding": "identity", "Accept": "*/*",
                             "Authorization": None, "Cookie": None, "Proxy-Authorization": None,
                             "X-GitHub-Api-Version": "2022-11-28"},
                    proxies=dict(job.proxies))
                job.response = response
                self._checkpoint(job)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = _header(response, "Location")
                    if redirects >= 3 or not location:
                        raise _Failure("unsafe_url")
                    next_url = _validate_url(urljoin(url, location))
                    if not self._close_response(response):
                        raise _Failure("cleanup_failed")
                    response = job.response = None
                    url = next_url
                    redirects += 1
                    continue
                if response.status_code != 200:
                    raise _Failure("http_error")
                break
            encoding = _header(response, "Content-Encoding")
            transfer = _header(response, "Transfer-Encoding")
            declared = _header(response, "Content-Length")
            if encoding is not None and encoding.lower() != "identity":
                raise _Failure("invalid_length")
            if transfer is not None and (transfer.lower() != "chunked" or declared is not None or expected is not None):
                raise _Failure("invalid_length")
            if declared is not None:
                if len(declared) > 12 or not re.fullmatch(r"0|[1-9][0-9]*", declared, re.ASCII):
                    raise _Failure("invalid_length")
                declared = int(declared)
                if declared > limit:
                    raise _Failure("size_limit")
            if expected is not None and declared != expected:
                raise _Failure("invalid_length")
            count = 0
            chunks = iter(response.iter_content(chunk_size=64 * 1024))
            while True:
                self._checkpoint(job)
                try:
                    chunk = next(chunks)
                except StopIteration:
                    break
                self._checkpoint(job)
                if not isinstance(chunk, bytes):
                    raise _Failure("invalid_length")
                if not chunk:
                    continue
                count += len(chunk)
                if count > limit or (expected is not None and count > expected):
                    raise _Failure("size_limit")
                if job.kind == "metadata":
                    job.metadata_bytes += len(chunk)
                    if job.metadata_bytes > _MAX_META:
                        raise _Failure("size_limit")
                yield chunk
            self._checkpoint(job)
            if (declared is not None and count != declared) or (expected is not None and count != expected):
                raise _Failure("invalid_length")
        finally:
            if response is not None and not self._close_response(response):
                raise _Failure("cleanup_failed")
            job.response = None

    def _read(self, url, job, expected=None):
        return b"".join(self._stream(url, job, limit=_MAX_META, expected=expected))

    def _asset(self, asset, name, version, maximum):
        if (not isinstance(asset, dict) or asset.get("name") != name
                or not _positive_int(asset.get("id")) or not _positive_int(asset.get("size"), maximum)
                or asset.get("state") != "uploaded"):
            raise _Failure("invalid_asset")
        expected = "https://github.com/" + _REPO + "/releases/download/v" + version + "/" + name
        if asset.get("browser_download_url") != expected:
            raise _Failure("unsafe_url")
        if asset.get("url") not in (None, "https://api.github.com/repos/" + _REPO + "/releases/assets/" + str(asset["id"])):
            raise _Failure("invalid_asset")
        _validate_url(expected)
        return _digest(asset.get("digest"))

    def _check_release(self, job):
        raw = self._read(_API, job)
        try:
            release = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object,
                                 parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            raise _Failure("invalid_release") from None
        if (not isinstance(release, dict) or not _positive_int(release.get("id"))
                or release.get("draft") is not False or release.get("prerelease") is not False):
            raise _Failure("invalid_release")
        tag = release.get("tag_name")
        try:
            if not isinstance(tag, str) or not tag.startswith("v"):
                raise ValueError("Invalid tag")
            version = tag[1:]
            parsed = _version(version)
        except ValueError:
            raise _Failure("invalid_release") from None
        if release.get("html_url") not in (None, "https://github.com/" + _REPO + "/releases/tag/" + tag):
            raise _Failure("invalid_release")
        if release.get("url") not in (None, "https://api.github.com/repos/" + _REPO + "/releases/" + str(release["id"])):
            raise _Failure("invalid_release")
        if parsed < self._current:
            raise _Failure("downgrade")
        if parsed == self._current:
            self._phase(job, "current")
            return
        assets = release.get("assets")
        if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
            raise _Failure("invalid_asset")
        name = "IRICA-Setup-v" + version + ".exe"
        candidates = [asset for asset in assets if asset.get("name") == name]
        if len(candidates) != 1:
            raise _Failure("invalid_asset")
        asset = candidates[0]
        digest = self._asset(asset, name, version, _MAX_EXE)
        checksum_names = {"IRICA-Setup-v" + version + ".sha256", "SHA256SUMS_v" + version + ".txt"}
        for checksum_name in sorted(checksum_names):
            checksums = [item for item in assets if item.get("name") == checksum_name]
            if not checksums:
                continue
            if len(checksums) != 1:
                raise _Failure("checksum_mismatch")
            checksum = checksums[0]
            checksum_digest = self._asset(checksum, checksum_name, version, _MAX_META)
            body = self._read(checksum["browser_download_url"], job, checksum["size"])
            if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), checksum_digest):
                raise _Failure("checksum_mismatch")
            try:
                matches = []
                for line in body.decode("utf-8").splitlines():
                    if not line:
                        continue
                    match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]([!-~]+)", line, re.ASCII)
                    if match is None:
                        raise _Failure("checksum_mismatch")
                    if match.group(2) == name:
                        matches.append(match.group(1).lower())
                if len(matches) != 1 or not hmac.compare_digest(matches[0], digest):
                    raise _Failure("checksum_mismatch")
            except UnicodeError:
                raise _Failure("checksum_mismatch") from None
        offer = VerifiedOffer(secrets.token_urlsafe(32), release["id"], asset["id"],
                              version, name, asset["size"], digest, asset["browser_download_url"])
        with self._lock:
            self._checkpoint(job)
            self._offer = offer
            self._state, self._code = "available", "available"

    def _new_stage(self):
        parent = Path(self._staging_parent or tempfile.gettempdir()).resolve()
        forbidden = [Path(__file__).resolve().parent, Path(sys.executable).resolve().parent.parent]
        for root in (self._install_dir, self._data_dir, os.environ.get("IRICA_DATA_DIR")):
            if root:
                forbidden.append(Path(root).resolve())
        if any(parent == root or root in parent.parents for root in forbidden):
            raise _Failure("unsafe_staging")
        try:
            self._stage = Path(tempfile.mkdtemp(prefix="irica-update-", dir=parent))
            info = self._stage.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise _UnsafeStage("Invalid staging directory")
            self._stage_identity = (info.st_dev, info.st_ino)
            if os.name == "nt":
                _windows_private_directory(self._stage)
            else:
                self._stage.chmod(0o700)
            return self._stage
        except OSError:
            raise _Failure("unsafe_staging") from None

    def _validate_stage(self):
        if self._stage_quarantined or self._stage_identity is None:
            raise _UnsafeStage("Staging identity is unavailable")
        info = self._stage.lstat()
        if (not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                or (info.st_dev, info.st_ino) != self._stage_identity):
            raise _UnsafeStage("The staging directory was substituted")

    def _download(self, job):
        offer = self._offer
        self._checkpoint(job)
        stage = self._new_stage()
        self._payload = stage / offer.name
        digest = hashlib.sha256()
        with self._payload.open("xb") as payload:
            for chunk in self._stream(offer.url, job, limit=_MAX_EXE, expected=offer.size):
                payload.write(chunk)
                digest.update(chunk)
                with self._lock:
                    self._downloaded += len(chunk)
            payload.flush()
            os.fsync(payload.fileno())
        if not hmac.compare_digest(digest.hexdigest(), offer.sha256):
            raise _Failure("integrity_error")
        self._handoff(job)

    def _handoff(self, job):
        self._phase(job, "verifying")
        offer, payload = self._offer, self._payload
        try:
            with _verification_handle(payload) as stream:
                self._validate_stage()
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != offer.size
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise _Failure("integrity_error")
                digest = hashlib.sha256()
                count = 0
                while True:
                    self._checkpoint(job)
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > offer.size:
                        raise _Failure("integrity_error")
                    digest.update(chunk)
                if count != offer.size or not hmac.compare_digest(digest.hexdigest(), offer.sha256):
                    raise _Failure("integrity_error")
                with self._lock:
                    self._checkpoint(job)
                    if offer is not self._offer or not self._confirmed or self._job is not job:
                        raise _Failure("invalid_offer")
                    job.handoff = True
                    job.timer.cancel()
                try:
                    result = self._installer(payload, offer)
                    status = result.get("status") if isinstance(result, Mapping) else None
                except Exception:
                    raise _Failure("installer_failed") from None
                if not isinstance(status, str) or status not in {"busy", "blocked", "launched"}:
                    raise _Failure("installer_failed")
                with self._lock:
                    self._state = "installing" if status == "launched" else "waiting_for_idle"
                    self._code = status
        except _UnsafeStage:
            with self._lock:
                self._stage_quarantined = self._cleanup_pending = True
            raise _Failure("cleanup_failed") from None
        except OSError:
            raise _Failure("integrity_error") from None

    def _cleanup(self):
        stage = self._stage
        if stage is None:
            return
        try:
            self._validate_stage()
            shutil.rmtree(stage)
        except OSError:
            with self._lock:
                self._stage_quarantined = self._cleanup_pending = True
                self._state, self._code = "failed", "cleanup_failed"
            return
        self._stage = self._payload = self._stage_identity = None
