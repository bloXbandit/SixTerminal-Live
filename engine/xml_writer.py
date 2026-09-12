"""
xml_writer.py — Serialize a Project object to valid Primavera P6 XML.

Built to exactly mirror the structure of a native P6 V23.12 XML export:

  1.  XML header + root namespace
  2.  DisplayCurrency
  3.  Currency
  4.  UDFType
  5.  OBS
  6.  Global Calendar(s) — Type=Global, ProjectObjectId xsi:nil
  7.  Resource
  8.  ResourceRate
  9.  FinancialPeriodTemplate
  10. Project block
        - OBSObjectId          → real (matches OBS above)
        - ParentEPSObjectId    → existing EPS ObjectId from the target P6 database
        - WBSObjectId          → hidden project root WBS ObjectId, not emitted as a WBS block
        - ActivityDefaultCalendarObjectId → global cal ObjectId
        - FinancialPeriodTemplateId       → template ObjectId
        - Project-scoped Calendar blocks  → Type=Project,
                                            ProjectObjectId=project uid,
                                            BaseCalendarObjectId=global cal
  12. WBS blocks — exported WBS only; hidden project root is not emitted
  13. Activity blocks — status-aware dates/durations
  14. Relationship blocks
"""

import uuid
import threading
from datetime import date as _date
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Optional, List, Any, Dict, Tuple
from .schedule_model import Project, Activity, WBSNode, Relation, Calendar

# ── Namespaces ─────────────────────────────────────────────────────────────────
# P6 imports XML written for its own schema version or an OLDER one, and
# refuses anything newer — a file declaring V23.12 simply does nothing in
# P6 21.12, with no error to explain it. So the version is selectable, and the
# default is old enough to load in the P6 releases still in the field while
# still carrying everything this writer emits.
P6_SCHEMA_VERSIONS = ["19.12", "20.12", "21.12", "22.12", "23.12"]
DEFAULT_P6_VERSION = "19.12"

_NS_TEMPLATE = "http://xmlns.oracle.com/Primavera/P6Professional/V{v}/API/BusinessObjects"
_P6_NS   = _NS_TEMPLATE.format(v=DEFAULT_P6_VERSION)
_XSI_NS  = "http://www.w3.org/2001/XMLSchema-instance"
_XSI_NIL = f"{{{_XSI_NS}}}nil"
_XSI_SL  = f"{{{_XSI_NS}}}schemaLocation"


def normalize_p6_version(version: Optional[str]) -> str:
    """Accept '21.12', 'V21.12', '21.12.9.45067' → '21.12'; else the default."""
    v = str(version or "").strip().lstrip("vV")
    parts = v.split(".")
    if len(parts) >= 2:
        v = f"{parts[0]}.{parts[1]}"
    return v if v in P6_SCHEMA_VERSIONS else DEFAULT_P6_VERSION


def p6_namespace(version: Optional[str] = None) -> str:
    return _NS_TEMPLATE.format(v=normalize_p6_version(version))


ET.register_namespace("",    _P6_NS)
ET.register_namespace("xsi", _XSI_NS)

# ── P6 reference ObjectIds from the clean native XML ───────────────────────────
# These mirror FA_MLCB_IPS_R1-1.xml. ParentEPSObjectId is referenced, not defined.
_CUR_OID = "1"       # USD currency
_OBS_OID = "540"     # Enterprise OBS used by Project and WBS
_EPS_OID = "3063"    # Existing EPS parent in target P6 database
_GCAL_5_NOHOL = "6590"
_GCAL_7_NOHOL = "6591"
_GCAL_5_HOL = "6592"
_GCAL_6_HOL = "6593"     # G6-DAY WITH HOLIDAY — emitted only when a project uses it
_GCAL_OID = _GCAL_5_NOHOL
_RES_OID = "6899"
_RRATE_OID = "7174"
_FPT_OID = "1"

# ── Safe generated ObjectId ranges ────────────────────────────────────────────
# P6 ObjectIds are database-style integers. Do not export random/large app IDs.
# These ranges mirror the clean native XML pattern and keep internal references stable.
_PROJECT_WBS_OID = "26058"   # hidden Project.WBSObjectId; native export does not emit this as <WBS>
_WBS_OID_START = 26059       # first explicit WBS block starts after hidden project WBS
_ACTIVITY_OID_START = 101923 # mirrors clean native XML range
_RELATIONSHIP_OID_START = 41150
_ASSIGNMENT_OID_START = 83399
# Real resources read off an import get their own range, clear of the single
# synthetic _RES_OID / _RRATE_OID stub above (which stays for the case where
# the app has assignments but no library to go with them).
# The project's OWN calendars, written as themselves. Clear of the fixed
# P6603/6604/6602/6605 block below, which stays for a project that has none.
_PCAL_OID_START = 6700
_RESOURCE_OID_START = 6900
_RESOURCE_RATE_OID_START = 7200
_PROJECT_OID_FALLBACK = "4510"
_INT32_MAX = 2_147_483_647

# Project calendar ObjectIds from the clean XML pattern.
_PCAL_7_NOHOL = "6603"   # P7-DAY NO HOLIDAY, base global 6591
_PCAL_5_NOHOL = "6604"   # P5-DAY NO HOL, base global 6590
_PCAL_5_HOL = "6602"     # P5-DAY STANDARD HOL, base global 6592
_PCAL_6_HOL = "6605"     # P6-DAY WITH HOLIDAY, base global 6593 — emitted on demand
_DEFAULT_PROJECT_CALENDAR_OID = _PCAL_5_NOHOL


# ── Target P6 environment profile ─────────────────────────────────────────────
# The defaults below preserve the working setup from your database. For another
# user's P6 instance, pass target_profile=..., seed_xml_path=..., or seed_xer_path=...
# to write_p6_xml(). The writer temporarily applies those IDs while serializing,
# then restores these defaults so concurrent user profiles cannot bleed together.
_PROFILE_LOCK = threading.RLock()

_PROFILE_GLOBAL_NAMES = (
    "_CUR_OID", "_OBS_OID", "_EPS_OID",
    "_GCAL_5_NOHOL", "_GCAL_7_NOHOL", "_GCAL_5_HOL", "_GCAL_OID",
    "_RES_OID", "_RRATE_OID", "_FPT_OID",
    "_PROJECT_WBS_OID", "_WBS_OID_START", "_ACTIVITY_OID_START",
    "_RELATIONSHIP_OID_START", "_ASSIGNMENT_OID_START", "_PROJECT_OID_FALLBACK",
    "_PCAL_7_NOHOL", "_PCAL_5_NOHOL", "_PCAL_5_HOL", "_DEFAULT_PROJECT_CALENDAR_OID",
)

_PROFILE_ALIASES = {
    # Enterprise / database refs
    "currency_object_id": "_CUR_OID",
    "obs_object_id": "_OBS_OID",
    "parent_eps_object_id": "_EPS_OID",
    "eps_object_id": "_EPS_OID",
    "financial_period_template_id": "_FPT_OID",
    "resource_object_id": "_RES_OID",
    "resource_rate_object_id": "_RRATE_OID",
    # Generated/id range settings
    "project_wbs_object_id": "_PROJECT_WBS_OID",
    "wbs_oid_start": "_WBS_OID_START",
    "activity_oid_start": "_ACTIVITY_OID_START",
    "relationship_oid_start": "_RELATIONSHIP_OID_START",
    "assignment_oid_start": "_ASSIGNMENT_OID_START",
    "project_oid_fallback": "_PROJECT_OID_FALLBACK",
    # Global calendars
    "global_5_day_no_holiday_calendar_object_id": "_GCAL_5_NOHOL",
    "global_7_day_no_holiday_calendar_object_id": "_GCAL_7_NOHOL",
    "global_5_day_standard_holiday_calendar_object_id": "_GCAL_5_HOL",
    "default_global_calendar_object_id": "_GCAL_OID",
    # Project calendars
    "project_7_day_no_holiday_calendar_object_id": "_PCAL_7_NOHOL",
    "project_5_day_no_holiday_calendar_object_id": "_PCAL_5_NOHOL",
    "project_5_day_standard_holiday_calendar_object_id": "_PCAL_5_HOL",
    "default_project_calendar_object_id": "_DEFAULT_PROJECT_CALENDAR_OID",
}

_GLOBAL_CALENDAR_NAME_KEYS = {
    "G5-DAY NO HOLIDAY": "global_5_day_no_holiday_calendar_object_id",
    "G7-DAY NO HOLIDAY": "global_7_day_no_holiday_calendar_object_id",
    "G5-DAY STANDARD HOL '25-'30": "global_5_day_standard_holiday_calendar_object_id",
    "G5-DAY STANDARD HOL": "global_5_day_standard_holiday_calendar_object_id",
}

_PROJECT_CALENDAR_NAME_KEYS = {
    "P7-DAY NO HOLIDAY": "project_7_day_no_holiday_calendar_object_id",
    "P5-DAY NO HOL": "project_5_day_no_holiday_calendar_object_id",
    "P5-DAY STANDARD HOL": "project_5_day_standard_holiday_calendar_object_id",
}


def _current_profile_globals() -> Dict[str, Any]:
    return {name: globals()[name] for name in _PROFILE_GLOBAL_NAMES}


def _normalize_calendar_name(name: Any) -> str:
    return " ".join(str(name or "").strip().upper().split())


def _canonicalize_target_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert friendly target_profile keys into the internal constant names."""
    if not profile:
        return {}

    out: Dict[str, Any] = {}

    for key, value in profile.items():
        if value is None or value == "":
            continue
        if key in _PROFILE_GLOBAL_NAMES:
            out[key] = value
        elif key in _PROFILE_ALIASES:
            out[_PROFILE_ALIASES[key]] = value

    # Nested calendar dictionaries are allowed for app/UI friendliness.
    # Example:
    # target_profile={"global_calendars": {"G5-DAY NO HOLIDAY": "123"}}
    for name, value in (profile.get("global_calendars") or {}).items():
        norm = _normalize_calendar_name(name)
        for known, friendly_key in _GLOBAL_CALENDAR_NAME_KEYS.items():
            if norm == _normalize_calendar_name(known):
                out[_PROFILE_ALIASES[friendly_key]] = value
                break

    for name, value in (profile.get("project_calendars") or {}).items():
        norm = _normalize_calendar_name(name)
        for known, friendly_key in _PROJECT_CALENDAR_NAME_KEYS.items():
            if norm == _normalize_calendar_name(known):
                out[_PROFILE_ALIASES[friendly_key]] = value
                break

    # If the target changed the base 5-day calendar but did not explicitly supply
    # a default, follow the new 5-day base instead of holding the old default.
    if "_GCAL_5_NOHOL" in out and "_GCAL_OID" not in out:
        out["_GCAL_OID"] = out["_GCAL_5_NOHOL"]
    if "_PCAL_5_NOHOL" in out and "_DEFAULT_PROJECT_CALENDAR_OID" not in out:
        out["_DEFAULT_PROJECT_CALENDAR_OID"] = out["_PCAL_5_NOHOL"]

    return out


def validate_target_profile(profile: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return warnings for missing target-environment values before writing XML."""
    merged = _current_profile_globals()
    merged.update(_canonicalize_target_profile(profile))

    required = {
        "_CUR_OID": "currency_object_id",
        "_OBS_OID": "obs_object_id",
        "_EPS_OID": "parent_eps_object_id",
        "_FPT_OID": "financial_period_template_id",
        "_GCAL_5_NOHOL": "global_5_day_no_holiday_calendar_object_id",
        "_GCAL_7_NOHOL": "global_7_day_no_holiday_calendar_object_id",
        "_GCAL_5_HOL": "global_5_day_standard_holiday_calendar_object_id",
    }
    warnings: List[str] = []
    for internal, friendly in required.items():
        if not merged.get(internal):
            warnings.append(f"Missing {friendly}; writer will not be target-P6 safe.")

    try:
        int(float(merged.get("_EPS_OID")))
    except Exception:
        warnings.append("parent_eps_object_id must be a numeric P6 EPS ObjectId.")

    return warnings


class _TargetProfileContext:
    """Thread-safe temporary override of target P6 environment constants."""

    def __init__(self, profile: Optional[Dict[str, Any]]):
        self.profile = _canonicalize_target_profile(profile)
        self.snapshot: Dict[str, Any] = {}

    def __enter__(self):
        _PROFILE_LOCK.acquire()
        self.snapshot = _current_profile_globals()
        for name, value in self.profile.items():
            if name in _PROFILE_GLOBAL_NAMES and value is not None and value != "":
                # Keep numeric range starts as integers; ObjectIds as strings are fine.
                if name.endswith("_START"):
                    globals()[name] = int(float(value))
                else:
                    globals()[name] = str(value)

        # Re-sync dependent defaults if caller changed only the base values.
        globals()["_GCAL_OID"] = str(globals().get("_GCAL_OID") or globals()["_GCAL_5_NOHOL"])
        globals()["_DEFAULT_PROJECT_CALENDAR_OID"] = str(
            globals().get("_DEFAULT_PROJECT_CALENDAR_OID") or globals()["_PCAL_5_NOHOL"]
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            for name, value in self.snapshot.items():
                globals()[name] = value
        finally:
            _PROFILE_LOCK.release()


def parse_xer_tables(xer_path: str) -> Dict[str, List[Dict[str, str]]]:
    """Parse a P6 XER into table rows using %T/%F/%R sections."""
    tables: Dict[str, List[Dict[str, str]]] = {}
    current_table: Optional[str] = None
    fields: List[str] = []

    with open(xer_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n\r")
            if not line:
                continue
            parts = line.split("\t")
            marker = parts[0]
            if marker == "%T" and len(parts) > 1:
                current_table = parts[1]
                tables.setdefault(current_table, [])
                fields = []
            elif marker == "%F" and current_table:
                fields = parts[1:]
            elif marker == "%R" and current_table and fields:
                values = parts[1:]
                # Pad short rows instead of dropping data.
                if len(values) < len(fields):
                    values = values + [""] * (len(fields) - len(values))
                tables[current_table].append(dict(zip(fields, values)))
    return tables


def _first_nonblank(row: Dict[str, str], *keys: str) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _pick_xer_project(tables: Dict[str, List[Dict[str, str]]], project_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    projects = tables.get("PROJECT") or []
    if not projects:
        return None
    if not project_id:
        return projects[0]
    target = str(project_id).strip().lower()
    for row in projects:
        candidates = [
            row.get("proj_short_name"),
            row.get("proj_name"),
            row.get("proj_id"),
        ]
        if any(str(c or "").strip().lower() == target for c in candidates):
            return row
    return projects[0]


def _apply_calendar_row_to_profile(profile: Dict[str, Any], name: str, object_id: str, is_project_calendar: bool):
    norm = _normalize_calendar_name(name)
    if not object_id:
        return
    lookup = _PROJECT_CALENDAR_NAME_KEYS if is_project_calendar else _GLOBAL_CALENDAR_NAME_KEYS
    for known, friendly_key in lookup.items():
        if norm == _normalize_calendar_name(known):
            profile[friendly_key] = object_id
            return


def get_target_profile_from_xer(xer_path: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Build a target P6 environment profile from a seed XER exported from that user's P6.
    This reads the database-level IDs needed by XML import; it does not alter project data.
    """
    tables = parse_xer_tables(xer_path)
    profile: Dict[str, Any] = {}

    project_row = _pick_xer_project(tables, project_id)
    if project_row:
        eps_id = _first_nonblank(project_row, "eps_id", "parent_eps_id")
        obs_id = _first_nonblank(project_row, "obs_id", "obs_node_id")
        clndr_id = _first_nonblank(project_row, "clndr_id")
        fintmpl_id = _first_nonblank(project_row, "fintmpl_id", "financial_period_template_id")
        if eps_id:
            profile["parent_eps_object_id"] = eps_id
        if obs_id:
            profile["obs_object_id"] = obs_id
        if clndr_id:
            profile["default_global_calendar_object_id"] = clndr_id
        if fintmpl_id:
            profile["financial_period_template_id"] = fintmpl_id

    # If the PROJECT table does not carry OBS, use the first OBS row.
    if "obs_object_id" not in profile and (tables.get("OBS") or []):
        obs_id = _first_nonblank(tables["OBS"][0], "obs_id", "obs_node_id")
        if obs_id:
            profile["obs_object_id"] = obs_id

    # Currency and financial template are not always present in seed XERs; use if available.
    for row in tables.get("CURRTYPE", []) + tables.get("CURRENCY", []):
        curr_id = _first_nonblank(row, "curr_id", "currency_id", "curr_type_id")
        if curr_id:
            profile["currency_object_id"] = curr_id
            break

    for table in ("FINTMPL", "FINANCIALPERIODTEMPLATE"):
        for row in tables.get(table, []):
            fpt_id = _first_nonblank(row, "fintmpl_id", "financial_period_template_id", "object_id")
            if fpt_id:
                profile["financial_period_template_id"] = fpt_id
                break

    for row in tables.get("CALENDAR", []):
        cal_id = _first_nonblank(row, "clndr_id", "calendar_id", "object_id")
        cal_name = _first_nonblank(row, "clndr_name", "calendar_name", "name") or ""
        cal_type = (_first_nonblank(row, "clndr_type", "calendar_type", "type") or "").lower()
        proj_ref = _first_nonblank(row, "proj_id")
        is_project = "project" in cal_type or bool(proj_ref)
        # Only global calendar IDs are environment references. Project calendar
        # ObjectIds are internal to the XML import package and should remain
        # generated/fixed unless explicitly overridden by target_profile.
        if not is_project:
            _apply_calendar_row_to_profile(profile, cal_name, cal_id or "", is_project_calendar=False)

    for row in tables.get("RSRC", []) + tables.get("RESOURCE", []):
        rsrc_id = _first_nonblank(row, "rsrc_id", "resource_id", "object_id")
        if rsrc_id:
            profile["resource_object_id"] = rsrc_id
            break

    for row in tables.get("RSRCRATE", []) + tables.get("RESOURCERATE", []):
        rate_id = _first_nonblank(row, "rsrc_rate_id", "resource_rate_id", "object_id")
        if rate_id:
            profile["resource_rate_object_id"] = rate_id
            break

    return profile


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _direct_children(parent: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _child_text(parent: ET.Element, name: str) -> Optional[str]:
    for child in _direct_children(parent, name):
        if child.get(_XSI_NIL) == "true":
            return None
        if child.text is not None:
            return child.text.strip()
    return None


def get_target_profile_from_xml(xml_path: str) -> Dict[str, Any]:
    """
    Build a target P6 profile from a known-good XML exported from that user's P6.
    This is often more complete than XER for XML import references.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    profile: Dict[str, Any] = {}

    for child in _direct_children(root, "Currency"):
        oid = _child_text(child, "ObjectId")
        if oid:
            profile["currency_object_id"] = oid
            break

    for child in _direct_children(root, "OBS"):
        oid = _child_text(child, "ObjectId")
        if oid:
            profile["obs_object_id"] = oid
            break

    for child in _direct_children(root, "FinancialPeriodTemplate"):
        oid = _child_text(child, "ObjectId")
        if oid:
            profile["financial_period_template_id"] = oid
            break

    for child in _direct_children(root, "Resource"):
        oid = _child_text(child, "ObjectId")
        if oid:
            profile["resource_object_id"] = oid
            break

    for child in _direct_children(root, "ResourceRate"):
        oid = _child_text(child, "ObjectId")
        if oid:
            profile["resource_rate_object_id"] = oid
            break

    for cal in _direct_children(root, "Calendar"):
        if (_child_text(cal, "Type") or "").lower() != "global":
            continue
        name = _child_text(cal, "Name") or ""
        oid = _child_text(cal, "ObjectId") or ""
        _apply_calendar_row_to_profile(profile, name, oid, is_project_calendar=False)

    projects = _direct_children(root, "Project")
    if projects:
        proj = projects[0]
        eps_oid = _child_text(proj, "ParentEPSObjectId")
        if eps_oid:
            profile["parent_eps_object_id"] = eps_oid
        fpt = _child_text(proj, "FinancialPeriodTemplateId")
        if fpt:
            profile["financial_period_template_id"] = fpt
        # Do not copy the seed project ObjectId/WBSObjectId or project-calendar
        # ObjectIds into the target profile. Those are internal to a specific
        # project export. The writer keeps its safe generated ranges so a new
        # project can be created instead of matching the seed project's internals.

    return profile


def build_target_profile(
    target_profile: Optional[Dict[str, Any]] = None,
    seed_xer_path: Optional[str] = None,
    seed_xml_path: Optional[str] = None,
    seed_project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Merge profile sources into one internal profile.

    Priority, lowest to highest:
    1. Working defaults from your P6 database
    2. seed_xml_path / seed_xer_path values
    3. explicit target_profile overrides
    """
    merged: Dict[str, Any] = {}
    if seed_xml_path:
        merged.update(get_target_profile_from_xml(seed_xml_path))
    if seed_xer_path:
        merged.update(get_target_profile_from_xer(seed_xer_path, project_id=seed_project_id))
    if target_profile:
        merged.update(target_profile)
    return _canonicalize_target_profile(merged)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


# P6 stores dates in SQL Server `datetime`, whose range starts at 1753-01-01 —
# not the 0001-01-01 that `datetime2` and Python's date both allow. A date
# below that floor imports as:
#
#   XMLImporterException: executeUpdateBatch: The conversion of a datetime2
#   data type to a datetime data type resulted in an out-of-range value.
#
# which names no activity, no field and no date, and fails the whole batch
# after it has run. The upper bound is 9999-12-31 and is not reachable in
# practice; the floor is, because a zeroed or malformed date lands there.
_SQL_DATETIME_MIN = "1753-01-01"
_SQL_DATETIME_MAX = "9999-12-31"

# Dates the writer refused, as (why, value). Collected per-write so the caller
# can tell the user which rows were dropped instead of shipping an XML that
# P6 rejects — see date_problems().
_BAD_DATES: List[Tuple[str, str]] = []


def _valid_date(d: Optional[str], where: str = "") -> Optional[str]:
    """
    The date part of `d`, or None when P6 could not store it.

    Emitting it anyway is the worse option by a distance: the import fails as
    a batch, so one bad row takes the whole schedule with it, and the message
    P6 gives back does not say which row.
    """
    if not d:
        return None
    s = str(d)[:10]
    try:
        _date.fromisoformat(s)
    except ValueError:
        _BAD_DATES.append((where or "date", str(d)))
        return None
    if s < _SQL_DATETIME_MIN or s > _SQL_DATETIME_MAX:
        _BAD_DATES.append((where or "date", s))
        return None
    return s


def _dt_start(d: Optional[str], where: str = "") -> Optional[str]:
    s = _valid_date(d, where)
    return s + "T08:00:00" if s else None


def _dt_finish(d: Optional[str], where: str = "") -> Optional[str]:
    s = _valid_date(d, where)
    return s + "T17:00:00" if s else None


def date_problems(project: Project) -> List[Dict[str, str]]:
    """
    Every date in the project P6 could not store, named by activity and field.

    Read-only, and worth running BEFORE handing someone an export: P6 fails
    the import as a batch and reports only "the conversion of a datetime2 data
    type to a datetime data type resulted in an out-of-range value" — no
    activity, no field, no value. One bad row takes the whole schedule with
    it and gives you nothing to go on.
    """
    fields = ("planned_start", "planned_finish", "actual_start",
              "actual_finish", "early_start", "early_finish",
              "late_start", "late_finish", "constraint_date")
    out: List[Dict[str, str]] = []
    for a in getattr(project, "activities", None) or []:
        for f in fields:
            raw = getattr(a, f, None)
            if not raw:
                continue
            s = str(raw)[:10]
            why = ""
            try:
                _date.fromisoformat(s)
            except ValueError:
                why = "not a real date"
            else:
                if s < _SQL_DATETIME_MIN:
                    why = f"before {_SQL_DATETIME_MIN}, which is as far back as P6 stores"
                elif s > _SQL_DATETIME_MAX:
                    why = f"after {_SQL_DATETIME_MAX}"
            if why:
                out.append({"activity_id": a.activity_id, "name": a.name,
                            "field": f, "value": str(raw), "why": why})
    for f, label in (("data_date", "data date"),
                     ("planned_start", "project start"),
                     ("must_finish_by", "must finish by")):
        raw = getattr(project, f, None)
        if not raw:
            continue
        s = str(raw)[:10]
        try:
            _date.fromisoformat(s)
            bad = s < _SQL_DATETIME_MIN or s > _SQL_DATETIME_MAX
        except ValueError:
            bad = True
        if bad:
            out.append({"activity_id": "—", "name": f"project {label}",
                        "field": f, "value": str(raw),
                        "why": "P6 cannot store this date"})
    return out


def _activity_type_name(act: Any) -> str:
    return (getattr(act, "activity_type", None) or "Task Dependent").strip()


def _is_start_milestone(act: Any) -> bool:
    return _activity_type_name(act).lower() == "start milestone"


def _is_finish_milestone(act: Any) -> bool:
    return _activity_type_name(act).lower() == "finish milestone"


def _dt_activity_start(d: Optional[str], act: Any) -> Optional[str]:
    """P6 native XML uses 17:00 for finish-milestone start/finish and 08:00 for start milestones."""
    if _is_finish_milestone(act):
        return _dt_finish(d)
    return _dt_start(d)


def _dt_activity_finish(d: Optional[str], act: Any) -> Optional[str]:
    """P6 native XML uses 08:00 for start-milestone finish and 17:00 otherwise."""
    if _is_start_milestone(act):
        return _dt_start(d)
    return _dt_finish(d)


def _sub(parent: ET.Element, tag: str, text: Optional[str] = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _nil(parent: ET.Element, tag: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.set(_XSI_NIL, "true")
    return el


_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday")          # index == Calendar.work_days value


def _days_on(cal) -> tuple:
    """The day NAMES this calendar works, from the pattern it was read with."""
    wd = getattr(cal, "work_days", None)
    if not wd:
        return _DAY_NAMES[:5]
    return tuple(_DAY_NAMES[d] for d in sorted(wd) if 0 <= d < 7)


def _shifts(hours_per_day: float) -> list:
    """
    The work times that add up to a day of this length.

    Hardcoded 08:00-11:59 + 13:00-16:59 meant every exported calendar was an
    eight-hour day whatever the schedule said, so a 6-day 10-hour job came back
    from P6 as 5-day 8-hour and every duration re-read as a different number of
    days. The shape is kept — a morning, an hour for lunch, an afternoon — and
    only the length moves.

    P6 writes a shift's Finish as the last working minute (11:59, not 12:00),
    so the same convention is used here.
    """
    h = float(hours_per_day or 8.0)
    if h <= 0:
        h = 8.0
    half = h / 2.0
    start = 8 * 60                       # 08:00
    m_end = start + int(round(half * 60))
    a_start = m_end + 60                 # an hour for lunch
    a_end = a_start + int(round((h - half) * 60))
    if a_end > 24 * 60 - 1:              # a very long day: run it straight
        m_end = start + int(round(h * 60))
        return [(start, m_end)]
    return [(start, m_end), (a_start, a_end)]


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _workweek(parent: ET.Element, days_on: tuple, hours_per_day: float = 8.0):
    """Write StandardWorkWeek. days_on is tuple of day names that are working."""
    all_days = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
    shifts = _shifts(hours_per_day)
    sww = _sub(parent, "StandardWorkWeek")
    for day in all_days:
        swh = _sub(sww, "StandardWorkHours")
        _sub(swh, "DayOfWeek", day)
        if day in days_on:
            for s, e in shifts:
                wt = _sub(swh, "WorkTime")
                _sub(wt, "Start", _hhmm(s))
                _sub(wt, "Finish", _hhmm(e - 1))
        else:
            _nil(swh, "WorkTime")




def _key(value: Any) -> str:
    """Stable string key for source-model ids, regardless of int/string input."""
    return "" if value is None else str(value)


def _as_int_text(value: Any, default: int = 0) -> str:
    """P6 XML is less brittle when integer-like fields are emitted as integers, not 0.0."""
    try:
        if value is None or value == "":
            return str(default)
        return str(int(float(value)))
    except Exception:
        return str(default)


def _safe_project_object_id(value: Any) -> str:
    """Keep the project ObjectId if it is a normal P6-sized int; otherwise use a safe fallback."""
    try:
        ivalue = int(float(value))
        if 0 < ivalue < _INT32_MAX:
            return str(ivalue)
    except Exception:
        pass
    return _PROJECT_OID_FALLBACK


def _build_oid_map(items: list, attr: str, start: int) -> Dict[str, str]:
    """Map source-model ObjectIds to sequential P6-safe ObjectIds."""
    mapping: Dict[str, str] = {}
    next_oid = start
    for idx, item in enumerate(items):
        raw = getattr(item, attr, None)
        key = _key(raw) or f"__{attr}_{idx}"
        if key not in mapping:
            mapping[key] = str(next_oid)
            next_oid += 1
    return mapping


def _pct_text(percent_0_100) -> str:
    """
    Percent complete as P6 XML carries it: a FRACTION, where 1.0 means 100%.

    P6 shows 0-100 in its own columns, but the XML importer wants 0-1 and
    rejects anything above it outright:
        "Unable to invoke setPhysicalPercentComplete ... Percent value 100.0
         is out of range. The maximum allowed value is 1.0"
    That is a SEVERE exception, not a warning — it aborts the activity import
    for the whole file, so the project arrives with its WBS and no activities.
    """
    try:
        v = float(percent_0_100 or 0)
    except (TypeError, ValueError):
        v = 0.0
    v = max(0.0, min(100.0, v)) / 100.0
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _project_uses_6day(project) -> bool:
    """Whether the 6-day project calendar will actually be written."""
    for cal in (getattr(project, "calendars", None) or []):
        if _cal_is_6day(cal):
            return True
    return False


def _calendar_target_from_name(name: str) -> str:
    lname = (name or "").lower()
    if "6" in lname or "six" in lname:
        return _PCAL_6_HOL
    if "7" in lname or "seven" in lname:
        return _PCAL_7_NOHOL
    if "standard hol" in lname or ("hol" in lname and "no hol" not in lname):
        return _PCAL_5_HOL
    return _PCAL_5_NOHOL


def _own_calendars(project) -> list:
    """
    The calendars this project actually carries, if any are usable.

    A calendar needs a uid to be pointed at and a name to be recognised in P6.
    With none that qualify the export falls back to the fixed set below, which
    is what an app-built schedule with no calendar data has always had.
    """
    out = []
    for c in (getattr(project, "calendars", None) or []):
        if getattr(c, "uid", None) and (getattr(c, "name", "") or "").strip():
            out.append(c)
    return out


def _build_calendar_oid_map(project: Project) -> Dict[str, str]:
    """
    Map app/XER calendar ids to the fixed project calendar ids written below.

    Only calendars this export actually emits may be referenced. The 6-day
    calendar is written on demand, while the name match that selects it fires
    on any name merely containing a "6" — so an activity could point at a
    calendar that was never written, and P6 logs
    "Referenced business object Calendar ... cannot be found, ignoring field
    CalendarObjectId", leaving the activity with no calendar at all.
    """
    own = _own_calendars(project)
    if own:
        # The project's calendars are written as themselves, so an activity
        # points at the calendar it is actually on. Before this, every calendar
        # was matched to one of three canned ones BY NAME and a six-day
        # ten-hour week had nowhere to land — which is why every import came
        # back on P6 5-day and had to be set by hand.
        mapping = {_key(cal.uid): str(_PCAL_OID_START + i)
                   for i, cal in enumerate(own)}
        mapping.setdefault("", mapping[_key(own[0].uid)])
        return mapping

    six = _project_uses_6day(project)
    mapping: Dict[str, str] = {}
    for cal in getattr(project, "calendars", []) or []:
        target = _calendar_target_from_name(getattr(cal, "name", ""))
        if target == _PCAL_6_HOL and not six:
            target = _DEFAULT_PROJECT_CALENDAR_OID
        mapping[_key(getattr(cal, "uid", None))] = target
    # Common parser default seen in failed output.
    mapping.setdefault("178", _PCAL_5_NOHOL)
    mapping.setdefault("1", _DEFAULT_PROJECT_CALENDAR_OID)
    mapping.setdefault("", _DEFAULT_PROJECT_CALENDAR_OID)
    return mapping


def _map_calendar_oid(calendar_uid: Any, calendar_oid_map: Dict[str, str]) -> str:
    return calendar_oid_map.get(_key(calendar_uid), _DEFAULT_PROJECT_CALENDAR_OID)


def _get_any(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None and value != "":
                return value
    return default


def _project_assignments(project: Project) -> list:
    """Return resource assignments if the model exposes them; otherwise none."""
    for attr in ("resource_assignments", "assignments", "activity_resource_assignments"):
        value = getattr(project, attr, None)
        if value:
            return list(value)
    return []

# ── Enterprise envelope sections ───────────────────────────────────────────────

def _section_display_currency(root: ET.Element):
    dc = _sub(root, "DisplayCurrency")
    c  = _sub(dc, "Currency")
    _sub(c, "DecimalPlaces",       "2")
    _sub(c, "DecimalSymbol",       "Period")
    _sub(c, "DigitGroupingSymbol", "Comma")
    _sub(c, "ExchangeRate",        "1")
    _sub(c, "Id",                  "USD")
    _sub(c, "Name",                "US Dollar")
    _sub(c, "NegativeSymbol",      "(#1.1)")
    _sub(c, "ObjectId",            _CUR_OID)
    _sub(c, "PositiveSymbol",      "#1.1")
    _sub(c, "Symbol",              "$")


def _section_currency(root: ET.Element):
    c = _sub(root, "Currency")
    _sub(c, "DecimalPlaces",       "2")
    _sub(c, "DecimalSymbol",       "Period")
    _sub(c, "DigitGroupingSymbol", "Comma")
    _sub(c, "ExchangeRate",        "1")
    _sub(c, "Id",                  "USD")
    _sub(c, "Name",                "US Dollar")
    _sub(c, "NegativeSymbol",      "(#1.1)")
    _sub(c, "ObjectId",            _CUR_OID)
    _sub(c, "PositiveSymbol",      "#1.1")
    _sub(c, "Symbol",              "$")


# UDF ObjectIds are minted in their own range so they cannot collide with the
# fixed reference ids above.
_UDF_OID_START = 900


# COMMENTS is emitted with the ObjectId the reference export uses, and every
# value that references it must use the SAME id. Handing it a second id from
# the generated range wrote <TypeObjectId>900</TypeObjectId> against a type
# only ever declared as 813, so P6 could not resolve the field and rejected
# the activity with an invalid-UDF-data-type error.
_COMMENTS_OID = "813"

# The only values P6 accepts in <DataType> on a UDFType — taken verbatim from
# Oracle's own generated schema classes (UDFTypeType, in the javadoc-embedded
# xsd:enumeration), not guessed. Two things were wrong in the first version of
# this list, invisibly, because nothing here was ever checked against a real
# P6-authored file: "Date" is not a member at all, and the real names carry a
# space — "Start Date" / "Finish Date", not "StartDate" / "FinishDate". A file
# that declared either would have failed the exact same "Invalid UDF data
# type" the whole normalisation exists to prevent.
_VALID_UDF_TYPES = ("Text", "Start Date", "Finish Date", "Cost", "Double",
                    "Integer", "Indicator", "Code")

# The child element each DataType's value is written into, on UDFAssignmentType
# — also read from the real schema, not inferred from the DataType name. This
# is the second, more damaging bug it uncovered: every value was being written
# into <Text>/<Integer>/<Double>, and P6 does not have elements by those names
# at all. The real ones are <TextValue>/<IntegerValue>/<DoubleValue> etc. — so
# every UDF value in every export was inside an element P6's schema does not
# recognise, independent of whether the DataType itself was valid. That is
# what actually produced "Invalid UDF data type when importing Activity
# NNNNN" even on a build that already carried the FT_TEXT fix: the type was
# valid, the element holding its value was not.
_UDF_VALUE_ELEMENT = {
    "Text": "TextValue", "Integer": "IntegerValue", "Double": "DoubleValue",
    "Cost": "CostValue", "Indicator": "IndicatorValue",
    "Start Date": "StartDateValue", "Finish Date": "FinishDateValue",
}

# P6's INTERNAL type codes, which show up in files that came from XER, from the
# API, or from a tool that passed them through. They are not XML DataTypes and
# must be translated before export.
_P6_TYPE_CODES = {
    "FT_TEXT": "Text",
    "FT_INT": "Integer",
    "FT_FLOAT": "Double",
    "FT_MONEY": "Cost",
    "FT_END_DATE": "Finish Date",
    "FT_START_DATE": "Start Date",
    "FT_STATIC_TYPE": "Indicator",
    "FT_STATIC_TYPE_TEXT": "Text",
    "FT_STATIC_TYPE_DOUBLE": "Double",
    "FT_STATIC_TYPE_INT": "Integer",
    # P6's generic "Date" UDF type has no direct match in the XML schema's
    # DataType enum (only Start Date / Finish Date exist) and this app has no
    # way to know which the source meant. Text keeps the data — every date
    # string still round-trips — rather than guessing the wrong one of two
    # enum members, which would fail the same import this fixes.
    "FT_STATIC_TYPE_DATE": "Text",
}


def normalize_udf_type(raw: str) -> str:
    """
    A DataType P6 will actually accept.

    This is a WHITELIST rather than a list of known-bad values on purpose. The
    first version mapped the FT_STATIC_TYPE_* spellings and let everything else
    through untouched, so a file carrying plain FT_TEXT exported FT_TEXT and P6
    rejected the import — the same failure the mapping was written to prevent,
    reached by a code that had not been thought of. Falling back to Text means
    an unrecognised code costs the field's type and never the whole import.
    """
    s = (raw or "").strip()
    if s in _VALID_UDF_TYPES:
        return s
    mapped = _P6_TYPE_CODES.get(s.upper())
    if mapped:
        return mapped
    for valid in _VALID_UDF_TYPES:          # tolerate casing differences
        if valid.lower() == s.lower():
            return valid
    return "Text"


def _udf_declarations(project) -> List[Tuple[str, str, str]]:
    """
    (title, ObjectId, DataType) for every activity UDF this export will carry —
    the single source of truth for BOTH the UDFType blocks and the per-activity
    values, so a value can never point at a type that was not declared.

    Covers the definitions the project arrived with and any field that only
    appears as a value (a column added in-app), so it exports as a real P6
    user-defined field rather than being dropped.

    A field declared numeric whose values are not all numeric is declared as
    Text instead. Writing <Text> under a <DataType>Double</DataType> is the
    same invalid-type rejection by another route, and downgrading keeps the
    data — dropping the value would lose it silently.
    """
    titles: List[str] = []
    for u in (getattr(project, "udf_types", None) or []):
        if u.title and u.title not in titles:
            titles.append(u.title)
    for a in project.activities:
        for t in (getattr(a, "udfs", None) or {}):
            if t and t not in titles:
                titles.append(t)

    declared = {}
    for u in (getattr(project, "udf_types", None) or []):
        if u.title:
            declared[u.title] = normalize_udf_type(u.data_type)

    out: List[Tuple[str, str, str]] = []
    next_oid = _UDF_OID_START
    for title in titles:
        dt = declared.get(title, "Text")
        if dt in ("Double", "Integer", "Cost"):
            for a in project.activities:
                v = (getattr(a, "udfs", None) or {}).get(title)
                if v in (None, ""):
                    continue
                try:
                    float(str(v))
                except (TypeError, ValueError):
                    dt = "Text"
                    break
        elif dt not in ("Text",):
            # Indicator / Start Date / Finish Date / Code all need a value
            # this app has no reliable way to produce — an Indicator is one of
            # a fixed set of status strings, a Code references a lookup list
            # entry by id, and a date needs real parsing. Every value here is
            # already a plain string in `act.udfs`, so writing it under any of
            # those types would put an arbitrary string where the schema
            # expects a specific shape and fail import by a new route. Text
            # keeps the data faithfully with no such risk.
            dt = "Text"
        if title == "COMMENTS":
            oid = _COMMENTS_OID
        else:
            oid = str(next_oid)
            next_oid += 1
        out.append((title, oid, dt))
    return out


def _udf_oid_map(project) -> Dict[str, str]:
    """Title -> ObjectId, matching what _section_udf_type declares."""
    return {t: oid for t, oid, _ in _udf_declarations(project)}


def _udf_data_type(project, title: str) -> str:
    for t, _oid, dt in _udf_declarations(project):
        if t == title:
            return dt
    return "Text"


def _section_udf_type(root: ET.Element, project=None, include_udfs: bool = True):
    """
    UDF definitions — one block per field, from the same list the values are
    written against, so every TypeObjectId a value references is declared here.

    COMMENTS is always emitted (the reference export carries it) even when no
    activity uses it.
    """
    if not include_udfs:
        return
    decls = _udf_declarations(project) if project is not None else []
    if not any(t == "COMMENTS" for t, _o, _d in decls):
        decls = [("COMMENTS", _COMMENTS_OID, "Text")] + decls
    for title, oid, dt in decls:
        t = _sub(root, "UDFType")
        # Normalised once more at the point of writing: this is the last line
        # before the bytes hit the file, and an invalid value here fails the
        # user's whole import rather than one field.
        _sub(t, "DataType",     normalize_udf_type(dt))
        _sub(t, "IsSecureCode", "0")
        _sub(t, "ObjectId",     oid)
        _sub(t, "SubjectArea",  "Activity")
        _sub(t, "Title",        title)


def _write_activity_udfs(a_el: ET.Element, act, oid_map: Dict[str, str],
                         project) -> None:
    """
    Per-activity UDF values, in the typed element P6 expects.

    That element is named for the VALUE ("TextValue", "IntegerValue", …), not
    for the type ("Text", "Integer", …) — confirmed against Oracle's own
    generated schema classes, which is also where this found that every value
    this writer ever produced was sitting inside an element P6's schema does
    not define at all (<Text> instead of <TextValue>, and so on). That is a
    second, independent way to fail exactly the same "Invalid UDF data type"
    import error, and it fires regardless of whether the DataType on the
    UDFType declaration itself is valid — which is why fixing only the
    DataType did not clear the error on its own.
    """
    vals = getattr(act, "udfs", None) or {}
    if not vals:
        return
    types = {t: dt for t, _oid, dt in _udf_declarations(project)}
    for title, value in vals.items():
        oid = oid_map.get(title)
        if not oid or value in (None, ""):
            continue
        u = _sub(a_el, "UDF")
        _sub(u, "TypeObjectId", oid)
        # _udf_declarations only ever settles on Text, Integer, Double or Cost
        # for a field this app can populate — everything else is downgraded
        # there already — so the element this maps to is always populated.
        dt = types.get(title, "Text")
        el = _UDF_VALUE_ELEMENT.get(dt, "TextValue")
        if dt in ("Double", "Cost"):
            _sub(u, el, str(float(str(value))))
        elif dt == "Integer":
            _sub(u, el, str(int(float(str(value)))))
        else:
            _sub(u, el, str(value))


def _section_obs(root: ET.Element):
    o = _sub(root, "OBS")
    _sub(o, "Description",    '<html>\n  <head>\n    \n  </head>\n\n  <body bgcolor="#ffffff">\n    Enterprise\n  </body>\n\n</html>')
    _nil(o, "GUID")
    _sub(o, "Name",           "Enterprise")
    _sub(o, "ObjectId",       _OBS_OID)
    _nil(o, "ParentObjectId")
    _sub(o, "SequenceNumber", "0")


def _holiday_exceptions(parent: ET.Element, holidays):
    """
    Write non-working exception days. A HolidayOrException with no WorkTime
    children is a full non-working day in P6.
    """
    if not holidays:
        return
    hx = _sub(parent, "HolidayOrExceptions")
    for iso in sorted(holidays):
        h = _sub(hx, "HolidayOrException")
        _sub(h, "Date", f"{str(iso)[:10]}T00:00:00")


def _project_uses(project, predicate) -> bool:
    """True if any calendar on the project matches — used to gate optional output."""
    for cal in (getattr(project, "calendars", None) or []):
        try:
            if predicate(cal):
                return True
        except Exception:
            continue
    return False


def _cal_holidays(cal) -> frozenset:
    return frozenset(getattr(cal, "holidays", None) or ())


def _cal_is_6day(cal) -> bool:
    wd = getattr(cal, "work_days", None)
    return bool(wd) and len(wd) == 6


def _global_calendar(root: ET.Element, oid: str, name: str, days_on: tuple,
                     holidays=(), hours_per_day: float = 8.0):
    """
    A global calendar the project calendars sit on top of.

    The hours follow the project rather than being fixed at eight: a project
    calendar inheriting from a base that disagrees with it about the length of
    a day is how a 10-hour job reads back as an 8-hour one.
    """
    h = float(hours_per_day or 8.0)
    cal = _sub(root, "Calendar")
    _nil(cal, "BaseCalendarObjectId")
    _sub(cal, "HoursPerDay",   str(int(h)))
    _sub(cal, "HoursPerMonth", str(int(round(h * len(days_on) * 4.333))))
    _sub(cal, "HoursPerWeek",  str(int(round(h * len(days_on)))))
    _sub(cal, "HoursPerYear",  str(int(round(h * len(days_on) * 52))))
    _sub(cal, "IsDefault",     "0")
    _sub(cal, "IsPersonal",    "0")
    _sub(cal, "Name",          name)
    _sub(cal, "ObjectId",      oid)
    _nil(cal, "ProjectObjectId")
    _sub(cal, "Type",          "Global")
    _workweek(cal, days_on, h)
    _holiday_exceptions(cal, holidays)


def _section_global_calendars(root: ET.Element, project=None):
    """
    Same global calendar order/pattern as the clean native P6 XML.

    Holiday exceptions and the 6-day calendar are emitted ONLY when the project
    actually carries them, so a schedule that never picks a holiday calendar
    produces byte-identical output to before this feature existed.
    """
    M_F = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
    ALL = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
    M_S = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")

    hol = frozenset()
    six = _project_uses_6day(project)
    if project is not None:
        for cal in (getattr(project, "calendars", None) or []):
            hol |= _cal_holidays(cal)

    _global_calendar(root, _GCAL_5_NOHOL, "G5-DAY NO HOLIDAY", M_F)
    _global_calendar(root, _GCAL_7_NOHOL, "G7-DAY NO HOLIDAY", ALL)
    _global_calendar(root, _GCAL_5_HOL, "G5-DAY STANDARD HOL '25-'30", M_F, holidays=hol)
    if six:
        _global_calendar(root, _GCAL_6_HOL, "G6-DAY WITH HOLIDAY", M_S, holidays=hol)


def _section_resource(root: ET.Element):
    r = _sub(root, "Resource")
    _sub(r, "AutoComputeActuals",    "1")
    _sub(r, "CalculateCostFromUnits","1")
    _sub(r, "CalendarObjectId",      _GCAL_OID)
    _sub(r, "CurrencyObjectId",      _CUR_OID)
    _sub(r, "DefaultUnitsPerTime",   "1")
    _nil(r, "EmailAddress")
    _nil(r, "EmployeeId")
    _sub(r, "GUID",                  _guid())
    _sub(r, "Id",                    "Cost-MLCB")
    _sub(r, "IsActive",              "1")
    _sub(r, "IsOverTimeAllowed",     "0")
    _sub(r, "Name",                  "Costs MLCB")
    _sub(r, "ObjectId",              _RES_OID)
    _nil(r, "OfficePhone")
    _nil(r, "OtherPhone")
    _sub(r, "OvertimeFactor",        "0")
    _nil(r, "ParentObjectId")
    _nil(r, "PrimaryRoleObjectId")
    _nil(r, "ResourceNotes")
    _sub(r, "ResourceType",          "Nonlabor")
    _sub(r, "SequenceNumber",        "600")
    _nil(r, "ShiftObjectId")
    _nil(r, "TimesheetApprovalManagerObjectId")
    _nil(r, "Title")
    _nil(r, "UnitOfMeasureObjectId")
    _sub(r, "UseTimesheets",         "0")
    _nil(r, "UserObjectId")


_RES_TYPES = {"Labor", "Nonlabor", "Material"}


def _section_real_resources(root: ET.Element, project: Project) -> Dict[str, str]:
    """
    Write the resource library the import actually found, and return
    {resource uid -> the ObjectId it was written with}.

    This exists because the alternative was worse than nothing: the export
    used to write ONE synthetic "Costs MLCB" resource and point every
    assignment at it, so a file that arrived with six crews on it came back
    with six assignments to a made-up nonlabor line. Now each resource is
    written as itself, and the assignment block points at the right one.

    Every resource gets a rate block whether or not it has a rate: P6 reads
    Max Units/Time off the rate, and a resource without one imports with a
    zero availability that quietly breaks levelling.
    """
    res_oid: Dict[str, str] = {}
    for i, r in enumerate(project.resources):
        oid = str(_RESOURCE_OID_START + i)
        res_oid[_key(r.uid)] = oid
        el = _sub(root, "Resource")
        _sub(el, "AutoComputeActuals",     "1")
        _sub(el, "CalculateCostFromUnits", "1")
        _sub(el, "CalendarObjectId",       _GCAL_OID)
        _sub(el, "CurrencyObjectId",       _CUR_OID)
        _sub(el, "DefaultUnitsPerTime",    _num(r.max_units, 1))
        _nil(el, "EmailAddress")
        _nil(el, "EmployeeId")
        _sub(el, "GUID",                   _guid())
        _sub(el, "Id",                     r.id or f"RSRC-{i + 1}")
        _sub(el, "IsActive",               "1" if r.is_active else "0")
        _sub(el, "IsOverTimeAllowed",      "0")
        _sub(el, "Name",                   r.name or r.id or f"Resource {i + 1}")
        _sub(el, "ObjectId",               oid)
        _nil(el, "OfficePhone")
        _nil(el, "OtherPhone")
        _sub(el, "OvertimeFactor",         "0")
        # A parent outside this library would dangle, so an unknown parent is
        # written as no parent rather than as a reference P6 cannot resolve.
        parent = res_oid.get(_key(r.parent_uid)) if r.parent_uid else None
        if parent:
            _sub(el, "ParentObjectId", parent)
        else:
            _nil(el, "ParentObjectId")
        _nil(el, "PrimaryRoleObjectId")
        _nil(el, "ResourceNotes")
        _sub(el, "ResourceType", r.type if r.type in _RES_TYPES else "Labor")
        _sub(el, "SequenceNumber",         str((i + 1) * 10))
        _nil(el, "ShiftObjectId")
        _nil(el, "TimesheetApprovalManagerObjectId")
        _nil(el, "Title")
        _nil(el, "UnitOfMeasureObjectId")
        _sub(el, "UseTimesheets",          "0")
        _nil(el, "UserObjectId")

    for i, r in enumerate(project.resources):
        rr = _sub(root, "ResourceRate")
        _sub(rr, "EffectiveDate",    "2024-01-01T00:00:00")
        _sub(rr, "MaxUnitsPerTime",  _num(r.max_units, 1))
        _sub(rr, "ObjectId",         str(_RESOURCE_RATE_OID_START + i))
        _sub(rr, "PricePerUnit",     _num(r.rate, 0))
        for n in range(2, 6):
            _sub(rr, f"PricePerUnit{n}", "0")
        _sub(rr, "ResourceObjectId", res_oid[_key(r.uid)])
        _nil(rr, "ShiftPeriodObjectId")
    return res_oid


def _num(v: Any, default: float = 0.0) -> str:
    """A number P6 will read: no exponent, no trailing noise, never blank."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = float(default)
    return str(int(f)) if f == int(f) else f"{f:.6f}".rstrip("0").rstrip(".")


def _section_resource_rate(root: ET.Element):
    rr = _sub(root, "ResourceRate")
    _sub(rr, "EffectiveDate",       "2024-01-01T00:00:00")
    _sub(rr, "MaxUnitsPerTime",     "1")
    _sub(rr, "ObjectId",            _RRATE_OID)
    _sub(rr, "PricePerUnit",        "1")
    _sub(rr, "PricePerUnit2",       "0")
    _sub(rr, "PricePerUnit3",       "0")
    _sub(rr, "PricePerUnit4",       "0")
    _sub(rr, "PricePerUnit5",       "0")
    _sub(rr, "ResourceObjectId",    _RES_OID)
    _nil(rr, "ShiftPeriodObjectId")


def _section_fpt(root: ET.Element):
    fpt = _sub(root, "FinancialPeriodTemplate")
    _sub(fpt, "FinancialPeriodTemplateName", "Calendar")
    _sub(fpt, "ObjectId",                    _FPT_OID)


# ── Project calendar (nested inside <Project>) ─────────────────────────────────

def _project_calendar(proj_el: ET.Element, cal: Calendar, proj_uid: str,
                      oid: Optional[str] = None):
    """
    Write a project-scoped calendar inside the Project block.

    This used to write Monday-to-Friday regardless of what the calendar said,
    and pick its base calendar by looking for a "7" in the NAME. So a six-day
    calendar went out as five-day, every import landed back on P6 5-day, and
    the working pattern had to be set by hand after every single import. The
    reader gets the pattern right and the app's own CPM uses it; only the
    export was throwing it away.

    Both now come off the calendar itself: the days it works, and the hours it
    works them. Holidays are written too — a calendar named for its holiday
    set that observes none of them is the same bug one level down.
    """
    c = _sub(proj_el, "Calendar")
    days = _days_on(cal)
    hol = _cal_holidays(cal)
    # The base calendar is chosen by what this calendar DOES, not by what it is
    # called. A job named "6-Day 10hr" has no "7" in it and was landing on the
    # five-day base.
    n = len(days)
    if n >= 7:
        base_oid = _GCAL_7_NOHOL
    elif n == 6:
        base_oid = _GCAL_6_HOL if hol else _GCAL_7_NOHOL
    else:
        base_oid = _GCAL_5_HOL if hol else _GCAL_5_NOHOL
    _sub(c, "BaseCalendarObjectId", base_oid)
    _sub(c, "HoursPerDay",          str(int(cal.hours_per_day)))
    _sub(c, "HoursPerMonth",        str(int(cal.hours_per_month)))
    _sub(c, "HoursPerWeek",         str(int(cal.hours_per_week)))
    _sub(c, "HoursPerYear",         str(int(cal.hours_per_year)))
    _sub(c, "IsDefault",            "0")
    _sub(c, "IsPersonal",           "0")
    _sub(c, "Name",                 cal.name)
    _sub(c, "ObjectId",             oid or str(cal.uid))
    _sub(c, "ProjectObjectId",      proj_uid)     # must equal the Project ObjectId
    _sub(c, "Type",                 "Project")    # NOT Global
    _workweek(c, days, cal.hours_per_day)
    _holiday_exceptions(c, hol)




def _fixed_project_calendar(proj_el: ET.Element, oid: str, base_oid: str, name: str, days_on: tuple, proj_uid: str, holidays=()):
    c = _sub(proj_el, "Calendar")
    _sub(c, "BaseCalendarObjectId", base_oid)
    _sub(c, "HoursPerDay", "8")
    _sub(c, "HoursPerMonth", "172")
    _sub(c, "HoursPerWeek", "40")
    _sub(c, "HoursPerYear", "2000")
    _sub(c, "IsDefault", "0")
    _sub(c, "IsPersonal", "0")
    _sub(c, "Name", name)
    _sub(c, "ObjectId", oid)
    _sub(c, "ProjectObjectId", proj_uid)
    _sub(c, "Type", "Project")
    _workweek(c, days_on)
    _holiday_exceptions(c, holidays)


def _section_project_calendars(proj_el: ET.Element, proj_uid: str, project=None):
    """
    The three project calendars present in the clean P6 XML, plus a 6-day one
    when the project uses it. Holidays are attached only when present, keeping
    output unchanged for schedules that do not use them.
    """
    own = _own_calendars(project)
    if own:
        for i, cal in enumerate(own):
            _project_calendar(proj_el, cal, proj_uid, str(_PCAL_OID_START + i))
        return

    hol = frozenset()
    six = False
    if project is not None:
        for cal in (getattr(project, "calendars", None) or []):
            hol |= _cal_holidays(cal)
            six = six or _cal_is_6day(cal)
    _fixed_project_calendar(
        proj_el,
        _PCAL_7_NOHOL,
        _GCAL_7_NOHOL,
        "P7-DAY NO HOLIDAY",
        ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"),
        proj_uid,
    )
    _fixed_project_calendar(
        proj_el,
        _PCAL_5_NOHOL,
        _GCAL_5_NOHOL,
        "P5-DAY NO HOL",
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"),
        proj_uid,
    )
    _fixed_project_calendar(
        proj_el,
        _PCAL_5_HOL,
        _GCAL_5_HOL,
        "P5-DAY STANDARD HOL",
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"),
        proj_uid,
        holidays=hol,
    )
    if six:
        _fixed_project_calendar(
            proj_el,
            _PCAL_6_HOL,
            _GCAL_6_HOL,
            "P6-DAY WITH HOLIDAY",
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"),
            proj_uid,
            holidays=hol,
        )

# ── WBS block ─────────────────────────────────────────────────────────────────

_ROOT_WBS_KEY = "__project_root_wbs__"


def _root_wbs_node(project: Project) -> WBSNode:
    """
    The project's own root WBS, as P6 models it: one node named after the
    project that owns every top-level folder. Built here rather than stored on
    the project so the app's tree stays free of a folder the user never made.
    """
    name = (getattr(project, "name", None) or getattr(project, "id", None)
            or "Project").strip()
    code = (getattr(project, "id", None) or name).strip()[:60] or "PROJ"
    node = WBSNode(uid=_ROOT_WBS_KEY, name=name, code=code,
                   parent_uid=None, sequence_num=0)
    setattr(node, "_export_seq", 0)
    return node


def _display_seq(wbs: WBSNode) -> int:
    """
    Position of this folder among its siblings, as the app displays it.
    Set by _assign_display_sequence before the WBS blocks are written.
    """
    return getattr(wbs, "_export_seq", None) or wbs.sequence_num


def _assign_display_sequence(nodes: List[WBSNode]) -> None:
    """
    Number each folder by its position among its siblings, using the same
    (sequence_num, name) ordering the grid sorts by, so P6 reproduces the
    order the user arranged rather than inventing its own.
    """
    by_parent: Dict[str, List[WBSNode]] = {}
    for w in nodes:
        by_parent.setdefault(_key(getattr(w, "parent_uid", None)), []).append(w)
    for sibs in by_parent.values():
        sibs.sort(key=lambda w: (w.sequence_num, w.name or ""))
        for i, w in enumerate(sibs):
            # 10-step spacing matches P6's own convention and leaves room to
            # insert a folder later without renumbering the whole level
            setattr(w, "_export_seq", i * 10)


def _write_wbs(
    proj_el: ET.Element,
    wbs: WBSNode,
    proj_uid: str,
    wbs_oid_map: Dict[str, str],
):
    w = _sub(proj_el, "WBS")
    _nil(w, "AnticipatedFinishDate")
    _nil(w, "AnticipatedStartDate")
    _sub(w, "Code",                      wbs.code)
    _sub(w, "EarnedValueComputeType",    "Activity Percent Complete")
    _sub(w, "EarnedValueETCComputeType", "PF = 1")
    _sub(w, "EarnedValueETCUserValue",   "0.88")
    _sub(w, "EarnedValueUserPercent",    "0.06")
    _sub(w, "GUID",                      _guid())
    _sub(w, "IndependentETCLaborUnits",  "0")
    _sub(w, "IndependentETCTotalCost",   "0")
    _sub(w, "Name",                      wbs.name)
    _sub(w, "OBSObjectId",               _OBS_OID)
    _sub(w, "ObjectId",                  wbs_oid_map[_key(wbs.uid)])
    _sub(w, "OriginalBudget",            "0")

    # A top-level folder hangs off the project's hidden root WBS — the node
    # Project/WBSObjectId names but that a native export never emits. Writing
    # xsi:nil here instead made every root folder an orphan: P6 has nowhere to
    # attach it, so on import the folders come in missing or in an arbitrary
    # order, and every activity underneath them is discarded with them. That
    # is the "imports with no activities" failure.
    parent_key = _key(getattr(wbs, "parent_uid", None))
    if _key(wbs.uid) == _ROOT_WBS_KEY:
        _nil(w, "ParentObjectId")              # the root itself has no parent
    elif parent_key and parent_key in wbs_oid_map:
        _sub(w, "ParentObjectId", wbs_oid_map[parent_key])
    else:
        _sub(w, "ParentObjectId", _PROJECT_WBS_OID)

    _sub(w, "ProjectObjectId",  proj_uid)
    # Emit the position this folder actually holds among its siblings. The
    # stored sequence_num is 0 for every node on an imported schedule, which
    # left P6 free to order folders however it liked; the app itself breaks
    # that tie by name, so the export has to spell the resulting order out.
    _sub(w, "SequenceNumber",   str(_display_seq(wbs)))
    _sub(w, "Status",           "Active")
    _nil(w, "WBSCategoryObjectId")


# ── Activity block ────────────────────────────────────────────────────────────

def _write_activity(
    proj_el: ET.Element,
    act: Activity,
    proj_uid: str,
    activity_oid_map: Dict[str, str],
    wbs_oid_map: Dict[str, str],
    calendar_oid_map: Dict[str, str],
    udf_oid_map: Optional[Dict[str, str]] = None,
    project=None,
):
    cal_uid = _map_calendar_oid(getattr(act, "calendar_uid", None), calendar_oid_map)
    # An activity whose folder isn't in the export (it sat in the project-name
    # root that gets promoted away, or its wbs_uid dangles) belongs at the
    # project root, which P6 accepts. Dropping it into whichever folder
    # happened to be first in the map silently moved work between folders.
    fallback_wbs_uid = _PROJECT_WBS_OID

    status = (act.status or "Not Started").strip()
    is_complete   = status.lower() in ("completed", "complete")
    is_inprogress = status.lower() in ("in progress", "inprogress")

    pct = 100 if is_complete else int(float(act.percent_complete or 0))
    pct_frac = _pct_text(pct)
    rem = 0 if is_complete else int(float(act.remaining_duration or act.planned_duration or 0))

    a = _sub(proj_el, "Activity")

    _sub(a, "ActualDuration", _as_int_text(act.actual_duration, 0))

    if is_complete and act.actual_finish:
        _sub(a, "ActualFinishDate", _dt_activity_finish(act.actual_finish, act))
    elif is_complete and act.planned_finish:
        _sub(a, "ActualFinishDate", _dt_activity_finish(act.planned_finish, act))
    else:
        _nil(a, "ActualFinishDate")

    _sub(a, "ActualLaborCost",    "0")
    # What has actually been spent. Written as a hard zero until now, which
    # erased the history of any job already under way: the usage profile plots
    # Actual Units BEHIND the data date, so a schedule with 79 finished
    # activities came back with nothing before the data date and no clue why.
    _sub(a, "ActualLaborUnits",   _num(act.actual_labor_units))
    _sub(a, "ActualNonLaborCost", "0")
    _sub(a, "ActualNonLaborUnits","0")

    if (is_complete or is_inprogress) and act.actual_start:
        _sub(a, "ActualStartDate", _dt_activity_start(act.actual_start, act))
    elif (is_complete or is_inprogress) and act.planned_start:
        _sub(a, "ActualStartDate", _dt_activity_start(act.planned_start, act))
    else:
        _nil(a, "ActualStartDate")

    _sub(a, "ActualThisPeriodLaborCost",    "0")
    _sub(a, "ActualThisPeriodLaborUnits",   "0")
    _sub(a, "ActualThisPeriodNonLaborCost", "0")
    _sub(a, "ActualThisPeriodNonLaborUnits","0")
    _sub(a, "AtCompletionDuration",         _as_int_text(act.planned_duration, 0))
    _sub(a, "AtCompletionExpenseCost",      "0")
    _sub(a, "AtCompletionLaborCost",        "0")
    # P6's own definition, so the three agree rather than contradicting:
    # at completion is what has been spent plus what is left.
    _sub(a, "AtCompletionLaborUnits",
         _num((act.actual_labor_units or 0) + (act.remaining_labor_units or 0)))
    _sub(a, "AtCompletionNonLaborCost",     "0")
    _sub(a, "AtCompletionNonLaborUnits",    "0")
    _sub(a, "AutoComputeActuals",           "0")
    _sub(a, "CalendarObjectId",             cal_uid)
    _sub(a, "DurationPercentComplete",      "0")
    _sub(a, "DurationType",                 "Fixed Duration and Units")
    _sub(a, "EstimatedWeight",              "1")
    _nil(a, "ExpectedFinishDate")
    _nil(a, "ExternalEarlyStartDate")
    _nil(a, "ExternalLateFinishDate")
    _sub(a, "Feedback", "")

    if act.early_finish:
        _sub(a, "FinishDate", _dt_activity_finish(act.early_finish, act))
    elif act.planned_finish:
        _sub(a, "FinishDate", _dt_activity_finish(act.planned_finish, act))
    else:
        _nil(a, "FinishDate")

    _sub(a, "GUID",                    _guid())
    _sub(a, "Id",                      act.activity_id)
    _sub(a, "IsNewFeedback",           "0")
    _sub(a, "LevelingPriority",        "Normal")
    _sub(a, "Name",                    act.name)
    _sub(a, "NonLaborUnitsPercentComplete", "0")
    _sub(a, "NotesToResources", "")
    _sub(a, "ObjectId",                activity_oid_map[_key(act.uid)])
    _sub(a, "PercentComplete",         pct_frac)
    _sub(a, "PercentCompleteType",     "Physical")
    _sub(a, "PhysicalPercentComplete", pct_frac)
    _sub(a, "PlannedDuration",         _as_int_text(act.planned_duration, 0))

    if act.planned_finish:
        _sub(a, "PlannedFinishDate", _dt_activity_finish(act.planned_finish, act))
    else:
        _nil(a, "PlannedFinishDate")

    _sub(a, "PlannedLaborCost",    "0")
    _sub(a, "PlannedLaborUnits",   str(act.planned_labor_units or 0))
    _sub(a, "PlannedNonLaborCost", "0")
    _sub(a, "PlannedNonLaborUnits","0")

    if act.planned_start:
        _sub(a, "PlannedStartDate", _dt_activity_start(act.planned_start, act))
    else:
        _nil(a, "PlannedStartDate")

    if act.constraint_date:
        _sub(a, "PrimaryConstraintDate", _dt_start(act.constraint_date))
    else:
        _nil(a, "PrimaryConstraintDate")
    if act.constraint_type:
        _sub(a, "PrimaryConstraintType", act.constraint_type)
    else:
        _nil(a, "PrimaryConstraintType")

    _nil(a, "PrimaryResourceObjectId")
    _sub(a, "ProjectObjectId",  proj_uid)
    _sub(a, "RemainingDuration", str(rem))

    re_finish = act.early_finish or act.planned_finish
    re_start  = act.early_start  or act.planned_start
    if re_finish:
        _sub(a, "RemainingEarlyFinishDate", _dt_activity_finish(re_finish, act))
    else:
        _nil(a, "RemainingEarlyFinishDate")
    if re_start:
        _sub(a, "RemainingEarlyStartDate", _dt_activity_start(re_start, act))
    else:
        _nil(a, "RemainingEarlyStartDate")

    _sub(a, "RemainingLaborCost",    "0")
    # What is left to spend — the half of the profile in front of the data
    # date. Zeroing it meant an imported schedule forecast no labour at all.
    _sub(a, "RemainingLaborUnits",   _num(act.remaining_labor_units))

    if act.late_finish:
        _sub(a, "RemainingLateFinishDate", _dt_activity_finish(act.late_finish, act))
    else:
        _nil(a, "RemainingLateFinishDate")
    if act.late_start:
        _sub(a, "RemainingLateStartDate", _dt_activity_start(act.late_start, act))
    else:
        _nil(a, "RemainingLateStartDate")

    _sub(a, "RemainingNonLaborCost",    "0")
    _sub(a, "RemainingNonLaborUnits",   "0")
    _nil(a, "ResumeDate")
    _sub(a, "ReviewRequired",           "0")
    _sub(a, "ScopePercentComplete",     "0")
    _nil(a, "SecondaryConstraintDate")
    _nil(a, "SecondaryConstraintType")

    if act.early_start:
        _sub(a, "StartDate", _dt_activity_start(act.early_start, act))
    elif act.planned_start:
        _sub(a, "StartDate", _dt_activity_start(act.planned_start, act))
    else:
        _nil(a, "StartDate")

    _sub(a, "Status",               status)
    _nil(a, "SuspendDate")
    _sub(a, "Type",                 act.activity_type or "Task Dependent")
    _sub(a, "UnitsPercentComplete", "0")
    _sub(a, "WBSObjectId",          wbs_oid_map.get(_key(act.wbs_uid), fallback_wbs_uid))
    if udf_oid_map:
        _write_activity_udfs(a, act, udf_oid_map, project)


# ── Relationship block ────────────────────────────────────────────────────────

def _write_relationship(
    proj_el: ET.Element,
    rel: Relation,
    proj_uid: str,
    activity_oid_map: Dict[str, str],
    relationship_oid_map: Dict[str, str],
):
    pred_key = _key(rel.predecessor_uid)
    succ_key = _key(rel.successor_uid)
    rel_key = _key(rel.uid)

    if pred_key not in activity_oid_map or succ_key not in activity_oid_map:
        return

    r = _sub(proj_el, "Relationship")
    _nil(r, "Comments")
    _sub(r, "Lag",                         _as_int_text(rel.lag, 0))
    _sub(r, "ObjectId",                    relationship_oid_map[rel_key])
    _sub(r, "PredecessorActivityObjectId", activity_oid_map[pred_key])
    _sub(r, "PredecessorProjectObjectId",  proj_uid)
    _sub(r, "SuccessorActivityObjectId",   activity_oid_map[succ_key])
    _sub(r, "SuccessorProjectObjectId",    proj_uid)
    _sub(r, "Type",                        rel.type or "Finish to Start")


def _write_resource_assignment(
    proj_el: ET.Element,
    assignment: Any,
    assignment_oid: str,
    proj_uid: str,
    activity_oid_map: Dict[str, str],
    wbs_oid_map: Dict[str, str],
    activity_by_uid: Dict[str, Activity],
    res_oid_map: Optional[Dict[str, str]] = None,
    res_type_by_uid: Optional[Dict[str, str]] = None,
) -> bool:
    """Write a native-style ResourceAssignment if assignment data exists in the model."""
    activity_uid = _get_any(
        assignment,
        "activity_uid",
        "activity_object_id",
        "activity_objectid",
        "task_uid",
        "task_id",
        "activity_id",
    )
    activity_key = _key(activity_uid)
    if activity_key not in activity_oid_map:
        return False

    act = activity_by_uid.get(activity_key)
    wbs_key = _key(_get_any(assignment, "wbs_uid", "wbs_object_id", "wbs_objectid", default=getattr(act, "wbs_uid", None)))
    wbs_oid = wbs_oid_map.get(wbs_key, next(iter(wbs_oid_map.values()), ""))

    planned_start = _get_any(assignment, "planned_start", "start_date", default=getattr(act, "planned_start", None))
    planned_finish = _get_any(assignment, "planned_finish", "finish_date", default=getattr(act, "planned_finish", None))
    remaining_start = _get_any(assignment, "remaining_start", "remaining_start_date", default=getattr(act, "early_start", None) or planned_start)
    remaining_finish = _get_any(assignment, "remaining_finish", "remaining_finish_date", default=getattr(act, "early_finish", None) or planned_finish)

    planned_units = _get_any(assignment, "planned_units", "budgeted_units", "units", default=0)
    planned_cost = _get_any(assignment, "planned_cost", "budgeted_cost", "cost", default=planned_units)
    remaining_units = _get_any(assignment, "remaining_units", default=planned_units)
    remaining_cost = _get_any(assignment, "remaining_cost", default=planned_cost)
    remaining_duration = _get_any(assignment, "remaining_duration", default=getattr(act, "remaining_duration", None) or getattr(act, "planned_duration", 0))

    # Which resource this is actually for. With a real library the assignment
    # points at the resource it came in on; with only the synthetic stub there
    # is one to point at, and that is the old behaviour left intact.
    res_key = _key(_get_any(assignment, "resource_uid", "resource_object_id",
                            "rsrc_id", default=None))
    res_oid = (res_oid_map or {}).get(res_key) or _RES_OID
    res_type = (res_type_by_uid or {}).get(res_key) or "Nonlabor"

    # Actuals as recorded. Zeroing these threw away progress that the file
    # arrived with — on a job part-built, that is the difference between what
    # has been spent and a clean sheet.
    act_units = float(_get_any(assignment, "actual_units", default=0) or 0)
    act_cost = float(_get_any(assignment, "actual_cost", default=0) or 0)

    ra = _sub(proj_el, "ResourceAssignment")
    _sub(ra, "ActivityObjectId", activity_oid_map[activity_key])
    _sub(ra, "ActualCost", _num(act_cost))
    _nil(ra, "ActualCurve")
    _nil(ra, "ActualFinishDate")
    _sub(ra, "ActualOvertimeCost", "0")
    _sub(ra, "ActualOvertimeUnits", "0")
    _sub(ra, "ActualRegularCost", _num(act_cost))
    _sub(ra, "ActualRegularUnits", _num(act_units))
    _nil(ra, "ActualStartDate")
    _sub(ra, "ActualThisPeriodCost", "0")
    _sub(ra, "ActualThisPeriodUnits", "0")
    _sub(ra, "ActualUnits", _num(act_units))
    _sub(ra, "AtCompletionCost", str(planned_cost or 0))
    _sub(ra, "AtCompletionUnits", str(planned_units or 0))
    _nil(ra, "CostAccountObjectId")
    _sub(ra, "DrivingActivityDatesFlag", "1")
    if planned_finish:
        _sub(ra, "FinishDate", _dt_finish(planned_finish))
    else:
        _nil(ra, "FinishDate")
    _sub(ra, "GUID", _guid())
    _sub(ra, "IsCostUnitsLinked", "1")
    _sub(ra, "IsPrimaryResource", "0")
    _sub(ra, "ObjectId", assignment_oid)
    _sub(ra, "OvertimeFactor", "0")
    _sub(ra, "PlannedCost", str(planned_cost or 0))
    _nil(ra, "PlannedCurve")
    if planned_finish:
        _sub(ra, "PlannedFinishDate", _dt_finish(planned_finish))
    else:
        _nil(ra, "PlannedFinishDate")
    _sub(ra, "PlannedLag", "0")
    if planned_start:
        _sub(ra, "PlannedStartDate", _dt_start(planned_start))
    else:
        _nil(ra, "PlannedStartDate")
    _sub(ra, "PlannedUnits", str(planned_units or 0))
    _sub(ra, "PlannedUnitsPerTime", str(_get_any(assignment, "planned_units_per_time", default=0) or 0))
    _sub(ra, "PricePerUnit", _num(_get_any(assignment, "rate", "price_per_unit", default=1) or 1))
    _sub(ra, "Proficiency", "3 - Skilled")
    _sub(ra, "ProjectObjectId", proj_uid)
    _sub(ra, "RateSource", "Resource")
    _sub(ra, "RateType", "Price / Unit")
    _sub(ra, "RemainingCost", str(remaining_cost or 0))
    _nil(ra, "RemainingCurve")
    _sub(ra, "RemainingDuration", _as_int_text(remaining_duration, 0))
    if remaining_finish:
        _sub(ra, "RemainingFinishDate", _dt_finish(remaining_finish))
    else:
        _nil(ra, "RemainingFinishDate")
    _sub(ra, "RemainingLag", "0")
    if remaining_start:
        _sub(ra, "RemainingStartDate", _dt_start(remaining_start))
    else:
        _nil(ra, "RemainingStartDate")
    _sub(ra, "RemainingUnits", str(remaining_units or 0))
    _sub(ra, "RemainingUnitsPerTime", str(_get_any(assignment, "remaining_units_per_time", default=0) or 0))
    _nil(ra, "ResourceCurveObjectId")
    _sub(ra, "ResourceObjectId", res_oid)
    _sub(ra, "ResourceType", res_type)
    _nil(ra, "RoleObjectId")
    if planned_start:
        _sub(ra, "StartDate", _dt_start(planned_start))
    else:
        _nil(ra, "StartDate")
    _sub(ra, "UnitsPercentComplete", "0")
    _sub(ra, "WBSObjectId", wbs_oid)
    return True


def _write_schedule_options(proj_el: ET.Element, proj_uid: str):
    so = _sub(proj_el, "ScheduleOptions")
    _sub(so, "CalculateFloatBasedOnFinishDate", "1")
    _sub(so, "ComputeTotalFloatType", "Finish Float = Late Finish - Early Finish")
    _sub(so, "CriticalActivityFloatThreshold", "0")
    _sub(so, "CriticalActivityPathType", "Longest Path")
    _sub(so, "ExternalProjectPriorityLimit", "5")
    _sub(so, "IgnoreOtherProjectRelationships", "0")
    _sub(so, "IncludeExternalResAss", "0")
    _sub(so, "LevelAllResources", "1")
    _sub(so, "LevelWithinFloat", "0")
    _sub(so, "MakeOpenEndedActivitiesCritical", "0")
    _sub(so, "MaximumMultipleFloatPaths", "10")
    _sub(so, "MinFloatToPreserve", "1")
    _sub(so, "MultipleFloatPathsEnabled", "0")
    _sub(so, "MultipleFloatPathsEndingActivityObjectId", "")
    _sub(so, "MultipleFloatPathsUseTotalFloat", "1")
    _sub(so, "OutOfSequenceScheduleType", "Retained Logic")
    _sub(so, "OverAllocationPercentage", "25")
    _sub(so, "PreserveScheduledEarlyAndLateDates", "1")
    _sub(so, "PriorityList", "(0||priority_type(alternate_sort_type|ASC_BY_FIELD|source_field|priority_type|alternate_sort_enabled|Y|sort_type|ASC)())")
    _sub(so, "ProjectObjectId", proj_uid)
    _sub(so, "RelationshipLagCalendar", "Predecessor Activity Calendar")
    _sub(so, "ResourceList", "")
    _sub(so, "StartToStartLagCalculationType", "1")
    _sub(so, "UseExpectedFinishDates", "1")


# ── Main writer ───────────────────────────────────────────────────────────────

def _write_p6_xml_impl(project: Project, output_path: str,
                       p6_version: Optional[str] = None,
                       include_udfs: bool = True) -> str:
    """
    Serialize a Project to a P6-importable XML file.

    Integrity rule:
    - Preserve source activity IDs/names/dates/logic.
    - Remap only XML ObjectId values into safe, sequential P6-style ranges.
    """
    proj_uid = _safe_project_object_id(getattr(project, "uid", None))

    # Build original-id lookup sets using source/app ids.
    all_wbs_nodes = list(project.wbs_nodes or [])
    all_wbs_uids = {_key(w.uid) for w in all_wbs_nodes}
    act_uids = {_key(a.uid) for a in project.activities}

    # Native P6 XML uses Project.WBSObjectId for a hidden project-root WBS.
    # That hidden root is NOT also emitted as a <WBS> block.
    # If the app model includes a project-name root WBS, skip it and promote its children to top-level.
    project_id_key = _key(getattr(project, "id", None)).strip().lower()
    project_name_key = _key(getattr(project, "name", None)).strip().lower()
    root_like_wbs_keys = set()
    for w in all_wbs_nodes:
        is_top = not _key(getattr(w, "parent_uid", None)) or _key(getattr(w, "parent_uid", None)) not in all_wbs_uids
        w_code = _key(getattr(w, "code", None)).strip().lower()
        w_name = _key(getattr(w, "name", None)).strip().lower()
        has_children = any(_key(getattr(child, "parent_uid", None)) == _key(getattr(w, "uid", None)) for child in all_wbs_nodes)
        if is_top and has_children and (w_code == project_id_key or w_name == project_name_key):
            root_like_wbs_keys.add(_key(getattr(w, "uid", None)))

    export_wbs_nodes = [w for w in all_wbs_nodes if _key(getattr(w, "uid", None)) not in root_like_wbs_keys]
    wbs_uids = {_key(w.uid) for w in export_wbs_nodes}

    # Sort exported WBS: roots first, then children so parent precedes child.
    def _wbs_depth(w: WBSNode) -> int:
        depth, uid = 0, _key(w.parent_uid)
        seen = set()
        while uid and uid in wbs_uids and uid not in seen:
            seen.add(uid)
            depth += 1
            parent = next((x for x in export_wbs_nodes if _key(x.uid) == uid), None)
            uid = _key(parent.parent_uid) if parent else ""
        return depth

    _assign_display_sequence(export_wbs_nodes)
    sorted_wbs = sorted(export_wbs_nodes,
                        key=lambda w: (_wbs_depth(w), _display_seq(w), w.name or ""))
    valid_relations = [
        rel for rel in project.relations
        if _key(rel.predecessor_uid) in act_uids and _key(rel.successor_uid) in act_uids
    ]
    assignments = _project_assignments(project)

    # Safe ObjectId maps. These are the main hardening step.
    wbs_oid_map = _build_oid_map(sorted_wbs, "uid", _WBS_OID_START)
    activity_oid_map = _build_oid_map(project.activities, "uid", _ACTIVITY_OID_START)
    relationship_oid_map = _build_oid_map(valid_relations, "uid", _RELATIONSHIP_OID_START)
    calendar_oid_map = _build_calendar_oid_map(project)
    activity_by_uid = {_key(a.uid): a for a in project.activities}

    root_wbs_uid = _PROJECT_WBS_OID

    # Declare the schema version the target P6 expects. P6 will not open a
    # file built for a newer release than itself.
    ver = normalize_p6_version(p6_version)
    ns = p6_namespace(ver)
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}APIBusinessObjects")
    root.set(_XSI_SL,
             f"{ns} "
             f"http://xmlns.oracle.com/Primavera/P6Professional/V{ver}/API/p6apibo.xsd")

    # Enterprise envelope.
    _section_display_currency(root)
    _section_currency(root)
    _section_udf_type(root, project, include_udfs)
    _section_obs(root)
    _section_global_calendars(root, project)

    # The resource library, when the import brought one. Each resource is
    # written as itself and the assignments below point at the right one —
    # before this, everything was pinned to a single synthetic "Costs MLCB"
    # line, so a file that arrived with six crews came back with six
    # assignments to a nonlabor account nobody had ever created.
    #
    # With assignments but no library (an app-built schedule, or an XER whose
    # RSRC table was not in the file) the original stub still stands: it is
    # what makes those assignments importable at all.
    res_oid_map: Dict[str, str] = {}
    if getattr(project, "resources", None):
        res_oid_map = _section_real_resources(root, project)
    elif assignments:
        _section_resource(root)
        _section_resource_rate(root)

    _section_fpt(root)

    proj_el = _sub(root, "Project")

    _sub(proj_el, "ActivityDefaultActivityType",           "Task Dependent")
    _sub(proj_el, "ActivityDefaultCalendarObjectId",       _GCAL_OID)
    _nil(proj_el, "ActivityDefaultCostAccountObjectId")
    _sub(proj_el, "ActivityDefaultDurationType",           "Fixed Duration and Units")
    _sub(proj_el, "ActivityDefaultPercentCompleteType",    "Physical")
    _sub(proj_el, "ActivityDefaultPricePerUnit",           "1")
    _sub(proj_el, "ActivityIdBasedOnSelectedActivity",     "1")
    _sub(proj_el, "ActivityIdIncrement",                   "10")
    _sub(proj_el, "ActivityIdPrefix",                      "A")
    _sub(proj_el, "ActivityIdSuffix",                      "1000")
    _sub(proj_el, "ActivityPercentCompleteBasedOnActivitySteps", "1")
    _sub(proj_el, "AddActualToRemaining",                  "0")
    _sub(proj_el, "AddedBy",                               "ADMIN")
    _sub(proj_el, "AllowNegativeActualUnitsFlag",          "0")
    _sub(proj_el, "AllowStatusReview",                     "0")
    _nil(proj_el, "AnnualDiscountRate")
    _nil(proj_el, "AnticipatedFinishDate")
    _nil(proj_el, "AnticipatedStartDate")
    _sub(proj_el, "AssignmentDefaultDrivingFlag",          "1")
    _sub(proj_el, "AssignmentDefaultRateType",             "Price / Unit")
    _sub(proj_el, "CheckOutStatus",                        "0")
    _sub(proj_el, "CostQuantityRecalculateFlag",           "0")
    _sub(proj_el, "CriticalActivityFloatLimit",            "0")
    _sub(proj_el, "CriticalActivityPathType",              "Longest Path")
    _nil(proj_el, "CurrentBaselineProjectObjectId")

    if project.data_date:
        _sub(proj_el, "DataDate", _dt_start(project.data_date))
    else:
        _nil(proj_el, "DataDate")

    _sub(proj_el, "DefaultPriceTimeUnits",                 "Hour")
    _nil(proj_el, "DiscountApplicationPeriod")
    _sub(proj_el, "EarnedValueComputeType",                "Activity Percent Complete")
    _sub(proj_el, "EarnedValueETCComputeType",             "PF = 1")
    _sub(proj_el, "EarnedValueETCUserValue",               "0.88")
    _sub(proj_el, "EarnedValueUserPercent",                "0.06")
    _sub(proj_el, "EnableSummarization",                   "1")
    _sub(proj_el, "FinancialPeriodTemplateId",             _FPT_OID)
    _sub(proj_el, "FiscalYearStartMonth",                  "1")
    _sub(proj_el, "GUID",                                  _guid())

    id_el = _sub(proj_el, "Id")
    id_el.text = project.id or (project.name[:20] if project.name else "PROJ")

    _sub(proj_el, "IndependentETCLaborUnits",              "0")
    _sub(proj_el, "IndependentETCTotalCost",               "0")
    _nil(proj_el, "LastFinancialPeriodObjectId")
    _sub(proj_el, "LevelingPriority",                      "10")
    _sub(proj_el, "LinkActualToActualThisPeriod",          "1")
    _sub(proj_el, "LinkPercentCompleteWithActual",         "1")
    _sub(proj_el, "LinkPlannedAndAtCompletionFlag",        "1")

    if project.must_finish_by:
        _sub(proj_el, "MustFinishByDate", _dt_finish(project.must_finish_by))
    else:
        _nil(proj_el, "MustFinishByDate")

    name_el = _sub(proj_el, "Name")
    name_el.text = project.name

    _sub(proj_el, "OBSObjectId",         _OBS_OID)
    _sub(proj_el, "ObjectId",            proj_uid)
    _sub(proj_el, "OriginalBudget",      "0")
    _sub(proj_el, "ParentEPSObjectId",   _EPS_OID)

    if project.planned_start:
        _sub(proj_el, "PlannedStartDate", _dt_start(project.planned_start))
    else:
        _nil(proj_el, "PlannedStartDate")

    _sub(proj_el, "PrimaryResourcesCanMarkActivitiesAsCompleted", "1")
    _nil(proj_el, "ProjectForecastStartDate")
    _sub(proj_el, "ResetPlannedToRemainingFlag",           "0")
    _sub(proj_el, "ResourceCanBeAssignedToSameActivityMoreThanOnce", "1")
    _sub(proj_el, "ResourcesCanAssignThemselvesToActivities", "1")

    if project.must_finish_by:
        _sub(proj_el, "ScheduledFinishDate", _dt_finish(project.must_finish_by))
    else:
        _nil(proj_el, "ScheduledFinishDate")

    _sub(proj_el, "Status",                project.status_code or "Active")
    _sub(proj_el, "StrategicPriority",     "500")
    _sub(proj_el, "SummarizeToWBSLevel",   "2")
    _sub(proj_el, "SummaryLevel",          "Assignment Level")
    _sub(proj_el, "UseProjectBaselineForEarnedValue", "1")
    _sub(proj_el, "WBSCodeSeparator",      ".")
    _sub(proj_el, "WBSObjectId",           root_wbs_uid)
    _nil(proj_el, "WebSiteRootDirectory")
    _nil(proj_el, "WebSiteURL")

    # Always write all three project calendars from the clean XML pattern.
    _section_project_calendars(proj_el, proj_uid, project)

    # The project's root WBS. Project/WBSObjectId names it and every top-level
    # folder is its child, so it has to exist as a <WBS> element — a native
    # export includes it. Emitting only the folders left the whole tree hanging
    # off an ObjectId that appeared nowhere in the file: P6's XML import found
    # an unresolvable parent, refused the project outright, and a more lenient
    # reader created the folders but dropped every activity under them.
    _write_wbs(proj_el, _root_wbs_node(project), proj_uid,
               {_ROOT_WBS_KEY: _PROJECT_WBS_OID})

    for wbs in sorted_wbs:
        _write_wbs(proj_el, wbs, proj_uid, wbs_oid_map)

    udf_oid_map = _udf_oid_map(project) if include_udfs else {}
    for act in project.activities:
        _write_activity(proj_el, act, proj_uid, activity_oid_map, wbs_oid_map,
                        calendar_oid_map, udf_oid_map, project)

    for rel in valid_relations:
        _write_relationship(proj_el, rel, proj_uid, activity_oid_map, relationship_oid_map)

    # If the app carries assignments, export them with safe 83400+ ObjectIds.
    # If not, no orphan Resource/ResourceRate blocks are written above.
    res_type_by_uid = {_key(r.uid): (r.type if r.type in _RES_TYPES else "Labor")
                       for r in (getattr(project, "resources", None) or [])}
    for idx, assignment in enumerate(assignments):
        _write_resource_assignment(
            proj_el,
            assignment,
            str(_ASSIGNMENT_OID_START + idx),
            proj_uid,
            activity_oid_map,
            wbs_oid_map,
            activity_by_uid,
            res_oid_map,
            res_type_by_uid,
        )

    # Native export includes ScheduleOptions after relationships/assignments.
    _write_schedule_options(proj_el, proj_uid)

    # Indent in place rather than serializing, re-parsing the whole document
    # with minidom, and writing it a second time. On a 2,776-activity export
    # that round trip parsed 215,601 elements twice and cost about five
    # seconds of solid CPU — which, on a tenth of a core, is nearly a minute
    # of the machine being busy every time a schedule is saved.
    #
    # ET.indent produces the same two-space layout; the only difference is
    # that it does not emit a declaration, which is written by hand below
    # exactly as it was before.
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode", xml_declaration=False)
    output = '<?xml version="1.0" encoding="utf-8"?>\n' + body

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    return output_path



def write_p6_xml(
    project: Project,
    output_path: str,
    target_profile: Optional[Dict[str, Any]] = None,
    seed_xer_path: Optional[str] = None,
    seed_xml_path: Optional[str] = None,
    seed_project_id: Optional[str] = None,
    p6_version: Optional[str] = None,
    include_udfs: bool = True,
) -> str:
    """
    Serialize a Project to P6 XML.

    p6_version selects the schema version declared in the file (e.g. "21.12").
    P6 opens files built for its own release or an older one and silently
    refuses anything newer, so this must not exceed the target install's
    version. Accepts a full build string like "21.12.9.45067".

    Default behavior preserves the working IDs from your P6 database.
    For another user's P6 instance, pass either:
      - seed_xml_path: a small known-good XML export from that user's P6, or
      - seed_xer_path: a seed XER from that user's P6, or
      - target_profile: explicit environment IDs.

    Example:
        write_p6_xml(project, "out.xml", seed_xer_path="target_seed.xer")

    The writer remains project-agnostic: project data comes from `project`; only
    P6 environment references come from the seed/profile.
    """
    profile = build_target_profile(
        target_profile=target_profile,
        seed_xer_path=seed_xer_path,
        seed_xml_path=seed_xml_path,
        seed_project_id=seed_project_id,
    )
    warnings = validate_target_profile(profile)
    if warnings:
        # Do not block export because the existing defaults may be intentional.
        # Attach warnings to the function for callers/UI preflight display.
        write_p6_xml.last_warnings = warnings
    else:
        write_p6_xml.last_warnings = []

    with _TargetProfileContext(profile):
        return _write_p6_xml_impl(project, output_path, p6_version=p6_version,
                                  include_udfs=include_udfs)


write_p6_xml.last_warnings = []
