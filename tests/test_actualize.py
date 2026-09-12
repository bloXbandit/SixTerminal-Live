"""
test_actualize.py — "the gens are done, we're out to terminations in ER 208".

What arrives is never a list of activity IDs. It is a front: where the work
has got to. And the update a front implies is much larger than what was said —
"Gen 315 is complete" is also saying its feeders are pulled, its gear is set,
and the precast under it went in months ago.

Nobody lists those, and they are not a judgement call. They are what the
finished work depends on, transitively, which is a computation over the whole
network. That is the thing being tested here: that the closure is right, that
it is shown separately before it is written, that the dates it invents are
defensible, and that it never quietly undoes work already statused.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.actualize import actualize, describe, plan
from engine.edit_engine import apply_command
from engine.schedule_model import Activity, Calendar, Project, Relation, WBSNode


def _job():
    """
    Precast feeds the rooms; each room is conduit -> wire -> terminate. The
    precast is what nobody mentions and everything depends on.
    """
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J"),
                   WBSNode(uid="pre", name="Precast", code="PC", parent_uid="root"),
                   WBSNode(uid="g1", name="Gen 315", code="G315", parent_uid="root"),
                   WBSNode(uid="g2", name="Gen 316", code="G316", parent_uid="root")]
    p.activities, p.relations = [], []

    def act(uid, aid, name, wbs, s, f):
        a = Activity(uid=uid, activity_id=aid, name=name, wbs_uid=wbs,
                     calendar_uid="1", planned_duration=16, remaining_duration=16,
                     planned_start=s, planned_finish=f)
        p.activities.append(a)
        return a

    act("pc", "PC10", "Erect Precast", "pre", "2025-11-03", "2025-11-07")
    for room, uid in (("Gen 315", "g1"), ("Gen 316", "g2")):
        n = uid[-1]
        act(f"c{n}", f"C{n}0", f"Install Conduit ({room})", uid,
            "2026-02-02", "2026-02-04")
        act(f"w{n}", f"W{n}0", f"Pull Wire ({room})", uid,
            "2026-02-05", "2026-02-07")
        act(f"t{n}", f"T{n}0", f"Terminate Generator ({room})", uid,
            "2026-02-09", "2026-02-11")
        p.relations += [
            Relation(uid=f"rp{n}", predecessor_uid="pc", successor_uid=f"c{n}"),
            Relation(uid=f"rc{n}", predecessor_uid=f"c{n}", successor_uid=f"w{n}"),
            Relation(uid=f"rw{n}", predecessor_uid=f"w{n}", successor_uid=f"t{n}"),
        ]
    p.build_lookups()
    return p


def _ids(rows):
    return sorted(r["activity_id"] for r in rows)


# ── the closure, which is the whole point ────────────────────────────────────

def test_saying_a_room_is_done_also_says_the_precast_is_done():
    """The sentence nobody says out loud. This is the feature."""
    r = plan(_job(), folder_pattern=r"^Gen 315$")
    assert _ids(r["named"]) == ["C10", "T10", "W10"]
    assert _ids(r["implied"]) == ["PC10"]


def test_the_implied_work_is_reported_apart_from_what_was_said():
    """A closure reaching somewhere it should not is caught by reading it, so
    it cannot be mixed in with the rows the user actually named."""
    out = describe(plan(_job(), folder_pattern=r"^Gen 315$"))
    assert "3 you named" in out
    assert "1 follow from them" in out
    assert out.index("Named by the evidence") < out.index("Implied by the logic")


def test_the_closure_can_be_turned_off():
    r = plan(_job(), folder_pattern=r"^Gen 315$", include_predecessors=False)
    assert r["implied"] == []


def test_out_to_an_activity_puts_that_one_in_progress_and_the_rest_behind_it_done():
    """"We are out to terminations" — the front is in progress, what feeds it
    is complete, and the work AFTER it is untouched."""
    r = plan(_job(), folder_pattern=r"^Gen 315$", through="Pull Wire")
    assert [(x["activity_id"], x["to"]) for x in r["named"]] == [("W10", "In Progress")]
    assert _ids(r["implied"]) == ["C10", "PC10"]
    assert all(x["activity_id"] != "T10" for x in r["rows"]), "it ran past the front"


def test_a_front_in_one_room_says_nothing_about_the_other_room():
    r = plan(_job(), folder_pattern=r"^Gen 315$")
    assert not any(x["activity_id"].startswith(("C2", "W2", "T2"))
                   for x in r["rows"])


def test_a_pattern_can_name_every_room_at_once():
    r = plan(_job(), folder_pattern=r"^Gen \d+$")
    assert len(r["named"]) == 6 and _ids(r["implied"]) == ["PC10"]


def test_explicit_activity_ids_work_too():
    r = plan(_job(), activity_ids=["T10"])
    assert _ids(r["named"]) == ["T10"]
    assert _ids(r["implied"]) == ["C10", "PC10", "W10"]


# ── dates ────────────────────────────────────────────────────────────────────

def test_work_already_in_the_past_keeps_the_dates_the_schedule_had():
    """Nothing contradicts them, and they are a better answer than today."""
    r = plan(_job(), folder_pattern=r"^Gen 315$")
    pc = [x for x in r["implied"] if x["activity_id"] == "PC10"][0]
    assert (pc["actual_start"], pc["actual_finish"]) == ("2025-11-03", "2025-11-07")


def test_work_the_schedule_still_had_in_the_future_lands_on_the_evidence_date():
    """It cannot finish next month if it is done now. The evidence wins."""
    r = plan(_job(), folder_pattern=r"^Gen 315$", as_of="2026-01-05")
    fin = {x["activity_id"]: x["actual_finish"] for x in r["named"]}
    assert set(fin.values()) == {"2026-01-05"}


def test_how_much_the_schedule_disagreed_is_counted_and_said():
    """Three rows pulled back from February is the headline — the plan is
    behind what is actually built — not a detail to bury."""
    r = plan(_job(), folder_pattern=r"^Gen 315$")
    assert r["capped"] == 3
    assert "still in the FUTURE" in describe(r)


def test_a_start_is_never_left_after_its_own_finish():
    r = plan(_job(), folder_pattern=r"^Gen 315$", as_of="2025-12-01")
    for x in r["named"]:
        assert x["actual_start"] <= x["actual_finish"], x


def test_the_evidence_date_can_be_later_than_the_projects_own():
    """A lookahead is usually ahead of the app. The project's data date is NOT
    moved by this — that is what the Schedule run does."""
    p = _job()
    r = plan(p, folder_pattern=r"^Gen 315$", as_of="2026-03-01")
    assert r["as_of"] == "2026-03-01" and r["data_date"] == "2026-01-05"
    assert "2026-01-05" in describe(r)
    actualize(p, folder_pattern=r"^Gen 315$", as_of="2026-03-01", apply=True)
    assert p.data_date == "2026-01-05", "it moved the project's data date"


def test_a_bad_date_says_so():
    with pytest.raises(ValueError, match="Not a valid date"):
        plan(_job(), folder_pattern=r"^Gen 315$", as_of="last tuesday")


# ── it never takes anything away ─────────────────────────────────────────────

def test_work_already_complete_keeps_its_own_actual_dates():
    p = _job()
    pc = p.get_activity(activity_id="PC10")
    pc.status, pc.actual_start, pc.actual_finish = "Completed", "2025-10-01", "2025-10-09"
    pc.percent_complete = 100.0
    actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    assert (pc.actual_start, pc.actual_finish) == ("2025-10-01", "2025-10-09")


def test_an_activity_already_in_progress_keeps_the_start_it_was_given():
    p = _job()
    w = p.get_activity(activity_id="W10")
    w.status, w.actual_start, w.percent_complete = "In Progress", "2026-01-02", 40.0
    actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    assert w.actual_start == "2026-01-02" and w.status == "Completed"


def test_nothing_outside_the_evidence_is_touched():
    p = _job()
    before = {a.uid: (a.status, a.actual_start, a.actual_finish,
                      a.planned_start, a.planned_finish)
              for a in p.activities}
    actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    for uid in ("c2", "w2", "t2"):
        a = [x for x in p.activities if x.uid == uid][0]
        assert (a.status, a.actual_start, a.actual_finish,
                a.planned_start, a.planned_finish) == before[uid]


def test_statusing_does_not_reschedule_the_rest_of_the_job():
    p = _job()
    before = {a.uid: (a.planned_start, a.planned_finish) for a in p.activities}
    actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    assert {a.uid: (a.planned_start, a.planned_finish)
            for a in p.activities} == before


def test_running_it_twice_changes_nothing_the_second_time():
    p = _job()
    actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    snap = {a.uid: (a.status, a.actual_start, a.actual_finish)
            for a in p.activities}
    again = actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    assert {a.uid: (a.status, a.actual_start, a.actual_finish)
            for a in p.activities} == snap
    assert not again["rows"]


# ── what it writes ───────────────────────────────────────────────────────────

def test_applying_sets_the_three_things_p6_reads_as_complete():
    p = _job()
    actualize(p, folder_pattern=r"^Gen 315$", apply=True)
    a = p.get_activity(activity_id="T10")
    assert a.status == "Completed"
    assert a.percent_complete == 100.0 and a.remaining_duration == 0.0
    assert a.actual_start and a.actual_finish


def test_a_front_is_left_in_the_state_p6_reads_as_running():
    p = _job()
    actualize(p, folder_pattern=r"^Gen 315$", through="Pull Wire", apply=True)
    a = p.get_activity(activity_id="W10")
    assert a.status == "In Progress"
    assert a.actual_start and a.actual_finish is None
    assert 0 < a.percent_complete < 100 and a.remaining_duration > 0


# ── it says what it could not do ─────────────────────────────────────────────

def test_a_folder_that_does_not_exist_is_named_not_ignored():
    r = plan(_job(), folder_pattern=r"^Chiller Yard$")
    assert not r["rows"] and any("Chiller Yard" in n for n in r["notes"])
    assert "Chiller Yard" in describe(r)


def test_a_front_that_matches_nothing_leaves_that_folder_alone():
    r = plan(_job(), folder_pattern=r"^Gen \d+$", through="Energise")
    assert not r["rows"]
    assert len(r["notes"]) == 2 and "Energise" in r["notes"][0]


def test_a_thin_closure_is_explained_by_the_logic_not_left_to_look_complete():
    """On a job full of open starts the implied set is short because nothing
    is linked — the user must not read that as "there is little to catch up"."""
    p = _job()
    p.relations = [r for r in p.relations if r.predecessor_uid != "pc"]
    p.build_lookups()
    r = plan(p, folder_pattern=r"^Gen 315$")
    assert r["named_unlinked"] == 1
    assert "no predecessor at all" in describe(r)


# ── the date the user wants held ─────────────────────────────────────────────

def test_it_reports_where_a_nominated_date_lands_rather_than_silently_compressing():
    """"Preserve the contract date" cannot mean inventing shorter durations
    without saying so. It reports the move and names what would have to give."""
    p = _job()
    r = actualize(p, folder_pattern=r"^Gen 315$", preserve="T20", apply=True)
    assert r["preserve_before"] == "2026-02-11"
    assert "preserve_after" in r


def test_a_held_date_that_holds_says_so():
    p = _job()
    r = actualize(p, folder_pattern=r"^Gen 315$", preserve="T20", apply=True)
    if r["preserve_after"] == r["preserve_before"]:
        assert "the date holds" in describe(r)


# ── as a command ─────────────────────────────────────────────────────────────

def test_it_reports_without_changing_anything_by_default():
    p = _job()
    ok, msg = apply_command(p, {"action": "actualize", "folder_pattern": r"^Gen 315$"})
    assert ok and "Nothing has been changed" in msg
    assert all(a.status != "Completed" for a in p.activities)


def test_apply_true_writes():
    p = _job()
    ok, msg = apply_command(p, {"action": "actualize", "folder_pattern": r"^Gen 315$",
                                "apply": True})
    assert ok
    assert p.get_activity(activity_id="PC10").status == "Completed"


def test_the_command_needs_something_to_go_on():
    ok, msg = apply_command(_job(), {"action": "actualize"})
    assert not ok and "folder_pattern" in msg
