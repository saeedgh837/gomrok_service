"""crm.py — CRM store, validators, and device stage machine.

Pure store/flow logic. No HTTP here: panel.py owns the routes and calls into
this module. The store lives in `%LOCALAPPDATA%/irica/crm_store.json`
(`IRICA_CRM_STORE` overrides the path — tests use it).

Entities:
  customers  — intake records with an integer-Toman transaction ledger
               (+ = received, - = debt/بدهی).
  owners     — device owner identities (1:1 with devices; validated with the
               full rule set: 10-digit national code + mod-11 checksum,
               shamsi birth date with a 15..99 age window, 09xx mobile,
               optional card number — 16 digits when present, empty allowed,
               optional latin passport — 1-20 chars when present, stored uppercase).
  identities — the inquiry-identity pool. BOTH the pre-registration intake
               inquiries AND the watch-stage re-inquiries pick a RANDOM
               entry (user item 7): a legally-registered device is recorded
               under the passenger's national code, so querying it with a
               pool code returns the site's "registered under another code /
               not the owner" message — and that message is itself the
               positive "registered" signal that gates the payment step.
  devices    — master case records driven through the stage machine.

All English in code per the project contract; Persian appears only in
user-facing error strings.
"""
import datetime
import jalali
import json
import os
import re
import threading
import time
import uuid

STORE_LOCK = threading.RLock()

BIRTH_RE = r"\d{4}/\d{1,2}/\d{1,2}"
MOBILE_RE = r"09\d{9}"
CARD_RE = r"\d{16}"
NATIONAL_RE = r"\d{10}"

DEFAULT_SETTINGS = {
    "watch_interval_hours": 2,   # operator decision 2026-09-07 (was 4 in draft)
    "scheduler_enabled": True,
    "auto_advance_default": False,
    "telegram_report_full_data": True,
    "registry_fee_amount": 24_000_000,  # هزینه ثبت رجیستری (تومان)
    # Default only — the panel domain owns validation and use.
    "registration_chat_id": "",
}

REGISTRY_FEE_NOTE = "هزینهٔ ثبت رجیستری"

# Stage machine: stage -> {action -> next stage}. Single source of truth (F12).
ALLOWED = {
    "new": {
        "queue_inquiry": "inquiry",
    },
    "inquiry": {
        "mark_problem": "problem",
        "mark_ready": "ready_owner",      # scheduler auto-join also uses this
        "close_case": "closed",           # operator terminates the case
    },
    "problem": {
        "requeue_inquiry": "inquiry",
        "close_case": "closed",           # operator terminates the case
        "detach_owner": "problem",        # unlink passenger; stage stays
    },
    "ready_owner": {
        "attach_owner": "ready_owner",    # stays until handover tick
        "detach_owner": "ready_owner",    # unlink passenger; stage stays
        "handover": "handed",             # tick «تحویل به مسئول سایت»
    },
    "handed": {
        "to_payment": "to_payment",       # scheduler auto-join on 'advanced'
        "mark_problem": "problem",        # watch found blocked/problem (F12)
    },
    "to_payment": {
        "start_payment": "payment",       # manual button or auto-advance on 200
    },
    "payment": {
        "confirm_payment": "payment_done",
    },
    "payment_done": {
        "deliver_code": "done",
    },
    "done": {},
    # Terminal like `done`: a closed (مختومه) case can no longer proceed and
    # is intentionally NOT rollback-able via _STAGE_BACK. The single entry is
    # detach_owner — a LINK-mutating action (unlink the passenger), not a
    # stage transition; the case itself stays مختومه.
    "closed": {
        "detach_owner": "closed",
    },
}

# Stage rollback: for each stage, the previous MAINLINE stage a device may be
# returned to via `stage_back` (operator-requested undo with confirmation).
# `problem` is off-mainline: from problem the operator re-queues instead.
_STAGE_BACK = {
    "inquiry": "new",
    "ready_owner": "inquiry",
    "handed": "ready_owner",
    "to_payment": "handed",
    "payment": "to_payment",
    "payment_done": "payment",
    # `done` IS rollback-able by operator decision (user item 3): a wrongly
    # delivered code must be correctable. advance()'s generic rollback clears
    # code_delivered_at and done_at, so the case can be completed again via
    # deliver_code. `closed` (مختومه) stays terminal — see ALLOWED above.
    "done": "payment_done",
}

# Operator-initiated SKIP: jump to the NEXT mainline stage without passing the
# normal gates (inquiry results, owner attachment). UI asks for explicit
# confirmation and each skipped stage is recorded in `skipped_stages`.
# NO skip path may reach `done`: deliver_code is the ONLY action that can
# complete a case (user item 12) — completion is never gate-free.
_STAGE_SKIP_NEXT = {
    "new": "inquiry",
    "inquiry": "ready_owner",
    "ready_owner": "handed",
    "handed": "to_payment",
    "to_payment": "payment",
    "payment": "payment_done",
    # `done` is terminal: nothing to skip.
    "problem": "inquiry",  # off-mainline branch re-enters the mainline
}

_STAGE_TIMESTAMP_KEY = {
    "handed": "handed_at",
    "to_payment": "registered_at",
    "payment_done": "payment_confirmed_at",
    "done": "code_delivered_at",
}


def _new_id():
    return uuid.uuid4().hex[:8]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# Persian/Arabic digit normalization
# --------------------------------------------------------------------------

_DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(text):
    """Persian/Arabic digits -> ASCII; everything else passes through."""
    return str(text or "").translate(_DIGIT_TABLE)


# --------------------------------------------------------------------------
# Jalali date helpers (stdlib-only conversion; F20)
# --------------------------------------------------------------------------

def gregorian_to_jalali(gy, gm, gd):
    """Gregorian -> Jalali; delegates to the shared jalali module (v1.4.2)."""
    return jalali.gregorian_to_jalali(gy, gm, gd)


def today_jalali():
    t = time.localtime()
    return gregorian_to_jalali(t.tm_year, t.tm_mon, t.tm_mday)


def shamsi_age(birth, today_shamsi=None):
    """Age in whole Jalali years from a `YYYY/M/D` birth string, or None."""
    match = re.fullmatch(BIRTH_RE, normalize_digits(birth))
    if not match:
        return None
    year_s, month_s, day_s = normalize_digits(birth).split("/")
    try:
        by, bm, bd = int(year_s), int(month_s), int(day_s)
    except ValueError:
        return None
    if not (1300 <= by <= 1405 and 1 <= bm <= 12 and 1 <= bd <= 31):
        return None
    ty, tm, td = today_shamsi or today_jalali()
    age = ty - by
    if (tm, td) < (bm, bd):
        age -= 1
    return age


def national_checksum_ok(national):
    """Iranian national code mod-11 checksum; input must be 10 ASCII digits."""
    national = normalize_digits(national)
    if not re.fullmatch(NATIONAL_RE, national):
        return False
    digits = [int(ch) for ch in national]
    check = digits[9]
    total = sum(d * (10 - i) for i, d in enumerate(digits[:9])) % 11
    return (total < 2 and check == total) or (total >= 2 and check == 11 - total)


# --------------------------------------------------------------------------
# Validators — return Persian error lists (empty = valid)
# --------------------------------------------------------------------------

def validate_customer(data):
    errors = []
    data = data or {}
    if not str(data.get("full_name") or "").strip():
        errors.append("نام و نام خانوادگی مشتری الزامی است")
    elif len(str(data["full_name"]).strip()) > 80:
        errors.append("نام مشتری حداکثر ۸۰ کاراکتر باشد")
    mobile = normalize_digits(data.get("mobile")).replace("-", "").replace(" ", "")
    if mobile and not re.fullmatch(MOBILE_RE, mobile):
        errors.append("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود")
    for field, label, limit in (("address", "آدرس", 300), ("notes", "یادداشت", 500)):
        if len(str(data.get(field) or "")) > limit:
            errors.append(f"{label} حداکثر {limit} کاراکتر باشد")
    return errors


def validate_owner(data):
    errors = []
    data = data or {}
    if not str(data.get("first_name") or "").strip():
        errors.append("نام مسافر الزامی است")
    if not str(data.get("last_name") or "").strip():
        errors.append("نام خانوادگی مسافر الزامی است")
    national = normalize_digits(data.get("national")).strip()
    if not national_checksum_ok(national):
        errors.append("کد ملی نامعتبر است")
    birth = normalize_digits(data.get("birth")).strip()
    if not re.fullmatch(BIRTH_RE, birth):
        errors.append("تاریخ تولد باید به شکل ۱۳۷۵/۰۴/۲۹ یا ۱۳۷۵/۴/۲۹ باشد")
    else:
        age = shamsi_age(birth)
        if age is None or age < 15 or age > 99:
            errors.append("سن مسافر باید بین ۱۵ تا ۹۹ سال باشد")
    mobile = normalize_digits(data.get("mobile")).strip()
    if not re.fullmatch(MOBILE_RE, mobile):
        errors.append("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود")
    # Card is optional (user item 13): empty/missing passes; when present it
    # must be exactly 16 digits (after digit normalization and dash/space
    # stripping).
    card = normalize_digits(data.get("card_no")).replace("-", "").replace(" ", "")
    if card and not re.fullmatch(CARD_RE, card):
        errors.append("شماره کارت باید ۱۶ رقم باشد")
    # Passport is optional: when provided, it must be 1-20 latin letters/digits,
    # trimmed original (mixed case accepted); uppercasing happens at
    # persistence (add_owner/update_owner).
    passport = str(data.get("passport") or "").strip()
    if passport and not re.fullmatch(r"[A-Za-z0-9]{1,20}", passport):
        errors.append("شماره گذرنامه باید ۱ تا ۲۰ کاراکتر و فقط شامل حروف و اعداد لاتین باشد")
    return errors


def validate_identity(data):
    """Pool entries obey EXACTLY the estelam-person rules (review F8)."""
    try:
        import panel
        return panel.validate_estelam_person({
            "national": normalize_digits((data or {}).get("national")),
            "birth": normalize_digits((data or {}).get("birth")),
            "mobile": normalize_digits((data or {}).get("mobile")),
        })
    except Exception:
        # Panel import unavailable (isolated unit tests) — mirror the rules.
        errors = []
        if not re.fullmatch(NATIONAL_RE, normalize_digits((data or {}).get("national"))):
            errors.append("کد ملی باید ۱۰ رقم عدد باشد")
        if not re.fullmatch(r"1[34]\d{2}/\d{2}/\d{2}",
                            normalize_digits((data or {}).get("birth")).replace("/",
                             "/") if re.fullmatch(BIRTH_RE,
                            normalize_digits((data or {}).get("birth"))) else ""):
            errors.append("تاریخ تولد باید به شکل ۱۳۷۵/۰۴/۲۹ باشد")
        if not re.fullmatch(MOBILE_RE, normalize_digits((data or {}).get("mobile"))):
            errors.append("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود")
        return errors


def _find_serial_conflict(data, serial, exclude_device_id=None):
    """Return the first OTHER device holding `serial` (either serial slot),
    or None. `exclude_device_id` is the record being updated — its own
    slots never count as a conflict."""
    serial = normalize_digits(serial).strip()
    if not serial:
        return None
    for other in data.get("devices", []):
        if exclude_device_id and str(other.get("id")) == str(exclude_device_id):
            continue
        for key in ("serial1", "serial2"):
            if normalize_digits(other.get(key) or "").strip() == serial:
                return other
    return None


def _serial_conflict_errors(data, serials, exclude_device_id=None):
    """Persian duplicate-serial errors naming the conflicting customer."""
    errors = []
    seen = set()
    for serial in serials:
        serial = normalize_digits(serial).strip()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        conflict = _find_serial_conflict(data, serial,
                                         exclude_device_id=exclude_device_id)
        if conflict:
            customer = next(
                (c for c in data.get("customers", [])
                 if c.get("id") == conflict.get("customer_id")), None)
            who = (customer or {}).get("full_name") or "مشتری نامشخص"
            errors.append(
                f"سریال «{serial}» قبلاً برای دستگاه «{conflict.get('name')}» "
                f"به نام {who} ثبت شده است")
    return errors


def validate_device(data, exclude_device_id=None):
    errors = []
    data = data or {}
    name = str(data.get("name") or "").strip()
    if not name:
        errors.append("نام دستگاه الزامی است")
    elif len(name) > 40:
        errors.append("نام دستگاه حداکثر ۴۰ کاراکتر باشد")
    serial1 = normalize_digits(data.get("serial1")).strip()
    serial2 = normalize_digits(data.get("serial2")).strip()
    if not serial1:
        errors.append("سریال اول الزامی است")
    for label, value in (("سریال اول", serial1), ("سریال دوم", serial2)):
        if value and not re.fullmatch(r"\d{15}", value):
            errors.append(f"{label} باید ۱۵ رقم عدد باشد")
    if serial1 and serial2 and serial1 == serial2:
        errors.append("سریال اول و دوم نباید یکسان باشند")
    if not errors:
        try:
            with STORE_LOCK:
                data_all = _store_read()
        except Exception:                       # pragma: no cover - store absent
            data_all = {}
        if data_all:
            errors.extend(_serial_conflict_errors(
                data_all, (serial1, serial2),
                exclude_device_id=exclude_device_id))
    return errors


# --------------------------------------------------------------------------
# Store I/O — locked, atomic, with a corruption-guarding .bak
# --------------------------------------------------------------------------

def _store_path():
    return (os.environ.get("IRICA_CRM_STORE") or "").strip() or _default_store_path()


def _default_store_path():
    import app_paths
    return app_paths.data_path("crm_store.json")


def _empty_store():
    return {"version": 1, "customers": [], "owners": [], "identities": [],
            "devices": [], "settings": dict(DEFAULT_SETTINGS)}


def reset_cache():
    """Test hook — crm reads the env-configured path on every call."""
    return None


def _store_read():
    path = _store_path()
    if not os.path.exists(path):
        return _empty_store()
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle) or {}
    except Exception:
        # Corrupt main file: fall back to the .bak before losing anything.
        bak = path + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as handle:
                    loaded = json.load(handle) or {}
            except Exception:
                loaded = {}
        else:
            loaded = {}
    data = _empty_store()
    for key in ("version", "customers", "owners", "identities", "devices"):
        if key in loaded:
            data[key] = loaded[key]
    settings = dict(DEFAULT_SETTINGS)
    settings.update(loaded.get("settings") or {})
    data["settings"] = settings
    # Legacy backfill (user item 10): device rows written before `created`
    # existed derive it from `updated` — in memory only; it persists naturally
    # on the next mutation's _store_write. Existing `created` values are never
    # touched, and odd rows (non-dict entries, non-list collections) never
    # raise.
    devices = data.get("devices")
    if isinstance(devices, list):
        for device in devices:
            if (isinstance(device, dict) and not device.get("created")
                    and device.get("updated")):
                device["created"] = device["updated"]
    return data


def _store_write(data):
    path = _store_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    # Keep one generation of backup for the financial ledger (F14/F6): the
    # old main becomes .bak; on the very first write seed .bak from the new
    # data so a corrupt main NEVER leaves us with zero fallbacks.
    try:
        if os.path.exists(path):
            os.replace(path, path + ".bak")
        else:
            with open(path + ".bak", "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
    except OSError:
        pass
    os.replace(tmp, path)


def log_path():
    base = os.environ.get("IRICA_CRM_STORE") or ""
    if base:
        return base + ".logs.jsonl"
    import app_paths
    return app_paths.data_path("crm_logs.jsonl")


def crm_log(level, event, **detail):
    """Append one CRM event line. Never logs personal data: callers pass ids
    and masked strings only (plan §12)."""
    entry = {"at": _now(), "level": str(level), "event": str(event)}
    entry.update({k: v for k, v in detail.items() if v})
    import log_utils
    log_utils.append_jsonl(log_path(), entry)
    return entry


def store_read():
    with STORE_LOCK:
        return _store_read()


def store_write(data):
    with STORE_LOCK:
        _store_write(data)


# --------------------------------------------------------------------------
# CRUD helpers
# --------------------------------------------------------------------------

def _collection(data, key):
    return data.setdefault(key, [])


def add_customer(fields):
    errs = validate_customer(fields)
    if errs:
        return None, errs
    with STORE_LOCK:
        data = _store_read()
        record = {
            "id": _new_id(),
            "full_name": str(fields.get("full_name") or "").strip(),
            "address": str(fields.get("address") or "").strip(),
            "mobile": normalize_digits(fields.get("mobile")).strip(),
            "telegram_id": str(fields.get("telegram_id") or "").strip(),
            "instagram_id": str(fields.get("instagram_id") or "").strip(),
            "notes": str(fields.get("notes") or "").strip(),
            "transactions": [],
            "created": _now(), "updated": _now(),
        }
        _collection(data, "customers").append(record)
        _store_write(data)
        return record, []


def update_customer(customer_id, fields):
    with STORE_LOCK:
        data = _store_read()
        record = next((c for c in data.get("customers", [])
                       if c.get("id") == customer_id), None)
        if not record:
            return None, ["مشتری پیدا نشد"]
        merged = dict(record)
        merged.update({k: fields[k] for k in
                       ("full_name", "address", "mobile", "telegram_id",
                        "instagram_id", "notes") if k in (fields or {})})
        errs = validate_customer(merged)
        if errs:
            return None, errs
        record.update(merged)
        record["mobile"] = normalize_digits(record["mobile"]).strip()
        record["updated"] = _now()
        _store_write(data)
        return record, []


def delete_customer(customer_id, force=False):
    """Delete a customer. Devices keep existing but lose the link.

    Review F13/F18: blocked while any of the customer's devices is mid-robot
    (an active estelam run or a busy linked site-1 profile); `force=True`
    (the UI's explicit confirm) is required when devices remain at all.
    """
    with STORE_LOCK:
        data = _store_read()
        record = next((c for c in data.get("customers", [])
                       if c.get("id") == customer_id), None)
        if not record:
            return False, ["مشتری پیدا نشد"]
        devices = [d for d in data.get("devices", [])
                   if d.get("customer_id") == customer_id]
        if devices and not force:
            return False, [f"این مشتری {len(devices)} دستگاه دارد — با تأیید حذف کنید"]
        for device in devices:
            mid = _device_mid_robot(device)
            if mid:
                return False, ["دستگاه در حال اجرای ربات است — ابتدا آن را متوقف کنید"]
            if device.get("customer_id"):
                # Unlinking is a device mutation: stamp it (user item 10).
                device["customer_id"] = ""
                device["updated"] = _now()
        data["customers"] = [c for c in data.get("customers", [])
                             if c.get("id") != customer_id]
        _store_write(data)
        return True, []


def _device_mid_robot(device):
    """True when an estelam run targets this row or its profile is busy (F13)."""
    try:
        import panel
        est = panel.estelam_load()
        row = next((d for d in est.get("devices", [])
                    if str(d.get("id")) == str(device.get("id"))), None)
        if row and str(row.get("status") or "") == "running":
            return True
        profile_id = device.get("profile_id")
        if profile_id:
            profile = next((p for p in panel.load_store().get("profiles", [])
                            if p.get("id") == profile_id), None)
            if profile and profile.get("run_status") == "busy":
                return True
    except Exception:
        return False
    return False


def customer_balance(customer):
    return sum(int(t.get("amount") or 0) for t in customer.get("transactions", []))


def add_transaction(customer_id, amount, note="", kind="", image="",
                    device_id=""):
    with STORE_LOCK:
        data = _store_read()
        record = next((c for c in data.get("customers", [])
                       if c.get("id") == customer_id), None)
        if not record:
            return None, ["مشتری پیدا نشد"]
        try:
            amount = int(normalize_digits(amount))
        except (TypeError, ValueError):
            return None, ["مبلغ باید عدد باشد"]
        entry = {"at": _now(), "amount": amount, "note": str(note or "").strip(),
                 "kind": str(kind or "").strip(), "image": str(image or "").strip(),
                 "device_id": str(device_id or "").strip()}
        record.setdefault("transactions", []).append(entry)
        record["updated"] = _now()
        _store_write(data)
        return entry, []


def _charge_registry_fee(data, customer_id, device_id):
    """Book the base registry fee for a newly registered device.

    Caller holds STORE_LOCK. Reads the configured amount from settings
    (registry_fee_amount, default 24_000_000 Toman); skips silently when
    the fee is configured to 0 or the customer is unknown. The entry is
    idempotent per device via the device_id marker on the transaction.
    """
    amount = int((data.get("settings") or {}).get("registry_fee_amount",
                                                  24_000_000) or 0)
    if amount <= 0 or not customer_id:
        return None
    customer = next((c for c in data.get("customers", [])
                     if c.get("id") == customer_id), None)
    if not customer:
        return None
    already = any(t.get("device_id") == device_id
                  and t.get("kind") == "base_fee"
                  for t in customer.get("transactions", []))
    if already:
        return None
    device = next((d for d in data.get("devices", [])
                   if str(d.get("id")) == str(device_id or "")), None)
    device_name = str((device or {}).get("name") or "").strip()
    note = (REGISTRY_FEE_NOTE + (" — " + device_name if device_name else ""))
    entry = {"at": _now(), "amount": -amount,
             "note": note, "kind": "base_fee",
             "image": "", "device_id": str(device_id or "").strip()}
    customer.setdefault("transactions", []).append(entry)
    customer["updated"] = _now()
    return entry


def _refund_registry_fee(data, device):
    """Reverse the base_fee rows booked for a device being deleted (Gate-H
    finding 2): serial1 corrections force delete-and-recreate, which would
    otherwise double-book the registry fee. The refund lands on whichever
    customer's ledger holds the original row (device_id marker), so a
    customer reassignment after booking still nets out. Idempotent per
    device like the charge side. Caller holds STORE_LOCK."""
    device_id = str(device.get("id") or "")
    if not device_id:
        return False
    already = any(t.get("device_id") == device_id
                  and t.get("kind") == "base_fee_refund"
                  for c in data.get("customers", [])
                  for t in c.get("transactions", []))
    if already:
        return False
    device_name = str(device.get("name") or "").strip()
    refunded = False
    for customer in data.get("customers", []):
        fees = [t for t in customer.get("transactions", [])
                if t.get("device_id") == device_id
                and t.get("kind") == "base_fee"]
        if not fees:
            continue
        for fee in fees:
            entry = {"at": _now(), "amount": -int(fee.get("amount") or 0),
                     "note": ("بازگشت " + REGISTRY_FEE_NOTE +
                              (" — " + device_name if device_name else "")),
                     "kind": "base_fee_refund", "image": "",
                     "device_id": device_id}
            customer.setdefault("transactions", []).append(entry)
        customer["updated"] = _now()
        refunded = True
    return refunded


def add_owner(fields):
    errs = validate_owner(fields)
    if errs:
        return None, errs
    with STORE_LOCK:
        data = _store_read()
        record = {
            "id": _new_id(),
            "first_name": str(fields.get("first_name") or "").strip(),
            "last_name": str(fields.get("last_name") or "").strip(),
            "national": normalize_digits(fields.get("national")).strip(),
            "birth": normalize_digits(fields.get("birth")).strip(),
            "mobile": normalize_digits(fields.get("mobile")).strip(),
            "card_no": normalize_digits(fields.get("card_no")).replace(
                "-", "").replace(" ", "").strip(),
            "passport": str(fields.get("passport") or "").strip().upper(),
            "created": _now(),
        }
        _collection(data, "owners").append(record)
        _store_write(data)
        return record, []


def update_owner(owner_id, fields):
    with STORE_LOCK:
        data = _store_read()
        record = next((o for o in data.get("owners", [])
                       if o.get("id") == owner_id), None)
        if not record:
            return None, ["مسافر پیدا نشد"]
        merged = dict(record)
        merged.update({k: fields[k] for k in
                       ("first_name", "last_name", "national", "birth",
                        "mobile", "card_no", "passport") if k in (fields or {})})
        errs = validate_owner(merged)
        if errs:
            return None, errs
        record.update(merged)
        for key in ("national", "birth", "mobile", "card_no"):
            record[key] = normalize_digits(record.get(key) or "").replace(
                "-", "").replace(" ", "").strip()
        # Passport skips digit normalization (Persian-digit translation is
        # wrong for latin passports) and gets its own trim+uppercase; the
        # defensive .get keeps legacy rows without the key safe here.
        record["passport"] = str(record.get("passport") or "").strip().upper()
        record["updated"] = _now()
        _store_write(data)
        return record, []


def delete_owner(owner_id):
    """Delete an unbound owner; a bound owner must be unlinked first (1:1)."""
    with STORE_LOCK:
        data = _store_read()
        bound = next((d for d in data.get("devices", [])
                      if d.get("owner_id") == owner_id), None)
        if bound:
            return False, ["این مسافر به یک دستگاه متصل است"]
        before = len(data.get("owners", []))
        data["owners"] = [o for o in data.get("owners", [])
                          if o.get("id") != owner_id]
        if len(data["owners"]) == before:
            return False, ["مسافر پیدا نشد"]
        _store_write(data)
        return True, []


def owner_bound_device_id(owner_id):
    with STORE_LOCK:
        data = _store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("owner_id") == owner_id), None)
        return device.get("id") if device else ""


def add_identity(fields):
    errs = validate_identity(fields)
    if errs:
        return None, errs
    with STORE_LOCK:
        data = _store_read()
        record = {
            "id": _new_id(),
            "label": str(fields.get("label") or "").strip() or "بدون عنوان",
            "national": normalize_digits(fields.get("national")).strip(),
            "birth": normalize_digits(fields.get("birth")).strip(),
            "mobile": normalize_digits(fields.get("mobile")).strip(),
            "created": _now(),
        }
        _collection(data, "identities").append(record)
        _store_write(data)
        return record, []


def update_identity(identity_id, fields):
    with STORE_LOCK:
        data = _store_read()
        record = next((i for i in data.get("identities", [])
                       if i.get("id") == identity_id), None)
        if not record:
            return None, ["مشخصات پیدا نشد"]
        merged = dict(record)
        merged.update({k: fields[k] for k in
                       ("label", "national", "birth", "mobile")
                       if k in (fields or {})})
        errs = validate_identity(merged)
        if errs:
            return None, errs
        record.update(merged)
        for key in ("national", "birth", "mobile"):
            record[key] = normalize_digits(record[key]).strip()
        record["updated"] = _now()
        _store_write(data)
        return record, []


def delete_identity(identity_id):
    with STORE_LOCK:
        data = _store_read()
        before = len(data.get("identities", []))
        data["identities"] = [i for i in data.get("identities", [])
                              if i.get("id") != identity_id]
        if len(data["identities"]) == before:
            return False, ["مشخصات پیدا نشد"]
        _store_write(data)
        return True, []


def add_device(fields, customer=None):
    """Add a device. `customer` is an optional inline new-customer dict."""
    errs = validate_device(fields)
    if errs:
        return None, errs
    with STORE_LOCK:
        data = _store_read()
        if customer:
            errs = validate_customer(customer)
            if errs:
                return None, errs
            record_c = {
                "id": _new_id(),
                "full_name": str(customer.get("full_name") or "").strip(),
                "address": str(customer.get("address") or "").strip(),
                "mobile": normalize_digits(customer.get("mobile")).strip(),
                "telegram_id": str(customer.get("telegram_id") or "").strip(),
                "instagram_id": str(customer.get("instagram_id") or "").strip(),
                "notes": "", "transactions": [],
                "created": _now(), "updated": _now(),
            }
            _collection(data, "customers").append(record_c)
            customer_id = record_c["id"]
        else:
            customer_id = str(fields.get("customer_id") or "").strip()
            if customer_id and not next((c for c in data.get("customers", [])
                                         if c.get("id") == customer_id), None):
                return None, ["مشتری پیدا نشد"]
        record = {
            "id": _new_id(),
            "name": str(fields.get("name") or "").strip(),
            "serial1": normalize_digits(fields.get("serial1")).strip(),
            "serial2": normalize_digits(fields.get("serial2")).strip(),
            "customer_id": customer_id,
            "owner_id": "",
            "stage": "new",
            "auto_advance": bool(fields.get("auto_advance")),
            "profile_id": "",
            "handed_at": "", "registered_at": "", "payment_confirmed_at": "",
            "code_delivered_at": "", "done_at": "",
            "last_inquiry_at": "", "notes": str(fields.get("notes") or "").strip(),
            "advance_attempts": 0,
            "created": _now(), "updated": _now(),
        }
        _collection(data, "devices").append(record)
        _charge_registry_fee(data, customer_id, record["id"])
        _store_write(data)
        return record, []


def update_device(device_id, fields):
    with STORE_LOCK:
        data = _store_read()
        record = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not record:
            return None, ["دستگاه پیدا نشد"]
        # serial1 is the immutable case identity (user item 2): a correction
        # means deleting the case and registering a new device. The delete
        # refunds the booked registry fee (Gate-H finding 2), so the message
        # tells the operator the correction is fee-neutral. Unchanged
        # (after digit normalization) or omitted serial1 keeps working;
        # serial2 stays fully editable.
        if "serial1" in (fields or {}) and normalize_digits(
                fields["serial1"]).strip() != normalize_digits(
                record.get("serial1") or "").strip():
            return None, [
                "سریال اول دستگاه قبلاً ثبت‌شده و قابل تغییر نیست — برای "
                "اصلاح، پروندهٔ دستگاه را حذف و دستگاه جدیدی با سریال صحیح "
                "ثبت کنید؛ هزینهٔ ثبت پروندهٔ قبلی هنگام حذف، خودکار به "
                "حساب مشتری برمی‌گردد"]
        merged = dict(record)
        merged.update({k: fields[k] for k in
                       ("name", "serial1", "serial2", "customer_id",
                        "auto_advance", "notes") if k in (fields or {})})
        errs = validate_device(merged, exclude_device_id=device_id)
        if errs:
            return None, errs
        if merged.get("customer_id"):
            if not next((c for c in data.get("customers", [])
                         if c.get("id") == merged["customer_id"]), None):
                return None, ["مشتری پیدا نشد"]
        record.update(merged)
        for key in ("serial1", "serial2"):
            record[key] = normalize_digits(record[key]).strip()
        record["updated"] = _now()
        _store_write(data)
        return record, []


def delete_device(device_id):
    """Delete a device case. Blocked mid-robot; unbinds the owner; removes
    only the CRM-origin estelam row; the site-1 profile is kept but disabled
    (F13)."""
    with STORE_LOCK:
        data = _store_read()
        record = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not record:
            return False, ["دستگاه پیدا نشد"]
        if _device_mid_robot(record):
            return False, ["دستگاه در حال اجرای ربات است — ابتدا آن را متوقف کنید"]
        # Reverse the booked registry fee before the case disappears (Gate-H
        # finding 2): the device_id marker survives on the ledger rows, but the
        # device name for the refund note comes from the record being deleted.
        _refund_registry_fee(data, record)
        data["devices"] = [d for d in data.get("devices", [])
                           if d.get("id") != device_id]
        _store_write(data)
    _remove_estelam_row(device_id, disable_profile=record.get("profile_id"))
    return True, []


def _remove_estelam_row(device_id, disable_profile=""):
    """Sync-side deletion through the panel's locked accessors only (F7)."""
    try:
        import panel
        est = panel.estelam_load()
        est["devices"] = [d for d in est.get("devices", [])
                          if str(d.get("id")) != str(device_id)]
        panel.estelam_save(est)
        if disable_profile:
            panel.set_profile_enabled(disable_profile, False)
    except Exception:
        pass


def _disable_detached_profile(device_id, profile_id):
    """Post-detach sync side effect: keep the site-1 registration profile but
    disable it — exactly what delete_device does to the profile record (F13).
    The estelam row is NOT touched (the device itself survives a detach).
    Best-effort like _remove_estelam_row: the committed store mutation is
    never rolled back by a sync-side failure; the miss is logged instead."""
    if not profile_id:
        return
    try:
        import panel
        panel.set_profile_enabled(profile_id, False)
    except Exception:
        crm_log("warn", "profile_disable_failed",
                device_id=device_id, profile_id=profile_id)


def attach_owner(device_id, owner_id):
    """Bind an owner to a device (1:1 — review F9/operator decision 3)."""
    with STORE_LOCK:
        data = _store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not device:
            return None, ["دستگاه پیدا نشد"]
        owner = next((o for o in data.get("owners", [])
                      if o.get("id") == owner_id), None)
        if not owner:
            return None, ["مسافر پیدا نشد"]
        bound = next((d for d in data.get("devices", [])
                      if d.get("owner_id") == owner_id
                      and d.get("id") != device_id), None)
        if bound:
            return None, ["این مسافر قبلاً به دستگاه دیگری متصل است"]
        device["owner_id"] = owner_id
        device["updated"] = _now()
        _store_write(data)
        return device, []


# --------------------------------------------------------------------------
# Stage machine
# --------------------------------------------------------------------------

# Mainline execution order (problem is an off-mainline branch, not a step).
STAGE_ORDER = ["new", "inquiry", "ready_owner", "handed", "to_payment",
               "payment", "payment_done", "done"]


def _stages_between(first, last):
    """Mainline stages strictly after `first` up to and including `last`."""
    order = [s for s in STAGE_ORDER if s != "problem"]
    try:
        start = order.index(first)
        end = order.index(last)
    except ValueError:
        return []
    if end <= start:
        return []
    return order[start + 1:end + 1]


# Inquiry outcomes that count as "passed" per service (operator request:
# both customs and Hamta must be OK before the next stage). Gomrok healthy
# covers the robot's healthy verdict; Hamta good/registered/success are its
# green outcomes (estelam_hamta._PANEL_LOG_STATUS maps all of them to
# healthy). `registered` for gomrok is NOT pass — it means the device was
# found already registered (watch-stage concern, handled elsewhere).
_GOMROK_PASS = {"healthy"}
_HAMTA_PASS = {"good", "registered", "success"}


def _inquiry_gate_errors(device):
    """Persian errors when the device's intake inquiries are not both OK.
    Reads the estelam store via panel (lazy import: crm must stay importable
    without the panel process)."""
    try:
        import panel
        est = panel.estelam_load()
    except Exception:
        return ["نتیجهٔ استعلام‌ها خوانده نشد — دوباره تلاش کنید"]
    row = next((d for d in est.get("devices", [])
                if str(d.get("id")) == str(device.get("id"))), None)
    if not row:
        return ["ابتدا استعلام‌های گمرک و همتا را انجام دهید"]
    errors = []
    gomrok = str(row.get("status") or "").strip().lower()
    hamta = str(row.get("hamta_status") or "").strip().lower()
    if gomrok not in _GOMROK_PASS:
        if gomrok in ("", "pending", "running"):
            errors.append("استعلام گمرک هنوز انجام نشده یا در جریان است")
        elif gomrok == "advanced":
            # ثبت‌بودن در گمرک در گام استعلام ثبت‌نبودن = خطر قرمز؛ توقف
            errors.append("خطر: دستگاه قبلاً در سامانهٔ گمرک ثبت شده است — "
                          "ادامهٔ مراحل برای این دستگاه مجاز نیست")
        else:
            errors.append("استعلام گمرک مشکل دارد — دستگاه مجاز نیست")
    if hamta not in _HAMTA_PASS:
        if hamta in ("", "pending", "running"):
            errors.append("استعلام همتا هنوز انجام نشده یا در جریان است")
        else:
            errors.append("استعلام همتا مشکل دارد — دستگاه مجاز نیست")
    return errors
def _detach_owner_case(device_id):
    """v1.5 link-mutating action: unlink the passenger. The stage is
    deliberately untouched — every allowed stage maps to itself. Mirrors
    delete_device for everything the binding touches: the 1:1 link is
    freed, the device's registration-profile link is cleared, and the
    site-1 profile itself survives but is disabled (F13 handling). The
    store write runs under the lock; the audit log and the profile
    disable run after release (delete_device precedent) so the panel's
    cross-process store lock is never held behind the CRM lock."""
    with STORE_LOCK:
        data = _store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not device:
            return None, ["دستگاه پیدا نشد"]
        stage = str(device.get("stage") or "new")
        if not ALLOWED.get(stage, {}).get("detach_owner"):
            return None, [f"این کنش در مرحلهٔ فعلی مجاز نیست ({stage})"]
        owner_id = str(device.get("owner_id") or "").strip()
        if not owner_id:
            return None, ["این مسافر به پرونده‌ای متصل نیست"]
        if _device_mid_robot(device):
            return None, ["دستگاه در حال اجرای ربات است — ابتدا آن را متوقف کنید"]
        profile_id = str(device.get("profile_id") or "")
        device["owner_id"] = ""
        device["profile_id"] = ""
        device["updated"] = _now()
        _store_write(data)
    # Audit trail (plan section 12): name BOTH sides of the severed link;
    # ids only — never personal data.
    crm_log("info", "detach_owner", device_id=device_id, owner_id=owner_id)
    _disable_detached_profile(device_id, profile_id)
    return device, []


def advance(device_id, action, **payload):
    """Apply a stage action; returns (device, errors). Persian errors."""
    if action == "detach_owner":
        return _detach_owner_case(device_id)
    with STORE_LOCK:
        data = _store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not device:
            return None, ["دستگاه پیدا نشد"]
        stage = str(device.get("stage") or "new")
        if action == "stage_back":
            # Operator-initiated rollback (with UI confirmation): move the
            # device to the previous mainline stage and clear the timestamps
            # of the stages we are leaving so re-execution stays possible.
            target = _STAGE_BACK.get(stage)
            if not target:
                return None, ["برگشت از این مرحله ممکن نیست"]
            leaving = _stages_between(target, stage)
            device["stage"] = target
            for left in leaving:
                stamp_key = _STAGE_TIMESTAMP_KEY.get(left)
                if stamp_key:
                    device[stamp_key] = ""
                if left == "done":
                    device["done_at"] = ""
            if target == "new":
                # Rollback past the intake tick: forget owner binding? No —
                # only stage markers are reset; the owner link survives.
                pass
            device["updated"] = _now()
            _store_write(data)
            return device, []
        if action == "stage_skip":
            # Operator-initiated skip (UI confirmation required): jump to the
            # NEXT mainline stage bypassing gates (inquiry results, owner
            # attachment). Every skip is recorded as provenance so the ledger
            # shows the stage was passed by operator decision, not by the
            # normal gate.
            next_stage = _STAGE_SKIP_NEXT.get(stage)
            if not next_stage:
                return None, ["رد کردن مرحله در این وضعیت ممکن نیست"]
            skipped = device.setdefault("skipped_stages", [])
            skipped.append({"stage": stage, "to": next_stage, "at": _now()})
            device["stage"] = next_stage
            stamp_key = _STAGE_TIMESTAMP_KEY.get(next_stage)
            if stamp_key and not device.get(stamp_key):
                device[stamp_key] = _now()
            device["updated"] = _now()
            _store_write(data)
            return device, []
        next_stage = ALLOWED.get(stage, {}).get(action)
        if not next_stage:
            return None, [f"این کنش در مرحلهٔ فعلی مجاز نیست ({stage})"]
        if action == "close_case":
            # Operator terminates the case (مختومه): a written reason is
            # mandatory so the closed tab always shows why the case ended.
            reason = str(payload.get("reason") or "").strip()
            if not reason:
                return None, ["ثبت دلیل برای مختومه‌کردن پرونده الزامی است"]
            device["closed_reason"] = reason
            device["closed_at"] = _now()
        # Owner gate: the case record and the step-4 Telegram handoff need
        # the passenger — NOT the inquiry identity (every stage's inquiries
        # run under the identity pool; user item 7). Handing a device over
        # without a passenger would produce an incomplete case; fail closed.
        if action == "handover":
            if not str(device.get("owner_id") or "").strip():
                return None, ["ابتدا مسافر را به دستگاه متصل کنید — اطلاعات "
                              "مسافر برای ثبت پرونده و تحویل لازم است"]
            owner = next((o for o in data.get("owners", [])
                          if o.get("id") == device.get("owner_id")), None)
            if not owner:
                return None, ["مسافر دستگاه پیدا نشد — اتصال مسافر را بررسی کنید"]
        # Inquiry gate (operator request): leaving the intake inquiry stage
        # requires BOTH inquiries to have passed for THIS device, as recorded
        # in the estelam store the robots write. Every other outcome
        # (missing row, pending/running, problem, blocked) blocks the tick
        # with a Persian error naming the failing service.
        if action == "mark_ready":
            gate_errors = _inquiry_gate_errors(device)
            if gate_errors:
                return None, gate_errors
        # Tick idempotency: a tick whose timestamp is already set is a no-op.
        stamp_key = _STAGE_TIMESTAMP_KEY.get(next_stage)
        if stamp_key and device.get(stamp_key) and action in (
                "handover", "confirm_payment", "deliver_code"):
            return None, ["این مرحله قبلاً تأیید شده است"]
        if action == "attach_owner":
            owner_id = str(payload.get("owner_id") or "").strip()
            if not owner_id:
                return None, ["انتخاب مسافر الزامی است"]
            owner = next((o for o in data.get("owners", [])
                          if o.get("id") == owner_id), None)
            if not owner:
                return None, ["مسافر پیدا نشد"]
            bound = next((d for d in data.get("devices", [])
                          if d.get("owner_id") == owner_id
                          and d.get("id") != device_id), None)
            if bound:
                return None, ["این مسافر قبلاً به دستگاه دیگری متصل است"]
            device["owner_id"] = owner_id
            device["stage"] = "ready_owner"
            device["updated"] = _now()
            _store_write(data)
            return device, []
        device["stage"] = next_stage
        if stamp_key and not device.get(stamp_key):
            device[stamp_key] = _now()
        if action == "deliver_code":
            device["done_at"] = _now()
        device["updated"] = _now()
        _store_write(data)
        return device, []


def stage_of(device):
    return str((device or {}).get("stage") or "new")


# --------------------------------------------------------------------------
# Reports (plan section 6; PDF via Edge lands in phase F)
# --------------------------------------------------------------------------

_STAGE_FA = {
    "new": "جدید",
    "inquiry": "در انتظار استعلام",
    "problem": "دارای اشکال",
    "ready_owner": "سالم — نیازمند مسافر",
    "handed": "تحویل‌شده به مسئول سایت",
    "to_payment": "ثبت‌شده — در انتظار پرداخت",
    "payment": "در حال پرداخت",
    "payment_done": "پرداخت تأییدشده",
    "done": "تکمیل‌شده",
    "closed": "مختومه",
}


def _esc(text):
    """HTML-escape every store-derived field (review F11)."""
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def mask_national(national):
    national = normalize_digits(national)
    if len(national) < 7:
        return "***"
    return f"{national[:3]}***{national[-4:]}"


def build_report(state, stage_filter=None, fmt="txt", full_data=True, plain=False):
    """Devices report grouped by stage. Identities appear ONLY when
    full_data=True; card numbers are excluded from telegram (plain=True) even
    then — they belong to the explicit file artifacts only (plan section 6)."""
    devices = state.get("devices") or []
    if stage_filter:
        devices = [d for d in devices if d.get("stage") in stage_filter]
    customers = {c.get("id"): c for c in state.get("customers") or []}
    owners = {o.get("id"): o for o in state.get("owners") or []}

    def line(device):
        customer = customers.get(device.get("customer_id")) or {}
        owner = owners.get(device.get("owner_id")) or {}
        parts = [
            f"دستگاه: {device.get('name')}",
            f"سریال: {device.get('serial1')}" +
            (f" / {device.get('serial2')}" if device.get("serial2") else ""),
            f"مشتری: {customer.get('full_name') or '—'}",
        ]
        if owner:
            parts.append(f"مسافر: {owner.get('first_name')} {owner.get('last_name')}"
                         f" | کد ملی: {mask_national(owner.get('national'))}"
                         f" | تولد: {owner.get('birth')}"
                         f" | موبایل: {owner.get('mobile')}")
            if full_data and not plain:
                # Cardless owners (user item 13): omit the «کارت» fragment
                # instead of rendering an empty «کارت: ».
                card_part = (f" | کارت: {owner.get('card_no')}"
                             if owner.get("card_no") else "")
                parts.append(f"کد ملی کامل: {owner.get('national')}"
                             + card_part)
            elif full_data and plain:
                # Telegram/report bodies: masked national, NEVER the card (§6)
                parts.append(f"کد ملی ماسک‌شده: {mask_national(owner.get('national'))}")
        est = device.get("estelam") or {}
        if est:
            parts.append(f"استعلام گمرک: {_STAGE_RESULT_FA.get(est.get('status'), est.get('status') or '—')}"
                         f" | همتا: {est.get('hamta_status') or '—'}")
        parts.append(f"مرحله: {_STAGE_FA.get(device.get('stage'), device.get('stage'))}")
        return " | ".join(parts)

    report_items = [line(d) for d in devices]
    header = (f"گزارش دستگاه‌ها — "
              f"{jalali.to_fa_datetime(time.strftime('%Y-%m-%d %H:%M'))} — "
              f"{jalali.fa_digits(len(devices))} مورد")
    if fmt == "html":
        rows = "".join(
            f"<tr><td>{_esc(device.get('name'))}</td>"
            f"<td>{_esc(device.get('serial1'))}</td>"
            f"<td>{_esc((customers.get(device.get('customer_id')) or {}).get('full_name'))}</td>"
            f"<td>{_esc(device.get('stage'))}</td></tr>"
            for device in devices)
        return ("<!DOCTYPE html><html lang=\"fa\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
                "<title>گزارش دستگاه‌ها</title></head><body>"
                f"<h1>{_esc(header)}</h1>"
                "<table border=\"1\" cellpadding=\"6\"><tr><th>دستگاه</th><th>سریال</th>"
                "<th>مشتری</th><th>مرحله</th></tr>" + rows + "</table></body></html>")
    body = "\n".join(f"{i}. {item}" for i, item in enumerate(report_items, 1))
    return header + "\n" + body


_STAGE_RESULT_FA = {
    "healthy": "سالم",
    "problem": "دارای اشکال",
    "advanced": "ثبت‌شده در گمرک",
    "error": "بدون پاسخ",
    "pending": "در انتظار",
    "running": "در حال استعلام",
}


# --------------------------------------------------------------------------
# Finance report (v1.5): ledger aggregation over a daily/weekly period.
# Read-only over the store dict; the panel layer serves/renders it.
# --------------------------------------------------------------------------

# Single Python source of truth for transaction-kind labels; MUST stay
# byte-identical to the JS TX_KIND_FA map in the CRM page (pinned by
# test_v1_5_finance_report).
TX_KIND_LABELS = {
    "base_fee": "هزینه ثبت رجیستری",
    "base_fee_refund": "بازگشت هزینه ثبت رجیستری",
    "receipt": "دریافتی",
    "debt": "بدهی",
    "tx": "تراکنش",
    "": "—",
}

# Persian week (شنبه تا جمعه). Indexed by (date.weekday() + 2) % 7 under the
# Python Mon=0..Sun=6 convention: Sat(5) -> 0 ... Fri(4) -> 6.
WEEKDAY_FA = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه",
              "پنجشنبه", "جمعه")

_TX_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def week_range(ref):
    """(start, end) of the شنبه تا جمعه week containing `ref` (a date)."""
    start = ref - datetime.timedelta(days=(ref.weekday() - 5) % 7)
    return start, start + datetime.timedelta(days=6)


def _fa_money(n):
    """Signed int Toman -> Persian money string: «۱٬۲۳۴٬۵۶۷ تومان» /
    «−۵۰۰٬۰۰۰ تومان» (U+066C thousands separator, U+2212 minus)."""
    n = int(n or 0)
    body = jalali.fa_digits(f"{abs(n):,}").replace(",", "٬")
    return ("−" if n < 0 else "") + body + " تومان"


def _tx_kind_label(kind):
    return TX_KIND_LABELS.get(kind) or kind or "—"


def finance_report_aggregate(data, mode, ref):
    """Aggregate the per-customer ledgers over a period (read-only).

    mode "daily": from = to = ref. mode "weekly": the شنبه تا جمعه week of
    ref. Buckets key on tx["at"][:10]; an `at` that is not a str whose first
    10 characters fullmatch YYYY-MM-DD is an anomaly — excluded from every
    bucket, counted in "anomalies". `ref` may be a date/datetime or an ISO
    date string. Returns the dict served as /crm-finance-report?fmt=json and
    consumed by finance_report_html/txt.
    """
    if mode not in ("daily", "weekly"):
        raise ValueError(f"unknown finance report mode: {mode!r}")
    if isinstance(ref, str):
        ref = datetime.date.fromisoformat(ref)
    elif isinstance(ref, datetime.datetime):
        ref = ref.date()
    if mode == "weekly":
        start, end = week_range(ref)
    else:
        start = end = ref
    from_iso, to_iso = start.isoformat(), end.isoformat()

    day_stats = {}
    if mode == "weekly":
        for offset in range(7):
            day = start + datetime.timedelta(days=offset)
            day_stats[day.isoformat()] = {"debits": 0, "credits": 0,
                                          "receipts": 0, "count": 0}
    totals = {"opening": 0, "debits": 0, "credits": 0, "receipts": 0,
              "closing": 0, "count": 0, "debtors_total": 0,
              "creditors_total": 0}
    kind_stats = {}
    customers_out = []
    transactions = []
    anomalies = 0

    for customer in data.get("customers") or []:
        name = str(customer.get("full_name") or "").strip()
        opening = debits = credits = receipts = count = 0
        for tx in customer.get("transactions") or []:
            at = tx.get("at") if isinstance(tx, dict) else None
            if not isinstance(at, str) or not _TX_DATE_RE.fullmatch(at[:10]):
                anomalies += 1
                continue
            bucket = at[:10]
            try:
                amount = int(tx.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0
            if bucket < from_iso:
                opening += amount
                continue
            if bucket > to_iso:
                continue
            count += 1
            if amount < 0:
                debits += amount
            elif amount > 0:
                credits += amount
            kind = str(tx.get("kind") or "")
            if kind == "receipt":
                receipts += amount
            stat = kind_stats.get(kind)
            if stat is None:
                stat = kind_stats[kind] = {"kind": kind,
                                           "label": _tx_kind_label(kind),
                                           "count": 0, "amount": 0}
            stat["count"] += 1
            stat["amount"] += amount
            day = day_stats.get(bucket)
            if day is not None:
                if amount < 0:
                    day["debits"] += amount
                elif amount > 0:
                    day["credits"] += amount
                if kind == "receipt":
                    day["receipts"] += amount
                day["count"] += 1
            transactions.append({
                "at": at, "customer_id": str(customer.get("id") or ""),
                "customer": name, "kind": kind,
                "label": _tx_kind_label(kind), "amount": amount,
                "note": str(tx.get("note") or ""),
                "device_id": str(tx.get("device_id") or "")})
        closing = opening + credits + debits
        totals["opening"] += opening
        totals["debits"] += debits
        totals["credits"] += credits
        totals["receipts"] += receipts
        totals["count"] += count
        if closing < 0:
            totals["debtors_total"] += closing
        elif closing > 0:
            totals["creditors_total"] += closing
        if count > 0:
            customers_out.append({
                "id": str(customer.get("id") or ""), "name": name,
                "opening": opening, "debits": debits, "credits": credits,
                "receipts": receipts, "closing": closing, "count": count})
    totals["closing"] = (totals["opening"] + totals["credits"]
                         + totals["debits"])
    customers_out.sort(key=lambda row: row["credits"] + abs(row["debits"]),
                       reverse=True)
    kinds = sorted(kind_stats.values(),
                   key=lambda row: abs(row["amount"]), reverse=True)
    transactions.sort(key=lambda row: row["at"], reverse=True)
    days = []
    if mode == "weekly":
        for offset in range(7):
            day = start + datetime.timedelta(days=offset)
            stat = day_stats[day.isoformat()]
            jy, jm, jd = jalali.gregorian_to_jalali(day.year, day.month,
                                                    day.day)
            days.append({
                "date": day.isoformat(),
                "date_fa": f"{jalali.fa_digits(jd)} {jalali.MONTHS[jm - 1]}",
                "day_fa": WEEKDAY_FA[(day.weekday() + 2) % 7],
                "debits": stat["debits"], "credits": stat["credits"],
                "receipts": stat["receipts"], "count": stat["count"]})
    return {
        "mode": mode, "from": from_iso, "to": to_iso,
        "from_fa": jalali.to_fa(from_iso), "to_fa": jalali.to_fa(to_iso),
        "totals": totals, "kinds": kinds, "customers": customers_out,
        "days": days, "transactions": transactions, "anomalies": anomalies,
    }


def finance_report_html(agg):
    """Self-contained RTL print document for the finance report (v1.5)."""
    mode = str(agg.get("mode") or "daily")
    weekly = mode == "weekly"
    from_fa = str(agg.get("from_fa") or "")
    to_fa = str(agg.get("to_fa") or "")
    title = (f"گزارش مالی مشتریان — {from_fa} تا {to_fa}" if weekly
             else f"گزارش مالی مشتریان — {from_fa}")
    totals = agg.get("totals") or {}
    empty_line = "در این بازه تراکنشی ثبت نشده است."

    def money(n):
        n = int(n or 0)
        if n < 0:
            return f'<span class="neg">{_fa_money(n)}</span>'
        if n > 0:
            return f'<span class="pos">{_fa_money(n)}</span>'
        return _fa_money(0)

    def balance(n):
        n = int(n or 0)
        return "تسویه" if n == 0 else money(n)

    out = [
        '<!DOCTYPE html><html dir="rtl" lang="fa">',
        '<head><meta charset="utf-8"><title>گزارش مالی مشتریان</title>',
        "<style>",
        "@font-face{font-family:'Vazirmatn';"
        "src:url('/fonts/Vazirmatn-var.woff2') format('woff2');"
        "font-weight:100 900;font-display:swap;}",
        "body{font-family:'Vazirmatn',tahoma,sans-serif;direction:rtl;"
        "margin:24px;color:#111;background:#fff}",
        "h1{font-size:18px;margin:0 0 2px}h2{font-size:15px;margin:22px 0 8px}",
        "table{border-collapse:collapse;width:100%;font-size:12.5px}",
        "th,td{border:1px solid #999;padding:4px 8px;text-align:right}",
        "th{background:#f2f2f2}",
        ".sum{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}",
        ".sum>div{border:1px solid #bbb;padding:8px 14px;min-width:150px}",
        ".muted{color:#666}.pos{color:#0c7d4f}.neg{color:#d93752}",
        "@media print{body{margin:8mm}}</style></head><body>",
        f"<h1>{_esc(title)}</h1>",
        f'<p class="muted">{"هفتگی" if weekly else "روزانه"}</p>',
        '<div class="sum">',
        '<div><span class="muted">دریافتی‌های نقدی (واریزی)</span><br>'
        + money(totals.get("receipts")) + "</div>",
        '<div><span class="muted">بدهکاری‌های جدید</span><br>'
        + money(totals.get("debits")) + "</div>",
        '<div><span class="muted">ماندهٔ کل مشتریان</span><br>'
        + balance(totals.get("closing"))
        + '<br><span class="muted">بدهکار '
        + _fa_money(totals.get("debtors_total")) + " / بستانکار "
        + _fa_money(totals.get("creditors_total")) + "</span></div></div>",
        "<h2>انواع تراکنش</h2>",
    ]
    kinds = agg.get("kinds") or []
    if kinds:
        rows = "".join(
            f"<tr><td>{_esc(k.get('label'))}</td>"
            f"<td>{jalali.fa_digits(k.get('count') or 0)}</td>"
            f"<td>{money(k.get('amount'))}</td></tr>" for k in kinds)
        out.append("<table><tr><th>نوع</th><th>تعداد</th><th>مبلغ</th></tr>"
                   + rows + "</table>")
    else:
        out.append(f'<p class="muted">{empty_line}</p>')
    if weekly:
        out.append("<h2>روزهای هفته</h2>")
        rows = "".join(
            f"<tr><td>{_esc(d.get('day_fa'))}</td>"
            f"<td>{_esc(d.get('date_fa'))}</td>"
            f"<td>{money(d.get('debits'))}</td>"
            f"<td>{money(d.get('credits'))}</td>"
            f"<td>{money(d.get('receipts'))}</td></tr>"
            for d in agg.get("days") or [])
        out.append("<table><tr><th>روز</th><th>تاریخ</th><th>بدهکاری</th>"
                   "<th>بستانکاری</th><th>دریافتی</th></tr>"
                   + rows + "</table>")
    out.append("<h2>مشتریان</h2>")
    customers = agg.get("customers") or []
    if customers:
        rows = "".join(
            f"<tr><td>{_esc(c.get('name'))}</td>"
            f"<td>{balance(c.get('opening'))}</td>"
            f"<td>{money(c.get('debits'))}</td>"
            f"<td>{money(c.get('credits'))}</td>"
            f"<td>{balance(c.get('closing'))}</td></tr>" for c in customers)
        out.append("<table><tr><th>مشتری</th><th>ماندهٔ اول</th>"
                   "<th>بدهکاری</th><th>بستانکاری</th><th>ماندهٔ پایان</th>"
                   "</tr>" + rows + "</table>")
    else:
        out.append(f'<p class="muted">{empty_line}</p>')
    out.append("<h2>تراکنش‌های این بازه</h2>")
    transactions = agg.get("transactions") or []
    if transactions:
        rows = "".join(
            f"<tr><td>{_esc(jalali.to_fa_datetime(t.get('at')))}</td>"
            f"<td>{_esc(t.get('customer'))}</td>"
            f"<td>{_esc(t.get('label'))}</td>"
            f"<td>{money(t.get('amount'))}</td>"
            f"<td>{_esc(t.get('note'))}</td></tr>" for t in transactions)
        out.append("<table><tr><th>زمان</th><th>مشتری</th><th>نوع</th>"
                   "<th>مبلغ</th><th>شرح</th></tr>" + rows + "</table>")
    else:
        out.append(f'<p class="muted">{empty_line}</p>')
    out.append("<p>مانده‌ها بر اساس مشتری‌های فعلی است.</p>")
    anomalies = int(agg.get("anomalies") or 0)
    if anomalies > 0:
        out.append(f'<p class="muted">{jalali.fa_digits(anomalies)}'
                   f' تراکنش با تاریخ نامعتبر نادیده‌گرفته شد.</p>')
    out.append("</body></html>")
    return "".join(out)


def finance_report_txt(agg):
    """Plain-text finance report for Telegram/download (no markup)."""
    mode = str(agg.get("mode") or "daily")
    weekly = mode == "weekly"
    from_fa = str(agg.get("from_fa") or "")
    to_fa = str(agg.get("to_fa") or "")
    title = (f"گزارش مالی مشتریان — {from_fa} تا {to_fa}" if weekly
             else f"گزارش مالی مشتریان — {from_fa}")
    totals = agg.get("totals") or {}
    empty_line = "در این بازه تراکنشی ثبت نشده است."

    def balance(n):
        n = int(n or 0)
        return "تسویه" if n == 0 else _fa_money(n)

    lines = [title, "هفتگی" if weekly else "روزانه", "",
             f"دریافتی‌های نقدی (واریزی): {_fa_money(totals.get('receipts'))}",
             f"بدهکاری‌های جدید: {_fa_money(totals.get('debits'))}",
             f"ماندهٔ کل مشتریان: {balance(totals.get('closing'))} "
             f"(بدهکار {_fa_money(totals.get('debtors_total'))} / "
             f"بستانکار {_fa_money(totals.get('creditors_total'))})",
             "", "انواع تراکنش:"]
    kinds = agg.get("kinds") or []
    if kinds:
        lines.extend(
            f"- {k.get('label')}: "
            f"{jalali.fa_digits(k.get('count') or 0)} مورد، "
            f"{_fa_money(k.get('amount'))}" for k in kinds)
    else:
        lines.append(empty_line)
    if weekly:
        lines.append("")
        lines.append("روزهای هفته:")
        lines.extend(
            f"- {d.get('day_fa')} {d.get('date_fa')}: "
            f"بدهکاری {_fa_money(d.get('debits'))} / "
            f"بستانکاری {_fa_money(d.get('credits'))} / "
            f"دریافتی {_fa_money(d.get('receipts'))}"
            for d in agg.get("days") or [])
    lines.append("")
    lines.append("مشتریان:")
    customers = agg.get("customers") or []
    if customers:
        lines.extend(
            f"- {c.get('name')}: ماندهٔ اول {balance(c.get('opening'))} | "
            f"بدهکاری {_fa_money(c.get('debits'))} | "
            f"بستانکاری {_fa_money(c.get('credits'))} | "
            f"ماندهٔ پایان {balance(c.get('closing'))}" for c in customers)
    else:
        lines.append(empty_line)
    lines.append("")
    lines.append("تراکنش‌های این بازه:")
    transactions = agg.get("transactions") or []
    if transactions:
        for t in transactions:
            note = str(t.get("note") or "")
            lines.append(
                f"- {jalali.to_fa_datetime(t.get('at'))} | "
                f"{t.get('customer')} | {t.get('label')} | "
                f"{_fa_money(t.get('amount'))}"
                + (f" | {note}" if note else ""))
    else:
        lines.append(empty_line)
    lines.append("")
    lines.append("مانده‌ها بر اساس مشتری‌های فعلی است.")
    anomalies = int(agg.get("anomalies") or 0)
    if anomalies > 0:
        lines.append(f"{jalali.fa_digits(anomalies)} "
                     "تراکنش با تاریخ نامعتبر نادیده‌گرفته شد.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Identity materialization + estelam queue sync (plan section 2; review
# F1/F2/F3/F8). The robots never learn about CRM: CRM writes the chosen
# identity into the existing single store["person"] right before its own
# strictly-serialized per-device run.
# --------------------------------------------------------------------------

INTAKE_STAGES = ("new", "inquiry", "problem", "ready_owner")
WATCH_STAGES = ("handed", "to_payment", "payment", "payment_done", "done")


def pick_pool_identity():
    """A random VALID pool entry, or (None, errors). Entries failing
    validate_estelam_person are never picked (review F8)."""
    data = store_read()
    valid = []
    for entry in data.get("identities") or []:
        candidate = {"national": entry.get("national"),
                     "birth": entry.get("birth"), "mobile": entry.get("mobile")}
        if not validate_identity(candidate):
            valid.append(entry)
    if not valid:
        return None, ["استخر مشخصات استعلام خالی است یا هیچ ورودی معتبری ندارد"]
    import random
    entry = random.choice(valid)
    return {"national": entry.get("national"), "birth": entry.get("birth"),
            "mobile": entry.get("mobile")}, []


def identity_for_stage(device_id, stage=None):
    """Identity for ANY inquiry stage: a random VALID pool entry.

    Both intake and watch-stage inquiries run under the pool (user item 7):
    a legally-registered device is recorded under the passenger's national
    code, so querying with a pool code returns the site's "registered under
    another code / not the owner" message — which is itself the positive
    "registered" signal that gates the payment step. Fails closed exactly
    like intake when no valid pool entry exists (review F8): the caller
    must NOT send the request. `stage` is kept for signature compatibility
    and no longer changes the routing.
    """
    return pick_pool_identity()
