"""jalali.py — Gregorian-to-Jalali (Shamsi) conversion and Persian date
formatting for report output (v1.4.2).

Architecture rule: stored data stays ISO Gregorian; conversion happens only
when user-facing report text is built (txt/html/pdf report + the report
Telegram message). Pure stdlib.
"""
import datetime
import re

MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

MISSING = "—"  # rendered for empty/None dates (matches report conventions)

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# Store/timestamp formats: "YYYY-MM-DD", "YYYY-MM-DD HH:MM",
# "YYYY-MM-DD HH:MM:SS" (space or ISO "T" separator).
_DT_RE = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})"
    r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


def fa_digits(text):
    """ASCII digits -> Persian digits; everything else passes through."""
    return str(text).translate(_FA_DIGITS)


def gregorian_to_jalali(gy, gm, gd):
    """Gregorian -> Jalali, classic 33-year-cycle conversion.

    Returns (jy, jm, jd). The same algorithm crm.py has shipped since F20;
    this module is now the single source of truth.
    """
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


def _parse(value):
    """-> ((gy, gm, gd), (hh, mm) or None), or None when unparseable.

    Accepts datetime/date objects and the store's string formats. Real
    calendar validation via datetime.date (rejects 2026-02-30 and friends).
    """
    if isinstance(value, datetime.datetime):
        return (value.year, value.month, value.day), (value.hour, value.minute)
    if isinstance(value, datetime.date):
        return (value.year, value.month, value.day), None
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _DT_RE.match(text)
    if not match:
        return None
    gy, gm, gd = (int(match.group(i)) for i in (1, 2, 3))
    try:
        datetime.date(gy, gm, gd)
    except ValueError:
        return None
    if match.group(4) is None:
        return (gy, gm, gd), None
    hh, mm = int(match.group(4)), int(match.group(5))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    if match.group(6) is not None and not 0 <= int(match.group(6)) <= 59:
        return None
    return (gy, gm, gd), (hh, mm)


def _format(value, with_clock):
    parsed = _parse(value)
    if parsed is None:
        # Empty -> «—»; non-empty garbage returns unchanged (data is never
        # silently destroyed).
        text = str(value or "").strip()
        return text if text else MISSING
    (gy, gm, gd), clock = parsed
    jy, jm, jd = gregorian_to_jalali(gy, gm, gd)
    out = f"{fa_digits(jd)} {MONTHS[jm - 1]} {fa_digits(jy)}"
    if with_clock and clock is not None:
        hh, mm = clock
        out += f"، {fa_digits(f'{hh:02d}:{mm:02d}')}"
    return out


def to_fa(value):
    """ISO date/datetime string or datetime/date -> «۲۲ شهریور ۱۴۰۵».

    Any time in the input is ignored. None/empty -> «—»; a non-empty value
    that does not parse returns unchanged.
    """
    return _format(value, with_clock=False)


def to_fa_datetime(value):
    """Like to_fa but keeps the clock: «۲۲ شهریور ۱۴۰۵، ۱۷:۳۰».

    A date-only input yields the plain date form.
    """
    return _format(value, with_clock=True)
