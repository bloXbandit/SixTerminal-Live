"""
test_sheet_sync.py — "make my dates match this screenshot", safely.

Somebody sends a lookahead or an owner's status report. Two asks come off the
same image: "how are we tracking?" and "make mine match." Both need the rows
paired to real activities and the differences shown before anything moves.

The care here is all about ACTUAL dates. Writing one asserts that work really
happened on a day; taking that from a possibly-misread screenshot and applying
it silently would corrupt history that nobody can reconstruct. So an actual is
only proposed when the source marked it as one, overwriting an existing actual
is flagged, and the risky rows are not ticked by default.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine import sheet_sync
from engine.schedule_model import Project, Activity, WBSNode, Calendar
from interpreter.vision import classify_image_intent, _iso


def _proj():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", data_date="2026-03-02",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None)]
    p.activities = []
    for uid, name, s, f in [("a1", "Pull Wire MV 105", "2026-02-02", "2026-02-06"),
                            ("a2", "Terminations MV 105", "2026-02-09", "2026-02-13"),
                            ("a3", "QA/QC Inspections MV 105", "2026-02-16", "2026-02-20")]:
        p.activities.append(Activity(
            uid=uid, activity_id=uid.upper(), name=name, wbs_uid="w",
            calendar_uid="1", activity_type="Task Dependent", status="Not Started",
            planned_duration=5.0, remaining_duration=5.0,
            planned_start=s, planned_finish=f))
    p.build_lookups()
    return p


def _row(**kw):
    base = {"activity_id": None, "name": None, "start": None, "finish": None,
            "actual_start": None, "actual_finish": None,
            "percent_complete": None, "status": None}
    base.update(kw)
    return base


# ── which read does the ask want ──────────────────────────────────────────────

def test_a_bare_upload_is_a_drawing():
    assert classify_image_intent("") == "drawing"
    assert classify_image_intent(None) == "drawing"


def test_asking_to_match_dates_and_status_is_a_schedule_read():
    assert classify_image_intent(
        "only match the activity dates and actualization status to my project") == "schedule"


def test_asking_how_we_track_is_a_schedule_read():
    assert classify_image_intent("how do we track against this") == "schedule"


def test_asking_what_a_sheet_shows_stays_a_drawing():
    assert classify_image_intent("what does this one-line diagram show") == "drawing"


# ── transcription is not trusted blindly ──────────────────────────────────────

def test_only_a_real_iso_date_survives():
    assert _iso("2026-03-04") == "2026-03-04"
    assert _iso("03/04/26") is None
    assert _iso("2026-13-40") is None
    assert _iso(None) is None


# ── pairing rows to activities ────────────────────────────────────────────────

def test_an_exact_id_matches():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A2", finish="2026-02-20")])
    assert r["matched"][0]["activity_id"] == "A2"
    assert r["matched"][0]["match"] == "id"


def test_an_exact_name_matches_when_there_is_no_id():
    r = sheet_sync.match_rows(_proj(), [_row(name="Terminations MV 105",
                                             finish="2026-02-20")])
    assert r["matched"][0]["activity_id"] == "A2"
    assert r["matched"][0]["match"] == "name"


def test_a_near_name_matches_but_is_flagged_as_near():
    r = sheet_sync.match_rows(_proj(), [_row(name="Terminations MV105",
                                             finish="2026-02-20")])
    assert r["matched"][0]["match"] == "close"


def test_work_that_is_not_ours_is_reported_not_guessed():
    r = sheet_sync.match_rows(_proj(), [_row(name="Hang Ductwork Level 4")])
    assert not r["matched"] and len(r["unmatched"]) == 1
    assert "no activity" in r["unmatched"][0]["why"]


def test_agreement_is_reported_as_agreement():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1", start="2026-02-02",
                                             finish="2026-02-06")])
    assert r["matched"][0]["changes"] == []
    assert r["rows_with_changes"] == 0


# ── what differs ──────────────────────────────────────────────────────────────

def test_a_moved_finish_is_reported_with_both_sides():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1", finish="2026-02-27")])
    c = r["matched"][0]["changes"][0]
    assert c["field"] == "finish" and c["from"] == "2026-02-06" and c["to"] == "2026-02-27"


def test_writing_a_new_actual_is_marked_as_the_serious_thing_it_is():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1",
                                             actual_start="2026-02-02")])
    c = r["matched"][0]["changes"][0]
    assert c["severity"] == "actual"
    assert "actually happened" in c["note"]


def test_overwriting_an_actual_already_recorded_is_high_severity():
    p = _proj()
    p.get_activity(activity_id="A1").actual_start = "2026-02-02"
    r = sheet_sync.match_rows(p, [_row(activity_id="A1", actual_start="2026-01-19")])
    c = r["matched"][0]["changes"][0]
    assert c["severity"] == "high" and "overwrites" in c["note"]


def test_reopening_a_completed_activity_is_high_severity():
    p = _proj()
    p.get_activity(activity_id="A1").status = "Completed"
    r = sheet_sync.match_rows(p, [_row(activity_id="A1", status="In Progress")])
    c = next(x for x in r["matched"][0]["changes"] if x["field"] == "status")
    assert c["severity"] == "high" and "reopens" in c["note"]


def test_a_field_the_image_could_not_read_is_left_alone():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1", finish=None,
                                             start=None)])
    assert r["matched"][0]["changes"] == []


def test_an_out_of_range_percentage_is_ignored():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1",
                                             percent_complete=250)])
    assert r["matched"][0]["changes"] == []


# ── turning accepted differences into edits ───────────────────────────────────

def _matched(**kw):
    return sheet_sync.match_rows(_proj(), [_row(activity_id="A1", **kw)])["matched"]


def test_the_start_is_set_before_the_finish():
    """Typing a finish adjusts DURATION against the current start, so order
    is not cosmetic — reversed, the dates land right and the duration wrong."""
    cmds = sheet_sync.to_commands(_proj(), _matched(start="2026-02-03",
                                                    finish="2026-02-27"))
    assert [c["field"] for c in cmds] == ["start", "finish"]
    assert all(c["action"] == "update_planned_date" for c in cmds)


def test_an_actual_becomes_a_set_actual_date_command():
    cmds = sheet_sync.to_commands(_proj(), _matched(actual_finish="2026-02-06"))
    assert cmds[0]["action"] == "set_actual_date"
    assert cmds[0]["field"] == "finish" and cmds[0]["date"] == "2026-02-06"


def test_every_command_it_builds_is_one_the_engine_accepts():
    """The shape has to be right, not just plausible — a command the engine
    rejects fails silently as 'nothing changed'."""
    from engine.edit_engine import apply_commands
    p = _proj()
    m = sheet_sync.match_rows(p, [_row(activity_id="A1", start="2026-02-03",
                                       finish="2026-02-27",
                                       actual_start="2026-02-03",
                                       percent_complete=40,
                                       status="In Progress")])["matched"]
    cmds = sheet_sync.to_commands(p, m)
    for cmd, (ok, msg) in zip(cmds, apply_commands(p, cmds)):
        assert ok, f"{cmd} -> {msg}"


def test_progress_becomes_a_set_progress_command():
    cmds = sheet_sync.to_commands(_proj(), _matched(percent_complete=40,
                                                    status="In Progress"))
    prog = next(c for c in cmds if c["action"] == "set_progress")
    assert prog["percent_complete"] == 40 and prog["status"] == "In Progress"


def test_only_the_named_fields_are_allowed_to_move():
    """"Only match the dates and actualization" has to mean ONLY those."""
    m = _matched(start="2026-02-03", percent_complete=40)
    cmds = sheet_sync.to_commands(_proj(), m, only=["start", "finish"])
    assert [c["action"] for c in cmds] == ["update_planned_date"]


def test_nothing_ticked_produces_no_commands():
    assert sheet_sync.to_commands(_proj(), []) == []


# ── through the routes ────────────────────────────────────────────────────────

def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _proj()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


_READ = {"source_title": "Owner 3-Week Lookahead", "data_date": "2026-03-02",
         "rows": [{"activity_id": "A1", "name": "Pull Wire MV 105",
                   "start": None, "finish": "2026-02-27",
                   "actual_start": "2026-02-02", "actual_finish": None,
                   "percent_complete": 60, "status": "In Progress"}],
         "notes": ["Dates read as DD/MM"]}


def _post(c, monkeypatch, question):
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_schedule", lambda *a, **k: dict(_READ))
    return c.post("/api/brain/image",
                  data={"file": (io.BytesIO(b"png"), "shot.png"),
                        "question": question},
                  content_type="multipart/form-data").get_json()


def test_a_status_ask_comes_back_as_a_comparison(monkeypatch):
    c = _client()
    body = _post(c, monkeypatch, "match the dates and actualization status to my project")
    assert body["type"] == "schedule_image"
    assert body["rows_matched"] == 1 and body["rows_with_changes"] == 1
    assert body["source_title"] == "Owner 3-Week Lookahead"


def test_reading_it_changes_absolutely_nothing(monkeypatch):
    c = _client()
    _post(c, monkeypatch, "match my dates to this")
    a = server._projects["t"]["project"].get_activity(activity_id="A1")
    assert a.planned_finish == "2026-02-06" and not a.actual_start


def test_the_agent_is_told_in_writing_that_nothing_moved(monkeypatch):
    c = _client()
    _post(c, monkeypatch, "match my dates to this")
    from interpreter.llm_interpreter import _build_conversation
    seen = _build_conversation(server._projects["t"]["chat_history"])
    assert "NOTHING HAS BEEN CHANGED" in seen
    assert "A1" in seen and "2026-02-27" in seen


def test_applying_the_ticked_rows_actually_moves_them(monkeypatch):
    c = _client()
    body = _post(c, monkeypatch, "match my dates to this")
    out = c.post("/api/sheet/apply",
                 json={"matched": body["matched"]}).get_json()
    assert out["commands_applied"] >= 1
    a = server._projects["t"]["project"].get_activity(activity_id="A1")
    assert a.planned_finish == "2026-02-27"


def test_excluded_fields_stay_put_when_applying(monkeypatch):
    c = _client()
    body = _post(c, monkeypatch, "match my dates to this")
    c.post("/api/sheet/apply", json={"matched": body["matched"],
                                     "fields": ["finish"]})
    a = server._projects["t"]["project"].get_activity(activity_id="A1")
    assert a.planned_finish == "2026-02-27"
    assert not a.actual_start          # not in `fields`, so never written


def test_applying_nothing_is_refused():
    c = _client()
    assert c.post("/api/sheet/apply", json={"matched": []}).status_code == 400


def test_an_apply_is_undoable(monkeypatch):
    c = _client()
    body = _post(c, monkeypatch, "match my dates to this")
    c.post("/api/sheet/apply", json={"matched": body["matched"]})
    c.post("/api/undo")
    a = server._projects["t"]["project"].get_activity(activity_id="A1")
    assert a.planned_finish == "2026-02-06"


def test_a_drawing_question_never_reaches_the_schedule_reader(monkeypatch):
    c = _client()
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_schedule",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong mode")))
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: {
        "sheet_number": "E1", "sheet_title": None, "discipline": "electrical",
        "summary": "s", "rooms": [], "equipment": [], "facts": [], "directives": []})
    body = c.post("/api/brain/image",
                  data={"file": (io.BytesIO(b"png"), "s.png"),
                        "question": "what does this riser diagram show"},
                  content_type="multipart/form-data").get_json()
    assert body["success"] and "reading" in body


# ── a status change is an actualization, not a label ──────────────────────────
# Caught in the browser: "Actual start" was left unticked and an actual start
# was written anyway, because the ticked "Status → In Progress" carries one.
# P6 defines the states by which actuals exist, so the engine was right and the
# grading was wrong — the tick must reflect what the change really does.

def test_marking_something_in_progress_is_graded_as_an_actualization():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1", status="In Progress")])
    c = next(x for x in r["matched"][0]["changes"] if x["field"] == "status")
    assert c["severity"] == "actual"
    assert "writes an actual start" in c["note"]


def test_marking_something_complete_is_graded_as_an_actualization():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1", status="Completed")])
    c = next(x for x in r["matched"][0]["changes"] if x["field"] == "status")
    assert c["severity"] == "actual" and "actual start and finish" in c["note"]


def test_restatusing_over_an_existing_actual_is_high_severity():
    p = _proj()
    a = p.get_activity(activity_id="A1")
    a.actual_start, a.status = "2026-02-02", "In Progress"
    r = sheet_sync.match_rows(p, [_row(activity_id="A1", status="Completed")])
    c = next(x for x in r["matched"][0]["changes"] if x["field"] == "status")
    assert c["severity"] == "actual"      # no actual FINISH yet — still a first write
    p2 = _proj()
    b = p2.get_activity(activity_id="A1")
    b.actual_start, b.actual_finish, b.status = "2026-02-02", "2026-02-06", "Completed"
    r2 = sheet_sync.match_rows(p2, [_row(activity_id="A1", status="In Progress")])
    c2 = next(x for x in r2["matched"][0]["changes"] if x["field"] == "status")
    assert c2["severity"] == "high" and "reopens" in c2["note"]


def test_a_plain_percentage_is_still_an_ordinary_edit():
    r = sheet_sync.match_rows(_proj(), [_row(activity_id="A1", percent_complete=40)])
    c = next(x for x in r["matched"][0]["changes"] if x["field"] == "percent_complete")
    assert c["severity"] == "normal"


def test_no_actual_is_written_when_only_the_safe_fields_are_taken():
    """The tick has to hold all the way to the schedule, not just the diff."""
    from engine.edit_engine import apply_commands
    p = _proj()
    m = sheet_sync.match_rows(p, [_row(activity_id="A1", finish="2026-02-27",
                                       actual_start="2026-02-02",
                                       status="In Progress")])["matched"]
    cmds = sheet_sync.to_commands(p, m, only=["finish"])
    apply_commands(p, cmds)
    a = p.get_activity(activity_id="A1")
    assert a.planned_finish == "2026-02-27"
    assert not a.actual_start and a.status == "Not Started"
