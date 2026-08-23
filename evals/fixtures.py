"""
fixtures.py — small schedules that still pose the real questions.

Deliberately tiny. Every eval case sends its schedule as context, so a
2,776-activity file would make a run cost more than it is worth and would
test the context builder's summarising rather than the agent's judgement.
Ten to twenty activities is enough to ask "did it pick the right
predecessor", "did it invent an id", "did it reach for the bulk action" —
which is what these are for.

Each fixture is built to make ONE class of mistake possible. A schedule where
nothing can go wrong proves nothing.
"""

from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   Relation)


def _act(uid, aid, name, wbs, start, finish, dur=5.0, **kw):
    return Activity(uid=uid, activity_id=aid, name=name, wbs_uid=wbs,
                    calendar_uid="1", activity_type=kw.pop("activity_type",
                                                           "Task Dependent"),
                    status=kw.pop("status", "Not Started"),
                    planned_duration=dur * 8, remaining_duration=dur * 8,
                    planned_start=start, planned_finish=finish, **kw)


def rooms() -> Project:
    """
    Three rooms, the same two trades in each, no logic at all.

    Poses: can it tie within a room instead of across rooms? The dates make
    the right answer checkable — each room's terminations follow its own pull.
    """
    p = Project(uid="1", name="Data Hall A", id="EVAL-ROOMS",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="e", name="Electrical", code="E")]
    p.activities = []
    n = 0
    for i, room in enumerate((105, 106, 107)):
        uid = f"w{room}"
        p.wbs_nodes.append(WBSNode(uid=uid, name=f"MV {room}", code=f"M{room}",
                                   parent_uid="e", sequence_num=i))
        base = 2 + i * 4                       # each room a fortnight later
        n += 1
        p.activities.append(_act(f"p{room}", f"A{1000 + n * 10}", "Pull Wire",
                                 uid, f"2026-02-{base:02d}", f"2026-02-{base+4:02d}"))
        n += 1
        p.activities.append(_act(f"t{room}", f"A{1000 + n * 10}", "Terminations",
                                 uid, f"2026-02-{base+7:02d}", f"2026-02-{base+11:02d}"))
    p.relations = []
    p.build_lookups()
    return p


def phased() -> Project:
    """
    Four phases in order, each with a finish milestone, nothing driving them.

    Poses: does it connect a phase finish to the NEXT phase, or jump straight
    to Closeout? That jump is a mistake the prompt spends a whole section on.
    """
    p = Project(uid="1", name="Plant Upgrade", id="EVAL-PHASED",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    phases = [("Foundations", "FDN"), ("Structure", "STR"),
              ("MEP Rough-In", "MEP"), ("Commissioning", "CX"), ("Closeout", "CO")]
    p.wbs_nodes = [WBSNode(uid="root", name="Plant Upgrade", code="P")]
    p.activities = []
    for i, (name, code) in enumerate(phases):
        uid = f"w{code}"
        p.wbs_nodes.append(WBSNode(uid=uid, name=name, code=code,
                                   parent_uid="root", sequence_num=i))
        m = 3 + i * 2
        p.activities.append(_act(f"a{code}", f"B{2000 + i * 100}",
                                 f"{name} Work", uid,
                                 f"2026-{m:02d}-02", f"2026-{m:02d}-20", dur=15))
        p.activities.append(_act(f"m{code}", f"B{2000 + i * 100 + 50}",
                                 f"{name} Complete", uid,
                                 f"2026-{m:02d}-20", f"2026-{m:02d}-20", dur=0,
                                 activity_type="Finish Milestone"))
    p.relations = []
    p.build_lookups()
    return p


def mixed_ids() -> Project:
    """
    A coded schedule with generic ids drifted into it.

    Poses: does it reach for normalize_activity_ids, or hand-write a pile of
    update_activity_id commands? Also: does it invent MDC1.FDG.1320, which
    looks exactly like the real ones and does not exist?
    """
    p = Project(uid="1", name="Substation", id="EVAL-IDS",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="root", name="Substation", code="S"),
                   WBSNode(uid="f", name="Foundations", code="FDG", parent_uid="root")]
    p.activities = [
        _act("u1", "MDC1.FDG.1290", "Excavate Footings", "f", "2026-02-02", "2026-02-06"),
        _act("u2", "MDC1.FDG.1300", "Rebar Footings", "f", "2026-02-09", "2026-02-13"),
        _act("u3", "MDC1.FDG.1310", "Pour Footings", "f", "2026-02-16", "2026-02-20"),
        _act("u4", "A1000", "Strip Forms", "f", "2026-02-23", "2026-02-27"),
        _act("u5", "A1010", "Backfill Footings", "f", "2026-03-02", "2026-03-06"),
    ]
    p.relations = []
    p.build_lookups()
    return p


def crews() -> Project:
    """
    A dozen similar activities, a crew count to apply across them.

    Poses: does it use one bulk_rules command, or emit twelve edits? The
    prompt says to reach for the bulk action; this is where that is checkable.
    """
    p = Project(uid="1", name="Fit-Out", id="EVAL-CREWS",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Level 2", code="L2")]
    p.activities = [
        _act(f"u{i}", f"C{3000 + i * 10}",
             f"Set Light Fixtures Room {200 + i}", "w",
             "2026-03-02", "2026-03-06")
        for i in range(12)]
    p.relations = []
    p.build_lookups()
    return p


def linked() -> Project:
    """
    A short chain that is already wired, with one obvious gap.

    Poses: does it leave existing logic alone and add only what is missing?
    And does a question about the chain get answered without editing anything?
    """
    p = Project(uid="1", name="Riser", id="EVAL-LINKED",
                data_date="2026-01-05", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Riser 1", code="R1")]
    p.activities = [
        _act("u1", "D1000", "Install Sleeves", "w", "2026-02-02", "2026-02-06"),
        _act("u2", "D1010", "Pull Feeders", "w", "2026-02-09", "2026-02-13"),
        _act("u3", "D1020", "Terminate Feeders", "w", "2026-02-16", "2026-02-20"),
        _act("u4", "D1030", "Megger Test", "w", "2026-02-23", "2026-02-27"),
    ]
    p.relations = [
        Relation(uid="r1", predecessor_uid="u1", successor_uid="u2",
                 type="Finish to Start", lag=0.0),
        Relation(uid="r2", predecessor_uid="u2", successor_uid="u3",
                 type="Finish to Start", lag=0.0),
    ]
    p.build_lookups()
    return p


ALL = {
    "rooms": rooms,
    "phased": phased,
    "mixed_ids": mixed_ids,
    "crews": crews,
    "linked": linked,
}
