"""
test_rules_and_udf.py — If/then find-and-change, and user-defined fields.

Two contracts here. A preview must report exactly what an apply would do
while writing nothing — otherwise it is not a preview, and a mistyped pattern
sweeping thousands of activities is expensive even with undo. And a UDF a
schedule arrived with (electrician counts, crew sizes) must survive import,
editing and export, since dropping it loses data the team put in P6 by hand.
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.schedule_model import (Project, Activity, WBSNode, Calendar,
                                   UDFType, compute_dates)
from engine.edit_engine import apply_command, electricians_field
from engine.xml_writer import write_p6_xml
from engine.xml_reader import load_xml


def _project():
    p = Project(uid="1", name="J", id="J", planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes = [
        WBSNode(uid="p1", name="Phase 1", code="P1"),
        WBSNode(uid="p2", name="Phase 2", code="P2"),
    ]

    def a(uid, aid, name, wbs, dur=5.0):
        return Activity(uid=uid, activity_id=aid, name=name, wbs_uid=wbs,
                        calendar_uid="1", planned_duration=dur * 8,
                        remaining_duration=dur * 8,
                        planned_start="2026-01-05", planned_finish="2026-01-09")

    p.activities = [
        a("1", "A1000", "Set Generator (Gen 318)", "p1"),
        a("2", "A1010", "Set Generator (Gen 319)", "p1"),
        a("3", "A1020", "Pull wire", "p1"),
        a("4", "A2000", "Set Generator (Gen 401)", "p2"),
    ]
    p.build_lookups()
    return p


def _rule(where_value, set_field, set_value, **extra):
    r = {"where": {"field": "name", "op": "contains", "value": where_value},
         "set": {"field": set_field, "value": set_value}}
    r["set"].update(extra)
    return r


def _run(p, rules, **kw):
    cmd = {"action": "bulk_rules", "rules": rules}
    cmd.update(kw)
    return apply_command(p, cmd)


# ── Preview ──────────────────────────────────────────────────────────────────

def test_preview_reports_the_count_without_writing_anything():
    p = _project()
    before = [(a.activity_id, a.name, a.planned_duration) for a in p.activities]
    ok, msg = _run(p, [_rule("Set Generator", "duration", 9)], preview=True)
    assert ok and "Would change 3" in msg
    assert [(a.activity_id, a.name, a.planned_duration) for a in p.activities] == before


def test_preview_and_apply_agree_on_the_count():
    p = _project()
    _, preview_msg = _run(_project(), [_rule("Set Generator", "duration", 9)], preview=True)
    _, apply_msg = _run(p, [_rule("Set Generator", "duration", 9)])
    assert re.search(r"Would change (\d+)", preview_msg).group(1) == \
           re.search(r"Changed (\d+)", apply_msg).group(1)


# ── The three change types ───────────────────────────────────────────────────

def test_duration_rule_changes_only_the_matches():
    p = _project()
    _run(p, [_rule("Set Generator", "duration", 9)])
    by = {a.activity_id: a.planned_duration / 8.0 for a in p.activities}
    assert by["A1000"] == 9 and by["A1010"] == 9 and by["A2000"] == 9
    assert by["A1020"] == 5, "an activity that did not match was changed"


def test_electricians_rule_sets_the_udf():
    p = _project()
    _run(p, [_rule("Set Generator", "electricians", "6")])
    field = electricians_field(p)
    assert p.get_activity(activity_id="A1000").udfs[field] == "6"
    assert not p.get_activity(activity_id="A1020").udfs


def test_appending_to_a_name_keeps_what_was_there():
    p = _project()
    _run(p, [_rule("Set Generator", "name", "(ER 209)", mode="append")])
    n = p.get_activity(activity_id="A1000").name
    assert n.startswith("Set Generator") and n.endswith("(ER 209)")


def test_appending_twice_does_not_duplicate_the_text():
    """Re-running a rule is normal — it must not stack the same suffix."""
    p = _project()
    _run(p, [_rule("Set Generator", "name", "(ER 209)", mode="append")])
    ok, msg = _run(p, [_rule("Set Generator", "name", "(ER 209)", mode="append")])
    assert "Changed 0" in msg
    assert p.get_activity(activity_id="A1000").name.count("(ER 209)") == 1


def test_prefix_position_puts_the_text_in_front():
    p = _project()
    _run(p, [_rule("Pull wire", "name", "SW -", mode="append", position="prefix")])
    assert p.get_activity(activity_id="A1020").name.startswith("SW -")


def test_replacing_a_name_outright():
    p = _project()
    _run(p, [_rule("Pull wire", "name", "Pull feeder")])
    assert p.get_activity(activity_id="A1020").name == "Pull feeder"


def test_wbs_rename_rule():
    p = _project()
    _run(p, [{"where": {"field": "wbs_name", "op": "equals", "value": "Phase 1"},
              "set": {"field": "wbs_name", "value": "Phase One"}}])
    assert p.get_wbs("p1").name == "Phase One"
    assert p.get_wbs("p2").name == "Phase 2"


# ── Scope ────────────────────────────────────────────────────────────────────

def test_a_rule_can_be_confined_to_one_folder():
    p = _project()
    _run(p, [_rule("Set Generator", "duration", 9)], wbs_name="Phase 1")
    by = {a.activity_id: a.planned_duration / 8.0 for a in p.activities}
    assert by["A1000"] == 9 and by["A1010"] == 9
    assert by["A2000"] == 5, "the rule escaped its folder"


def test_an_unknown_folder_is_reported():
    p = _project()
    ok, msg = _run(p, [_rule("x", "duration", 1)], wbs_name="Nowhere")
    assert not ok and "not found" in msg.lower()


# ── Matching ─────────────────────────────────────────────────────────────────

def test_match_operators():
    cases = [("equals", "Pull wire", 1), ("starts_with", "Set", 3),
             ("ends_with", "318)", 1), ("not_contains", "Set Generator", 1),
             ("regex", r"Gen \d+", 3)]
    for op, value, expected in cases:
        p = _project()
        _, msg = _run(p, [{"where": {"field": "name", "op": op, "value": value},
                           "set": {"field": "duration", "value": 9}}], preview=True)
        assert f"Would change {expected}" in msg, f"{op} '{value}' gave: {msg}"


def test_matching_is_case_insensitive():
    p = _project()
    _, msg = _run(p, [_rule("set generator", "duration", 9)], preview=True)
    assert "Would change 3" in msg


def test_a_bad_regex_is_reported_not_crashed():
    p = _project()
    ok, msg = _run(p, [{"where": {"field": "name", "op": "regex", "value": "([unclosed"},
                        "set": {"field": "duration", "value": 1}}])
    assert not ok and "pattern" in msg.lower()


def test_an_unsupported_target_field_is_refused():
    p = _project()
    ok, msg = _run(p, [_rule("Set", "float", "5")])
    assert not ok and "Cannot set" in msg


# ── UDFs round-trip ──────────────────────────────────────────────────────────

def test_electricians_field_binds_to_the_project_own_spelling():
    p = _project()
    assert electricians_field(p) == "Number of Electricians"     # default
    p.udf_types = [UDFType(uid="9", title="# Electricians (crew)", data_type="Integer")]
    assert electricians_field(p) == "# Electricians (crew)"


def test_setting_a_udf_also_defines_it_so_the_export_is_valid():
    """A value referencing a column P6 never heard of would not import."""
    p = _project()
    apply_command(p, {"action": "update_udf", "activity_id": "A1000", "value": "6"})
    titles = {u.title for u in p.udf_types}
    assert electricians_field(p) in titles


def test_udf_survives_export_and_reimport():
    p = _project()
    compute_dates(p)
    apply_command(p, {"action": "update_udf", "activity_id": "A1000", "value": "6"})
    field = electricians_field(p)

    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    tmp.close()
    try:
        write_p6_xml(p, tmp.name)
        xml = open(tmp.name).read()
        assert field in xml, "the UDF definition is missing from the export"
        back = load_xml(tmp.name)
    finally:
        os.unlink(tmp.name)

    assert field in {u.title for u in back.udf_types}
    assert back.get_activity(activity_id="A1000").udfs.get(field) == "6"


def test_clearing_a_udf_removes_the_value():
    p = _project()
    apply_command(p, {"action": "update_udf", "activity_id": "A1000", "value": "6"})
    apply_command(p, {"action": "update_udf", "activity_id": "A1000", "value": ""})
    assert not p.get_activity(activity_id="A1000").udfs
