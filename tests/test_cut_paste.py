"""
test_cut_paste.py — cut-and-paste of activities and folders, and the paste
that used to vanish.

The reported failure: copy a block of activities, delete them, then paste.
Nothing appeared. copy_activities names the source rows by activity_id, the
engine could not find the deleted ones, and it rejected the ENTIRE command —
so the paste was silently dropped. Two changes cover it:
  - the engine skips rows that no longer exist instead of failing outright;
  - the grid saves each row's full data at copy time, so when the sources are
    gone it rebuilds them (with dates and constraints) rather than sending a
    command that names ghosts.

Cut is the other half: move_activities relocates rows without duplicating
them, so IDs, dates, constraints and every link — including links leaving the
selection, which a copy has to drop — survive the move.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


def _client():
    return server.app.test_client()


def _load(c, text, name="Job"):
    r = c.post("/api/import/paste", json={"text": text, "project_name": name})
    c.post("/api/import/commit", json={"contract": r.get_json()["contract"],
                                       "mode": "replace", "project_name": name})


def _secs(c):
    return c.get("/api/schedule").get_json()["wbs_sections"]


def _uid(c, name):
    return next(w["uid"] for w in _secs(c) if w["name"] == name)


def _acts(c, name):
    return {a["activity_id"]: a for w in _secs(c) if w["name"] == name
            for a in w["activities"]}


def _direct(c, cmds, label="t"):
    return c.post("/api/direct", json={"commands": cmds, "label": label})


SAMPLE = ("Sitework\n"
          "A1000\tClear and grub\t5\t05-Jan-26\t09-Jan-26\n"
          "A1010\tRough grade\t5\t12-Jan-26\t16-Jan-26\n"
          "Structure\n"
          "A2000\tFootings\t10\t19-Jan-26\t30-Jan-26\n")


# ── the reported bug ──────────────────────────────────────────────────────────

def test_copy_delete_then_paste_still_lands_rows():
    """The exact sequence that produced nothing: copy, delete, paste."""
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")

    _direct(c, [{"action": "delete_activity", "activity_id": "A1000"},
                {"action": "delete_activity", "activity_id": "A1010"}])

    # the grid now falls back to the data it saved at copy time
    r = _direct(c, [
        {"action": "add_activity", "wbs_uid": dst, "name": "Clear and grub",
         "duration_days": 5, "planned_start": "2026-01-05", "planned_finish": "2026-01-09"},
        {"action": "add_activity", "wbs_uid": dst, "name": "Rough grade",
         "duration_days": 5, "planned_start": "2026-01-12", "planned_finish": "2026-01-16"},
    ])
    assert r.status_code == 200

    got = _acts(c, "Structure")
    assert len(got) == 3                                   # A2000 plus the two pasted
    names = {a["name"] for a in got.values()}
    assert {"Clear and grub", "Rough grade"} <= names
    # and they came back with their dates, not blank
    for a in got.values():
        if a["name"] in ("Clear and grub", "Rough grade"):
            assert a["planned_start"], f"{a['name']} pasted with a blank start"


def test_grid_paste_skips_deleted_rows_instead_of_failing():
    """One stale id must not reject the whole paste — for the grid's replay."""
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    _direct(c, [{"action": "delete_activity", "activity_id": "A1010"}])

    r = _direct(c, [{"action": "copy_activities", "skip_missing": True,
                     "activity_ids": ["A1000", "A1010"], "wbs_uid": dst}])
    assert r.status_code == 200
    assert len(_acts(c, "Structure")) == 2                 # A2000 + the surviving copy


def test_agent_copy_still_fails_loudly_on_an_unknown_id():
    """Without the flag a typo is reported, and nothing is half-copied."""
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    r = _direct(c, [{"action": "copy_activities",
                     "activity_ids": ["A1000", "NOPE"], "wbs_uid": dst}])
    assert "nope" in str(r.get_json()).lower()
    assert len(_acts(c, "Structure")) == 1                 # A2000 only


def test_copy_reports_when_every_source_is_gone():
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    _direct(c, [{"action": "delete_activity", "activity_id": "A1000"},
                {"action": "delete_activity", "activity_id": "A1010"}])

    r = _direct(c, [{"action": "copy_activities", "skip_missing": True,
                     "activity_ids": ["A1000", "A1010"], "wbs_uid": dst}])
    body = r.get_json()
    assert "no longer exist" in str(body).lower() or "still exist" in str(body).lower()
    assert len(_acts(c, "Structure")) == 1                 # nothing invented


# ── add_activity now carries the whole row ────────────────────────────────────

def test_pasted_rows_keep_dates_constraint_and_udfs():
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    _direct(c, [{"action": "add_activity", "wbs_uid": dst, "name": "Rebuilt",
                 "duration_days": 4,
                 "planned_start": "2026-03-02", "planned_finish": "2026-03-05",
                 "constraint_type": "Start On", "constraint_date": "2026-03-02",
                 "udfs": {"Number of Electricians": "6"}}])
    a = next(v for v in _acts(c, "Structure").values() if v["name"] == "Rebuilt")
    assert a["planned_start"].startswith("2026-03-02")
    assert a["constraint_type"] == "Start On"
    assert a["udfs"].get("Number of Electricians") == "6"


# ── cut and paste activities ──────────────────────────────────────────────────

def test_move_activities_relocates_without_duplicating():
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    before = c.get("/api/schedule").get_json()["activity_count"]

    _direct(c, [{"action": "move_activities",
                 "activity_ids": ["A1000", "A1010"], "wbs_uid": dst}])

    after = c.get("/api/schedule").get_json()
    assert after["activity_count"] == before               # moved, not copied
    assert set(_acts(c, "Structure")) == {"A2000", "A1000", "A1010"}
    assert _acts(c, "Sitework") == {}


def test_move_activities_keeps_ids_dates_and_outside_logic():
    """A cut keeps links that leave the selection — a copy cannot."""
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    _direct(c, [{"action": "add_relation",
                 "predecessor_id": "A1010", "successor_id": "A2000"}])
    start_before = _acts(c, "Sitework")["A1000"]["planned_start"]

    _direct(c, [{"action": "move_activities",
                 "activity_ids": ["A1000", "A1010"], "wbs_uid": dst}])

    moved = _acts(c, "Structure")
    assert moved["A1000"]["planned_start"] == start_before  # dates untouched
    # the A1010 → A2000 link crossed the selection boundary and survived
    succs = [s["activity_id"] for s in moved["A1010"].get("successors", [])]
    assert "A2000" in succs


def test_move_activities_needs_a_real_target():
    c = _client()
    _load(c, SAMPLE)
    r = _direct(c, [{"action": "move_activities",
                     "activity_ids": ["A1000"], "wbs_uid": "no-such-folder"}])
    assert "not found" in str(r.get_json()).lower()
    assert set(_acts(c, "Sitework")) == {"A1000", "A1010"}


def test_move_activities_skips_rows_that_are_gone_for_the_grid():
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    _direct(c, [{"action": "delete_activity", "activity_id": "A1010"}])
    r = _direct(c, [{"action": "move_activities", "skip_missing": True,
                     "activity_ids": ["A1000", "A1010"], "wbs_uid": dst}])
    assert r.status_code == 200
    assert set(_acts(c, "Structure")) == {"A2000", "A1000"}


def test_agent_move_fails_loudly_on_an_unknown_id():
    """No flag, no partial move: the schedule is left exactly as it was."""
    c = _client()
    _load(c, SAMPLE)
    dst = _uid(c, "Structure")
    r = _direct(c, [{"action": "move_activities",
                     "activity_ids": ["A1000", "NOPE"], "wbs_uid": dst}])
    assert "nope" in str(r.get_json()).lower()
    assert set(_acts(c, "Sitework")) == {"A1000", "A1010"}   # A1000 did not move


# ── cut and paste folders ─────────────────────────────────────────────────────

def test_move_wbs_targets_by_uid_not_name():
    """
    Folder moves used to be addressed by name, and name matching is a
    substring match — with repeated names the move lands in the wrong branch.
    """
    c = _client()
    _load(c, SAMPLE)
    _direct(c, [{"action": "add_wbs", "name": "Site"}])     # substring of "Sitework"
    site = _uid(c, "Site")
    sitework = _uid(c, "Sitework")

    _direct(c, [{"action": "move_wbs", "wbs_uid": sitework, "parent_uid": site}])

    secs = {w["uid"]: w for w in _secs(c)}
    assert secs[sitework]["parent_uid"] == site


def test_move_wbs_carries_its_activities():
    c = _client()
    _load(c, SAMPLE)
    _direct(c, [{"action": "add_wbs", "name": "Phase 2"}])
    p2 = _uid(c, "Phase 2")
    sitework = _uid(c, "Sitework")

    _direct(c, [{"action": "move_wbs", "wbs_uid": sitework, "parent_uid": p2}])

    assert set(_acts(c, "Sitework")) == {"A1000", "A1010"}
    assert next(w for w in _secs(c) if w["uid"] == sitework)["parent_uid"] == p2


def test_move_wbs_refuses_to_nest_a_folder_inside_itself():
    c = _client()
    _load(c, SAMPLE)
    sitework = _uid(c, "Sitework")
    r = _direct(c, [{"action": "move_wbs", "wbs_uid": sitework, "parent_uid": sitework}])
    assert "itself" in str(r.get_json()).lower()


def test_move_wbs_refuses_a_cycle_through_a_child():
    c = _client()
    _load(c, SAMPLE)
    sitework = _uid(c, "Sitework")
    _direct(c, [{"action": "add_wbs", "name": "Under", "parent_uid": sitework}])
    under = _uid(c, "Under")
    r = _direct(c, [{"action": "move_wbs", "wbs_uid": sitework, "parent_uid": under}])
    assert "underneath" in str(r.get_json()).lower()


def test_add_wbs_makes_a_subfolder_under_the_uid_given():
    """Right-click → New sub-folder targets the clicked folder by uid."""
    c = _client()
    _load(c, SAMPLE)
    sitework = _uid(c, "Sitework")
    _direct(c, [{"action": "add_wbs", "name": "Utilities", "parent_uid": sitework}])
    sub = next(w for w in _secs(c) if w["name"] == "Utilities")
    assert sub["parent_uid"] == sitework
