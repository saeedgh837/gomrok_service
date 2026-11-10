"""Consent-gated ChromeDriver updater.

Read-only calls check compatibility without network or proxy activity. After
current explicit user confirmation, this module resolves the latest matching
Chrome-for-Testing patch (or validates an exact requested build) and installs
it atomically without downgrading a known local version.

Why the Cloudflare proxy is the default
---------------------------------------
The CfT hosts (googlechromelabs.github.io, storage.googleapis.com) are not
reliably reachable from Iran. This project already runs a local HTTP CONNECT
proxy (``cloudflare_http_proxy.py``, 127.0.0.1:18081) for Telegram. Downloads
use that route unless the current confirmed request explicitly selects another
proxy or direct access. A missing proxy never silently enables direct access.

Safety rules enforced here
--------------------------
* Every installation requires ``consent=True`` for the current call.
* Downloads use the proxy unless direct mode was explicitly selected.
* Downloads are bounded by one wall-clock budget for the whole attempt, a hard
  attempt limit with jittered exponential backoff, and a per-read stall
  timeout. ``requests``' timeout is a per-read stall allowance, not a total
  one, so every request is given ``min(nominal, budget left)``; a stalled read
  then aborts at the deadline instead of at the nominal allowance. The body is
  read in chunks with the deadline re-checked between them, which makes the cap
  a soft one — worst case a read begun just before the deadline runs on for
  what was left of the budget — but the nominal allowance is never added on top
  of an already-spent budget.

* A download is verified (it is a zip, it holds chromedriver.exe, and that
  binary's ``--version`` equals the exact CfT build that was asked for) before
  it is allowed anywhere near the live path.
* The swap is ``os.replace`` from the target's own directory (atomic on one
  volume) and the file it replaced is staged into place as ``.bak`` first, so
  neither the live driver nor its backup can ever be observed half-written.
* ``IRICA_NO_DRIVER_UPDATE=1`` is a kill switch.
* Nothing here logs or transmits proxy credentials, bot tokens, or the
  contents of upstream_proxy.json.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

CFT_LAST_KNOWN_GOOD_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)
CFT_MILESTONE_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "latest-versions-per-milestone-with-downloads.json"
)
CFT_STORAGE_BASE = "https://storage.googleapis.com/chrome-for-testing-public"
# The catalogue host is also the readiness target: a proxy that cannot CONNECT
# here cannot serve us anything, so there is no point probing anything else.
CFT_CATALOGUE_HOST = "googlechromelabs.github.io"

DEFAULT_PLATFORM = "win64"
ZIP_MEMBER_SUFFIX = "chromedriver.exe"

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 18081
PROXY_START_TIMEOUT = 15.0
PROXY_PROBE_TIMEOUT = 2.0
PROXY_SCRIPT = "cloudflare_http_proxy.py"

HTTP_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 180.0
# Read size for the streaming body loop in http_get_bytes. Only its effect
# matters: a small chunk means the wall-clock deadline is re-checked often.
CHUNK_SIZE = 65536
ATTEMPTS = 5
BACKOFF = 1.5
BACKOFF_CAP = 30.0
RETRY_TOTAL_SECONDS = 300.0
VERSION_TIMEOUT = 15

KILL_SWITCH_ENV = "IRICA_NO_DRIVER_UPDATE"
_FALSEY = ("", "0", "false", "no", "off")

# Answers that will not change on a second try: retrying them only delays the
# honest verdict, and each attempt is another trip through the tunnel. 403 is
# the one this host really hits — it is what a direct (proxy-less) request to
# Chrome-for-Testing returns.
PERMANENT_STATUS = frozenset({400, 401, 403, 404, 405, 410, 451})


class DriverUpdateError(RuntimeError):
    """A driver update could not be completed. Message is safe to log/print.

    An HTTP failure carries its status so the retry loop can tell a transient
    drop of the tunnel from a definitive answer such as the 403 this host gets
    when it reaches Chrome-for-Testing without the proxy."""

    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = int(status) if status else None
        self.retry_after = retry_after


# --------------------------------------------------------------------------
# version helpers
# --------------------------------------------------------------------------

def major_of(text):
    """Leading major version from a version string, or None.

    Accepts "154.0.8037.92", "ChromeDriver 153.0.8010.52 (...)", "Chrome 154.0.1".
    Never raises."""
    if text is None:
        return None
    text = str(text)
    match = re.search(r"(\d+)\.\d+\.\d+", text)
    if match:
        return int(match.group(1))
    match = re.match(r"\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else None


def version_of(text):
    """Full four-part version token from a version string, or None.

    "ChromeDriver 154.0.8037.92 (abc)" -> "154.0.8037.92"."""
    if text is None:
        return None
    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", str(text))
    return match.group(1) if match else None


def _version_key(version):
    """Strict numeric four-part version for comparisons and safe URL paths."""
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _version_output(exe_path):
    """Raw ``--version`` output of a driver, or "" when it cannot be read."""
    if not exe_path or not os.path.isfile(exe_path):
        return ""
    try:
        out = subprocess.run([exe_path, "--version"], capture_output=True,
                             text=True, timeout=VERSION_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or "") + (out.stderr or "")


def read_exe_major(exe_path):
    """Major version of a chromedriver.exe via --version. None if unreadable."""
    return major_of(_version_output(exe_path))


def read_exe_version(exe_path):
    """Full version of a chromedriver.exe via --version. None if unreadable."""
    return version_of(_version_output(exe_path))


# --------------------------------------------------------------------------
# Chrome-for-Testing catalogue
# --------------------------------------------------------------------------

def cft_zip_url(version, platform=DEFAULT_PLATFORM):
    """Deterministic CfT download URL for a driver version."""
    version = str(version or "").strip()
    if _version_key(version) is None:
        raise ValueError("a four-part version is required")
    return f"{CFT_STORAGE_BASE}/{version}/{platform}/chromedriver-{platform}.zip"


def pick_from_last_known_good(payload, major):
    """Stable channel version if its major matches, else None.

    Deliberately strict: a Chrome 153 machine must never be handed the 154
    stable build just because that is what the channel currently serves."""
    channels = (payload or {}).get("channels") or {}
    stable = channels.get("Stable") or {}
    version = stable.get("version")
    if _version_key(version) is not None and major_of(version) == major:
        return version
    return None


def pick_from_milestones(payload, major):
    """Latest CfT build for a milestone, else None.

    The per-milestone document is small and answers exactly this question: the
    newest published driver for one Chrome major. Strict on the major so a
    Chrome 153 machine is never handed a 154 build.
    """
    milestones = (payload or {}).get("milestones") or {}
    entry = milestones.get(str(major))
    if entry is None:
        entry = milestones.get(major)
    if not isinstance(entry, dict):
        return None
    version = entry.get("version")
    return version if _version_key(version) is not None and major_of(version) == major else None


# --------------------------------------------------------------------------
# proxy
# --------------------------------------------------------------------------

def proxy_url(host=PROXY_HOST, port=PROXY_PORT):
    return f"http://{host}:{port}"


def is_port_listening(host, port, timeout=1.0):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def proxy_ready(host=PROXY_HOST, port=PROXY_PORT,
                target_host=CFT_CATALOGUE_HOST, target_port=443,
                timeout=PROXY_PROBE_TIMEOUT):
    """True when the proxy completes a real CONNECT for the catalogue host.

    An open port proves nothing: the tunnel can be listening while its upstream
    is down, and every download would then fail in a way that reads like "no
    driver available" — the exact confusion this module exists to remove. So
    this issues the very request the downloader will make and requires a 200.
    """
    if not is_port_listening(host, port, timeout=min(timeout, 1.0)):
        return False
    request = (f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
               f"Host: {target_host}:{target_port}\r\n\r\n").encode("ascii")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            status_line = sock.recv(128).split(b"\r\n", 1)[0]
    except OSError:
        return False
    return b" 200 " in status_line


def _budget_left(deadline, ceiling):
    """``ceiling`` seconds, but never more than what is left of ``deadline``.

    Starting the proxy is part of the attempt's budget, not outside it, so a
    caller passing a small total must not be kept waiting the full spawn
    allowance afterwards. The floor of one second keeps a nearly-spent budget
    from turning into a zero or negative socket timeout.
    """
    if deadline is None:
        return ceiling
    return max(1.0, min(ceiling, deadline - time.monotonic()))


def _spawn_proxy_process(timeout=PROXY_START_TIMEOUT):
    """Start cloudflare_http_proxy.py detached and wait until it really works.

    Mirrors start_telegram_relay_chain.bat, which probes the port and then
    launches the proxy hidden. Returns True only once a CONNECT succeeds, and
    never later than ``timeout`` seconds after the spawn."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), PROXY_SCRIPT)
    if not os.path.isfile(script):
        return False
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x08000000  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [sys.executable, "-u", script],
            cwd=os.path.dirname(script),
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    deadline = time.time() + timeout
    while True:
        if proxy_ready(PROXY_HOST, PROXY_PORT):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


# --------------------------------------------------------------------------
# HTTP (proxy is not optional)
# --------------------------------------------------------------------------

def http_get_bytes(url, proxy, timeout=HTTP_TIMEOUT, deadline=None, allow_direct=False, **kwargs):
    """GET through the proxy (or direct if allow_direct=True). Raises DriverUpdateError."""
    if not str(proxy or "").strip() and not allow_direct:
        raise DriverUpdateError(
            "refusing to download without a proxy: direct connections are not "
            "allowed for this updater")
    import requests

    session = requests.Session()
    # Confirmed routing and TLS policy must not inherit proxies, CA overrides
    # or .netrc credentials from the process environment, including direct mode.
    session.trust_env = False
    proxies = ({"http": proxy, "https": proxy}
               if str(proxy or "").strip() else {})
    response = None
    try:
        response = session.get(url, proxies=proxies, verify=True,
                               timeout=timeout, stream=True)
        if response.status_code != 200:
            raise DriverUpdateError(
                f"HTTP {response.status_code} for {url}",
                status=response.status_code,
                retry_after=response.headers.get("Retry-After"))
        chunks = []
        for chunk in response.iter_content(CHUNK_SIZE):
            if deadline is not None and time.monotonic() > deadline:
                raise DriverUpdateError(
                    "the transfer did not finish within the time budget")
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)
    except DriverUpdateError:
        raise
    except Exception as exc:  # requests raises many unrelated types
        raise DriverUpdateError(f"request failed: {exc}") from exc
    finally:
        if response is not None:
            response.close()
        session.close()


def _retry_after_seconds(value):
    """Retry-After as a number of seconds, or None.

    Only the delta-seconds form is honoured; a date form falls back to the
    ordinary backoff, which keeps this parser total."""
    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def retrying_fetch(fetch, url, proxy, timeout=HTTP_TIMEOUT, attempts=ATTEMPTS,
                   backoff=BACKOFF, total=RETRY_TOTAL_SECONDS, sleep=time.sleep,
                   deadline=None):
    """Call fetch(url, proxy, timeout), retrying transient failures.

    Backoff is exponential with jitter (the tunnel cuts a large transfer at
    unpredictable moments, so several robots retrying in lockstep would hit it
    together) and capped per pause. The budget bounds the whole sequence by
    elapsed time — enforced on each individual request as well as between them,
    so five attempts against a stalling tunnel cannot add up to a quarter of an
    hour. A definitive answer (see PERMANENT_STATUS) is surfaced immediately
    rather than retried five times, and a Retry-After header is respected within
    the remaining budget. Partial downloads are not resumed: a fresh whole-file
    GET is simpler than validated Range resume and covers the observed failure.

    Pass ``deadline`` (an absolute ``time.monotonic()`` instant) to share one
    budget across several calls — an attempt that resolves a version and then
    downloads must spend a single allowance, not one per request. It is handed
    to ``fetch`` so the request itself, not only the gap between requests,
    honours it. ``total`` is the relative form and is ignored when ``deadline``
    is given.
    """
    attempts = max(1, int(attempts))
    if deadline is None and total:
        deadline = time.monotonic() + total
    last = None
    made = 0
    for index in range(attempts):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            break
        try:
            made += 1
            call_timeout = timeout
            if remaining is not None:
                call_timeout = max(1.0, min(timeout, remaining))
            return fetch(url, proxy, call_timeout, deadline=deadline)

        except Exception as exc:
            last = exc
            if getattr(exc, "status", None) in PERMANENT_STATUS:
                break
            if index + 1 >= attempts:
                break
            pause = min(backoff * (2 ** index), BACKOFF_CAP) if backoff else 0.0
            if pause:
                pause *= random.uniform(0.5, 1.5)
            retry_after = _retry_after_seconds(getattr(exc, "retry_after", None))
            if retry_after is not None:
                pause = max(pause, retry_after)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pause = min(pause, remaining)
            if pause:
                sleep(pause)
    raise DriverUpdateError(f"failed after {made} attempt(s): {last}")


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def _stage_backup(target_exe, backup):
    """Put the driver about to be replaced onto ``.bak``, atomically.

    A plain copy onto a live path can be observed half-written, and a truncated
    ``.bak`` is worse than none — it is the file a human falls back to when an
    update goes wrong. Staging beside it and replacing means ``.bak`` is always
    exactly one of the two drivers. The staging name must be unique per call: a
    robot and the panel's manual button can be updating the same directory at
    once, and a shared name would let two copies interleave.

    Every failure is raised as a DriverUpdateError rather than a bare OSError,
    because the caller reserves PermissionError for a locked *live* driver — a
    backup it cannot write must not be reported as one.
    """
    try:
        handle, staged = tempfile.mkstemp(
            prefix=os.path.basename(backup) + ".", suffix=".tmp",
            dir=os.path.dirname(backup) or ".")
    except OSError as exc:
        raise DriverUpdateError(
            f"could not stage a backup beside {backup}: {exc}") from exc
    os.close(handle)
    try:
        shutil.copy2(target_exe, staged)
        os.replace(staged, backup)
    except OSError as exc:
        raise DriverUpdateError(
            f"could not write the backup {backup}: {exc}") from exc
    finally:
        if os.path.isfile(staged):
            try:
                os.remove(staged)
            except OSError:
                pass


def _unsafe_member(name):
    """True when a zip member name must not be extracted.

    ``ZipFile.extract`` already strips absolute paths and parent references, so
    this is a second line of defence rather than the only one: the same name is
    then joined onto the staging directory to find the extracted file, and that
    composition must not depend on the sanitiser's behaviour.
    """
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return True
    return any(part == ".." for part in name.replace("\\", "/").split("/"))


def install_from_zip(zip_path, target_exe, expected_major, read_major,
                     expected_version=None, read_version=None):
    """Verify the downloaded zip, then atomically replace target_exe.

    The staged driver must report ``expected_major`` — and, when
    ``expected_version`` is given, exactly that build — before it is allowed to
    replace the live file. That second check matters because Chrome-for-Testing
    serves several builds per major: a wrong-but-compatible build would pass a
    major-only check and silently bind the robots to a different driver than
    the one selected. The previous file is staged onto ``.bak`` first.
    """
    target_exe = os.path.abspath(target_exe)
    target_dir = os.path.dirname(target_exe)
    try:
        os.makedirs(target_dir, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=".cd_update_", dir=target_dir)
    except OSError as exc:
        # An unwritable target directory is a failure, not a locked driver. The
        # caller must not answer "close the robots" when the real problem is
        # that the driver's own folder cannot be written — the override path
        # (CHROMEDRIVER_PATH) may be a drive root or under Program Files.
        raise DriverUpdateError(
            f"cannot write in the driver's folder {target_dir}: {exc}") from exc

    try:
        try:
            with zipfile.ZipFile(zip_path) as archive:
                members = [n for n in archive.namelist()
                           if n.lower().endswith(ZIP_MEMBER_SUFFIX)]
                if not members:
                    raise DriverUpdateError(
                        "archive does not contain chromedriver.exe")
                member = sorted(members, key=len)[0]
                if _unsafe_member(member):
                    raise DriverUpdateError(
                        f"archive member has an unsafe path: {member}")
                archive.extract(member, staging)
        except zipfile.BadZipFile as exc:
            raise DriverUpdateError(f"invalid zip archive: {exc}") from exc
        except (RuntimeError, NotImplementedError) as exc:
            # Encrypted or unsupported-compression entries; zipfile raises these
            # without them being DriverUpdateErrors, and the "never raises"
            # contract of update_chromedriver must hold.
            raise DriverUpdateError(f"unusable zip archive: {exc}") from exc

        extracted = os.path.join(staging, *member.replace("\\", "/").split("/"))
        if not os.path.isfile(extracted):
            raise DriverUpdateError("chromedriver.exe missing after extraction")

        actual = read_major(extracted)
        if actual is None:
            raise DriverUpdateError(
                "downloaded chromedriver did not report a version")
        if expected_major is not None and actual != expected_major:
            raise DriverUpdateError(
                f"downloaded driver major {actual} does not match "
                f"Chrome {expected_major}")
        if expected_version:
            actual_version = (read_version or read_exe_version)(extracted)
            if actual_version != expected_version:
                raise DriverUpdateError(
                    f"downloaded driver is {actual_version}, expected "
                    f"{expected_version}")

        if os.path.isfile(target_exe):
            _stage_backup(target_exe, target_exe + ".bak")
        try:
            os.replace(extracted, target_exe)
        except PermissionError as exc:
            # Windows raises PermissionError here for two unrelated reasons and
            # they need opposite advice. A sharing or lock violation (WinError
            # 32/33) means something is executing the file -- the robots run
            # this very copy, so the caller answers "close the robots". A
            # read-only destination or folder (ERROR_ACCESS_DENIED, 5) is
            # nobody's lock: reporting it as driver-in-use would send the
            # operator hunting a robot that is not running. Anything else
            # (including every non-Windows PermissionError) is treated the same
            # way, since only the two sharing codes actually name a live image.
            if getattr(exc, "winerror", None) in (32, 33):
                raise
            # If target missing after failed replace, attempt restore from backup
            if not os.path.isfile(target_exe) and os.path.isfile(target_exe + ".bak"):
                try:
                    os.replace(target_exe + ".bak", target_exe)
                except Exception as restore_exc:
                    raise DriverUpdateError(
                        f"cannot write over {target_exe}: {exc}; backup restore also failed: {restore_exc}") from exc
            raise DriverUpdateError(
                f"cannot write over {target_exe}: {exc}") from exc
        except OSError as exc:
            if not os.path.isfile(target_exe) and os.path.isfile(target_exe + ".bak"):
                try:
                    os.replace(target_exe + ".bak", target_exe)
                except Exception as restore_exc:
                    raise DriverUpdateError(
                        f"could not replace {target_exe}: {exc}; backup restore also failed: {restore_exc}") from exc
            raise DriverUpdateError(
                f"could not replace {target_exe}: {exc}") from exc
        return target_exe

    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def _resolve_version(fetch, proxy, major, timeout, attempts, backoff,
                     total=RETRY_TOTAL_SECONDS, deadline=None):
    """CfT version for this browser major, or None.

    The last-known-good document is tiny and answers the common case (Chrome on
    the stable channel). Only when its major does not match do we pull the
    per-milestone document, which is a few KB and indexed by major — the large
    all-versions-with-downloads documents are never needed.

    Both lookups spend the caller's one budget: resolving a version and then
    downloading is a single attempt, so it must not be granted a fresh
    allowance here."""
    raw = retrying_fetch(fetch, CFT_LAST_KNOWN_GOOD_URL, proxy, timeout,
                         attempts, backoff, total=total, deadline=deadline)
    version = pick_from_last_known_good(_loads(raw), major)
    if version:
        return version
    raw = retrying_fetch(fetch, CFT_MILESTONE_URL, proxy, timeout,
                         attempts, backoff, total=total, deadline=deadline)
    return pick_from_milestones(_loads(raw), major)



def _loads(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise DriverUpdateError(f"catalogue response was not JSON: {exc}") from exc


def update_chromedriver(target_exe, expected_major, *, consent=False,
                        desired_version=None, read_major=None,
                        read_version=None, fetcher=None, probe=None,
                        spawn_proxy=None, env=None, extra_targets=(),
                        timeout=HTTP_TIMEOUT, attempts=ATTEMPTS, backoff=BACKOFF,
                        total=RETRY_TOTAL_SECONDS, log=None,
                        proxy_override=None, allow_direct=False):
    """Check locally by default; install only with current ``consent=True``.

    With consent, ``desired_version=None`` resolves and compares the latest CfT
    patch, including same-major updates. An exact four-part desired version
    skips catalogue resolution. Wrong-major versions and known downgrades are
    refused before downloading. Old files remain intact on failed validation.

    Return {status, detail, target, version}; status is disabled,
    no-browser-version, consent-required, up-to-date, updated, driver-in-use or
    failed. Proxy startup, catalogue requests and the download share one bounded
    wall-clock budget. No approval is saved for any subsequent call.
    """
    env = os.environ if env is None else env
    read_major = read_major or read_exe_major
    read_version = read_version or read_exe_version
    log = log or (lambda message: None)
    result = {"status": "failed", "detail": "", "target": target_exe,
              "version": ""}

    def finish(status, detail=""):
        result["status"] = status
        result["detail"] = detail
        return result

    if str(env.get(KILL_SWITCH_ENV, "")).strip().lower() not in _FALSEY:
        return finish("disabled", f"{KILL_SWITCH_ENV} is set")

    if not expected_major:
        return finish("no-browser-version", "Installed Chrome major is unknown.")
    try:
        expected_major = int(expected_major)
        if expected_major <= 0:
            raise ValueError("major must be positive")
        target_exe = os.path.abspath(target_exe)
        current = read_major(target_exe) if os.path.isfile(target_exe) else None
    except (OSError, TypeError, ValueError) as exc:
        return finish("failed", f"Could not inspect the local driver: {exc}")

    desired_key = _version_key(desired_version)
    if desired_version is not None and (
            desired_key is None or desired_key[0] != expected_major):
        return finish("failed", "The desired driver version must be an exact "
                      f"four-part version for Chrome {expected_major}.")

    # Robot starts perform only compatibility checks; no catalogue, proxy or
    # download may be touched without this request's boolean True approval.
    if consent is not True and desired_version is None:
        if current == expected_major:
            return finish("up-to-date", "The local ChromeDriver major matches Chrome.")
        return finish("consent-required", "ChromeDriver update required. Confirm "
                      "the driver update in the panel before installation.")

    current_version = read_version(target_exe) if current is not None else None
    current_key = _version_key(current_version)
    if current_key is not None and current_key[0] != current:
        current_key = None
    if consent is not True:
        if current == expected_major and current_key == desired_key:
            result["version"] = current_version
            return finish("up-to-date")
        return finish("consent-required", "Confirm the ChromeDriver update before installation.")
    if current is not None and current > expected_major:
        return finish("failed", "Refusing to downgrade the installed ChromeDriver major.")
    if desired_key is not None and current_key is not None:
        if desired_key < current_key:
            return finish("failed", "Refusing to downgrade the installed ChromeDriver version.")
        if desired_key == current_key:
            result["version"] = current_version
            return finish("up-to-date")

    # One budget for the whole attempt, started here so that starting the proxy
    # and every request after it spend the same allowance. Resolving a version
    # and then downloading is one attempt; granting each fetch its own total
    # would let a stalling tunnel hold a robot start for a multiple of the
    # caller's budget.
    deadline = (time.monotonic() + total) if total else None

    if proxy_override:
        proxy = str(proxy_override).strip()
    elif allow_direct:
        proxy = ""
    else:
        proxy = proxy_url()
        probe = probe or proxy_ready
        if not probe(PROXY_HOST, PROXY_PORT):
            log("driver update: proxy down, starting it")
            spawn = spawn_proxy or _spawn_proxy_process
            try:
                # The spawn allowance is spent from the same budget as everything
                # else: with a small total the caller must not be kept waiting the
                # full start-up allowance and only then be told there is no time
                # left for the transfer.
                started = spawn(timeout=_budget_left(deadline, PROXY_START_TIMEOUT))
            except Exception:
                started = False
            if not started:
                return finish("failed",
                              f"proxy {proxy} is not available and could not be "
                              f"started")

    if deadline is not None and time.monotonic() > deadline:
        return finish("failed",
                      "the time budget was spent before the download could "
                      "start")

    fetch = fetcher or (lambda u, p, t, **kw: http_get_bytes(u, p, timeout=t, allow_direct=allow_direct, **kw))
    try:
        version = desired_version or _resolve_version(
            fetch, proxy, expected_major, timeout, attempts, backoff,
            total=total, deadline=deadline)
    except DriverUpdateError as exc:
        return finish("failed", f"could not resolve a driver version: {exc}")

    version_key = _version_key(version)
    if version_key is None or version_key[0] != expected_major:
        return finish("failed",
                      f"Chrome-for-Testing has no valid build for Chrome {expected_major}.")
    if current_key is not None:
        if version_key < current_key:
            return finish("failed", "Refusing to downgrade the installed ChromeDriver version.")
        if version_key == current_key:
            result["version"] = current_version
            return finish("up-to-date")

    url = cft_zip_url(version)
    download_dir = None
    try:
        try:
            body = retrying_fetch(fetch, url, proxy, DOWNLOAD_TIMEOUT, attempts,
                                  backoff, total=total, deadline=deadline)
        except DriverUpdateError as exc:
            # Reported as a download failure: the archive never arrived, so
            # blaming the install would point the operator and the log at a step
            # that was never reached.
            return finish("failed", f"could not download {version}: {exc}")
        download_dir = tempfile.mkdtemp(prefix="cd_dl_")
        zip_path = os.path.join(download_dir, "chromedriver.zip")
        with open(zip_path, "wb") as stream:
            stream.write(body)
        had_driver = os.path.isfile(target_exe)
        try:
            install_from_zip(zip_path, target_exe, expected_major, read_major,
                             expected_version=version, read_version=read_version)
        except PermissionError as exc:
            # Only a sharing/lock violation reaches here: Windows refusing to
            # overwrite a running image, and the robots run this very file.
            # Nothing was changed and the driver in place is intact, so
            # "close the robots" is the right advice. install_from_zip turns
            # every other permission failure (read-only destination, unwritable
            # target directory, a backup it cannot write) into a
            # DriverUpdateError, so those are reported as failures rather than
            # mistaken for a lock and answered with advice about a robot that
            # is not running.
            return finish("driver-in-use",
                          f"the driver file could not be replaced: {exc}")
        except DriverUpdateError as exc:
            return finish("failed", f"could not install {version}: {exc}")
    except OSError as exc:
        return finish("failed", f"could not write the download: {exc}")
    finally:
        if download_dir:
            shutil.rmtree(download_dir, ignore_errors=True)

    verification_problem = ""
    try:
        installed = read_major(target_exe)
        installed_version = read_version(target_exe)
        if installed != expected_major or installed_version != version:
            verification_problem = (
                f"After install the driver reports {installed_version or installed}, "
                f"expected {version}.")
    except Exception as exc:
        verification_problem = f"Could not verify the installed driver: {exc}"
    if verification_problem:
        try:
            if had_driver:
                os.replace(target_exe + ".bak", target_exe)
                recovery = " The previous driver was restored."
            else:
                os.remove(target_exe)
                recovery = " The unverified new driver was removed."
        except OSError as exc:
            return finish("failed", verification_problem +
                          f" Rollback failed: {exc}; retain the backup for manual recovery.")
        return finish("failed", verification_problem + recovery)

    # Refresh any secondary copies (e.g. the bundled .tools driver an installer
    # would ship) so a reinstall does not resurrect the stale one. A failure
    # here does not undo a successful primary update.
    for extra in extra_targets or ():
        try:
            extra = os.path.abspath(extra)
            if extra == target_exe:
                continue
            if os.path.isfile(extra):
                extra_version = _version_key(read_version(extra))
                extra_major = read_major(extra)
                if extra_major is not None and extra_major > expected_major:
                    continue
                if extra_version is not None and extra_version >= version_key:
                    continue
            # Each downloaded archive stays in its own fresh directory.
            with tempfile.TemporaryDirectory(prefix="cd_dl_") as extra_download:
                tmp_zip = os.path.join(extra_download, "chromedriver.zip")
                with open(tmp_zip, "wb") as stream:
                    stream.write(body)
                install_from_zip(tmp_zip, extra, expected_major, read_major,
                                 expected_version=version,
                                 read_version=read_version)
        except (DriverUpdateError, OSError) as exc:
            log(f"driver update: secondary target {extra} failed: {exc}")

    result["version"] = version
    return finish("updated", f"installed chromedriver {version}")
