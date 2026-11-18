"""Draw the labour curves as P6 usage profiles and put them in as images.

Excel's own chart engine kept fighting this. Column A holds dates, so Excel
built a date axis spanning about 770 days and scattered the 107 weekly bars
across it one day wide apiece -- hairlines, with a wall of date boxes under
the plot. Converting the axis in the XML fixed the bars but made the
category labels render as date serials, and openpyxl cannot round-trip the
workbook at all: loading the user's Excel-saved file and saving it again
writes every chart out with zero series.

So the charts are drawn here instead, to match P6's profile exactly, and
embedded as images. The numbers come straight out of the user's own v41 --
nothing is recomputed.

P6's look, for the record: bars touch with no gap, one slot per week; blue
is actual, green is remaining, red is the baseline, drawn as its own bar
beside the actual/remaining stack rather than behind it; the x-axis carries
month initials with year bands beneath; the legend sits inside the plot at
the top left under the heading "Units".
"""
import datetime as dt
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
import openpyxl                                          # noqa: E402
from matplotlib.patches import Rectangle                 # noqa: E402

SRC, OUTDIR = sys.argv[1], sys.argv[2]
P6_BLUE, P6_GREEN, P6_RED = "#0000FF", "#00C000", "#FF0000"

wb = openpyxl.load_workbook(SRC, data_only=True)
ws = wb["Labor Curve"]
wk, bl, act, rem, cb, cp = [], [], [], [], [], []
r = 7
while True:
    d = ws.cell(row=r, column=1).value
    if d is None or not hasattr(d, "year"):
        break
    wk.append(d.date() if hasattr(d, "date") else d)
    bl.append(ws.cell(row=r, column=3).value or 0)
    act.append(ws.cell(row=r, column=5).value or 0)
    rem.append(ws.cell(row=r, column=7).value or 0)
    cb.append(ws.cell(row=r, column=9).value or 0)
    cp.append(ws.cell(row=r, column=10).value or 0)
    r += 1
n = len(wk)
print(f"read {n} weeks from {wk[0]} to {wk[-1]}")
print(f"   BL {sum(bl):,.0f} h   Actual {sum(act):,.0f} h   "
      f"Remaining {sum(rem):,.0f} h")

x = list(range(n))


def frame(ax, ymax):
    """P6's grid: a faint vertical rule per period, horizontal dotted."""
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(0, ymax * 1.08)
    ax.set_ylabel("Units", fontsize=9)
    for i in range(0, n, 1):
        ax.axvline(i - 0.5, color="#D0D0D0", lw=0.4, zorder=0)
    ax.grid(axis="y", color="#808080", lw=0.5, ls=(0, (2, 3)), zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", labelsize=8)
    # month initials, with year bands beneath -- P6's two-tier axis
    ticks, labels = [], []
    yr = {}
    for i, d in enumerate(wk):
        if i == 0 or d.month != wk[i - 1].month:
            ticks.append(i)
            labels.append(d.strftime("%b")[0] if d.month != 1 else "J")
        yr.setdefault(d.year, []).append(i)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=6.5)
    ax.tick_params(axis="x", length=2, pad=2)
    for y, idx in yr.items():
        mid = (min(idx) + max(idx)) / 2
        ax.annotate(str(y), xy=(mid, 0), xytext=(0, -22),
                    textcoords="offset points", ha="center", fontsize=8.5,
                    annotation_clip=False)
        ax.annotate("", xy=(min(idx) - 0.5, 0), xytext=(max(idx) + 0.5, 0),
                    xycoords=("data", "axes fraction"),
                    textcoords=("data", "axes fraction"),
                    arrowprops=dict(arrowstyle="-", color="#404040",
                                    lw=0.8, shrinkA=0, shrinkB=0))


def legend(ax, items):
    hs = [Rectangle((0, 0), 1, 1, fc=c, ec="black", lw=0.4)
          for _, c in items]
    lg = ax.legend(hs, [t for t, _ in items], title="Units", loc="upper left",
                   fontsize=8.5, title_fontsize=9, framealpha=1,
                   edgecolor="#808080", borderpad=0.7, labelspacing=0.5)
    lg.get_frame().set_linewidth(0.6)


def save(fig, name):
    fig.savefig(f"{OUTDIR}/{name}", dpi=170, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"   wrote {name}")


# ---- 1. baseline only -------------------------------------------------
fig, ax = plt.subplots(figsize=(19, 5.0))
ax.bar(x, bl, width=1.0, color=P6_RED, ec="black", lw=0.15, zorder=3)
frame(ax, max(bl))
legend(ax, [("BL Project Labor", P6_RED)])
ax.set_title("BASELINE - 02/04 Contract     (533,400 h, peak 279 crew)",
             fontsize=11, fontweight="bold", pad=10)
save(fig, "curve_1_baseline.png")

# ---- 2. projected: actual then remaining ------------------------------
fig, ax = plt.subplots(figsize=(19, 5.0))
ax.bar(x, act, width=1.0, color=P6_BLUE, ec="black", lw=0.15, zorder=3)
ax.bar(x, rem, width=1.0, bottom=act, color=P6_GREEN, ec="black", lw=0.15,
       zorder=3)
frame(ax, max(a + b for a, b in zip(act, rem)))
legend(ax, [("Actual Labor", P6_BLUE), ("Remaining Labor", P6_GREEN)])
ax.set_title("PROJECTED - 10/07 Proposed     (605,707 h, peak 466 crew)",
             fontsize=11, fontweight="bold", pad=10)
save(fig, "curve_2_projected.png")

# ---- 3. combined, P6 style: BL beside the actual/remaining stack -------
fig, ax = plt.subplots(figsize=(19, 5.6))
w = 0.46
ax.bar([i - w / 2 for i in x], bl, width=w, color=P6_RED, ec="black",
       lw=0.12, zorder=3)
ax.bar([i + w / 2 for i in x], act, width=w, color=P6_BLUE, ec="black",
       lw=0.12, zorder=3)
ax.bar([i + w / 2 for i in x], rem, width=w, bottom=act, color=P6_GREEN,
       ec="black", lw=0.12, zorder=3)
frame(ax, max(max(bl), max(a + b for a, b in zip(act, rem))))
legend(ax, [("Actual Labor", P6_BLUE), ("Remaining Labor", P6_GREEN),
            ("BL Project Labor", P6_RED)])
ax2 = ax.twinx()
ax2.plot(x, cb, color="#A50021", lw=2.0, zorder=5, label="Cum Baseline")
ax2.plot(x, cp, color="#1F4E79", lw=2.0, zorder=5, label="Cum Projected")
ax2.set_ylabel("Cumulative hours", fontsize=9)
ax2.set_ylim(0, max(max(cb), max(cp)) * 1.08)
ax2.tick_params(labelsize=8)
ax2.spines["top"].set_visible(False)
ax2.legend(loc="upper right", fontsize=8.5, framealpha=1,
           edgecolor="#808080")
ax.set_title("COMBINED - Baseline vs Projected, with labour curve",
             fontsize=11, fontweight="bold", pad=10)
save(fig, "curve_3_combined.png")

# ---- 4. trade stacking: rooms open per month --------------------------
# openpyxl breaks every chart on round-trip, so this one is redrawn as an
# image too rather than left to be written out empty.
ts = wb["Trade Stacking"]
months, counts = [], []
r = 11
while True:
    m = ts.cell(row=r, column=1).value
    c = ts.cell(row=r, column=2).value
    if m is None or not isinstance(c, (int, float)):
        break
    months.append(str(m).replace(" 20", " '"))
    counts.append(c)
    r += 1
if months:
    fig, ax = plt.subplots(figsize=(11, 4.2))
    cols = ["#C00000" if v >= 20 else "#E8A33D" if v >= 12 else "#9DC3E6"
            for v in counts]
    ax.bar(range(len(counts)), counts, width=1.0, color=cols, ec="black",
           lw=0.4, zorder=3)
    for i, v in enumerate(counts):
        if v:
            ax.annotate(str(v), (i, v), textcoords="offset points",
                        xytext=(0, 3), ha="center", fontsize=8,
                        fontweight="bold")
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels([m[:3] + " " + m.split()[-1][-2:] for m in months],
                       rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Electrical rooms open", fontsize=9)
    ax.set_ylim(0, 26)
    ax.grid(axis="y", color="#B0B0B0", lw=0.5, ls=(0, (2, 3)), zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_title("Electrical rooms open at the same time  (24 rooms total, "
                 "three datacenters)", fontsize=10.5, fontweight="bold",
                 pad=10)
    save(fig, "stack_rooms.png")
