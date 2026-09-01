"""
test_brain_tab.py — the brain, shown the way the agent actually receives it.

The screen is organised by AUTHORITY, not by subject, and that is the whole
design. context_block() emits the objective, then enforced rules, then
unenforced notes, then things that matched nothing, then rules the user keeps
overriding — there is no procurement/drawings/permits axis anywhere in the
brain. A screen grouped that way would look aligned and be decorative: moving
an item between such groups would not change one byte of what the agent reads.

Two things here are worth more than the rest, because both are invisible today
and both cost the user something real:

  A rule that matches nothing enforces nothing while still looking like a rule.

  A rule past the per-pile cap is STILL ENFORCED — the tie ranker scores the
  stored objects, not the prompt text — but the agent can no longer recall or
  explain it. Those are different failures and the screen has to tell them
  apart.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import server
from engine.schedule_model import (Activity, Calendar, Project, Relation,
                                   WBSNode)


def _job():
    p = Project(uid="p", name="MDC1", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [WBSNode(uid="LLE", name="Phase 1 (Build-Out) / LLE", code="L"),
                   WBSNode(uid="w", name="MV 101", code="A")]
    p.activities, p.relations = [], []
    for u, n, f in [("S1", "Chillers", "LLE"), ("I1", "Set Chiller", "w"),
                    ("T1", "Terminate Generator", "w"),
                    ("T2", "Terminate Generator B", "w"),
                    ("C1", "Commissioning", "w")]:
        p.activities.append(Activity(
            uid=u, activity_id=u, name=n, wbs_uid=f, calendar_uid="1",
            activity_type="Task Dependent", status="Not Started",
            planned_duration=40, remaining_duration=40,
            planned_start="2026-06-01", planned_finish="2026-06-05"))
    p.relations.append(Relation(uid="r1", predecessor_uid="T1",
                                successor_uid="C1", type="Finish to Start",
                                lag=0.0))          # T2 deliberately orphaned
    p.build_lookups()
    return p


@pytest.fixture
def client():
    p = _job()
    pid = "J"
    server._projects.clear(); server._brains.clear()
    server._projects[pid] = server._make_session(pid, "t.xml")
    server._projects[pid]["project"] = p
    server._active_id[0] = pid
    return server.app.test_client(), p, server._brain_for(p)


def _get(c):
    r = c.get("/api/brain/overview")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ── the piles carry different authority ──────────────────────────────────────

def test_an_enforced_rule_is_separated_from_a_bare_note(client):
    c, p, b = client
    b.add("Set Chiller must come after Chillers", p)
    b.add("this is a data centre fit-out", p)
    d = _get(c)
    assert [x["text"] for x in d["piles"]["rules"]] == \
        ["Set Chiller must come after Chillers"]
    assert [x["text"] for x in d["piles"]["notes"]] == \
        ["this is a data centre fit-out"]


def test_a_rule_matching_nothing_lands_in_open_with_the_reason(client):
    """The most useful pile: said as a rule, binds to nothing, so it is not in
    force — and the reason says whether the naming differs or the work is
    missing."""
    c, p, b = client
    b.add("QA/QC follows terminations", p)
    d = _get(c)
    assert d["piles"]["rules"] == []
    open_ = d["piles"]["open"]
    assert len(open_) == 1
    assert open_[0]["note_reason"], "no reason given for a rule that binds to nothing"


def test_an_enforced_rule_reports_how_much_it_applies_to(client):
    c, p, b = client
    b.add("Set Chiller must come after Chillers", p)
    assert _get(c)["piles"]["rules"][0]["matched"] > 0


def test_a_contested_rule_is_called_out_separately(client):
    """Enforced, but repeatedly overridden — a different problem from a rule
    that binds to nothing, and it wants a different answer."""
    c, p, b = client
    d0 = b.add("Set Chiller must come after Chillers", p)
    from engine import project_brain
    for _ in range(project_brain._REVIEW_OVERRIDES):
        b.record_conflict(d0.id, overridden=True)
    d = _get(c)
    assert [x["text"] for x in d["piles"]["contested"]] == [d0.text]
    assert d["health"]["contested"] == 1


# ── the cap, which is invisible everywhere else ──────────────────────────────

def test_a_rule_past_the_cap_is_marked_as_unseen_by_the_agent(client):
    """It is still enforced. What is lost is the agent's ability to recall or
    explain it, and nothing told the user that today."""
    c, p, b = client
    for i in range(server._brain_for(p)._CAP + 3):
        b.add(f"note number {i} about this job", p)
    d = _get(c)
    notes = d["piles"]["notes"]
    assert sum(1 for n in notes if not n["in_prompt"]) == 3
    assert d["health"]["hidden_from_prompt"] == 3


def test_the_newest_survive_the_cap_not_the_oldest(client):
    c, p, b = client
    cap = server._brain_for(p)._CAP
    for i in range(cap + 2):
        b.add(f"note number {i} about this job", p)
    shown = [n["text"] for n in _get(c)["piles"]["notes"] if n["in_prompt"]]
    assert f"note number {cap + 1} about this job" in shown
    assert "note number 0 about this job" not in shown


# ── the live checks are real engines, not decoration ─────────────────────────

def test_the_checks_come_back_for_a_loaded_schedule(client):
    c, p, b = client
    ch = _get(c)["checks"]
    assert ch["procurement"] is not None and "at_risk" in ch["procurement"]
    assert ch["flow"] is not None and "tally" in ch["flow"]
    assert ch["documents"]["filed"] == 0


def test_a_requirement_is_evaluated_rather_than_merely_listed(client):
    """`check()` reports violations as a COUNT and passed as a bool. Reading
    violations as a list threw, and the exception guard turned every
    requirement into 'none set' — which reads exactly like having set none."""
    c, p, b = client
    b.requirements = [{"kind": "reaches", "what": "Terminate",
                       "to": "Commissioning"}]
    r = _get(c)["checks"]["requirements"]
    assert r["total"] == 1 and r["holding"] == 0
    assert r["items"][0]["violations"] == 1      # T2 has no path
    assert r["items"][0]["matched"] == 2


def test_a_requirement_the_engine_cannot_read_is_counted_not_swallowed(client):
    c, p, b = client
    b.requirements = [{"kind": "reaches", "what": "Terminate", "to": "Commissioning"},
                      {"kind": "nonsense"}]
    r = _get(c)["checks"]["requirements"]
    assert r["total"] == 1 and r["unreadable"] == 1


def test_one_failing_engine_does_not_take_the_screen_down(client, monkeypatch):
    """A screen that will not open because one engine threw is worse than a
    screen with a gap in it."""
    c, p, b = client
    import engine.procurement_map as pm
    monkeypatch.setattr(pm, "analyse",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = _get(c)
    assert d["success"] and d["checks"]["procurement"] is None
    assert d["checks"]["flow"] is not None


# ── it reads, never writes ───────────────────────────────────────────────────

def test_opening_the_tab_changes_nothing(client):
    c, p, b = client
    b.add("Set Chiller must come after Chillers", p)
    before = (len(p.activities), len(p.relations), len(b.directives),
              [d.enabled for d in b.directives])
    _get(c); _get(c)
    after = (len(p.activities), len(p.relations), len(b.directives),
             [d.enabled for d in b.directives])
    assert before == after


def test_it_refuses_politely_with_no_schedule_loaded():
    server._projects.clear(); server._active_id[0] = None
    r = server.app.test_client().get("/api/brain/overview")
    assert r.status_code == 400 and "error" in r.get_json()


def test_an_empty_brain_still_renders(client):
    """A new job has nothing taught; the screen has to open anyway."""
    c, p, b = client
    d = _get(c)
    assert d["success"]
    assert all(d["piles"][k] == [] for k in ("rules", "notes", "open", "contested"))
    assert d["health"]["dead_rules"] == 0
