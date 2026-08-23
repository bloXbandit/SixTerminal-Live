"""
cases.py — the claims the system prompt makes, as things that can be scored.

Every case here exists because the prompt asserts something. If a section of
SYSTEM_PROMPT is worth its tokens, it should be possible to write a case that
fails when that section is deleted — and if it isn't, that section is decoration
and the eval is telling you something useful about the prompt.

`claim` names the instruction each case is defending, so a failure points at
the paragraph to go and fix rather than at a vague sense that it got worse.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from evals import fixtures
from evals.checks import (answers_at_all, asks_a_question, command_count_at_most,
                          direction_sane, edits_nothing, emits, field_equals,
                          ids_exist, mentions_an_id, no_action, no_relation,
                          not_says, only_actions, relation,
                          relations_within_same_folder, says, touches_all,
                          touches_only, wbs_names_exist)


@dataclass
class Case:
    id: str
    category: str
    claim: str                       # the prompt instruction under test
    fixture: str
    instruction: str
    checks: List[Callable]
    chat: List[Dict[str, Any]] = field(default_factory=list)
    edits: List[Dict[str, Any]] = field(default_factory=list)

    def project(self):
        return fixtures.ALL[self.fixture]()


# Every case carries ids_exist() and answers_at_all(): an invented id or an
# empty turn is a failure of any request, whatever else the case is about.
def _case(**kw) -> Case:
    kw["checks"] = list(kw["checks"]) + [ids_exist(), answers_at_all()]
    return Case(**kw)


CASES: List[Case] = [

    # ── Sourcing: never invent an id ─────────────────────────────────────────
    _case(
        id="no-invented-id-when-asked-for-a-missing-one",
        category="sourcing",
        claim="NEVER INVENT AN ID — every id must appear verbatim in context",
        fixture="mixed_ids",
        instruction="Tie MDC1.FDG.1320 to the backfill activity.",
        checks=[
            # 1320 does not exist. Naming the gap or asking is fine; quietly
            # emitting a command against it is the failure.
            no_action("add_relation"),
        ],
    ),
    _case(
        id="names-real-ids-when-answering",
        category="sourcing",
        claim="Reference real data: actual activity IDs, WBS names",
        fixture="linked",
        instruction="What drives Megger Test right now?",
        checks=[edits_nothing(), mentions_an_id()],
    ),

    # ── Direction: predecessor happens first ─────────────────────────────────
    _case(
        id="direction-right-when-phrased-backwards",
        category="direction",
        claim="THE PREDECESSOR HAPPENS FIRST — execute the CORRECT direction",
        fixture="linked",
        instruction="Make Terminate Feeders the predecessor of Pull Feeders.",
        checks=[direction_sane(), no_relation("D1020", "D1010")],
    ),
    _case(
        id="direction-right-on-a-plain-tie",
        category="direction",
        claim="THE PREDECESSOR HAPPENS FIRST",
        fixture="linked",
        instruction="Link Terminate Feeders and Megger Test.",
        checks=[emits("add_relation"), direction_sane(),
                relation("D1020", "D1030")],
    ),

    # ── Report is not an edit ────────────────────────────────────────────────
    _case(
        id="wire-means-wire-not-report",
        category="report-vs-edit",
        claim="A REPORT IS NOT AN EDIT / WHEN YOU MEAN TO WIRE, WIRE IT",
        fixture="rooms",
        instruction="Wire up MV 105 — tie the pull to the terminations.",
        checks=[emits("add_relation"), direction_sane(),
                relations_within_same_folder()],
    ),
    _case(
        id="a-question-is-not-an-edit",
        category="report-vs-edit",
        claim="Pure conversation returns only a chat action",
        fixture="rooms",
        instruction="How many activities are missing a predecessor?",
        checks=[edits_nothing()],
    ),

    # ── Room discipline ──────────────────────────────────────────────────────
    _case(
        id="ties-within-a-room-not-across",
        category="area",
        claim="area — same room / level / area, or demonstrably different",
        fixture="rooms",
        instruction="Connect the pull wire and terminations in each MV room.",
        checks=[emits("add_relation"), relations_within_same_folder(),
                direction_sane()],
    ),

    # ── Phase sequencing: the Closeout rule ──────────────────────────────────
    _case(
        id="phase-finish-goes-to-the-next-phase",
        category="phase-sequence",
        claim="THE CLOSEOUT RULE — connect phase-by-phase, never jump",
        fixture="phased",
        instruction="Foundations Complete has no successor. Connect it using proper logic.",
        checks=[emits("add_relation"), direction_sane(),
                # Structure is next; Closeout is three phases away.
                no_relation("B2050", "B2400"), no_relation("B2050", "B2450")],
    ),

    # ── Reaching for the bulk action ─────────────────────────────────────────
    _case(
        id="bulk-rule-not-twelve-edits",
        category="bulk",
        claim="Use bulk_rules instead of emitting hundreds of individual edits",
        fixture="crews",
        instruction="Every light fixture activity should be 3 days.",
        checks=[command_count_at_most(3)],
    ),
    _case(
        id="id-cleanup-uses-the-normalizer",
        category="bulk",
        claim="normalize_activity_ids — do NOT hand-write update_activity_id",
        fixture="mixed_ids",
        instruction="Clean up the activity IDs so they all follow the project pattern.",
        checks=[emits("normalize_activity_ids"), command_count_at_most(2)],
    ),

    # ── Acting rather than asking ────────────────────────────────────────────
    _case(
        id="acts-when-told-to-decide",
        category="act-first",
        claim="RULE 1 — locked out of clarify once the user defers",
        fixture="rooms",
        instruction="Wire MV 106 however you think best — your call, just do it.",
        checks=[asks_a_question(False), emits("add_relation")],
    ),
    _case(
        id="infers-a-duration-unit",
        category="act-first",
        claim="Duration in weeks converts to working days; do not ask",
        fixture="linked",
        instruction="Make Megger Test two weeks.",
        checks=[emits("update_duration"), asks_a_question(False),
                field_equals("update_duration", "new_duration_days", 10)],
    ),

    # ── Following up on the conversation ─────────────────────────────────────
    _case(
        id="same-thing-for-the-list-above",
        category="follow-up",
        claim="BATCH FOLLOW-UPS — resolve the actual prior list",
        fixture="rooms",
        instruction="Do the same thing for the list above.",
        chat=[
            {"role": "user",
             "text": "Set the duration to 8 days on Pull Wire in MV 105 and MV 106"},
            {"role": "assistant", "text": "Done — both set to 8 days.",
             "context": "Applied update_duration on A1010 (Pull Wire, MV 105) "
                        "and A1030 (Pull Wire, MV 106), 8 days each."},
            {"role": "user", "text": "now make them 6"},
            {"role": "assistant", "text": "Done — A1010 and A1030 are 6 days."},
        ],
        edits=[{"instruction": "Set the duration to 8 days on Pull Wire in MV 105 and MV 106",
                "commands": [], "results": [
                    {"action": "update_duration", "success": True,
                     "message": "A1010 duration 5d -> 8d"},
                    {"action": "update_duration", "success": True,
                     "message": "A1030 duration 5d -> 8d"}]}],
        checks=[
            # "the list above" is the two Pull Wire rows, not every activity
            # and not the terminations that sit beside them.
            touches_only("A1010", "A1030"),
            asks_a_question(False),
        ],
    ),
    _case(
        id="refers-back-to-what-was-offered",
        category="follow-up",
        claim="Use CONVERSATION SO FAR to resolve 'the second option'",
        fixture="linked",
        instruction="Apply the second one.",
        chat=[
            {"role": "user", "text": "what should Megger Test connect to?"},
            {"role": "assistant", "text": "Options for D1030",
             "context": "Tie options offered for D1030 — Megger Test:\n"
                        "  Option 1 (predecessor): add_relation D1000 'Install Sleeves' "
                        "-> D1030 'Megger Test' [40% confident]\n"
                        "  Option 2 (predecessor): add_relation D1020 'Terminate Feeders' "
                        "-> D1030 'Megger Test' [85% confident]\n"
                        "If the user picks one by number, issue that add_relation directly."},
        ],
        checks=[relation("D1020", "D1030"), asks_a_question(False)],
    ),

    # ── Not inventing documents ──────────────────────────────────────────────
    _case(
        id="does-not-describe-a-sheet-it-never-saw",
        category="sourcing",
        claim="You have seen a drawing ONLY if its reading is in the conversation",
        fixture="rooms",
        instruction="What did that grounding drawing I sent you say about MV 105?",
        checks=[
            edits_nothing(),
            says(r"do(?:n'?t| not) have|not (?:been )?(?:given|shared|sent)|"
                 r"no .{0,20}(?:drawing|sheet)|attach|upload|paperclip|share it"),
        ],
    ),

    # ── Leaving existing logic alone ─────────────────────────────────────────
    _case(
        id="adds-the-missing-tie-without-redoing-the-chain",
        category="minimal-edit",
        claim="Open ends are a signal; do not repeat logic that exists",
        fixture="linked",
        instruction="Megger Test has no predecessor. Fix that.",
        checks=[emits("add_relation"), command_count_at_most(2),
                direction_sane()],
    ),
]


CATEGORIES = sorted({c.category for c in CASES})


def by_id(case_id: str) -> Optional[Case]:
    return next((c for c in CASES if c.id == case_id), None)


def select(ids=None, categories=None) -> List[Case]:
    out = CASES
    if ids:
        want = set(ids)
        out = [c for c in out if c.id in want]
    if categories:
        want = set(categories)
        out = [c for c in out if c.category in want]
    return out
