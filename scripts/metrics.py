"""Turn finished SUMO runs into one row of numbers per run, and one CSV per period and scale.

Run it AFTER the simulations (it only reads files, so it is safe to run at any time):

    python scripts/metrics.py results/raw/test --out results/baseline

It looks for every run folder under the root (a folder is a finished run if it contains
run_info.json, which run_scenario.py writes last, so runs that are still going or crashed
are skipped). Each run needs its OWN folder, e.g. results/raw/test/AM_70_seed1/ .

Output: <out>/<period>/scale_<pct>.csv, one row per seed, plus a summary table on screen.
Period and scale come from the route file name (AM_70.rou.xml -> AM, 70%); if you also
passed --scale to run_scenario the two are multiplied. Re-running rewrites the CSVs from
scratch, so adding more seeds later just means running it again.

What the columns mean
  offered            vehicles whose departure time fell inside the 1-hour demand window
  finished_by_3600   of those, how many had completed their trip by t=3600 (= hourly throughput)
  finished_pct       finished_by_3600 / offered
  backlog_at_3600    vehicles still waiting OUTSIDE the network to enter at t=3600
  in_network_at_3600 vehicles inside the network at t=3600
  clear_time_s       seconds after t=3600 until the last vehicle left (0 if already empty)
  mean_delay_s       per vehicle: time lost in the network + time waiting to enter
  p95_delay_s        95th percentile of that
  mean_stops         average number of times a vehicle came to a stop
  max_waiting_s      longest total standing time of any single vehicle
  worst_wait_flow    which flow that vehicle belonged to, e.g. node5_EB_to_E7 = entered at node5's EB
                     approach, left the network at E7. Shows WHICH movement is being starved
  n_wait_over_300s / n_wait_over_600s   how many vehicles stood still for more than 5 / 10 minutes.
                     A low mean delay can hide a few starved movements; these columns expose them
                     (a run FAILS if n_wait_over_600s is not 0, see the pass rule below)
  worst_entry        entry road whose vehicles waited longest to enter, and that mean wait
  teleports etc.     SUMO's own signs of gridlock or unsafe behaviour; non-zero = distrust the run
  pass               the rule below, so each scale can be called handled or not
"""
import argparse
import csv
import json
import re
import statistics as st
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMAND_END = 3600            # flows run from 0 to 3600 s
BACKLOG_FRAC = 0.005         # PASS needs: backlog at 3600 <= 0.5% of offered demand
CLEAR_S = 600                #   AND last vehicle out within 600 s after demand ends
                             #   AND no teleports AND the run actually emptied
                             #   AND no vehicle stood still for more than 600 s (no starved movement;
                             #       this is the n_wait_over_600s column, so it must be 0)

COLUMNS = ["period", "scale_pct", "seed", "controller", "offered", "finished_by_3600", "finished_pct",
           "backlog_at_3600", "backlog_pct", "in_network_at_3600", "vehicles_completed", "clear_time_s",
           "mean_delay_s", "p95_delay_s", "mean_time_loss_s", "mean_insertion_delay_s",
           "max_insertion_delay_s", "mean_stops", "mean_waiting_s", "max_waiting_s", "worst_wait_flow",
           "n_wait_over_300s", "n_wait_over_600s", "mean_travel_time_s", "worst_entry", "worst_entry_delay_s", "teleports", "collisions",
           "emergency_braking", "emptied", "end_time_s", "pass", "notes", "run_dir"]


def pct95(values):
    s = sorted(values)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def read_summary(path):
    """Row at the end of the demand window (last row with time <= 3600)."""
    at = None
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == "step":
            if float(el.get("time")) <= DEMAND_END:
                at = dict(el.attrib)
            el.clear()
    if at is None:
        raise ValueError("no summary rows")
    return at


def read_tripinfo(path):
    rows = []
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == "tripinfo":
            rows.append(dict(el.attrib))
            el.clear()
    return rows


def label_from_route(info):
    m = re.search(r"(AM|PM)_(\d+)\.rou\.xml$", info["route_file"])
    period, pct = (m.group(1), int(m.group(2))) if m else ("NA", 100)
    eff = pct * float(info["scale"])
    return period, (int(eff) if abs(eff - round(eff)) < 1e-9 else round(eff, 1))


def compute(run_dir):
    run_dir = Path(run_dir)
    info = json.loads((run_dir / "run_info.json").read_text())
    period, scale_pct = label_from_route(info)
    at = read_summary(run_dir / "summary.xml")
    trips = read_tripinfo(run_dir / "tripinfo.xml")
    stat = ET.parse(run_dir / "statistic.xml").getroot()

    offered = int(at["inserted"]) + int(at["waiting"])
    finished = int(at["ended"]) - int(at["discarded"])     # 'ended' also counts vehicles thinned out by --scale
    backlog = int(at["waiting"])
    n = len(trips)
    flt = lambda key: [float(t[key]) for t in trips]
    time_loss, ins_delay = flt("timeLoss"), flt("departDelay")
    total = [a + b for a, b in zip(time_loss, ins_delay)]
    last_arrival = max(flt("arrival")) if n else 0.0

    by_entry = defaultdict(list)                           # entry road = lane id without the lane number
    for t, d in zip(trips, ins_delay):
        by_entry[t["departLane"].rsplit("_", 1)[0]].append(d)
    worst = max(by_entry, key=lambda e: st.mean(by_entry[e])) if by_entry else ""

    teleports = int(stat.find("teleports").get("total"))
    safety = stat.find("safety")
    collisions = int(safety.get("collisions"))
    clear_time = max(0.0, last_arrival - DEMAND_END)

    notes = []
    if not info["emptied"]:
        notes.append("hit max-time with vehicles still in network")
    if n != offered:
        notes.append(f"completed trips ({n}) != offered ({offered})")
    starved = sum(w > 600 for w in flt("waitingTime"))
    if starved:
        worst_flow = max(trips, key=lambda t: float(t["waitingTime"]))["id"].rsplit(".", 1)[0]
        notes.append(f"{starved} vehicles stood >600 s (worst flow {worst_flow})")
    ok = (info["emptied"] and teleports == 0 and clear_time <= CLEAR_S
          and backlog <= BACKLOG_FRAC * offered and starved == 0)

    return {
        "period": period, "scale_pct": scale_pct, "seed": info["seed"], "controller": info["controller"],
        "offered": offered, "finished_by_3600": finished, "finished_pct": round(100 * finished / offered, 1),
        "backlog_at_3600": backlog, "backlog_pct": round(100 * backlog / offered, 2),
        "in_network_at_3600": int(at["running"]), "vehicles_completed": n, "clear_time_s": round(clear_time),
        "mean_delay_s": round(st.mean(total), 1), "p95_delay_s": round(pct95(total), 1),
        "mean_time_loss_s": round(st.mean(time_loss), 1), "mean_insertion_delay_s": round(st.mean(ins_delay), 1),
        "max_insertion_delay_s": round(max(ins_delay), 1),
        "mean_stops": round(st.mean(flt("waitingCount")), 2), "mean_waiting_s": round(st.mean(flt("waitingTime")), 1),
        "max_waiting_s": round(max(flt("waitingTime")), 1),
        "worst_wait_flow": max(trips, key=lambda t: float(t["waitingTime"]))["id"].rsplit(".", 1)[0],
        "n_wait_over_300s": sum(w > 300 for w in flt("waitingTime")),
        "n_wait_over_600s": sum(w > 600 for w in flt("waitingTime")),
        "mean_travel_time_s": round(st.mean(flt("duration")), 1),
        "worst_entry": worst, "worst_entry_delay_s": round(st.mean(by_entry[worst]), 1) if worst else 0,
        "teleports": teleports, "collisions": collisions, "emergency_braking": int(safety.get("emergencyBraking")),
        "emptied": info["emptied"], "end_time_s": round(info["end_time"]), "pass": ok,
        "notes": "; ".join(notes), "run_dir": str(run_dir),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="folder containing the run folders, e.g. results/raw/test")
    ap.add_argument("--out", default=str(REPO / "results" / "baseline"),
                    help="where the CSVs go (default results/baseline; use results/optimized for your controller)")
    a = ap.parse_args()
    root = Path(a.root)

    rows, skipped = [], []
    for info_file in sorted(root.rglob("run_info.json")):
        d = info_file.parent
        missing = [f for f in ("tripinfo.xml", "summary.xml", "statistic.xml") if not (d / f).is_file()]
        if missing:
            skipped.append(f"{d} (missing {', '.join(missing)})")
            continue
        try:
            rows.append(compute(d))
        except Exception as exc:                                   # one bad run must not stop the rest
            skipped.append(f"{d} ({type(exc).__name__}: {exc})")
    for s in sorted(root.rglob("summary.xml")):
        if not (s.parent / "run_info.json").is_file():
            skipped.append(f"{s.parent} (no run_info.json: unfinished or crashed run)")
    if not rows:
        sys.exit(f"no finished runs found under {root}")

    seen = defaultdict(list)
    for r in rows:
        seen[(r["period"], r["scale_pct"], r["seed"], r["controller"])].append(r["run_dir"])
    for key, dirs in seen.items():
        if len(dirs) > 1:
            print(f"WARNING: {key} appears in several folders: {dirs}")

    groups = defaultdict(list)
    for r in rows:
        groups[(r["period"], r["scale_pct"])].append(r)
    out = Path(a.out)
    for (period, pct), rs in sorted(groups.items()):
        (out / period).mkdir(parents=True, exist_ok=True)
        with open(out / period / f"scale_{pct}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(sorted(rs, key=lambda r: (r["controller"], r["seed"])))

    print(f"{len(rows)} runs -> {out}/<period>/scale_<pct>.csv   (mean over seeds)")
    print(f"{'':9s}{'seeds':>5s}{'offered':>8s}{'finished':>9s}{'backlog':>8s}{'delay':>7s}{'p95':>6s}{'clear+':>7s}{'tele':>5s}{'>600s':>6s}  pass")
    for (period, pct), rs in sorted(groups.items()):
        m = lambda k: st.mean(float(r[k]) for r in rs)
        print(f"{period} {str(pct) + '%':>5s}{len(rs):5d}{m('offered'):8.0f}{m('finished_pct'):8.0f}%{m('backlog_at_3600'):8.0f}"
              f"{m('mean_delay_s'):6.0f}s{m('p95_delay_s'):5.0f}s{max(r['clear_time_s'] for r in rs):6.0f}s"
              f"{max(r['teleports'] for r in rs):5d}{m('n_wait_over_600s'):6.0f}  {sum(r['pass'] for r in rs)}/{len(rs)}")
    if skipped:
        print("\nskipped:")
        for s in skipped:
            print("  ", s)


if __name__ == "__main__":
    main()
