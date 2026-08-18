"""
xml_reader.py — Parse a Primavera P6 XML file into the internal schedule model.

P6 XML schema: APIBusinessObjects root element containing Project, WBS,
Activity, Relationship, and Calendar child elements.

This reader is namespace-safe and reads the actual P6 nesting pattern:
root-level enterprise objects plus WBS/Activity/Relationship blocks inside Project.
"""

import xml.etree.ElementTree as ET
from typing import Optional, Dict, Iterable, List
from .schedule_model import Project, WBSNode, Activity, Relation, Calendar, UDFType
from .xml_writer import normalize_udf_type

_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_XSI_NIL = f"{{{_XSI_NS}}}nil"


def _strip_ns(tag: str) -> str:
    """Strip XML namespace prefix from a tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _is_nil(el: Optional[ET.Element]) -> bool:
    """Return True when an element is explicitly marked xsi:nil='true'."""
    if el is None:
        return True
    value = el.attrib.get(_XSI_NIL)
    if value is None:
        # Defensive fallback for parsers/files that preserve a prefixed attribute key.
        value = el.attrib.get("xsi:nil") or el.attrib.get("nil")
    return str(value).lower() == "true"


def _children(parent: Optional[ET.Element], tag: Optional[str] = None) -> Iterable[ET.Element]:
    """Iterate direct children, namespace-agnostic."""
    if parent is None:
        return []
    if tag is None:
        return list(parent)
    return [child for child in parent if _strip_ns(child.tag) == tag]


def _descendants(parent: Optional[ET.Element], tag: str) -> Iterable[ET.Element]:
    """Iterate all descendants, namespace-agnostic."""
    if parent is None:
        return []
    return [el for el in parent.iter() if _strip_ns(el.tag) == tag]


def _child(el: Optional[ET.Element], tag: str) -> Optional[ET.Element]:
    """Find one direct child by local tag name, ignoring XML namespaces."""
    if el is None:
        return None
    for child in el:
        if _strip_ns(child.tag) == tag:
            return child
    return None


def _text(el: Optional[ET.Element], tag: str, default: str = "") -> str:
    """Get child text by local tag name, namespace-safe and xsi:nil-aware."""
    child = _child(el, tag)
    if child is None or _is_nil(child) or child.text is None:
        return default
    return child.text.strip()


def _has_real_value(el: Optional[ET.Element], tag: str) -> bool:
    """True when the tag is present, not nil, and not empty text."""
    child = _child(el, tag)
    return child is not None and not _is_nil(child) and (child.text or "").strip() != ""


def _float(el: Optional[ET.Element], tag: str, default: float = 0.0) -> float:
    try:
        raw = _text(el, tag, str(default))
        if raw == "":
            return default
        return float(raw)
    except (ValueError, TypeError):
        return default


def _int(el: Optional[ET.Element], tag: str, default: int = 0) -> int:
    try:
        return int(float(_text(el, tag, str(default))))
    except (ValueError, TypeError):
        return default


def _iso_date(val: Optional[str]) -> Optional[str]:
    """Convert P6 XML datetime (YYYY-MM-DDThh:mm:ss) to ISO date (YYYY-MM-DD)."""
    if not val or not str(val).strip():
        return None
    val = str(val).strip()
    return val[:10] if len(val) >= 10 else val


def _norm(value: Optional[str]) -> str:
    return " ".join((value or "").replace("_", " ").replace("-", " ").split()).lower()


def _map_activity_type(raw: str) -> str:
    mapping = {
        "taskdependent": "Task Dependent",
        "task dependent": "Task Dependent",
        "resourcedependent": "Resource Dependent",
        "resource dependent": "Resource Dependent",
        "levelofeffort": "Level of Effort",
        "level of effort": "Level of Effort",
        "wbssummary": "WBS Summary",
        "wbs summary": "WBS Summary",
        "startmilestone": "Start Milestone",
        "start milestone": "Start Milestone",
        "finishmilestone": "Finish Milestone",
        "finish milestone": "Finish Milestone",
    }
    key = _norm(raw).replace(" ", "")
    spaced_key = _norm(raw)
    return mapping.get(spaced_key) or mapping.get(key) or "Task Dependent"


def _map_status(raw: str) -> str:
    mapping = {
        "notstarted": "Not Started",
        "not started": "Not Started",
        "inprogress": "In Progress",
        "in progress": "In Progress",
        "completed": "Completed",
        "complete": "Completed",
    }
    key = _norm(raw).replace(" ", "")
    spaced_key = _norm(raw)
    return mapping.get(spaced_key) or mapping.get(key) or "Not Started"


def _map_relation_type(raw: str) -> str:
    mapping = {
        "finishstart": "Finish to Start",
        "finish to start": "Finish to Start",
        "fs": "Finish to Start",
        "startstart": "Start to Start",
        "start to start": "Start to Start",
        "ss": "Start to Start",
        "finishfinish": "Finish to Finish",
        "finish to finish": "Finish to Finish",
        "ff": "Finish to Finish",
        "startfinish": "Start to Finish",
        "start to finish": "Start to Finish",
        "sf": "Start to Finish",
    }
    key = _norm(raw).replace(" ", "")
    spaced_key = _norm(raw)
    return mapping.get(spaced_key) or mapping.get(key) or "Finish to Start"


def _unique_by_uid(items: Iterable[ET.Element]) -> List[ET.Element]:
    seen = set()
    out: List[ET.Element] = []
    for el in items:
        uid = _text(el, "ObjectId")
        if not uid:
            # Keep no-uid elements rather than silently dropping them.
            out.append(el)
            continue
        if uid in seen:
            continue
        seen.add(uid)
        out.append(el)
    return out


def _read_udfs(el, udf_titles):
    """
    Per-activity UDF values, keyed by the field's title.

    P6 writes the value in one of several typed elements depending on the
    field's data type, so take whichever one is present rather than assuming.

    The real elements — confirmed against Oracle's own generated schema
    classes — are named for the VALUE (TextValue, IntegerValue, …), not the
    type. This reader and the writer both used the un-suffixed names
    (Text, Integer, …) until now, which is why re-loading this app's own
    exports always worked: read and write agreed with each other, just not
    with P6. The old names are kept as a second try so a file already
    exported by this tool keeps loading; a P6-authored file only ever uses
    the real ones.
    """
    out = {}
    for u in _descendants(el, "UDF"):
        oid = _text(u, "TypeObjectId")
        title = udf_titles.get(oid)
        if not title:
            continue
        for tag in ("TextValue", "IntegerValue", "DoubleValue", "CostValue",
                    "IndicatorValue", "StartDateValue", "FinishDateValue",
                    # legacy: this tool's own pre-fix exports
                    "Text", "Double", "Integer", "StartDate", "FinishDate",
                    "CodeValue", "Indicator"):
            v = _text(u, tag)
            if v not in (None, ""):
                out[title] = v
                break
    return out


def load_xml(path: str) -> Project:
    """
    Parse a P6 XML file and return a Project object.
    Raises ValueError if the file cannot be parsed or contains no Project.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML file: {e}") from e

    root = tree.getroot()

    # P6 XML uses a default namespace, so a raw .find('Project') will miss it.
    proj_el = next(iter(_descendants(root, "Project")), None)
    if proj_el is None:
        raise ValueError("No <Project> element found in P6 XML file.")

    proj_uid = _text(proj_el, "ObjectId") or _text(proj_el, "Id") or "1"

    # User-defined fields. P6 puts the definitions at the document root and
    # references them by ObjectId on each activity, so the titles have to be
    # collected before the activities are read. Without this every UDF — the
    # electrician counts, crew sizes, anything a team added — was silently
    # dropped on import and could never come back out on export.
    udf_titles = {}
    udf_defs = []
    for u in _descendants(root, "UDFType"):
        oid = _text(u, "ObjectId")
        title = _text(u, "Title")
        if not oid or not title:
            continue
        udf_titles[oid] = title
        # A file can arrive carrying P6's internal type codes (FT_TEXT and
        # friends) rather than XML DataTypes — from an XER round trip, the API,
        # or a tool that passed them through. Normalising on the way IN means
        # the model only ever holds a type P6 will accept, so nothing
        # downstream has to remember to translate it.
        udf_defs.append(UDFType(
            uid=oid, title=title,
            subject_area=_text(u, "SubjectArea") or "Activity",
            data_type=normalize_udf_type(_text(u, "DataType"))))

    project = Project(
        uid=proj_uid,
        name=_text(proj_el, "Name") or _text(proj_el, "Id", "Unknown"),
        id=_text(proj_el, "Id") or _text(proj_el, "Name", "")[:20],
        data_date=_iso_date(_text(proj_el, "DataDate")),
        planned_start=_iso_date(_text(proj_el, "PlannedStartDate")),
        must_finish_by=_iso_date(_text(proj_el, "MustFinishByDate")),
        status_code=_text(proj_el, "Status", "Active"),
    )
    project.udf_types = udf_defs

    # --- Calendars ---
    # Native P6 XML has root-level global calendars and nested project calendars.
    # Read both, keeping unique ObjectIds.
    calendar_elements = list(_children(root, "Calendar")) + list(_children(proj_el, "Calendar"))
    for cal_el in _unique_by_uid(calendar_elements):
        uid = _text(cal_el, "ObjectId")
        if not uid:
            continue
        project.calendars.append(Calendar(
            uid=uid,
            name=_text(cal_el, "Name") or f"Calendar {uid}",
            hours_per_day=_float(cal_el, "HoursPerDay", 8.0),
            hours_per_week=_float(cal_el, "HoursPerWeek", 40.0),
            hours_per_month=_float(cal_el, "HoursPerMonth", 172.0),
            hours_per_year=_float(cal_el, "HoursPerYear", 2000.0),
            type=_text(cal_el, "Type", "Global"),
        ))

    if not project.calendars:
        project.calendars.append(Calendar(uid="1", name="Standard"))

    # --- WBS ---
    # Most P6 exports nest <WBS> inside <Project>, but some emit them at the
    # root alongside <Project>. Reading only one location silently dropped every
    # folder, which then reappeared as flat "Imported WBS <id>" placeholders for
    # whichever ones activities happened to reference — losing the hierarchy.
    wbs_ids = set()
    _wbs_elements = list(_children(proj_el, "WBS")) + list(_children(root, "WBS"))
    for wbs_el in _unique_by_uid(_wbs_elements):
        uid = _text(wbs_el, "ObjectId")
        if not uid:
            continue
        parent_uid = _text(wbs_el, "ParentObjectId") or None
        project.wbs_nodes.append(WBSNode(
            uid=uid,
            name=_text(wbs_el, "Name") or _text(wbs_el, "Code") or uid,
            code=_text(wbs_el, "Code") or _text(wbs_el, "Name", "")[:20],
            parent_uid=parent_uid,
            sequence_num=_int(wbs_el, "SequenceNumber", 0),
        ))
        wbs_ids.add(uid)

    # If activities point to a hidden Project.WBSObjectId that is not emitted as a WBS block,
    # add a placeholder so the app has a valid root reference.
    hidden_project_wbs_uid = _text(proj_el, "WBSObjectId")
    if hidden_project_wbs_uid and hidden_project_wbs_uid not in wbs_ids:
        project.wbs_nodes.insert(0, WBSNode(
            uid=hidden_project_wbs_uid,
            name=project.name,
            code=project.id,
            parent_uid=None,
            sequence_num=0,
        ))
        wbs_ids.add(hidden_project_wbs_uid)

    # Top-level folders that carry no ParentObjectId belong to the project's
    # root WBS. Without this they come back as siblings of the project node
    # instead of sitting under it, so one level of hierarchy is lost on every
    # export -> re-import round trip.
    if hidden_project_wbs_uid:
        for w in project.wbs_nodes:
            if w.uid != hidden_project_wbs_uid and not w.parent_uid:
                w.parent_uid = hidden_project_wbs_uid

    # --- Activities ---
    default_calendar_uid = project.calendars[0].uid if project.calendars else "1"
    for act_el in _children(proj_el, "Activity"):
        uid = _text(act_el, "ObjectId")
        if not uid:
            continue

        early_start = (
            _iso_date(_text(act_el, "StartDate"))
            or _iso_date(_text(act_el, "RemainingEarlyStartDate"))
            or _iso_date(_text(act_el, "PlannedStartDate"))
        )
        early_finish = (
            _iso_date(_text(act_el, "FinishDate"))
            or _iso_date(_text(act_el, "RemainingEarlyFinishDate"))
            or _iso_date(_text(act_el, "PlannedFinishDate"))
        )
        late_start = (
            _iso_date(_text(act_el, "RemainingLateStartDate"))
            or _iso_date(_text(act_el, "LateStartDate"))
        )
        late_finish = (
            _iso_date(_text(act_el, "RemainingLateFinishDate"))
            or _iso_date(_text(act_el, "LateFinishDate"))
        )

        critical_text = _text(act_el, "Critical", "false").lower()
        is_critical = critical_text in ("true", "1", "yes")

        project.activities.append(Activity(
            uid=uid,
            activity_id=_text(act_el, "Id") or uid,
            name=_text(act_el, "Name") or _text(act_el, "Id") or uid,
            wbs_uid=_text(act_el, "WBSObjectId") or hidden_project_wbs_uid,
            calendar_uid=_text(act_el, "CalendarObjectId") or default_calendar_uid,
            activity_type=_map_activity_type(_text(act_el, "Type", "Task Dependent")),
            status=_map_status(_text(act_el, "Status", "Not Started")),
            planned_duration=_float(act_el, "PlannedDuration"),
            remaining_duration=_float(act_el, "RemainingDuration"),
            actual_duration=_float(act_el, "ActualDuration"),
            # Prefer Physical % Complete (a PM's actual assessment) over the
            # duration-based PercentComplete — but `x or y` treats an explicit
            # 0 the same as "absent", which silently discarded the exact case
            # this was meant to catch: work stalled at 0% physical despite
            # duration already ticking forward. Check presence, not truthiness.
            percent_complete=(_float(act_el, "PhysicalPercentComplete")
                              if _has_real_value(act_el, "PhysicalPercentComplete")
                              else _float(act_el, "PercentComplete")),
            planned_start=_iso_date(_text(act_el, "PlannedStartDate")),
            planned_finish=_iso_date(_text(act_el, "PlannedFinishDate")),
            actual_start=_iso_date(_text(act_el, "ActualStartDate")),
            actual_finish=_iso_date(_text(act_el, "ActualFinishDate")),
            early_start=early_start,
            early_finish=early_finish,
            late_start=late_start,
            late_finish=late_finish,
            total_float=_float(act_el, "TotalFloat"),
            free_float=_float(act_el, "FreeFloat"),
            is_critical=is_critical,
            constraint_type=_text(act_el, "PrimaryConstraintType") or None,
            constraint_date=_iso_date(_text(act_el, "PrimaryConstraintDate")) or None,
            notes=_text(act_el, "NotebookTopic") or _text(act_el, "NotesToResources") or None,
            planned_labor_units=_float(act_el, "PlannedLaborUnits"),
            udfs=_read_udfs(act_el, udf_titles),
        ))

    # Add placeholder WBS nodes for any activity WBS reference not emitted/read.
    existing_wbs = {w.uid for w in project.wbs_nodes}
    missing_wbs = sorted({a.wbs_uid for a in project.activities if a.wbs_uid and a.wbs_uid not in existing_wbs})
    for idx, wbs_uid in enumerate(missing_wbs, start=1):
        project.wbs_nodes.append(WBSNode(
            uid=wbs_uid,
            name=f"Imported WBS {wbs_uid}",
            code=wbs_uid,
            parent_uid=hidden_project_wbs_uid if hidden_project_wbs_uid in existing_wbs else None,
            sequence_num=10_000 + idx,
        ))

    # --- Relationships ---
    activity_ids = {a.uid for a in project.activities}
    for rel_el in _children(proj_el, "Relationship"):
        pred_uid = _text(rel_el, "PredecessorActivityObjectId")
        succ_uid = _text(rel_el, "SuccessorActivityObjectId")
        if not pred_uid or not succ_uid:
            continue
        # Preserve only logic that points to activities present in this XML project.
        if pred_uid not in activity_ids or succ_uid not in activity_ids:
            continue
        project.relations.append(Relation(
            uid=_text(rel_el, "ObjectId") or f"REL-{len(project.relations) + 1}",
            predecessor_uid=pred_uid,
            successor_uid=succ_uid,
            type=_map_relation_type(_text(rel_el, "Type", "Finish to Start")),
            lag=_float(rel_el, "Lag"),
        ))

    project.build_lookups()
    return project
