"""
test_drawing_reading.py — a drawing goes in, confirmable facts come out.

The model call itself is mocked — what is under test is everything around it:
that the reading is parsed defensively, that the job's own room names are
handed to the model so the sheet is read in the schedule's vocabulary, that
the route fails with a plain sentence rather than a stack trace, and that
nothing a drawing says touches the project until each line is confirmed
through the same door as a typed rule.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.schedule_model import Project, Activity, WBSNode, Calendar
from interpreter.vision import _parse_reading, _job_vocabulary, read_drawing


def _proj():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E", parent_uid=None),
                   WBSNode(uid="m5", name="MV 105", code="M5", parent_uid="w"),
                   WBSNode(uid="m6", name="MV 106", code="M6", parent_uid="w")]
    p.activities = [Activity(uid="a1", activity_id="A1", name="Terminations",
                             wbs_uid="m5", calendar_uid="1",
                             activity_type="Task Dependent", status="Not Started",
                             planned_duration=5.0, remaining_duration=5.0,
                             planned_start="2026-02-02", planned_finish="2026-02-06"),
                    Activity(uid="a2", activity_id="A2", name="QA/QC Inspections",
                             wbs_uid="m5", calendar_uid="1",
                             activity_type="Task Dependent", status="Not Started",
                             planned_duration=5.0, remaining_duration=5.0,
                             planned_start="2026-02-09", planned_finish="2026-02-13")]
    p.build_lookups()
    return p


# ── parsing what the model sends back ─────────────────────────────────────────

def test_a_clean_reading_comes_through_shaped():
    raw = json.dumps({"sheet_number": "E03-021AB", "sheet_title": "Grounding Plan",
                      "discipline": "electrical", "summary": "Grounding for segments A/B.",
                      "rooms": ["MV 105"], "equipment": ["GIS RMU"],
                      "facts": ["Ground grid ties to building steel"],
                      "directives": ["Grounding before energization"]})
    r = _parse_reading(raw)
    assert r["sheet_number"] == "E03-021AB" and r["rooms"] == ["MV 105"]


def test_json_wrapped_in_prose_is_still_found():
    r = _parse_reading('Here is what I see:\n{"summary": "a plan", "facts": []}\nHope that helps!')
    assert r["summary"] == "a plan"


def test_no_json_at_all_is_an_error_not_a_guess():
    try:
        _parse_reading("I cannot read this image.")
        assert False, "should have raised"
    except ValueError:
        pass


def test_missing_fields_default_instead_of_crashing():
    r = _parse_reading('{"summary": "x"}')
    assert r["rooms"] == [] and r["directives"] == [] and r["discipline"] == "other"


def test_runaway_lists_are_capped():
    r = _parse_reading(json.dumps({"facts": [f"f{i}" for i in range(200)]}))
    assert len(r["facts"]) == 25


# ── the model is handed the job's own naming ──────────────────────────────────

def test_the_jobs_room_names_go_with_the_image():
    v = _job_vocabulary(_proj())
    assert "MV 105" in v and "MV 106" in v


def test_no_project_means_no_vocabulary_block():
    assert _job_vocabulary(None) == ""


# ── refusals are plain sentences ──────────────────────────────────────────────

def test_a_file_that_is_not_a_drawing_is_refused():
    try:
        read_drawing(b"hello", "notes.txt")
        assert False
    except RuntimeError as e:
        assert "not a readable image" in str(e)


def test_an_oversize_image_says_snip_tighter():
    try:
        read_drawing(b"x" * (6 * 1024 * 1024), "big.png")
        assert False
    except RuntimeError as e:
        assert "5MB" in str(e)


def test_no_api_key_is_a_settings_message_not_a_crash(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        read_drawing(b"x", "sheet.png", model_key="claude", api_key=None)
        assert False
    except RuntimeError as e:
        assert "key" in str(e).lower()


# ── through the route, model mocked ───────────────────────────────────────────

def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _proj()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


_READING = {"sheet_number": "E03-021AB", "sheet_title": "1st Floor Grounding",
            "discipline": "electrical", "summary": "Grounding plan, segments A and B.",
            "rooms": ["MV 105"], "equipment": ["GIS RMU"],
            "facts": ["Ground grid under MV 105"],
            "directives": ["QA/QC inspections follow terminations in the same room"]}


def test_the_route_returns_the_reading(monkeypatch):
    c = _client()
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: dict(_READING))
    body = c.post("/api/brain/image",
                  data={"file": (io.BytesIO(b"png"), "snip.png")},
                  content_type="multipart/form-data").get_json()
    assert body["success"] and body["reading"]["sheet_number"] == "E03-021AB"


def test_a_reading_alone_changes_nothing_in_the_brain(monkeypatch):
    c = _client()
    import interpreter.vision as vz
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: dict(_READING))
    c.post("/api/brain/image", data={"file": (io.BytesIO(b"png"), "snip.png")},
           content_type="multipart/form-data")
    assert c.get("/api/brain").get_json()["directives"] == []


def test_a_confirmed_line_lands_as_a_grounded_rule(monkeypatch):
    """The click posts the sentence to /api/brain — same door as typing it."""
    c = _client()
    body = c.post("/api/brain",
                  json={"text": _READING["directives"][0]}).get_json()
    assert body["directive"]["kind"] == "order"
    assert body["directive"]["same_area"] is True


def test_a_model_refusal_reads_as_a_sentence(monkeypatch):
    c = _client()
    import interpreter.vision as vz
    def boom(*a, **k):
        raise RuntimeError("Anthropic API key not set. Enter your key in the settings panel.")
    monkeypatch.setattr(vz, "read_drawing", boom)
    resp = c.post("/api/brain/image", data={"file": (io.BytesIO(b"png"), "snip.png")},
                  content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "settings" in resp.get_json()["error"]


def test_no_file_attached_is_refused():
    c = _client()
    assert c.post("/api/brain/image", data={},
                  content_type="multipart/form-data").status_code == 400
