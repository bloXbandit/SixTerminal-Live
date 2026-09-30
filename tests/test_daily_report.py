"""
test_daily_report.py — progressing a schedule from what the foremen wrote down.

The foremen already write down what they did and how many people did it, every
day, in their own words. That is the same vocabulary the activities are named
in, because the same people named both — so the notes can be matched to
activities and the schedule progressed from them.

Two things these tests are really about.

The data has to be cleaned first. These reports arrive with duplicate
submissions, ten-times-too-large hour counts and test rows in them; on the job
this was built for, 59% of one project's reported hours were in three bad rows.
Progressing a schedule from uncleaned reports books that error into the labour.

And a wrong match is worse than no match. An activity marked complete stops
asking to be done, so a line that fits many activities has to come back
unmatched rather than be given to whichever scored highest. Seventeen
activities on this job are named exactly "Install High Steel".
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl
import pytest

from engine import daily_report as dr
from engine.schedule_model import Activity, Calendar, Project, WBSNode

COLS = ["Report Name", "Timestamp", "Report Date", "Day", "Foreman", "Project",
        "Shift", "Number of Workers", "Total Labor Hours", "Work Status",
        "Tasks"]


def _book(rows):
    """rows: (name, date, foreman, workers, hours, status, tasks)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Daily Reports"
    ws.append(COLS)
    for name, date, foreman, workers, hours, status, tasks in rows:
        ws.append([name, f"{date} 09:00:00", f"{date} 12:00:00", "Monday",
                   foreman, "MDC 1", "Day Shift", workers, hours, status, tasks])
    fh = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    fh.close()
    wb.save(fh.name)
    return fh.name


def _read(rows, **kw):
    path = _book(rows)
    try:
        return dr.read(path, project="MDC 1", **kw)
    finally:
        os.unlink(path)


DONE = "Work Completed as Planned"
PART = "Work Partially Completed"


# ── cleaning, which is not optional ──────────────────────────────────────────

def test_a_resubmitted_report_is_not_counted_twice():
    """
    The tool appends "(2)" to a resubmission and keeps both. On the real sheet
    the pair was 65 seconds apart, same foreman, same 38 workers, same 3,800
    hours — and both were in the total.
    """
    r = _read([("MDC 1-T - 2026-09-21", "2026-09-21", "T", 38, 380, PART, "conduit"),
               ("MDC 1-T - 2026-09-21 (2)", "2026-09-21", "T", 38, 380, PART, "conduit")])
    assert r["counts"]["kept"] == 1
    assert r["counts"]["hours_clean"] == 380


def test_an_identical_report_is_not_counted_twice_even_without_the_marker():
    r = _read([("a", "2026-09-21", "T", 10, 100, PART, "x"),
               ("b", "2026-09-21", "T", 10, 100, PART, "x")])
    assert r["counts"]["kept"] == 1


def test_a_hundred_hours_per_worker_in_one_day_is_read_as_a_slip():
    """
    16 workers and 1,600 hours is 100 hours each. Twelve-hour days and weekend
    doubles are normal here; a hundred is a keying slip, and three rows like it
    carried 59% of one project's reported total.
    """
    r = _read([("a", "2026-09-18", "T", 16, 1600, PART, "conduit")])
    assert r["counts"]["hours_clean"] == 160
    assert r["counts"]["hours_reported"] == 1600
    assert "per worker" in r["removed"][0]["why"]


def test_a_long_shift_is_not_mistaken_for_a_slip():
    """Twelve hours a man is a Saturday, not an error."""
    r = _read([("a", "2026-09-18", "T", 10, 120, DONE, "conduit")])
    assert r["counts"]["hours_clean"] == 120
    assert r["removed"] == []


def test_a_row_with_no_workers_and_no_hours_is_dropped():
    r = _read([("a", "2026-09-17", "K", 0, 0, DONE, "TEST")])
    assert r["counts"]["kept"] == 0
    assert "test row" in r["removed"][0]["why"]


def test_what_was_removed_is_reported_not_swallowed():
    """A schedule progressed from uncleaned reports books their errors into the
    labour, and nothing downstream says so."""
    r = _read([("a", "2026-09-18", "T", 16, 1600, PART, "x"),
               ("b", "2026-09-17", "K", 0, 0, DONE, "TEST")])
    assert len(r["removed"]) == 2
    assert all(x["why"] for x in r["removed"])


def test_another_project_is_left_out():
    path = _book([("a", "2026-09-18", "T", 10, 100, DONE, "x")])
    try:
        assert dr.read(path, project="MDC 2")["counts"]["kept"] == 0
    finally:
        os.unlink(path)


def test_a_sheet_without_the_columns_says_so_rather_than_guessing():
    wb = openpyxl.Workbook()
    wb.active.append(["Date", "Who", "Notes"])
    fh = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    fh.close()
    wb.save(fh.name)
    try:
        with pytest.raises(ValueError, match="no .*column"):
            dr.read(fh.name)
    finally:
        os.unlink(fh.name)


# ── matching, and refusing to ────────────────────────────────────────────────

def _job():
    """
    A schedule with the collision that matters: the same work name in several
    rooms, plus one out on the roof.
    """
    p = Project(uid="1", name="J", id="J-1", data_date="2026-08-27",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [
        WBSNode(uid="root", name="J", code="J"),
        WBSNode(uid="er1", name="ER 201", code="ER201", parent_uid="root"),
        WBSNode(uid="er2", name="ER 202", code="ER202", parent_uid="root"),
        WBSNode(uid="roof", name="Roof", code="ROOF", parent_uid="root"),
    ]
    rows = [("A1", "Install High Steel", "er1"),
            ("A2", "Install High Steel", "er2"),
            ("A3", "OH Ladder Tray Install", "er1"),
            ("A4", "Roof Detailing", "roof")]
    p.activities = []
    for i, (aid, name, folder) in enumerate(rows, start=1):
        p.activities.append(Activity(
            uid=f"u{i}", activity_id=aid, name=name, wbs_uid=folder,
            calendar_uid="1", status="Not Started", planned_duration=40,
            remaining_duration=40, planned_start="2026-09-14",
            planned_finish="2026-09-25"))
    p.relations = []
    p.build_lookups()
    return p


def _reports(*lines, date="2026-09-18", status=PART):
    return {"reports": [{"date": dt.date.fromisoformat(date), "foreman": "T",
                         "project": "MDC 1", "workers": 10, "hours": 100,
                         "status": status, "lines": list(lines)}],
            "removed": [], "counts": {}}


def test_a_note_that_fits_two_rooms_equally_is_not_given_to_either():
    """
    The failure that matters. Seventeen activities on the real job are named
    exactly "Install High Steel"; a note that does not say which room is not
    evidence about any one of them, and an activity wrongly marked complete
    stops asking to be done.

    Refused either because the words are too common to identify anything or
    because several activities tie — which guard catches it depends on how rare
    the words are in that particular schedule, and both are the right answer.
    """
    m = dr.match(_job(), _reports("hanging high steel"))
    assert m["counts"]["matched"] == 0
    why = m["unmatched"][0]["why"]
    assert ("fit about as well" in why) or ("too weak" in why), why


def test_the_room_in_the_note_picks_the_room():
    m = dr.match(_job(), _reports("Install high steel in ER 202"))
    assert m["counts"]["matched"] == 1
    assert m["matched"][0]["activity_id"] == "A2"


def test_work_at_the_roof_does_not_land_in_a_room():
    """
    The bug two rounds of this took to fix. "Run Ladder Tray at Middle Roof"
    scored 12.6 against a generator room on its work words alone and beat the
    roof activity at 9.4, because a missing location cost nothing. Work in the
    roof is not work in Gen 326, however well the verbs line up.
    """
    m = dr.match(_job(), _reports("Run Ladder tray at Middle roof"))
    assert m["counts"]["matched"] == 1
    assert m["matched"][0]["activity_id"] == "A4", \
        f"landed on {m['matched'][0]['activity_id']}"


def test_a_note_about_work_scheduled_next_summer_is_not_evidence_about_it():
    p = _job()
    for a in p.activities:
        a.planned_start, a.planned_finish = "2027-06-01", "2027-06-30"
    p.build_lookups()
    m = dr.match(p, _reports("Install high steel in ER 202"))
    assert m["counts"]["matched"] == 0


def test_a_line_with_no_identifying_words_is_reported():
    m = dr.match(_job(), _reports("housekeeping"))
    assert m["counts"]["matched"] == 0


# ── progressing, once something has been confirmed ───────────────────────────

def test_a_completed_note_completes_the_activity():
    p = _job()
    m = dr.match(p, _reports("Install high steel in ER 202", status=DONE))
    dr.progress(p, m, apply=True, data_date="2026-09-28")
    a = p.get_activity(activity_id="A2")
    assert a.status == "Completed"
    assert a.percent_complete == 100.0
    assert a.remaining_duration == 0.0
    assert a.actual_finish == "2026-09-18"
    assert p.data_date == "2026-09-28"


def test_a_partial_note_puts_it_in_progress_with_work_left():
    p = _job()
    m = dr.match(p, _reports("Install high steel in ER 202", status=PART))
    dr.progress(p, m, apply=True)
    a = p.get_activity(activity_id="A2")
    assert a.status == "In Progress"
    assert 0 < a.percent_complete < 100
    assert a.remaining_duration > 0


def test_planning_changes_nothing():
    p = _job()
    m = dr.match(p, _reports("Install high steel in ER 202", status=DONE))
    dr.progress(p, m, apply=False)
    assert p.get_activity(activity_id="A2").status == "Not Started"


def test_an_activity_already_complete_is_left_alone():
    p = _job()
    a = p.get_activity(activity_id="A2")
    a.status, a.percent_complete = "Completed", 100.0
    a.actual_finish = "2026-08-01"
    p.build_lookups()
    m = dr.match(p, _reports("Install high steel in ER 202", status=PART))
    r = dr.progress(p, m, apply=True)
    assert a.actual_finish == "2026-08-01"
    assert all(c.get("skipped") or c["activity_id"] != "A2"
               for c in r["changes"])


def test_a_note_does_not_drag_a_start_later_than_planned():
    """The report says work happened, not that the plan was wrong."""
    p = _job()
    m = dr.match(p, _reports("Install high steel in ER 202", status=PART))
    dr.progress(p, m, apply=True)
    a = p.get_activity(activity_id="A2")
    assert a.actual_start == "2026-09-14"
