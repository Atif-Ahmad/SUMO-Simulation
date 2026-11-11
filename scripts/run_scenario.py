"""Run ONE SUMO simulation through TraCI and save SUMO's raw output files.

This is the building block for the sweep: it knows nothing about demand levels or
CSV files. Give it a route file, a scale and a seed; it runs until the network is
empty (or a time cap) and writes the raw outputs into a folder.

Typical use from the repo root (or from anywhere, paths are resolved for you):

    python scripts/run_scenario.py --scale 0.1 --seed 1 --out results/raw/test
    python scripts/run_scenario.py --routes routes/AM_100.rou.xml --scale 0.7 --seed 3 \
                                   --out results/raw/baseline/AM/s70_seed3

From another script:

    from run_scenario import run
    info = run(route_file, scale=0.7, seed=3, out_dir=out_dir)

Baseline = SUMO's own actuated signals (controller=None). To test your own
controller later, pass an object with a step(sim_time) method; it is called once per
simulated second, right before the simulation advances.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# --- find traci / sumolib: pip install first, SUMO_HOME/tools as a fallback ---------
try:
    import traci
    import sumolib
except ImportError:
    if "SUMO_HOME" not in os.environ:
        sys.exit("traci/sumolib not importable and SUMO_HOME is not set")
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
    import traci
    import sumolib

REPO = Path(__file__).resolve().parents[1]          # scripts/ is one level below the repo root
DEFAULT_NET = REPO / "network5.net.xml"
DEFAULT_ROUTES = REPO / "combined_routes.rou.xml"   # placeholder demand (1800 per flow)
DEFAULT_MAX_TIME = 14400                            # hard stop, 4 h of simulated time


def run(route_file, scale, seed, out_dir, net_file=DEFAULT_NET,
        max_time=DEFAULT_MAX_TIME, controller=None, gui=False):
    """Simulate one scenario; return a dict describing the run.

    Writes into out_dir: tripinfo.xml, summary.xml, statistic.xml, sumo.log, run_info.json.
    The run keeps going after the demand window ends so late vehicles are counted;
    it stops when no vehicle is left to insert or travel, or at max_time.
    """
    route_file = Path(route_file).resolve()
    net_file = Path(net_file).resolve()
    out_dir = Path(out_dir).resolve()
    for p in (route_file, net_file):
        if not p.is_file():
            raise FileNotFoundError(p)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sumolib.checkBinary("sumo-gui" if gui else "sumo"),
        "-n", str(net_file),
        "-r", str(route_file),
        "--scale", str(scale),
        "--seed", str(seed),
        "--tripinfo-output", str(out_dir / "tripinfo.xml"),
        "--summary-output", str(out_dir / "summary.xml"),
        "--statistic-output", str(out_dir / "statistic.xml"),
        "--log", str(out_dir / "sumo.log"),          # warnings and errors go here
        "--no-step-log", "true",
        "--duration-log.disable", "true",
    ]
    if gui:
        cmd += ["--start", "true", "--quit-on-end", "true"]

    wall_start = time.time()
    traci.start(cmd)
    try:
        while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < max_time:
            if controller is not None:
                controller.step(traci.simulation.getTime())
            traci.simulationStep()
        end_time = traci.simulation.getTime()
        emptied = traci.simulation.getMinExpectedNumber() == 0
        sumo_version = traci.getVersion()[1]
    finally:
        traci.close()

    info = {
        "route_file": str(route_file),
        "net_file": str(net_file),
        "scale": scale,
        "seed": seed,
        "controller": type(controller).__name__ if controller is not None else "sumo_actuated",
        "max_time": max_time,
        "end_time": end_time,
        "emptied": emptied,                  # False = hit the time cap with vehicles still around
        "wall_seconds": round(time.time() - wall_start, 1),
        "sumo_version": sumo_version,
        "outputs": {n: str(out_dir / n) for n in ("tripinfo.xml", "summary.xml", "statistic.xml", "sumo.log")},
    }
    (out_dir / "run_info.json").write_text(json.dumps(info, indent=2))
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routes", default=str(DEFAULT_ROUTES), help="route file (default: combined_routes.rou.xml)")
    ap.add_argument("--net", default=str(DEFAULT_NET), help="network file (default: network5.net.xml)")
    ap.add_argument("--scale", type=float, default=1.0, help="demand multiplier, 0.7 = 70%% (default 1.0)")
    ap.add_argument("--seed", type=int, default=1, help="random seed (default 1)")
    ap.add_argument("--out", required=True, help="folder for the raw output files")
    ap.add_argument("--max-time", type=float, default=DEFAULT_MAX_TIME, help="stop at this sim time (s)")
    ap.add_argument("--gui", action="store_true", help="watch it in sumo-gui")
    a = ap.parse_args()
    info = run(a.routes, a.scale, a.seed, a.out, net_file=a.net, max_time=a.max_time, gui=a.gui)
    print(f"done: sim time {info['end_time']:.0f}s, emptied={info['emptied']}, "
          f"wall {info['wall_seconds']}s, outputs in {Path(a.out).resolve()}")
    if not info["emptied"]:
        print("WARNING: hit max-time with vehicles still in the network; treat this run's numbers with care")


if __name__ == "__main__":
    main()
