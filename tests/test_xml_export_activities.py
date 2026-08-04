"""
test_xml_export_activities.py — Values in the exported XML must be in the
units P6's importer expects.

P6 displays percent complete as 0-100 but carries it in XML as a FRACTION,
where 1.0 means 100%. Exporting 100 made the importer throw

    SEVERE: XMLImporterException: Unable to invoke setPhysicalPercentComplete
    on business object Activity. Percent value 100.0 is out of range.
    The maximum allowed value is 1.0, which represents 100%.

That is fatal rather than a warning: it aborts the activity import for the
whole file, so the project loads with its WBS folders and no activities.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.xml_writer import _pct_text

# Every percent-bearing element P6 range-checks on import.
_PCT_TAGS = ("PercentComplete", "PhysicalPercentComplete", "DurationPercentComplete",
             "UnitsPercentComplete", "NonLaborUnitsPercentComplete",
             "ScopePercentComplete")


def _client():
    return server.app.test_client()


def _mixed_status_project(c):
    """One completed activity, one in progress, one not started."""
    block = ("MDC1.MIL.1000\tCompleted milestone\t1\t02-Jul-25 A\t02-Jul-25 A\n"
             "MDC1.FDG.1670\tIn progress work\t77\t25-Aug-25 A\t12-Dec-25\n"
             "MDC1.PMT.1250\tNot started\t45\t12-Dec-25\t18-Feb-26")
    r = c.post("/api/import/paste", json={"text": block, "project_name": "25-1539-INT"})
    c.post("/api/import/commit", json={"contract": r.get_json()["contract"],
                                       "mode": "replace", "project_name": "25-1539-INT"})


def _export(c):
    return c.get("/api/download?p6_version=21.12").data.decode()


def test_percent_helper_converts_to_a_fraction():
    assert _pct_text(0) == "0"
    assert _pct_text(100) == "1"
    assert _pct_text(50) == "0.5"
    assert _pct_text(99.5) == "0.995"
    # junk and out-of-range input must still land inside 0..1
    for junk in (None, "", "abc", -5, 150):
        assert 0.0 <= float(_pct_text(junk)) <= 1.0


def test_no_percent_value_exceeds_one():
    """The exact condition P6 rejects. Anything above 1.0 aborts the import."""
    c = _client()
    _mixed_status_project(c)
    xml = _export(c)
    for tag in _PCT_TAGS:
        for raw in re.findall(r"<%s>([^<]*)</%s>" % (tag, tag), xml):
            if raw.strip():
                assert float(raw) <= 1.0, f"{tag}={raw} is out of P6's 0-1 range"


def test_a_completed_activity_exports_as_fully_complete():
    c = _client()
    _mixed_status_project(c)
    xml = _export(c)
    done = re.search(r"<Activity>((?:(?!</Activity>).)*?"
                     r"<Id>MDC1\.MIL\.1000</Id>.*?)</Activity>", xml, re.S).group(1)
    assert re.search(r"<Status>([^<]*)</Status>", done).group(1) == "Completed"
    # 100% expressed the way P6 wants it
    assert float(re.search(r"<PhysicalPercentComplete>([^<]*)<", done).group(1)) == 1.0
    assert float(re.search(r"<PercentComplete>([^<]*)<", done).group(1)) == 1.0


def test_a_not_started_activity_exports_as_zero():
    c = _client()
    _mixed_status_project(c)
    xml = _export(c)
    ns = re.search(r"<Activity>((?:(?!</Activity>).)*?"
                   r"<Id>MDC1\.PMT\.1250</Id>.*?)</Activity>", xml, re.S).group(1)
    assert float(re.search(r"<PhysicalPercentComplete>([^<]*)<", ns).group(1)) == 0.0


def test_every_activity_calendar_reference_is_actually_emitted():
    """P6 logs "Referenced business object Calendar ... cannot be found,
    ignoring field CalendarObjectId" and leaves the activity with no calendar.
    The 6-day calendar is only written on demand, but the name match that
    selects it fires on any name merely containing a "6"."""
    c = _client()
    _mixed_status_project(c)
    xml = _export(c)
    emitted = set(re.findall(r"<Calendar>.*?<ObjectId>(\d+)</ObjectId>", xml, re.S))
    refs = set(re.findall(r"<Activity>.*?<CalendarObjectId>(\d+)</CalendarObjectId>",
                          xml, re.S))
    assert refs, "no activity calendar references found"
    assert not (refs - emitted), \
        f"activities reference calendars never written: {sorted(refs - emitted)}"


def test_a_calendar_named_with_a_six_does_not_dangle():
    """Regression for the specific trap: '2026 Standard' contains a 6, which
    routed it to the on-demand 6-day calendar that was never emitted."""
    from engine.schedule_model import Calendar
    c = _client()
    _mixed_status_project(c)
    proj = server._get_session()["project"]
    proj.calendars = [Calendar(uid="1", name="2026 Standard")]
    for a in proj.activities:
        a.calendar_uid = "1"
    xml = _export(c)
    emitted = set(re.findall(r"<Calendar>.*?<ObjectId>(\d+)</ObjectId>", xml, re.S))
    refs = set(re.findall(r"<Activity>.*?<CalendarObjectId>(\d+)</CalendarObjectId>",
                          xml, re.S))
    assert not (refs - emitted), \
        f"'2026 Standard' pointed at an unwritten calendar: {sorted(refs - emitted)}"
