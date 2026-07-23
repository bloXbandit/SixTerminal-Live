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


# ── WBS re-parenting (move_wbs) ──────────────────────────────────────────────

def _wbs_project():
    from engine.schedule_model import Project, WBSNode, Activity, Calendar
    p = Project(uid="1", name="t", id="T")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="10", name="Structure", code="STR"),
        WBSNode(uid="11", name="Level 1", code="L1", parent_uid="10"),
        WBSNode(uid="12", name="Interiors", code="INT"),
        WBSNode(uid="13", name="Deep", code="DEEP", parent_uid="11"),
    ]
    p.activities = [Activity(uid="100", activity_id="A1000", name="In L1",
                             wbs_uid="11", calendar_uid="1")]
    p.build_lookups()
    return p


def _parent_of(p, name):
    by_uid = {w.uid: w for w in p.wbs_nodes}
    node = next(w for w in p.wbs_nodes if w.name == name)
    return by_uid[node.parent_uid].name if node.parent_uid in by_uid else None


def test_move_wbs_reparents_and_keeps_contents():
    from engine.edit_engine import apply_command
    p = _wbs_project()
    ok, _ = apply_command(p, {"action": "move_wbs", "wbs_name": "Level 1",
                              "parent_name": "Interiors"})
    assert ok
    assert _parent_of(p, "Level 1") == "Interiors"
    # activities stay in the folder, and nested folders travel with it
    assert p.get_activity(activity_id="A1000").wbs_uid == "11"
    assert _parent_of(p, "Deep") == "Level 1"


def test_move_wbs_to_root():
    from engine.edit_engine import apply_command
    p = _wbs_project()
    ok, _ = apply_command(p, {"action": "move_wbs", "wbs_name": "Level 1",
                              "to_root": True})
    assert ok and _parent_of(p, "Level 1") is None


def test_move_wbs_rejects_moving_into_own_descendant():
    from engine.edit_engine import apply_command
    p = _wbs_project()
    ok, msg = apply_command(p, {"action": "move_wbs", "wbs_name": "Structure",
                                "parent_name": "Deep"})
    assert not ok and "underneath" in msg
    assert _parent_of(p, "Structure") is None      # unchanged


def test_move_wbs_rejects_moving_into_itself():
    from engine.edit_engine import apply_command
    p = _wbs_project()
    ok, msg = apply_command(p, {"action": "move_wbs", "wbs_name": "Structure",
                                "parent_name": "Structure"})
    assert not ok and "itself" in msg


# ── WBS hierarchy on import ──────────────────────────────────────────────────

_NESTED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6/V23.12/API/BusinessObjects">
  <Project>
    <ObjectId>4510</ObjectId><Id>NEST</Id><Name>Nested</Name>
    <WBSObjectId>1000</WBSObjectId>
    {WBS}
    <Activity><ObjectId>5001</ObjectId><Id>A1000</Id><Name>In Sub A</Name>
      <WBSObjectId>1002</WBSObjectId><PlannedDuration>40</PlannedDuration></Activity>
  </Project>
</APIBusinessObjects>"""

_WBS_BLOCKS = """
    <WBS><ObjectId>1001</ObjectId><Code>P1</Code><Name>Phase 1</Name>
      <ParentObjectId>1000</ParentObjectId><SequenceNumber>1</SequenceNumber></WBS>
    <WBS><ObjectId>1002</ObjectId><Code>P1A</Code><Name>Sub A</Name>
      <ParentObjectId>1001</ParentObjectId><SequenceNumber>1</SequenceNumber></WBS>
"""


def _parents(project):
    by = {w.uid: w for w in project.wbs_nodes}
    return {w.name: (by[w.parent_uid].name if w.parent_uid in by else None)
            for w in project.wbs_nodes}


def _load(tmp_path, xml, name):
    from engine.xml_reader import load_xml
    p = os.path.join(str(tmp_path), name)
    open(p, "w").write(xml)
    return load_xml(p)


def test_wbs_nested_inside_project_keeps_hierarchy(tmp_path):
    proj = _load(tmp_path, _NESTED_XML.replace("{WBS}", _WBS_BLOCKS), "a.xml")
    assert _parents(proj)["Sub A"] == "Phase 1"


def test_wbs_emitted_at_root_level_is_still_read(tmp_path):
    """Some P6 exports put <WBS> beside <Project>, not inside it."""
    xml = _NESTED_XML.replace("{WBS}", "")
    xml = xml.replace("  <Project>", _WBS_BLOCKS + "  <Project>")
    proj = _load(tmp_path, xml, "b.xml")
    names = {w.name for w in proj.wbs_nodes}
    assert {"Phase 1", "Sub A"} <= names, "root-level WBS blocks were dropped"
    assert _parents(proj)["Sub A"] == "Phase 1"


def test_top_level_wbs_attaches_to_project_root(tmp_path):
    """A nil ParentObjectId means 'directly under the project'."""
    proj = _load(tmp_path, _NESTED_XML.replace("{WBS}", _WBS_BLOCKS), "c.xml")
    assert _parents(proj)["Phase 1"] == "Nested"


def test_wbs_hierarchy_survives_export_reimport(tmp_path):
    from engine.xml_writer import write_p6_xml
    from engine.xml_reader import load_xml
    proj = _load(tmp_path, _NESTED_XML.replace("{WBS}", _WBS_BLOCKS), "d.xml")
    out = os.path.join(str(tmp_path), "rt.xml")
    write_p6_xml(proj, out)
    again = load_xml(out)
    p = _parents(again)
    assert p["Sub A"] == "Phase 1"
    assert p["Phase 1"] == "Nested"
