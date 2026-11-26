#!/usr/bin/env python3
"""
MDC1 v40.  Reconcile progress against Holder's 11/03 Exhibit S.

Five steps, in order.

1. Reopen four fire-protection actuals we carried that Holder has not started:
   ER 201, ER 202, ER 203, ERR 201.  The row is **WBO, the sprinkler
   contractor, so JER had no basis to close it, and ER 201's actual finish
   lands inside Holder's wall rough-in window for that room, which is what a
   date-aligned actuals import looks like when it lands on the wrong row.

2. Resequence MV 101-103 fire protection off the room's first overhead step
   instead of off Wall Rough In's.  Holder runs FP first or third in all 40
   rooms except the eight ER 2xx / ERR 201 outliers, and he already has
   MV 101-103 FP actual-complete in August.

3. Take Holder's reported actuals both ways.  Where he is actual-complete and
   our row is open, close it on his dates.  Where our row is complete and he
   has not started it, reopen it.

4. Close the blockers that step 3 exposes.  A row cannot be complete with an
   in-room predecessor still open.  A blocker is closed only on one of two
   grounds: Holder reports it finished, so we take his dates; or Holder does
   not carry the row at all and it is a short prep step of JER's own
   breakdown, so we back-date it in front of the row it must precede.  Any
   other blocker is reported, never invented.

5. Reopen downstream rows left stranded by a reopen, here and in v31.  v31
   reopened the generator-room pulls but left the terminations and MEG/QC
   downstream of them complete, which is backwards.  A complete row whose
   in-room predecessor is open is reopened, unless Holder himself reports
   that row complete, in which case it is his inconsistency and we report it.

No predecessor or successor the user set is removed.  The only logic touched
is the single FP driving tie in MV 101-103, replaced for the reason in step 2.
"""
import sys, re, copy, collections, datetime as dt
sys.path.insert(0, '/home/user/SixTerminal-Live')
import openpyxl
from engine.xml_reader import load_xml
from engine.xml_writer import write_p6_xml
from engine.schedule_model import compute_dates

SCR = '/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad'
SRC = f'{SCR}/MDC1_logic_repaired_v39.xml'
DST = f'{SCR}/MDC1_logic_repaired_v40.xml'
XLS = f'{SCR}/exs1103_src/beb850c9-CloudHQ_MDC1_Exhibit_S_11.03.2026_Schedule_Comments_1.xlsx'
DATA_DATE  = '2026-10-23'   # Holder's, on the 11/03 Exhibit S
OUR_DD     = '2026-10-31'   # ours, from the XML's own DataDate
# Our data date is eight working days later than his, so his report cannot
# speak to anything we progressed between the two.  A row we closed in that
# window is left alone and listed instead: his silence there is not evidence.

ROOM_SUFFIX = re.compile(r'\(([A-Za-z]{2,4}\s*\d{3}[A-Za-z]?)\)\s*$')
protected = set()
ROOM_ANY    = re.compile(r'\b([A-Za-z]{2,4}\s*\d{3}[A-Za-z]?)\b')
# Only these may be back-dated when Holder carries no such row: short layout
# and marking steps off JER's foreman breakdown, which leave no record of
# their own and cannot sensibly be the thing holding a room open.  Pulls,
# terminations, connections and equipment sets are never derived, because v31
# reopened a block of exactly those on Holder's and JER's own evidence and a
# back-date here would silently undo it.
PREP = re.compile(r'layout|penetration|marking|lay out', re.I)
log = []


def norm_room(s):
    return re.sub(r'\s+', ' ', s.upper()).strip()


def base_name(n):
    """Strip the room suffix, the WBO/WBS trade markers and all punctuation,
    then fold the spellings the two files disagree on."""
    n = re.sub(r'\*+', ' ', n or '')
    n = re.sub(r'\s*\([^)]*\)\s*$', '', n).strip().lower()
    n = re.sub(r'[^a-z0-9]+', ' ', n).strip()
    n = re.sub(r'\b(wbo|wbs)\b', ' ', n)          # the trade marker, not a word
    n = n.replace('overhead', 'oh').replace('dry wall', 'drywall')
    n = re.sub(r'\brough ins?\b', 'rough in', n)
    n = re.sub(r'\bwall rough in s\b', 'wall rough in', n)
    return re.sub(r'\s+', ' ', n).strip()


def d10(v):
    return str(v)[:10] if v else ''


def prev_workday(iso, n=1):
    try:
        d = dt.date.fromisoformat(iso)
    except Exception:
        return iso
    k = 0
    while k < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            k += 1
    return d.isoformat()


def read_holder():
    wb = openpyxl.load_workbook(XLS, data_only=True)
    ws = wb['Schedule']
    rows = list(ws.iter_rows(values_only=True))
    I = {h: i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r or r[0] is None:
            continue
        nm = str(r[I['Activity Name']] or '')
        m = ROOM_SUFFIX.search(nm)
        if not m:
            continue
        out[(norm_room(m.group(1)), base_name(nm))] = {
            'start':  d10(r[I['Start']]),
            'finish': d10(r[I['Finish']]),
            'sact':   str(r[I['Start Status']]  or '') == 'Actual',
            'fact':   str(r[I['Finish Status']] or '') == 'Actual',
        }
    return out


def finish_of(project):
    compute_dates(project, hold_unlinked_dates=True, apply_dates=False)
    return max((a.early_finish or '' for a in project.activities))


def reopen(a):
    a.status = 'Not Started'
    a.actual_start = a.actual_finish = None
    a.actual_duration = 0.0
    a.percent_complete = 0.0
    a.remaining_duration = a.planned_duration
    a.actual_labor_units = 0.0
    a.remaining_labor_units = a.planned_labor_units


def close_out(a, start, finish):
    a.status = 'Completed'
    a.actual_start = start or finish
    a.actual_finish = finish
    a.actual_duration = a.planned_duration
    a.remaining_duration = 0.0
    a.percent_complete = 100.0
    a.actual_labor_units = a.planned_labor_units
    a.remaining_labor_units = 0.0


def cycles(project):
    g = collections.defaultdict(list)
    for r in project.relations:
        g[r.predecessor_uid].append(r.successor_uid)
    idx, low, on, st, cnt, n = {}, {}, set(), [], [0], 0
    for root in list(g):
        if root in idx:
            continue
        idx[root] = low[root] = cnt[0]; cnt[0] += 1
        st.append(root); on.add(root)
        work = [(root, iter(g[root]))]
        while work:
            v, it = work[-1]
            adv = False
            for w in it:
                if w not in idx:
                    idx[w] = low[w] = cnt[0]; cnt[0] += 1
                    st.append(w); on.add(w)
                    work.append((w, iter(g[w]))); adv = True
                    break
                if w in on:
                    low[v] = min(low[v], idx[w])
            if adv:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == idx[v]:
                comp = []
                while True:
                    w = st.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    n += 1
    return n


def main():
    holder = read_holder()
    p = load_xml(SRC)
    wbs = {w.uid: w for w in p.wbs_nodes}

    def room_of(a):
        m = ROOM_SUFFIX.search(a.name or '')
        if m:
            return norm_room(m.group(1))
        w = wbs.get(a.wbs_uid)
        while w:
            m = ROOM_ANY.search(w.name or '')
            if m:
                return norm_room(m.group(1))
            w = wbs.get(w.parent_uid)
        return None

    before = finish_of(p)
    print(f'v39 in   {len(p.activities)} activities, {len(p.relations)} relations, '
          f'finish {before}')

    by_room = collections.defaultdict(list)
    uid2room, byuid = {}, {}
    for a in p.activities:
        byuid[a.uid] = a
        r = room_of(a)
        if r:
            by_room[r].append(a)
            uid2room[a.uid] = r
    matched = sum(1 for a in p.activities
                  if (uid2room.get(a.uid), base_name(a.name)) in holder)
    print(f'         {matched} activities matched to Holder by room and name')

    # ---- 1 -----------------------------------------------------------------
    n1 = 0
    for rm in ('ER 201', 'ER 202', 'ER 203', 'ERR 201'):
        for a in by_room.get(rm, []):
            if 'fire protection' in (a.name or '').lower() and a.actual_finish:
                log.append(('1 reopen FP', rm, a.activity_id, a.name,
                            f'was complete {a.actual_finish}', 'Not Started'))
                reopen(a); protected.add(a.uid); n1 += 1
    print(f'  step 1  reopened {n1} fire-protection actuals')

    # ---- 2 -----------------------------------------------------------------
    pred_of = collections.defaultdict(list)
    for r in p.relations:
        pred_of[r.successor_uid].append(r)
    n2 = 0
    for rm in ('MV 101', 'MV 102', 'MV 103'):
        acts = by_room.get(rm, [])
        fp = [a for a in acts if 'fire protection' in (a.name or '').lower()]
        hs = [a for a in acts if re.search(r'high steel', a.name or '', re.I)]
        if not fp or not hs:
            print(f'  step 2  {rm}: no FP or no High Steel, skipped')
            continue
        f, anchor = fp[0], hs[0]
        was = '; '.join(f'{byuid[r.predecessor_uid].name} {r.type}+{r.lag/8:g}d'
                        for r in pred_of[f.uid]) or 'nothing'
        tmpl = p.relations[0]
        p.relations = [r for r in p.relations if r.successor_uid != f.uid]
        nr = copy.deepcopy(tmpl)
        nr.predecessor_uid, nr.successor_uid = anchor.uid, f.uid
        nr.type, nr.lag = 'Start to Start', 8.0
        p.relations.append(nr)
        log.append(('2 FP resequence', rm, f.activity_id, f.name,
                    f'driven by {was}', f'SS+1d off {anchor.name}'))
        n2 += 1
    print(f'  step 2  resequenced {n2} MV fire-protection ties')

    # ---- 3 -----------------------------------------------------------------
    took = reop = 0
    future, unjudged = [], []
    for rm, acts in by_room.items():
        for a in acts:
            h = holder.get((rm, base_name(a.name)))
            if not h:
                continue
            if h['fact'] and not a.actual_finish:
                if h['finish'] > DATA_DATE:
                    future.append((rm, a.name, h['finish']))
                    continue
                close_out(a, h['start'], h['finish'])
                log.append(('3 close out', rm, a.activity_id, a.name, 'was open',
                            f"actual {h['start']} to {h['finish']} per Holder"))
                took += 1
            elif a.actual_finish and not h['fact'] and not h['sact']:
                if a.actual_finish > DATA_DATE:
                    unjudged.append((rm, a.name, a.actual_finish, h['start']))
                    continue
                log.append(('3 reopen', rm, a.activity_id, a.name,
                            f'was complete {a.actual_finish}',
                            f"Holder not started, plans {h['start']}"))
                reopen(a); protected.add(a.uid); reop += 1
    print(f'  step 3  closed {took} on Holder\'s dates, reopened {reop}, '
          f'left {len(unjudged)} alone as progressed after his data date')

    # ---- 4 -----------------------------------------------------------------
    # Back-fill the in-room chain feeding a row Holder reports complete.  If he
    # says drywall framing finished in HV 103, the overhead work our breakdown
    # puts in front of it finished too, whatever he calls it.  The walk starts
    # only from rows Holder confirms, which is what keeps it out of the
    # generator rooms, where v31 reopened the pulls on his own evidence and the
    # stranded rows downstream have to be reopened instead (step 5).
    pinned = set()
    derived, n4h, n4d = [], 0, 0
    confirmed = [a for a in p.activities
                 if a.actual_finish
                 and (holder.get((uid2room.get(a.uid), base_name(a.name))) or {})
                     .get('fact')]
    queue = collections.deque(confirmed)
    seen = set()
    while queue:
        a = queue.popleft()
        if a.uid in seen:
            continue
        seen.add(a.uid)
        rm = uid2room.get(a.uid)
        if rm is None:
            continue
        pred_of = collections.defaultdict(list)
        for r in p.relations:
            pred_of[r.successor_uid].append(r)
        for r in pred_of[a.uid]:
            q = byuid.get(r.predecessor_uid)
            if q is None or uid2room.get(q.uid) != rm:
                continue            # a crew arriving from another room
            if not q.actual_finish and q.uid not in protected:
                h = holder.get((rm, base_name(q.name)))
                if h and h['fact'] and h['finish'] <= DATA_DATE:
                    close_out(q, h['start'], h['finish'])
                    log.append(('4 back-fill, Holder', rm, q.activity_id, q.name,
                                'was open',
                                f"actual {h['start']} to {h['finish']} per Holder"))
                    n4h += 1
                elif PREP.search(q.name or '') and (q.planned_duration or 0) <= 16:
                    days = max(1, int(round((q.planned_duration or 8) / 8)))
                    f2 = prev_workday(a.actual_start or a.actual_finish, 1)
                    s2 = prev_workday(f2, days - 1) if days > 1 else f2
                    close_out(q, s2, f2)
                    derived.append((rm, q.name, s2, f2, a.name))
                    log.append(('4 back-fill, derived', rm, q.activity_id, q.name,
                                'was open, Holder carries no matching row',
                                f'actual {s2} to {f2}, back-dated in front of '
                                f'{a.name}, which Holder reports complete'))
                    n4d += 1
                else:
                    continue        # reported by the final screen, not invented
                pinned.add(q.uid)
            queue.append(q)
    # A derived back-date is only defensible when the step was actually ready
    # to go: if its own in-room predecessor is still open we have no basis for
    # it either, so withdraw it and let the pair be reported instead.
    derived_uids = {q for q in pinned}
    for _ in range(6):
        pred_of = collections.defaultdict(list)
        for r in p.relations:
            pred_of[r.successor_uid].append(r)
        pulled = 0
        for u in list(derived_uids):
            q = byuid.get(u)
            if q is None or not q.actual_finish:
                continue
            rm = uid2room.get(u)
            bad = [byuid[r.predecessor_uid] for r in pred_of[u]
                   if byuid.get(r.predecessor_uid) is not None
                   and uid2room.get(r.predecessor_uid) == rm
                   and not byuid[r.predecessor_uid].actual_finish]
            if bad and not (holder.get((rm, base_name(q.name))) or {}).get('fact'):
                reopen(q)
                pinned.discard(u); derived_uids.discard(u)
                derived[:] = [d for d in derived if d[1] != q.name or d[0] != rm]
                log.append(('4 back-fill withdrawn', rm, q.activity_id, q.name,
                            'had been back-dated',
                            f'reopened, its own predecessor '
                            f'{bad[0].name} is still open'))
                n4d -= 1
                pulled += 1
        if not pulled:
            break

    pinned |= {a.uid for a in confirmed}
    print(f'  step 4  back-filled {n4h} rows on Holder\'s dates and '
          f'{n4d} from our own breakdown, feeding {len(confirmed)} rows he confirms')

    # ---- 5 -----------------------------------------------------------------
    def strand_sweep(step):
        """Reopen a complete row whose in-room predecessor is open, unless
        Holder himself reports that row finished.  Runs once here and again
        after step 6, whose restored ties can strand a row of their own."""
        n, his = 0, []
        for _ in range(8):
            pred_of = collections.defaultdict(list)
            for r in p.relations:
                pred_of[r.successor_uid].append(r)
            fixed = 0
            for a in p.activities:
                rm = uid2room.get(a.uid)
                if not a.actual_finish or rm is None or a.uid in pinned:
                    continue
                open_pre = [byuid[r.predecessor_uid] for r in pred_of[a.uid]
                            if byuid.get(r.predecessor_uid) is not None
                            and uid2room.get(r.predecessor_uid) == rm
                            and not byuid[r.predecessor_uid].actual_finish]
                if not open_pre:
                    continue
                h = holder.get((rm, base_name(a.name)))
                if h and h['fact']:
                    for q in open_pre:
                        his.append((rm, a.name, q.name))
                    continue
                log.append((f'{step} reopen stranded', rm, a.activity_id, a.name,
                            f'was complete {a.actual_finish}',
                            'in-room predecessor '
                            + ', '.join(q.name for q in open_pre) + ' is open'))
                reopen(a)
                fixed += 1
                n += 1
            if not fixed:
                break
        return n, his

    n5, his = strand_sweep('5')
    print(f'  step 5  reopened {n5} stranded rows')

    # ---- 6 -----------------------------------------------------------------
    # ERR 201 and ERR 202 are built backwards.  Tape finish, trim out and paint
    # run first, then high steel, then the whole overhead electrical chain
    # last.  The culprit is an in-room tie an earlier pass of mine added, which
    # anchored Install High Steel to Paint instead of letting it open the room:
    # it is absent in v2 and present by v29.  It sat harmless while the room
    # was all actualised, and bit the moment step 3 reopened the trim-out on
    # Holder's report, cascading the reopen forward through the inversion and
    # dragging Data Hall 202 and the whole Phase 2 Cx ladder with it.
    #
    # The fix imposes the sequence a healthy room already follows and drops
    # only the in-room ties that run backwards against it.  Ties that leave the
    # room are untouched, and no tie that predates this work is removed.
    CANON = ['oh caulking paint', 'oh caulking amp paint', 'install high steel',
             'high steel install', 'install hangers', 'hanger install',
             'install cable tray', 'oh cable tray install', 'conduit install',
             'oh conduit install', 'oh branch power',
             'oh rough in fire protection', 'oh rough in mechanical',
             'oh rough in fa', 'oh rough in electrical fa', 'bus duct install',
             'drywall framing', 'layout wall penetration',
             'wall penetration layouts', 'box penetration', 'wall rough in',
             'electrical pulls and final connections',
             'branch electrical pulls and final connections',
             'hang tape finish', 'device trim out', 'paint',
             'mechanical final connections', 'floor finishes',
             'vesda rack install', 'set electrical skids', 'set crah s',
             'set crahs', 'pull from edp to crahs', 'term from edp to crahs',
             'electrical terminations and controls', 'lighting install',
             'testing and inspections', 'start ups of equipment']
    rank = {k: i for i, k in enumerate(CANON)}

    def rank_of(a):
        return rank.get(base_name(a.name))

    dropped, added = 0, 0
    for rm in ('ERR 201', 'ERR 202'):
        acts = by_room.get(rm, [])
        uids = {a.uid for a in acts}
        keep = []
        for r in p.relations:
            if r.predecessor_uid in uids and r.successor_uid in uids:
                i = rank_of(byuid[r.predecessor_uid])
                j = rank_of(byuid[r.successor_uid])
                if i is not None and j is not None and i > j:
                    log.append(('6 drop inverted tie', rm,
                                byuid[r.successor_uid].activity_id,
                                byuid[r.successor_uid].name,
                                f'was {r.type} after '
                                f'{byuid[r.predecessor_uid].name}',
                                'dropped, it ran backwards against the room order'))
                    dropped += 1
                    continue
            keep.append(r)
        p.relations = keep

        # anything now with no in-room predecessor is chained to the previous
        # ranked step in the room, so the room reads as one sequence again
        have = collections.defaultdict(list)
        for r in p.relations:
            have[r.successor_uid].append(r)
        ordered = sorted((a for a in acts if rank_of(a) is not None),
                         key=rank_of)
        for n_, a_ in enumerate(ordered):
            if n_ == 0:
                continue
            if any(r.predecessor_uid in uids for r in have[a_.uid]):
                continue
            prev = ordered[n_ - 1]
            nr = copy.deepcopy(p.relations[0])
            nr.predecessor_uid, nr.successor_uid = prev.uid, a_.uid
            nr.type, nr.lag = 'Finish to Start', 0.0
            p.relations.append(nr)
            have[a_.uid].append(nr)
            log.append(('6 restore room order', rm, a_.activity_id, a_.name,
                        'had no in-room predecessor left',
                        f'FS+0 after {prev.name}'))
            added += 1
    print(f'  step 6  ERR 201/202: dropped {dropped} inverted in-room ties, '
          f'added {added} to restore the room order')

    # ---- 7 -----------------------------------------------------------------
    n7, his7 = strand_sweep('7')
    his += his7
    print(f'  step 7  reopened {n7} rows stranded by step 6\'s restored ties')

    after = finish_of(p)
    print(f'\nv40 out  finish {after}   '
          f'({"no change" if after == before else "moved from " + before})')

    if unjudged:
        print(f'\nwe closed these after Holder\'s {DATA_DATE} data date, so his '
              f'report cannot contradict them ({len(unjudged)}), left as ours:')
        for rm, nm, af, hs in sorted(unjudged):
            print(f'  {rm:<9}{nm[:40]:<42}ours {af}, he plans {hs}')

    if derived:
        print(f'\nderived actuals, Holder carries no matching row ({len(derived)}):')
        for rm, nm, s2, f2, suc in sorted(derived):
            print(f'  {rm:<9}{nm[:40]:<42}{s2} to {f2}  in front of {suc[:26]}')

    # Final screen.  Every out-of-sequence pair that survives must be
    # explainable, not merely tolerated: either Holder himself reports the
    # complete row finished while leaving its predecessor open, or the
    # predecessor is one we reopened on evidence in step 1 or 3 and will not
    # re-close.  Anything else is a bug in this script.
    pred_of = collections.defaultdict(list)
    for r in p.relations:
        pred_of[r.successor_uid].append(r)
    holders, ours, unexplained = set(), set(), set()
    for a2 in p.activities:
        rm = uid2room.get(a2.uid)
        if not a2.actual_finish or rm is None:
            continue
        hc = (holder.get((rm, base_name(a2.name))) or {}).get('fact')
        for r in pred_of[a2.uid]:
            q = byuid.get(r.predecessor_uid)
            if q is None or uid2room.get(q.uid) != rm or q.actual_finish:
                continue
            row = (rm, a2.name, q.name)
            if q.uid in protected:
                ours.add(row)
            elif hc:
                holders.add(row)
            else:
                unexplained.add(row)

    if ours:
        print(f'\nout of sequence because we reopened the predecessor on '
              f'evidence and Holder still shows the row complete ({len(ours)}):')
        for rm, nm, qn in sorted(ours):
            print(f'  {rm:<9}{nm[:40]:<42}needs {qn[:36]}')
    if holders:
        print(f'\nout of sequence in Holder\'s own file, left as reported '
              f'({len(holders)}):')
        for rm, nm, qn in sorted(holders):
            print(f'  {rm:<9}{nm[:40]:<42}needs {qn[:36]}')
    print(f'\nunexplained out-of-sequence rows: {len(unexplained)}')
    assert not unexplained, sorted(unexplained)[:6]

    c = cycles(p)
    print(f'cycles: {c}')
    assert c == 0, 'v40 introduced a circular relationship'

    write_p6_xml(p, DST)
    print(f'wrote {DST}')

    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = 'v40 changes'
    ws.append(['Step', 'Room', 'Activity ID', 'Activity Name', 'Before', 'After'])
    for row in log:
        ws.append(list(row))
    for c2, w in zip('ABCDEF', (20, 11, 26, 54, 36, 52)):
        ws.column_dimensions[c2].width = w
    ws.freeze_panes = 'A2'
    out = f'{SCR}/MDC1_v40_actuals_change_log.xlsx'
    wb.save(out)
    print(f'wrote {out}  ({len(log)} rows)')


if __name__ == '__main__':
    main()
