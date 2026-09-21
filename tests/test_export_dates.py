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


def test_percent_complete_comes_off_p6_xml_as_0_to_100():
    """
    P6 XML carries a FRACTION (1.0 is complete); XER carries 0-100, and
    set_progress, actualize and the writer all assume 0-100. Passing the
    fraction through raw made the scale depend on which format a project
    arrived in — the same schedule read two ways disagreed by 100x.
    """
    from engine.xml_reader import _pct_0_100
    assert _pct_0_100(1.0) == 100.0
    assert _pct_0_100(0.5) == 50.0
    assert _pct_0_100(0) == 0.0
    assert _pct_0_100(None) == 0.0
    # a file that already wrote 0-100 is taken at face value, not sent to 8000%
    assert _pct_0_100(80) == 80.0
    assert _pct_0_100("nonsense") == 0.0


def test_percent_survives_a_round_trip_at_the_same_scale(tmp_path):
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from engine.schedule_model import (Activity, Calendar, Project, WBSNode)
    from engine.xml_reader import load_xml
    from engine.xml_writer import write_p6_xml

    p = Project(uid="1", name="J", id="J", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="A", code="A")]
    p.activities = [
        Activity(uid="a1", activity_id="A10", name="Done", wbs_uid="w",
                 calendar_uid="1", status="Completed", percent_complete=100.0,
                 actual_start="2026-01-02", actual_finish="2026-01-03",
                 planned_duration=8),
        Activity(uid="a2", activity_id="A20", name="Half", wbs_uid="w",
                 calendar_uid="1", status="In Progress", percent_complete=50.0,
                 actual_start="2026-01-02", planned_duration=8),
    ]
    p.relations = []
    p.build_lookups()
    out = str(tmp_path / "rt.xml")
    write_p6_xml(p, out)
    back = load_xml(out)
    got = {a.activity_id: round(float(a.percent_complete or 0), 1)
           for a in back.activities}
    assert got["A10"] == 100.0
    assert got["A20"] == 50.0, f"a half-done activity came back as {got['A20']}"


# ── P6 refuses a whole import over the resource pool alone ───────────────────

def _res_job():
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from engine.schedule_model import (Activity, Calendar, Project, Resource,
                                       ResourceAssignment, WBSNode)
    p = Project(uid="1", name="J", id="J", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="A", code="A")]
    p.activities = [Activity(uid="u1", activity_id="A10", name="Pull Wire",
                             wbs_uid="w", calendar_uid="1", planned_duration=40,
                             planned_start="2026-02-02",
                             planned_finish="2026-02-06",
                             remaining_labor_units=80)]
    p.resources = [Resource(uid="r1", name="Electricians", id="ELEC")]
    p.resource_assignments = [ResourceAssignment(
        uid="ra1", activity_uid="u1", resource_uid="r1", remaining_units=80)]
    p.relations = []
    p.build_lookups()
    return p


def test_resources_can_be_left_out_of_the_export(tmp_path):
    """
    Resources are ENTERPRISE-GLOBAL in P6, so importing one is a create against
    the shared pool. A login without that privilege gets the WHOLE import
    refused — "You do not have create privileges on object Resource" — and the
    privilege has to come from an administrator. The schedule is fine, so this
    ships it without them rather than waiting on someone else.
    """
    import re

    from engine.xml_writer import write_p6_xml

    out = str(tmp_path / "r.xml")
    write_p6_xml(_res_job(), out, include_resources=True)
    raw = open(out).read()
    assert re.search(r"<Resource>", raw), "the normal export lost its resources"

    write_p6_xml(_res_job(), out, include_resources=False)
    raw = open(out).read()
    assert not re.search(r"<Resource>", raw)


def test_dropping_resources_drops_their_assignments_too(tmp_path):
    """An assignment naming a resource the file does not carry is a dangling
    reference, and P6 does not fail on one — it logs it and leaves the field
    empty, which is the silent version of the same problem."""
    import re

    from engine.xml_audit import audit
    from engine.xml_writer import write_p6_xml

    out = str(tmp_path / "r.xml")
    write_p6_xml(_res_job(), out, include_resources=False)
    raw = open(out).read()
    assert not re.search(r"<ResourceAssignment>", raw)
    assert audit(raw)["ok"], audit(raw)


def test_the_schedule_itself_is_unaffected_by_dropping_resources(tmp_path):
    """Dates, logic and the WBS are the point of the file."""
    from engine.xml_reader import load_xml
    from engine.xml_writer import write_p6_xml

    a = str(tmp_path / "with.xml")
    b = str(tmp_path / "without.xml")
    write_p6_xml(_res_job(), a, include_resources=True)
    write_p6_xml(_res_job(), b, include_resources=False)
    pa, pb = load_xml(a), load_xml(b)
    key = lambda p: sorted((x.activity_id, str(x.planned_start)[:10],
                            str(x.planned_finish)[:10], x.wbs_uid is not None)
                           for x in p.activities)
    assert key(pa) == key(pb)
    assert len(pa.wbs_nodes) == len(pb.wbs_nodes)


def test_the_endpoint_exposes_it():
    import server
    from engine.schedule_model import Project
    server._projects.clear()
    server._projects["J"] = server._make_session("J", "t.xml")
    server._projects["J"]["project"] = _res_job()
    server._active_id[0] = "J"
    c = server.app.test_client()
    body = c.get("/api/download?resources=0").data.decode("utf-8", "replace")
    assert "<Resource>" not in body
    body = c.get("/api/download").data.decode("utf-8", "replace")
    assert "<Resource>" in body, "it now omits resources by default"


def test_a_resource_keeps_the_guid_it_arrived_with(tmp_path):
    """
    P6 identifies a resource on import by its GUID. Minting a fresh one each
    export made the SAME resource look new every time, so P6 tried to CREATE it
    against the enterprise-global pool instead of matching the one already
    sitting there — and a login without create-resource privilege had the whole
    import refused, dates and logic and all.

    This is why an import could fail on a resource the user could see in their
    own P6: the pool had it, the file did not present it as the same one.
    """
    import re

    from engine.xml_reader import load_xml
    from engine.xml_writer import write_p6_xml

    p = _res_job()
    p.resources[0].guid = "{11111111-2222-3333-4444-555555555555}"
    out = str(tmp_path / "r.xml")

    seen = set()
    for _ in range(3):
        write_p6_xml(p, out)
        raw = open(out).read()
        seen.add(re.search(r"<Resource>.*?<GUID>([^<]+)</GUID>", raw, re.S).group(1))
    assert seen == {"{11111111-2222-3333-4444-555555555555}"}, \
        f"the guid changed between exports: {seen}"

    assert load_xml(out).resources[0].guid == p.resources[0].guid


def test_a_resource_this_app_invented_still_gets_a_guid(tmp_path):
    """Only a resource with no guid of its own is given one."""
    import re

    from engine.xml_writer import write_p6_xml

    p = _res_job()
    p.resources[0].guid = None
    out = str(tmp_path / "r.xml")
    write_p6_xml(p, out)
    got = re.search(r"<Resource>.*?<GUID>([^<]+)</GUID>", open(out).read(), re.S)
    assert got and len(got.group(1)) > 30


def test_a_resource_never_writes_an_empty_mandatory_reference(tmp_path):
    """
    CalendarObjectId and CurrencyObjectId are both MANDATORY on a Resource.
    Emptying them does not soften a warning, it fails the import outright, one
    field at a time:

      SEVERE: Field CurrencyObjectId may not be set to null.   (element 1336)
      SEVERE: Field CalendarObjectId may not be set to null.   (element 1336)

    They were nil-ed on a misreading of

      Unresolved reference null on Resource.
          CurrencyObjectId = 1

    as a complaint about the values. It is not. The same import reports
    "Currency 'USD' (1) matched by 1 from xml", and the calendar only ever
    draws a WARNING — "Calendar 'G5-DAY NO HOLIDAY' (6590) is not created as
    security privilege is not assigned" — which P6 logs and carries on from.

    A pointer P6 ignores is survivable. An empty one is not.
    """
    import re

    from engine.xml_writer import write_p6_xml

    out = str(tmp_path / "r.xml")
    write_p6_xml(_res_job(), out)
    block = re.search(r"<Resource>.*?</Resource>", open(out).read(), re.S).group(0)
    for tag in ("CalendarObjectId", "CurrencyObjectId"):
        opening = re.search(rf"<{tag}[^>]*>", block)
        assert opening and "nil" not in opening.group(0), \
            f"{tag} is nil — P6 refuses the whole import"
        got = re.search(rf"<{tag}[^>]*>([^<]*)</{tag}>", block)
        assert got and got.group(1).strip(), f"{tag} is empty"


def test_a_resource_keeps_a_calendar_the_file_really_carries(tmp_path):
    """Empty is for "we do not know", not for discarding what we do."""
    import re

    from engine.xml_writer import write_p6_xml

    p = _res_job()
    p.resources[0].calendar_uid = p.calendars[0].uid
    out = str(tmp_path / "r.xml")
    write_p6_xml(p, out)
    block = re.search(r"<Resource>.*?</Resource>", open(out).read(), re.S).group(0)
    got = re.search(r"<CalendarObjectId[^>]*>([^<]*)</CalendarObjectId>", block)
    assert got and got.group(1).strip(), "a real calendar was thrown away"
