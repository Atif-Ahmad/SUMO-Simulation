import argparse
import sys
from pathlib import Path

# Make "import run_scenario" work no matter which folder you run this from.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_scenario import run, traci


# Constants
MIN_GREEN = 10.0
MAX_GREEN = 90.0
YELLOW_TIME = 3.0
ALL_RED = 2.0
DECISION_INTERVAL = 1.0
STARVATION_LIMIT = 90.0
TIE_BREAK_RULE = "keep_current"
DETECT_RANGE = None  # meters from the stop line the camera sees; None = the whole road

PHASES = ["NS", "NS_LEFT", "EW", "EW_LEFT"]

SIDES_WITH_GREEN = {
    "NS": ["N", "S"],
    "NS_LEFT": ["N", "S"],
    "EW": ["E", "W"],
    "EW_LEFT": ["E", "W"],
}

JUNCTIONS = {
    "J1": {
        "name": "node1",
        "sides": {"N": "-E7", "S": "-E16", "E": "-E8", "W": "-E9"},
        "phases": {
            "NS":      "GGGGrrrrrGGGGrrrrr",
            "NS_LEFT": "rrrrGrrrrrrrrGrrrr",
            "EW":      "rrrrrGGGrrrrrrGGGr",
            "EW_LEFT": "rrrrrrrrGrrrrrrrrG",
        },
    },
    "J0": {
        "name": "node2",
        "sides": {"N": "E16", "S": "E23", "E": "E19", "W": "-E5"},
        "phases": {
            "NS":      "rrrrrGGGGrrrrrrGGGGr",
            "NS_LEFT": "rrrrrrrrrGrrrrrrrrrG",
            "EW":      "GGGGrrrrrrGGGGrrrrrr",
            "EW_LEFT": "rrrrGrrrrrrrrrGrrrrr",
        },
    },
    "J2": {
        "name": "node3",
        "sides": {"N": "-E6", "S": "-E17", "E": "E5", "W": "-E18"},
        "phases": {
            "NS":      "rrrrrGGGrrrrrrGGGr",
            "NS_LEFT": "rrrrrrrrGrrrrrrrrG",
            "EW":      "GGGGrrrrrGGGGrrrrr",
            "EW_LEFT": "rrrrGrrrrrrrrGrrrr",
        },
    },
    "J17": {
        "name": "node4",
        "sides": {"N": "-E20", "S": "-E21", "E": "-E22", "W": "-E19"},
        "phases": {
            "NS":      "GGGrrrrrrGGGrrrrrr",
            "NS_LEFT": "rrrGrrrrrrrrGrrrrr",
            "EW":      "rrrrGGGGrrrrrGGGGr",
            "EW_LEFT": "rrrrrrrrGrrrrrrrrG",
        },
    },
    "J18": {
        "name": "node5",
        "sides": {"N": "-E23", "S": "-E26", "E": "-E24", "W": "-E25"},
        "phases": {
            "NS":      "rrrrGGGGrrrrrGGGGr",
            "NS_LEFT": "rrrrrrrrGrrrrrrrrG",
            "EW":      "GGGrrrrrrGGGrrrrrr",
            "EW_LEFT": "rrrGrrrrrrrrGrrrrr",
        },
    },
}


# Junction Class
class JunctionController:

    def __init__(self, tls_id, decide_function):
        settings = JUNCTIONS[tls_id]
        self.tls_id = tls_id
        self.name = settings["name"]
        self.sides = settings["sides"]
        self.greens = settings["phases"]   # {"NS": "...", "NS_LEFT": "...", ...}
        self.decide = decide_function

        self.lanes = {}       # all lane ids of each side
        self.left_lane = {}   # the left-turn-only lane of each side (always the highest number)
        for side in self.sides:
            edge = self.sides[side]
            num_lanes = traci.edge.getLaneNumber(edge)
            self.lanes[side] = []
            for i in range(num_lanes):
                self.lanes[side].append(edge + "_" + str(i))
            self.left_lane[side] = edge + "_" + str(num_lanes - 1)

        self.check_network()

        # Current simulation stuff
        self.current_phase = "NS"
        self.next_phase = None
        self.stage = "green"
        self.stage_start_time = 0.0
        self.next_decision_time = 0.0
        self.switch_count = 0

        # Last time each side had ANY green, and last time its left turn had a PROTECTED green.
        self.last_green_time = {}
        self.last_left_green_time = {}
        for side in self.sides:
            self.last_green_time[side] = 0.0
            self.last_left_green_time[side] = 0.0

        self.set_light(self.greens["NS"])

    def set_light(self, state):
        if not self.is_safe(state):
            raise RuntimeError(self.tls_id + ": refusing to show a conflicting state " + state)
        traci.trafficlight.setRedYellowGreenState(self.tls_id, state)

    def is_safe(self, state):
        """A state is safe if all its green letters are also green in ONE of our 4 phases."""
        for phase in PHASES:
            phase_state = self.greens[phase]
            ok = True
            for position in range(len(state)):
                if state[position] in "Gg" and phase_state[position] not in "Gg":
                    ok = False
                    break
            if ok:
                return True
        return False

    def yellow_state(self, current, nxt):
        """Links that stay green in the next phase keep their color; links that lose green turn yellow."""
        now_state = self.greens[current]
        next_state = self.greens[nxt]
        letters = []
        for position in range(len(now_state)):
            green_now = now_state[position] in "Gg"
            green_next = next_state[position] in "Gg"
            if green_now and green_next:
                letters.append(now_state[position])
            elif green_now:
                letters.append("y")
            else:
                letters.append("r")
        return "".join(letters)

    def all_red_state(self, current, nxt):
        """Everything red, except links that are green in both phases (none, with protected-only lefts)."""
        now_state = self.greens[current]
        next_state = self.greens[nxt]
        letters = []
        for position in range(len(now_state)):
            if now_state[position] in "Gg" and next_state[position] in "Gg":
                letters.append(now_state[position])
            else:
                letters.append("r")
        return "".join(letters)

    def check_network(self):
        """Safety check: the table must match the network that SUMO loaded."""

        links = traci.trafficlight.getControlledLinks(self.tls_id)

        # Turn {"N": "-E23", ...} around into {"-E23": "N", ...}.
        side_of_edge = {}
        for side in self.sides:
            side_of_edge[self.sides[side]] = side

        for phase in PHASES:
            state = self.greens[phase]

            if len(state) != len(links):
                sys.exit(
                    self.tls_id + ": the " + phase + " state has " + str(len(state))
                    + " letters but the network has " + str(len(links)) + " links"
                )

            for position in range(len(links)):
                incoming_lane = links[position][0][0]
                incoming_edge = traci.lane.getEdgeID(incoming_lane)
                side = side_of_edge.get(incoming_edge)

                if side is None:
                    sys.exit(
                        self.tls_id + ": letter " + str(position)
                        + " belongs to edge " + incoming_edge
                        + ", which is not in the JUNCTIONS table"
                    )

                if state[position] in "Gg":
                    if side not in SIDES_WITH_GREEN[phase]:
                        sys.exit(
                            self.tls_id + ": letter " + str(position) + " (cars from side " + side
                            + ") is green in " + phase + ". Update JUNCTIONS for this junction."
                        )
                    if phase.endswith("_LEFT") and incoming_lane != self.left_lane[side]:
                        sys.exit(
                            self.tls_id + ": letter " + str(position) + " is green in " + phase
                            + " but is not the left-turn lane. Update JUNCTIONS for this junction."
                        )

    def measure_side(self, side, now):
        vehicles = []

        for lane in self.lanes[side]:
            for vehicle in traci.lane.getLastStepVehicleIDs(lane):
                # If the camera range is limited, skip cars that are too far from the stop line.
                if DETECT_RANGE is not None:
                    distance = traci.lane.getLength(lane) - traci.vehicle.getLanePosition(vehicle)
                    if distance > DETECT_RANGE:
                        continue
                vehicles.append(vehicle)

        queue = 0           # stopped cars on the whole side
        left_queue = 0      # stopped cars in the left-turn lane only
        total_wait = 0.0
        max_wait = 0.0
        left_max_wait = 0.0

        for vehicle in vehicles:
            stopped = traci.vehicle.getSpeed(vehicle) < 0.1
            in_left_lane = traci.vehicle.getLaneID(vehicle) == self.left_lane[side]
            wait = traci.vehicle.getAccumulatedWaitingTime(vehicle)

            if stopped:
                queue += 1
                if in_left_lane:
                    left_queue += 1

            total_wait += traci.vehicle.getWaitingTime(vehicle)

            if wait > max_wait:
                max_wait = wait
            if in_left_lane and wait > left_max_wait:
                left_max_wait = wait

        return {
            "edge": self.sides[side],
            "lanes": len(self.lanes[side]),
            "vehicles": len(vehicles),
            "queue": queue,                       # all lanes
            "through_queue": queue - left_queue,  # lanes for straight and right
            "left_queue": left_queue,             # left-turn lane
            "total_wait": total_wait,
            "max_wait": max_wait,
            "left_max_wait": left_max_wait,
            "time_since_green": now - self.last_green_time[side],
            "time_since_left_green": now - self.last_left_green_time[side],
        }

    def make_snapshot(self, now, switch_now):
        sides_info = {}

        for side in self.sides:
            sides_info[side] = self.measure_side(side, now)

        return {
            "tls": self.tls_id,
            "junction": self.name,
            "time": now,
            "current_phase": self.current_phase,  # "NS", "NS_LEFT", "EW" or "EW_LEFT"
            "time_in_phase": now - self.stage_start_time,
            "must_switch": switch_now,
            "sides": sides_info,
            "params": {
                "MIN_GREEN": MIN_GREEN,
                "MAX_GREEN": MAX_GREEN,
                "YELLOW_TIME": YELLOW_TIME,
                "ALL_RED": ALL_RED,
                "DECISION_INTERVAL": DECISION_INTERVAL,
                "STARVATION_LIMIT": STARVATION_LIMIT,
                "TIE_BREAK_RULE": TIE_BREAK_RULE,
            },
        }

    def step(self, now):
        time_in_stage = now - self.stage_start_time

        if self.stage == "green":
            for side in SIDES_WITH_GREEN[self.current_phase]:
                self.last_green_time[side] = now
                if self.current_phase.endswith("_LEFT"):
                    self.last_left_green_time[side] = now

            if time_in_stage >= MIN_GREEN and now >= self.next_decision_time:
                self.next_decision_time = now + DECISION_INTERVAL
                switch_now = time_in_stage >= MAX_GREEN

                snapshot = self.make_snapshot(now, switch_now)
                new_phase = self.decide(snapshot)

                if new_phase not in PHASES:
                    raise ValueError("decide function must return one of " + str(PHASES)
                                     + ", got " + str(new_phase))

                # At MAX_GREEN, if the algorithm wants to keep it, move to the next phase in PHASES.
                if switch_now and new_phase == self.current_phase:
                    position = PHASES.index(self.current_phase)
                    new_phase = PHASES[(position + 1) % len(PHASES)]

                if new_phase != self.current_phase:
                    self.next_phase = new_phase
                    self.stage = "yellow"
                    self.stage_start_time = now
                    self.set_light(self.yellow_state(self.current_phase, self.next_phase))

        elif self.stage == "yellow":
            if time_in_stage >= YELLOW_TIME:
                self.stage = "all_red"
                self.stage_start_time = now
                self.set_light(self.all_red_state(self.current_phase, self.next_phase))

        if self.stage == "all_red":
            if now - self.stage_start_time >= ALL_RED:
                self.current_phase = self.next_phase
                self.next_phase = None
                self.stage = "green"
                self.stage_start_time = now
                self.next_decision_time = now
                self.switch_count += 1
                self.set_light(self.greens[self.current_phase])


class NetworkController:

    def __init__(self, decide_function):
        self.decide = decide_function
        self.junctions = None

    def step(self, now):
        if self.junctions is None:
            self.junctions = {}

            for tls_id in JUNCTIONS:
                self.junctions[tls_id] = JunctionController(tls_id, self.decide)

        for tls_id in self.junctions:
            self.junctions[tls_id].step(now)


def placeholder(snapshot):
    sides = snapshot["sides"]
    limit = snapshot["params"]["STARVATION_LIMIT"]

    for phase in PHASES:
        for side in SIDES_WITH_GREEN[phase]:
            info = sides[side]
            if phase.endswith("_LEFT"):
                starving = info["left_queue"] > 0 and info["time_since_left_green"] > limit
            else:
                starving = info["through_queue"] > 0 and info["time_since_green"] > limit
            if starving:
                return phase

    scores = {
        "NS": sides["N"]["through_queue"] + sides["S"]["through_queue"],
        "NS_LEFT": sides["N"]["left_queue"] + sides["S"]["left_queue"],
        "EW": sides["E"]["through_queue"] + sides["W"]["through_queue"],
        "EW_LEFT": sides["E"]["left_queue"] + sides["W"]["left_queue"],
    }

    best_phase = snapshot["current_phase"]   # 3. a tie keeps the current phase
    for phase in PHASES:
        if scores[phase] > scores[best_phase]:
            best_phase = phase

    return best_phase


def main():
    parser = argparse.ArgumentParser(
        description="Run one scenario with our own signal controller."
    )
    parser.add_argument(
        "--routes",
        required=True,
        help="route file, e.g. la_routes/AM_70.rou.xml",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1,
        help="random seed (use the same ones as the baseline)",
    )
    parser.add_argument("--out", required=True, help="folder for the output files")
    parser.add_argument("--gui", action="store_true", help="watch it in sumo-gui")

    args = parser.parse_args()

    decide_function = placeholder

    controller = NetworkController(decide_function)
    info = run(
        args.routes,
        1.0,
        args.seed,
        args.out,
        controller=controller,
        gui=args.gui,
    )

    print("phase changes per junction:")

    for tls_id in controller.junctions:
        junction = controller.junctions[tls_id]
        print("   ", junction.name, junction.switch_count)

    print(
        "done: sim time",
        round(info["end_time"]),
        "s, emptied =",
        info["emptied"],
        ", took",
        info["wall_seconds"],
        "s",
    )

    if not info["emptied"]:
        print("WARNING: hit max-time with vehicles still in the network")


if __name__ == "__main__":
    main()