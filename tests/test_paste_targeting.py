"""
test_paste_targeting.py — Pasted rows must land under the folder the user
picked in the paste dialog.

Two ways this went wrong, both from treating WBS codes as identifiers:
  - An incoming section whose name-derived code matched ANY folder in the
    schedule was merged into that folder, ignoring the chosen target
    entirely ("Sitework" pasted into Phase 2 landed in the root Sitework).
  - The dialog itself identified the chosen folder by code, and codes repeat
    in real schedules (P6 short names are only unique among siblings), so
    the first same-code folder won.
The dialog now targets by uid, and code-based folder reuse is restricted to
folders already under the chosen target.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from engine.schedule_model import WBSNode


def _client():
    return server.app.test_client()


def _load(c, text, name="Job"):
    r = c.post("/api/import/paste", json={"text": text, "project_name": name})
    c.post("/api/import/commit", json={"contract": r.get_json()["contract"],
                                       "mode": "replace", "project_name": name})


def _grid(c):
    s = c.get("/api/schedule").get_json()
    return {w["name"] + "|" + w["uid"]: [a["activity_id"] for a in w["activities"]]
            for w in s["wbs_sections"]}, s["wbs_sections"]


def _paste_into(c, text, target_uid, **extra):
    ct = c.post("/api/import/paste", json={"text": text}).get_json()["contract"]
    body = {"contract": ct, "mode": "merge", "flatten": False, "dedupe": "off",
            "target_wbs_uid": target_uid}
    body.update(extra)
    return c.post("/api/import/commit", json=body)


def test_same_name_section_elsewhere_does_not_hijack_the_target():
    c = _client()
    _load(c, "Sitework\nA1000\tOld task\t5\t05-Jan-26\t09-Jan-26")
    c.post("/api/direct", json={"commands": [{"action": "add_wbs", "name": "Phase 2"}],
                                "label": "a"})
    _, secs = _grid(c)
    p2 = next(w for w in secs if w["name"] == "Phase 2")

    _paste_into(c, "Sitework\nA2000\tNew task\t5\t02-Feb-26\t06-Feb-26", p2["uid"])

    _, secs = _grid(c)
    old_site = next(w for w in secs if w["name"] == "Sitework" and w["parent_uid"] != p2["uid"])
    new_site = next(w for w in secs if w["name"] == "Sitework" and w["parent_uid"] == p2["uid"])
    assert [a["activity_id"] for a in old_site["activities"]] == ["A1000"]
    assert [a["activity_id"] for a in new_site["activities"]] == ["A2000"]


def test_repasting_the_same_section_reuses_the_under_target_folder():
    """Re-pasting must not stack duplicate section folders under the target."""
    c = _client()
    _load(c, "Sitework\nA1000\tOld task\t5\t05-Jan-26\t09-Jan-26")
    c.post("/api/direct", json={"commands": [{"action": "add_wbs", "name": "Phase 2"}],
                                "label": "a"})
    _, secs = _grid(c)
    p2_uid = next(w["uid"] for w in secs if w["name"] == "Phase 2")

    _paste_into(c, "Sitework\nA2000\tTask one\t5\t02-Feb-26\t06-Feb-26", p2_uid)
    _paste_into(c, "Sitework\nA2100\tTask two\t5\t09-Feb-26\t13-Feb-26", p2_uid)

    _, secs = _grid(c)
    under_p2 = [w for w in secs if w["parent_uid"] == p2_uid]
    assert len(under_p2) == 1                       # one Sitework, not two
    assert sorted(a["activity_id"] for a in under_p2[0]["activities"]) == ["A2000", "A2100"]


def test_uid_targeting_hits_the_right_folder_when_codes_collide():
    """Two folders named Electrical with the IDENTICAL code — the uid sent by
    the dialog must land the paste in exactly the one that was picked."""
    c = _client()
    _load(c, "A1\tSeed\t1\t05-Jan-26\t05-Jan-26", name="J2")
    c.post("/api/direct", json={"commands": [
        {"action": "add_wbs", "name": "Area A"},
        {"action": "add_wbs", "name": "Area B"}], "label": "a"})
    c.post("/api/direct", json={"commands": [
        {"action": "add_wbs", "name": "Electrical", "parent_name": "Area A"}], "label": "a"})
    proj = server._get_session()["project"]
    area_b = next(w for w in proj.wbs_nodes if w.name == "Area B")
    elec_a = next(w for w in proj.wbs_nodes if w.name == "Electrical")
    proj.wbs_nodes.append(WBSNode(uid="777", name="Electrical", code=elec_a.code,
                                  parent_uid=area_b.uid))

    r = _paste_into(c, "A9000\tPull wire\t5\t02-Feb-26\t06-Feb-26", "777")
    assert r.get_json().get("success")

    _, secs = _grid(c)
    b_elec = next(w for w in secs if w["uid"] == "777")
    a_elec = next(w for w in secs if w["uid"] == elec_a.uid)
    assert [a["activity_id"] for a in b_elec["activities"]] == ["A9000"]
    assert a_elec["activities"] == []


def test_a_deleted_target_reports_instead_of_misplacing():
    c = _client()
    _load(c, "A1\tSeed\t1\t05-Jan-26\t05-Jan-26")
    r = _paste_into(c, "A9000\tRow\t5\t02-Feb-26\t06-Feb-26", "gone-uid")
    assert r.status_code == 400
    assert "no longer exists" in r.get_json()["error"]


def test_legacy_code_targeting_still_works():
    c = _client()
    _load(c, "Sitework\nA1000\tOld task\t5\t05-Jan-26\t09-Jan-26")
    ct = c.post("/api/import/paste",
                json={"text": "A2000\tRow\t5\t02-Feb-26\t06-Feb-26"}).get_json()["contract"]
    r = c.post("/api/import/commit", json={"contract": ct, "mode": "merge",
                                           "flatten": False, "dedupe": "off",
                                           "target_wbs_code": "SITEWORK"})
    assert r.get_json().get("success")
    _, secs = _grid(c)
    site = next(w for w in secs if w["name"] == "Sitework")
    assert "A2000" in [a["activity_id"] for a in site["activities"]]
