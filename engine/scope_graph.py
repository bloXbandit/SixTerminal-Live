"""
scope_graph.py — the flow a scope document describes, as something usable.

A scope of work is not really 497 rows. It is a much smaller set of claims:
which systems are on this job, how far each one is taken (delivered? set?
terminated? commissioned?), and in which phase. Everything else in those rows
is quantity and boilerplate. So the rows are distilled into that — a few dozen
nodes — and the rows themselves are never sent to a model.

The SHAPE of the flow is generic MEP knowledge and does not come from the
document: every system runs design → procure → rough-in → install → connect →
test → energize → commission → turnover, and within a phase each system's last
working stage feeds that phase's commissioning. What the document supplies is
which parts of that shape are REAL on this job, and where. Without it, the
tool would have to assume every system exists in every phase — which is how
you get a tie proposed between work that is not in anybody's contract.

That is the whole bargain: the document grounds a general model of how MEP
gets built in the specifics of this job.

It is deliberately weaker than what the user has said. A rule they typed came
from someone who walked the job; this came from parsing a PDF. Where the two
disagree, the rule wins and the disagreement is reported. Ordering, strongest
first:

    1. rules the user taught
    2. this graph — the contract document
    3. the schedule's own dates
    4. inference from names and trade order

`verdict()` deliberately mirrors project_brain.directive_verdict, so the tie
ranker consumes it through the machinery that already exists rather than
growing a second, parallel notion of "something that has an opinion".
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from .logic_advisor import EQUIPMENT_TERMS, phase_number

# ── The lifecycle every MEP system runs through ──────────────────────────────
# Rank order is the whole point: two activities on one system in one phase are
# ordered by their stage, and nothing else needs to be known about them.

STAGES: List[Tuple[int, str, Tuple[str, ...]]] = [
    (10, "design", ("submittal", "shop drawing", "shop dwg", "design",
                    "engineering", "ifc", "coordination", "bim", "approval",
                    "review", "permit")),
    (20, "procure", ("procure", "procurement", "purchase", "buy-out", "buyout",
                     "award", "fabricate", "fabrication", "manufactur",
                     "deliver", "delivery", "lead time", "long lead", "ship")),
    (30, "rough_in", ("rough-in", "rough in", "roughin", "sleeve", "hanger",
                      "support", "embed", "underground", "conduit", "raceway",
                      "cable tray", "duct bank", "ductbank", "core drill",
                      "layout", "backbox", "back box")),
    (40, "install", ("install", "installation", "set ", "setting", "rig ",
                     "rigging", "erect", "mount", "place", "hoist",
                     "furnish and install")),
    (50, "connect", ("connect", "connection", "terminate", "termination",
                     "wire", "pull wire", "pull cable", "cable pull", "splice",
                     "tie-in", "tie in", "weld", "braze", "final connection")),
    (60, "test", ("test", "testing", "megger", "hi-pot", "hipot",
                  "pressure test", "flush", "clean", "balanc", "tab ",
                  "point to point", "point-to-point", "inspection", "qa/qc",
                  "qaqc", "pre-functional", "prefunctional")),
    (70, "energize", ("energize", "energization", "energisation", "startup",
                      "start-up", "start up", "first power", "backfeed")),
    (80, "commission", ("commission", "commissioning", " cx", "cx ",
                        "functional test", "integrated system", "ist ")),
    (90, "turnover", ("turnover", "handover", "closeout", "close-out",
                      "substantial completion", "o&m", "training", "as-built",
                      "record drawing", "punch")),
]

_STAGE_RANK = {name: rank for rank, name, _ in STAGES}
_STAGE_NAME = {rank: name for rank, name, _ in STAGES}
COMMISSION_RANK = _STAGE_RANK["commission"]

# The pseudo-system for commissioning that names a phase rather than a piece
# of equipment — "Phase 2 commissioning" is about the phase, and it is what
# every other system in that phase hands off to.
PHASE_CX = "phase commissioning"

# ── The systems an MEP scope covers ─────────────────────────────────────────
# Built on the equipment vocabulary the procurement checker already uses, so
# the two do not drift apart, plus the distribution and mechanical systems a
# scope document names that are not discrete equipment.

_EXTRA_SYSTEMS: List[Tuple[str, Tuple[str, ...]]] = [
    ("feeder", ("feeder", "branch circuit", "wire and conduit")),
    ("grounding", ("grounding", "ground grid", "bonding")),
    ("lighting", ("lighting", "light fixture", "luminaire")),
    ("fire alarm", ("fire alarm", "notification device", "smoke detect")),
    ("ahu", ("ahu", "air handler", "air handling")),
    ("pump", ("pump",)),
    ("piping", ("piping", "chilled water", "condenser water", "hydronic",
                "pipe run")),
    ("ductwork", ("ductwork", "sheet metal", "ductwork riser")),
    ("plumbing", ("domestic water", "sanitary", "storm drain", "plumbing")),
    ("sprinkler", ("sprinkler", "standpipe", "fire protection")),
    ("controls", ("bms", " bas ", "controls", "ddc", "instrumentation")),
]

SYSTEMS: List[Tuple[str, Tuple[str, ...]]] = list(EQUIPMENT_TERMS) + _EXTRA_SYSTEMS


def classify(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    (system, stage) for a line of scope OR an activity name.

    One classifier for both on purpose: a scope line and the activity that
    delivers it have to land on the same node or nothing can ever be matched
    up. Either half may be None — plenty of lines name a system with no verb,
    or a verb with no system, and half a classification is still worth having.
    """
    low = f" {(text or '').lower()} "
    system = next((name for name, words in SYSTEMS
                   if any(w in low for w in words)), None)
    stage = None
    best = -1
    for rank, name, words in STAGES:
        if any(w in low for w in words) and rank > best:
            # The LATEST stage a line mentions is the one it delivers:
            # "furnish, install and terminate" ends at terminate.
            stage, best = name, rank
    return system, stage


@dataclass
class ScopeNode:
    system: str
    stage: str
    phase: Optional[int]
    lines: int = 0                      # scope lines that landed here
    examples: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.system}|{self.stage}|{self.phase if self.phase else '-'}"

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScopeGraph:
    nodes: Dict[str, ScopeNode] = field(default_factory=dict)
    source: str = ""
    line_count: int = 0
    classified: int = 0

    # -- what the document established ------------------------------------
    def systems(self) -> List[str]:
        return sorted({n.system for n in self.nodes.values()})

    def phases(self) -> List[Optional[int]]:
        return sorted({n.phase for n in self.nodes.values()},
                      key=lambda p: (p is None, p))

    def stages_for(self, system: str, phase: Optional[int]) -> List[str]:
        got = [n.stage for n in self.nodes.values()
               if n.system == system and n.phase == phase]
        return sorted(got, key=lambda s: _STAGE_RANK[s])

    def has(self, system: str, stage: str, phase: Optional[int]) -> bool:
        return f"{system}|{stage}|{phase if phase else '-'}" in self.nodes

    def edges(self) -> List[Tuple[str, str, str]]:
        """
        (from_key, to_key, why) for every ordering this document supports.

        Two kinds, and only two, because both are defensible from the
        document alone: a system's stages run in order, and within a phase
        every system's last working stage feeds that phase's commissioning.
        """
        out = []
        for system in self.systems():
            for phase in self.phases():
                stages = self.stages_for(system, phase)
                for a, b in zip(stages, stages[1:]):
                    out.append((f"{system}|{a}|{phase if phase else '-'}",
                                f"{system}|{b}|{phase if phase else '-'}",
                                f"{system}: {a} before {b}"))
        # everything feeds its own phase's commissioning
        for phase in self.phases():
            cx = [n for n in self.nodes.values()
                  if n.phase == phase and n.stage == "commission"]
            if not cx:
                continue
            target = cx[0]
            for n in self.nodes.values():
                if n.phase != phase or n.stage == "commission":
                    continue
                if _STAGE_RANK[n.stage] >= COMMISSION_RANK:
                    continue
                out.append((n.key, target.key,
                            f"{n.system} {n.stage} feeds commissioning"
                            + (f" in phase {phase}" if phase else "")))
        return out

    # -- the opinion the ranker consumes ----------------------------------
    def verdict(self, pred_name: str, succ_name: str,
                pred_where: str = "", succ_where: str = "") -> Optional[str]:
        """
        "supports" | "violates" | None for one candidate tie.

        Same signature as project_brain.directive_verdict so the tie ranker
        treats it as one more thing with an opinion, rather than needing a
        parallel notion of evidence.

        Says nothing unless the document actually covers BOTH sides. A graph
        that opines on work it never mentioned would be inventing sequence,
        not reading it.
        """
        p_sys, p_stage = classify(f"{pred_name} {pred_where}")
        s_sys, s_stage = classify(f"{succ_name} {succ_where}")
        if not p_stage or not s_stage:
            return None
        p_phase = phase_number(f"{pred_name} {pred_where}")
        s_phase = phase_number(f"{succ_name} {succ_where}")
        if p_phase and s_phase and p_phase != s_phase:
            return None                       # different phases — not this tie

        pr, sr = _STAGE_RANK[p_stage], _STAGE_RANK[s_stage]

        # Same system: the lifecycle orders them, provided the document put
        # that system in this phase at all.
        if p_sys and s_sys and p_sys == s_sys:
            if not self._covers(p_sys, p_phase or s_phase):
                return None
            if sr > pr:
                return "supports"
            if sr < pr:
                return "violates"
            return None

        # Different (or unknown) systems: the only claim the document supports
        # across systems is that work feeds its own phase's commissioning.
        if s_stage == "commission" and pr < COMMISSION_RANK:
            if p_sys and self._covers(p_sys, p_phase or s_phase):
                return "supports"
            return None
        if p_stage == "commission" and sr < COMMISSION_RANK:
            if s_sys and self._covers(s_sys, s_phase or p_phase):
                return "violates"
        return None

    def _covers(self, system: str, phase: Optional[int]) -> bool:
        """
        Did the document put this system in this phase?

        When the PHASE is unknown — plenty of activities carry it in neither
        their name nor their folder — the question collapses to "is this
        system in the document at all". Demanding a phase match there would
        silence the graph on any schedule that does not spell the phase out
        on every row, which is most of them.
        """
        if system == PHASE_CX:
            return False              # a target, never evidence about a system
        if phase is None:
            return any(n.system == system for n in self.nodes.values())
        if any(n.system == system and n.phase == phase for n in self.nodes.values()):
            return True
        # A document that never phases anything still covers the system.
        return any(n.system == system and n.phase is None
                   for n in self.nodes.values())

    def explain(self, pred_name: str, succ_name: str,
                pred_where: str = "", succ_where: str = "") -> str:
        p_sys, p_stage = classify(f"{pred_name} {pred_where}")
        s_sys, s_stage = classify(f"{succ_name} {succ_where}")
        if p_sys and p_sys == s_sys:
            return f"scope: {p_sys} runs {p_stage} before {s_stage}"
        if s_stage == "commission":
            return f"scope: {p_sys or p_stage} feeds commissioning"
        return "scope"

    # -- what the agent is told -------------------------------------------
    def context_block(self, cap: int = 14) -> str:
        """
        The document in a dozen lines. The 497 rows never go near a prompt —
        what the agent needs is which systems exist, how far each is taken,
        and in which phase.
        """
        if not self.nodes:
            return ""
        out = ["", "SCOPE OF WORK (read from the contract document — weaker "
                   "than anything you were told directly, stronger than a guess "
                   "from names):"]
        shown = 0
        for phase in self.phases():
            label = f"Phase {phase}" if phase else "no phase stated"
            systems = sorted({n.system for n in self.nodes.values()
                              if n.phase == phase})
            if not systems:
                continue
            out.append(f"  {label}:")
            for system in systems:
                if shown >= cap:
                    out.append("    …more not listed")
                    return "\n".join(out)
                stages = self.stages_for(system, phase)
                out.append(f"    {system}: " + " → ".join(stages))
                shown += 1
        out.append("  Work of one system runs in that order, and each system "
                   "feeds its own phase's commissioning. Use this to decide "
                   "ties; say so when it disagrees with the dates.")
        return "\n".join(out)

    def to_json(self) -> Dict[str, Any]:
        return {"source": self.source, "line_count": self.line_count,
                "classified": self.classified,
                "nodes": [n.to_json() for n in self.nodes.values()]}

    @classmethod
    def from_json(cls, data: Any) -> Optional["ScopeGraph"]:
        if not isinstance(data, dict) or not data.get("nodes"):
            return None
        g = cls(source=data.get("source", ""),
                line_count=int(data.get("line_count") or 0),
                classified=int(data.get("classified") or 0))
        for raw in data["nodes"]:
            if not isinstance(raw, dict) or not raw.get("system"):
                continue
            if raw.get("stage") not in _STAGE_RANK:
                continue
            node = ScopeNode(system=raw["system"], stage=raw["stage"],
                             phase=raw.get("phase"),
                             lines=int(raw.get("lines") or 0),
                             examples=list(raw.get("examples") or [])[:3])
            g.nodes[node.key] = node
        return g or None


def build(lines: List[Any], source: str = "") -> ScopeGraph:
    """
    Distil scope lines into the flow they describe.

    `lines` are ScopeLine objects or plain strings. A line that names neither
    a system nor a stage contributes nothing and is counted, not guessed at —
    plenty of a scope document is quantities and boilerplate, and the honest
    answer for those is that they say nothing about sequence.
    """
    g = ScopeGraph(source=source)
    for item in lines:
        text = item if isinstance(item, str) else getattr(item, "text", "")
        g.line_count += 1
        system, stage = classify(text)
        # Commissioning is the one stage that routinely names no equipment:
        # "Phase 2 commissioning of the standby power system" is about the
        # PHASE, and it is the node everything else in that phase points at.
        # Requiring a system here would drop the target and leave every
        # "feeds commissioning" edge with nowhere to go.
        if stage == "commission" and not system:
            system = PHASE_CX
        if not system or not stage:
            continue
        phase = phase_number(text)
        node = ScopeNode(system=system, stage=stage, phase=phase)
        node = g.nodes.setdefault(node.key, node)
        node.lines += 1
        if len(node.examples) < 3:
            node.examples.append(text[:140])
        g.classified += 1
    return g


def read_and_build(source: Any, name: str = "") -> Tuple[ScopeGraph, Dict[str, Any]]:
    """Read a scope PDF and distil it. Returns (graph, read report)."""
    from . import scope_reader
    report = scope_reader.read_scope(source)
    graph = build(report["lines"], source=name or "scope document")
    return graph, report
