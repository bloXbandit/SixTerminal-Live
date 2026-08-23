"""
checks.py — what a good answer looks like, stated so a machine can score it.

Most LLM evals need a human or a judge model because the output is prose.
This one mostly does not: the agent emits structured commands against a
schedule we already have, so "did it invent an id", "did it get the direction
right", "did it reach for the bulk action" are all decidable by looking.

That is the whole reason an eval harness is worth building here rather than
being an aspiration. Where judgement genuinely is needed — tone, whether an
explanation is any good — there is `says`/`not_says` on the chat message, and
nothing pretends those are as strong as the structural ones.

A check takes (project, commands, chat_message) and returns (ok, detail).
Detail is shown on failure and must say what actually happened, not repeat
what was wanted — "emitted clarify" beats "expected no clarify".
"""

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

Result = Tuple[bool, str]


def _edits(commands: List[Dict]) -> List[Dict]:
    return [c for c in commands
            if c.get("action") not in (None, "chat", "clarify", "error")]


def _actions(commands: List[Dict]) -> List[str]:
    return [c.get("action") for c in _edits(commands)]


def _ids_in(cmd: Dict) -> List[Tuple[str, str]]:
    out = []
    for key in ("activity_id", "predecessor_id", "successor_id", "target_id"):
        val = cmd.get(key)
        if isinstance(val, str) and val.strip():
            out.append((key, val.strip()))
    return out


# ── what it did ──────────────────────────────────────────────────────────────

def emits(action: str) -> Callable:
    """At least one command of this action."""
    def check(project, commands, chat) -> Result:
        got = _actions(commands)
        return (action in got,
                f"emitted {got or 'no edit commands'}")
    check.__name__ = f"emits({action})"
    return check


def no_action(action: str) -> Callable:
    def check(project, commands, chat) -> Result:
        got = _actions(commands)
        return action not in got, f"emitted {got or 'no edit commands'}"
    check.__name__ = f"no_action({action})"
    return check


def only_actions(*allowed: str) -> Callable:
    """Nothing outside this set — catches an answer that also did something else."""
    def check(project, commands, chat) -> Result:
        got = _actions(commands)
        extra = [a for a in got if a not in allowed]
        return not extra, f"also emitted {extra}" if extra else f"emitted {got}"
    check.__name__ = f"only_actions({','.join(allowed)})"
    return check


def edits_nothing() -> Callable:
    """A question is answered, not acted on."""
    def check(project, commands, chat) -> Result:
        got = _actions(commands)
        return not got, f"emitted {got}"
    check.__name__ = "edits_nothing"
    return check


def command_count_at_most(n: int) -> Callable:
    """
    The bulk-action test. Twelve individual edits where one rule would do is
    the failure this catches — it is correct output and the wrong answer.
    """
    def check(project, commands, chat) -> Result:
        got = _edits(commands)
        return len(got) <= n, f"{len(got)} edit commands"
    check.__name__ = f"command_count_at_most({n})"
    return check


def asks_a_question(expected: bool = True) -> Callable:
    def check(project, commands, chat) -> Result:
        asked = any(c.get("action") == "clarify" for c in commands)
        return asked == expected, "asked a question" if asked else "did not ask"
    check.__name__ = f"asks_a_question({expected})"
    return check


# ── whether what it did is real ──────────────────────────────────────────────

def ids_exist() -> Callable:
    """
    Every id referenced must be in the schedule.

    The single most damaging failure this agent can have: MDC1.FDG.1320 looks
    exactly like the real ids around it and does not exist, so the edit fails
    or — worse — a plausible wrong id succeeds against the wrong row.
    """
    def check(project, commands, chat) -> Result:
        real = {a.activity_id for a in project.activities}
        bad = []
        for cmd in _edits(commands):
            for key, val in _ids_in(cmd):
                if val not in real:
                    bad.append(f"{cmd.get('action')}.{key}={val}")
        return not bad, ("invented " + ", ".join(bad[:4])) if bad else "all ids real"
    check.__name__ = "ids_exist"
    return check


def wbs_names_exist() -> Callable:
    def check(project, commands, chat) -> Result:
        real = {(w.name or "").lower() for w in project.wbs_nodes}
        bad = [str(c.get("wbs_name")) for c in _edits(commands)
               if c.get("wbs_name") and str(c["wbs_name"]).lower() not in real]
        return not bad, ("no such folder: " + ", ".join(bad[:3])) if bad else "folders real"
    check.__name__ = "wbs_names_exist"
    return check


def direction_sane() -> Callable:
    """
    For every tie created, the predecessor must not finish after the successor
    starts. The dates are already in the file, so a backwards tie is not a
    matter of opinion.
    """
    def check(project, commands, chat) -> Result:
        by_id = {a.activity_id: a for a in project.activities}
        bad = []
        for cmd in _edits(commands):
            if cmd.get("action") != "add_relation":
                continue
            p = by_id.get(str(cmd.get("predecessor_id") or ""))
            s = by_id.get(str(cmd.get("successor_id") or ""))
            if not p or not s:
                continue
            pf = str(p.planned_finish or "")[:10]
            ss = str(s.planned_start or "")[:10]
            if pf and ss and pf > ss:
                bad.append(f"{p.activity_id}({pf}) -> {s.activity_id}({ss})")
        return not bad, ("backwards: " + ", ".join(bad[:3])) if bad else "directions sane"
    check.__name__ = "direction_sane"
    return check


def relation(pred_id: str, succ_id: str) -> Callable:
    """This exact tie is created."""
    def check(project, commands, chat) -> Result:
        pairs = [(str(c.get("predecessor_id")), str(c.get("successor_id")))
                 for c in _edits(commands) if c.get("action") == "add_relation"]
        return ((pred_id, succ_id) in pairs,
                f"tied {pairs or 'nothing'}")
    check.__name__ = f"relation({pred_id}->{succ_id})"
    return check


def no_relation(pred_id: str, succ_id: str) -> Callable:
    def check(project, commands, chat) -> Result:
        pairs = [(str(c.get("predecessor_id")), str(c.get("successor_id")))
                 for c in _edits(commands) if c.get("action") == "add_relation"]
        return ((pred_id, succ_id) not in pairs, f"tied {pairs or 'nothing'}")
    check.__name__ = f"no_relation({pred_id}->{succ_id})"
    return check


def relations_within_same_folder() -> Callable:
    """
    Every tie joins two activities in one folder.

    On a schedule of repeated rooms this is the difference between wiring each
    room and wiring room 105's pull to room 107's terminations because the
    dates happened to abut.
    """
    def check(project, commands, chat) -> Result:
        by_id = {a.activity_id: a for a in project.activities}
        bad = []
        for cmd in _edits(commands):
            if cmd.get("action") != "add_relation":
                continue
            p = by_id.get(str(cmd.get("predecessor_id") or ""))
            s = by_id.get(str(cmd.get("successor_id") or ""))
            if p and s and p.wbs_uid != s.wbs_uid:
                bad.append(f"{p.activity_id}->{s.activity_id}")
        return not bad, ("across folders: " + ", ".join(bad[:3])) if bad else "all within a folder"
    check.__name__ = "relations_within_same_folder"
    return check


def touches_only(*activity_ids: str) -> Callable:
    """
    The set of activities edited is exactly these — the check that catches a
    follow-up like "do the same for the list above" resolving to the wrong
    list, which is otherwise invisible because the commands all look fine.
    """
    want = set(activity_ids)
    def check(project, commands, chat) -> Result:
        got = set()
        for cmd in _edits(commands):
            for _, val in _ids_in(cmd):
                got.add(val)
        if got == want:
            return True, f"touched {sorted(got)}"
        return False, (f"touched {sorted(got)}, "
                       f"missing {sorted(want - got)}, extra {sorted(got - want)}")
    check.__name__ = f"touches_only({','.join(sorted(want))})"
    return check


def touches_all(*activity_ids: str) -> Callable:
    want = set(activity_ids)
    def check(project, commands, chat) -> Result:
        got = set()
        for cmd in _edits(commands):
            for _, val in _ids_in(cmd):
                got.add(val)
        missing = want - got
        return not missing, f"missing {sorted(missing)}" if missing else f"touched all {len(want)}"
    check.__name__ = f"touches_all({len(want)})"
    return check


def field_equals(action: str, field: str, value: Any) -> Callable:
    """Every command of this action carries this value — durations, types."""
    def check(project, commands, chat) -> Result:
        got = [c.get(field) for c in _edits(commands) if c.get("action") == action]
        if not got:
            return False, f"no {action} commands"
        wrong = [g for g in got if g != value]
        return not wrong, f"{field}={got}"
    check.__name__ = f"field_equals({action}.{field}={value})"
    return check


# ── what it said ─────────────────────────────────────────────────────────────
# Weaker than the structural checks and treated as such: a regex on prose is a
# proxy, not a proof. Used only where the CLAIM is about what gets said.

def says(pattern: str, flags=re.I) -> Callable:
    def check(project, commands, chat) -> Result:
        hit = bool(re.search(pattern, chat or "", flags))
        return hit, f"said: {(chat or '')[:120]!r}"
    check.__name__ = f"says({pattern!r})"
    return check


def not_says(pattern: str, flags=re.I) -> Callable:
    def check(project, commands, chat) -> Result:
        hit = bool(re.search(pattern, chat or "", flags))
        return not hit, f"said: {(chat or '')[:120]!r}"
    check.__name__ = f"not_says({pattern!r})"
    return check


def mentions_an_id() -> Callable:
    """The prompt asks for real ids in answers, not vague description."""
    def check(project, commands, chat) -> Result:
        real = {a.activity_id for a in project.activities}
        hit = [r for r in real if r in (chat or "")]
        return bool(hit), f"named {hit[:3] or 'no ids'}"
    check.__name__ = "mentions_an_id"
    return check


def answers_at_all() -> Callable:
    """Guards against an empty turn passing every other check vacuously."""
    def check(project, commands, chat) -> Result:
        ok = bool((chat or "").strip()) or bool(_edits(commands))
        return ok, "empty response" if not ok else "responded"
    check.__name__ = "answers_at_all"
    return check
