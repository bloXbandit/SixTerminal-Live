# -*- coding: utf-8 -*-
"""
resource_restore.py — putting the labour back after it was lost.

WHY THIS EXISTS
  This app used to drop resource assignments on import and write every
  activity's actual and remaining labour out as a hard zero. Both are fixed
  now, but the files that went through in the meantime came out stripped, and
  a schedule cannot be un-round-tripped. What is left is a job with dates,
  progress and logic intact and no labour behind any of it.

  Recovery has two sources and they are not equally good:

    A DONOR schedule — an earlier export that still has the assignments. This
    is real data. It wins wherever it reaches.

    A CREW COUNT on the activity — "Number of Electricians" and its like,
    typed by hand into a UDF. It is not a resource assignment and P6's usage
    profile cannot read it, but it is the headcount somebody actually meant,
    and units = crew x duration turns it into one.

  So the rule is: keep what is already there, take the donor's where it
  reaches, derive the rest from the crew count, and say plainly which
  activities got which — and which got nothing, because those are the ones
  still needing a human.

WHAT IT WILL NOT DO
  It does not overwrite an activity that already has an assignment unless it
  is told to. Re-running it is therefore safe: the second pass fills only
  what the first could not.

  It does not invent a crew count. An activity with no donor row and no crew
  number is reported as uncovered rather than given a default of one, which
  would look like data and be a guess.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .schedule_model import Activity, Project, Resource, ResourceAssignment

# The P6 statuses this app uses. Progress decides how a derived budget splits
# between spent and remaining.
_DONE, _WIP = "Completed", "In Progress"

# What a derived crew becomes, when the donor has no resource to borrow.
DEFAULT_RESOURCE_ID = "ELEC"
DEFAULT_RESOURCE_NAME = "Electrician"

# Field names a crew count is usually typed into. Checked in order; the first
# that any activity actually carries wins, so a job that calls it something
# else needs the name passed in rather than a code change.
CREW_FIELD_HINTS = ("number of electricians", "electricians", "crew size",
                    "crew", "manpower", "headcount", "men")


def crew_field_of(project: Project) -> Optional[str]:
    """Whichever UDF on this job holds a headcount, or None."""
    seen: Dict[str, int] = {}
    for a in project.activities:
        for k, v in (getattr(a, "udfs", None) or {}).items():
            if str(v).strip():
                seen[k] = seen.get(k, 0) + 1
    if not seen:
        return None
    # Whole words only. A plain substring test matched "men" inside "COMMENTS"
    # and picked the notes field as the headcount — which then derived hours
    # from a sentence, or from nothing, depending on the row.
    for hint in CREW_FIELD_HINTS:
        pat = re.compile(r"\b" + re.escape(hint) + r"\b", re.I)
        for k in seen:
            if pat.search(k):
                return k
    # Nothing recognised by name: take a field whose values are all numbers,
    # since a headcount is, and a comments field is not.
    for k, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        vals = [str((a.udfs or {}).get(k, "")).strip()
                for a in project.activities if (a.udfs or {}).get(k)]
        if vals and all(_as_num(v) is not None for v in vals):
            return k
    return None


def _as_num(v: Any) -> Optional[float]:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _crew_of(act: Activity, field: Optional[str]) -> Optional[float]:
    if not field:
        return None
    return _as_num((getattr(act, "udfs", None) or {}).get(field))


def _has_labour(act: Activity, assigned: bool) -> bool:
    """Whether this activity already carries labour worth keeping."""
    return assigned or any((getattr(act, f, 0) or 0) > 0 for f in
                           ("planned_labor_units", "actual_labor_units",
                            "remaining_labor_units"))


def _split(units: float, act: Activity) -> Tuple[float, float]:
    """
    How a budget divides between spent and left to spend.

    This is what P6 does with Auto Compute Actuals on: the split follows the
    activity's progress rather than being typed. Complete means all of it is
    behind the data date, not started means all of it is in front, and in
    progress is cut at the percentage. Percent complete is a FRACTION here,
    not 0..100 — multiplying by a whole number would put a hundred times the
    hours on the wrong side of the data date.
    """
    if act.status == _DONE:
        return units, 0.0
    if act.status == _WIP:
        pct = min(max(float(act.percent_complete or 0), 0.0), 1.0)
        spent = round(units * pct, 2)
        return spent, round(units - spent, 2)
    return 0.0, units


# ── what this schedule has and has not ───────────────────────────────────────

def audit(project: Project, crew_field: Optional[str] = None) -> Dict[str, Any]:
    """
    A count of where the labour stands, before anything is changed.

    The point is to answer "how much was lost and how much can be got back"
    in one screen, rather than by scrolling a grid of two thousand rows.
    """
    field = crew_field or crew_field_of(project)
    assigned = {a.activity_uid for a in (project.resource_assignments or [])}
    acts = [a for a in project.activities
            if "Milestone" not in (a.activity_type or "")]

    has_assign = [a for a in acts if a.uid in assigned]
    has_budget = [a for a in acts if (a.planned_labor_units or 0) > 0]
    has_actual = [a for a in acts if (a.actual_labor_units or 0) > 0]
    has_remain = [a for a in acts if (a.remaining_labor_units or 0) > 0]
    has_crew = [a for a in acts if _crew_of(a, field)]

    # The fingerprint of the loss: a budget number with no assignment behind
    # it. P6 keeps the activity total as a roll-up of its assignments, so a
    # total with nothing under it is a roll-up whose rows were taken away.
    orphaned = [a for a in has_budget if a.uid not in assigned]
    started = [a for a in acts if a.actual_start]

    return {
        "activities": len(acts),
        "crew_field": field,
        "with_assignment": len(has_assign),
        "with_budget": len(has_budget),
        "with_actual": len(has_actual),
        "with_remaining": len(has_remain),
        "with_crew_count": len(has_crew),
        "orphaned_budget": len(orphaned),
        "orphaned_sample": [a.activity_id for a in orphaned[:10]],
        "started": len(started),
        # The one that matters: work already under way carrying no record of
        # what it cost. This is exactly what an emptied usage profile is.
        "started_without_actual": len([a for a in started
                                       if not (a.actual_labor_units or 0)]),
        "recoverable_from_crew": len([a for a in has_crew
                                      if not _has_labour(a, a.uid in assigned)]),
        "resources": len(project.resources or []),
        "assignments": len(project.resource_assignments or []),
    }


# ── the plan, then the doing ─────────────────────────────────────────────────

def plan(target: Project,
         donor: Optional[Project] = None,
         crew_field: Optional[str] = None,
         overwrite: bool = False,
         hours_per_day: float = 8.0) -> Dict[str, Any]:
    """
    Where each activity's labour would come from, without changing anything.

    Four outcomes per activity, and the caller should see all four before
    agreeing to any of them:

      keep    — already has an assignment; left alone unless overwrite
      donor   — the other schedule has one for this activity id
      crew    — derived from the headcount on the activity
      none    — neither reaches it; a person has to decide
    """
    field = crew_field or crew_field_of(target)
    assigned = {a.activity_uid for a in (target.resource_assignments or [])}

    donor_by_aid: Dict[str, List[ResourceAssignment]] = {}
    donor_res: Dict[str, Resource] = {}
    if donor is not None:
        by_uid = {a.uid: a for a in donor.activities}
        donor_res = {r.uid: r for r in (donor.resources or [])}
        for ra in (donor.resource_assignments or []):
            act = by_uid.get(ra.activity_uid)
            if act:
                donor_by_aid.setdefault(act.activity_id, []).append(ra)
        # A donor that carries roll-ups but lost its own assignments is still
        # worth something: the activity totals survived the same trip.
        for act in donor.activities:
            if act.activity_id in donor_by_aid:
                continue
            if (act.planned_labor_units or 0) > 0:
                donor_by_aid.setdefault(act.activity_id, [])

    rows = []
    for a in target.activities:
        if "Milestone" in (a.activity_type or ""):
            continue
        already = a.uid in assigned
        if already and not overwrite:
            rows.append({"activity_id": a.activity_id, "source": "keep",
                         "units": None})
            continue
        from_donor = donor_by_aid.get(a.activity_id)
        if from_donor is not None:
            units = sum(r.planned_units or 0 for r in from_donor)
            if not units and donor is not None:
                d = donor.get_activity(activity_id=a.activity_id)
                units = float(getattr(d, "planned_labor_units", 0) or 0) if d else 0
            if units:
                rows.append({"activity_id": a.activity_id, "source": "donor",
                             "units": round(units, 2),
                             "assignments": len(from_donor)})
                continue
        crew = _crew_of(a, field)
        if crew:
            units = crew * float(a.planned_duration or 0)
            rows.append({"activity_id": a.activity_id, "source": "crew",
                         "units": round(units, 2), "crew": crew})
            continue
        rows.append({"activity_id": a.activity_id, "source": "none",
                     "units": None})

    tally: Dict[str, int] = {}
    hours: Dict[str, float] = {}
    for r in rows:
        tally[r["source"]] = tally.get(r["source"], 0) + 1
        hours[r["source"]] = hours.get(r["source"], 0.0) + (r["units"] or 0)
    return {
        "crew_field": field,
        "counts": tally,
        "hours": {k: round(v, 1) for k, v in hours.items()},
        "rows": rows,
        "uncovered": [r["activity_id"] for r in rows if r["source"] == "none"][:200],
        "donor_resources": len(donor_res),
    }


def restore(target: Project,
            donor: Optional[Project] = None,
            crew_field: Optional[str] = None,
            overwrite: bool = False,
            resource_id: str = DEFAULT_RESOURCE_ID,
            resource_name: str = DEFAULT_RESOURCE_NAME,
            hours_per_day: float = 8.0) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Put the labour back, and report exactly what came from where.

    Donor first because it is real; crew count second because it is derived;
    nothing at all for an activity that has neither, because a default of one
    electrician would look like data.

    Both halves are written: the assignment, which is what P6's usage profile
    reads, and the activity roll-up, which is what the grid shows. Writing one
    without the other is how the schedule ended up with roll-ups that had
    nothing underneath them.
    """
    p = plan(target, donor, crew_field, overwrite, hours_per_day)
    by_source = {r["activity_id"]: r for r in p["rows"]}

    # Whatever the donor used, so a restored assignment names the crew it was
    # actually on rather than a generic line.
    res_by_uid: Dict[str, Resource] = {r.uid: r for r in (target.resources or [])}
    have_ids = {r.id for r in res_by_uid.values()}
    donor_res: Dict[str, Resource] = {}
    if donor is not None:
        for r in (donor.resources or []):
            donor_res[r.uid] = r
            if r.id not in have_ids:
                target.resources.append(Resource(
                    uid=r.uid, id=r.id, name=r.name, type=r.type,
                    calendar_uid=None, unit_of_measure=r.unit_of_measure,
                    max_units=r.max_units, rate=r.rate, is_active=r.is_active))
                have_ids.add(r.id)
                res_by_uid[r.uid] = target.resources[-1]

    derived_res: Optional[Resource] = None

    def _derived() -> Resource:
        nonlocal derived_res
        if derived_res is None:
            existing = next((r for r in target.resources if r.id == resource_id), None)
            if existing:
                derived_res = existing
            else:
                derived_res = Resource(
                    uid=f"RSRC-{resource_id}", id=resource_id, name=resource_name,
                    type="Labor", max_units=hours_per_day, rate=0.0)
                target.resources.append(derived_res)
        return derived_res

    donor_ra: Dict[str, List[ResourceAssignment]] = {}
    if donor is not None:
        d_by_uid = {a.uid: a for a in donor.activities}
        for ra in (donor.resource_assignments or []):
            act = d_by_uid.get(ra.activity_uid)
            if act:
                donor_ra.setdefault(act.activity_id, []).append(ra)

    if overwrite:
        touched = {aid for aid, r in by_source.items() if r["source"] != "none"}
        keep_uid = {a.uid for a in target.activities
                    if a.activity_id not in touched}
        target.resource_assignments = [
            ra for ra in (target.resource_assignments or [])
            if ra.activity_uid in keep_uid]

    made = {"donor": 0, "crew": 0}
    seq = len(target.resource_assignments or []) + 1
    for act in target.activities:
        row = by_source.get(act.activity_id)
        if not row or row["source"] in ("keep", "none"):
            continue

        if row["source"] == "donor" and donor_ra.get(act.activity_id):
            for ra in donor_ra[act.activity_id]:
                res = res_by_uid.get(ra.resource_uid) or _derived()
                target.resource_assignments.append(ResourceAssignment(
                    uid=f"RA-{seq}", activity_uid=act.uid, resource_uid=res.uid,
                    planned_units=ra.planned_units, actual_units=ra.actual_units,
                    remaining_units=ra.remaining_units,
                    planned_cost=ra.planned_cost, actual_cost=ra.actual_cost,
                    rate=ra.rate))
                seq += 1
            made["donor"] += 1
        else:
            # Either a donor roll-up with no rows behind it, or a crew count.
            # Both become one assignment carrying the whole number, split by
            # the activity's own progress.
            units = float(row["units"] or 0)
            if not units:
                continue
            spent, left = _split(units, act)
            res = _derived()
            target.resource_assignments.append(ResourceAssignment(
                uid=f"RA-{seq}", activity_uid=act.uid, resource_uid=res.uid,
                planned_units=units, actual_units=spent, remaining_units=left,
                rate=res.rate))
            seq += 1
            made[row["source"]] = made.get(row["source"], 0) + 1

    _resync_rollups(target)

    n = made["donor"] + made["crew"]
    bits = []
    if made["donor"]:
        bits.append(f"{made['donor']} from the donor schedule")
    if made["crew"]:
        bits.append(f"{made['crew']} derived from crew counts")
    uncov = p["counts"].get("none", 0)
    msg = (f"Restored labour on {n} activit{'y' if n == 1 else 'ies'}"
           + (" — " + ", ".join(bits) if bits else ""))
    if uncov:
        msg += f"; {uncov} had neither and were left alone"
    detail = dict(p)
    detail["made"] = made
    detail["total_hours"] = round(
        sum(a.planned_labor_units or 0 for a in target.activities), 1)
    return True, msg, detail


def _resync_rollups(project: Project) -> None:
    """
    Make each activity's totals agree with the assignments under it.

    P6 treats the activity numbers as a roll-up, so leaving them stale is how
    a schedule ends up reporting a budget it cannot account for — which is the
    exact state this module exists to clean up.
    """
    tot: Dict[str, List[float]] = {}
    for ra in (project.resource_assignments or []):
        t = tot.setdefault(ra.activity_uid, [0.0, 0.0, 0.0])
        t[0] += ra.planned_units or 0
        t[1] += ra.actual_units or 0
        t[2] += ra.remaining_units or 0
    for a in project.activities:
        if a.uid in tot:
            b, s, r = tot[a.uid]
            a.planned_labor_units = round(b, 2)
            a.actual_labor_units = round(s, 2)
            a.remaining_labor_units = round(r, 2)
