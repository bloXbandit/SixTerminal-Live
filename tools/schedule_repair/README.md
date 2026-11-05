# Schedule repair pipeline

Scripts used to repair and reconcile the P6 schedules for the Cloud HQ data
centre buildings (MDC-1, MDC-2, MDC-3). They are one-off analysis and build
scripts rather than library code, kept here because they encode decisions and
measurements that are expensive to rediscover.

Each reads a P6 XML export through `engine.xml_reader` and writes one through
`engine.xml_writer`. Paths at the top of each script point at a scratchpad
directory and need repointing to wherever the source files live.

## Order

| Script | Does |
| --- | --- |
| `holder_parse.py` | Parses the GC's (Holder) schedule export .xlsx into `holder_mdc3.json`: activities, relationships from both the Predecessors and Successors columns, and the actual-date flags. |
| `holder_vs_ours.py` | Coverage, logic, date and actuals comparison between our file and the GC's. |
| `holder_scope.py` | Sorts the GC's activities we do not carry into JER electrical scope / gates-us / not-ours. |
| `precast.py` | Precast erection and turnover analysis. |
| `mdc3_v22.py` | Applies a field update and ties the precast areas end to end. |
| `mdc3_v26.py` | Reconciles to the GC's export: actuals, three missing rooms, the equipment tag gate, and the GC's own logic. |
| `mdc3_catchup22.py` | Catch-up and two-week lookahead workbook for the field. |
| `mdc3_recon.py` | Reconciliation workbook: what changed, what to confirm with the GC, what is left. |

## Things that cost time to find out

**P6 XML block order.** Inside `<Project>` the order must be Calendar → WBS →
Activity → Relationship → ResourceAssignment, or the import is rejected.

**`PercentComplete` is a fraction** in the XML (`1` means 100%).
`PercentCompleteType` is `Physical`, so percent complete does not drive
scheduling — remaining duration does.

**Relationship lag is in hours** in the XML; divide by 8 for days. A lag in
P6 is counted in working days, so convert through the activity's work week,
not the calendar week.

**`Relation` fields** are `uid, predecessor_uid, successor_uid, type, lag`,
and `type` is the full phrase (`"Finish to Start"`), not a code.

**P6's XML import adds and updates relationships but never deletes** ones
absent from the file. Importing as a new project is the way to drop logic.

**`Start On or After` is the constraint for holding a date.** `Mandatory
Start` overrides logic in both directions and hides sequence problems.

**A UDF text value may not exceed 255 characters.** P6's XML importer rejects
the whole file with `Field "Text" in UDFValue may not contain more than 255
characters`, after it has already created activities, so a failed import
leaves a half-built project behind. The value is written as `<TextValue>`
inside `<UDF>`, and P6 counts the decoded characters. Activity `Name` is
capped at 120 and `Id` at 40. Check all three before writing a file, and
remember that appending a second note to an activity that already has one is
what pushes a comment over.

**When a note has to be shortened, keep the sentences that carry a flag.**
Trimming from the front dropped exactly the notes worth reading: the marker
on 194 tag activities saying the sign-off is the contractor's own, and the
warning on 3 of the 5 activities where the field update contradicts the GC.

**Float is computed from logic**, so an open end reads as unlimited float (no
successor) or collapses to the data date (no predecessor) rather than showing
real slack. Fix the open end before reading float.

**Guard relationship redundancy, not just cycles.** Importing the GC's logic
with only a cycle guard pushed redundancy from 15% to 33% (MDC-1 sits at 17%).
A tie that restates a path the network already implies adds no constraint and
hides the real driver; the long-range ones also pick up absurd inferred lags
(one read FS+208d, where the "lag" was really the intermediate chain).

**Order the candidates shortest-lag-first** when importing inferred logic.
Processed arbitrarily, a long-range tie lands first and then rejects the tight
tie that is the real logic — and a later redundancy cleanup deletes the right
one instead of the wrong one.

**Do not infer a relationship from dates alone.** Two activities whose dates
happen to abut are not necessarily tied; MDC-2's precast areas have zero ties
between them and the crane walk exists only in the dates.

**Verify a derived key against independent evidence before trusting it.**
Scope labels, room keys and area keys built from id prefixes have all been
wrong at least once: an area key that only recognised Gen/MV/HV/ER/DH/GL
reported no slab or precast rows in a file holding 279 of them, and a scope
label covering only the 8-activity `STR.UDG` branch reported underground as
complete when 195 activities were 4% done.

**Compare by name, not by id prefix,** when asking what the GC carries that we
do not. Our MV rooms are `MDC3.MV.106.*` and theirs are `MDC3.PH1.MV.*`, so an
id-prefix comparison reports identical work as a 100% gap.

**Python 3.11** cannot take a backslash inside the expression part of an
f-string, and cannot nest the same quote character inside one. Precompile
regexes outside the f-string.

**`itertools.groupby`**, not `collections.groupby`.
