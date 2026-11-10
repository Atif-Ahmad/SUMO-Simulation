import os, sys

if "SUMO_HOME" not in os.environ:
    sys.exit("SUMO_HOME is not set")
sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

import traci
from sumolib import checkBinary

CFG = "CNS.sumocfg"   # change to whichever cfg uses your static or actuated net

traci.start([checkBinary("sumo"), "-c", CFG, "--no-step-log", "true"])

print("TLS ids:", traci.trafficlight.getIDList())
for tls in traci.trafficlight.getIDList():
    print(tls, traci.trafficlight.getRedYellowGreenState(tls))

for step in range(300):
    traci.simulationStep()          # advances 1 simulated second by default
    if step % 50 == 0:
        print(step, traci.simulation.getTime(), "vehicles:", traci.vehicle.getIDCount())

traci.close()