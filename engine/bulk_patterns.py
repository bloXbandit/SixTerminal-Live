# -*- coding: utf-8 -*-
"""
bulk_patterns.py — edits whose value comes from the folder they land in.

THE GAP THIS FILLS
  Every bulk action here already took a literal: append "(ER 209)" to this
  folder, create these named sub-folders under that parent. That is fine for
  one folder and wrong for thirty, because the agent then has to enumerate
  folders it can only see as samples and get thirty literals right in one
  message. It gets some right, invents a couple, and reports success.

  Both of these instead iterate the folders themselves and take the text FROM
  each folder, so the request is stated once and the schedule supplies the
  particulars:

    "every activity in a Gen room should carry that room's number"
      -> tag_by_folder, one command, every Gen folder, each with its own token

    "put the WBO work in each folder into a WBO sub-folder"
      -> group_into_subfolder, one command, a sub-folder per folder that
         actually has some

WHY THEY PREVIEW
  These reach hundreds of rows. Every function here runs with apply=False by
  default and returns what it WOULD do, folder by folder, so the plan can be
  read — and so an ambiguity in the request shows up as a line in the report
  rather than as a silent decision. A preview is also the honest way to ask
  "did you mean these?" without a round of guessing.

WHAT THEY WILL NOT DO
  They do not invent a token. A folder the token pattern cannot read is
  reported as skipped and named, because a folder called "Gen Yard" is not
  "Gen 0" and a made-up number is worse than a gap.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .schedule_model import Activity, Project, WBSNode

# A trailing "(...)" is how this app and this user's schedules already carry a
# location tag — "Install High Steel (Gen 315)". Recognising it is what lets a
# re-run CORRECT a wrong tag instead of appending a second one, which is the
# state the subject schedule was actually found in.
_TRAILING_TAG = re.compile(r"\s*\(([^()]*)\)\s*$")


def _uid() -> str:
    import uuid
    return str(uuid.uuid4().int)[:10]


def _children(project: Project) -> Dict[Optional[str], List[WBSNode]]:
    out: Dict[Optional[str], List[WBSNode]] = {}
    for w in project.wbs_nodes:
        out.setdefault(w.parent_uid, []).append(w)
    return out


def _descendants(project: Project, root: WBSNode) -> List[WBSNode]:
    kids = _children(project)
    out, stack, seen = [], [root], {root.uid}
    while stack:
        cur = stack.pop()
        out.append(cur)
        for c in kids.get(cur.uid, []):
            if c.uid not in seen:
                seen.add(c.uid)
                stack.append(c)
    return out


def _path(project: Project, node: WBSNode) -> str:
    by_uid = {w.uid: w for w in project.wbs_nodes}
    parts, cur, guard = [], node, 0
    while cur and guard < 200:
        parts.insert(0, cur.name)
        cur = by_uid.get(cur.parent_uid)
        guard += 1
    return " / ".join(parts)


def _acts_in(project: Project, node: WBSNode, recursive: bool) -> List[Activity]:
    uids = ({w.uid for w in _descendants(project, node)} if recursive
            else {node.uid})
    return [a for a in project.activities if a.wbs_uid in uids]


def _folders(project: Project, folder_pattern: Optional[str],
             under: Optional[WBSNode]) -> List[WBSNode]:
    """Folders to operate on: matched by pattern, optionally within a branch."""
    pool = (_descendants(project, under) if under is not None
            else list(project.wbs_nodes))
    if under is not None:
        pool = [w for w in pool if w.uid != under.uid]
    if not folder_pattern:
        return pool
    rx = re.compile(folder_pattern, re.I)
    return [w for w in pool if rx.search(w.name or "")]


# ── 1. tag every activity with something taken from its own folder ───────────

def tag_by_folder(project: Project,
                  folder_pattern: Optional[str] = None,
                  token_pattern: Optional[str] = None,
                  template: str = "{name} ({token})",
                  under: Optional[WBSNode] = None,
                  recursive: bool = False,
                  replace_existing: bool = True,
                  apply: bool = False) -> Dict[str, Any]:
    """
    Give every activity in a folder a name carrying that folder's own token.

    "In the Gen 316 folder, Install High Steel should be called
     Install High Steel (Gen 316)" — stated once, applied to every Gen folder,
    each taking its own number.

      folder_pattern   regex picking the folders, e.g. r"Gen\\s*\\d+"
      token_pattern    regex with the token to lift out of the FOLDER name.
                       "Gen 315 - JER" with r"Gen\\s*\\d+" gives "Gen 315", so
                       a room and its trade sub-folders agree. Default: the
                       whole folder name.
      template         how the new name is built. {name} is the activity's
                       name with any old tag already stripped, {token} the
                       folder's, {folder} the folder's full name.
      replace_existing a name already ending in "(...)" has that replaced
                       rather than a second tag appended — which is what makes
                       this fix a WRONG tag, not just fill in missing ones.

    Returns a plan; nothing is written unless apply is true.
    """
    rx_tok = re.compile(token_pattern, re.I) if token_pattern else None
    changes: List[Dict[str, str]] = []
    skipped_folders: List[Dict[str, str]] = []
    touched_folders = 0

    for w in _folders(project, folder_pattern, under):
        token = (w.name or "").strip()
        if rx_tok is not None:
            m = rx_tok.search(w.name or "")
            if not m:
                acts = _acts_in(project, w, recursive)
                if acts:
                    # Named, never guessed at. "Gen Yard" is not "Gen 0".
                    skipped_folders.append({
                        "folder": w.name, "path": _path(project, w),
                        "activities": len(acts),
                        "why": "the token pattern does not match this folder's name"})
                continue
            token = (m.group(1) if m.groups() else m.group(0)).strip()

        acts = _acts_in(project, w, recursive)
        if not acts:
            continue
        hit = False
        for a in acts:
            base = a.name or ""
            if replace_existing:
                base = _TRAILING_TAG.sub("", base).strip()
            new = template.format(name=base, token=token, folder=w.name)
            if new == (a.name or ""):
                continue                       # already right; idempotent
            changes.append({"activity_id": a.activity_id, "from": a.name,
                            "to": new, "folder": w.name,
                            "path": _path(project, w)})
            hit = True
            if apply:
                a.name = new
        if hit:
            touched_folders += 1

    return {
        "action": "tag_by_folder",
        "applied": bool(apply),
        "folders": touched_folders,
        "renamed": len(changes),
        # The list is capped so a 2,000-row preview does not become the whole
        # answer, but "renamed" above stays the true total — a truncated list
        # reporting its own length as the count understates the edit.
        "changes": changes if apply else changes[:400],
        "changes_truncated": (not apply) and len(changes) > 400,
        "skipped_folders": skipped_folders,
    }


# ── 2. gather matching work into a sub-folder, one per folder that has any ───

def group_into_subfolder(project: Project,
                         match: str,
                         subfolder_template: str = "WBO - {parent}",
                         code_template: str = "WBO-{code}",
                         under: Optional[WBSNode] = None,
                         folder_pattern: Optional[str] = None,
                         direct_only: bool = True,
                         marker: Optional[str] = None,
                         apply: bool = False) -> Dict[str, Any]:
    """
    For every folder holding work that matches, make one sub-folder and move
    that work into it.

    "Add a WBO sub-folder to each of my folders that has WBO activities, move
     them in, prefix the sub-folder name with WBO" — one command, a sub-folder
    only where there is something to put in it.

      match              regex against the ACTIVITY name, e.g. r"\\*+\\s*WBO"
      subfolder_template {parent} is the folder's name, {match} the literal
                         matched on the first activity found there
      direct_only        only activities sitting directly in the folder, not
                         in its sub-folders — otherwise a parent would hoover
                         up work that already lives somewhere sensible

    A folder that already has a sub-folder of that name reuses it rather than
    making a second, so re-running after new WBO work arrives tidies the new
    rows and leaves the rest alone.
    """
    rx = re.compile(match, re.I)
    existing_names = {(w.parent_uid, (w.name or "").strip().lower()): w
                      for w in project.wbs_nodes}
    by_uid = {w.uid: w for w in project.wbs_nodes}

    # The keyword the match is really about — "WBO" out of r"\*+\s*WBO" — so a
    # folder that ALREADY carries it can be recognised however it was named.
    # The subject schedule had both "Gen 315 - WBO" and "WBO MV 101" done by
    # hand before this existed; without this the tool layers a third convention
    # on top and produces "WBO - Gen 315 - WBO".
    _kw = marker or max(re.findall(r"\w+", match) or [""], key=len)
    _rx_kw = re.compile(re.escape(_kw), re.I) if _kw else None

    def _already_grouped(w: WBSNode) -> bool:
        return bool(_rx_kw and _rx_kw.search(w.name or ""))

    def _is_a_destination(w: WBSNode) -> bool:
        """
        Whether this folder is one a previous run already made.

        Without this the second run is not a no-op but a nesting: the WBO work
        now lives in "WBO - ER 212", that folder matches the same rule, and it
        gets a "WBO - WBO - ER 212" inside it. Comparing the folder's name to
        what the template would have produced for its own parent recognises
        the destination exactly, whatever template was used.
        """
        parent = by_uid.get(w.parent_uid)
        if parent is None:
            return False
        return (w.name or "").strip().lower() == subfolder_template.format(
            parent=parent.name, match=match).strip().lower()
    plan: List[Dict[str, Any]] = []
    already: List[Dict[str, Any]] = []
    made = moved = reused = 0

    for w in _folders(project, folder_pattern, under):
        if _is_a_destination(w):
            continue
        if _already_grouped(w):
            # Report rather than compound. An existing convention the user set
            # by hand is a pattern to follow, not a folder to wrap again.
            acts = [a for a in _acts_in(project, w, not direct_only)
                    if rx.search(a.name or "")]
            if acts:
                already.append({"folder": w.name, "path": _path(project, w),
                                "activities": len(acts)})
            continue
        acts = [a for a in _acts_in(project, w, not direct_only)
                if rx.search(a.name or "")]
        if not acts:
            continue
        # A folder that IS the destination must not feed itself.
        sub_name = subfolder_template.format(parent=w.name, match=match)
        if (w.name or "").strip().lower() == sub_name.strip().lower():
            continue

        key = (w.uid, sub_name.strip().lower())
        target = existing_names.get(key)
        entry = {"folder": w.name, "path": _path(project, w),
                 "subfolder": sub_name, "activities": len(acts),
                 "activity_ids": [a.activity_id for a in acts][:50],
                 "reused": target is not None}
        plan.append(entry)
        if target is None:
            made += 1
        else:
            reused += 1
        moved += len(acts)

        if not apply:
            continue
        if target is None:
            target = WBSNode(
                uid=_uid(), name=sub_name,
                code=code_template.format(code=(w.code or w.uid))[:40],
                parent_uid=w.uid,
                sequence_num=max((c.sequence_num or 0)
                                 for c in project.wbs_nodes) + 1)
            project.wbs_nodes.append(target)
            existing_names[key] = target
        for a in acts:
            a.wbs_uid = target.uid

    if apply:
        project.build_lookups()

    return {
        "action": "group_into_subfolder",
        "applied": bool(apply),
        "subfolders_created": made,
        "subfolders_reused": reused,
        "activities_moved": moved,
        "plan": plan,
        "already_grouped": already,
    }


# ── how either plan reads back to a person ───────────────────────────────────

def describe(result: Dict[str, Any], limit: int = 12) -> str:
    """
    The plan as a sentence and a short list.

    A preview nobody can read is the same as no preview, and the list is what
    turns "did you mean these?" into a question the user can answer at a
    glance instead of a round of guessing.
    """
    if result.get("action") == "tag_by_folder":
        n, f = result["renamed"], result["folders"]
        if not n:
            head = "Nothing to rename — every activity already carries its folder's tag."
        else:
            head = (f"{'Renamed' if result['applied'] else 'Would rename'} {n} "
                    f"activit{'y' if n == 1 else 'ies'} across {f} folder"
                    f"{'' if f == 1 else 's'}.")
        lines = [f"  {c['folder']}: {c['from']}  ->  {c['to']}"
                 for c in result["changes"][:limit]]
        if n > limit:
            lines.append(f"  …and {n - limit} more")
        if result["skipped_folders"]:
            lines.append("")
            lines.append("Skipped — no token could be read from these folder names:")
            lines += [f"  {s['path']} ({s['activities']} activities)"
                      for s in result["skipped_folders"][:limit]]
        return "\n".join([head] + lines)

    if result.get("action") == "group_into_subfolder":
        made, reused = result["subfolders_created"], result["subfolders_reused"]
        mv = result["activities_moved"]
        if not mv:
            return "Nothing matched — no folder holds work of that kind."
        head = (f"{'Created' if result['applied'] else 'Would create'} {made} "
                f"sub-folder{'' if made == 1 else 's'}"
                + (f" (and reuse {reused} that already exist)" if reused else "")
                + f" and {'moved' if result['applied'] else 'move'} "
                  f"{mv} activit{'y' if mv == 1 else 'ies'} in.")
        lines = [f"  {e['path']}  ->  {e['subfolder']}  ({e['activities']} acts)"
                 for e in result["plan"][:limit]]
        if len(result["plan"]) > limit:
            lines.append(f"  …and {len(result['plan']) - limit} more folders")
        ag = result.get("already_grouped") or []
        if ag:
            lines.append("")
            lines.append(f"Left alone — {len(ag)} folder{'' if len(ag) == 1 else 's'} "
                         f"already group this work under a name of their own. "
                         f"Say which naming you want and they can be brought in line:")
            lines += [f"  {e['path']} ({e['activities']} acts)" for e in ag[:limit]]
            if len(ag) > limit:
                lines.append(f"  …and {len(ag) - limit} more")
        return "\n".join([head] + lines)

    if result.get("action") == "align_child_tokens":
        n, rt = result["renamed"], result["retagged_activities"]
        if not n:
            head = "Nothing to align — every sub-folder already carries its parent's number."
        else:
            head = (f"{'Renamed' if result['applied'] else 'Would rename'} {n} "
                    f"sub-folder{'' if n == 1 else 's'} to match their parent"
                    + (f", and {'retagged' if result['applied'] else 'retag'} "
                       f"{rt} activit{'y' if rt == 1 else 'ies'} inside them"
                       if rt else "") + ".")
        lines = [f"  under {c['parent']}:  {c['from']}  ->  {c['to']}"
                 for c in result["changes"][:limit]]
        if n > limit:
            lines.append(f"  …and {n - limit} more")
        if result["wrong_kind"]:
            lines.append("")
            lines.append("Left alone — these carry a different kind of tag from "
                         "their parent, which reads as a folder in the wrong "
                         "place rather than one with the wrong number:")
            lines += [f"  {e['path']}" for e in result["wrong_kind"][:limit]]
        return "\n".join([head] + lines)

    return ""


# ── 3. make a sub-folder's number agree with its parent's ────────────────────

# "Gen 326", "ER 208", "MV 101", "Gen 318- JER" — a word or two and a number,
# however it was spaced. Generic enough to find a room tag without being told
# which kind, and the kind is then checked so it never rewrites one as another.
DEFAULT_TOKEN = r"\b[A-Za-z]{2,5}\s*-?\s*\d{2,4}\b"


def _kind_of(token: str) -> str:
    """The non-numeric half of a tag, normalised: "Gen 318- " -> "gen"."""
    return re.sub(r"[^a-z]", "", (token or "").lower())


def align_child_tokens(project: Project,
                       token_pattern: str = DEFAULT_TOKEN,
                       under: Optional[WBSNode] = None,
                       folder_pattern: Optional[str] = None,
                       retag_activities: bool = True,
                       activity_template: str = "{name} ({token})",
                       apply: bool = False) -> Dict[str, Any]:
    """
    Make every sub-folder carry its PARENT's number.

    "Gen 326 has a Gen 315 - JER and a Gen 315 - WBO under it; flip the 315s to
    326 so they all match." The subject schedule has 30 of these — a Gen 301
    room holding a "Gen 326- JER", a Gen 324 holding a "Gen 315 - JER" — which
    is why an activity in one room ends up labelled with another's number.

    Only the matched token is replaced, so everything else in the name
    survives: "Gen 318- JER" under "Gen 306" becomes "Gen 306- JER", odd
    spacing and trade suffix intact.

    Two things it will not do:

      It will not rewrite one KIND of tag as another. An "MV 101" sitting
      under an "ER 208" is a folder in the wrong place, not a folder with the
      wrong number, and renaming it would bury that rather than show it — so
      it is reported instead.

      It will not touch a sub-folder carrying no tag at all. "Rough-Ins" under
      "Gen 326" is a stage, not a mis-numbered room.

    Folders are walked parents-first, so a corrected folder passes its number
    on to its own children in the same run.

    retag_activities also brings the activity names inside the renamed folders
    in line, which is the other half of "so all match" — renaming the folder
    alone leaves every activity still reading the old number.
    """
    rx = re.compile(token_pattern, re.I)
    by_uid = {w.uid: w for w in project.wbs_nodes}
    kids = _children(project)
    scope = {w.uid for w in _folders(project, folder_pattern, under)}

    # Parents first: a folder fixed on this pass must hand its new number down
    # to its own children in the same run, not the one it arrived with.
    order: List[WBSNode] = []
    stack = [w for w in project.wbs_nodes if w.parent_uid not in by_uid]
    seen = {w.uid for w in stack}
    while stack:
        cur = stack.pop(0)
        order.append(cur)
        for c in kids.get(cur.uid, []):
            if c.uid not in seen:
                seen.add(c.uid)
                stack.append(c)

    renamed: List[Dict[str, str]] = []
    wrong_kind: List[Dict[str, str]] = []
    for w in order:
        par = by_uid.get(w.parent_uid)
        if par is None or w.uid not in scope:
            continue
        mp, mc = rx.search(par.name or ""), rx.search(w.name or "")
        if not mp or not mc:
            continue
        p_tok, c_tok = mp.group(0), mc.group(0)
        if _kind_of(p_tok) != _kind_of(c_tok):
            wrong_kind.append({"folder": w.name, "parent": par.name,
                               "path": _path(project, w),
                               "why": "a different kind of tag, so this looks "
                                      "like a folder in the wrong place rather "
                                      "than one with the wrong number"})
            continue
        if p_tok.strip() == c_tok.strip():
            continue
        new = w.name[:mc.start()] + p_tok.strip() + w.name[mc.end():]
        if new == w.name:
            continue
        renamed.append({"from": w.name, "to": new, "parent": par.name,
                        "path": _path(project, w), "uid": w.uid})
        if apply:
            w.name = new

    # Renaming the folder alone leaves every activity inside still reading the
    # old number, which is half a job and looks like the tool failed.
    retagged = 0
    if retag_activities and renamed:
        for entry in renamed:
            node = by_uid.get(entry["uid"])
            if node is None:
                continue
            m = rx.search(entry["to"])
            if not m:
                continue
            token = m.group(0).strip()
            for a in _acts_in(project, node, True):
                base = _TRAILING_TAG.sub("", a.name or "").strip()
                new = activity_template.format(name=base, token=token,
                                               folder=entry["to"])
                if new != (a.name or ""):
                    retagged += 1
                    if apply:
                        a.name = new

    return {
        "action": "align_child_tokens",
        "applied": bool(apply),
        "renamed": len(renamed),
        "retagged_activities": retagged,
        "changes": renamed,
        "wrong_kind": wrong_kind,
    }
