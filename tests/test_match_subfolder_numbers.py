"""
test_match_subfolder_numbers.py — subfolders take their parent folder's number.

A phase copied from another one carries its subfolder names with it: Gen 312's
children still say "Gen 311 - JER" because Gen 311 is where they came from.
The parent numbers are the ones the user corrected, so they are the truth, and
asking a model to hand-write dozens of rename_wbs commands from a compressed
tree map is how wrong numbers land. The engine walks the real tree instead:
each child's number becomes its parent's, the separator is normalised to
" - ", and anything that does not fit the pattern is named in the report
rather than guessed at.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.edit_engine import apply_commands
from engine.schedule_model import Calendar, Project, WBSNode


def _p():
    p = Project(uid="p", name="J", id="J", data_date="2026-01-05",
                planned_start="2026-01-05")
    p.calendars = [Calendar(uid="1", name="S")]
    p.wbs_nodes, p.activities, p.relations = [], [], []
    return p


def _w(p, uid, name, parent=None):
    p.wbs_nodes.append(WBSNode(uid=uid, name=name, code=uid, parent_uid=parent))
    p.build_lookups()


def _name(p, uid):
    return next(w.name for w in p.wbs_nodes if w.uid == uid)


def _gen_job():
    """Phase 2 Generators as the user found it: parent numbers right, the JER
    and WBO subfolders still carrying Phase-1-era numbers. Phase 1 already
    matches and must stay untouched."""
    p = _p()
    _w(p, "PH1", "Phase 1")
    _w(p, "G301", "Gen 301", "PH1")
    _w(p, "G301J", "Gen 301 - JER", "G301")

    _w(p, "PH2", "Phase 2")
    _w(p, "GEN", "Generators", "PH2")
    _w(p, "G312", "Gen 312", "GEN")
    _w(p, "G312J", "Gen 311 - JER", "G312")
    _w(p, "G312W", "Gen 311 -WBO", "G312")
    _w(p, "G313", "Gen 313", "GEN")
    _w(p, "G313J", "Gen 311 - JER", "G313")
    _w(p, "G313W", "Gen 311-WBO", "G313")
    _w(p, "G314", "Gen 314", "GEN")
    _w(p, "G314J", "Gen 314 - JER", "G314")      # already correct
    return p


def _run(p, cmd=None):
    cmd = cmd or {"action": "match_subfolder_numbers", "wbs_name": "Generators"}
    ok, msg = apply_commands(p, [cmd])[0]
    assert ok, msg
    return msg


# ── the fix itself ───────────────────────────────────────────────────────────

def test_subfolders_take_their_parents_number():
    p = _gen_job()
    _run(p)
    assert _name(p, "G312J") == "Gen 312 - JER"
    assert _name(p, "G313J") == "Gen 313 - JER"


def test_the_separator_is_normalised():
    """'Gen 311 -WBO' and 'Gen 311-WBO' both come out as 'NNN - WBO' — the
    user asked for one style, not a preserved typo."""
    p = _gen_job()
    _run(p)
    assert _name(p, "G312W") == "Gen 312 - WBO"
    assert _name(p, "G313W") == "Gen 313 - WBO"


def test_already_correct_children_are_left_alone():
    p = _gen_job()
    msg = _run(p)
    assert _name(p, "G314J") == "Gen 314 - JER"
    assert "Gen 314 - JER" not in msg, "an unchanged folder is not a rename"


def test_the_scope_holds_phase_1_is_untouched():
    p = _gen_job()
    _run(p)
    assert _name(p, "G301J") == "Gen 301 - JER"


def test_the_report_names_every_rename():
    msg = _run(_gen_job())
    assert "4 folders renamed" in msg
    assert "'Gen 311 - JER' → 'Gen 312 - JER'" in msg


# ── what it refuses to guess ─────────────────────────────────────────────────

def test_an_unrelated_child_is_not_renamed():
    """'Commissioning' under Gen 312 is not part of the numbering pattern."""
    p = _gen_job()
    _w(p, "G312C", "Commissioning", "G312")
    msg = _run(p)
    assert _name(p, "G312C") == "Commissioning"
    assert "Commissioning" not in msg, "not part of the pattern, not noise"


def test_a_prefix_child_with_no_number_is_reported_not_guessed():
    """'Gen - JER' shares the parent's prefix and lost its number — that is a
    real member of the pattern, so it is named for the user to decide."""
    p = _gen_job()
    _w(p, "G312X", "Gen - JER", "G312")
    msg = _run(p)
    assert _name(p, "G312X") == "Gen - JER"
    assert "Gen - JER" in msg and "left alone" in msg


def test_two_numbers_is_ambiguous_and_reported():
    p = _gen_job()
    _w(p, "G312B", "Gen 311 Bay 2 - JER", "G312")
    msg = _run(p)
    assert _name(p, "G312B") == "Gen 311 Bay 2 - JER"
    assert "more than one number" in msg


def test_container_folders_are_walked_not_matched():
    """'Generators' and 'Phase 2' have no number — their children must not be
    treated as pattern members of a numberless parent."""
    p = _gen_job()
    _run(p)
    assert _name(p, "G312") == "Gen 312", "the parents themselves never move"


# ── plumbing ─────────────────────────────────────────────────────────────────

def test_a_clean_area_says_so():
    p = _gen_job()
    _run(p)
    msg = _run(p)                        # second pass: nothing left to fix
    assert "already match" in msg


def test_the_aliases_work():
    p = _gen_job()
    _run(p, {"action": "renumber_subfolders", "wbs_name": "Generators"})
    assert _name(p, "G312J") == "Gen 312 - JER"


def test_a_missing_scope_fails_loudly():
    ok, msg = apply_commands(_gen_job(), [
        {"action": "match_subfolder_numbers", "wbs_name": "Turbines"}])[0]
    assert not ok
    assert "Turbines" in msg or "No WBS" in msg or "not found" in msg.lower()
