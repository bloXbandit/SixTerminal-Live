"""
test_xml_reader_shapes.py — the shapes a P6 export actually arrives in.

Both bugs here were silent. Neither raised, neither warned, and both produced
a Project that looked plausible: right name, right id, and the work missing.
A file that fails to load is a bug someone reports in a minute; a file that
loads wrong is one that gets exported back to P6 and believed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile

from engine.xml_reader import load_xml

NS = ('xmlns="http://xmlns.oracle.com/Primavera/P6/V21.12/API/BusinessObjects"')


def _load(body):
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(f'<?xml version="1.0" encoding="UTF-8" ?>\n'
                 f'<APIBusinessObjects {NS}>{body}</APIBusinessObjects>')
        path = fh.name
    try:
        return load_xml(path)
    finally:
        os.unlink(path)


_REAL = """
  <Project ObjectId="2908">
    <Id>25-1539-INT</Id><Name>MDC-1-Internal</Name>
    <DataDate>2025-11-01T00:00:00</DataDate>
    <WBS><ObjectId>10</ObjectId><Code>A</Code><Name>Area</Name></WBS>
    <Activity>
      <ObjectId>100</ObjectId><Id>A1010</Id><Name>Pull wire</Name>
      <WBSObjectId>10</WBSObjectId><Status>Not Started</Status>
      <PlannedDuration>40</PlannedDuration>
    </Activity>
    <ResourceAssignment>
      <ObjectId>900</ObjectId><ActivityObjectId>100</ActivityObjectId>
      <ResourceObjectId>4147</ResourceObjectId>
      <PlannedUnits>320</PlannedUnits><ActualUnits>0</ActualUnits>
      <RemainingUnits>320</RemainingUnits>
    </ResourceAssignment>
  </Project>"""


def test_the_projectlist_stub_is_not_mistaken_for_the_project():
    """
    A P6 export opens with a <ProjectList> header holding a STUB — Id, Name,
    TemplateId and nothing else — before the real project further down.

    Taking the first <Project> read the stub: a 23 MB file carrying 2,421
    activities and 4,188 assignments loaded as a schedule with NO activities,
    no error and no warning. The name and id even came out right, which is
    what made it convincing.
    """
    p = _load("""
      <ProjectList>
        <Project ObjectId="2908"><Id>25-1539-INT</Id>
          <Name>MDC-1-Internal</Name><TemplateId>1</TemplateId></Project>
      </ProjectList>""" + _REAL)
    assert len(p.activities) == 1, "read the ProjectList stub, not the project"
    assert p.data_date is not None
    assert p.activities[0].activity_id == "A1010"


def test_a_project_with_no_projectlist_still_loads():
    p = _load(_REAL)
    assert len(p.activities) == 1


def test_assignments_survive_a_file_that_declares_no_resource():
    """
    Assignments pointed at a resource the enterprise pool already holds, with
    no <Resource> block, is the supported shape — it is how a login without
    create-privilege can import at all, and it is what THIS APP exports.

    Dropping them meant 4,188 assignments loaded as none, and meant the app
    could not read back its own export: save, re-upload, every hour gone.
    """
    p = _load(_REAL)
    assert len(p.resource_assignments) == 1, "the assignment was dropped"
    assert p.resource_assignments[0].planned_units == 320


def test_the_absent_resource_keeps_its_p6_objectid():
    """So the export can point at the same resource again."""
    p = _load(_REAL)
    assert [r.uid for r in p.resources] == ["4147"]


def test_a_round_trip_through_our_own_export_keeps_the_hours():
    """The regression that matters: export assignments-only, read it back."""
    from engine.xml_writer import write_p6_xml
    p = _load(_REAL)
    with tempfile.NamedTemporaryFile(suffix=".xml") as tmp:
        write_p6_xml(p, tmp.name, include_resources="4147")
        back = load_xml(tmp.name)
    assert len(back.resource_assignments) == 1, "hours lost on the round trip"
    assert back.resource_assignments[0].planned_units == 320
    assert back.resource_assignments[0].resource_uid == "4147"
