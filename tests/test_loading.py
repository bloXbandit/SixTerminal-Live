"""
test_loading.py — crew demand per week, and the lookahead.

A schedule can be a valid network and still be impossible to staff: the dates
say the work fits, and only spreading the crew across the working days shows
the weeks where it does not. Crew is spread evenly over an activity's own
working days — a Mon-Fri activity with 4 electricians puts 4 on each of those
five days, not 20 on the Monday.

The lookahead is the same data cut the other way: everything RUNNING in a
window, grouped by area, including work carried in from before it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.loading import crew_load, lookahead, crew_field
from engine.schedule_model import (Project, Activity, WBSNode, Calendar, UDFType)


def _proj(data_date="2026-03-02"):
    p = Project(uid="p", name="P", id="P", data_date=data_date,
                planned_start="2026-03-02")
    p.calendars = [Calendar(uid="1", name="Std", work_days=frozenset({0, 1, 2, 3, 4}),
                            holidays=frozenset(), hours_per_day=8.0)]
    p.wbs_nodes = [WBSNode(uid="w", name="Work", code="W", parent_uid=None),
                   WBSNode(uid="w2", name="Other", code="O", parent_uid=None)]
    p.udf_types = [UDFType(uid="9", title="Number of Electricians", data_type="Integer")]
    p.activities = []
    return p


def _act(p, uid, start, finish, crew=None, wbs="w", status="Not Started", dur=5.0):
    a = Activity(uid=uid, activity_id=uid.upper(), name=f"Task {uid}", wbs_uid=wbs,
                 calendar_uid="1", activity_type="Task Dependent", status=status,
                 planned_duration=dur * 8, remaining_duration=dur * 8,
                 planned_start=start, planned_finish=finish,
                 udfs={} if crew is None else {"Number of Electricians": str(crew)})
    p.activities.append(a)
    p.build_lookups()
    return a


def _week(res, wk):
    return next((w for w in res["weeks"] if w["week"] == wk), None)


# ── which column holds a headcount ────────────────────────────────────────────

def test_the_electricians_column_is_found():
    assert crew_field(_proj()) == "Number of Electricians"


def test_a_schedule_with_no_crew_column_says_so():
    p = _proj()
    p.udf_types = []
    assert crew_field(p) is None


# ── spreading crew across working days ────────────────────────────────────────

def test_crew_is_spread_over_the_working_days_not_dumped_on_day_one():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)      # Mon-Fri
    r = crew_load(p)
    w = _week(r, "2026-W10")
    assert w["peak_crew"] == 4          # four people on site, not twenty
    assert w["crew_days"] == 20         # 4 x 5 days


def test_overlapping_activities_add_up_on_the_same_day():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    _act(p, "a2", "2026-03-02", "2026-03-06", crew=6)
    assert _week(crew_load(p), "2026-W10")["peak_crew"] == 10


def test_work_that_does_not_overlap_does_not_stack():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    _act(p, "a2", "2026-03-09", "2026-03-13", crew=6)
    r = crew_load(p)
    assert _week(r, "2026-W10")["peak_crew"] == 4
    assert _week(r, "2026-W11")["peak_crew"] == 6


def test_weekends_carry_no_crew():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-08", crew=2)      # Mon-Sun
    assert _week(crew_load(p), "2026-W10")["crew_days"] == 10   # five working days


def test_a_holiday_is_skipped():
    p = _proj()
    p.calendars[0].holidays = frozenset({"2026-03-04"})
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=2)
    assert _week(crew_load(p), "2026-W10")["crew_days"] == 8    # four days, not five


def test_the_peak_is_the_worst_single_day_not_the_weekly_total():
    """The number that breaks is how many are on site at once."""
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-02", crew=30)     # one day, 30 people
    _act(p, "a2", "2026-03-03", "2026-03-06", crew=5)
    w = _week(crew_load(p), "2026-W10")
    assert w["peak_crew"] == 30
    assert w["crew_days"] == 50


# ── milestones, completed work, scope ─────────────────────────────────────────

def test_milestones_carry_no_crew():
    p = _proj()
    a = _act(p, "a1", "2026-03-02", "2026-03-02", crew=5)
    a.activity_type = "Finish Milestone"
    assert crew_load(p)["weeks"] == []


def test_completed_work_is_excluded_by_default():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4, status="Completed")
    assert crew_load(p)["weeks"] == []
    assert crew_load(p, include_completed=True)["weeks"]


def test_an_area_can_be_isolated():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4, wbs="w")
    _act(p, "a2", "2026-03-02", "2026-03-06", crew=6, wbs="w2")
    assert _week(crew_load(p, scope_uid="w"), "2026-W10")["peak_crew"] == 4


# ── when nobody has filled the column in ──────────────────────────────────────

def test_with_no_crew_numbers_it_counts_activities_and_says_so():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06")
    _act(p, "a2", "2026-03-02", "2026-03-06")
    r = crew_load(p)
    assert r["counted_as_activities"] is True
    assert _week(r, "2026-W10")["peak_crew"] == 2


def test_rows_without_a_number_are_reported_as_unstaffed():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    _act(p, "a2", "2026-03-02", "2026-03-06")
    w = _week(crew_load(p), "2026-W10")
    assert w["unstaffed"] == 1
    assert w["peak_crew"] == 5      # 4 + the unstaffed row counted as 1


def test_the_spike_ratio_shows_how_uneven_the_plan_is():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=40)
    for i, wk in enumerate(("2026-03-09", "2026-03-16", "2026-03-23")):
        _act(p, f"b{i}", wk, wk, crew=2)
    r = crew_load(p)
    assert r["peak_crew"] == 40
    assert r["spike_ratio"] and r["spike_ratio"] >= 10
    assert r["busiest_week"]["week"] == "2026-W10"


# ── lookahead ─────────────────────────────────────────────────────────────────

def test_the_window_starts_on_a_monday():
    p = _proj(data_date="2026-03-04")          # a Wednesday
    _act(p, "a1", "2026-03-02", "2026-03-06")
    assert lookahead(p, weeks=3)["from"] == "2026-03-02"


def test_three_weeks_covers_twenty_one_days():
    p = _proj()
    d = lookahead(p, weeks=3)
    assert (d["from"], d["to"]) == ("2026-03-02", "2026-03-22")


def test_work_already_running_is_carried_in():
    """A crew needs to see what is under way, not only what starts."""
    p = _proj()
    _act(p, "a1", "2026-02-16", "2026-03-06")
    d = lookahead(p, weeks=3)
    row = d["groups"][0]["activities"][0]
    assert row["activity_id"] == "A1"
    assert row["starts_in_window"] is False


def test_work_beyond_the_window_is_left_out():
    p = _proj()
    _act(p, "a1", "2026-06-01", "2026-06-05")
    assert lookahead(p, weeks=3)["activity_count"] == 0


def test_rows_are_grouped_by_area_and_sorted_by_start():
    p = _proj()
    _act(p, "a2", "2026-03-09", "2026-03-13", wbs="w")
    _act(p, "a1", "2026-03-02", "2026-03-06", wbs="w")
    _act(p, "a3", "2026-03-02", "2026-03-06", wbs="w2")
    d = lookahead(p, weeks=3)
    assert len(d["groups"]) == 2
    work = next(g for g in d["groups"] if g["wbs_path"].endswith("Work"))
    assert [a["activity_id"] for a in work["activities"]] == ["A1", "A2"]


def test_the_crew_number_travels_onto_the_lookahead():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=7)
    assert lookahead(p, weeks=3)["groups"][0]["activities"][0]["crew"] == 7


# ── through the API ───────────────────────────────────────────────────────────

def _client(p):
    c = server.app.test_client()
    server._projects["t"] = server._make_session("t", "t.xml")
    server._projects["t"]["project"] = p
    server._active_id[0] = "t"
    return c


def test_the_loading_endpoint_returns_weeks():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    body = _client(p).get("/api/loading").get_json()
    assert body["weeks"] and body["peak_crew"] == 4


def test_the_lookahead_endpoint_returns_groups():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    body = _client(p).get("/api/lookahead?weeks=3").get_json()
    assert body["activity_count"] == 1 and body["groups"]


def test_the_lookahead_downloads_as_csv():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    r = _client(p).get("/api/lookahead?weeks=3&format=csv")
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    text = r.get_data(as_text=True)
    assert "Activity ID" in text and "A1" in text and "Number of Electricians" in text


def test_the_week_count_is_clamped_to_something_sane():
    p = _proj()
    _act(p, "a1", "2026-03-02", "2026-03-06")
    assert _client(p).get("/api/lookahead?weeks=999").get_json()["weeks"] == 26
    assert _client(p).get("/api/lookahead?weeks=junk").get_json()["weeks"] == 3


# ── the past is not staffable ─────────────────────────────────────────────────

def test_weeks_that_ended_before_the_data_date_are_dropped():
    """A schedule with stale dates on not-started work otherwise opens on years
    of one-activity weeks, with the real peak far below the fold."""
    p = _proj(data_date="2026-03-02")
    _act(p, "old", "2021-07-05", "2021-07-09", crew=1)
    _act(p, "now", "2026-03-02", "2026-03-06", crew=4)
    weeks = [w["week"] for w in crew_load(p)["weeks"]]
    assert weeks == ["2026-W10"]


def test_the_past_can_be_asked_for():
    p = _proj(data_date="2026-03-02")
    _act(p, "old", "2021-07-05", "2021-07-09", crew=1)
    _act(p, "now", "2026-03-02", "2026-03-06", crew=4)
    assert len(crew_load(p, include_past=True)["weeks"]) == 2


def test_the_week_containing_the_data_date_is_kept():
    p = _proj(data_date="2026-03-04")          # mid-week
    _act(p, "a1", "2026-03-02", "2026-03-06", crew=4)
    assert [w["week"] for w in crew_load(p)["weeks"]] == ["2026-W10"]


def test_the_peak_reflects_only_the_weeks_shown():
    """A huge historical week must not set the scale for staffable work."""
    p = _proj(data_date="2026-03-02")
    _act(p, "old", "2021-07-05", "2021-07-09", crew=99)
    _act(p, "now", "2026-03-02", "2026-03-06", crew=4)
    assert crew_load(p)["peak_crew"] == 4
