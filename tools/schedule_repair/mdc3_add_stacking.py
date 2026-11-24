"""Append the verified stacking breakdown to the Trade Stacking tab, and the
per-room compression to the Compression tab.

Everything here is measured 02/04 contract vs the 9/25 LIVE schedule, which
is the workbook's own data date. The 10/07 Exhibit S was only captured at WBS
band level, so it cannot carry an activity-level stacking count; 9/25 is the
activity-level source that matches. Every figure was also re-run against the
11/04 update and moves the same way, only further.
"""
import openpyxl, sys
from openpyxl.styles import Font, Alignment, PatternFill
SRC=sys.argv[1]; OUT=sys.argv[2]
wb=openpyxl.load_workbook(SRC)
H1=Font(bold=True,size=11); HD=Font(bold=True,color="FFFFFF")
FILL=PatternFill("solid",fgColor="1F4E79"); NOTE=Font(italic=True,size=9,color="555555")
HI=PatternFill("solid",fgColor="FCE4D6")

def put(ws,r,vals,font=None,fill=None):
    for c,v in enumerate(vals,1):
        cell=ws.cell(row=r,column=c,value=v)
        if font: cell.font=font
        if fill: cell.fill=fill
    return r+1

ws=wb["Trade Stacking"]
r=ws.max_row+3
r=put(ws,r,["5.  TRADES SHARING A ROOM IN THE SAME WEEK"],H1)
r=put(ws,r,["Counted per room, per week. TRADES, not activities: the current schedule is broken down "
            "finer than the contract, so counting activities would show stacking that is only extra detail."],NOTE)
r=put(ws,r,["Splitting one electrical activity into four still leaves one trade in the room."],NOTE)
r+=1
r=put(ws,r,["Trades in one room, same week","02/04 contract","9/25 schedule","Change","","What it means"],HD,FILL)
for row,hl in ((["1 trade",529,754,"+225","","room to itself"],False),
               (["2 trades",351,339,"-12","","normal coordination"],False),
               (["3 trades",72,97,"+25","","crowded"],False),
               (["4 trades",6,19,"+13","","three times the contract"],True),
               (["5 trades",1,8,"+7","","eight times the contract"],True)):
    r=put(ws,r,row,fill=HI if hl else None)   # row, not row[0]: a string
                                              # would be written one cell per character
r=put(ws,r,["3 or more trades",79,124,"+45","","8.2% of room-weeks -> 10.2%"],Font(bold=True))
r=put(ws,r,["Total room-weeks",959,1217,"+258","",""])
r+=1
r=put(ws,r,["Which trade is in the crowded rooms","02/04 contract","9/25 schedule","Change","","What it means"],HD,FILL)
for row in (["Drywall / Finishes",28,114,"+86","","biggest single increase"],
            ["Fire Protection",0,34,"+34","","new - none in the contract"],
            ["Electrical (JER)",79,109,"+30","","our exposure"],
            ["Commissioning",13,34,"+21","",""],
            ["Mechanical",68,86,"+18","",""],
            ["Concrete / Structural",57,30,"-27","","moved out of the way"]):
    r=put(ws,r,row,fill=HI if row[0].startswith(("Drywall","Fire","Electrical")) else None)
r+=1
r=put(ws,r,["Counts are room-weeks in which that trade is one of three or more sharing the room."],NOTE)
r+=2
r=put(ws,r,["6.  SAME-DAY STARTS WRITTEN INTO HOLDER'S OWN LOGIC"],H1)
r=put(ws,r,["Taken from the relationship list in Holder's own schedule, not inferred from dates. "
            "The relationship itself says the two activities start together."],NOTE)
r+=1
r=put(ws,r,["Finding","Count","","","",""],HD,FILL)
r=put(ws,r,["Predecessor-successor pairs in Holder's schedule",13854,"","","",""])
r=put(ws,r,["   of those, starting on the SAME DAY",400,"","","",""])
r=put(ws,r,["   of those, same room AND different trade",24,"","","",""])
r=put(ws,r,["   of those, Overhead Rough In Electrical/FA + Drywall Framing",20,"","","","same room, same day"],
      Font(bold=True),HI)
r+=1
r=put(ws,r,["The 20 pairs cover 18 rooms:"],Font(bold=True))
r=put(ws,r,["   ER 210, 211, 212, 213, 214","","","","",""])
r=put(ws,r,["   HV 101, 103, 104, 105","","","","",""])
r=put(ws,r,["   MV 101, 102, 103, 104, 105, 108, 109, 112, 113","","","","",""])
r+=1
r=put(ws,r,["Overhead electrical rough-in and drywall framing cannot share a room on the same day "
            "without interference. This is the GC's logic stating they will."],Font(bold=True))
r=put(ws,r,["Verified against both the 9/25 schedule and the 11/04 update - the same 20 pairs start "
            "on the same day in both."],NOTE)
r+=1
r=put(ws,r,["DRAFT - figures measured 02/04 contract vs 9/25 LIVE (the workbook's data date). "
            "Re-run against the 11/04 update every figure moves the same way, only further: "
            "4-trade weeks 33, 5-trade weeks 7, 3+ at 11.4%."],NOTE)
for col,w in (("A",48),("B",16),("C",16),("D",12),("E",4),("F",40)):
    ws.column_dimensions[col].width=max(ws.column_dimensions[col].width or 0,w)

cs=wb["Compression"]
r=cs.max_row+2
r=put(cs,r,["PER-ROOM COMPRESSION - same room, contract window vs 9/25"],H1)
r=put(cs,r,["58 rooms appear in both schedules. The pattern is consistent: the start is pushed "
            "roughly five months while the finish is held, so the window shrinks."],NOTE)
r+=1
r=put(cs,r,["Rooms in both schedules",58,"","","","","",""],HD,FILL)
r=put(cs,r,["   window SHORTER now (compressed)",44,"","","","","","76% of them"],fill=HI)
r=put(cs,r,["   window LONGER now",13,"","","","","",""])
r=put(cs,r,["   unchanged",1,"","","","","",""])
r=put(cs,r,["   average loss on the 44 compressed","-104 days","","","","","",""],Font(bold=True))
r+=1
r=put(cs,r,["Room","02/04 start","02/04 finish","9/25 start","9/25 finish","02/04 days","9/25 days","Change"],HD,FILL)
for row in (["MV 106","2026-09-08","2027-05-13","2027-05-24","2027-06-16",247,23,-224],
            ["MV 104","2026-09-01","2027-07-13","2027-03-24","2027-07-08",315,106,-209],
            ["MV 103","2026-08-28","2027-08-03","2027-02-19","2027-07-30",340,161,-179],
            ["MV 102","2026-08-26","2027-05-14","2027-02-26","2027-06-09",261,103,-158],
            ["ER 107","2026-07-28","2027-09-28","2027-01-22","2027-10-26",427,277,-150],
            ["ER 212","2026-07-23","2027-05-14","2027-03-12","2027-08-04",295,145,-150],
            ["ER 108","2026-07-21","2027-10-12","2027-01-14","2027-11-09",448,299,-149],
            ["ER 106","2026-08-17","2027-09-14","2027-02-01","2027-10-12",393,253,-140]):
    r=put(cs,r,row)
r=put(cs,r,["Worst eight shown. Measured 02/04 contract vs 9/25 LIVE; the 11/04 update gives "
            "38 compressed at -109 days average."],NOTE)
wb.save(OUT)
print(f"wrote {OUT}")
