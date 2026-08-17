"""
test_crew_defaults.py — filling a crew column across thousands of rows.

Typing a headcount into 2,729 activities one at a time is not going to happen,
so a count set once travels: put 6 on "Install High Steel Area 3" and every
other Install High Steel takes it too, past and future.

Matching is on the WORK, not the exact name — the trailing area / level / room
/ phase and any bare numbers come off first, because "Area 3" and "Area 7" are
the same task in two places and take the same crew. A count already set by
hand is never overwritten.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.edit_engine import apply_command, crew_defaults, _norm_name
from engine.schedule_model import Project, Activity, WBSNode, Calendar, UDFType

FIELD = "Number of Electricians"


def _proj():
    p = Project(uid="p", name="P", id="P", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W", parent_uid=None),
                   WBSNode(uid="w2", name="Other", code="O", parent_uid=None)]
    p.udf_types = [UDFType(uid="9", title=FIELD, data_type="Integer")]
    p.activities = []
    return p


def _act(p, uid, name, crew=None, wbs="w"):
    a = Activity(uid=uid, activity_id=uid.upper(), name=name, wbs_uid=wbs,
                 calendar_uid="1", activity_type="Task Dependent",
                 status="Not Started", planned_duration=40.0, remaining_duration=40.0,
                 planned_start="2026-02-02", planned_finish="2026-02-06",
                 udfs={} if crew is None else {FIELD: str(crew)})
    p.activities.append(a)
    p.build_lookups()
    return a


def _crew(p, aid):
    return (p.get_activity(activity_id=aid).udfs or {}).get(FIELD)


def _steel():
    """The same work in several areas, one of which has a count."""
    p = _proj()
    _act(p, "a1", "Install High Steel Area 1")
    _act(p, "a2", "Install High Steel Area 2")
    _act(p, "a3", "Install High Steel Area 3", crew=6)
    _act(p, "a4", "Install High Steel Area 7")
    _act(p, "b1", "Pour Slab Area 1")
    return p


# ── what counts as the same work ──────────────────────────────────────────────

def test_the_area_comes_off_the_name():
    assert _norm_name("Install High Steel Area 3") == _norm_name("Install High Steel Area 7")


def test_levels_rooms_and_phases_come_off_too():
    base = _norm_name("Pull Wire")
    for n in ("Pull Wire Level 3", "Pull Wire Room 12", "Pull Wire (PH2)",
              "Pull Wire - Grid Line 18"):
        assert _norm_name(n) == base, n


def test_a_parenthetical_count_is_ignored():
    assert (_norm_name("Precast Erection Area 8 (97 Pieces)")
            == _norm_name("Precast Erection Area 1 (199 Pieces North)"))


def test_genuinely_different_work_stays_different():
    assert _norm_name("Install High Steel Area 1") != _norm_name("Pour Slab Area 1")


# ── carrying a count across the schedule ──────────────────────────────────────

def _apply(p, **kw):
    return apply_command(p, dict({"action": "apply_crew_to_name"}, **kw))


def test_a_count_set_once_reaches_every_area():
    p = _steel()
    ok, msg = _apply(p, activity_id="A3")
    assert ok
    assert _crew(p, "A1") == "6" and _crew(p, "A2") == "6" and _crew(p, "A4") == "6"
    assert "3 activities" in msg


def test_other_work_is_left_alone():
    p = _steel()
    _apply(p, activity_id="A3")
    assert _crew(p, "B1") is None


def test_a_count_already_set_by_hand_is_not_overwritten():
    p = _steel()
    p.get_activity(activity_id="A1").udfs[FIELD] = "9"
    ok, msg = _apply(p, activity_id="A3")
    assert _crew(p, "A1") == "9"
    assert "left alone" in msg


def test_overwriting_can_be_asked_for():
    p = _steel()
    p.get_activity(activity_id="A1").udfs[FIELD] = "9"
    _apply(p, activity_id="A3", only_missing=False)
    assert _crew(p, "A1") == "6"


def test_a_value_can_be_given_directly():
    p = _steel()
    _apply(p, name="Install High Steel", value=4)
    assert _crew(p, "A1") == "4" and _crew(p, "A2") == "4"


def test_an_exact_match_can_be_demanded():
    p = _proj()
    _act(p, "a1", "Install High Steel Area 1")
    _act(p, "a2", "Install High Steel Area 2")
    _apply(p, name="Install High Steel Area 1", value=5, match="exact")
    assert _crew(p, "A1") == "5" and _crew(p, "A2") is None


def test_a_substring_match_can_be_used():
    p = _steel()
    _apply(p, name="high steel", value=3, match="contains")
    assert _crew(p, "A1") == "3" and _crew(p, "B1") is None


def test_it_can_be_held_to_one_branch():
    p = _steel()
    _act(p, "c1", "Install High Steel Area 9", wbs="w2")
    _apply(p, activity_id="A3", wbs_uid="w")
    assert _crew(p, "A1") == "6" and _crew(p, "C1") is None


def test_a_row_with_no_count_says_so():
    p = _steel()
    ok, msg = _apply(p, activity_id="A1")
    assert not ok and "set one on the activity first" in msg


def test_matching_nothing_is_reported():
    p = _steel()
    ok, msg = _apply(p, name="Hang Ductwork", value=2)
    assert not ok and "Nothing matches" in msg


def test_a_non_numeric_count_is_refused():
    p = _steel()
    ok, msg = _apply(p, name="Install High Steel", value="lots")
    assert not ok and "needs a number" in msg


# ── what the schedule has already taught it ───────────────────────────────────

def test_defaults_report_what_is_set_and_what_is_missing():
    d = crew_defaults(_steel())
    steel = next(r for r in d if "High Steel" in r["name"])
    assert steel["crew"] == 6
    assert steel["with_value"] == 1 and steel["missing"] == 3


def test_the_most_common_count_wins_and_disagreement_is_flagged():
    p = _proj()
    _act(p, "a1", "Pull Wire Area 1", crew=4)
    _act(p, "a2", "Pull Wire Area 2", crew=4)
    _act(p, "a3", "Pull Wire Area 3", crew=9)
    r = crew_defaults(p)[0]
    assert r["crew"] == 4 and r["varies"] is True


def test_work_needing_the_most_filling_in_comes_first():
    p = _proj()
    _act(p, "a1", "Rare Task Area 1", crew=2)
    for i in range(4):
        _act(p, f"b{i}", f"Common Task Area {i}", crew=3 if i == 0 else None)
    assert crew_defaults(p)[0]["name"].startswith("Common")


# ── filling every blank at once ───────────────────────────────────────────────

def test_every_blank_it_can_answer_is_filled():
    p = _steel()
    ok, msg = apply_command(p, {"action": "fill_crew_defaults"})
    assert ok
    assert _crew(p, "A1") == _crew(p, "A2") == _crew(p, "A4") == "6"
    assert "3 activities" in msg


def test_work_it_has_never_seen_a_count_for_is_left_blank():
    p = _steel()
    apply_command(p, {"action": "fill_crew_defaults"})
    assert _crew(p, "B1") is None


def test_filling_with_nothing_learned_yet_says_so():
    p = _proj()
    _act(p, "a1", "Install High Steel Area 1")
    ok, msg = apply_command(p, {"action": "fill_crew_defaults"})
    assert not ok and "set a few first" in msg


# ── a new activity inherits the count ─────────────────────────────────────────

def test_a_new_activity_doing_known_work_gets_the_crew():
    p = _steel()
    apply_command(p, {"action": "add_activity", "wbs_uid": "w",
                      "name": "Install High Steel Area 9", "duration_days": 5})
    new = next(a for a in p.activities if a.name.endswith("Area 9"))
    assert (new.udfs or {}).get(FIELD) == "6"


def test_a_new_activity_doing_unknown_work_gets_nothing():
    p = _steel()
    apply_command(p, {"action": "add_activity", "wbs_uid": "w",
                      "name": "Hang Ductwork", "duration_days": 5})
    new = next(a for a in p.activities if a.name == "Hang Ductwork")
    assert not (new.udfs or {}).get(FIELD)


def test_a_count_given_on_the_new_activity_wins():
    p = _steel()
    apply_command(p, {"action": "add_activity", "wbs_uid": "w",
                      "name": "Install High Steel Area 9", "duration_days": 5,
                      "udfs": {FIELD: "12"}})
    new = next(a for a in p.activities if a.name.endswith("Area 9"))
    assert new.udfs[FIELD] == "12"


def test_inheriting_can_be_turned_off():
    p = _steel()
    apply_command(p, {"action": "add_activity", "wbs_uid": "w",
                      "name": "Install High Steel Area 9", "duration_days": 5,
                      "inherit_crew": False})
    new = next(a for a in p.activities if a.name.endswith("Area 9"))
    assert not (new.udfs or {}).get(FIELD)


# ── through the API ───────────────────────────────────────────────────────────

def test_the_defaults_endpoint_reports_the_column_and_the_gaps():
    c = server.app.test_client()
    server._projects["t"] = server._make_session("t", "t.xml")
    server._projects["t"]["project"] = _steel()
    server._active_id[0] = "t"
    body = c.get("/api/crew/defaults").get_json()
    assert body["field"] == FIELD
    assert body["filled"] == 1 and body["blank"] == 4
    assert any("High Steel" in r["name"] for r in body["defaults"])
