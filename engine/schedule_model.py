"""
schedule_model.py — Internal data model for Six Terminal Live.

All parsers (XER, P6 XML) normalize their output into these dataclasses.
The edit engine operates on this model.
The XML writer serializes this model back to valid P6 XML.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Calendar:
    uid: str
    name: str
    hours_per_day: float = 8.0
    hours_per_week: float = 40.0
    hours_per_month: float = 176.0
    hours_per_year: float = 2080.0
    type: str = "Global"  # Global | Project | Resource


@dataclass
class WBSNode:
    uid: str
    name: str
    code: str
    parent_uid: Optional[str] = None
    sequence_num: int = 0


@dataclass
class Activity:
    uid: str
    activity_id: str          # User-visible code e.g. "A1000"
    name: str
    wbs_uid: str
    calendar_uid: str
    activity_type: str = "Task Dependent"  # Task Dependent | Resource Dependent | Level of Effort | WBS Summary | Start Milestone | Finish Milestone
    status: str = "Not Started"            # Not Started | In Progress | Completed
    planned_duration: float = 0.0          # hours
    remaining_duration: float = 0.0        # hours
    actual_duration: float = 0.0           # hours
    percent_complete: float = 0.0
    planned_start: Optional[str] = None    # ISO date string
    planned_finish: Optional[str] = None
    actual_start: Optional[str] = None
    actual_finish: Optional[str] = None
    early_start: Optional[str] = None
    early_finish: Optional[str] = None
    late_start: Optional[str] = None
    late_finish: Optional[str] = None
    total_float: Optional[float] = None    # hours
    free_float: Optional[float] = None     # hours
    is_critical: bool = False
    is_longest_path: bool = False
    constraint_type: Optional[str] = None
    constraint_date: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Relation:
    uid: str
    predecessor_uid: str
    successor_uid: str
    type: str = "Finish to Start"   # Finish to Start | Start to Start | Finish to Finish | Start to Finish
    lag: float = 0.0                # hours


@dataclass
class Project:
    uid: str
    name: str
    id: str                         # Short project code e.g. "MTJ-UP08"
    data_date: Optional[str] = None
    planned_start: Optional[str] = None
    must_finish_by: Optional[str] = None
    status_code: str = "Active"
    calendars: List[Calendar] = field(default_factory=list)
    wbs_nodes: List[WBSNode] = field(default_factory=list)
    activities: List[Activity] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)

    # Lookup helpers (populated after load)
    _activity_by_uid: Dict[str, Activity] = field(default_factory=dict, repr=False)
    _activity_by_id: Dict[str, Activity] = field(default_factory=dict, repr=False)
    _wbs_by_uid: Dict[str, WBSNode] = field(default_factory=dict, repr=False)

    def build_lookups(self):
        """Build fast-access lookup dicts after loading."""
        self._activity_by_uid = {a.uid: a for a in self.activities}
        self._activity_by_id = {a.activity_id: a for a in self.activities}
        self._wbs_by_uid = {w.uid: w for w in self.wbs_nodes}

    def get_activity(self, uid: Optional[str] = None, activity_id: Optional[str] = None) -> Optional[Activity]:
        if uid:
            return self._activity_by_uid.get(uid)
        if activity_id:
            return self._activity_by_id.get(activity_id)
        return None

    def get_wbs(self, uid: str) -> Optional[WBSNode]:
        return self._wbs_by_uid.get(uid)

    def summary(self) -> str:
        return (
            f"Project: {self.name} ({self.id})\n"
            f"  Data Date: {self.data_date}\n"
            f"  Activities: {len(self.activities)}\n"
            f"  WBS Nodes: {len(self.wbs_nodes)}\n"
            f"  Relations: {len(self.relations)}\n"
            f"  Calendars: {len(self.calendars)}"
        )
