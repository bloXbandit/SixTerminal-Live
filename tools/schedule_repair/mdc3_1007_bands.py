"""The 10/07 Exhibit S WBS summary bands, pages 1-30, read off the render.

The text layer is outlined and the OCR of it is 23% accurate, so none of
this can be parsed. But a WBS band is a grey-filled rectangle, and the FILL
survives in the vector layer even though the text does not. So the bands are
located programmatically (mdc3_wbs_bands.py), cropped out of the page, and
stacked onto a contact sheet -- 88 bands off 30 pages onto 2 sheets, which
is a quantity that can be read by eye accurately.

That matters because a WBS band carries the rolled-up duration, start and
finish for its whole block. Hours come from our v32; a window comes from
here. Hours plus a window is a labour curve. It is coarser than activity
level and is labelled as such, but it is read off the contract print rather
than inferred, and it covers scope the activity ids never would.

Dates are as printed: a trailing "A" is an actual. Durations are P6's
At Completion Duration in days.
"""

# name, at-completion duration, start, finish, source page
BANDS = [
    ("Milestones", 578, "01-Dec-25 A", "17-Mar-28", 1),
    ("General Milestones", 578, "01-Dec-25 A", "17-Mar-28", 1),
    ("Milestones (Phase 1)", 100, "12-Aug-27", "06-Jan-28", 1),
    ("Milestones (Phase 2)", 100, "01-Oct-27", "25-Feb-28", 1),
    ("Milestones (Phase 3)", 90, "05-Nov-27", "17-Mar-28", 1),
    ("Preconstruction / Procurement", 517, "11-Apr-25 A", "29-Apr-27", 1),
    ("Design", 422, "11-Apr-25 A", "11-Dec-26", 1),
    ("Permits", 280, "05-Dec-25 A", "19-Jan-27", 1),
    ("Site Permit", 271, "05-Dec-25 A", "05-Jan-27", 1),
    ("Building Permit", 270, "19-Dec-25 A", "19-Jan-27", 2),
    ("FAA Permits", 232, "06-Jan-26 A", "03-Dec-26", 2),
    ("Trade Permits", 132, "06-Jul-26 A", "13-Jan-27", 2),
    ("Funding", 247, "20-Oct-25 A", "12-Oct-26", 2),
    ("VDC Coordination", 174, "13-Apr-26 A", "17-Dec-26", 2),
    ("VDC Underground", 45, "13-Apr-26 A", "15-Jun-26 A", 2),
    ("VDC Level 1 - South & CUP", 118, "13-Apr-26 A", "28-Sep-26", 2),
    ("VDC Level 1 - North", 118, "13-Apr-26 A", "28-Sep-26", 3),
    ("VDC Roof & Penthouse", 75, "22-Jun-26 A", "06-Oct-26", 3),
    ("VDC Level 2 - South", 115, "22-Jun-26 A", "03-Dec-26", 3),
    ("VDC Level 2 - North", 125, "22-Jun-26 A", "17-Dec-26", 3),
    ("Submittals/Shop Drawings", 302, "21-Oct-25 A", "05-Jan-27", 3),
    ("LLE Submittals", 261, "21-Oct-25 A", "02-Nov-26", 3),
    ("Construction Submittals", 189, "09-Feb-26 A", "03-Nov-26", 3),
    ("Shop Drawings", 66, "29-Sep-26", "05-Jan-27", 4),
    ("Safety", 1, "02-Apr-26 A", "02-Apr-26 A", 4),
    ("General Procurement", 245, "11-May-26 A", "29-Apr-27", 4),
    ("Rebar", 92, "19-May-26 A", "28-Sep-26", 4),
    ("Framing Material Lead Time", 64, "20-Jan-27", "19-Apr-27", 4),
    ("Fragnets (precon)", 42, "08-May-26 A", "08-Jul-26 A", 5),
    ("Site Development Permit Delay", 42, "08-May-26 A", "08-Jul-26 A", 5),
    ("TFO (Design / Permits / Funding / LLE)", 386, "07-Jan-26 A",
     "16-Jul-27", 5),
    ("TFO Design", 243, "07-Jan-26 A", "21-Dec-26", 5),
    ("TFO Owner Release", 80, "06-Oct-26", "02-Feb-27", 5),
    ("TFO LLE Equipment", 195, "07-Oct-26", "16-Jul-27", 5),
    ("Busway Procurement", 152, "07-Oct-26", "14-May-27", 5),
    ("MTS Procurement", 195, "07-Oct-26", "16-Jul-27", 5),
    ("TechWater Pipe Perimeter Procurement", 190, "07-Oct-26", "09-Jul-27", 5),
    ("Building Construction (Core & Shell)", 375, "02-Mar-26 A",
     "23-Aug-27", 5),
    ("Exterior/Site Utilities", 375, "02-Mar-26 A", "23-Aug-27", 5),
    ("Structure / Underground", 207, "07-Jul-26 A", "30-Apr-27", 6),
    ("Fragnets (structure)", 55, "07-Jul-26 A", "22-Sep-26 A", 6),
    ("Deep Foundations", 42, "08-Jul-26 A", "03-Sep-26 A", 6),
    ("MEP Site Underground", 107, "20-Jul-26 A", "18-Dec-26", 6),
    ("Shallow Foundations", 78, "03-Aug-26 A", "19-Nov-26", 6),
    ("Elevators", 78, "21-Oct-26", "15-Feb-27", 7),
    ("Precast", 121, "21-Sep-26 A", "16-Mar-27", 8),
    ("Slab Pours", 185, "06-Aug-26 A", "30-Apr-27", 9),
    ("Roof", 329, "27-Mar-26 A", "15-Jul-27", 13),
    ("Long Lead Equipment Delivery", 343, "27-Mar-26 A", "30-Apr-27", 13),
    ("Equipment Set", 138, "20-Jan-27", "29-Jun-27", 13),
    ("Roof Cranes", 112, "12-Jan-27", "18-Jun-27", 18),
    ("Roofing System Installation", 97, "01-Dec-26", "23-Mar-27", 18),
    ("Roof Mechanical/Electrical Installation", 171, "01-Dec-26",
     "17-Jun-27", 18),
    ("Mechanical Platform", 98, "24-Mar-27", "15-Jul-27", 20),
    ('PH1: DH202 "Datacenter A"', 482, "09-Feb-26 A", "06-Jan-28", 20),
    ("LLE - Datacenter A", 301, "09-Feb-26 A", "16-Apr-27", 20),
    ("PH1 LLE Summary", 287, "09-Feb-26 A", "29-Mar-27", 20),
    ("Skids Assemblies", 114, "02-Nov-26", "16-Apr-27", 20),
    ("LLE Transformers", 228, "09-Feb-26 A", "04-Jan-27", 21),
    ("LLE MSB", 229, "09-Feb-26 A", "05-Jan-27", 21),
    ("LLE CDB", 213, "09-Feb-26 A", "09-Dec-26", 21),
    ("LLE UPS", 237, "09-Feb-26 A", "15-Jan-27", 21),
    ("LLE RMU", 189, "09-Feb-26 A", "03-Nov-26", 21),
    ("LLE PDP", 216, "09-Feb-26 A", "14-Dec-26", 22),
    ("LLE EDP (MV)", 216, "09-Feb-26 A", "14-Dec-26", 22),
    ("LLE UODP", 216, "09-Feb-26 A", "14-Dec-26", 22),
    ("LLE FW-DH", 287, "09-Feb-26 A", "29-Mar-27", 22),
    ("LLE Adiabatic Dry Coolers", 241, "09-Feb-26 A", "22-Jan-27", 23),
    ("LLE CDU", 270, "09-Feb-26 A", "04-Mar-27", 23),
    ("LLE WCC-CUP", 286, "10-Feb-26 A", "29-Mar-27", 23),
    ("LLE EDP (Gallery)", 228, "09-Feb-26 A", "04-Jan-27", 23),
    ("LLE ESB", 228, "09-Feb-26 A", "04-Jan-27", 24),
    ("LLE GENs", 280, "09-Feb-26 A", "18-Mar-27", 24),
    ("LLE MSG", 194, "09-Feb-26 A", "10-Nov-26", 24),
    ("LLE HVS (GIS SE)", 214, "09-Feb-26 A", "10-Dec-26", 24),
    ("LLE CSB", 206, "09-Feb-26 A", "30-Nov-26", 24),
    ("LLE House-DP", 235, "09-Feb-26 A", "13-Jan-27", 24),
    ("LLE CRAH", 261, "09-Feb-26 A", "19-Feb-27", 24),
    ("Admin Space", 119, "06-Apr-27", "21-Aug-27", 25),
    ("1st Floor Admin Build-Out", 94, "06-Apr-27", "23-Jul-27", 25),
    ("2nd Floor Office Build-Out", 97, "01-May-27", "21-Aug-27", 28),
    ("Generator Rooms - Datacenter A", 143, "31-Dec-26", "23-Jul-27", 29),
    ("GEN 315", 74, "31-Dec-26", "15-Apr-27", 29),
    ("GEN 316", 71, "08-Jan-27", "19-Apr-27", 29),
    ("GEN 317", 68, "15-Jan-27", "21-Apr-27", 30),
    ("GEN 318", 65, "25-Jan-27", "23-Apr-27", 30),
    ("GEN 319", 62, "01-Feb-27", "27-Apr-27", 30),
    ("GEN 320", 65, "08-Feb-27", "07-May-27", 30),
    # ---- part 2, document pages 31-60 -------------------------------
    ("GEN 321", 53, "15-Feb-27", "28-Apr-27", 31),
    ("GEN 322", 65, "22-Feb-27", "21-May-27", 31),
    ("GEN 323", 53, "01-Mar-27", "12-May-27", 31),
    ("GEN 325", 53, "08-Apr-27", "22-Jun-27", 31),
    ("GEN 327", 45, "22-Apr-27", "24-Jun-27", 31),
    ("HV / HVR / MV Rooms - Datacenter A", 172, "15-Jan-27", "17-Sep-27", 32),
    ("HVR Rooms - Datacenter A", 76, "20-Jan-27", "05-May-27", 32),
    ("HV Rooms - Datacenter A", 74, "15-Jan-27", "29-Apr-27", 32),
    ("MV Rooms - Datacenter A", 119, "04-Feb-27", "22-Jul-27", 33),
    ("CUP - Datacenter A", 92, "22-Jan-27", "01-Jun-27", 38),
    ("Level 1 - Rough-Ins / Room Builds", 72, "22-Jan-27", "03-May-27", 38),
    ("Mech Line-ups", 45, "30-Mar-27", "01-Jun-27", 38),
    ("Electrical Rooms - Datacenter A", 174, "15-Jan-27", "21-Sep-27", 39),
    ("ERR 202", 81, "15-Jan-27", "10-May-27", 39),
    ("ER 208", 111, "15-Jan-27", "22-Jun-27", 40),
    ("ER 209", 111, "15-Jan-27", "22-Jun-27", 41),
    ("ER 210", 105, "26-Jan-27", "22-Jun-27", 43),
    ("ER 211", 92, "12-Feb-27", "22-Jun-27", 44),
    ("ER 212", 89, "25-Feb-27", "30-Jun-27", 45),
    ("ER 213", 93, "04-Mar-27", "14-Jul-27", 46),
    ("ER 214", 84, "29-Mar-27", "26-Jul-27", 47),
    ("POE - Datacenter A", 83, "06-Jan-27", "03-May-27", 48),
    ("POE 202 - Segment H", 76, "15-Jan-27", "03-May-27", 48),
    ("POE 201 - Segment E", 76, "06-Jan-27", "22-Apr-27", 48),
    ("ELEC - Datacenter A", 76, "15-Jan-27", "03-May-27", 48),
    ("ELEC 202 - Segment H", 76, "15-Jan-27", "03-May-27", 48),
    ("ELEC 203 - Segment H", 76, "15-Jan-27", "03-May-27", 49),
    ("VAL - Datacenter A", 52, "06-Jan-27", "19-Mar-27", 49),
    ("VAL 203 - Segment H", 45, "15-Jan-27", "19-Mar-27", 49),
    ("VAL 202 - Segment E", 49, "06-Jan-27", "16-Mar-27", 49),
    ("STO - Datacenter A", 45, "15-Jan-27", "19-Mar-27", 49),
    ("STO 204 - Segment H", 36, "15-Jan-27", "08-Mar-27", 49),
    ("STO 205 - Segment H", 45, "15-Jan-27", "19-Mar-27", 50),
    ("TL - Datacenter A", 54, "15-Jan-27", "01-Apr-27", 50),
    ("TL 203 - Segment H", 54, "15-Jan-27", "01-Apr-27", 50),
    ("Data Hall & Gallery (DH 202)", 265, "16-Dec-26", "06-Jan-28", 50),
    ("Data Hall 202", 265, "16-Dec-26", "06-Jan-28", 50),
    ("Galleries (GL 203 & GL 204)", 127, "15-Jan-27", "15-Jul-27", 59),
]

if __name__ == "__main__":
    import collections
    import datetime as dt

    MON = {m: i + 1 for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

    def pd(s):
        s = s.replace(" A", "").strip()
        d, m, y = s.split("-")
        return dt.date(2000 + int(y), MON[m], int(d))

    print(f"{len(BANDS)} WBS bands read off pages 1-30 of the 10/07\n")
    print(f"{'WBS band':<40}{'dur':>5}{'start':>12}{'finish':>12}{'pg':>5}")
    print("-" * 76)
    for nm, dur, s, f, pg in BANDS:
        print(f"{nm[:38]:<40}{dur:>5}{s.replace(' A','*'):>12}"
              f"{f.replace(' A','*'):>12}{pg:>5}")
    ds = [pd(s) for _, _, s, _, _ in BANDS]
    df = [pd(f) for _, _, _, f, _ in BANDS]
    print(f"\nspan of what we have: {min(ds)} .. {max(df)}")
    print("* = actual date")
    pages = collections.Counter(p for *_, p in BANDS)
    have = set(pages)
    print(f"\npages 1-30 covered; bands found on {len(have)} of 30")
    print(f"pages 31-127 not yet received: that is where the ER rooms, data "
          f"halls,\ngalleries and the Level 3/4/5 commissioning blocks will "
          f"be.")
