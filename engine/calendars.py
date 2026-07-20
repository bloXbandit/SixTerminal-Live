# -*- coding: utf-8 -*-
"""
calendars.py — Work-week patterns and US federal holidays.

The app ships a small, known set of calendars rather than trying to round-trip
arbitrary P6 calendars:

    5-DAY NO HOLIDAY   Mon-Fri, works through holidays   (the default)
    5-DAY WITH HOLIDAY Mon-Fri, federal holidays off
    6-DAY WITH HOLIDAY Mon-Sat, federal holidays off
    7-DAY NO HOLIDAY   every day, works through holidays

Holidays are generated from the federal rules rather than hard-coded, because
the observed date moves: a fixed-date holiday landing on a Saturday is observed
the preceding Friday, and on a Sunday the following Monday. Typing a table by
hand drifts a day and silently shifts the schedule.
"""

import datetime as _dt
from typing import Dict, List, Set, Tuple

# Weekday numbers match datetime.date.weekday(): Mon=0 ... Sun=6
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)

WORKWEEK_5_DAY = frozenset({MON, TUE, WED, THU, FRI})
WORKWEEK_6_DAY = frozenset({MON, TUE, WED, THU, FRI, SAT})
WORKWEEK_7_DAY = frozenset({MON, TUE, WED, THU, FRI, SAT, SUN})

# Default horizon. Long-lead datacenter work runs years out, so keep this wide.
DEFAULT_START_YEAR = 2024
DEFAULT_END_YEAR = 2035


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """The nth <weekday> of a month, e.g. 3rd Monday of January."""
    d = _dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + _dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    """The last <weekday> of a month, e.g. last Monday of May."""
    if month == 12:
        nxt = _dt.date(year + 1, 1, 1)
    else:
        nxt = _dt.date(year, month + 1, 1)
    d = nxt - _dt.timedelta(days=1)
    return d - _dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: _dt.date) -> _dt.date:
    """Federal observation rule: Saturday -> Friday before, Sunday -> Monday after."""
    if d.weekday() == SAT:
        return d - _dt.timedelta(days=1)
    if d.weekday() == SUN:
        return d + _dt.timedelta(days=1)
    return d


def us_federal_holidays(start_year: int = DEFAULT_START_YEAR,
                        end_year: int = DEFAULT_END_YEAR) -> List[Tuple[str, str]]:
    """
    Return [(iso_date, name), ...] of observed US federal holidays, sorted.
    Floating-Monday/Thursday holidays already fall on a weekday, so the
    observation rule is applied only to the fixed-date ones.
    """
    out: List[Tuple[str, str]] = []
    for y in range(start_year, end_year + 1):
        fixed = [
            (_dt.date(y, 1, 1),   "New Year's Day"),
            (_dt.date(y, 6, 19),  "Juneteenth National Independence Day"),
            (_dt.date(y, 7, 4),   "Independence Day"),
            (_dt.date(y, 11, 11), "Veterans Day"),
            (_dt.date(y, 12, 25), "Christmas Day"),
        ]
        floating = [
            (_nth_weekday(y, 1, MON, 3),  "Birthday of Martin Luther King, Jr."),
            (_nth_weekday(y, 2, MON, 3),  "Washington's Birthday"),
            (_last_weekday(y, 5, MON),    "Memorial Day"),
            (_nth_weekday(y, 9, MON, 1),  "Labor Day"),
            (_nth_weekday(y, 10, MON, 2), "Columbus Day"),
            (_nth_weekday(y, 11, THU, 4), "Thanksgiving Day"),
        ]
        for d, name in fixed:
            out.append((_observed(d).isoformat(), name))
        for d, name in floating:
            out.append((d.isoformat(), name))
    out.sort()
    return out


def holiday_dates(start_year: int = DEFAULT_START_YEAR,
                  end_year: int = DEFAULT_END_YEAR) -> Set[str]:
    """Just the observed ISO dates, for fast membership tests during CPM."""
    return {iso for iso, _ in us_federal_holidays(start_year, end_year)}


# ── The calendar presets the app offers ──────────────────────────────────────
# name -> (work_days, honors_holidays)
CALENDAR_PRESETS: Dict[str, Tuple[frozenset, bool]] = {
    "5-DAY NO HOLIDAY":   (WORKWEEK_5_DAY, False),   # default — current behaviour
    "5-DAY WITH HOLIDAY": (WORKWEEK_5_DAY, True),
    "6-DAY WITH HOLIDAY": (WORKWEEK_6_DAY, True),
    "7-DAY NO HOLIDAY":   (WORKWEEK_7_DAY, False),
}

DEFAULT_CALENDAR_NAME = "5-DAY NO HOLIDAY"


def preset_for(name: str) -> Tuple[frozenset, bool]:
    """Look up a preset by name, tolerant of case and spacing."""
    if not name:
        return CALENDAR_PRESETS[DEFAULT_CALENDAR_NAME]
    key = " ".join(str(name).upper().split())
    if key in CALENDAR_PRESETS:
        return CALENDAR_PRESETS[key]
    # tolerate names carried in from P6 ("P5-DAY NO HOL", "G7-DAY NO HOLIDAY")
    six   = "6" in key or "SIX" in key
    seven = "7" in key or "SEVEN" in key
    no_hol = "NO HOL" in key
    has_hol = (not no_hol) and ("HOL" in key)
    if seven:
        return (WORKWEEK_7_DAY, has_hol)
    if six:
        return (WORKWEEK_6_DAY, has_hol if ("HOL" in key) else True)
    return (WORKWEEK_5_DAY, has_hol)
