"""Which precast areas actually feed the penthouse? If the eight remaining
Temporary Dry In predecessors are the right ones, the areas that cover the
generator rooms should be among them."""
import sys, re, collections, datetime as dt
sys.path.insert(0,"/home/user/SixTerminal-Live")
from engine.xml_reader import load_xml
SCR="/tmp/claude-0/-home-user-SixTerminal-Live/bd14e389-f5e4-5638-b9b0-8ffe79025c5f/scratchpad"
p=load_xml(f"{SCR}/MDC3_logic_repaired_v35.xml")
byu={a.uid:a for a in p.activities}
ids={str(a.activity_id):a for a in p.activities}
suc=collections.defaultdict(list)
for r in p.relations: suc[r.predecessor_uid].append(r)
dry=ids["MDC3.ROOF.1870"]
keep={str(byu[r.predecessor_uid].activity_id)
      for r in p.relations if r.successor_uid==dry.uid}
print("Temporary Dry In predecessors AS THE FILE NOW STANDS: "
      f"{len(keep)}\n")
turn=[a for a in p.activities if re.search(r'Turnover',str(a.name),re.I)
      and re.search(r'ST\.PC\.',str(a.activity_id))]
print(f"{'area turnover':<24}{'name':<26}{'feeds Dry In':<14}what else it feeds")
print("-"*96)
for a in sorted(turn,key=lambda a:(int(re.search(r'A(\d+)',str(a.activity_id)).group(1)),str(a.activity_id))):
    k=str(a.activity_id)
    tgt=collections.Counter()
    for r in suc[a.uid]:
        s=byu[r.successor_uid]; sid=str(s.activity_id)
        m=re.match(r'MDC3\.(?:PH\d\.)?([A-Z]+)',sid)
        tgt[m.group(1) if m else "?"]+=1
    mark="YES" if k in keep else "-"
    tops=", ".join(f"{v}x {t}" for t,v in tgt.most_common(4)) or "(nothing)"
    print(f"{k:<24}{str(a.name)[:25]:<26}{mark:<14}{tops}")
print("\nwhich areas feed GENERATOR rooms (ie actually cover the penthouse):")
for a in turn:
    g=[byu[r.successor_uid] for r in suc[a.uid]
       if re.search(r'\.GEN\.',str(byu[r.successor_uid].activity_id))]
    if g:
        print(f"   {str(a.activity_id):<22}{str(a.name)[:26]:<28}"
              f"feeds {len(g)} gen activities   in Dry In preds: "
              f"{'YES' if str(a.activity_id) in keep else 'NO'}")
print("\nwhich areas feed ROOF activities:")
for a in turn:
    g=[byu[r.successor_uid] for r in suc[a.uid]
       if re.search(r'ROOF',str(byu[r.successor_uid].activity_id))]
    if g:
        print(f"   {str(a.activity_id):<22}{str(a.name)[:26]:<28}"
              f"feeds {len(g)} roof activities  in Dry In preds: "
              f"{'YES' if str(a.activity_id) in keep else 'NO'}")
