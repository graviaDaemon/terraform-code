# ---------------------------------------------------------------
# BIO COLLECTOR  -  step 1 of the biology loop
#
# The cargo slot only empties when the Bio Lab calls take_from().
# If the Lab script is not running, this loop WILL idle forever, and
# that is correct behaviour rather than a bug - so it says so out loud
# instead of going quiet.
# ---------------------------------------------------------------

import bio
import bus
import caps
from results import is_transient

EXCHANGE_ID = "bio_exchange_1"

# Pulls allowed per node per sweep. scan() returns nodes nearest-first and
# collection does not deplete them, so with no memory at all the collector
# would re-pull the closest dot forever. This is what forces it to work
# through the whole field before starting over.
MAX_PULLS = 1

# collect() outcomes that kill a coordinate for good. Bad coordinates do
# not become valid later.
PERMANENT = ["invalid_coords"]

# Outcomes that retire a node for the rest of THIS sweep only. A node with
# nothing on it right now is worth trying again next time round.
SWEEP_ONLY = ["no_fragment"]

# Either kind ends that node's participation in the current sweep.
TERMINAL = PERMANENT + SWEEP_ONLY

# Passes to stay idle before saying so.
IDLE_REPORT_AFTER = 12

# Freshness thresholds for the Signal Bus.
WANTED_MAX_AGE = 30       # Exchange broadcast; older than this, read orders
LAB_MAX_AGE = 30          # Lab heartbeat; older than this, assume it stopped

exchange = get_component(EXCHANGE_ID)

caps.report("Bio Collector online", caps.common() + [
    ("exchange", exchange is not None),
])

# coord -> [pull_count, last_status]
# In-memory on purpose. A save/load wipes it, which simply starts a fresh
# sweep - exactly the behaviour we want, so this is NOT worth persisting
# to the Data Archive even though it now could be.
known = {}

idle = {"passes": 0, "reason": ""}

# count: which sweep we are on. pulls: successful collections this sweep.
sweep = {"count": 1, "pulls": 0}


def begin_sweep():
    """Clear per-sweep state so every live node is collectable again.

    Only PERMANENT entries survive. Returns how many nodes were freed, so
    the caller can tell a real new sweep from a field that is entirely
    dead.
    """
    expired = []
    for coord in known:
        if known[coord][1] not in PERMANENT:
            expired.append(coord)
    for coord in expired:
        known.pop(coord)
    sweep["pulls"] = 0
    return len(expired)


def shared_wanted():
    """Wanted fragment ids, preferring the Exchange broadcast.

    Falls back to reading the order list directly when the Exchange script
    is stopped, so behaviour is unchanged if it is not running.
    """
    published = bus.read_fresh(bus.BIO_WANTED, WANTED_MAX_AGE)
    if published is not None:
        return published
    return bio.wanted_fragments(exchange.orders())


def report_idle(reason):
    """Count consecutive idle passes and notify once per stretch."""
    if idle["reason"] != reason:
        idle["reason"] = reason
        idle["passes"] = 0
    idle["passes"] = idle["passes"] + 1
    if idle["passes"] == IDLE_REPORT_AFTER:
        notify(f"Collector idle: {reason}", "warn")


def working():
    idle["passes"] = 0
    idle["reason"] = ""


def coord_of(location):
    return (location.coords[0], location.coords[1])


def is_exhausted(coord):
    if coord not in known:
        return False
    entry = known[coord]
    if entry[1] in TERMINAL:
        return True
    return entry[0] >= MAX_PULLS


def record(coord, status):
    previous = 0
    if coord in known:
        previous = known[coord][0]
    if status == "ok":
        known[coord] = [previous + 1, status]
        sweep["pulls"] = sweep["pulls"] + 1
    else:
        known[coord] = [previous, status]
        if status in PERMANENT:
            notify(f"Fragment node permanently retired at {coord}")


# --- main loop ----------------------------------------------------------

while True:

    bus.publish(bus.COLLECTOR_STATUS, "running")
    wanted = shared_wanted()

    # --- cargo already sitting here --------------------------------------
    if self.cargo is not None:
        held = self.cargo.fragment_id

        if held is not None and not bio.is_wanted(wanted, held):
            dropped = self.discard()
            if dropped.status == "ok":
                notify(f"Discarded unwanted specimen: {held}")
                working()
                continue
            elif is_transient(dropped):
                sleep(1)
                continue
            else:
                print(dropped.message)

        # Waiting on the Lab. The heartbeat is what separates "the Lab is
        # busy" from "the Lab is not running" - the single most confusing
        # stall in this pipeline, and now a fact rather than a guess.
        if bus.is_alive(bus.LAB_STATUS, LAB_MAX_AGE):
            report_idle("cargo full, waiting for the Bio Lab to take it")
        else:
            report_idle(f"cargo full and the Bio Lab has not reported in"
                        f" {LAB_MAX_AGE}s - is its script running?")
        sleep(1)
        continue

    # --- pick a target ---------------------------------------------------
    attempted = False
    seen = 0
    retired = 0
    unwanted = 0

    for location in self.scan():
        seen = seen + 1
        coord = coord_of(location)

        if is_exhausted(coord):
            retired = retired + 1
            continue

        # A known dot nothing needs - skip before paying for the trip.
        # Unknown dots are always worth collecting; identifying them is
        # the whole point.
        if location.cataloged:
            if not bio.is_wanted(wanted, location.fragment_id):
                unwanted = unwanted + 1
                continue

        result = self.collect(location.coords)

        if result.status == "ok":
            record(coord, "ok")
            working()
        elif result.status in TERMINAL:
            record(coord, result.status)
            working()
        elif result.status == "cargo_occupied":
            pass                  # lab hasn't taken the last one - retry
        elif is_transient(result):
            sleep(1)
        else:
            print(result.message)

        attempted = True
        break                     # world changed - re-scan, don't trust a stale list

    if not attempted:
        # Say WHY. These are very different problems.
        if seen == 0:
            report_idle("scan() returned no fragments at all")
        elif retired > 0:
            # Nothing was eligible this pass and at least one node is only
            # RESTING between sweeps. Start the next sweep rather than idle.
            #
            # This deliberately does NOT require every dot to be retired.
            # Unwanted dots are skipped without ever being retired, so they
            # inflate `seen` forever - gating the reset on `retired >= seen`
            # meant a field holding even one unwanted dot could never start
            # a new sweep, and the collector stalled permanently.
            productive = sweep["pulls"] > 0
            freed = begin_sweep()
            if freed > 0:
                sweep["count"] = sweep["count"] + 1
                if productive:
                    notify(f"Sweep {sweep['count']} starting -"
                           f" {freed} nodes eligible again")
                    working()
                    continue      # straight back to scanning, no sleep
                # A whole sweep with nothing collected means every node is
                # rejecting instantly. Rejections do not spend game time,
                # so looping straight back would spin the script. Back off.
                report_idle(f"a full sweep of {seen} dots collected nothing")
            else:
                report_idle(f"{retired} dots permanently retired,"
                            f" {unwanted} unwanted - nothing eligible")
        elif unwanted > 0:
            report_idle(f"{unwanted} of {seen} dots are cataloged"
                        " but no open order needs them")
        else:
            report_idle("nothing eligible this pass")
        sleep(5)