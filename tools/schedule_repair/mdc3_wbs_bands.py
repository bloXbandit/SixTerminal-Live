"""Pull the WBS summary bands out of the 10/07 Exhibit S part-1 PDF.

The text on these pages is outlined, so nothing can be parsed -- the only way
to read them is by eye off a render. Reading 30 full pages that way is
expensive and most of each page is activity detail we cannot use anyway.

But a WBS summary band in P6's print is a grey-filled row, and the fill
survives as a vector rectangle even though the text does not. So the bands
can be FOUND programmatically and only those rows rendered. A WBS band
carries the rolled-up start and finish for its whole block, which is exactly
what a scope-level labour curve needs.

Pages are rotated 90 degrees: in page coordinates x is the ROW and y is the
COLUMN, with y descending left to right. A band is therefore a rectangle
that is tall in y (spans the table) and thin in x (one row).
"""
import sys

import pymupdf

SRC, OUT = sys.argv[1], sys.argv[2]
GREY = {(0.69, 0.69, 0.69), (0.745, 0.745, 0.745), (0.584, 0.584, 0.584),
        (0.302, 0.302, 0.302), (0.2, 0.2, 0.2)}
# The grey fill covers only the ID and Name columns (y about 546..761); the
# duration and date columns sit on white. So a band is detected on the fill
# but CLIPPED wider, to carry its rolled-up start and finish along with it.
CLIP_Y = (452, 772)           # ID .. Finish, what we want to read
FILL_DY_MIN = 140             # the fill must span ID+Name, not be a tick
ROW_MIN, ROW_MAX = 2.5, 9.0   # one printed row, in the x direction

doc = pymupdf.open(SRC)
# Parts of this Exhibit S have been re-saved by different tools, and they do
# not agree on page rotation: part 1 carries /Rotate 0, part 2 carries
# /Rotate 90 (macOS Quartz re-save). get_drawings() reports unrotated
# coordinates either way, so a clip built from them lands in the wrong place
# on a rotated page -- the date columns still render and the name column
# falls outside the crop, which looks like missing data rather than a bug.
# Clearing the rotation makes every part behave like part 1.
for pno in range(doc.page_count):
    if doc[pno].rotation:
        doc[pno].set_rotation(0)

bands = []
for pno in range(doc.page_count):
    seen = []
    for p in doc[pno].get_drawings():
        f = p.get("fill")
        if f is None:
            continue
        if tuple(round(c, 3) for c in f) not in GREY:
            continue
        r = p["rect"]
        if not (ROW_MIN <= r.x1 - r.x0 <= ROW_MAX):
            continue
        if r.y1 - r.y0 < FILL_DY_MIN:
            continue          # must span the ID+Name columns
        seen.append((round(r.x0, 1), round(r.x1, 1)))
    seen = sorted(set(seen))
    merged = []
    for x0, x1 in seen:       # one row may be filled more than once
        if merged and x0 - merged[-1][0] < 2.0:
            merged[-1] = (min(merged[-1][0], x0), max(merged[-1][1], x1))
        else:
            merged.append((x0, x1))
    for x0, x1 in merged:
        bands.append((pno, x0, x1))
    print(f"  page {pno + 1:>3}: {len(merged)} WBS bands")

print(f"\n{len(bands)} WBS summary bands across {doc.page_count} pages")

# ---- contact sheet: every band stacked, left columns only ------------
PAD = 1.2
STRIP_H = 11.0                 # printed height per band on the sheet
PER_PAGE = 60
out = pymupdf.open()
SHEET_W, SHEET_H = 760, 700
n = 0
for i in range(0, len(bands), PER_PAGE):
    chunk = bands[i:i + PER_PAGE]
    page = out.new_page(width=SHEET_W, height=SHEET_H)
    for j, (pno, x0, x1) in enumerate(chunk):
        src_clip = pymupdf.Rect(x0 - PAD, CLIP_Y[0], x1 + PAD, CLIP_Y[1])
        dst = pymupdf.Rect(4, 6 + j * STRIP_H, SHEET_W - 4,
                           6 + (j + 1) * STRIP_H - 1.2)
        page.show_pdf_page(dst, doc, pno, clip=src_clip, rotate=270)
        page.insert_text((SHEET_W - 30, 6 + (j + 1) * STRIP_H - 3.5),
                         f"p{pno + 1}", fontsize=5)
        n += 1
out.save(OUT)
print(f"{n} bands onto {out.page_count} sheets -> {OUT}")
