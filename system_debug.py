"""Optional per-run diagnostics for robot startup and runtime failures."""
from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import sys
import threading
import traceback

_LOCK = threading.RLock()
_ORIGINAL_STDOUT = None
_ORIGINAL_STDERR = None
_LOG = None


def _enabled():
    return str(os.environ.get("IRICA_SYSTEM_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _log_dir():
    return os.path.abspath(os.environ.get(
        "IRICA_SYSTEM_LOG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "system")))


def _stamp():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _write(line):
    with _LOCK:
        if _LOG is None:
            return
        try:
            _LOG.write(f"[{_stamp()}] {line}\n")
            _LOG.flush()
        except OSError:
            pass


class _Tee:
    def __init__(self, original):
        self.original = original

    def write(self, value):
        if value:
            with _LOCK:
                try:
                    _LOG.write(value)
                    _LOG.flush()
                except (AttributeError, OSError):
                    pass
        return self.original.write(value)

    def flush(self):
        with _LOCK:
            try:
                _LOG.flush()
            except (AttributeError, OSError):
                pass
        return self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


def _uncaught(exc_type, exc_value, exc_traceback):
    _write("UNCAUGHT EXCEPTION")
    _write("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).rstrip())
    if _ORIGINAL_STDERR:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=_ORIGINAL_STDERR)


def configure(service):
    global _LOG, _ORIGINAL_STDOUT, _ORIGINAL_STDERR
    if not _enabled() or _LOG is not None:
        return ""
    os.makedirs(_log_dir(), exist_ok=True)
    safe_service = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(service or "robot"))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_log_dir(), f"{stamp}_{safe_service}_{os.getpid()}.log")
    _LOG = open(path, "a", encoding="utf-8", buffering=1)
    _ORIGINAL_STDOUT, _ORIGINAL_STDERR = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(sys.stdout), _Tee(sys.stderr)
    sys.excepthook = _uncaught
    _write(f"SYSTEM DEBUG START service={safe_service} pid={os.getpid()}")
    _write(f"cwd={os.getcwd()}")
    _write(f"python={sys.executable}")
    _write(f"argv={json.dumps(sys.argv, ensure_ascii=False)}")
    atexit.register(close)
    return path


def event(message):
    if _LOG is not None:
        _write(str(message))


def exception(label, exc):
    if _LOG is not None:
        _write(f"{label}: {type(exc).__name__}: {exc}")
        _write(traceback.format_exc().rstrip())


def close():
    global _LOG
    with _LOCK:
        if _LOG is None:
            return
        _write("SYSTEM DEBUG END")
        try:
            _LOG.close()
        except OSError:
            pass
        _LOG = None
