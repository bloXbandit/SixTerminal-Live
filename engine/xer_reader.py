"""
xer_reader.py — Parse a Primavera P6 XER file into the internal schedule model.

XER format: tab-delimited flat file, each section starts with %T <TABLE_NAME>,
followed by %F <field names>, then %R <row data> lines.

This reader is read-only — it produces a Project object for the edit engine.
All writes go through xml_writer.py (P6 XML output).
"""

import re
from typing import Dict, List, Optional, Tuple
from .schedule_model import Project, WBSNode, Activity, Relation, Calendar


def _parse_xer_tables(path: str) -> Dict[str, List[Dict]]:
    """Parse XER file into {table_name: [row_dicts]}."""
    tables: Dict[str, List[Dict]] = {}
    current_table: Optional[str] = None
    current_fields: List[str] = []

    with open(path, "r", encoding="latin-1", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if line.startswith("%T"):
                current_table = line[2:].strip()
                tables[current_table] = []
                current_fields = []
            elif line.startswith("%F") and current_table:
                current_fields = line[2:].strip().split("\t")
            elif line.startswith("%R") and current_table and current_fields:
                values = line[2:].strip().split("\t")
                row = dict(zip(current_fields, values))
                tables[current_table].append(row)
    return tables


def _safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val) if val and val.strip() else default
    except (ValueError, TypeError):
        return default


def _iso_date(val: str) -> Optional[str]:
    """Convert P6 date string (YYYY-MM-DD HH:MM or YYYY-MM-DD) to ISO date."""
    if not val or not val.strip():
        return None
    val = val.strip()
    # P6 stores as "2026-03-15 00:00" — take date part only
    return val[:10] if len(val) >= 10 else val


def load_xer(path: str) -> Project:
    """
    Parse an XER file and return a Project object.
    Raises ValueError if the file cannot be parsed.
    """
    tables = _parse_xer_tables(path)

    # --- Project ---
    proj_rows = tables.get("PROJECT", [])
    if not proj_rows:
        raise ValueError(f"No PROJECT table found in {path}")
    pr = proj_rows[0]  # Use first project (XER can contain multiple)

    project = Project(
        uid=pr.get("proj_id", ""),
        name=pr.get("proj_short_name", pr.get("proj_name", "Unknown")),
        id=pr.get("proj_short_name", ""),
        data_date=_iso_date(pr.get("last_recalc_date", "")),
        planned_start=_iso_date(pr.get("plan_start_date", "")),
        must_finish_by=_iso_date(pr.get("scd_end_date", "")),
    )

    # --- Calendars ---
    for row in tables.get("CALENDAR", []):
        project.calendars.append(Calendar(
            uid=row.get("clndr_id", ""),
            name=row.get("clndr_name", ""),
            hours_per_day=_safe_float(row.get("day_hr_cnt", "8"), 8.0),
            hours_per_week=_safe_float(row.get("week_hr_cnt", "40"), 40.0),
            hours_per_month=_safe_float(row.get("month_hr_cnt", "176"), 176.0),
            hours_per_year=_safe_float(row.get("year_hr_cnt", "2080"), 2080.0),
        ))

    # --- WBS ---
    proj_uid = project.uid
    for row in tables.get("PROJWBS", []):
        if row.get("proj_id", "") != proj_uid:
            continue
        project.wbs_nodes.append(WBSNode(
            uid=row.get("wbs_id", ""),
            name=row.get("wbs_name", ""),
            code=row.get("wbs_short_name", row.get("wbs_name", "")),
            parent_uid=row.get("parent_wbs_id", None) or None,
            sequence_num=int(_safe_float(row.get("seq_num", "0"))),
        ))

    # --- Activities ---
    status_map = {
        "TK_NotStart": "Not Started",
        "TK_Active": "In Progress",
        "TK_Complete": "Completed",
    }
    type_map = {
        "TT_Task": "Task Dependent",
        "TT_Rsrc": "Resource Dependent",
        "TT_LOE": "Level of Effort",
        "TT_WBS": "WBS Summary",
        "TT_Mile": "Start Milestone",
        "TT_FinMile": "Finish Milestone",
    }

    for row in tables.get("TASK", []):
        if row.get("proj_id", "") != proj_uid:
            continue
        tf_raw = _safe_float(row.get("total_float_hr_cnt", "0"))
        ff_raw = _safe_float(row.get("free_float_hr_cnt", "0"))
        project.activities.append(Activity(
            uid=row.get("task_id", ""),
            activity_id=row.get("task_code", ""),
            name=row.get("task_name", ""),
            wbs_uid=row.get("wbs_id", ""),
            calendar_uid=row.get("clndr_id", ""),
            activity_type=type_map.get(row.get("task_type", ""), "Task Dependent"),
            status=status_map.get(row.get("status_code", ""), "Not Started"),
            planned_duration=_safe_float(row.get("target_drtn_hr_cnt", "0")),
            remaining_duration=_safe_float(row.get("remain_drtn_hr_cnt", "0")),
            actual_duration=_safe_float(row.get("act_drtn_hr_cnt", "0")),
            percent_complete=_safe_float(row.get("phys_complete_pct", row.get("complete_pct", "0"))),
            planned_start=_iso_date(row.get("target_start_date", "")),
            planned_finish=_iso_date(row.get("target_end_date", "")),
            actual_start=_iso_date(row.get("act_start_date", "")),
            actual_finish=_iso_date(row.get("act_end_date", "")),
            early_start=_iso_date(row.get("early_start_date", "")),
            early_finish=_iso_date(row.get("early_end_date", "")),
            late_start=_iso_date(row.get("late_start_date", "")),
            late_finish=_iso_date(row.get("late_end_date", "")),
            total_float=tf_raw,
            free_float=ff_raw,
            is_critical=row.get("driving_path_flag", "N") == "Y",
            is_longest_path=row.get("longest_path_flag", "N") == "Y",
        ))

    # --- Relations ---
    rel_type_map = {
        "PR_FS": "Finish to Start",
        "PR_SS": "Start to Start",
        "PR_FF": "Finish to Finish",
        "PR_SF": "Start to Finish",
    }
    for row in tables.get("TASKPRED", []):
        if row.get("proj_id", "") != proj_uid:
            continue
        project.relations.append(Relation(
            uid=row.get("task_pred_id", ""),
            predecessor_uid=row.get("pred_task_id", ""),
            successor_uid=row.get("task_id", ""),
            type=rel_type_map.get(row.get("pred_type", "PR_FS"), "Finish to Start"),
            lag=_safe_float(row.get("lag_hr_cnt", "0")),
        ))

    project.build_lookups()
    return project
