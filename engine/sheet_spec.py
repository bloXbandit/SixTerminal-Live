# -*- coding: utf-8 -*-
"""
sheet_spec.py — the tracker's local changes, kept somewhere they survive.

WHY NOT JUST EDIT THE FILE
  The obvious reading of "let me tweak the workbook before it exports" is to
  open the .xlsx and change it. That works exactly once. The next export is
  built from the schedule again — which is the whole point of a generated
  tracker — and every hand change is gone. Worse, it is gone silently: the
  file looks right until somebody notices last month's column is missing.

  So what is stored is not the file but the CHANGE: hide the Crew column,
  call Sub Area "Line-up", make Phase 2 amber, add a sheet of tie-in dates.
  The generator reads that on every build, so a tweak survives a schedule
  revision, a re-export and a restart — and it goes on applying to rows that
  did not exist when it was asked for.

WHAT IT DELIBERATELY CANNOT DO
  It cannot reach into a formula or move a column. Both are possible and both
  are how a workbook quietly stops adding up: a moved column shifts every
  reference behind it, and a hand-edited formula is a formula nobody
  regenerates correctly. Hiding a column leaves it computing and simply out of
  the way, which is what "I don't need to see that" actually means.

WHERE IT LIVES
  On the project brain, keyed like everything else on P6's own project id, so
  it rides to R2 in the same manifest and comes back with the job rather than
  with the file.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# A colour a person can name, so the agent never has to invent a hex code and
# the user never has to read one back.
COLOURS = {
    'blue': 'DDEBF7', 'green': 'E2EFDA', 'yellow': 'FFF2CC', 'amber': 'FFF2CC',
    'orange': 'FCE4D6', 'purple': 'E4DFEC', 'teal': 'DAEEF3', 'red': 'FFC7CE',
    'grey': 'F2F2F2', 'gray': 'F2F2F2', 'pink': 'FCE4EC', 'none': '',
}


def resolve_colour(v: str) -> str:
    """A name, or a hex code, or nothing. Never a guess."""
    s = str(v or '').strip().lstrip('#')
    if not s:
        return ''
    low = s.lower()
    if low in COLOURS:
        return COLOURS[low]
    if len(s) == 6 and all(c in '0123456789abcdefABCDEF' for c in s):
        return s.upper()
    return ''


@dataclass
class ExtraSheet:
    """A sheet the generator knows nothing about, carried whole."""
    name: str
    headers: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    note: str = ''

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SheetSpec:
    """Everything asked for about the tracker that the schedule cannot say."""
    hidden: List[str] = field(default_factory=list)        # Update headers
    renamed: Dict[str, str] = field(default_factory=dict)  # header -> shown as
    phase_colours: Dict[str, str] = field(default_factory=dict)
    sheets: List[ExtraSheet] = field(default_factory=list)

    # ── reading ──────────────────────────────────────────────────────────────
    def is_empty(self) -> bool:
        return not (self.hidden or self.renamed or self.phase_colours or self.sheets)

    def shown(self, header: str) -> str:
        return self.renamed.get(header, header)

    def is_hidden(self, header: str) -> bool:
        return header in self.hidden

    def sheet(self, name: str) -> Optional[ExtraSheet]:
        low = (name or '').strip().lower()
        return next((s for s in self.sheets if s.name.strip().lower() == low), None)

    # ── the operations the agent has ─────────────────────────────────────────
    def hide(self, header: str) -> str:
        if header in self.hidden:
            return f'"{header}" was already hidden'
        self.hidden.append(header)
        return f'"{header}" will be hidden — it still calculates, it is just out of the way'

    def show(self, header: str) -> str:
        if header not in self.hidden:
            return f'"{header}" was not hidden'
        self.hidden.remove(header)
        return f'"{header}" is shown again'

    def rename(self, header: str, to: str) -> str:
        to = (to or '').strip()
        if not to:
            self.renamed.pop(header, None)
            return f'"{header}" goes back to its own name'
        self.renamed[header] = to
        return f'"{header}" will read "{to}"'

    def colour_phase(self, phase: str, colour: str) -> str:
        hexv = resolve_colour(colour)
        if not hexv:
            if str(colour).strip().lower() in ('none', 'clear', ''):
                self.phase_colours.pop(phase, None)
                return f'{phase} goes back to its default colour'
            return (f'"{colour}" is not a colour I can use — name one of '
                    f'{", ".join(sorted(k for k in COLOURS if k != "gray"))}, '
                    f'or give a hex code like FFE599')
        self.phase_colours[phase] = hexv
        return f'{phase} will be {colour}'

    def add_sheet(self, name: str, headers=None, rows=None, note: str = '') -> str:
        name = (name or '').strip()
        if not name:
            return 'a sheet needs a name'
        # The generator owns these; a spec sheet of the same name would be
        # built twice and Excel refuses to open a workbook with two of one name.
        if name.lower() in {s.lower() for s in RESERVED_SHEETS}:
            return (f'"{name}" is one of the generated tabs — pick another name, '
                    f'or ask for a change to that tab instead')
        existing = self.sheet(name)
        if existing is not None:
            existing.headers = list(headers or existing.headers)
            existing.rows = [list(r) for r in (rows or existing.rows)]
            existing.note = note or existing.note
            return f'"{name}" updated — {len(existing.rows)} rows'
        self.sheets.append(ExtraSheet(name=name, headers=list(headers or []),
                                      rows=[list(r) for r in (rows or [])],
                                      note=note))
        return f'"{name}" added — {len(rows or [])} rows'

    def drop_sheet(self, name: str) -> str:
        s = self.sheet(name)
        if s is None:
            return f'there is no sheet called "{name}"'
        self.sheets.remove(s)
        return f'"{name}" removed'

    def clear(self) -> str:
        n = len(self.hidden) + len(self.renamed) + len(self.phase_colours) + len(self.sheets)
        self.hidden, self.renamed, self.phase_colours, self.sheets = [], {}, {}, []
        return f'{n} customisation(s) cleared — the tracker is back to standard'

    # ── what it reads back as ────────────────────────────────────────────────
    def describe(self) -> str:
        if self.is_empty():
            return 'The tracker is standard — nothing has been changed about it.'
        out = []
        if self.hidden:
            out.append('Hidden columns: ' + ', '.join(self.hidden))
        if self.renamed:
            out.append('Renamed: ' + ', '.join(f'{k} → {v}' for k, v in self.renamed.items()))
        if self.phase_colours:
            inv = {v: k for k, v in COLOURS.items() if v}
            out.append('Phase colours: ' + ', '.join(
                f'{k} = {inv.get(v, "#" + v)}' for k, v in self.phase_colours.items()))
        for s in self.sheets:
            out.append(f'Sheet "{s.name}": {len(s.rows)} rows, '
                       f'{len(s.headers)} columns'
                       + (f' — {s.note}' if s.note else ''))
        return '\n'.join(out)

    # ── persistence ──────────────────────────────────────────────────────────
    def to_json(self) -> Dict[str, Any]:
        return {'hidden': list(self.hidden), 'renamed': dict(self.renamed),
                'phase_colours': dict(self.phase_colours),
                'sheets': [s.to_json() for s in self.sheets]}

    @classmethod
    def from_json(cls, data: Any) -> 'SheetSpec':
        """Never raises. A spec that cannot be read is no reason to lose an
        export — the tracker is still correct without its customisation."""
        if not isinstance(data, dict):
            return cls()
        try:
            return cls(
                hidden=[str(h) for h in (data.get('hidden') or [])],
                renamed={str(k): str(v) for k, v in (data.get('renamed') or {}).items()},
                phase_colours={str(k): str(v) for k, v in
                               (data.get('phase_colours') or {}).items()},
                sheets=[ExtraSheet(name=str(s.get('name') or ''),
                                   headers=list(s.get('headers') or []),
                                   rows=[list(r) for r in (s.get('rows') or [])],
                                   note=str(s.get('note') or ''))
                        for s in (data.get('sheets') or [])
                        if isinstance(s, dict) and s.get('name')],
            )
        except Exception:
            return cls()


# The tabs the generator builds. A spec sheet may not take one of these names.
RESERVED_SHEETS = ('Start Here', 'Update', 'Dashboard', 'Areas',
                   'Critical Path', 'Notes', 'Data')
