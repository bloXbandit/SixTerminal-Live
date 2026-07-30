"""
test_xml_reader_pct.py — Physical % Complete must win over duration-based %,
even when Physical is explicitly zero.

The whole point of preferring PhysicalPercentComplete is to catch work that's
stalled in reality despite duration ticking forward on paper. `x or y` in
Python treats an explicit 0 the same as "absent", which silently discarded
exactly that case — the fix must check tag presence, not truthiness.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import Project, Activity, WBSNode, Calendar
from engine.xml_writer import write_p6_xml
from engine.xml_reader import load_xml


def _base_xml(tmp_path):
    p = Project(uid="1", name="T", id="T", data_date="2026-01-01")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="w", name="W", code="W")]
    p.activities = [
        Activity(uid="1", activity_id="A1000", name="Stalled work", wbs_uid="w",
                 calendar_uid="1", planned_duration=80.0, remaining_duration=80.0,
                 percent_complete=0.0, status="In Progress", actual_start="2026-01-01"),
    ]
    p.build_lookups()
    out = os.path.join(str(tmp_path), "base.xml")
    write_p6_xml(p, out)
    return open(out).read()


def test_explicit_zero_physical_beats_nonzero_duration_percent(tmp_path):
    """The exact case the feature was built for: PM assessed 0% physical
    progress despite the schedule showing 50% by duration."""
    xml = _base_xml(tmp_path)
    xml = xml.replace("<PercentComplete>0</PercentComplete>",
                      "<PercentComplete>50</PercentComplete>", 1)
    out = os.path.join(str(tmp_path), "divergent.xml")
    open(out, "w").write(xml)
    p = load_xml(out)
    assert p.get_activity(activity_id="A1000").percent_complete == 0.0


def test_falls_back_to_percent_complete_when_physical_tag_is_absent(tmp_path):
    """Older exports may not carry PhysicalPercentComplete at all."""
    import re
    xml = _base_xml(tmp_path)
    xml = re.sub(r"<PhysicalPercentComplete>[\d.]+</PhysicalPercentComplete>\s*",
                "", xml, count=1)
    xml = xml.replace("<PercentComplete>0</PercentComplete>",
                      "<PercentComplete>35</PercentComplete>", 1)
    out = os.path.join(str(tmp_path), "absent.xml")
    open(out, "w").write(xml)
    p = load_xml(out)
    assert p.get_activity(activity_id="A1000").percent_complete == 35.0


def test_falls_back_to_percent_complete_when_physical_is_nil(tmp_path):
    xml = _base_xml(tmp_path)
    xml = xml.replace("<PercentComplete>0</PercentComplete>",
                      "<PercentComplete>40</PercentComplete>")
    xml = xml.replace(
        "<PhysicalPercentComplete>0</PhysicalPercentComplete>",
        '<PhysicalPercentComplete xsi:nil="true"></PhysicalPercentComplete>')
    out = os.path.join(str(tmp_path), "nil.xml")
    open(out, "w").write(xml)
    p = load_xml(out)
    assert p.get_activity(activity_id="A1000").percent_complete == 40.0


def test_matching_values_are_unaffected(tmp_path):
    xml = _base_xml(tmp_path)   # both 0 by default
    out = os.path.join(str(tmp_path), "match.xml")
    open(out, "w").write(xml)
    p = load_xml(out)
    assert p.get_activity(activity_id="A1000").percent_complete == 0.0
