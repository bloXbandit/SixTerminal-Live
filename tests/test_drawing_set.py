"""
test_drawing_set.py — a set of sheets read as a set, not as N unrelated reads.

One sheet at a time is how a drawing gets read; it is not how a drawing SET
gets understood. The riser says one thing, the floor plan another, and the
sequencing note on a third confirms it — evidence that only counts once you
can see it repeat, and which was invisible when every upload was read alone.

What is defended here: provenance survives the merge (you can see which sheet
said what), agreement is counted rather than assumed, a set that disagrees
with itself reports the conflict instead of picking a winner, one bad sheet
does not lose the rest, and — as everywhere else — nothing reaches the project
without being graded against the real schedule first.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.schedule_model import Project, Activity, WBSNode, Calendar
from interpreter import vision as vz


def _project():
    p = Project(uid="p", name="DC", id="25-1539-INT-1", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Std")]
    p.wbs_nodes = [WBSNode(uid="w", name="Electrical", code="E")]
    p.activities = []
    n = 0
    for room in (105, 106, 107):
        uid = f"w{room}"
        p.wbs_nodes.append(WBSNode(uid=uid, name=f"MV {room}", code=f"M{room}",
                                   parent_uid="w"))
        for work in ("Pull Wire", "Terminations"):
            n += 1
            p.activities.append(Activity(
                uid=f"a{n}", activity_id=f"A{1000 + n * 10}", name=work,
                wbs_uid=uid, calendar_uid="1", activity_type="Task Dependent",
                status="Not Started", planned_duration=40.0,
                remaining_duration=40.0, planned_start="2026-02-02",
                planned_finish="2026-02-06"))
    p.build_lookups()
    return p


def _reading(sheet, rooms=None, facts=None, directives=None, flow=None):
    return {"sheet_number": sheet, "sheet_title": f"{sheet} title",
            "discipline": "electrical", "summary": f"{sheet} summary",
            "rooms": rooms or [], "equipment": [], "facts": facts or [],
            "directives": directives or [], "room_flow": flow}


def _files(n=2):
    return [(b"png", f"s{i}.png") for i in range(1, n + 1)]


# ── parsing a room flow ──────────────────────────────────────────────────────

def test_a_flow_needs_a_family_and_two_rooms():
    assert vz._parse_room_flow({"family": "MV", "order": [107, 105],
                                "confidence": "stated"})
    assert vz._parse_room_flow({"family": "MV", "order": [107],
                                "confidence": "stated"}) is None
    assert vz._parse_room_flow({"family": "", "order": [107, 105],
                                "confidence": "stated"}) is None


def test_a_model_saying_it_read_no_order_is_believed():
    """confidence "none" is the model telling us it was not reading a flow —
    promoting that into a rule would invent one."""
    assert vz._parse_room_flow({"family": "MV", "order": [107, 105],
                                "confidence": "none"}) is None


def test_room_numbers_are_dug_out_of_whatever_shape_they_arrive_in():
    f = vz._parse_room_flow({"family": "MV", "order": ["MV 107", "105", "Room 106"],
                             "confidence": "implied"})
    assert f["order"] == [107, 105, 106]


def test_a_repeated_room_is_not_counted_twice():
    f = vz._parse_room_flow({"family": "MV", "order": [107, 105, 107],
                             "confidence": "stated"})
    assert f["order"] == [107, 105]


def test_the_flow_becomes_a_sentence_the_brain_parses_as_a_rule():
    from engine.project_brain import parse_directive, ROOM_ORDER
    f = vz._parse_room_flow({"family": "MV", "order": [107, 105, 106],
                             "confidence": "stated"})
    d = parse_directive(vz.flow_sentence(f))
    assert d.kind == ROOM_ORDER and d.order == [107, 105, 106]


# ── merging across sheets ────────────────────────────────────────────────────

def _read_set(monkeypatch, per_sheet, files=None, project=None):
    calls = iter(per_sheet)
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: next(calls))
    return vz.read_drawing_set(files or _files(len(per_sheet)), project)


def test_facts_from_every_sheet_arrive_together(monkeypatch):
    rep = _read_set(monkeypatch, [
        _reading("E-01", facts=["ground grid under MV 105"]),
        _reading("E-02", facts=["busway feeds MV 106"])])
    assert {f["text"] for f in rep["facts"]} == {
        "ground grid under MV 105", "busway feeds MV 106"}


def test_the_same_fact_on_two_sheets_is_merged_and_remembers_both(monkeypatch):
    rep = _read_set(monkeypatch, [
        _reading("E-01", facts=["Ground grid under MV 105"]),
        _reading("E-02", facts=["ground grid under mv 105"])])
    assert len(rep["facts"]) == 1
    assert rep["facts"][0]["sources"] == ["E-01", "E-02"]


def test_every_item_says_which_sheet_it_came_from(monkeypatch):
    rep = _read_set(monkeypatch, [
        _reading("E-01", rooms=["MV 105"]),
        _reading("E-02", rooms=["MV 106"])])
    by_room = {r["text"]: r["sources"] for r in rep["rooms"]}
    assert by_room["MV 105"] == ["E-01"] and by_room["MV 106"] == ["E-02"]


# ── agreement and disagreement about the flow ────────────────────────────────

_FLOW_A = {"family": "MV", "order": [107, 105, 106], "confidence": "implied",
           "why": "utility entry at 107"}
_FLOW_B = {"family": "MV", "order": [105, 106, 107], "confidence": "implied",
           "why": "numbered left to right"}


def test_two_sheets_agreeing_on_a_flow_is_counted(monkeypatch):
    rep = _read_set(monkeypatch, [_reading("E-01", flow=dict(_FLOW_A)),
                                  _reading("E-02", flow=dict(_FLOW_A))])
    assert len(rep["room_flows"]) == 1
    flow = rep["room_flows"][0]
    assert flow["order"] == [107, 105, 106]
    assert flow["agreed_by"] == 2 and flow["sources"] == ["E-01", "E-02"]


def test_a_set_that_disagrees_with_itself_reports_both_and_proposes_neither(monkeypatch):
    rep = _read_set(monkeypatch, [_reading("E-01", flow=dict(_FLOW_A)),
                                  _reading("E-02", flow=dict(_FLOW_B))])
    assert rep["room_flows"] == []
    assert len(rep["conflicts"]) == 1
    c = rep["conflicts"][0]
    assert c["family"] == "MV"
    assert {tuple(r["order"]) for r in c["readings"]} == {(107, 105, 106), (105, 106, 107)}


def test_a_stated_order_is_preferred_over_an_implied_one(monkeypatch):
    """Both sheets say the same thing; the one that SAYS it carries the why."""
    stated = {**_FLOW_A, "confidence": "stated", "why": "sequence note on sheet"}
    rep = _read_set(monkeypatch, [_reading("E-01", flow=dict(_FLOW_A)),
                                  _reading("E-02", flow=stated)])
    assert rep["room_flows"][0]["confidence"] == "stated"
    assert rep["room_flows"][0]["why"] == "sequence note on sheet"


def test_flows_over_different_families_do_not_collide(monkeypatch):
    other = {"family": "ER", "order": [1, 2], "confidence": "stated", "why": "x"}
    rep = _read_set(monkeypatch, [_reading("E-01", flow=dict(_FLOW_A)),
                                  _reading("E-02", flow=dict(other))])
    assert {f["family"] for f in rep["room_flows"]} == {"MV", "ER"}
    assert not rep["conflicts"]


# ── one bad sheet must not lose the rest ─────────────────────────────────────

def test_a_sheet_that_fails_is_reported_without_losing_the_others(monkeypatch):
    def read(blob, name, *a, **k):
        if name == "s2.png":
            raise RuntimeError("too blurry to read")
        return _reading("E-01", facts=["something real"])
    monkeypatch.setattr(vz, "read_drawing", read)
    rep = vz.read_drawing_set(_files(3), None)
    assert rep["read_count"] == 2 and rep["failed_count"] == 1
    assert rep["facts"]
    bad = [s for s in rep["sheets"] if s["error"]]
    assert len(bad) == 1 and "blurry" in bad[0]["error"]


def test_every_sheet_failing_is_an_error_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("no API key")))
    try:
        vz.read_drawing_set(_files(2), None)
        assert False, "should have raised"
    except RuntimeError as e:
        assert "None of those sheets" in str(e)


def test_an_oversized_batch_is_refused_with_a_number():
    try:
        vz.read_drawing_set(_files(vz.MAX_SHEETS + 1), None)
        assert False, "should have raised"
    except RuntimeError as e:
        assert str(vz.MAX_SHEETS) in str(e)


def test_no_sheets_at_all_is_refused():
    try:
        vz.read_drawing_set([], None)
        assert False
    except RuntimeError:
        pass


# ── through the route ────────────────────────────────────────────────────────

def _client():
    server._projects.clear()
    server._brains.clear()
    sess = server._make_session("t", "t.xml")
    sess["project"] = _project()
    server._projects["t"] = sess
    server._active_id[0] = "t"
    return server.app.test_client()


def _post(c, n=2, question="what do these sheets show"):
    return c.post("/api/brain/image", data={
        "files": [(io.BytesIO(b"png"), f"s{i}.png") for i in range(1, n + 1)],
        "question": question,
    }, content_type="multipart/form-data")


def test_several_sheets_come_back_as_one_drawing_set(monkeypatch):
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        _reading("E-01", facts=["a fact"], flow=dict(_FLOW_A)))
    body = _post(_client()).get_json()
    assert body["type"] == "drawing_set" and body["read_count"] == 2


def test_a_single_sheet_still_takes_the_old_single_sheet_path(monkeypatch):
    """The existing upload must behave exactly as it did."""
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: _reading("E-01"))
    c = _client()
    body = c.post("/api/brain/image",
                  data={"file": (io.BytesIO(b"png"), "one.png")},
                  content_type="multipart/form-data").get_json()
    assert body.get("success") and "reading" in body
    assert body.get("type") != "drawing_set"


def test_a_proposed_flow_is_graded_against_the_real_schedule(monkeypatch):
    """MV 105/106/107 all exist here, so the flow binds."""
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        _reading("E-01", flow=dict(_FLOW_A)))
    body = _post(_client()).get_json()
    flow = body["room_flows"][0]
    assert flow["binds"] is True
    assert "MV 107 → MV 105" in flow["understood"]


def test_a_flow_over_rooms_this_job_does_not_have_is_shown_as_not_binding(monkeypatch):
    ghost = {"family": "MV", "order": [900, 901], "confidence": "stated", "why": "x"}
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        _reading("E-01", flow=dict(ghost)))
    body = _post(_client()).get_json()
    assert body["room_flows"][0]["binds"] is False


def test_reading_a_set_changes_nothing_in_the_brain(monkeypatch):
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        _reading("E-01", flow=dict(_FLOW_A),
                                 directives=["Terminations follow Pull Wire in the same room"]))
    c = _client()
    _post(c)
    assert c.get("/api/brain").get_json()["directives"] == []


def test_confirming_the_flow_lands_it_as_an_enforced_rule(monkeypatch):
    """The click posts the sentence to /api/brain — same door as typing it."""
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        _reading("E-01", flow=dict(_FLOW_A)))
    c = _client()
    flow = _post(c).get_json()["room_flows"][0]
    body = c.post("/api/brain", json={"text": flow["sentence"]}).get_json()
    assert body["directive"]["kind"] == "room_order"
    assert body["directive"]["order"] == [107, 105, 106]


def test_the_agent_is_told_what_the_set_said_and_that_nothing_landed(monkeypatch):
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k:
                        _reading("E-01", flow=dict(_FLOW_A), facts=["a real fact"]))
    c = _client()
    _post(c)
    from interpreter.llm_interpreter import _build_conversation
    seen = _build_conversation(server._projects["t"]["chat_history"])
    assert "ROOM FLOW" in seen and "a real fact" in seen
    assert "NOTHING IS IN THE BRAIN YET" in seen


def test_a_conflicted_set_tells_the_agent_it_conflicts(monkeypatch):
    calls = iter([_reading("E-01", flow=dict(_FLOW_A)),
                  _reading("E-02", flow=dict(_FLOW_B))])
    monkeypatch.setattr(vz, "read_drawing", lambda *a, **k: next(calls))
    c = _client()
    body = _post(c).get_json()
    assert body["conflicts"]
    from interpreter.llm_interpreter import _build_conversation
    assert "CONFLICT" in _build_conversation(server._projects["t"]["chat_history"])
