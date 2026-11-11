"""Parse the five LA turning-movement-count PDFs into data/la_counts.csv.

Usage:  python scripts/export_counts.py "<folder with the 5 PDFs>"
Needs pdftotext (poppler) and sumolib. AM = 8-9 AM, PM = 5-6 PM. Checks every row total
against the PDF and every approach edge against network5.net.xml.
"""
import re, subprocess, csv, math, sys
from pathlib import Path
import sumolib

REPO = Path(__file__).resolve().parents[1]
PDF = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data" / "traffic_data_pdfs"
def txt(f): return subprocess.run(["pdftotext","-layout",str(PDF/f),"-"],capture_output=True,text=True).stdout

# ---- node layout (same names as the project): label -> (node name, TLS id, count location, count date, source pdf)
NODES = {
 "N": ("node1","J1","Highland Ave / Hollywood Blvd","2017-04-26","22506_HIGHOL170426.pdf"),
 "C": ("node2","J0","Highland Ave / Sunset Blvd","2016-05-12","22559_HIGSUN160512.pdf"),
 "W": ("node3","J2","La Brea Ave / Sunset Blvd","2016-12-01","21664_LABSUN161201.pdf"),
 "E": ("node4","J17","Wilcox Ave / Sunset Blvd","2018-05-16","WILCOX.SUNSET.180516.MAN.pdf"),
 "S": ("node5","J18","Highland Ave / Santa Monica Blvd","2016-04-20","20676_HIGSAN_160420.pdf"),
}
# approach -> network edge entering that signal (from the baseline-branch network)
EDGE = {"N":{"SB":"-E7","NB":"-E16","EB":"-E9","WB":"-E8"}, "C":{"SB":"E16","NB":"E23","WB":"E19","EB":"-E5"},
        "W":{"EB":"-E18","WB":"E5","SB":"-E6","NB":"-E17"}, "E":{"WB":"-E22","EB":"-E19","SB":"-E20","NB":"-E21"},
        "S":{"NB":"-E26","SB":"-E23","WB":"-E24","EB":"-E25"}}
ENTRIES = {"-E7","-E9","-E8","-E26","-E24","-E25","-E18","-E6","-E17","-E22","-E20","-E21"}
HEADING = {"SB":-90,"NB":90,"EB":0,"WB":180}   # compass heading of traffic on that approach (SUMO y points up)

def hourly(f):                       # LADOT summary page: hourly L/T/R per approach
    t = txt(f); out = {"AM":{}, "PM":{}}
    for per,hr in (("AM","8-9"),("PM","17-18")):
        m = re.findall(rf"^{hr}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+{hr}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", t, re.M)
        assert len(m)==2, (f,per,len(m))
        (a,b) = m
        out[per]["NB"]=tuple(map(int,a[0:3])); assert sum(out[per]["NB"])==int(a[3])      # row total matches
        out[per]["SB"]=tuple(map(int,a[4:7])); assert sum(out[per]["SB"])==int(a[7])
        out[per]["EB"]=tuple(map(int,b[0:3])); assert sum(out[per]["EB"])==int(b[3])
        out[per]["WB"]=tuple(map(int,b[4:7])); assert sum(out[per]["WB"])==int(b[7])
    return out

def labrea():                        # 15-min: SB R T L | WB R T L | NB R T L | EB R T L | total
    t = txt("21664_LABSUN161201.pdf")
    rows = re.findall(r"^\s*(\d{1,2}:\d\d)\s+((?:\d+\s+){12})(\d+)\s*$", t, re.M)[:24]
    def agg(times):
        s=[0]*12; n=0
        for tm,vals,tot in rows:
            if tm in times:
                v=list(map(int,vals.split())); assert sum(v)==int(tot),(tm,v,tot); s=[a+b for a,b in zip(s,v)]; n+=1
        assert n==4
        return {"SB":(s[2],s[1],s[0]),"WB":(s[5],s[4],s[3]),"NB":(s[8],s[7],s[6]),"EB":(s[11],s[10],s[9])}
    return {"AM":agg({"8:00","8:15","8:30","8:45"}),"PM":agg({"17:00","17:15","17:30","17:45"})}

uturns = 0
def wilcox():                        # 15-min: NL NT NR NU SL ST SR SU EL ET ER EU WL WT WR WU | total
    global uturns
    t = txt("WILCOX.SUNSET.180516.MAN.pdf")
    rows = re.findall(r"^\s*(\d{1,2}:\d\d)\s+(AM|PM)\s+((?:\d+\s+){16})(\d+)\s*$", t, re.M)[:24]
    def agg(times, per):
        global uturns
        s=[0]*16; n=0
        for tm,ap,vals,tot in rows:
            if ap==per and tm in times:
                v=list(map(int,vals.split())); assert sum(v)==int(tot),(tm,v,tot); s=[a+b for a,b in zip(s,v)]; n+=1
        assert n==4
        uturns += s[3]+s[7]+s[11]+s[15]
        return {"NB":(s[0],s[1],s[2]),"SB":(s[4],s[5],s[6]),"EB":(s[8],s[9],s[10]),"WB":(s[12],s[13],s[14])}
    return {"AM":agg({"8:00","8:15","8:30","8:45"},"AM"),"PM":agg({"5:00","5:15","5:30","5:45"},"PM")}

data = {"N":hourly(NODES["N"][4]), "C":hourly(NODES["C"][4]), "S":hourly(NODES["S"][4]), "W":labrea(), "E":wilcox()}

# ---- validate edge mapping against the repo network
net = sumolib.net.readNet(str(REPO/"network5.net.xml"), withInternal=False)
for key,(node,tls,*_ ) in NODES.items():
    for ap,e in EDGE[key].items():
        ed = net.getEdge(e); assert ed.getToNode().getID()==tls, (e, ed.getToNode().getID(), tls)
        sh = ed.getShape(); h = math.degrees(math.atan2(sh[-1][1]-sh[-2][1], sh[-1][0]-sh[-2][0]))
        assert abs((h-HEADING[ap]+180)%360-180) < 20, (e, ap, h)
    assert net.getNode(tls).getType()=="traffic_light"
print("network check OK: every approach edge ends at its TLS and points the stated direction")

out = REPO/"data"/"la_counts.csv"
with open(out,"w",newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["period","window","node","tls","approach","edge","is_entry","left","through","right","intersection","count_date"])
    for per,win in (("AM","08:00-09:00"),("PM","17:00-18:00")):
        for key in ("N","C","W","E","S"):
            node,tls,loc,date,_ = NODES[key]
            for ap in ("NB","SB","EB","WB"):
                L,T,R = data[key][per][ap]; e = EDGE[key][ap]
                w.writerow([per,win,node,tls,ap,e,int(e in ENTRIES),L,T,R,loc,date])
rows = list(csv.DictReader(open(out)))
print("wrote", out, "rows:", len(rows))
for per in ("AM","PM"):
    ent = sum(int(r["left"])+int(r["through"])+int(r["right"]) for r in rows if r["period"]==per and r["is_entry"]=="1")
    print(f"{per}: entry approaches {sum(1 for r in rows if r['period']==per and r['is_entry']=='1')}, total entering demand = {ent} veh/h")
print("Wilcox U-turn counts dropped (not representable in L/T/R):", uturns, "vehicles over the two peak hours")
