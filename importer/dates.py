"""Parse manifest `date_raw` -> (date_start, date_end, precision) per SPEC §3.8.

`date_raw` is ALSO kept verbatim by the importer as the human display label; this
module only produces the machine range used by the timeline overlap filter (§4.1).
Precision is one of: day | month | season | year | approx.
"""
import calendar
import re
from datetime import date

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "march": 3, "apr": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Season -> (start_month, start_day, end_month, end_day). Winter crosses the year.
SEASONS = {
    "spring": (3, 1, 5, 31),
    "summer": (6, 1, 8, 31),
    "fall": (9, 1, 11, 30),
    "autumn": (9, 1, 11, 30),
    "winter": (12, 1, 2, 28),  # Dec(year) .. Feb(year+1); handled specially
}


def _eom(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth (1-based) weekday (Mon=0) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, _eom(year, month))
    offset = (last.weekday() - weekday) % 7
    return date(year, month, last.day - offset)


def _season_range(season: str, year: int):
    sm, sd, em, ed = SEASONS[season]
    if season == "winter":
        return date(year, sm, sd), date(year + 1, 2, _eom(year + 1, 2)), "season"
    return date(year, sm, sd), date(year, em, ed), "season"


def parse_date_raw(raw: str):
    """Return (date_start: date|None, date_end: date|None, precision: str|None).

    On an unrecognized string, falls back to a bare 4-digit year if present,
    else (None, None, None) — the caller logs misses and may use the magazine span.
    """
    if not raw or not raw.strip():
        return None, None, None
    s = raw.strip()
    low = s.lower()

    years = re.findall(r"(19\d{2}|20\d{2})", s)
    approx = bool(re.search(r"\b(c\.|circa|approx|early)\b", low) or "~" in s)

    # --- Holidays (specific dates) ---
    if "christmas" in low and years:
        y = int(years[0]); d = date(y, 12, 25)
        return d, d, "day"
    if "easter" in low and years:
        d = _easter(int(years[0]))
        return d, d, "day"
    if "thanksgiving" in low and years:
        d = _nth_weekday(int(years[0]), 11, 3, 4)  # 4th Thursday (Thu=3)
        return d, d, "day"
    if "memorial day" in low and years:
        d = _last_weekday(int(years[0]), 5, 0)      # last Monday (Mon=0)
        return d, d, "day"

    # --- "Early YYYY" -> Jan..Apr, approx ---
    if "early" in low and years:
        y = int(years[0])
        return date(y, 1, 1), date(y, 4, 30), "approx"

    # --- circa year or year-range: "c. 1970", "c. 1969-1970" ---
    if re.match(r"^(c\.|circa)", low) and years:
        y0 = int(years[0]); y1 = int(years[-1])
        return date(y0, 1, 1), date(y1, 12, 31), "approx"

    # --- Seasons (single or range), one year (winter year-range handled too) ---
    season_tokens = re.findall(r"spring|summer|fall|autumn|winter", low)
    if season_tokens and years:
        # "Winter 1970-71" -> two-year tokens; use first year as anchor
        y = int(years[0])
        s_start, _, _ = _season_range(season_tokens[0], y)
        _, e_end, _ = _season_range(season_tokens[-1], y)
        prec = "season"
        return s_start, e_end, prec

    # --- Month forms ---
    mfound = re.findall(r"[A-Za-z]+", s)
    month_tokens = [MONTHS[m.lower()] for m in mfound if m.lower() in MONTHS]

    # Day-precise: "July 4 1964", "June 11 1969"
    m_day = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\s+(19\d{2}|20\d{2})$", s)
    if m_day and m_day.group(1).lower() in MONTHS:
        mo = MONTHS[m_day.group(1).lower()]
        d = date(int(m_day.group(3)), mo, int(m_day.group(2)))
        return d, d, "day"

    if month_tokens and years:
        y = int(years[0])
        m0, m1 = month_tokens[0], month_tokens[-1]
        # year may differ only for explicit ranges; single year assumed here
        start = date(y, m0, 1)
        end = date(y, m1, _eom(y, m1))
        prec = "month"
        return start, end, prec

    # --- Bare year ---
    if years and not month_tokens and not season_tokens:
        y = int(years[0])
        prec = "approx" if approx else "year"
        return date(y, 1, 1), date(y, 12, 31), prec

    # Unrecognized but a year exists -> fall back to whole year.
    if years:
        y = int(years[0])
        return date(y, 1, 1), date(y, 12, 31), "year"
    return None, None, None
