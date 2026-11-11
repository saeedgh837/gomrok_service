"""Bundled release summaries and isolated, durable account acknowledgments."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import tempfile


_CONTENT = {
    "version": "1.20.0",
    "title": "تازه‌های ایریکا",
    "intro": "مروری کوتاه بر تغییرهایی که در کار روزانه به شما کمک می‌کنند.",
    "sections": [
        {
            "label": "در این نسخه",
            "version": "1.20.0",
            "items": [{
                "title": "خلاصهٔ تغییرات، پس از ورود",
                "body": "پس از هر به‌روزرسانی، مهم‌ترین تغییرات را در اولین ورود ببینید. این خلاصه برای هر حساب جداگانه نمایش داده می‌شود.",
            }],
        },
        {
            "label": "همچنین در به‌روزرسانی پیشین",
            "version": "1.19.0",
            "items": [
                {
                    "title": "به‌روزرسانی، از همان پنل",
                    "body": "نسخهٔ برنامه و درایور مرورگر را از سربرگ هر بخش بررسی کنید. نصب فقط با تأیید شما انجام می‌شود.",
                },
                {
                    "title": "اطلاعات درست برای تلاش بعدی",
                    "body": "پس از اصلاح اطلاعات مسافر در مدیریت پرونده‌ها، تلاش بعدی پروندهٔ پرداخت‌نشده با اطلاعات جدید آماده می‌شود؛ نیازی به حذف پروفایل قبلی نیست.",
                },
                {
                    "title": "بررسی ارتباط سرویس‌ها",
                    "body": "ارتباط سرور ابری، تلگرام و پروکسی اختیاری را از هر سه پنل بررسی کنید. این بررسی پیام آزمایشی نمی‌فرستد و تحویل پیام را تأیید نمی‌کند.",
                },
            ],
        },
    ],
    "footer": "هر زمان بخواهید، این خلاصه را از «به‌روزرسانی‌ها» دوباره باز کنید.",
}
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_VERSION = re.compile(r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\Z", re.ASCII)


class StorageError(OSError):
    pass


def notes_for(installed_version):
    return copy.deepcopy(_CONTENT) if installed_version == _CONTENT["version"] else None


def subject_from_session(session):
    if not isinstance(session, dict):
        raise ValueError("A valid release-summary session is required")
    mode = session.get("mode")
    if mode == "standalone":
        username = session.get("user")
        if not isinstance(username, str) or re.fullmatch(r"[A-Za-z0-9_]{3,32}", username, re.ASCII) is None:
            raise ValueError("The release-summary account is invalid")
        identity = "standalone\0" + username
    elif mode == "cloud":
        from app_paths import account_profile_key
        fields = [session.get(name) for name in ("api_base", "tenant_id", "user_id")]
        key = session.get("profile_key")
        if (any(not isinstance(value, str) or not value.strip() for value in fields)
                or not isinstance(key, str) or not _HEX.fullmatch(key)
                or not hmac.compare_digest(account_profile_key(*fields), key)):
            raise ValueError("The release-summary cloud account is invalid")
        identity = "cloud\0" + key
    else:
        raise ValueError("The release-summary session mode is invalid")
    return hashlib.sha256(("irica-release-notes:v1\0" + identity).encode("utf-8")).hexdigest()


def _marker(root, subject, version):
    if (not isinstance(subject, str) or not _HEX.fullmatch(subject)
            or not isinstance(version, str) or not _VERSION.fullmatch(version)
            or any(int(part) > 65535 for part in version.split("."))):
        raise ValueError("The release-summary marker identity is invalid")
    return Path(root) / "release_notes" / "v1" / subject / (version + ".json")


def _notice_id(subject, version):
    return hashlib.sha256(("irica-release-notice:v1\0" + subject + "\0" + version).encode("ascii")).hexdigest()


def _record(subject, version):
    return {"schema": 1, "version": version, "notice_id": _notice_id(subject, version)}


def _read_seen(root, subject, version):
    marker = _marker(root, subject, version)
    try:
        with marker.open("r", encoding="ascii") as stream:
            raw = stream.read(1025)
        if len(raw) > 1024:
            return False, True
        record = json.loads(raw)
        valid = (isinstance(record, dict) and type(record.get("schema")) is int
                 and record == _record(subject, version))
        return valid, not valid
    except FileNotFoundError:
        return False, False
    except (OSError, ValueError, UnicodeError):
        return False, True


def is_seen(root, subject, version):
    return _read_seen(root, subject, version)[0]


def mark_seen(root, subject, version):
    marker = _marker(root, subject, version)
    temporary = None
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".seen-", suffix=".tmp", dir=marker.parent)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            json.dump(_record(subject, version), stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    except OSError:
        raise StorageError("The release-summary acknowledgment could not be saved") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("Release-summary temporary marker cleanup failed")


def snapshot(root, session, installed_version):
    subject = subject_from_session(session)
    content = notes_for(installed_version)
    if content is None:
        return {"ok": True, "notes": None, "notice_id": "", "seen": False, "storage_warning": False}
    seen, warning = _read_seen(root, subject, installed_version)
    return {"ok": True, "notes": content, "notice_id": _notice_id(subject, installed_version),
            "seen": seen, "storage_warning": warning}


def acknowledge(root, session, installed_version, payload):
    subject = subject_from_session(session)
    if (notes_for(installed_version) is None or not isinstance(payload, dict)
            or set(payload) != {"version", "notice_id"}
            or payload.get("version") != installed_version
            or not isinstance(payload.get("notice_id"), str)
            or not _HEX.fullmatch(payload["notice_id"])
            or not hmac.compare_digest(payload["notice_id"], _notice_id(subject, installed_version))):
        raise ValueError("Refresh the current release summary before acknowledging it")
    mark_seen(root, subject, installed_version)
