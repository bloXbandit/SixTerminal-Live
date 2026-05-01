"""
xml_writer.py — Serialize a Project object to valid Primavera P6 XML.

P6 XML schema reference: Primavera P6 EPPM XML Import/Export Guide.
Output is compatible with P6 Professional and P6 EPPM File → Import → Primavera PM (XML).

Key design decisions:
  - UIDs are preserved from the source file when editing existing projects.
  - For new projects / new activities, UIDs are generated as sequential integers.
  - Durations are stored in hours in the internal model; P6 XML also uses hours.
  - Dates are stored as ISO strings (YYYY-MM-DD); P6 XML uses YYYY-MM-DDThh:mm:ss.
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Optional
from datetime import datetime
from .schedule_model import Project, Activity, WBSNode, Relation, Calendar


def _dt(date_str: Optional[str]) -> Optional[str]:
    """Convert ISO date (YYYY-MM-DD) to P6 XML datetime (YYYY-MM-DDT00:00:00)."""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return d.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _sub(parent: ET.Element, tag: str, text: Optional[str] = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _activity_type_to_p6(activity_type: str) -> str:
    mapping = {
        "Task Dependent": "TaskDependent",
        "Resource Dependent": "ResourceDependent",
        "Level of Effort": "LevelOfEffort",
        "WBS Summary": "WBSSummary",
        "Start Milestone": "StartMilestone",
        "Finish Milestone": "FinishMilestone",
    }
    return mapping.get(activity_type, "TaskDependent")


def _relation_type_to_p6(rel_type: str) -> str:
    mapping = {
        "Finish to Start": "FinishStart",
        "Start to Start": "StartStart",
        "Finish to Finish": "FinishFinish",
        "Start to Finish": "StartFinish",
    }
    return mapping.get(rel_type, "FinishStart")


def _status_to_p6(status: str) -> str:
    mapping = {
        "Not Started": "Not Started",
        "In Progress": "In Progress",
        "Completed": "Completed",
    }
    return mapping.get(status, "Not Started")


def write_p6_xml(project: Project, output_path: str) -> str:
    """
    Serialize a Project object to a P6 XML file.
    Returns the output path.
    """
    root = ET.Element("APIBusinessObjects")

    # --- Project ---
    proj_el = _sub(root, "Project")
    _sub(proj_el, "ObjectId", project.uid or "1")
    _sub(proj_el, "Id", project.id or project.name[:20])
    _sub(proj_el, "Name", project.name)
    if project.data_date:
        _sub(proj_el, "DataDate", _dt(project.data_date))
    if project.planned_start:
        _sub(proj_el, "PlannedStartDate", _dt(project.planned_start))
    if project.must_finish_by:
        _sub(proj_el, "MustFinishByDate", _dt(project.must_finish_by))
    _sub(proj_el, "Status", project.status_code or "Active")

    # --- Calendars ---
    for cal in project.calendars:
        cal_el = _sub(root, "Calendar")
        _sub(cal_el, "ObjectId", cal.uid)
        _sub(cal_el, "Name", cal.name)
        _sub(cal_el, "HoursPerDay", str(cal.hours_per_day))
        _sub(cal_el, "HoursPerWeek", str(cal.hours_per_week))
        _sub(cal_el, "HoursPerMonth", str(cal.hours_per_month))
        _sub(cal_el, "HoursPerYear", str(cal.hours_per_year))
        _sub(cal_el, "Type", cal.type)
        _sub(cal_el, "ProjectObjectId", project.uid or "1")

    # --- WBS ---
    for wbs in project.wbs_nodes:
        wbs_el = _sub(root, "WBS")
        _sub(wbs_el, "ObjectId", wbs.uid)
        _sub(wbs_el, "Code", wbs.code)
        _sub(wbs_el, "Name", wbs.name)
        _sub(wbs_el, "ProjectObjectId", project.uid or "1")
        if wbs.parent_uid:
            _sub(wbs_el, "ParentObjectId", wbs.parent_uid)
        _sub(wbs_el, "SequenceNumber", str(wbs.sequence_num))

    # --- Activities ---
    for act in project.activities:
        act_el = _sub(root, "Activity")
        _sub(act_el, "ObjectId", act.uid)
        _sub(act_el, "Id", act.activity_id)
        _sub(act_el, "Name", act.name)
        _sub(act_el, "ProjectObjectId", project.uid or "1")
        _sub(act_el, "WBSObjectId", act.wbs_uid)
        _sub(act_el, "CalendarObjectId", act.calendar_uid)
        _sub(act_el, "Type", _activity_type_to_p6(act.activity_type))
        _sub(act_el, "Status", _status_to_p6(act.status))
        _sub(act_el, "PlannedDuration", str(act.planned_duration))
        _sub(act_el, "RemainingDuration", str(act.remaining_duration))
        _sub(act_el, "ActualDuration", str(act.actual_duration))
        _sub(act_el, "PercentComplete", str(act.percent_complete))
        if act.planned_start:
            _sub(act_el, "PlannedStartDate", _dt(act.planned_start))
        if act.planned_finish:
            _sub(act_el, "PlannedFinishDate", _dt(act.planned_finish))
        if act.actual_start:
            _sub(act_el, "ActualStartDate", _dt(act.actual_start))
        if act.actual_finish:
            _sub(act_el, "ActualFinishDate", _dt(act.actual_finish))
        if act.early_start:
            _sub(act_el, "StartDate", _dt(act.early_start))
        if act.early_finish:
            _sub(act_el, "FinishDate", _dt(act.early_finish))
        if act.total_float is not None:
            _sub(act_el, "TotalFloat", str(act.total_float))
        if act.free_float is not None:
            _sub(act_el, "FreeFloat", str(act.free_float))
        _sub(act_el, "Critical", "true" if act.is_critical else "false")
        if act.constraint_type:
            _sub(act_el, "PrimaryConstraintType", act.constraint_type)
        if act.constraint_date:
            _sub(act_el, "PrimaryConstraintDate", _dt(act.constraint_date))
        if act.notes:
            _sub(act_el, "NotebookTopic", act.notes)

    # --- Relations ---
    for rel in project.relations:
        rel_el = _sub(root, "Relationship")
        _sub(rel_el, "ObjectId", rel.uid)
        _sub(rel_el, "PredecessorActivityObjectId", rel.predecessor_uid)
        _sub(rel_el, "SuccessorActivityObjectId", rel.successor_uid)
        _sub(rel_el, "Type", _relation_type_to_p6(rel.type))
        _sub(rel_el, "Lag", str(rel.lag))
        _sub(rel_el, "ProjectObjectId", project.uid or "1")

    # Pretty-print
    raw = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    # Remove the extra XML declaration minidom adds (we add our own)
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    output = '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    return output_path
