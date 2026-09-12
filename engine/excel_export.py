# -*- coding: utf-8 -*-
"""
excel_export.py — the field progress tracker, generated from the live schedule.

WHY A WORKBOOK AT ALL
  P6 is not where a foreman reports progress. The field wants a sheet it can
  filter, colour and hand back, and the office wants those actuals to reach
  P6 without anybody retyping two thousand rows. This writes the first half;
  pasting the Update tab back into the grid closes the loop, and the app's own
  XML export takes it from there.

WHY IT IS GENERATED, NOT A TEMPLATE
  A tracker cut by hand is stale the moment the schedule moves. This is built
  from whatever is loaded right now, so "export the current week" is a button
  rather than an afternoon.

WHAT THE FORMULAS MAY USE
  Only functions Excel has had since 2007 — SUMIFS, COUNTIFS, INDEX/MATCH,
  IFERROR, nested IF — plus MINIFS and MAXIFS, which must carry the `_xlfn.`
  prefix because that is how Excel stores them; unprefixed they open as #NAME?.

  Nothing that spills (FILTER, SORT, XLOOKUP, UNIQUE): openpyxl writes no
  spill metadata, so a spilling formula silently fills one cell and leaves the
  rest blank — the sheet looks built and reports nothing.

  WORKDAY.INTL is avoided even prefixed. The six-day week is plain arithmetic
  instead: the next working day is +1, and +1 again only if that lands on a
  Sunday; adding n days skips INT((WEEKDAY(start,2)-1+n)/6) Sundays, which is
  exact rather than approximate.
"""

import datetime as dt
import re
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import (Alignment, Border, Font, PatternFill, Protection,
                             Side)
from openpyxl.utils import get_column_letter as CL
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

# ── design tokens ────────────────────────────────────────────────────────────
NAVY, ACCENT, INK, MUTE, RULE = '1F3864', '2E75B6', '262626', '7F7F7F', 'D9D9D9'
OK_BG, OK_TX = 'C6EFCE', '006100'
WIP_BG, WIP_TX = 'BDD7EE', '1F4E79'
NEW_BG, NEW_TX = 'F2F2F2', '595959'
BAD_BG, BAD_TX = 'FFC7CE', '9C0006'
WARN_BG, WARN_TX = 'FFEB9C', '9C6500'
WBO_BG, WBO_TX = 'E4DFEC', '5F497A'
TBD_BG, TBD_TX = 'FFF2CC', '806000'
FLAG_BG, FLAG_TX = 'FCE4D6', '974706'
ENTRY = 'FFF9E3'
# One tint per phase, pale enough to read black text over and to print. A
# colour belongs to its row, so it survives every filter and sort the field
# applies — which a header row or a divider line does not.
PHASE_BANDS = ['DDEBF7', 'E2EFDA', 'FFF2CC', 'FCE4D6', 'E4DFEC', 'DAEEF3']

# The Update sheet's columns, by name. Used as identifiers rather than quoted
# strings so a name can sit inside an f-string formula without fighting it.
COL_PROJECT, COL_LEAD, COL_PHASE, COL_AREA = 'Project', 'Lead', 'Phase', 'Area'
COL_SUBAREA, COL_ROOM, COL_TASK = 'Sub Area', 'Room', 'Task'
COL_ID, COL_NAME, COL_BY = 'Activity ID', 'Activity Name', 'By'
COL_BLSTART, COL_BLFINISH, COL_DAYS, COL_CREW = 'BL Start', 'BL Finish', 'Days', 'Crew'
COL_STATUS, COL_PCT = 'Status', '% Comp'
COL_ASTART, COL_AFINISH, COL_NOTES, COL_UPDBY = 'Act Start', 'Act Finish', 'Notes', 'Updated By'
COL_VAR, COL_WINDOW, COL_NEXT, COL_FLAG = 'Var (d)', 'Window', 'Next Step', 'Flag \u2691'


FONT = 'Arial'

# Who is doing the work. "TBD" is the honest default for anything nobody has
# claimed yet, and it is a filter the PM actually wants: unassigned work is a
# question, not a blank.
CREW_TBD, CREW_WBO, CREW_OWN = 'TBD', 'Work by others', 'Richards'
def _crew_options(own: str) -> str:
    return f'"{CREW_TBD},{CREW_WBO},{own}"'

# Why work actually stops, in the words the field uses. Short enough to pick
# from a dropdown on a phone, specific enough that the count is worth reading
# — "Blocked" and "Material" are different problems with different owners.
FLAGS = ['Blocked', 'Needs info', 'Material', 'Manpower', 'Access',
         'Rework', 'Watch']

# The roster, not a rule. Passed in per export so a new job needs no code
# change — this is only the default for the jobs already running.
LEADS = {'MDC1': 'Kris', 'MDC2': 'Jerome', 'MDC3': 'Kyle'}


def _code_for(project) -> str:
    """
    A short project code from whatever the file calls itself.

    P6 ids look like "25-1539-INT-3"; the crews call the job MDC1, and the
    activity codes carry that (MDC1.PH2.ER.1000). So the activity prefix is
    the better name when there is one, and the P6 id is the fallback.
    """
    heads = {}
    for a in (getattr(project, 'activities', None) or [])[:400]:
        seg = re.split(r'[.\-_/ ]+', (a.activity_id or '').strip(), 1)
        if seg and seg[0] and not seg[0].isdigit() and len(seg[0]) <= 8:
            heads[seg[0].upper()] = heads.get(seg[0].upper(), 0) + 1
    if heads:
        top, n = max(heads.items(), key=lambda kv: kv[1])
        if n >= 5:
            return top
    return (getattr(project, 'id', None) or getattr(project, 'name', None) or 'PROJECT')
_WBO_RE = re.compile(r'\*+\s*WBO', re.I)

thin = Side(style='thin', color=RULE)
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
UNDER = Border(bottom=Side(style='medium', color=NAVY))
LEFT = Alignment(horizontal='left', vertical='center')
CTR = Alignment(horizontal='center', vertical='center')
WRAP = Alignment(horizontal='left', vertical='top', wrap_text=True)


def _f(sz=10, b=False, c=INK, i=False):
    return Font(name=FONT, size=sz, bold=b, color=c, italic=i)


def _fill(c):
    return PatternFill('solid', fgColor=c)


def _d(v):
    return str(v)[:10] if v else None


def _date(v):
    s = _d(v)
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _title(ws, title, sub, width=12):
    ws.sheet_view.showGridLines = False
    ws['A1'] = title
    ws['A1'].font = _f(16, True, NAVY)
    ws['A2'] = sub
    ws['A2'].font = _f(9, False, MUTE)
    ws['A2'].alignment = WRAP
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 26
    for c in range(1, width + 1):
        ws.cell(row=3, column=c).border = UNDER


def _head(ws, r, headers, widths=None):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = _f(9, True, 'FFFFFF')
        c.fill = _fill(NAVY)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = BOX
    ws.row_dimensions[r].height = 30
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[CL(i)].width = w


def _cf(ws, rng, formula, bg, tx, bold=False, italic=False, sz=9):
    ws.conditional_formatting.add(rng, FormulaRule(
        formula=[formula], stopIfTrue=False, fill=_fill(bg),
        font=Font(name=FONT, size=sz, color=tx, bold=bold, italic=italic)))



def _wbs_order(project) -> Dict[str, int]:
    """
    Where each folder sits when the WBS is unfolded top to bottom — the order
    the app's own tree shows, and the order P6 shows.

    Everything used to be sorted by NAME, which is why the Areas sheet put
    Closeout and Commissioning in the middle of Phase 1 and Procurement at the
    bottom of the job: alphabetically that is exactly where they belong, and it
    bears no relation to how the work runs. P6 orders siblings by
    sequence_num, so a depth-first walk in that order reproduces the tree.
    """
    by_uid = {w.uid: w for w in project.wbs_nodes}
    kids: Dict[Any, List] = {}
    for w in project.wbs_nodes:
        kids.setdefault(w.parent_uid, []).append(w)
    for v in kids.values():
        v.sort(key=lambda x: ((x.sequence_num or 0), x.name or ''))

    out: Dict[str, int] = {}
    n = [0]

    def walk(uid):
        for w in kids.get(uid, []):
            if w.uid in out:
                continue                      # a cyclic parent chain
            out[w.uid] = n[0]
            n[0] += 1
            walk(w.uid)

    for root in [w for w in project.wbs_nodes if w.parent_uid not in by_uid]:
        if root.uid not in out:
            out[root.uid] = n[0]
            n[0] += 1
            walk(root.uid)
    walk(None)
    return out


def _collect(project, project_code: str) -> Dict[str, Any]:
    """Flatten the schedule into the rows the workbook is built from."""
    from engine.logic_advisor import (location_tag, strip_location,
                                      wbs_path, wbs_segments)
    from engine.schedule_model import compute_dates

    # Float is what seeds the critical tab. apply_dates=False so Start/Finish
    # are untouched — only the derived columns are refreshed.
    try:
        compute_dates(project, hold_unlinked_dates=True, apply_dates=False)
    except Exception:
        pass

    # Whichever field this job uses for headcount — the name differs per job,
    # and hardcoding one means every other schedule exports a blank column.
    crew_counts: Dict[str, int] = {}
    for a in project.activities:
        for k in (getattr(a, 'udfs', None) or {}):
            crew_counts[k] = crew_counts.get(k, 0) + 1
    crew_field = ''
    if crew_counts:
        pref = [k for k in crew_counts if 'elect' in k.lower() or 'crew' in k.lower()
                or 'man' in k.lower()]
        crew_field = (pref[0] if pref
                      else max(crew_counts.items(), key=lambda kv: kv[1])[0])

    order_of = _wbs_order(project)
    by_uid = {a.uid: a for a in project.activities}
    preds: Dict[str, List[str]] = {}
    fin: Dict[str, str] = {}
    for a in project.activities:
        fin[a.uid] = _d(a.early_finish or a.planned_finish) or ''
    for r in project.relations:
        if r.predecessor_uid in by_uid and r.successor_uid in by_uid:
            preds.setdefault(r.successor_uid, []).append(r.predecessor_uid)

    rows = []
    for a in project.activities:
        # Ask the WBS for its levels rather than splitting the joined path
        # back apart — a folder may have " / " in its own name, and three
        # in this job do.
        seg = wbs_segments(project, a)
        path = ' / '.join(seg)
        phase = seg[1] if len(seg) > 1 else '(unfiled)'
        area = seg[2] if len(seg) > 2 else phase
        # Everything below the work area, kept rather than thrown away. "CUP"
        # alone put 280 activities in one bucket with Lineup 1 and Room Builds
        # indistinguishable; the division the user needs to filter on is
        # exactly the part that was being discarded.
        sub = ' / '.join(seg[3:]) if len(seg) > 3 else ''
        name = a.name or ''
        wbo = bool(_WBO_RE.search(name))
        rows.append({
            'project': project_code,
            'phase': phase,
            'area': area,
            'sub_area': sub,
            'order': order_of.get(a.wbs_uid, 10 ** 6),
            'room': location_tag(name) or '',
            'work_type': _WBO_RE.sub('', strip_location(name)).strip(' *-'),
            'activity_id': a.activity_id,
            'name': name,
            'by': CREW_WBO if wbo else CREW_TBD,
            'type': a.activity_type or '',
            'is_milestone': 'Milestone' in (a.activity_type or ''),
            'status': a.status,
            'pct': float(a.percent_complete or 0),
            'bl_start': _d(a.planned_start),
            'bl_finish': _d(a.planned_finish),
            'act_start': _d(a.actual_start),
            'act_finish': _d(a.actual_finish),
            'days': round((a.planned_duration or 0) / 8.0, 1),
            'float_d': (round(a.total_float / 8.0, 1) if a.total_float is not None else None),
            'crew': (getattr(a, 'udfs', None) or {}).get(crew_field, ''),
            'preds': ', '.join(by_uid[u].activity_id for u in preds.get(a.uid, [])[:6]),
            'succs': '',
            'constraint': a.constraint_type or '',
            'path': path,
        })
    # WBS order, not alphabetical. Within a folder the dates still decide, so
    # the rows read the way the work runs at both levels.
    rows.sort(key=lambda r: (r['order'], r['bl_start'] or '9999', r['activity_id']))

    # The run that decides each phase date: walk back from a Substantial
    # Completion milestone, each step taking whichever predecessor finishes
    # latest. Milestones are skipped — they carry no duration to model.
    # Keyed on the phase the milestone NAMES, not the folder it sits in — every
    # milestone lives together under "Milestones", so keying on the folder
    # collapsed all three phases onto one entry and only one block was drawn.
    chains: Dict[str, List[str]] = {}
    for m in project.activities:
        nm = (m.name or '').lower()
        if 'substantial completion' not in nm or 'Milestone' not in (m.activity_type or ''):
            continue
        ph = re.search(r'\(?\bPH\s*(\d)\b\)?', m.name or '', re.I)
        key = f'Phase {ph.group(1)}' if ph else (m.name or m.activity_id)
        walk, seen, cur = [], {m.uid}, m
        for _ in range(60):
            cand = [by_uid[u] for u in preds.get(cur.uid, ()) if u not in seen]
            if not cand:
                break
            cur = max(cand, key=lambda x: fin.get(x.uid, ''))
            seen.add(cur.uid)
            if 'Milestone' not in (cur.activity_type or ''):
                walk.append(cur.activity_id)
        chains.setdefault(key, list(reversed(walk))[-16:])

    mils = sorted([r for r in rows if r['is_milestone'] and r['bl_finish']],
                  key=lambda r: r['bl_finish'])
    return {'rows': rows, 'chains': chains, 'milestones': mils,
            'data_date': _d(project.data_date) or dt.date.today().isoformat()}


def build_workbook(project, out_path: str, project_code: Optional[str] = None,
                   title: Optional[str] = None,
                   leads: Optional[Dict[str, str]] = None,
                   own_crew: str = CREW_OWN,
                   spec=None) -> str:
    """
    Write the tracker for this project. Returns the path written.

    Nothing about one job is baked in: the code, the phases, the areas, the
    contract dates and the driving chains all come out of the schedule that
    was handed in. `leads` and `own_crew` are the only preferences, and both
    have defaults rather than requirements.
    """
    # Whatever was asked for about the tracker that the schedule cannot say —
    # a hidden column, a renamed heading, a phase colour, an extra sheet. Held
    # apart from the generator so it survives every rebuild rather than being
    # a hand edit that the next export silently throws away.
    from .sheet_spec import SheetSpec
    spec = spec if isinstance(spec, SheetSpec) else SheetSpec()
    project_code = project_code or _code_for(project)
    # The roster is for a job that HAS siblings. MDC1/2/3 share a workbook, so
    # listing all three there is the point — but carrying those three names
    # into an unrelated job's file is somebody else's crew on the front page.
    # The default roster therefore applies only when this job is in it.
    if leads is None:
        leads = dict(LEADS) if project_code in LEADS else {}
    else:
        leads = dict(leads)
    leads.setdefault(project_code, '')
    D = _collect(project, project_code)
    ROWS, MILS = D['rows'], D['milestones']
    wb = Workbook()

    # ══════════════════════════════════════════════════════════ START HERE ══
    ws = wb.active
    ws.title = 'Start Here'
    _title(ws, f"{title or project_code} — Progress Tracker",
           'Daily and weekly field updates. Everything on the other tabs is driven '
           'from the two yellow cells below. Generated from the live schedule — '
           're-export any time to pick up changes.')
    ws['A5'] = 'CONTROLS'
    ws['A5'].font = _f(11, True, NAVY)
    for i, (lab, val, tip) in enumerate([
            ('Status Date', _date(D['data_date']) or dt.date.today(),
             'The date progress is reported as of. Drives every window, variance and lookahead.'),
            ('Lookahead Weeks', 6,
             'How far the "Lookahead" window reaches. 2, 3, 4 or 6 are the usual choices.')]):
        r = 6 + i
        ws.cell(row=r, column=1, value=lab).font = _f(10, True)
        c = ws.cell(row=r, column=2, value=val)
        c.fill = _fill(ENTRY); c.border = BOX; c.font = _f(11, True, WIP_TX); c.alignment = CTR
        if isinstance(val, dt.date):
            c.number_format = 'mm/dd/yyyy'
        ws.cell(row=r, column=3, value=tip).font = _f(9, False, MUTE)
    for col, w in (('A', 20), ('B', 14), ('C', 96)):
        ws.column_dimensions[col].width = w

    ws['A10'] = 'PROJECTS'
    ws['A10'].font = _f(11, True, NAVY)
    ws['C10'] = ('Paste another job\'s activities into Update with its code in the Project '
                 'column and every tab counts them automatically.')
    ws['C10'].font = _f(9, False, MUTE)
    _head(ws, 11, ['Project', 'Lead Foreman', 'In this file'])
    for i, (code, lead) in enumerate(leads.items()):
        r = 12 + i
        here = 'Yes' if code == project_code else 'Not yet'
        for j, v in enumerate((code, lead, here), start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.border = BOX
            c.alignment = LEFT if j < 3 else CTR
            c.font = _f(10, j == 1, MUTE if here == 'Not yet' else INK)

    hr = 13 + len(leads)          # the roster is as long as the roster is
    ws[f'A{hr}'] = 'HOW TO USE'
    ws[f'A{hr}'].font = _f(11, True, NAVY)
    guide = [
        ('Update', 'The tab the field fills in. Yellow cells only: By, Status, % Complete, '
                   'Actual Start, Actual Finish, Notes, Updated By and Flag.'),
        ('', 'Flag \u2691 is yours to set — pick a reason from the dropdown (or type your '
             'own) on anything that needs attention. Filter that column to see only '
             'flagged work; the Dashboard counts them per phase. "Next Step" beside it '
             'is worked out for you and cannot be typed in.'),
        ('', 'Filter with the header arrows — Phase, Area, Work Type, By or Window. '
             'Clear them to see the whole schedule again.'),
        ('Dashboard', 'Read-only rollup: phases, milestones, and where the job stands '
                      'against the contract dates.'),
        ('Areas', 'Folder level. One line per work area with its own percent and date range.'),
        ('Critical Path', 'The run to each Substantial Completion. Pick an Activity ID from '
                          'the dropdown and the row fills itself in.'),
        ('Notes', 'Longer notes per area.'),
        ('Data', 'Reference only — predecessors, float, constraints. Locked.'),
    ]
    for i, (k, v) in enumerate(guide):
        ws.cell(row=hr + 1 + i, column=1, value=k).font = _f(10, True, ACCENT)
        ws.cell(row=hr + 1 + i, column=3, value=v).font = _f(9)

    lr = hr + 1 + len(guide) + 1
    ws.cell(row=lr, column=1, value='LEGEND').font = _f(11, True, NAVY)
    for i, (lab, bg, tx) in enumerate([
            ('Complete', OK_BG, OK_TX), ('In Progress', WIP_BG, WIP_TX),
            ('Not Started', NEW_BG, NEW_TX), ('OVERDUE', BAD_BG, BAD_TX),
            ('Due this week', WARN_BG, WARN_TX), (CREW_WBO, WBO_BG, WBO_TX),
            ('Nobody assigned (TBD)', TBD_BG, TBD_TX),
            ('Flagged \u2691', FLAG_BG, FLAG_TX), ('Type in yellow cells', ENTRY, INK)]):
        c = ws.cell(row=lr + 1 + i, column=1, value=lab)
        c.fill = _fill(bg); c.font = _f(10, True, tx); c.border = BOX; c.alignment = CTR
    ws.freeze_panes = 'A4'

    # ═════════════════════════════════════════════════════════════ UPDATE ══
    up = wb.create_sheet('Update')
    _title(up, 'Update — field entry',
           'Fill the yellow columns only. Filter with the header arrows; clear the '
           'filters to see the whole schedule. Raise a Flag \u2691 on the far right for '
           'anything that needs attention — it colours the row and counts on the '
           'Dashboard. "Next Step" is worked out for you.', 23)
    # "Sub Area" is everything below the work area — "Mech Line-Ups / Lineup 1"
    # under CUP, "Gen 315 / Gen 315 - JER" under Generator Rooms. Without it
    # 280 CUP activities filtered as one undifferentiated block.
    #
    # "Task" is the activity name with its room tag and WBO marker stripped, so
    # every "Device Trim Out" on the job filters together however it is
    # labelled. It was headed "Work Type", which read as a trade category and
    # is not one — the category IS the Area. Renamed to what it is.
    HDR = [COL_PROJECT, COL_LEAD, COL_PHASE, COL_AREA, COL_SUBAREA, COL_ROOM,
           COL_TASK, COL_ID, COL_NAME, COL_BY, COL_BLSTART, COL_BLFINISH,
           COL_DAYS, COL_CREW, COL_STATUS, COL_PCT, COL_ASTART, COL_AFINISH,
           COL_NOTES, COL_UPDBY, COL_VAR, COL_WINDOW, COL_NEXT, COL_FLAG]
    # The heading is what the user reads; the MAP below still keys on the
    # canonical name, so renaming a column cannot detach a formula from it.
    _head(up, 4, [spec.shown(h) for h in HDR],
          [9, 9, 15, 20, 26, 11, 26, 22, 46, 15, 11, 11, 7, 7,
           14, 8, 11, 11, 34, 12, 9, 13, 14, 15])
    # Column letters by NAME. Every formula below reads through this, so a
    # column can be added or moved without hunting $N and $U through a hundred
    # lines and getting one of them wrong — which is exactly what adding
    # "Sub Area" would otherwise have cost.
    C = {h: CL(i) for i, h in enumerate(HDR, start=1)}
    LASTCOL = CL(len(HDR))
    HR = 4
    N = {h: i for i, h in enumerate(HDR, start=1)}      # name -> column NUMBER

    # One colour per phase, so the divisions are visible at a glance without a
    # header row. Header rows were the obvious answer and the wrong one: the
    # Update sheet is an Excel Table with a filter on it, and a row that holds
    # no activity either breaks the filter or vanishes under it. A colour
    # belongs to the row, so it survives every filter and sort the field
    # applies. Assigned in WBS order, so Phase 1 is always the first colour.
    phase_order, seen_ph = [], set()
    for rec in ROWS:
        if rec['phase'] not in seen_ph:
            seen_ph.add(rec['phase'])
            phase_order.append(rec['phase'])
    phase_bg = {ph: PHASE_BANDS[i % len(PHASE_BANDS)]
                for i, ph in enumerate(phase_order)}
    for ph, hexv in spec.phase_colours.items():
        if ph in phase_bg and hexv:
            phase_bg[ph] = hexv

    for i, rec in enumerate(ROWS):
        r = HR + 1 + i
        base = [('Project', rec['project']),
                ('Lead', leads.get(rec['project'], '')),
                ('Phase', rec['phase']),
                ('Area', rec['area']),
                ('Sub Area', rec['sub_area']),
                ('Room', rec['room']),
                ('Task', rec['work_type']),
                ('Activity ID', rec['activity_id']),
                ('Activity Name', rec['name'])]
        for key, v in base:
            c = up.cell(row=r, column=N[key], value=v)
            c.font = _f(9); c.border = BOX
            c.alignment = CTR if key in ('Project', 'Lead', 'Room') else LEFT
        # The phase band, on the columns that say where the row belongs.
        band = phase_bg.get(rec['phase'])
        if band:
            for key in ('Project', 'Lead', 'Phase', 'Area', 'Sub Area'):
                up.cell(row=r, column=N[key]).fill = _fill(band)

        # "By" is an ENTRY cell seeded from the **WBO tag in the name.
        c = up.cell(row=r, column=N['By'], value=rec['by'])
        c.fill = _fill(ENTRY); c.border = BOX; c.font = _f(9); c.alignment = CTR
        c.protection = Protection(locked=False)
        for key, v in (('BL Start', _date(rec['bl_start'])),
                       ('BL Finish', _date(rec['bl_finish']))):
            c = up.cell(row=r, column=N[key], value=v)
            c.number_format = 'mm/dd/yy'; c.font = _f(9); c.border = BOX; c.alignment = CTR
        for key, v in (('Days', rec['days']), ('Crew', rec['crew'])):
            if key == 'Crew' and v not in ('', None):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    pass
            c = up.cell(row=r, column=N[key], value=v)
            c.font = _f(9); c.border = BOX; c.alignment = CTR
        st = {'Completed': 'Complete', 'In Progress': 'In Progress'}.get(rec['status'], 'Not Started')
        for key, v in (('Status', st),
                       ('% Comp', rec['pct'] or (1.0 if st == 'Complete' else 0.0)),
                       ('Act Start', _date(rec['act_start'])),
                       ('Act Finish', _date(rec['act_finish'])),
                       ('Notes', None), ('Updated By', None)):
            c = up.cell(row=r, column=N[key], value=v)
            c.fill = _fill(ENTRY); c.border = BOX; c.font = _f(9)
            c.protection = Protection(locked=False)
            if key == '% Comp':
                c.number_format = '0%'; c.alignment = CTR
            elif key in ('Act Start', 'Act Finish'):
                c.number_format = 'mm/dd/yy'; c.alignment = CTR
            else:
                c.alignment = CTR if key == 'Status' else LEFT

        st_, pc, af, bf, bs, win, by = (C['Status'], C['% Comp'], C['Act Finish'],
                                        C['BL Finish'], C['BL Start'], C['Window'],
                                        C['By'])
        up.cell(row=r, column=N['Var (d)'],
                value=f'=IF(AND(${af}{r}<>"",${bf}{r}<>""),${af}{r}-${bf}{r},'
                      f'IF(AND(${st_}{r}<>"Complete",${bf}{r}<>"",StatusDate>${bf}{r}),'
                      f'StatusDate-${bf}{r},""))')
        up.cell(row=r, column=N['Window'],
                value=f'=IF(${st_}{r}="Complete","Complete",IF(${bf}{r}="","No dates",'
                      f'IF(${bf}{r}<StatusDate,"OVERDUE",IF(${bs}{r}<=StatusDate+7,"This Week",'
                      f'IF(${bs}{r}<=StatusDate+14,"2-Week",'
                      f'IF(${bs}{r}<=StatusDate+LookaheadWeeks*7,"Lookahead","Later"))))))')
        up.cell(row=r, column=N['Next Step'],
                value=f'=IF(${st_}{r}="Complete","Done",'
                      f'IF(${win}{r}="OVERDUE","Behind",'
                      f'IF(AND(${st_}{r}="In Progress",${pc}{r}>0),"Running",'
                      f'IF(${st_}{r}="On Hold","On hold",'
                      f'IF(${by}{r}="{CREW_WBO}","By others",'
                      f'IF(${win}{r}="This Week","Start now",'
                      f'IF(AND(${by}{r}="{CREW_TBD}",OR(${win}{r}="2-Week",${win}{r}="Lookahead")),"Needs a crew",'
                      f'IF(${win}{r}="2-Week","Coming up",'
                      f'IF(${win}{r}="Lookahead","In lookahead",'
                      f'IF(${win}{r}="No dates","No dates","Later"))))))))))')
        for key in ('Var (d)', 'Window', 'Next Step'):
            c = up.cell(row=r, column=N[key])
            c.font = _f(9); c.border = BOX; c.alignment = CTR
        up.cell(row=r, column=N['Var (d)']).number_format = '+0;-0;;@'
        # The flag the FIELD raises — the column that was being looked for.
        c = up.cell(row=r, column=N[COL_FLAG])
        c.fill = _fill(ENTRY); c.border = BOX; c.font = _f(9, True); c.alignment = CTR
        c.protection = Protection(locked=False)

    LAST = HR + len(ROWS)
    ST, BY, PC, FL, WN = (C['Status'], C['By'], C['% Comp'], C[COL_FLAG], C['Window'])
    up.freeze_panes = f"{C['Activity ID']}5"
    t = Table(displayName='UpdateTbl', ref=f'A{HR}:{LASTCOL}{LAST}')
    t.tableStyleInfo = TableStyleInfo(name='TableStyleLight1', showRowStripes=True)
    up.add_table(t)

    dv = DataValidation(type='list', formula1='"Not Started,In Progress,Complete,On Hold"',
                        allow_blank=True, showDropDown=False)
    up.add_data_validation(dv); dv.add(f'{ST}{HR+1}:{ST}{LAST}')
    # Suggests the three, still lets anyone type a subcontractor's name —
    # showErrorMessage off is what makes it a suggestion rather than a gate.
    dvby = DataValidation(type='list', formula1=_crew_options(own_crew), allow_blank=True,
                          showDropDown=False, showErrorMessage=False)
    up.add_data_validation(dvby); dvby.add(f'{BY}{HR+1}:{BY}{LAST}')
    dvp = DataValidation(type='decimal', operator='between', formula1=0, formula2=1,
                         allow_blank=True)
    dvp.error = 'Enter a percent between 0% and 100%'
    up.add_data_validation(dvp); dvp.add(f'{PC}{HR+1}:{PC}{LAST}')
    # The reasons work actually stops, in the words the field uses. Like the
    # By column this suggests rather than gates, so anything else can be typed.
    dvflag = DataValidation(type='list', formula1=f'"{",".join(FLAGS)}"',
                            allow_blank=True, showDropDown=False,
                            showErrorMessage=False)
    dvflag.prompt = ('Raise a flag when this activity needs attention. Pick one '
                     'or type your own, then filter the column to see them all.')
    dvflag.promptTitle = 'Flag this activity'
    dvflag.showInputMessage = True
    up.add_data_validation(dvflag); dvflag.add(f'{FL}{HR+1}:{FL}{LAST}')

    BODY = f'A{HR+1}:{LASTCOL}{LAST}'
    R1 = HR + 1
    # First rule wins in Excel, and a blocked row is the one you must not miss
    # — it goes ahead of Complete, which would otherwise paint over it.
    _cf(up, BODY, f'${FL}{R1}="Blocked"', BAD_BG, BAD_TX, bold=True)
    _cf(up, BODY, f'${ST}{R1}="Complete"', OK_BG, OK_TX)
    _cf(up, BODY, f'AND(${ST}{R1}<>"Complete",${BY}{R1}="{CREW_WBO}")', WBO_BG, WBO_TX, italic=True)
    _cf(up, BODY, f'AND(${ST}{R1}<>"Complete",${WN}{R1}="OVERDUE")', BAD_BG, BAD_TX, bold=True)
    _cf(up, BODY, f'${ST}{R1}="In Progress"', WIP_BG, WIP_TX)
    _cf(up, BODY, f'AND(${ST}{R1}="Not Started",${WN}{R1}="This Week")', WARN_BG, WARN_TX)
    _cf(up, BODY, f'${ST}{R1}="On Hold"', 'FFE0CC', '974706')
    _cf(up, f'{BY}{R1}:{BY}{LAST}', f'${BY}{R1}="{CREW_TBD}"', TBD_BG, TBD_TX, bold=True)
    # Any flag at all stands out in its own column, whatever was typed there.
    _cf(up, f'{FL}{R1}:{FL}{LAST}', f'${FL}{R1}<>""', FLAG_BG, FLAG_TX, bold=True)
    up.conditional_formatting.add(f'{PC}{R1}:{PC}{LAST}', DataBarRule(
        start_type='num', start_value=0, end_type='num', end_value=1, color=ACCENT))
    # Hidden, not removed. A removed column shifts every reference behind it;
    # a hidden one goes on calculating and is simply out of the way, which is
    # what "I do not need to see that" actually means.
    for h in spec.hidden:
        if h in C:
            up.column_dimensions[C[h]].hidden = True
    up.protection.sheet = True
    up.protection.autoFilter = False
    up.protection.sort = False
    up.protection.password = project_code.lower()

    U = 'Update!'
    # A whole Update column, addressed by the NAME of that column. Reading
    # through the same map the sheet was written with is what lets a column be
    # added without every dashboard formula silently pointing one column left.
    R = lambda name: (lambda col: f"{U}${col}${HR+1}:${col}${LAST}")(C[name])

    # ══════════════════════════════════════════════════════════ DASHBOARD ══
    db = wb.create_sheet('Dashboard')
    _title(db, f'{title or project_code} — Dashboard',
           'Read-only. Every figure recalculates from the Update tab.', 12)
    _head(db, 6, ['Phase', 'Activities', 'Complete', 'Running', 'Not Started', 'By others',
                  '% Complete', 'Overdue', 'This Week', 'Lookahead', 'Earliest', 'Latest',
                  'Flags ⚑'],
          [26, 11, 11, 11, 12, 11, 12, 10, 11, 11, 12, 12, 10])
    db.cell(row=5, column=1, value='BY PHASE').font = _f(11, True, NAVY)
    phases, seen = [], set()
    for rec in ROWS:
        if rec['phase'] not in seen:
            seen.add(rec['phase']); phases.append(rec['phase'])
    for i, ph in enumerate(phases):
        r = 7 + i
        q = f'{R(COL_PHASE)},$A{r}'
        db.cell(row=r, column=1, value=ph)
        for j, fx in ((2, f'=COUNTIFS({q})'),
                      (3, f'=COUNTIFS({q},{R(COL_STATUS)},"Complete")'),
                      (4, f'=COUNTIFS({q},{R(COL_STATUS)},"In Progress")'),
                      (5, f'=COUNTIFS({q},{R(COL_STATUS)},"Not Started")'),
                      (6, f'=COUNTIFS({q},{R(COL_BY)},"{CREW_WBO}")'),
                      (7, f'=IFERROR(AVERAGEIFS({R(COL_PCT)},{q}),0)'),
                      (8, f'=COUNTIFS({q},{R(COL_WINDOW)},"OVERDUE")'),
                      (9, f'=COUNTIFS({q},{R(COL_WINDOW)},"This Week")'),
                      (10, f'=COUNTIFS({q},{R(COL_WINDOW)},"Lookahead")'),
                      (11, f'=IFERROR(_xlfn.MINIFS({R(COL_BLSTART)},{q}),"")'),
                      (12, f'=IFERROR(_xlfn.MAXIFS({R(COL_BLFINISH)},{q}),"")'),
                      # Flags raised by the field, so they are not typed into
                      # a column nobody reads. "<>" is COUNTIFS for non-blank.
                      (13, f'=COUNTIFS({q},{R(COL_FLAG)},"<>")')):
            db.cell(row=r, column=j, value=fx)
        for j in range(1, 14):
            c = db.cell(row=r, column=j); c.border = BOX
            c.font = _f(10, j == 1); c.alignment = LEFT if j == 1 else CTR
            if j == 7:
                c.number_format = '0%'
            if j in (11, 12):
                c.number_format = 'mm/dd/yy'
    rT = 7 + len(phases)
    db.cell(row=rT, column=1, value=f'ALL {project_code}')
    for j, fx in ((2, f'=COUNTA({R(COL_ID)})'), (3, f'=COUNTIFS({R(COL_STATUS)},"Complete")'),
                  (4, f'=COUNTIFS({R(COL_STATUS)},"In Progress")'), (5, f'=COUNTIFS({R(COL_STATUS)},"Not Started")'),
                  (6, f'=COUNTIFS({R(COL_BY)},"{CREW_WBO}")'), (7, f'=IFERROR(AVERAGE({R(COL_PCT)}),0)'),
                  (8, f'=COUNTIFS({R(COL_WINDOW)},"OVERDUE")'), (9, f'=COUNTIFS({R(COL_WINDOW)},"This Week")'),
                  (10, f'=COUNTIFS({R(COL_WINDOW)},"Lookahead")'),
                  (13, f'=COUNTIFS({R(COL_FLAG)},"<>")')):
        db.cell(row=rT, column=j, value=fx)
    for j in range(1, 14):
        c = db.cell(row=rT, column=j)
        c.fill = _fill(NAVY); c.font = _f(10, True, 'FFFFFF'); c.border = BOX
        c.alignment = LEFT if j == 1 else CTR
        if j == 7:
            c.number_format = '0%'
    db.conditional_formatting.add(f'H7:H{rT}', CellIsRule(
        operator='greaterThan', formula=['0'], fill=_fill(BAD_BG),
        font=Font(name=FONT, size=10, color=BAD_TX, bold=True)))
    db.conditional_formatting.add(f'M7:M{rT}', CellIsRule(
        operator='greaterThan', formula=['0'], fill=_fill(FLAG_BG),
        font=Font(name=FONT, size=10, color=FLAG_TX, bold=True)))
    db.conditional_formatting.add(f'G7:G{rT-1}', DataBarRule(
        start_type='num', start_value=0, end_type='num', end_value=1, color='70AD47'))

    mr = rT + 3
    db.cell(row=mr, column=1, value='MILESTONES & CONTRACT DATES').font = _f(11, True, NAVY)
    _head(db, mr + 1, ['Milestone', 'Activity ID', 'Contract / BL', 'Forecast', 'Var (d)',
                       'Status', '', '', '', '', '', ''])
    for i, m in enumerate(MILS):
        r = mr + 2 + i
        aid = m['activity_id']
        db.cell(row=r, column=1, value=m['name'])
        db.cell(row=r, column=2, value=aid)
        c = db.cell(row=r, column=3, value=_date(m['bl_finish'])); c.number_format = 'mm/dd/yy'
        db.cell(row=r, column=4,
                value=f'=IFERROR(IF(INDEX({R(COL_AFINISH)},MATCH("{aid}",{R(COL_ID)},0))<>"",'
                      f'INDEX({R(COL_AFINISH)},MATCH("{aid}",{R(COL_ID)},0)),$C{r}),$C{r})')
        db.cell(row=r, column=5, value=f'=IFERROR($D{r}-$C{r},"")')
        db.cell(row=r, column=6, value=f'=IFERROR(INDEX({R(COL_STATUS)},MATCH("{aid}",{R(COL_ID)},0)),"")')
        key = 'Substantial Completion' in m['name'] or 'Certificate of Occupancy' in m['name']
        for j in range(1, 7):
            c = db.cell(row=r, column=j); c.border = BOX
            c.font = _f(9, key, NAVY if key else INK)
            c.alignment = LEFT if j == 1 else CTR
            if j == 4:
                c.number_format = 'mm/dd/yy'
            if j == 5:
                c.number_format = '+0;-0;0'
    # Not every schedule names a milestone. With none, the range below runs
    # backwards (E14:E13) and openpyxl raises — the whole export died on a job
    # whose only fault was that nobody had set the milestones up yet.
    if MILS:
        mlast = mr + 1 + len(MILS)
        db.conditional_formatting.add(f'E{mr+2}:E{mlast}', CellIsRule(
            operator='greaterThan', formula=['0'], fill=_fill(BAD_BG),
            font=Font(name=FONT, size=9, color=BAD_TX, bold=True)))
        db.conditional_formatting.add(f'E{mr+2}:E{mlast}', CellIsRule(
            operator='lessThan', formula=['0'], fill=_fill(OK_BG),
            font=Font(name=FONT, size=9, color=OK_TX)))
    db.freeze_panes = 'A4'
    db.protection.sheet = True
    db.protection.password = project_code.lower()

    # ══════════════════════════════════════════════════════════════ AREAS ══
    ar = wb.create_sheet('Areas')
    _title(ar, 'Areas — folder level',
           'One line per work area, in the order the WBS unfolds — the same '
           'order the app and P6 show it. Sub Area is everything below the '
           'area, so CUP breaks out into its line-ups instead of reading as '
           'one block of 280. NOTE: the filter dropdowns list values '
           'alphabetically — that is Excel and cannot be changed — so a name '
           'sitting oddly in a dropdown says nothing about the sheet. Sort by '
           '# to put the rows back in WBS order after any other sort.', 14)
    # Named, not hardcoded. Every letter below is looked up, so inserting a
    # column cannot silently point a formula at its neighbour — which is
    # exactly what went wrong the last time this sheet grew one.
    AHDR = ['#', 'Phase', 'Area', 'Sub Area', 'Activities', 'Complete',
            'Running', 'Not Started', 'By others', '% Complete', 'Overdue',
            'Starts', 'Ends', 'Flag']
    _head(ar, 4, AHDR, [5, 18, 24, 30, 10, 10, 9, 11, 10, 12, 9, 11, 11, 14])
    AC = {h: CL(i) for i, h in enumerate(AHDR, start=1)}
    AN = {h: i for i, h in enumerate(AHDR, start=1)}
    LABELS = ('#', 'Phase', 'Area', 'Sub Area')

    areas, seen = [], set()
    for rec in ROWS:
        k = (rec['phase'], rec['area'], rec['sub_area'])
        if k not in seen:
            seen.add(k); areas.append(k)
    for i, (ph, a, sub) in enumerate(areas):
        r = 5 + i
        q = (f'{R(COL_PHASE)},${AC["Phase"]}{r},'
             f'{R(COL_AREA)},${AC["Area"]}{r},'
             f'{R(COL_SUBAREA)},${AC["Sub Area"]}{r}')
        # The WBS position, so the hierarchy is a value on the row and not
        # just the order the rows happen to be in. Sorting by any other
        # column is one click; getting back was impossible before this.
        ar.cell(row=r, column=AN['#'], value=i + 1)
        ar.cell(row=r, column=AN['Phase'], value=ph)
        ar.cell(row=r, column=AN['Area'], value=a)
        ar.cell(row=r, column=AN['Sub Area'], value=sub)
        # The same phase colour the Update sheet uses, so the two read as one
        # document and a phase is recognisable without reading its name.
        band = phase_bg.get(ph)
        for h, fx in (
                ('Activities', f'=COUNTIFS({q})'),
                ('Complete', f'=COUNTIFS({q},{R(COL_STATUS)},"Complete")'),
                ('Running', f'=COUNTIFS({q},{R(COL_STATUS)},"In Progress")'),
                ('Not Started', f'=COUNTIFS({q},{R(COL_STATUS)},"Not Started")'),
                ('By others', f'=COUNTIFS({q},{R(COL_BY)},"{CREW_WBO}")'),
                ('% Complete', f'=IFERROR(AVERAGEIFS({R(COL_PCT)},{q}),0)'),
                ('Overdue', f'=COUNTIFS({q},{R(COL_WINDOW)},"OVERDUE")'),
                ('Starts', f'=IFERROR(_xlfn.MINIFS({R(COL_BLSTART)},{q}),"")'),
                ('Ends', f'=IFERROR(_xlfn.MAXIFS({R(COL_BLFINISH)},{q}),"")'),
                ('Flag',
                 f'=IF(${AC["Activities"]}{r}=0,"",'
                 f'IF(${AC["Complete"]}{r}=${AC["Activities"]}{r},"COMPLETE",'
                 f'IF(${AC["Overdue"]}{r}>0,"BEHIND",'
                 f'IF(${AC["Running"]}{r}>0,"Running",'
                 f'IF(${AC["Complete"]}{r}>0,"Part done","Not started")))))')):
            ar.cell(row=r, column=AN[h], value=fx)
        for h in AHDR:
            c = ar.cell(row=r, column=AN[h]); c.border = BOX
            if band and h in LABELS:
                c.fill = _fill(band)
            c.font = _f(9, h == 'Area')
            c.alignment = LEFT if h in ('Phase', 'Area', 'Sub Area') else CTR
            if h == '% Complete':
                c.number_format = '0%'
            if h in ('Starts', 'Ends'):
                c.number_format = 'mm/dd/yy'
    ALAST = 4 + len(areas)
    ar.freeze_panes = f'{AC["Activities"]}5'
    ALASTCOL = CL(len(AHDR))
    ar.auto_filter.ref = f'A4:{ALASTCOL}{ALAST}'
    for flag, bg, tx, bold in (('COMPLETE', OK_BG, OK_TX, False), ('BEHIND', BAD_BG, BAD_TX, True),
                               ('Running', WIP_BG, WIP_TX, False), ('Part done', 'DEEBF7', WIP_TX, False)):
        _cf(ar, f'{AC["Activities"]}5:{ALASTCOL}{ALAST}',
            f'${AC["Flag"]}5="{flag}"', bg, tx, bold=bold)
    ar.conditional_formatting.add(
        f'{AC["% Complete"]}5:{AC["% Complete"]}{ALAST}', DataBarRule(
            start_type='num', start_value=0, end_type='num', end_value=1, color=ACCENT))
    ar.protection.sheet = True
    ar.protection.autoFilter = False
    ar.protection.password = project_code.lower()

    # ══════════════════════════════════════════════════════ CRITICAL PATH ══
    cp = wb.create_sheet('Critical Path')
    _title(cp, 'Critical Path to Substantial Completion',
           'Seeded with the run that actually drives each date, walked back from Substantial '
           'Completion. Click a yellow cell and pick an Activity ID from the dropdown — start '
           'typing to narrow it — and the row fills itself in. Dates then run down the chain on '
           'a 6-day week, Sunday off. Modelled STRICTLY END TO END with no overlap, so it reads '
           'later than P6 wherever real logic runs work in parallel.', 10)
    for i, w in enumerate([6, 26, 44, 8, 12, 12, 12, 10, 10, 30], start=1):
        cp.column_dimensions[CL(i)].width = w

    sc = {}
    for m in MILS:
        if 'Substantial Completion' not in m['name']:
            continue
        g = re.search(r'\(?\bPH\s*(\d)\b\)?', m['name'], re.I)
        key = f'Phase {g.group(1)}' if g else m['name']
        sc.setdefault(key, (m['activity_id'], _date(m['bl_finish'])))
    if not sc:
        sc = {ph: ('', None) for ph in phases[:3]}
    BLOCK = 16
    row = 5
    for ph, (mid, contract) in list(sc.items())[:4]:
        byid = {x['activity_id']: x for x in ROWS}
        seed = [byid[i] for i in D['chains'].get(ph, []) if i in byid][-BLOCK:]
        cp.cell(row=row, column=1, value=f'{ph} — Substantial Completion').font = _f(13, True, NAVY)
        cp.cell(row=row, column=5, value='Contract:').font = _f(9, True)
        c = cp.cell(row=row, column=6, value=contract)
        c.number_format = 'mm/dd/yy'; c.font = _f(10, True, NAVY)
        cp.cell(row=row, column=7, value='Modelled:').font = _f(9, True)
        hrow = row
        row += 1
        _head(cp, row, ['No.', 'Activity ID', 'Activity Name', 'Days', 'Start', 'Finish',
                        'BL Finish', 'vs BL', '% Comp', 'Note'])
        first = row + 1
        for k in range(BLOCK):
            r = row + 1 + k
            cp.cell(row=r, column=1, value=k + 1).font = _f(9, False, MUTE)
            idc = cp.cell(row=r, column=2,
                          value=(seed[k]['activity_id'] if k < len(seed) else None))
            idc.fill = _fill(ENTRY); idc.font = _f(9); idc.protection = Protection(locked=False)
            cp.cell(row=r, column=3, value=f'=IFERROR(INDEX({R(COL_NAME)},MATCH($B{r},{R(COL_ID)},0)),"")')
            cp.cell(row=r, column=4, value=f'=IFERROR(INDEX({R(COL_DAYS)},MATCH($B{r},{R(COL_ID)},0)),"")')
            if k == 0:
                cp.cell(row=r, column=5,
                        value=f'=IFERROR(IF(INDEX({R(COL_ASTART)},MATCH($B{r},{R(COL_ID)},0))<>"",'
                              f'INDEX({R(COL_ASTART)},MATCH($B{r},{R(COL_ID)},0)),'
                              f'INDEX({R(COL_BLSTART)},MATCH($B{r},{R(COL_ID)},0))),"")')
            else:
                # Next working day on a 6-day week: +1, and +1 again only if
                # that lands on a Sunday (WEEKDAY(...,2)=7).
                cp.cell(row=r, column=5,
                        value=f'=IF($B{r}="","",IF($F{r-1}="","",'
                              f'$F{r-1}+1+IF(WEEKDAY($F{r-1}+1,2)=7,1,0)))')
            cp.cell(row=r, column=6,
                    # Add the remaining duration across a 6-day week. Every 6
                    # working days from the start weekday crosses one Sunday, so
                    # INT((weekday-1+n)/6) is exactly the number to skip.
                    value=f'=IF($B{r}="","",IFERROR(IF(INDEX({R(COL_AFINISH)},MATCH($B{r},{R(COL_ID)},0))<>"",'
                          f'INDEX({R(COL_AFINISH)},MATCH($B{r},{R(COL_ID)},0)),'
                          f'$E{r}+MAX(0,ROUND($D{r}*(1-$I{r}),0))'
                          f'+INT((WEEKDAY($E{r},2)-1+MAX(0,ROUND($D{r}*(1-$I{r}),0)))/6)),""))')
            cp.cell(row=r, column=7, value=f'=IFERROR(INDEX({R(COL_BLFINISH)},MATCH($B{r},{R(COL_ID)},0)),"")')
            cp.cell(row=r, column=8, value=f'=IF($B{r}="","",IFERROR($F{r}-$G{r},""))')
            cp.cell(row=r, column=9, value=f'=IFERROR(INDEX({R(COL_PCT)},MATCH($B{r},{R(COL_ID)},0)),0)')
            n = cp.cell(row=r, column=10)
            n.fill = _fill(ENTRY); n.protection = Protection(locked=False)
            for j in range(1, 11):
                c = cp.cell(row=r, column=j); c.border = BOX; c.font = _f(9)
                c.alignment = LEFT if j in (3, 10) else CTR
                if j in (5, 6, 7):
                    c.number_format = 'mm/dd/yy'
                if j == 9:
                    c.number_format = '0%'
                if j == 8:
                    c.number_format = '+0;-0;0'
        last = row + BLOCK
        c = cp.cell(row=hrow, column=8, value=f'=IFERROR(MAX($F{first}:$F{last}),"")')
        c.number_format = 'mm/dd/yy'; c.font = _f(10, True)
        c = cp.cell(row=hrow, column=9, value=f'=IF($H{hrow}="","",$H{hrow}-$F{hrow})')
        c.number_format = '+0" d";-0" d";"on time"'; c.font = _f(10, True)
        cp.conditional_formatting.add(f'H{hrow}', CellIsRule(
            operator='greaterThan', formula=[f'$F{hrow}'], fill=_fill(BAD_BG),
            font=Font(name=FONT, size=10, color=BAD_TX, bold=True)))
        _cf(cp, f'A{first}:J{last}', f'$I{first}=1', OK_BG, OK_TX)
        cp.conditional_formatting.add(f'H{first}:H{last}', CellIsRule(
            operator='greaterThan', formula=['0'], fill=_fill(BAD_BG),
            font=Font(name=FONT, size=9, color=BAD_TX, bold=True)))
        row = last + 3
    cp.protection.sheet = True
    cp.protection.password = project_code.lower()

    # ══════════════════════════════════════════════════════════════ NOTES ══
    nt = wb.create_sheet('Notes')
    _title(nt, 'Notes — by area', 'Longer than fits on an activity line. Yellow columns are yours.', 7)
    _head(nt, 4, ['Project', 'Phase', 'Area', 'Sub Area', 'Note / issue',
                  'Raised by', 'Date', 'Status'],
          [10, 18, 24, 28, 66, 14, 12, 10])
    for i, (ph, a, sub) in enumerate(areas):
        r = 5 + i
        nt.cell(row=r, column=1, value=project_code)
        nt.cell(row=r, column=2, value=ph)
        nt.cell(row=r, column=3, value=a)
        nt.cell(row=r, column=4, value=sub)
        band = phase_bg.get(ph)
        for j in (5, 6, 7, 8):
            c = nt.cell(row=r, column=j)
            c.fill = _fill(ENTRY); c.protection = Protection(locked=False)
            if j == 7:
                c.number_format = 'mm/dd/yy'
        for j in range(1, 9):
            c = nt.cell(row=r, column=j); c.border = BOX; c.font = _f(9, j == 3)
            c.alignment = WRAP if j == 5 else (LEFT if j in (2, 3, 4) else CTR)
            if band and j <= 4:
                c.fill = _fill(band)
        nt.row_dimensions[r].height = 22
    NLAST = 4 + len(areas)
    dvn = DataValidation(type='list', formula1='"Open,Watching,Closed"',
                         allow_blank=True, showDropDown=False, showErrorMessage=False)
    nt.add_data_validation(dvn); dvn.add(f'H5:H{NLAST}')
    _cf(nt, f'A5:H{NLAST}', '$H5="Closed"', NEW_BG, MUTE, italic=True)
    _cf(nt, f'A5:H{NLAST}', 'AND($E5<>"",$H5<>"Closed")', WARN_BG, WARN_TX)
    nt.freeze_panes = 'E5'
    nt.auto_filter.ref = f'A4:H{NLAST}'
    nt.protection.sheet = True
    nt.protection.autoFilter = False
    nt.protection.password = project_code.lower()

    # ═══════════════════════════════════════════════════════════════ DATA ══
    da = wb.create_sheet('Data')
    _title(da, 'Schedule reference',
           'Straight from the schedule. Read-only — nothing here feeds the other tabs. '
           'Column A also backs the Activity ID dropdown on Critical Path.', 10)
    _head(da, 4, ['Activity ID', 'Activity Name', 'WBS path', 'Type', 'Float (d)',
                  'Predecessors', 'Constraint', 'Crew', 'Status', 'Phase'],
          [24, 46, 62, 16, 9, 34, 16, 7, 12, 18])
    for i, rec in enumerate(ROWS):
        r = 5 + i
        for j, v in enumerate([rec['activity_id'], rec['name'], rec['path'], rec['type'],
                               rec['float_d'], rec['preds'], rec['constraint'],
                               rec['crew'], rec['status'], rec['phase']], start=1):
            c = da.cell(row=r, column=j, value=v)
            c.font = _f(9); c.border = BOX
            c.alignment = CTR if j in (4, 5, 7, 8, 9) else LEFT
    DLAST = 4 + len(ROWS)
    da.freeze_panes = 'B5'
    da.auto_filter.ref = f'A4:J{DLAST}'
    da.conditional_formatting.add(f'E5:E{DLAST}', CellIsRule(
        operator='lessThanOrEqual', formula=['0'], fill=_fill(BAD_BG),
        font=Font(name=FONT, size=9, color=BAD_TX, bold=True)))
    da.protection.sheet = True
    da.protection.autoFilter = False
    da.protection.password = project_code.lower()

    # ── names ───────────────────────────────────────────────────────────────
    wb.defined_names.add(DefinedName('StatusDate', attr_text="'Start Here'!$B$6"))
    wb.defined_names.add(DefinedName('LookaheadWeeks', attr_text="'Start Here'!$B$7"))
    # Backs the Activity ID dropdown. Excel 365 narrows a validation list as you
    # type, so this is the type-ahead: three characters gets you to the row
    # instead of copying a 22-character code by hand.
    wb.defined_names.add(DefinedName('ActivityIDs', attr_text=f"Data!$A$5:$A${DLAST}"))

    # Applied after the name exists, since the formula refers to it.
    for ws_cp in (wb['Critical Path'],):
        dvid = DataValidation(type='list', formula1='=ActivityIDs', allow_blank=True,
                              showDropDown=False, showErrorMessage=False)
        dvid.prompt = 'Pick an Activity ID, or start typing to narrow the list'
        dvid.promptTitle = 'Activity'
        ws_cp.add_data_validation(dvid)
        r = 6
        while r <= ws_cp.max_row:
            if ws_cp.cell(row=r, column=1).value == 'No.':
                dvid.add(f'B{r+1}:B{r+BLOCK}')
                r += BLOCK
            r += 1

    # ═════════════════════════════════════════════════ SHEETS THAT WERE ASKED FOR ══
    # Anything the generator knows nothing about, carried whole and rebuilt on
    # every export. Written last so it can never sit between the generated tabs
    # or take a name one of them needs.
    for extra in spec.sheets:
        if extra.name in wb.sheetnames:
            continue                          # a generated tab wins its name
        xs = wb.create_sheet(extra.name)
        _title(xs, extra.name, extra.note or 'Added to this tracker. Rebuilt on '
                               'every export, so it survives a schedule revision.',
               max(len(extra.headers), 1))
        if extra.headers:
            _head(xs, 4, [str(h) for h in extra.headers],
                  [max(12, min(48, len(str(h)) + 8)) for h in extra.headers])
        for i, row in enumerate(extra.rows):
            r = 5 + i
            for j, v in enumerate(row[:len(extra.headers) or len(row)], start=1):
                c = xs.cell(row=r, column=j, value=v)
                c.font = _f(9); c.border = BOX
                c.alignment = LEFT if j == 1 else CTR
        if extra.headers and extra.rows:
            xs.freeze_panes = 'A5'
            xs.auto_filter.ref = (f'A4:{CL(len(extra.headers))}'
                                  f'{4 + len(extra.rows)}')

    wb.active = 0
    wb.save(out_path)
    return out_path
