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
               16-digit card).
  identities — the inquiry-identity pool. Pre-registration intake inquiries
               pick a RANDOM entry; the watch re-inquiry runs under the
               device owner's identity (customs registration is scoped to the
               registering identity — see plan §2, review F1).
  devices    — master case records driven through the stage machine.

All English in code per the project contract; Persian appears only in
user-facing error strings.
"""
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
}

# Stage machine: stage -> {action -> next stage}. Single source of truth (F12).
ALLOWED = {
    "new": {
        "queue_inquiry": "inquiry",
    },
    "inquiry": {
        "mark_problem": "problem",
        "mark_ready": "ready_owner",      # scheduler auto-join also uses this
    },
    "problem": {
        "requeue_inquiry": "inquiry",
    },
    "ready_owner": {
        "attach_owner": "ready_owner",    # stays until handover tick
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
    """Gregorian -> Jalali (the classic 20-line Borkowski-style conversion)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2] + gd2
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        g_day_no += 1
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        jm = 1 + j_day_no // 31
        jd = 1 + j_day_no % 31
    else:
        jm = 7 + (j_day_no - 186) // 30
        jd = 1 + (j_day_no - 186) % 30
    return jy, jm, jd


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
        errors.append("نام مالک الزامی است")
    if not str(data.get("last_name") or "").strip():
        errors.append("نام خانوادگی مالک الزامی است")
    national = normalize_digits(data.get("national")).strip()
    if not national_checksum_ok(national):
        errors.append("کد ملی نامعتبر است")
    birth = normalize_digits(data.get("birth")).strip()
    if not re.fullmatch(BIRTH_RE, birth):
        errors.append("تاریخ تولد باید به شکل ۱۳۷۵/۰۴/۲۹ یا ۱۳۷۵/۴/۲۹ باشد")
    else:
        age = shamsi_age(birth)
        if age is None or age < 15 or age > 99:
            errors.append("سن مالک باید بین ۱۵ تا ۹۹ سال باشد")
    mobile = normalize_digits(data.get("mobile")).strip()
    if not re.fullmatch(MOBILE_RE, mobile):
        errors.append("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود")
    card = normalize_digits(data.get("card_no")).replace("-", "").replace(" ", "")
    if not re.fullmatch(CARD_RE, card):
        errors.append("شماره کارت باید ۱۶ رقم باشد")
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


def validate_device(data):
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
    try:
        path = log_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
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
            device["customer_id"] = ""
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


def add_transaction(customer_id, amount, note=""):
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
        entry = {"at": _now(), "amount": amount, "note": str(note or "").strip()}
        record.setdefault("transactions", []).append(entry)
        record["updated"] = _now()
        _store_write(data)
        return entry, []


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
            "card_no": normalize_digits(fields.get("card_no")).replace("-", "").strip(),
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
            return None, ["مالک پیدا نشد"]
        merged = dict(record)
        merged.update({k: fields[k] for k in
                       ("first_name", "last_name", "national", "birth",
                        "mobile", "card_no") if k in (fields or {})})
        errs = validate_owner(merged)
        if errs:
            return None, errs
        record.update(merged)
        for key in ("national", "birth", "mobile", "card_no"):
            record[key] = normalize_digits(record[key]).replace("-", "").strip()
        _store_write(data)
        return record, []


def delete_owner(owner_id):
    """Delete an unbound owner; a bound owner must be unlinked first (1:1)."""
    with STORE_LOCK:
        data = _store_read()
        bound = next((d for d in data.get("devices", [])
                      if d.get("owner_id") == owner_id), None)
        if bound:
            return False, ["این مالک به یک دستگاه متصل است"]
        before = len(data.get("owners", []))
        data["owners"] = [o for o in data.get("owners", [])
                          if o.get("id") != owner_id]
        if len(data["owners"]) == before:
            return False, ["مالک پیدا نشد"]
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
        _store_write(data)
        return record, []


def update_device(device_id, fields):
    with STORE_LOCK:
        data = _store_read()
        record = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not record:
            return None, ["دستگاه پیدا نشد"]
        merged = dict(record)
        merged.update({k: fields[k] for k in
                       ("name", "serial1", "serial2", "customer_id",
                        "auto_advance", "notes") if k in (fields or {})})
        errs = validate_device(merged)
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
            return None, ["مالک پیدا نشد"]
        bound = next((d for d in data.get("devices", [])
                      if d.get("owner_id") == owner_id
                      and d.get("id") != device_id), None)
        if bound:
            return None, ["این مالک قبلاً به دستگاه دیگری متصل است"]
        device["owner_id"] = owner_id
        device["updated"] = _now()
        _store_write(data)
        return device, []


# --------------------------------------------------------------------------
# Stage machine
# --------------------------------------------------------------------------

def advance(device_id, action, **payload):
    """Apply a stage action; returns (device, errors). Persian errors."""
    with STORE_LOCK:
        data = _store_read()
        device = next((d for d in data.get("devices", [])
                       if d.get("id") == device_id), None)
        if not device:
            return None, ["دستگاه پیدا نشد"]
        stage = str(device.get("stage") or "new")
        next_stage = ALLOWED.get(stage, {}).get(action)
        if not next_stage:
            return None, [f"این کنش در مرحلهٔ فعلی مجاز نیست ({stage})"]
        # Tick idempotency: a tick whose timestamp is already set is a no-op.
        stamp_key = _STAGE_TIMESTAMP_KEY.get(next_stage)
        if stamp_key and device.get(stamp_key) and action in (
                "handover", "confirm_payment", "deliver_code"):
            return None, ["این مرحله قبلاً تأیید شده است"]
        if action == "attach_owner":
            owner_id = str(payload.get("owner_id") or "").strip()
            if not owner_id:
                return None, ["انتخاب مالک الزامی است"]
            owner = next((o for o in data.get("owners", [])
                          if o.get("id") == owner_id), None)
            if not owner:
                return None, ["مالک پیدا نشد"]
            bound = next((d for d in data.get("devices", [])
                          if d.get("owner_id") == owner_id
                          and d.get("id") != device_id), None)
            if bound:
                return None, ["این مالک قبلاً به دستگاه دیگری متصل است"]
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
    "ready_owner": "سالم — نیازمند مالک",
    "handed": "تحویل‌شده به مسئول سایت",
    "to_payment": "ثبت‌شده — در انتظار پرداخت",
    "payment": "در حال پرداخت",
    "payment_done": "پرداخت تأییدشده",
    "done": "تکمیل‌شده",
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
            parts.append(f"مالک: {owner.get('first_name')} {owner.get('last_name')}"
                         f" | کد ملی: {mask_national(owner.get('national'))}"
                         f" | تولد: {owner.get('birth')}"
                         f" | موبایل: {owner.get('mobile')}")
            if full_data and not plain:
                parts.append(f"کد ملی کامل: {owner.get('national')}"
                             f" | کارت: {owner.get('card_no')}")
        est = device.get("estelam") or {}
        if est:
            parts.append(f"استعلام گمرک: {_STAGE_RESULT_FA.get(est.get('status'), est.get('status') or '—')}"
                         f" | همتا: {est.get('hamta_status') or '—'}")
        parts.append(f"مرحله: {_STAGE_FA.get(device.get('stage'), device.get('stage'))}")
        return " | ".join(parts)

    report_items = [line(d) for d in devices]
    header = f"گزارش دستگاه‌ها — {time.strftime('%Y-%m-%d %H:%M')} — {len(devices)} مورد"
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
