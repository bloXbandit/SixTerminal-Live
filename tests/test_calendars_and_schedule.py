"""
test_calendars_and_schedule.py — the calendar the file states is the calendar
the app schedules on.

The reader parsed HoursPerDay and then ignored <StandardWorkWeek> and
<HolidayOrException> entirely, so every imported calendar fell back to the
dataclass default: Monday-Friday, no holidays, whatever the file actually
said. A six-day job came back five-day, Saturdays were treated as weekends,
and a calendar named for its holiday set observed none of them. The CPM was
always right — it reads work_days, hours_per_day and holidays properly — so
the reader was the whole of the bug.

Also covered: the Schedule button's run log, which exists so "what did that
just do, and should I put it back" has an answer that is not memory; and the
folder flow tint staying live as logic changes, since a relationship edit
patches activity rows in place and would otherwise leave folder headers
showing the state from before the edit.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode, compute_dates)

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def _day(name, work=True, start="07:00:00", finish="17:59:00"):
    if not work:
        return (f'<StandardWorkHours><DayOfWeek>{name}</DayOfWeek>'
                f'<WorkTime xsi:nil="true"/></StandardWorkHours>')
    return (f'<StandardWorkHours><DayOfWeek>{name}</DayOfWeek>'
            f'<WorkTime><Start>{start}</Start><Finish>{finish}</Finish></WorkTime>'
            f'</StandardWorkHours>')


def _xml(work_days_xml, hpd="10", holidays_xml="", extra_acts="", rels=""):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6/V21.12/API/BusinessObjects"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <Calendar><Name>JOB CAL</Name><ObjectId>900</ObjectId><Type>Global</Type>
  <HoursPerDay>{hpd}</HoursPerDay>
  <StandardWorkWeek>{work_days_xml}</StandardWorkWeek>
  {holidays_xml}
 </Calendar>
 <Project><ObjectId>1</ObjectId><Id>CAL</Id><Name>Cal Test</Name>
  <DataDate>2026-01-05T00:00:00</DataDate>
  <PlannedStartDate>2026-01-05T00:00:00</PlannedStartDate>
  <WBS><ObjectId>10</ObjectId><Code>E</Code><Name>Elec</Name></WBS>
  <Activity><ObjectId>100</ObjectId><Id>A1000</Id><Name>Pull Wire</Name>
   <Type>Task Dependent</Type><Status>Not Started</Status>
   <CalendarObjectId>900</CalendarObjectId>
   <PlannedDuration>60</PlannedDuration><RemainingDuration>60</RemainingDuration>
   <WBSObjectId>10</WBSObjectId></Activity>
  {extra_acts}{rels}
 </Project></APIBusinessObjects>"""


SIX_DAY = "".join([_day("Sunday", False), _day("Monday"), _day("Tuesday"),
                   _day("Wednesday"), _day("Thursday"), _day("Friday"),
                   _day("Saturday")])
FIVE_DAY = "".join([_day("Sunday", False), _day("Monday"), _day("Tuesday"),
                    _day("Wednesday"), _day("Thursday"), _day("Friday"),
                    _day("Saturday", False)])


def _load(xml, tmp_path):
    from engine.xml_reader import load_xml
    p = tmp_path / "c.xml"
    p.write_text(xml, encoding="utf-8")
    return load_xml(str(p))


# ── the reader keeps what the file says ──────────────────────────────────────

def test_a_six_day_week_is_read_as_six_days(tmp_path):
    cal = _load(_xml(SIX_DAY), tmp_path).calendars[0]
    assert SAT in cal.work_days, "Saturday is a work day on this job"
    assert SUN not in cal.work_days, "Sunday is not"
    assert cal.work_days == frozenset({MON, TUE, WED, THU, FRI, SAT})


def test_a_five_day_week_is_still_read_as_five(tmp_path):
    cal = _load(_xml(FIVE_DAY), tmp_path).calendars[0]
    assert cal.work_days == frozenset({MON, TUE, WED, THU, FRI})


def test_hours_per_day_is_the_files_value_not_eight(tmp_path):
    assert _load(_xml(SIX_DAY, hpd="10"), tmp_path).calendars[0].hours_per_day == 10.0


def test_holidays_are_read(tmp_path):
    hol = ("<HolidayOrExceptions>"
           "<HolidayOrException><Date>2026-01-01T00:00:00</Date></HolidayOrException>"
           "<HolidayOrException><Date>2026-12-25T00:00:00</Date></HolidayOrException>"
           "</HolidayOrExceptions>")
    cal = _load(_xml(SIX_DAY, holidays_xml=hol), tmp_path).calendars[0]
    assert cal.holidays == frozenset({"2026-01-01", "2026-12-25"})


def test_an_exception_that_carries_work_time_is_not_a_holiday(tmp_path):
    """A Saturday brought IN is a working exception, not a day off. Treating
    it as a holiday would remove a day the crew is actually on site."""
    hol = ("<HolidayOrExceptions>"
           "<HolidayOrException><Date>2026-05-30T00:00:00</Date>"
           "<WorkTime><Start>07:00:00</Start><Finish>12:00:00</Finish></WorkTime>"
           "</HolidayOrException></HolidayOrExceptions>")
    cal = _load(_xml(SIX_DAY, holidays_xml=hol), tmp_path).calendars[0]
    assert "2026-05-30" not in cal.holidays


def test_a_calendar_with_no_workweek_block_keeps_the_default(tmp_path):
    """Nothing stated must not mean nothing works."""
    xml = _xml("", hpd="8").replace("<StandardWorkWeek></StandardWorkWeek>", "")
    cal = _load(xml, tmp_path).calendars[0]
    assert cal.work_days == frozenset({MON, TUE, WED, THU, FRI})


def test_an_all_nonworking_week_falls_back_rather_than_freezing_the_job(tmp_path):
    """A parse that finds no working day at all is a parse failure — taking it
    literally would make every date unreachable."""
    none_on = "".join(_day(d, False) for d in
                      ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                       "Friday", "Saturday"))
    cal = _load(_xml(none_on), tmp_path).calendars[0]
    assert cal.work_days == frozenset({MON, TUE, WED, THU, FRI})


# ── and the CPM actually schedules on it ─────────────────────────────────────

def _one_task(work_days, hpd, holidays=frozenset()):
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="C", hours_per_day=hpd,
                            work_days=work_days, holidays=holidays)]
    p.wbs_nodes = [WBSNode(uid="w", name="W", code="W")]
    p.activities = [Activity(uid="a1", activity_id="A", name="A", wbs_uid="w",
                             calendar_uid="1", activity_type="Task Dependent",
                             status="Not Started", planned_duration=6 * hpd,
                             remaining_duration=6 * hpd,
                             planned_start="2026-01-05",
                             planned_finish="2026-01-05")]
    p.relations = []
    p.build_lookups()
    return p


def test_six_working_days_lands_earlier_on_a_six_day_week():
    """Mon 5 Jan + 6 working days: five-day skips Sat AND Sun, six-day only Sun."""
    five = _one_task(frozenset({MON, TUE, WED, THU, FRI}), 8)
    six = _one_task(frozenset({MON, TUE, WED, THU, FRI, SAT}), 10)
    compute_dates(five, hold_unlinked_dates=False, apply_dates=True)
    compute_dates(six, hold_unlinked_dates=False, apply_dates=True)
    assert five.activities[0].planned_finish == "2026-01-12"   # Mon
    assert six.activities[0].planned_finish == "2026-01-10"    # Sat


def test_a_holiday_pushes_the_finish_out_by_a_day():
    plain = _one_task(frozenset({MON, TUE, WED, THU, FRI, SAT}), 10)
    holed = _one_task(frozenset({MON, TUE, WED, THU, FRI, SAT}), 10,
                      holidays=frozenset({"2026-01-07"}))
    compute_dates(plain, hold_unlinked_dates=False, apply_dates=True)
    compute_dates(holed, hold_unlinked_dates=False, apply_dates=True)
    assert holed.activities[0].planned_finish > plain.activities[0].planned_finish


def test_no_work_is_ever_scheduled_on_a_non_working_day():
    import datetime as d
    p = _one_task(frozenset({MON, TUE, WED, THU, FRI, SAT}), 10)
    compute_dates(p, hold_unlinked_dates=False, apply_dates=True)
    for a in p.activities:
        for field in (a.planned_start, a.planned_finish):
            assert d.date.fromisoformat(field).weekday() != SUN


# ── the Schedule button, its log, and putting it back ────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    server._projects.clear()
    server._brains.clear()
    server._active_id[0] = None
    yield


def _client_with(xml):
    c = server.app.test_client()
    c.post("/api/upload", data={"file": (io.BytesIO(xml.encode()), "c.xml")},
           content_type="multipart/form-data")
    return c


_TWO_TASKS = _xml(
    SIX_DAY,
    extra_acts=("<Activity><ObjectId>101</ObjectId><Id>A1010</Id><Name>Terminate</Name>"
                "<Type>Task Dependent</Type><Status>Not Started</Status>"
                "<CalendarObjectId>900</CalendarObjectId>"
                "<PlannedDuration>20</PlannedDuration><RemainingDuration>20</RemainingDuration>"
                "<WBSObjectId>10</WBSObjectId></Activity>"),
    rels=("<Relationship><ObjectId>1</ObjectId>"
          "<PredecessorActivityObjectId>100</PredecessorActivityObjectId>"
          "<SuccessorActivityObjectId>101</SuccessorActivityObjectId>"
          "<Type>Finish to Start</Type><Lag>0</Lag></Relationship>"))


def test_the_schedule_button_runs_and_honours_the_calendar():
    import datetime as d
    c = _client_with(_TWO_TASKS)
    r = c.post("/api/schedule/run", json={}).get_json()
    assert r["success"]
    p = server._projects[server._active_id[0]]["project"]
    for a in p.activities:
        assert d.date.fromisoformat(a.planned_start).weekday() != SUN


def test_every_schedule_run_is_logged():
    c = _client_with(_TWO_TASKS)
    c.post("/api/schedule/run", json={})
    c.post("/api/schedule/run", json={})
    log = c.get("/api/schedule/log").get_json()
    assert log["count"] == 2
    assert log["runs"][0]["at"] >= log["runs"][1]["at"], "most recent first"


def test_the_log_says_what_a_run_moved():
    c = _client_with(_TWO_TASKS)
    # lengthen a task so the next Schedule genuinely has to move something
    c.post("/api/direct", json={"commands": [
        {"action": "update_duration", "activity_id": "A1000",
         "new_duration_days": 20}], "label": "stretch"})
    c.post("/api/schedule/run", json={})
    run = c.get("/api/schedule/log").get_json()["runs"][0]
    assert run["moved"] >= 1, "a real reflow reported nothing moved"
    assert run["samples"], "the moved rows must be named, not just counted"
    s = run["samples"][0]
    # a reflow often moves only the FINISH (a duration change, a predecessor
    # slipping), so the sample has to carry both or it can look like a no-op
    assert (s["from"], s["from_finish"]) != (s["to"], s["to_finish"])


def test_schedule_can_be_reverted():
    c = _client_with(_TWO_TASKS)
    p = server._projects[server._active_id[0]]["project"]
    c.post("/api/direct", json={"commands": [
        {"action": "update_duration", "activity_id": "A1000",
         "new_duration_days": 20}], "label": "stretch"})
    before = {a.activity_id: a.planned_finish for a in p.activities}
    c.post("/api/schedule/run", json={})
    u = c.post("/api/undo", json={}).get_json()
    assert "Schedule" in (u.get("undone_label") or "")
    p = server._projects[server._active_id[0]]["project"]
    assert {a.activity_id: a.planned_finish for a in p.activities} == before


# ── the flow tint keeps up with logic changes ────────────────────────────────

_TWO_FOLDERS = """<?xml version="1.0" encoding="UTF-8"?>
<APIBusinessObjects xmlns="http://xmlns.oracle.com/Primavera/P6/V21.12/API/BusinessObjects">
 <Project><ObjectId>1</ObjectId><Id>F</Id><Name>Flow</Name>
  <DataDate>2026-01-05T00:00:00</DataDate>
  <WBS><ObjectId>10</ObjectId><Code>A</Code><Name>Area A</Name></WBS>
  <WBS><ObjectId>20</ObjectId><Code>B</Code><Name>Area B</Name></WBS>
  <Activity><ObjectId>100</ObjectId><Id>A1</Id><Name>Work A</Name>
   <Type>Task Dependent</Type><Status>Not Started</Status>
   <PlannedDuration>8</PlannedDuration><RemainingDuration>8</RemainingDuration>
   <WBSObjectId>10</WBSObjectId></Activity>
  <Activity><ObjectId>200</ObjectId><Id>B1</Id><Name>Work B</Name>
   <Type>Task Dependent</Type><Status>Not Started</Status>
   <PlannedDuration>8</PlannedDuration><RemainingDuration>8</RemainingDuration>
   <WBSObjectId>20</WBSObjectId></Activity>
 </Project></APIBusinessObjects>"""


def _flows(c):
    return {w["name"]: w.get("flow")
            for w in c.get("/api/schedule").get_json()["wbs_sections"]}


def test_adding_a_relationship_repaints_the_folder_tint_without_a_reload():
    """The header row is not among the activity rows patched in place, so
    without this the colour keeps showing the state from before the edit."""
    c = _client_with(_TWO_FOLDERS)
    assert _flows(c) == {"Area A": "isolated", "Area B": "isolated"}

    r = c.post("/api/direct", json={"commands": [
        {"action": "add_relation", "predecessor_id": "A1",
         "successor_id": "B1", "type": "fs"}], "label": "tie"}).get_json()

    assert r["structural"] is False, "this should still be an in-place patch"
    changed = {f["uid"]: f["flow"] for f in r["changed_folders"]}
    assert changed == {"10": "one_way", "20": "one_way"}
    assert _flows(c) == {"Area A": "one_way", "Area B": "one_way"}


def test_undoing_a_relationship_puts_the_tint_back():
    c = _client_with(_TWO_FOLDERS)
    c.post("/api/direct", json={"commands": [
        {"action": "add_relation", "predecessor_id": "A1",
         "successor_id": "B1", "type": "fs"}], "label": "tie"})
    c.post("/api/undo", json={})
    assert _flows(c) == {"Area A": "isolated", "Area B": "isolated"}


def test_an_edit_that_changes_no_folder_verdict_reports_none():
    """The payload must not carry every folder on every keystroke."""
    c = _client_with(_TWO_FOLDERS)
    r = c.post("/api/direct", json={"commands": [
        {"action": "rename_activity", "activity_id": "A1",
         "new_name": "Work A revised"}], "label": "rename"}).get_json()
    assert r["changed_folders"] == []
