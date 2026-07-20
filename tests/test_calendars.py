"""
test_calendars.py — Working calendars: federal holidays, work weeks, and the
guarantee that schedules which don't opt in are completely unaffected.

Run with: python -m pytest tests/ -v
"""

import os
import re
import sys
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.calendars import (us_federal_holidays, holiday_dates, preset_for,
                              WORKWEEK_5_DAY, WORKWEEK_6_DAY, WORKWEEK_7_DAY)
from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   compute_dates)


# ── Federal holiday rules ────────────────────────────────────────────────────

def _lookup(start=2025, end=2028):
    return dict(us_federal_holidays(start, end))


def test_fixed_holiday_on_saturday_observed_friday():
    # 4 Jul 2026 falls on a Saturday -> observed Friday 3 Jul
    assert _lookup()["2026-07-03"] == "Independence Day"


def test_fixed_holiday_on_sunday_observed_monday():
    # 4 Jul 2027 falls on a Sunday -> observed Monday 5 Jul
    assert _lookup()["2027-07-05"] == "Independence Day"


def test_floating_holidays_land_on_the_right_weekday():
    h = _lookup()
    assert h["2026-01-19"].startswith("Birthday of Martin Luther King")  # 3rd Mon Jan
    assert h["2026-05-25"] == "Memorial Day"                             # last Mon May
    assert h["2026-11-26"] == "Thanksgiving Day"                         # 4th Thu Nov
    assert h["2026-09-07"] == "Labor Day"                                # 1st Mon Sep


def test_no_observed_holiday_ever_falls_on_a_weekend():
    for iso in holiday_dates(2024, 2035):
        assert dt.date.fromisoformat(iso).weekday() < 5, iso


def test_eleven_federal_holidays_per_year():
    assert len(holiday_dates(2026, 2026)) == 11


def test_preset_lookup_tolerates_p6_calendar_names():
    assert preset_for("P5-DAY NO HOL")[0] == WORKWEEK_5_DAY
    assert preset_for("P5-DAY NO HOL")[1] is False
    assert preset_for("G7-DAY NO HOLIDAY")[0] == WORKWEEK_7_DAY
    assert preset_for("6-DAY WITH HOLIDAY")[0] == WORKWEEK_6_DAY


# ── CPM honours the calendar ─────────────────────────────────────────────────

def _one_task_project(cal, days=10, start="2026-06-29"):
    """A single task spanning the 4 Jul 2026 holiday (observed Fri 3 Jul)."""
    p = Project(uid="1", name="cal", id="CAL", planned_start=start)
    p.calendars = [cal]
    p.wbs_nodes = [WBSNode(uid="10", name="W", code="W")]
    p.activities = [Activity(uid="100", activity_id="A1000", name="Task",
                             wbs_uid="10", calendar_uid=cal.uid,
                             planned_duration=days * 8.0,
                             remaining_duration=days * 8.0)]
    p.build_lookups()
    compute_dates(p)
    return p.activities[0]


def test_default_calendar_is_five_day_and_ignores_holidays():
    """The default must reproduce the original hard-coded Mon-Fri behaviour."""
    a = _one_task_project(Calendar(uid="1", name="Standard"))
    assert a.planned_start == "2026-06-29"
    assert a.planned_finish == "2026-07-13"


def test_holiday_calendar_pushes_the_finish_out():
    hols = frozenset(holiday_dates())
    a = _one_task_project(Calendar(uid="1", name="5-DAY WITH HOLIDAY",
                                   work_days=WORKWEEK_5_DAY, holidays=hols))
    # one observed holiday inside the window -> one day later
    assert a.planned_finish == "2026-07-14"


def test_six_day_calendar_finishes_earlier_than_five_day():
    hols = frozenset(holiday_dates())
    five = _one_task_project(Calendar(uid="1", name="5-DAY WITH HOLIDAY",
                                      work_days=WORKWEEK_5_DAY, holidays=hols))
    six = _one_task_project(Calendar(uid="1", name="6-DAY WITH HOLIDAY",
                                     work_days=WORKWEEK_6_DAY, holidays=hols))
    assert six.planned_finish < five.planned_finish


def test_work_never_lands_on_a_non_working_day():
    hols = frozenset(holiday_dates())
    a = _one_task_project(Calendar(uid="1", name="5-DAY WITH HOLIDAY",
                                   work_days=WORKWEEK_5_DAY, holidays=hols))
    for iso in (a.planned_start, a.planned_finish):
        d = dt.date.fromisoformat(iso)
        assert d.weekday() < 5, f"{iso} is a weekend"
        assert iso not in hols, f"{iso} is a holiday"


# ── Export safety ────────────────────────────────────────────────────────────

def _norm(xml_text):
    """GUIDs are regenerated per run; ignore them when comparing."""
    return re.sub(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}",
                  "{GUID}", xml_text)


def _export(project, tmp_path, name):
    from engine.xml_writer import write_p6_xml
    out = os.path.join(str(tmp_path), name)
    write_p6_xml(project, out)
    return _norm(open(out).read())


def test_default_export_writes_no_holiday_exceptions(tmp_path):
    """A schedule that never opts in must export exactly as it always did."""
    from tests.test_engine import _make_project
    p = _make_project()
    p.build_lookups()
    xml = _export(p, tmp_path, "default.xml")
    assert "HolidayOrException" not in xml
    assert "6-DAY WITH HOLIDAY" not in xml


def test_six_day_export_adds_calendar_and_holidays(tmp_path):
    hols = frozenset(holiday_dates(2026, 2027))
    p = Project(uid="1", name="six", id="SIX", planned_start="2026-06-29")
    p.calendars = [Calendar(uid="1", name="6-DAY WITH HOLIDAY",
                            work_days=WORKWEEK_6_DAY, holidays=hols)]
    p.wbs_nodes = [WBSNode(uid="10", name="W", code="W")]
    p.activities = [Activity(uid="100", activity_id="A1000", name="Task",
                             wbs_uid="10", calendar_uid="1",
                             planned_duration=80.0, remaining_duration=80.0)]
    p.build_lookups()
    xml = _export(p, tmp_path, "six.xml")
    assert "G6-DAY WITH HOLIDAY" in xml
    assert "P6-DAY WITH HOLIDAY" in xml
    assert "<Date>2026-07-03T00:00:00</Date>" in xml      # observed 4 Jul
    assert xml.count("<HolidayOrException>") == len(hols) * 4   # 2 global + 2 project
