"""Score the 10/07 text layer against a hand-read transcription of page 1.

The words file carries text, but the text is mangled -- "01-Deo25A",
"2TMar26A", "MajoriLong". That is OCR, not an embedded P6 text layer, so
before anything is built on it the question is how often it is WRONG, and
specifically how often it is wrong about a DATE. A name that reads "Ovmer"
costs nothing. A date that reads 2T-Mar-26 instead of 27-Mar-26 silently
poisons every span in the acceleration analysis.

Ground truth is the transcription in mdc3_p1_1007.py, read by eye off a 14x
render of the same page and spot-checked a second time at higher zoom for
the three Substantial Completion rows.
"""
import collections
import datetime as dt
import gzip
import json
import re
import sys

SCR = ("/tmp/claude-0/-home-user-SixTerminal-Live/"
       "bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad")
sys.path.insert(0, f"{SCR}/work")
import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):       # it prints when imported
    from mdc3_p1_1007 import P1, pd                   # noqa: E402

WORDS = sys.argv[1]
pages = json.load(gzip.open(WORDS, "rt"))
pg = pages[0]

# bands measured off this layout: x is the ROW, y the COLUMN (y desc = L->R)
BAND_ID = (715, 755)
BAND_NAME = (625, 712)
BAND_DUR = (505, 528)
BAND_START = (483, 504)
BAND_FIN = (457, 482)
AID = re.compile(r"^MDC3?\.[A-Z0-9][A-Z0-9.]*\d$")
MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
# OCR confusions seen in this file, applied ONLY to date-shaped tokens
FIX = {"Deo": "Dec", "DeC": "Dec", "Jub": "Jul", "Mai": "Mar", "Nowv": "Nov"}
DATE = re.compile(r"^(\d{1,2})-?([A-Za-z]{3})-?(\d{2})\s*([A*]?)$")


def parse_date(t):
    t = t.strip()
    m = DATE.match(t)
    if not m:
        return None, "", t
    mon = FIX.get(m.group(2).title(), m.group(2).title())
    if mon not in MON:
        return None, "", t
    try:
        return (dt.date(2000 + int(m.group(3)), MON[mon], int(m.group(1))),
                m.group(4), t)
    except ValueError:
        return None, "", t


# ---- group words into rows by x -------------------------------------
by = collections.defaultdict(list)
for w in pg:
    by[round(w[0] * 2) / 2].append(w)
ks = sorted(by)
rows, cur = [], []
for k in ks:
    if cur and k - cur[-1] <= 1.0:
        cur.append(k)
    else:
        if cur:
            rows.append(cur)
        cur = [k]
if cur:
    rows.append(cur)

parsed = {}
for grp in rows:
    ws = [w for k in grp for w in by[k]]
    ws.sort(key=lambda w: -w[1])

    def band(lo, hi):
        return [w[4] for w in ws if lo <= w[1] <= hi]

    ids = [t for t in band(*BAND_ID) if AID.match(t)]
    if not ids:
        continue
    s = f = None
    sf = ff = ""
    for t in band(*BAND_START):
        d, fl, _ = parse_date(t)
        if d and s is None:
            s, sf = d, fl
    for t in band(*BAND_FIN):
        d, fl, _ = parse_date(t)
        if d and f is None:
            f, ff = d, fl
    for aid in ids:
        parsed.setdefault(aid, (s, f, " ".join(band(*BAND_NAME))))

print("=" * 96)
print("OCR TEXT LAYER vs HAND-READ TRANSCRIPTION -- page 1")
print("=" * 96)
print(f"rows the OCR yielded an activity id for : {len(parsed)}")
print(f"rows on the page by eye                 : {len(P1)}")

truth = {}
for aid, nm, s, f in P1:
    d, _ = pd(f or s)
    truth[aid] = d

missing = [a for a in truth if a not in parsed]
extra = [a for a in parsed if a not in truth]
print(f"ids the OCR MISSED entirely             : {len(missing)}"
      f"{'  ' + ', '.join(missing) if missing else ''}")
print(f"ids the OCR invented                    : {len(extra)}"
      f"{'  ' + ', '.join(extra) if extra else ''}")

ok = bad = nodate = 0
errs = []
for aid, want in truth.items():
    if aid not in parsed:
        continue
    s, f, _ = parsed[aid]
    got = f or s
    if got is None:
        nodate += 1
        errs.append((aid, want, None))
    elif got == want:
        ok += 1
    else:
        bad += 1
        errs.append((aid, want, got))

tot = ok + bad + nodate
print()
print(f"{'dates read correctly':<40}{ok:>4} of {tot}"
      f"   ({100 * ok / max(tot, 1):.0f}%)")
print(f"{'dates read WRONG':<40}{bad:>4}")
print(f"{'dates the OCR could not read at all':<40}{nodate:>4}")

if errs:
    print("\n" + "-" * 96)
    print("EVERY ROW THE OCR GOT WRONG OR DROPPED")
    print("-" * 96)
    print(f"{'activity id':<22}{'by eye':<14}{'OCR':<14}")
    for aid, want, got in sorted(errs, key=lambda x: x[0]):
        w = want.strftime("%d-%b-%y") if want else "-"
        g = got.strftime("%d-%b-%y") if got else "COULD NOT READ"
        print(f"{aid:<22}{w:<14}{g:<14}")

print("\n" + "=" * 96)
print("THE THREE CONTRACT DATES, SPECIFICALLY")
print("=" * 96)
for aid, lbl in [("MDC3.MIL.PH1.1070", "Substantial Completion (PH1)"),
                 ("MDC3.MIL.PH2.9070", "Substantial Completion (PH2)"),
                 ("MDC3.MIL.PH3.9070", "Substantial Completion (PH3)")]:
    want = truth.get(aid)
    got = parsed.get(aid, (None, None, ""))[1] or \
        parsed.get(aid, (None, None, ""))[0]
    mark = "match" if got == want else ">>> MISMATCH <<<"
    print(f"   {lbl:<32}eye {want}   OCR "
          f"{got if got else 'COULD NOT READ'}   {mark}")

print("\n" + "=" * 96)
print("WHAT THE GARBLING LOOKS LIKE (raw tokens in the date bands)")
print("=" * 96)
raw = []
for grp in rows:
    ws = [w for k in grp for w in by[k]]
    for w in ws:
        if BAND_FIN[0] <= w[1] <= BAND_START[1] and re.search(r"\d", w[4]):
            d, _, t = parse_date(w[4])
            if d is None and len(t) > 4:
                raw.append(t)
print(f"date-band tokens that will not parse as a date: {len(raw)}")
for t in raw[:25]:
    print(f"   {t!r}")
