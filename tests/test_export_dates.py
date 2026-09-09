"""
test_export_dates.py — dates P6 physically cannot store.

P6 keeps dates in SQL Server `datetime`, whose range starts at 1753-01-01 —
not the 0001-01-01 that `datetime2` and Python's own date allow. Send one
below that floor and the import fails as a BATCH with:

  XMLImporterException: executeUpdateBatch: The conversion of a datetime2 data
  type to a datetime data type resulted in an out-of-range value.

which names no activity, no field and no value. One bad row takes the entire
schedule with it, after the import has run, and leaves nothing to go on but
bisecting a few thousand rows by hand.

The writer used to do `d[:10] + "T08:00:00"` on whatever was in the field, so
anything at all went out. Two things now stand in the way: the emission
refuses a date P6 cannot take, and date_problems() names them all first.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.xml_writer import (_SQL_DATETIME_MIN, _dt_finish, _dt_start,
                               date_problems)
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _job(**bad):
    p = Project(uid="p", name="M", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="w", name="A", code="A")]
    p.activities, p.relations = [], []
    a = Activity(uid="A1", activity_id="A1", name="Work", wbs_uid="w",
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40,
                 remaining_duration=40, planned_start="2026-02-02",
                 planned_finish="2026-02-06")
    for k, v in bad.items():
        setattr(a, k, v)
    p.activities.append(a)
    p.build_lookups()
    return p


# ── the emission refuses what P6 cannot take ─────────────────────────────────

@pytest.mark.parametrize("value", [
    "0001-01-01",     # Python's date.min
    "0026-02-02",     # a two-digit year that lost its century
    "1752-12-31",     # one day below the floor
    "2026-13-45",     # not a real date at all
])
def test_a_date_p6_cannot_store_is_not_emitted(value):
    assert _dt_start(value) is None
    assert _dt_finish(value) is None


def test_the_floor_itself_is_allowed():
    """1753-01-01 is valid; refusing it would be an off-by-one that loses a
    legitimate date."""
    assert _dt_start(_SQL_DATETIME_MIN) == "1753-01-01T08:00:00"


def test_an_ordinary_date_is_unaffected():
    assert _dt_start("2026-02-02") == "2026-02-02T08:00:00"
    assert _dt_finish("2026-02-02") == "2026-02-02T17:00:00"


def test_an_empty_date_stays_empty():
    assert _dt_start(None) is None and _dt_start("") is None


def test_a_datetime_string_still_works():
    """Dates arrive as both plain dates and ISO datetimes."""
    assert _dt_start("2026-02-02T00:00:00") == "2026-02-02T08:00:00"


# ── the check names them before the export leaves ────────────────────────────

def test_an_out_of_range_date_is_reported_with_the_activity_and_field():
    p = _job(planned_start="0001-01-01")
    probs = date_problems(p)
    assert len(probs) == 1
    assert probs[0]["activity_id"] == "A1"
    assert probs[0]["field"] == "planned_start"
    assert "1753-01-01" in probs[0]["why"]


def test_a_malformed_date_is_reported_as_such():
    probs = date_problems(_job(constraint_date="2026-13-45"))
    assert probs and probs[0]["why"] == "not a real date"


def test_every_date_field_is_checked_not_just_the_planned_ones():
    p = _job(actual_finish="0026-02-02", late_finish="1752-12-31",
             constraint_date="0001-01-01")
    fields = {r["field"] for r in date_problems(p)}
    assert fields == {"actual_finish", "late_finish", "constraint_date"}


def test_a_clean_schedule_reports_nothing():
    assert date_problems(_job()) == []


def test_a_bad_project_level_date_is_caught_too():
    """The data date is written as well, and fails the import the same way."""
    p = _job()
    p.data_date = "0001-01-01"
    probs = date_problems(p)
    assert any(r["field"] == "data_date" for r in probs)


def test_the_report_carries_the_offending_value():
    """Without it the user still has to go looking for what to change."""
    probs = date_problems(_job(planned_start="0026-02-02"))
    assert probs[0]["value"] == "0026-02-02"


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_check_endpoint_reports_the_problems():
    import server
    p = _job(planned_start="0001-01-01")
    server._projects.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = p
    server._active_id[0] = "J"
    d = server.app.test_client().get("/api/export/check").get_json()
    assert d["success"] and d["count"] == 1
    assert d["problems"][0]["activity_id"] == "A1"


def test_the_check_endpoint_is_quiet_on_a_clean_schedule():
    import server
    server._projects.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = _job()
    server._active_id[0] = "J"
    d = server.app.test_client().get("/api/export/check").get_json()
    assert d["count"] == 0 and d["problems"] == []


def test_a_written_export_omits_the_bad_date_rather_than_shipping_it():
    """The whole point: the file that comes out has to be importable."""
    import tempfile
    from engine.xml_writer import write_p6_xml
    p = _job(planned_start="0001-01-01")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        path = f.name
    write_p6_xml(p, path)
    xml = open(path, encoding="utf-8").read()
    os.unlink(path)
    assert "0001-01-01" not in xml
