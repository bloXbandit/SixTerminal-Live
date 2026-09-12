"""
test_wbs_resolution.py — never edit a folder nobody named.

Naming a folder used to be a bare substring scan that returned the FIRST hit
and said nothing about the rest. On a real job that is not a near miss, it is
routinely the wrong folder: of 203 distinct folder names in the subject
schedule, 70 match more than one, and asking for "Area 1" returned
"Precast Area 1" — silently. The edit then reported success against work
nobody meant to touch, in a file that gets imported into P6.

The rule now is strictest first — uid, code, exact name, path, then a
substring only when it lands on exactly one folder — and a query that still
matches several RAISES with all of them. A refusal that lists the candidates
costs one more turn. A silent wrong folder costs an edit.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine.edit_engine import EditError, _find_wbs
from engine.schedule_model import Activity, Calendar, Project, WBSNode


def _job(*folders):
    """folders: (uid, name, code, parent_uid)"""
    p = Project(uid="p", name="Job", id="J1", data_date="2026-01-05")
    p.calendars = [Calendar(uid="1", name="Standard")]
    p.wbs_nodes = [WBSNode(uid=u, name=n, code=c, parent_uid=par)
                   for u, n, c, par in folders]
    p.activities, p.relations = [], []
    p.build_lookups()
    return p


# The shape that actually caused this: a short name that is a prefix of a
# longer one, several folders repeating a name under different parents, and a
# folder whose own name contains a slash.
def _real():
    return _job(
        ("root", "Job", "J", None),
        ("ph1", "Phase 1", "P1", "root"),
        ("ph2", "Phase 2", "P2", "root"),
        ("slab", "Slabs", "SL", "root"),
        ("a1", "Area 1", "AREA1", "slab"),
        ("a11", "Area 11", "AREA11", "slab"),
        ("pa1", "Precast Area 1", "PCA1", "slab"),
        ("mv1", "MV Rooms", "MV1", "ph1"),
        ("mv2", "MV Rooms", "MV2", "ph2"),
        ("g315", "Gen 315", "G315", "ph1"),
        ("g315j", "Gen 315 - JER", "G315J", "ph1"),
        ("proc", "Procurement/ Pre-Construction", "PRE", "root"),
        ("uniq", "Deep Foundations", "DF", "root"),
    )


# ── the bug ──────────────────────────────────────────────────────────────────

def test_an_exact_name_beats_a_longer_folder_that_contains_it():
    """The reported case: "Area 1" silently returned "Precast Area 1"."""
    assert _find_wbs(_real(), wbs_name="Area 1").uid == "a1"


def test_a_short_name_does_not_land_on_a_longer_one():
    assert _find_wbs(_real(), wbs_name="Area 11").uid == "a11"


def test_a_name_that_several_folders_share_is_refused_not_guessed():
    with pytest.raises(EditError) as e:
        _find_wbs(_real(), wbs_name="MV Rooms")
    assert "matches 2 folders" in str(e.value)


def test_the_refusal_names_every_candidate_by_its_full_path():
    """A refusal that does not say what the choices are just costs a turn."""
    with pytest.raises(EditError) as e:
        _find_wbs(_real(), wbs_name="MV Rooms")
    msg = str(e.value)
    assert "Job / Phase 1 / MV Rooms" in msg
    assert "Job / Phase 2 / MV Rooms" in msg


def test_an_ambiguous_substring_is_refused_too():
    """"Gen 315" is inside "Gen 315 - JER"; picking either silently is a
    coin toss."""
    with pytest.raises(EditError):
        _find_wbs(_real(), wbs_name="Gen 3")


# ── what still works ─────────────────────────────────────────────────────────

def test_a_unique_name_resolves_straight_through():
    assert _find_wbs(_real(), wbs_name="Deep Foundations").uid == "uniq"


def test_a_unique_substring_still_resolves():
    """Deliberately kept: refusing a substring that lands on exactly one
    folder would break the ordinary case for no gain."""
    assert _find_wbs(_real(), wbs_name="Deep Found").uid == "uniq"


def test_a_uid_is_exact_and_beats_everything():
    assert _find_wbs(_real(), wbs_uid="pa1", wbs_name="Area 1").uid == "pa1"


def test_a_code_resolves_exactly():
    assert _find_wbs(_real(), wbs_code="AREA11").uid == "a11"


def test_a_code_is_matched_whatever_its_case():
    assert _find_wbs(_real(), wbs_code="area11").uid == "a11"


def test_a_folder_that_does_not_exist_is_still_just_not_found():
    assert _find_wbs(_real(), wbs_name="Nowhere At All") is None


def test_nothing_asked_for_resolves_to_nothing():
    assert _find_wbs(_real()) is None


# ── telling same-named folders apart by path ─────────────────────────────────

def test_a_path_picks_between_folders_that_share_a_name():
    assert _find_wbs(_real(), wbs_name="Phase 1 / MV Rooms").uid == "mv1"
    assert _find_wbs(_real(), wbs_name="Phase 2 / MV Rooms").uid == "mv2"


def test_a_path_may_skip_levels():
    """Segments must appear in order, not be adjacent — so a folder nested
    three deep is reachable without naming every level in between."""
    assert _find_wbs(_real(), wbs_name="Job / Area 1").uid == "a1"


def test_an_exact_last_segment_beats_a_partial_one():
    """".../ Gen 315" means that folder, not "Gen 315 - JER" beside it."""
    assert _find_wbs(_real(), wbs_name="Phase 1 / Gen 315").uid == "g315"


def test_a_path_that_still_matches_several_is_refused():
    p = _job(("root", "Job", "J", None),
             ("a", "Phase 1", "P1", "root"),
             ("b", "Rooms", "R1", "a"),
             ("c", "Rooms", "R2", "a"))
    with pytest.raises(EditError):
        _find_wbs(p, wbs_name="Phase 1 / Rooms")


# ── a folder whose own name contains a slash ─────────────────────────────────

def test_a_folder_named_with_a_slash_is_not_read_as_a_path():
    """P6 schedules are full of these — "Procurement/ Pre-Construction",
    "Exterior/ Site Utilities". Splitting the name on its own slash would
    make the folder unreachable by the name it actually has."""
    got = _find_wbs(_real(), wbs_name="Procurement/ Pre-Construction")
    assert got.uid == "proc"


# ── the whole point, on the shape of a real schedule ─────────────────────────

def test_no_query_ever_returns_a_folder_that_does_not_match_it():
    """The invariant. Every name in the schedule must either resolve to a
    folder carrying that name, or refuse — never to some other folder."""
    p = _real()
    for w in p.wbs_nodes:
        try:
            got = _find_wbs(p, wbs_name=w.name)
        except EditError:
            continue                      # refused, which is allowed
        assert w.name.lower() in got.name.lower(), (
            f"asking for {w.name!r} returned {got.name!r}")
