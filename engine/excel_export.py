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
ENTRY = 'FFF9E3'
FONT = 'Arial'

# Who is doing the work. "TBD" is the honest default for anything nobody has
# claimed yet, and it is a filter the PM actually wants: unassigned work is a
# question, not a blank.
CREW_TBD, CREW_WBO, CREW_OWN = 'TBD', 'Work by others', 'Richards'
def _crew_options(own: str) -> str:
    return f'"{CREW_TBD},{CREW_WBO},{own}"'

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


def _collect(project, project_code: str) -> Dict[str, Any]:
    """Flatten the schedule into the rows the workbook is built from."""
    from engine.logic_advisor import location_tag, strip_location, wbs_path
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
        path = wbs_path(project, a) or ''
        seg = path.split(' / ')
        phase = seg[1] if len(seg) > 1 else '(unfiled)'
        area = seg[2] if len(seg) > 2 else phase
        name = a.name or ''
        wbo = bool(_WBO_RE.search(name))
        rows.append({
            'project': project_code,
            'phase': phase,
            'area': area,
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
    rows.sort(key=lambda r: (r['phase'], r['area'], r['bl_start'] or '9999', r['activity_id']))

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
                   own_crew: str = CREW_OWN) -> str:
    """
    Write the tracker for this project. Returns the path written.

    Nothing about one job is baked in: the code, the phases, the areas, the
    contract dates and the driving chains all come out of the schedule that
    was handed in. `leads` and `own_crew` are the only preferences, and both
    have defaults rather than requirements.
    """
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
                   'Actual Start, Actual Finish, Notes, Updated By.'),
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
            ('Nobody assigned (TBD)', TBD_BG, TBD_TX), ('Type in yellow cells', ENTRY, INK)]):
        c = ws.cell(row=lr + 1 + i, column=1, value=lab)
        c.fill = _fill(bg); c.font = _f(10, True, tx); c.border = BOX; c.alignment = CTR
    ws.freeze_panes = 'A4'

    # ═════════════════════════════════════════════════════════════ UPDATE ══
    up = wb.create_sheet('Update')
    _title(up, 'Update — field entry',
           'Fill the yellow columns only. Filter with the header arrows; clear the '
           'filters to see the whole schedule.', 22)
    HDR = ['Project', 'Lead', 'Phase', 'Area', 'Room', 'Work Type', 'Activity ID',
           'Activity Name', 'By', 'BL Start', 'BL Finish', 'Days', 'Crew',
           'Status', '% Comp', 'Act Start', 'Act Finish', 'Notes', 'Updated By',
           'Var (d)', 'Window', 'Flag']
    _head(up, 4, HDR, [9, 9, 15, 22, 11, 26, 22, 46, 15, 11, 11, 7, 7,
                       14, 8, 11, 11, 34, 12, 9, 13, 14])
    HR = 4
    for i, rec in enumerate(ROWS):
        r = HR + 1 + i
        base = [rec['project'], leads.get(rec['project'], ''), rec['phase'], rec['area'],
                rec['room'], rec['work_type'], rec['activity_id'], rec['name']]
        for j, v in enumerate(base, start=1):
            c = up.cell(row=r, column=j, value=v)
            c.font = _f(9); c.border = BOX
            c.alignment = CTR if j in (1, 2, 5) else LEFT
        # "By" is an ENTRY cell seeded from the **WBO tag in the name.
        c = up.cell(row=r, column=9, value=rec['by'])
        c.fill = _fill(ENTRY); c.border = BOX; c.font = _f(9); c.alignment = CTR
        c.protection = Protection(locked=False)
        for j, v in ((10, _date(rec['bl_start'])), (11, _date(rec['bl_finish']))):
            c = up.cell(row=r, column=j, value=v)
            c.number_format = 'mm/dd/yy'; c.font = _f(9); c.border = BOX; c.alignment = CTR
        for j, v in ((12, rec['days']), (13, rec['crew'])):
            c = up.cell(row=r, column=j)
            if j == 13 and v not in ('', None):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    pass
            c.value = v; c.font = _f(9); c.border = BOX; c.alignment = CTR
        st = {'Completed': 'Complete', 'In Progress': 'In Progress'}.get(rec['status'], 'Not Started')
        seed = [st, (rec['pct'] or (1.0 if st == 'Complete' else 0.0)),
                _date(rec['act_start']), _date(rec['act_finish']), None, None]
        for j, v in zip(range(14, 20), seed):
            c = up.cell(row=r, column=j, value=v)
            c.fill = _fill(ENTRY); c.border = BOX; c.font = _f(9)
            c.protection = Protection(locked=False)
            if j == 15:
                c.number_format = '0%'; c.alignment = CTR
            elif j in (16, 17):
                c.number_format = 'mm/dd/yy'; c.alignment = CTR
            else:
                c.alignment = CTR if j == 14 else LEFT
        up.cell(row=r, column=20,
                value=f'=IF(AND($Q{r}<>"",$K{r}<>""),$Q{r}-$K{r},'
                      f'IF(AND($N{r}<>"Complete",$K{r}<>"",StatusDate>$K{r}),StatusDate-$K{r},""))')
        up.cell(row=r, column=21,
                value=f'=IF($N{r}="Complete","Complete",IF($K{r}="","No dates",'
                      f'IF($K{r}<StatusDate,"OVERDUE",IF($J{r}<=StatusDate+7,"This Week",'
                      f'IF($J{r}<=StatusDate+14,"2-Week",'
                      f'IF($J{r}<=StatusDate+LookaheadWeeks*7,"Lookahead","Later"))))))')
        up.cell(row=r, column=22,
                value=f'=IF($N{r}="Complete","Done",'
                      f'IF($U{r}="OVERDUE","Behind",'
                      f'IF(AND($N{r}="In Progress",$O{r}>0),"Running",'
                      f'IF($U{r}="This Week","Start now",'
                      f'IF($I{r}="{CREW_WBO}","By others","")))))')
        for j in (20, 21, 22):
            c = up.cell(row=r, column=j); c.font = _f(9); c.border = BOX; c.alignment = CTR
        up.cell(row=r, column=20).number_format = '+0;-0;;@'

    LAST = HR + len(ROWS)
    up.freeze_panes = 'H5'
    t = Table(displayName='UpdateTbl', ref=f'A{HR}:V{LAST}')
    t.tableStyleInfo = TableStyleInfo(name='TableStyleLight1', showRowStripes=True)
    up.add_table(t)

    dv = DataValidation(type='list', formula1='"Not Started,In Progress,Complete,On Hold"',
                        allow_blank=True, showDropDown=False)
    up.add_data_validation(dv); dv.add(f'N{HR+1}:N{LAST}')
    # Suggests the three, still lets anyone type a subcontractor's name —
    # showErrorMessage off is what makes it a suggestion rather than a gate.
    dvby = DataValidation(type='list', formula1=_crew_options(own_crew), allow_blank=True,
                          showDropDown=False, showErrorMessage=False)
    up.add_data_validation(dvby); dvby.add(f'I{HR+1}:I{LAST}')
    dvp = DataValidation(type='decimal', operator='between', formula1=0, formula2=1,
                         allow_blank=True)
    dvp.error = 'Enter a percent between 0% and 100%'
    up.add_data_validation(dvp); dvp.add(f'O{HR+1}:O{LAST}')

    BODY = f'A{HR+1}:V{LAST}'
    _cf(up, BODY, f'$N{HR+1}="Complete"', OK_BG, OK_TX)
    _cf(up, BODY, f'AND($N{HR+1}<>"Complete",$I{HR+1}="{CREW_WBO}")', WBO_BG, WBO_TX, italic=True)
    _cf(up, BODY, f'AND($N{HR+1}<>"Complete",$U{HR+1}="OVERDUE")', BAD_BG, BAD_TX, bold=True)
    _cf(up, BODY, f'$N{HR+1}="In Progress"', WIP_BG, WIP_TX)
    _cf(up, BODY, f'AND($N{HR+1}="Not Started",$U{HR+1}="This Week")', WARN_BG, WARN_TX)
    _cf(up, BODY, f'$N{HR+1}="On Hold"', 'FFE0CC', '974706')
    _cf(up, f'I{HR+1}:I{LAST}', f'$I{HR+1}="{CREW_TBD}"', TBD_BG, TBD_TX, bold=True)
    up.conditional_formatting.add(f'O{HR+1}:O{LAST}', DataBarRule(
        start_type='num', start_value=0, end_type='num', end_value=1, color=ACCENT))
    up.protection.sheet = True
    up.protection.autoFilter = False
    up.protection.sort = False
    up.protection.password = project_code.lower()

    U = 'Update!'
    R = lambda col: f"{U}${col}${HR+1}:${col}${LAST}"

    # ══════════════════════════════════════════════════════════ DASHBOARD ══
    db = wb.create_sheet('Dashboard')
    _title(db, f'{title or project_code} — Dashboard',
           'Read-only. Every figure recalculates from the Update tab.', 12)
    _head(db, 6, ['Phase', 'Activities', 'Complete', 'Running', 'Not Started', 'By others',
                  '% Complete', 'Overdue', 'This Week', 'Lookahead', 'Earliest', 'Latest'],
          [26, 11, 11, 11, 12, 11, 12, 10, 11, 11, 12, 12])
    db.cell(row=5, column=1, value='BY PHASE').font = _f(11, True, NAVY)
    phases, seen = [], set()
    for rec in ROWS:
        if rec['phase'] not in seen:
            seen.add(rec['phase']); phases.append(rec['phase'])
    for i, ph in enumerate(phases):
        r = 7 + i
        q = f'{R("C")},$A{r}'
        db.cell(row=r, column=1, value=ph)
        for j, fx in ((2, f'=COUNTIFS({q})'),
                      (3, f'=COUNTIFS({q},{R("N")},"Complete")'),
                      (4, f'=COUNTIFS({q},{R("N")},"In Progress")'),
                      (5, f'=COUNTIFS({q},{R("N")},"Not Started")'),
                      (6, f'=COUNTIFS({q},{R("I")},"{CREW_WBO}")'),
                      (7, f'=IFERROR(AVERAGEIFS({R("O")},{q}),0)'),
                      (8, f'=COUNTIFS({q},{R("U")},"OVERDUE")'),
                      (9, f'=COUNTIFS({q},{R("U")},"This Week")'),
                      (10, f'=COUNTIFS({q},{R("U")},"Lookahead")'),
                      (11, f'=IFERROR(_xlfn.MINIFS({R("J")},{q}),"")'),
                      (12, f'=IFERROR(_xlfn.MAXIFS({R("K")},{q}),"")')):
            db.cell(row=r, column=j, value=fx)
        for j in range(1, 13):
            c = db.cell(row=r, column=j); c.border = BOX
            c.font = _f(10, j == 1); c.alignment = LEFT if j == 1 else CTR
            if j == 7:
                c.number_format = '0%'
            if j in (11, 12):
                c.number_format = 'mm/dd/yy'
    rT = 7 + len(phases)
    db.cell(row=rT, column=1, value=f'ALL {project_code}')
    for j, fx in ((2, f'=COUNTA({R("G")})'), (3, f'=COUNTIFS({R("N")},"Complete")'),
                  (4, f'=COUNTIFS({R("N")},"In Progress")'), (5, f'=COUNTIFS({R("N")},"Not Started")'),
                  (6, f'=COUNTIFS({R("I")},"{CREW_WBO}")'), (7, f'=IFERROR(AVERAGE({R("O")}),0)'),
                  (8, f'=COUNTIFS({R("U")},"OVERDUE")'), (9, f'=COUNTIFS({R("U")},"This Week")'),
                  (10, f'=COUNTIFS({R("U")},"Lookahead")')):
        db.cell(row=rT, column=j, value=fx)
    for j in range(1, 13):
        c = db.cell(row=rT, column=j)
        c.fill = _fill(NAVY); c.font = _f(10, True, 'FFFFFF'); c.border = BOX
        c.alignment = LEFT if j == 1 else CTR
        if j == 7:
            c.number_format = '0%'
    db.conditional_formatting.add(f'H7:H{rT}', CellIsRule(
        operator='greaterThan', formula=['0'], fill=_fill(BAD_BG),
        font=Font(name=FONT, size=10, color=BAD_TX, bold=True)))
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
                value=f'=IFERROR(IF(INDEX({R("Q")},MATCH("{aid}",{R("G")},0))<>"",'
                      f'INDEX({R("Q")},MATCH("{aid}",{R("G")},0)),$C{r}),$C{r})')
        db.cell(row=r, column=5, value=f'=IFERROR($D{r}-$C{r},"")')
        db.cell(row=r, column=6, value=f'=IFERROR(INDEX({R("N")},MATCH("{aid}",{R("G")},0)),"")')
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
    _title(ar, 'Areas — folder level', 'One line per work area, in schedule order.', 12)
    _head(ar, 4, ['Phase', 'Area', 'Activities', 'Complete', 'Running', 'Not Started',
                  'By others', '% Complete', 'Overdue', 'Starts', 'Ends', 'Flag'],
          [18, 32, 10, 10, 9, 11, 10, 12, 9, 11, 11, 14])
    areas, seen = [], set()
    for rec in ROWS:
        k = (rec['phase'], rec['area'])
        if k not in seen:
            seen.add(k); areas.append(k)
    for i, (ph, a) in enumerate(areas):
        r = 5 + i
        q = f'{R("C")},$A{r},{R("D")},$B{r}'
        ar.cell(row=r, column=1, value=ph)
        ar.cell(row=r, column=2, value=a)
        for j, fx in ((3, f'=COUNTIFS({q})'),
                      (4, f'=COUNTIFS({q},{R("N")},"Complete")'),
                      (5, f'=COUNTIFS({q},{R("N")},"In Progress")'),
                      (6, f'=COUNTIFS({q},{R("N")},"Not Started")'),
                      (7, f'=COUNTIFS({q},{R("I")},"{CREW_WBO}")'),
                      (8, f'=IFERROR(AVERAGEIFS({R("O")},{q}),0)'),
                      (9, f'=COUNTIFS({q},{R("U")},"OVERDUE")'),
                      (10, f'=IFERROR(_xlfn.MINIFS({R("J")},{q}),"")'),
                      (11, f'=IFERROR(_xlfn.MAXIFS({R("K")},{q}),"")'),
                      (12, f'=IF($C{r}=0,"",IF($D{r}=$C{r},"COMPLETE",'
                           f'IF($I{r}>0,"BEHIND",IF($E{r}>0,"Running",'
                           f'IF($D{r}>0,"Part done","Not started")))))')):
            ar.cell(row=r, column=j, value=fx)
        for j in range(1, 13):
            c = ar.cell(row=r, column=j); c.border = BOX
            c.font = _f(9, j == 2); c.alignment = LEFT if j in (1, 2) else CTR
            if j == 8:
                c.number_format = '0%'
            if j in (10, 11):
                c.number_format = 'mm/dd/yy'
    ALAST = 4 + len(areas)
    ar.freeze_panes = 'C5'
    ar.auto_filter.ref = f'A4:L{ALAST}'
    for flag, bg, tx, bold in (('COMPLETE', OK_BG, OK_TX, False), ('BEHIND', BAD_BG, BAD_TX, True),
                               ('Running', WIP_BG, WIP_TX, False), ('Part done', 'DEEBF7', WIP_TX, False)):
        _cf(ar, f'A5:L{ALAST}', f'$L5="{flag}"', bg, tx, bold=bold)
    ar.conditional_formatting.add(f'H5:H{ALAST}', DataBarRule(
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
            cp.cell(row=r, column=3, value=f'=IFERROR(INDEX({R("H")},MATCH($B{r},{R("G")},0)),"")')
            cp.cell(row=r, column=4, value=f'=IFERROR(INDEX({R("L")},MATCH($B{r},{R("G")},0)),"")')
            if k == 0:
                cp.cell(row=r, column=5,
                        value=f'=IFERROR(IF(INDEX({R("P")},MATCH($B{r},{R("G")},0))<>"",'
                              f'INDEX({R("P")},MATCH($B{r},{R("G")},0)),'
                              f'INDEX({R("J")},MATCH($B{r},{R("G")},0))),"")')
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
                    value=f'=IF($B{r}="","",IFERROR(IF(INDEX({R("Q")},MATCH($B{r},{R("G")},0))<>"",'
                          f'INDEX({R("Q")},MATCH($B{r},{R("G")},0)),'
                          f'$E{r}+MAX(0,ROUND($D{r}*(1-$I{r}),0))'
                          f'+INT((WEEKDAY($E{r},2)-1+MAX(0,ROUND($D{r}*(1-$I{r}),0)))/6)),""))')
            cp.cell(row=r, column=7, value=f'=IFERROR(INDEX({R("K")},MATCH($B{r},{R("G")},0)),"")')
            cp.cell(row=r, column=8, value=f'=IF($B{r}="","",IFERROR($F{r}-$G{r},""))')
            cp.cell(row=r, column=9, value=f'=IFERROR(INDEX({R("O")},MATCH($B{r},{R("G")},0)),0)')
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
    _head(nt, 4, ['Project', 'Phase', 'Area', 'Note / issue', 'Raised by', 'Date', 'Status'],
          [10, 18, 30, 74, 14, 12, 10])
    for i, (ph, a) in enumerate(areas):
        r = 5 + i
        nt.cell(row=r, column=1, value=project_code)
        nt.cell(row=r, column=2, value=ph)
        nt.cell(row=r, column=3, value=a)
        for j in (4, 5, 6, 7):
            c = nt.cell(row=r, column=j)
            c.fill = _fill(ENTRY); c.protection = Protection(locked=False)
            if j == 6:
                c.number_format = 'mm/dd/yy'
        for j in range(1, 8):
            c = nt.cell(row=r, column=j); c.border = BOX; c.font = _f(9, j == 3)
            c.alignment = WRAP if j == 4 else (LEFT if j in (2, 3) else CTR)
        nt.row_dimensions[r].height = 22
    NLAST = 4 + len(areas)
    dvn = DataValidation(type='list', formula1='"Open,Watching,Closed"',
                         allow_blank=True, showDropDown=False, showErrorMessage=False)
    nt.add_data_validation(dvn); dvn.add(f'G5:G{NLAST}')
    _cf(nt, f'A5:G{NLAST}', '$G5="Closed"', NEW_BG, MUTE, italic=True)
    _cf(nt, f'A5:G{NLAST}', 'AND($D5<>"",$G5<>"Closed")', WARN_BG, WARN_TX)
    nt.freeze_panes = 'D5'
    nt.auto_filter.ref = f'A4:G{NLAST}'
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

    wb.active = 0
    wb.save(out_path)
    return out_path
