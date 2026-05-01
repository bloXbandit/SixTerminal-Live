"""
xml_reader.py — Parse a Primavera P6 XML file into the internal schedule model.

P6 XML schema: APIBusinessObjects root element containing Project, WBS,
Activity, Relationship, and Calendar child elements.

This is the mirror of xer_reader.py — both produce the same Project object
so the edit engine and xml_writer work identically regardless of input format.
"""

import xml.etree.ElementTree as ET
from typing import Optional, List, Dict
from .schedule_model import Project, WBSNode, Activity, Relation, Calendar


def _text(el: Optional[ET.Element], tag: str, default: str = "") -> str:
    """Get text of a child element, or default if missing."""
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _float(el: Optional[ET.Element], tag: str, default: float = 0.0) -> float:
    try:
        return float(_text(el, tag, str(default)))
    except (ValueError, TypeError):
        return default


def _iso_date(val: str) -> Optional[str]:
    """Convert P6 XML datetime (YYYY-MM-DDThh:mm:ss) to ISO date (YYYY-MM-DD)."""
    if not val or not val.strip():
        return None
    val = val.strip()
    return val[:10] if len(val) >= 10 else val


def _strip_ns(tag: str) -> str:
    """Strip XML namespace prefix from a tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _iter_elements(root: ET.Element, tag: str):
    """Iterate all elements with a given tag, stripping namespaces."""
    for el in root:
        if _strip_ns(el.tag) == tag:
            yield el


def load_xml(path: str) -> Project:
    """
    Parse a P6 XML file and return a Project object.
    Raises ValueError if the file cannot be parsed.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML file: {e}")

    root = tree.getroot()

    # P6 XML root is <APIBusinessObjects> — find the Project element
    proj_el = None
    for el in _iter_elements(root, "Project"):
        proj_el = el
        break

    if proj_el is None:
        raise ValueError("No <Project> element found in P6 XML file.")

    proj_uid = _text(proj_el, "ObjectId") or _text(proj_el, "Id") or "1"

    project = Project(
        uid=proj_uid,
        name=_text(proj_el, "Name") or _text(proj_el, "Id", "Unknown"),
        id=_text(proj_el, "Id") or _text(proj_el, "Name", "")[:20],
        data_date=_iso_date(_text(proj_el, "DataDate")),
        planned_start=_iso_date(_text(proj_el, "PlannedStartDate")),
        must_finish_by=_iso_date(_text(proj_el, "MustFinishByDate")),
        status_code=_text(proj_el, "Status", "Active"),
    )

    # --- Calendars ---
    for cal_el in _iter_elements(root, "Calendar"):
        project.calendars.append(Calendar(
            uid=_text(cal_el, "ObjectId"),
            name=_text(cal_el, "Name"),
            hours_per_day=_float(cal_el, "HoursPerDay", 8.0),
            hours_per_week=_float(cal_el, "HoursPerWeek", 40.0),
            hours_per_month=_float(cal_el, "HoursPerMonth", 176.0),
            hours_per_year=_float(cal_el, "HoursPerYear", 2080.0),
            type=_text(cal_el, "Type", "Global"),
        ))

    # Default calendar if none found
    if not project.calendars:
        project.calendars.append(Calendar(uid="1", name="Standard"))

    # --- WBS ---
    for wbs_el in _iter_elements(root, "WBS"):
        parent_uid = _text(wbs_el, "ParentObjectId") or None
        project.wbs_nodes.append(WBSNode(
            uid=_text(wbs_el, "ObjectId"),
            name=_text(wbs_el, "Name"),
            code=_text(wbs_el, "Code") or _text(wbs_el, "Name", "")[:20],
            parent_uid=parent_uid,
            sequence_num=int(_float(wbs_el, "SequenceNumber", 0)),
        ))

    # --- Activities ---
    type_map = {
        "TaskDependent": "Task Dependent",
        "ResourceDependent": "Resource Dependent",
        "LevelOfEffort": "Level of Effort",
        "WBSSummary": "WBS Summary",
        "StartMilestone": "Start Milestone",
        "FinishMilestone": "Finish Milestone",
    }
    status_map = {
        "Not Started": "Not Started",
        "In Progress": "In Progress",
        "Completed": "Completed",
    }

    for act_el in _iter_elements(root, "Activity"):
        tf_raw = _float(act_el, "TotalFloat")
        ff_raw = _float(act_el, "FreeFloat")
        critical_text = _text(act_el, "Critical", "false").lower()
        is_critical = critical_text in ("true", "1", "yes")

        # Dates — P6 XML uses StartDate/FinishDate for early dates
        early_start = _iso_date(_text(act_el, "StartDate")) or _iso_date(_text(act_el, "PlannedStartDate"))
        early_finish = _iso_date(_text(act_el, "FinishDate")) or _iso_date(_text(act_el, "PlannedFinishDate"))

        project.activities.append(Activity(
            uid=_text(act_el, "ObjectId"),
            activity_id=_text(act_el, "Id"),
            name=_text(act_el, "Name"),
            wbs_uid=_text(act_el, "WBSObjectId"),
            calendar_uid=_text(act_el, "CalendarObjectId") or (project.calendars[0].uid if project.calendars else "1"),
            activity_type=type_map.get(_text(act_el, "Type", "TaskDependent"), "Task Dependent"),
            status=status_map.get(_text(act_el, "Status", "Not Started"), "Not Started"),
            planned_duration=_float(act_el, "PlannedDuration"),
            remaining_duration=_float(act_el, "RemainingDuration"),
            actual_duration=_float(act_el, "ActualDuration"),
            percent_complete=_float(act_el, "PercentComplete"),
            planned_start=_iso_date(_text(act_el, "PlannedStartDate")),
            planned_finish=_iso_date(_text(act_el, "PlannedFinishDate")),
            actual_start=_iso_date(_text(act_el, "ActualStartDate")),
            actual_finish=_iso_date(_text(act_el, "ActualFinishDate")),
            early_start=early_start,
            early_finish=early_finish,
            late_start=_iso_date(_text(act_el, "LateStartDate")),
            late_finish=_iso_date(_text(act_el, "LateFinishDate")),
            total_float=tf_raw,
            free_float=ff_raw,
            is_critical=is_critical,
            constraint_type=_text(act_el, "PrimaryConstraintType") or None,
            constraint_date=_iso_date(_text(act_el, "PrimaryConstraintDate")) or None,
            notes=_text(act_el, "NotebookTopic") or None,
        ))

    # --- Relations ---
    type_map_rel = {
        "FinishStart": "Finish to Start",
        "StartStart": "Start to Start",
        "FinishFinish": "Finish to Finish",
        "StartFinish": "Start to Finish",
    }

    for rel_el in _iter_elements(root, "Relationship"):
        project.relations.append(Relation(
            uid=_text(rel_el, "ObjectId"),
            predecessor_uid=_text(rel_el, "PredecessorActivityObjectId"),
            successor_uid=_text(rel_el, "SuccessorActivityObjectId"),
            type=type_map_rel.get(_text(rel_el, "Type", "FinishStart"), "Finish to Start"),
            lag=_float(rel_el, "Lag"),
        ))

    project.build_lookups()
    return project
