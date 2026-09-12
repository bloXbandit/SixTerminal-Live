"""
test_bulk_patterns.py — edits whose value comes from the folder they land in.

Every bulk action here already took a literal: append "(ER 209)" to THIS
folder, create THESE named sub-folders. Fine for one folder, wrong for thirty
— the agent then has to enumerate folders it can only see as samples and get
thirty literals right in one message. It gets most right, invents a couple,
and reports success.

These two iterate the folders themselves and take the text FROM each folder,
so the request is stated once:

  "every activity in a Gen room carries that room's number"
  "put the WBO work in each folder into a WBS sub-folder"

Three things decide whether that is trustworthy rather than merely fast: it
must correct a wrong tag instead of stacking a second one, it must be safe to
run twice, and it must refuse to invent a token for a folder it cannot read.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.bulk_patterns import describe, group_into_subfolder, tag_by_folder
from engine.edit_engine import apply_command
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _job(rooms=("Gen 315", "Gen 316"), acts=("Install High Steel", "Wall Rough-Ins")):
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J"),
                   WBSNode(uid="gens", name="Generator Rooms", code="GR",
                           parent_uid="root")]
    p.activities, p.relations = [], []
    n = 0
    for r in rooms:
        uid = "f" + r.replace(" ", "")
        p.wbs_nodes.append(WBSNode(uid=uid, name=r, code=r.replace(" ", ""),
                                   parent_uid="gens"))
        for nm in acts:
            n += 1
            p.activities.append(Activity(
                uid=f"u{n}", activity_id=f"A{n}0", name=nm, wbs_uid=uid,
                calendar_uid="1", planned_duration=40, remaining_duration=40,
                planned_start="2026-02-02", planned_finish="2026-02-06"))
    p.build_lookups()
    return p


def _named(p, aid):
    return p.get_activity(activity_id=aid).name


GEN = r"Gen\s*\d+"


# ── tagging every activity with its own folder's number ──────────────────────

def test_each_folder_supplies_its_own_token():
    """The whole point: one command, and Gen 315 gets 315 while Gen 316 gets
    316 — neither typed into the command."""
    p = _job()
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, apply=True)
    assert _named(p, "A10") == "Install High Steel (Gen 315)"
    assert _named(p, "A30") == "Install High Steel (Gen 316)"


def test_a_wrong_tag_is_corrected_not_stacked():
    """The state the subject schedule was actually in: activities in the
    Gen 301 folder named "(Gen 322)". Appending would give two tags."""
    p = _job()
    p.activities[0].name = "Install High Steel (Gen 322)"
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, apply=True)
    assert _named(p, "A10") == "Install High Steel (Gen 315)"


def test_an_activity_already_named_right_is_left_alone():
    p = _job()
    p.activities[0].name = "Install High Steel (Gen 315)"
    res = tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, apply=True)
    assert all(c["activity_id"] != "A10" for c in res["changes"])


def test_running_it_twice_changes_nothing_the_second_time():
    p = _job()
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, apply=True)
    again = tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, apply=True)
    assert again["renamed"] == 0


def test_a_token_is_lifted_out_of_a_longer_folder_name():
    """"Gen 315 - JER" should tag "(Gen 315)", so a room and its trade
    sub-folders agree rather than each inventing its own label."""
    p = _job(rooms=("Gen 315 - JER",))
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, apply=True)
    assert _named(p, "A10") == "Install High Steel (Gen 315)"


def test_a_folder_whose_token_cannot_be_read_is_named_not_guessed():
    """"Gen Yard" is not "Gen 0". A made-up number is worse than a gap."""
    p = _job(rooms=("Gen 315", "Gen Yard"))
    res = tag_by_folder(p, folder_pattern="Gen", token_pattern=GEN, apply=True)
    assert _named(p, "A30") == "Install High Steel", "a token was invented"
    assert res["skipped_folders"][0]["folder"] == "Gen Yard"
    assert res["skipped_folders"][0]["activities"] == 2


def test_the_template_is_the_callers_to_choose():
    p = _job()
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN,
                  template="{token} — {name}", apply=True)
    assert _named(p, "A10") == "Gen 315 — Install High Steel"


def test_appending_without_replacing_is_still_available():
    p = _job()
    p.activities[0].name = "Install High Steel (East)"
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN,
                  replace_existing=False, apply=True)
    assert _named(p, "A10") == "Install High Steel (East) (Gen 315)"


def test_a_preview_changes_nothing():
    p = _job()
    res = tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN)
    assert res["renamed"] == 4 and not res["applied"]
    assert _named(p, "A10") == "Install High Steel"


def test_the_preview_count_is_the_real_count_not_the_truncated_list():
    """A list capped for readability that then reports its own length as the
    total understates the edit — which is the number the user says yes to."""
    p = _job(rooms=tuple(f"Gen {300 + i}" for i in range(220)))
    res = tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN)
    assert res["renamed"] == 440
    assert len(res["changes"]) == 400 and res["changes_truncated"]
    assert "440" in describe(res).split("\n")[0]


def test_the_scope_can_be_held_to_one_branch():
    p = _job()
    p.wbs_nodes.append(WBSNode(uid="ph2", name="Phase 2", code="P2", parent_uid="root"))
    p.wbs_nodes.append(WBSNode(uid="g9", name="Gen 999", code="G999", parent_uid="ph2"))
    p.activities.append(Activity(uid="z", activity_id="Z10", name="Elsewhere",
                                 wbs_uid="g9", calendar_uid="1"))
    p.build_lookups()
    gens = next(w for w in p.wbs_nodes if w.uid == "gens")
    tag_by_folder(p, folder_pattern=GEN, token_pattern=GEN, under=gens, apply=True)
    assert _named(p, "Z10") == "Elsewhere", "an edit escaped the branch it was given"


# ── gathering matching work into a sub-folder ────────────────────────────────

def _wbo_job():
    p = _job(rooms=("ER 208", "ER 209"),
             acts=("Wall Rough-Ins", "Epoxy Floor **WBO", "Hang SCR **WBO"))
    return p


def test_a_subfolder_is_made_only_where_there_is_work_for_it():
    p = _wbo_job()
    p.activities = [a for a in p.activities if "WBO" not in a.name
                    or a.wbs_uid == "fER208"]
    p.build_lookups()
    res = group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    assert res["subfolders_created"] == 1
    assert [w.name for w in p.wbs_nodes if w.name.startswith("WBO")] == ["WBO - ER 208"]


def test_the_matching_work_actually_moves_and_nothing_else_does():
    p = _wbo_job()
    group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    sub = next(w for w in p.wbs_nodes if w.name == "WBO - ER 208")
    moved = [a.name for a in p.activities if a.wbs_uid == sub.uid]
    assert sorted(moved) == ["Epoxy Floor **WBO", "Hang SCR **WBO"]
    assert p.get_activity(activity_id="A10").wbs_uid == "fER208"


def test_no_activity_is_lost():
    p = _wbo_job()
    before = len(p.activities)
    group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    assert len(p.activities) == before


def test_running_it_twice_does_not_nest_a_second_subfolder():
    """The second run is otherwise not a no-op but a nesting: the work now
    lives in "WBO - ER 208", that folder matches the same rule, and it gets a
    "WBO - WBO - ER 208" inside it."""
    p = _wbo_job()
    group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    res = group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    assert res["activities_moved"] == 0
    assert not [w for w in p.wbs_nodes if w.name.count("WBO") > 1]


def test_a_folder_that_already_groups_this_work_is_reported_not_wrapped():
    """The subject schedule had "Gen 315 - WBO" and "WBO MV 101" done by hand,
    two conventions already. Wrapping them gives a third."""
    p = _wbo_job()
    p.wbs_nodes.append(WBSNode(uid="own", name="ER 210 - WBO", code="E210W",
                               parent_uid="gens"))
    p.activities.append(Activity(uid="o1", activity_id="O10",
                                 name="Epoxy Floor **WBO", wbs_uid="own",
                                 calendar_uid="1"))
    p.build_lookups()
    res = group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    assert [e["folder"] for e in res["already_grouped"]] == ["ER 210 - WBO"]
    assert p.get_activity(activity_id="O10").wbs_uid == "own", "it was wrapped anyway"
    assert "already group this work" in describe(res)


def test_the_subfolder_name_is_the_callers_to_choose():
    p = _wbo_job()
    group_into_subfolder(p, match=r"\*+\s*WBO",
                         subfolder_template="{parent} (Work by others)", apply=True)
    assert any(w.name == "ER 208 (Work by others)" for w in p.wbs_nodes)


def test_a_preview_creates_nothing():
    p = _wbo_job()
    before = len(p.wbs_nodes)
    res = group_into_subfolder(p, match=r"\*+\s*WBO")
    assert res["subfolders_created"] == 2 and not res["applied"]
    assert len(p.wbs_nodes) == before


def test_work_in_a_sub_folder_is_left_where_it_is_by_default():
    """Otherwise a parent hoovers up rows that already live somewhere
    sensible."""
    p = _wbo_job()
    p.wbs_nodes.append(WBSNode(uid="deep", name="Trim", code="T", parent_uid="fER208"))
    p.get_activity(activity_id="A20").wbs_uid = "deep"
    p.build_lookups()
    group_into_subfolder(p, match=r"\*+\s*WBO", apply=True)
    # It gets its own sub-folder under Trim — what must NOT happen is being
    # pulled up into ER 208's, away from the work it belongs with.
    landed = p.get_activity(activity_id="A20").wbs_uid
    by_uid = {w.uid: w for w in p.wbs_nodes}
    assert by_uid[landed].parent_uid == "deep"


# ── reachable as edit commands ───────────────────────────────────────────────

def test_tagging_runs_as_a_command():
    p = _job()
    ok, msg = apply_command(p, {"action": "tag_by_folder", "folder_pattern": GEN,
                                "token_pattern": GEN})
    assert ok and "Would rename 4" in msg
    assert _named(p, "A10") == "Install High Steel", "a preview wrote to the schedule"


def test_tagging_applies_only_when_explicitly_told_to():
    """A one-line request that reaches 768 rows should be agreed to, not
    discovered afterwards."""
    p = _job()
    apply_command(p, {"action": "tag_by_folder", "folder_pattern": GEN,
                      "token_pattern": GEN})
    assert _named(p, "A10") == "Install High Steel", "the default wrote"
    apply_command(p, {"action": "tag_by_folder", "folder_pattern": GEN,
                      "token_pattern": GEN, "apply": True})
    assert _named(p, "A10") == "Install High Steel (Gen 315)"


def test_grouping_runs_as_a_command():
    p = _wbo_job()
    ok, msg = apply_command(p, {"action": "group_into_subfolder",
                                "match": r"\*+\s*WBO"})
    assert ok and "Would create 2 sub-folders" in msg
    ok, msg = apply_command(p, {"action": "group_into_subfolder",
                                "match": r"\*+\s*WBO", "apply": True})
    assert "Created 2 sub-folders" in msg


def test_grouping_without_a_match_says_what_is_missing():
    ok, msg = apply_command(_wbo_job(), {"action": "group_into_subfolder"})
    assert not ok and "match is required" in msg


def test_an_ambiguous_scope_is_still_refused():
    """The folder resolver's refusal must not be bypassed by a bulk action —
    a pattern edit aimed at the wrong branch is the expensive kind."""
    p = _job()
    p.wbs_nodes.append(WBSNode(uid="d1", name="Rooms", code="R1", parent_uid="root"))
    p.wbs_nodes.append(WBSNode(uid="d2", name="Rooms", code="R2", parent_uid="root"))
    ok, msg = apply_command(p, {"action": "tag_by_folder", "folder_pattern": GEN,
                                "under_wbs": "Rooms"})
    assert not ok and "matches 2 folders" in msg


# ── making a sub-folder carry its parent's number ────────────────────────────
#
# "Gen 326 has a Gen 315 - JER and a Gen 315 - WBO under it; flip the 315s to
# 326 so they all match." The subject schedule had 30 of these, which is how an
# activity in one room ends up labelled with another room's number.

from engine.bulk_patterns import align_child_tokens


def _rooms(*pairs):
    """pairs: (parent name, [child names]) — the Gen-room shape."""
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid="root", name="Job", code="J"),
                   WBSNode(uid="gens", name="Generator Rooms", code="GR",
                           parent_uid="root")]
    p.activities, p.relations = [], []
    n = 0
    for i, (parent, children) in enumerate(pairs):
        pu = f"p{i}"
        p.wbs_nodes.append(WBSNode(uid=pu, name=parent, code=f"P{i}",
                                   parent_uid="gens"))
        for j, ch in enumerate(children):
            cu = f"{pu}c{j}"
            p.wbs_nodes.append(WBSNode(uid=cu, name=ch, code=f"C{i}{j}",
                                       parent_uid=pu))
            n += 1
            p.activities.append(Activity(
                uid=f"u{n}", activity_id=f"A{n}0",
                name=f"Install High Steel ({ch.split(' -')[0].strip()})",
                wbs_uid=cu, calendar_uid="1", planned_duration=40))
    p.build_lookups()
    return p


def _names(p):
    return {w.name for w in p.wbs_nodes}


def test_a_sub_folder_takes_its_parents_number():
    p = _rooms(("Gen 326", ["Gen 315 - JER", "Gen 315 - WBO"]))
    align_child_tokens(p, apply=True)
    assert "Gen 326 - JER" in _names(p) and "Gen 326 - WBO" in _names(p)
    assert "Gen 315 - JER" not in _names(p)


def test_only_the_number_changes_and_the_rest_of_the_name_survives():
    """"Gen 318- JER" has no space before the dash. Rebuilding the name from
    a template would tidy that away and change something nobody asked about."""
    p = _rooms(("Gen 306", ["Gen 318- JER"]))
    align_child_tokens(p, apply=True)
    assert "Gen 306- JER" in _names(p)


def test_a_sub_folder_already_matching_is_left_alone():
    p = _rooms(("Gen 326", ["Gen 326 - JER"]))
    assert align_child_tokens(p, apply=True)["renamed"] == 0


def test_running_it_twice_changes_nothing_the_second_time():
    p = _rooms(("Gen 326", ["Gen 315 - JER"]))
    align_child_tokens(p, apply=True)
    assert align_child_tokens(p, apply=True)["renamed"] == 0


def test_a_different_kind_of_tag_is_reported_not_rewritten():
    """An "MV 101" under an "ER 208" is a folder in the wrong PLACE. Renaming
    it to "ER 208" would bury that instead of showing it."""
    p = _rooms(("ER 208", ["MV 101"]))
    res = align_child_tokens(p, apply=True)
    assert res["renamed"] == 0
    assert res["wrong_kind"][0]["folder"] == "MV 101"
    assert "MV 101" in _names(p)
    assert "different kind of tag" in describe(res)


def test_a_sub_folder_with_no_tag_at_all_is_untouched():
    """"Rough-Ins" under "Gen 326" is a stage, not a mis-numbered room."""
    p = _rooms(("Gen 326", ["Rough-Ins"]))
    assert align_child_tokens(p, apply=True)["renamed"] == 0
    assert "Rough-Ins" in _names(p)


def test_the_activities_inside_are_brought_in_line_too():
    """Renaming the folder alone leaves every activity still reading the old
    number, which is half a job and looks like the tool failed."""
    p = _rooms(("Gen 326", ["Gen 315 - JER"]))
    align_child_tokens(p, apply=True)
    assert _named(p, "A10") == "Install High Steel (Gen 326)"


def test_the_activities_can_be_left_alone_if_asked():
    p = _rooms(("Gen 326", ["Gen 315 - JER"]))
    align_child_tokens(p, retag_activities=False, apply=True)
    assert "Gen 326 - JER" in _names(p)
    assert _named(p, "A10") == "Install High Steel (Gen 315)"


def test_a_correction_passes_down_to_a_grandchild_in_the_same_run():
    """Folders are walked parents-first, so a folder fixed on this pass hands
    its NEW number down rather than the one it arrived with."""
    p = _rooms(("Gen 326", ["Gen 315 - JER"]))
    kid = next(w for w in p.wbs_nodes if w.name == "Gen 315 - JER")
    p.wbs_nodes.append(WBSNode(uid="gc", name="Gen 301 - Trim", code="GC",
                               parent_uid=kid.uid))
    p.build_lookups()
    align_child_tokens(p, apply=True)
    assert "Gen 326 - Trim" in _names(p)


def test_a_preview_changes_nothing():
    p = _rooms(("Gen 326", ["Gen 315 - JER"]))
    res = align_child_tokens(p)
    assert res["renamed"] == 1 and not res["applied"]
    assert "Gen 315 - JER" in _names(p)
    assert _named(p, "A10") == "Install High Steel (Gen 315)"


def test_it_runs_as_a_command_and_previews_by_default():
    p = _rooms(("Gen 326", ["Gen 315 - JER"]))
    ok, msg = apply_command(p, {"action": "align_child_tokens"})
    assert ok and "Would rename 1 sub-folder" in msg
    assert "Gen 315 - JER" in _names(p)
    apply_command(p, {"action": "match_subfolders_to_parent", "apply": True})
    assert "Gen 326 - JER" in _names(p)
