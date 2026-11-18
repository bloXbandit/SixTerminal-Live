"""Make the labour charts read as a P6 usage profile, by editing the xlsx XML.

openpyxl cannot round-trip this workbook: loading the user's Excel-saved
file and saving it again writes every chart out with zero series. So the
file is patched as a zip of XML and everything that is not a chart is
copied through byte for byte, which also preserves the user's own edits.

The actual fault was not the bar width setting. Column A holds dates, so
Excel built the x-axis as a c:dateAx with baseTimeUnit="days" -- a
continuous daily timeline about 770 days long, with the 107 weekly bars
scattered across it one day wide apiece. That is why the bars came out as
hairlines and why the axis filled up with date boxes.

P6's profile uses a CATEGORY axis: one slot per period, bars filling the
slot, labels every so often. So each c:dateAx becomes a c:catAx, the
time-scale elements that only belong on a date axis come out, and
tickLblSkip puts a label on every 13th week instead of all 107.
"""
import re
import shutil
import sys
import zipfile

SRC, OUT = sys.argv[1], sys.argv[2]
LABOR_CHARTS = {"chart2.xml", "chart3.xml", "chart4.xml"}
WIDE_CX, WIDE_CY = 19800000, 4320000        # 55cm x 12cm, in EMU
LABEL_EVERY = 13                            # a label per quarter


def to_cat_axis(x):
    """c:dateAx -> c:catAx, with the schema's child order respected."""
    n = 0

    def fix(m):
        nonlocal n
        body = m.group(1)
        # these three are date-axis only and are invalid on a catAx
        body = re.sub(r"<c:baseTimeUnit[^/]*/>", "", body)
        body = re.sub(r"<c:majorUnit[^/]*/>", "", body)
        body = re.sub(r"<c:minorUnit[^/]*/>", "", body)
        # a catAx numFmt of dd-mmm-yy is harmless but the General default
        # reads better once the labels are skipped
        body = body.replace('formatCode="dd\\-mmm\\-yy"', 'formatCode="General"')
        extra = (f'<c:tickLblSkip val="{LABEL_EVERY}"/>'
                 f'<c:tickMarkSkip val="{LABEL_EVERY}"/>'
                 f'<c:noMultiLvlLbl val="0"/>')
        if "<c:lblOffset" in body:           # tickLblSkip follows lblOffset
            body = re.sub(r"(<c:lblOffset[^/]*/>)", r"\1" + extra, body)
        else:
            body += extra
        n += 1
        return f"<c:catAx>{body}</c:catAx>"

    x = re.sub(r"<c:dateAx>(.*?)</c:dateAx>", fix, x, flags=re.S)
    return x, n


shutil.copy(SRC, OUT + ".tmp")
zin = zipfile.ZipFile(SRC)
zout = zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED)
report = []
for item in zin.infolist():
    data = zin.read(item.filename)
    base = item.filename.split("/")[-1]
    if item.filename.startswith("xl/charts/") and base in LABOR_CHARTS:
        x = data.decode("utf8")
        x, n = to_cat_axis(x)
        # P6 bars touch; no gap between periods and none between series
        x = re.sub(r'<c:gapWidth val="\d+"/>', '<c:gapWidth val="0"/>', x)
        if "<c:overlap" not in x and "stacked" not in x:
            x = x.replace('<c:gapWidth val="0"/>',
                          '<c:gapWidth val="0"/><c:overlap val="0"/>')
        data = x.encode("utf8")
        report.append(f"   {base}: {n} date axis -> category axis, "
                      f"label every {LABEL_EVERY} weeks, gapWidth 0")
    elif item.filename == "xl/drawings/drawing2.xml":
        x = data.decode("utf8")
        before = re.findall(r'<xdr:ext cx="(\d+)" cy="(\d+)"/>', x)
        x = re.sub(r'<xdr:ext cx="\d+" cy="\d+"/>',
                   f'<xdr:ext cx="{WIDE_CX}" cy="{WIDE_CY}"/>', x)
        data = x.encode("utf8")
        report.append(f"   drawing2.xml: {len(before)} charts resized "
                      f"{int(before[0][0]) / 360000:.0f}cm -> "
                      f"{WIDE_CX / 360000:.0f}cm wide")
    zout.writestr(item, data)
zout.close()
zin.close()
print("patched:")
for line in report:
    print(line)
print(f"\nwrote {OUT}")
