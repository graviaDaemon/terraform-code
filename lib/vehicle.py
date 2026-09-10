"""Shared ground-vehicle layer: drive, park, dock, and never strand yourself.

    import vehicle
    vehicle.find_home(self)
    vehicle.drive_to(self, x, y)

`self` does not exist in a library, so the vehicle is passed in. Everything
here touches only `nav`, `battery` and fleet telemetry, which every ground
vehicle has - a Rover, a scouting Pioneer and a constructing Pioneer are the
same thing from this file's point of view.

The rule the whole layer is built around, from Vehicle Proximity & Service:
GET NEAR, PARK, THEN WORK. get_distance_to() reaching tolerance means close
enough, NOT stopped, so nav.brake() comes before every scan, survey, drill
or transfer.

The learned drive cost is keyed per VEHICLE ID, not per kind. Each mounted
module adds to the draw while moving, so two Pioneers on the same `.kind`
carrying different rigs cost different amounts per meter, and one blended
figure would send one of them home too late (D-011, D-017).

Every fraction and tolerance below is a module constant AND a default
argument, because the Pioneer's are not the Rover's.
"""

import bus
import caps

STATION_TYPE = "charging_station"
FLEET_ID = "fleet"
ARCHIVE_ID = "notebook"

# Where "home" is. (0, 0) is only the coordinate origin; the point that
# matters is the charging station's docking position, resolved by
# find_home() when the script starts. Docked means parked inside the
# outpost footprint plus a ~2 m margin, and a vehicle parked 3 m short of
# that footprint waits for a charge that never comes.
#
# "id" is the charging STATION; "outpost" is the outpost that station
# stands on, which is what fleet telemetry reports in `.docked_at`. The
# two are separate because docked somewhere is not docked HERE, and
# telling those apart is the whole of D-040.
HOME = {"x": 0, "y": 0, "id": "origin", "outpost": None}

# Arrival tolerance. The docs say a loop needs > 2 rather than exact zero.
ARRIVE_TOLERANCE = 3.0

# Home leg tolerance. Must land inside the ~2 m service margin, so it is
# tighter than the field tolerance.
HOME_TOLERANCE = 1.5

# One throttle for every leg, and a low one. The docs are blunt about this:
# a full Rover at full throttle lasts about 5 hours, at half throttle closer
# to 20. Racing home is what strands a vehicle, not dawdling. Using a single
# value also keeps the learned Wh/metre figure meaningful - a blend of two
# throttles would describe neither.
CRUISE_THROTTLE = 0.5
RETURN_THROTTLE = 0.5

# Battery fractions. Below 0.1 the docs call a vehicle close to stranded.
RESERVE_FRACTION = 0.30        # stop working, head home
STRANDED_FRACTION = 0.12       # stop everything, call for help
DEPART_FRACTION = 0.45         # do not start a new trip below this

# Learned energy cost, persisted once Data Archive exists. One key per
# vehicle id: a rig is what costs Wh per meter, and ids survive the module
# swaps both Pioneers are due when Large Battery Holders unlock.
DRAIN_PREFIX = "vehicle.wh_per_meter"
DEFAULT_DRAIN = 0.05           # Wh per meter, deliberately pessimistic
DRAIN_SMOOTHING = 0.3          # weight of each new sample

# A drive that makes no progress for this many checks is treated as blocked.
STALL_LIMIT = 20

# Seconds to wait between passes when there is nothing to do.
IDLE_INTERVAL = 10

# Passes to wait at base before pointing out that nothing is charging us.
CHARGE_WARN_AFTER = 30

# Passes of a flat battery level before the station counts as done with
# us. Comfortably longer than fleet.run's own 10 s pass, so a gap between
# two charge queues does not read as a finished charge.
STALL_CHARGE_PASSES = 6


# --- learned drive cost -------------------------------------------------

def _notebook():
    """The Data Archive component, or None before that research."""
    return caps.component(ARCHIVE_ID)


def drain_key(vehicle) -> str:
    """This vehicle's own cost key, e.g. "vehicle.wh_per_meter:rover_1"."""
    return DRAIN_PREFIX + ":" + vehicle.id


def drain_per_meter(vehicle):
    """Learned Wh cost per meter driven, or a pessimistic default.

    Being wrong high is cheap: the vehicle comes home early. Being wrong
    low strands it in the field, so the default errs high until real
    samples replace it - which is also why a vehicle with no history of
    its own starts from that default rather than borrowing another rig's
    figure.
    """
    key = drain_key(vehicle)
    book = _notebook()
    if book is None:
        return bus.read(key, DEFAULT_DRAIN)
    return book.get(key, DEFAULT_DRAIN)


def record_drain(vehicle, wh_used, meters):
    """Fold one observed trip into this vehicle's running estimate."""
    if meters <= 0 or wh_used <= 0:
        return
    key = drain_key(vehicle)
    sample = wh_used / meters
    blended = (drain_per_meter(vehicle) * (1 - DRAIN_SMOOTHING)
               + sample * DRAIN_SMOOTHING)
    book = _notebook()
    if book is not None:
        book.set(key, blended)
    bus.publish(key, blended)


def can_reach_and_return(vehicle, x, y, reserve=RESERVE_FRACTION):
    """True when the current charge covers the round trip with reserve left."""
    distance = vehicle.nav.get_distance_to(x, y)
    home_leg = _distance_between(x, y, HOME["x"], HOME["y"])
    needed = (distance + home_leg) * drain_per_meter(vehicle)
    usable = vehicle.battery.wh() - (vehicle.battery.capacity() * reserve)
    return usable > needed


def _distance_between(ax, ay, bx, by):
    return sqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by))


# --- home and docking ---------------------------------------------------

def find_home(vehicle):
    """Resolve HOME to the nearest charging station's docking point.

    The docs say to target a building's own position for at-building
    work rather than the outpost footprint anchor, and charging is
    at-building work. Every outpost is searched and the station nearest
    the vehicle's current position wins, so a vehicle working out of a
    founded outpost docks there. Falls back to the home outpost anchor,
    then to the origin, and says which one it picked.
    """
    position = vehicle.nav.get_position()
    nearest = None
    nearest_distance = 0
    for ref in caps.buildings(STATION_TYPE):
        distance = _distance_between(position.x, position.y,
                                     ref.position[0], ref.position[1])
        if nearest is None or distance < nearest_distance:
            nearest = ref
            nearest_distance = distance

    if nearest is not None:
        HOME["x"] = nearest.position[0]
        HOME["y"] = nearest.position[1]
        HOME["id"] = nearest.id
        HOME["outpost"] = nearest.outpost_id
        return

    network = caps.component("outpost_network")
    if network is not None:
        outpost = network.home()
        coords = outpost.coords()
        HOME["x"] = coords[0]
        HOME["y"] = coords[1]
        HOME["id"] = outpost.id
        HOME["outpost"] = outpost.id
        notify("No Vehicle Charging Station found - homing on the outpost"
               " anchor instead", "warn")
        return

    HOME["outpost"] = None
    notify("No outpost network readable - homing on (0, 0)", "warn")


def fleet_ref(vehicle):
    """This vehicle's own entry in the fleet snapshot, or None."""
    component = get_component(FLEET_ID)
    if component is None:
        return None
    for ref in component.vehicles():
        if ref.id == vehicle.id:
            return ref
    return None


def docked_status(vehicle):
    """The outpost's own verdict on whether this vehicle is docked ANYWHERE.

    This is the exact flag the charging station's manager gates on, read
    from fleet telemetry. None when it cannot be read. It is the honest
    answer to "is this thing parked", which is not the same question as
    "is it parked at home" - see docked_home().
    """
    ref = fleet_ref(vehicle)
    if ref is None:
        return None
    return ref.is_docked


def docked_outpost(vehicle):
    """The id of the outpost this vehicle is parked at, or None."""
    ref = fleet_ref(vehicle)
    if ref is None or not ref.is_docked:
        return None
    if ref.docked_at == "":
        return None
    return ref.docked_at


def docked_home(vehicle):
    """True, False, or None: is this vehicle docked at HOME's outpost?

    None means the question cannot be answered from telemetry - no fleet
    component, no snapshot for this vehicle, an unresolved HOME outpost,
    or a docked flag with no outpost id beside it - and the caller falls
    back to distance rather than guessing either way.

    `.is_docked` alone means parked at AN outpost. A constructor that
    founds one 344 m out and parks inside its footprint reads as docked
    there, and reading that as home is what strands it (D-040).
    """
    ref = fleet_ref(vehicle)
    if ref is None:
        return None
    if not ref.is_docked:
        return False
    if HOME["outpost"] is None or ref.docked_at == "":
        return None
    return ref.docked_at == HOME["outpost"]


# Foreign outposts already reported, so a long drive home does not say
# the same thing every pass.
foreign = {"outposts": []}


def foreign_dock_warning(vehicle):
    """Parked at an outpost that is not home - say so once per outpost.

    Silence is what made this expensive: the constructor parked inside
    the outpost it had just built, read itself as home, and sat there
    reporting `idle` for the rest of the day (D-040).
    """
    where = docked_outpost(vehicle)
    if where is None or where == HOME["outpost"]:
        return
    if where in foreign["outposts"]:
        return
    foreign["outposts"].append(where)
    notify(f"{vehicle.name} is docked at {where}, not at"
           f" {HOME['outpost']} - heading back", "warn")


def at_home(vehicle, tolerance=HOME_TOLERANCE):
    """True when this vehicle is docked at HOME's own outpost, or is within
    HOME_TOLERANCE of the docking point if docking cannot be read."""
    docked = docked_home(vehicle)
    if docked is not None:
        if not docked:
            foreign_dock_warning(vehicle)
        return docked
    return vehicle.nav.get_distance_to(HOME["x"], HOME["y"]) <= tolerance


undocked = {"passes": 0}


def undocked_warning(vehicle, warn_after=CHARGE_WARN_AFTER):
    """Parked on the home target but the outpost says not docked. The home
    coordinate is wrong, or the station sits outside the footprint - say
    so once, not every pass."""
    undocked["passes"] = undocked["passes"] + 1
    if undocked["passes"] % warn_after != 1:
        return
    position = vehicle.nav.get_position()
    notify(f"{vehicle.name} parked at ({round(position.x, 1)},"
           f" {round(position.y, 1)}) targeting {HOME['id']} at"
           f" ({round(HOME['x'], 1)}, {round(HOME['y'], 1)}) but the outpost"
           f" does not count it as docked - it will not be charged here",
           "warn")


# --- movement -----------------------------------------------------------

def drive_to(vehicle, x, y, throttle=CRUISE_THROTTLE,
             tolerance=ARRIVE_TOLERANCE, stranded=STRANDED_FRACTION,
             stall_limit=STALL_LIMIT):
    """Drive to (x, y) and PARK there. True on arrival.

    Returns False without parking when the battery falls to the stranding
    floor or the vehicle stops making progress - the caller decides what
    to do about it, because "head home" and "give up here" are different.
    """
    targeted = vehicle.nav.set_target(x, y)
    if targeted.status != "ok":
        notify(f"nav.set_target({x}, {y}): {targeted.message}", "warn")
        return False

    vehicle.nav.set_throttle(throttle)
    stalled = 0

    while vehicle.nav.get_distance_to(x, y) > tolerance:
        if vehicle.battery.level() < stranded:
            vehicle.nav.brake()
            return False
        if vehicle.nav.get_speed() <= 0:
            stalled = stalled + 1
            if stalled >= stall_limit:
                vehicle.nav.brake()
                notify(f"{vehicle.name} made no progress toward"
                       f" ({x}, {y}) - blocked?", "warn")
                return False
        else:
            stalled = 0
        sleep(1)

    vehicle.nav.brake()            # near is not stopped; work needs parked
    return True


def go_home(vehicle, throttle=RETURN_THROTTLE, tolerance=HOME_TOLERANCE,
            idle=IDLE_INTERVAL, warn_after=CHARGE_WARN_AFTER):
    """Drive to the docking point and park. True once the outpost counts
    the vehicle as docked, or on arrival when docking cannot be read.

    Arriving within tolerance is not the same as being docked, and only
    docked vehicles get charged. So arrival is checked against the
    outpost's flag, and a parked-but-undocked vehicle is reported rather
    than left looking idle.
    """
    if not drive_to(vehicle, HOME["x"], HOME["y"], throttle, tolerance):
        return False

    sleep(1)                       # telemetry is a snapshot; let it see us parked
    if docked_home(vehicle) is False:
        undocked_warning(vehicle, warn_after)
        sleep(idle)
        return False

    undocked["passes"] = 0
    return True


def in_bounds(x, y, planet):
    """True when the planet accepts this coordinate."""
    if planet is None:
        return True
    return planet.contains(x, y)


# --- waiting ------------------------------------------------------------

waiting = {"passes": 0}

# Charge progress while parked: the best level seen, and how many passes
# it has sat there. Read by ready_to_depart(), cleared by working().
charging = {"level": None, "flat": 0}


def wait_for_charge(vehicle, level, warn_after=CHARGE_WARN_AFTER,
                    idle=IDLE_INTERVAL):
    """Park at base and wait for the battery to come up.

    A parked vehicle costs nothing, so waiting is free. But nothing
    charges a vehicle on its own: it needs a Vehicle Charging Station to
    be parked at, or a rescue drone. If neither exists this wait never
    ends, so say so rather than looking idle forever.
    """
    waiting["passes"] = waiting["passes"] + 1
    if waiting["passes"] == warn_after:
        notify(f"{vehicle.name} docked at {HOME['id']} at"
               f" {round(level * 100)}% and not charging - is the station"
               f" powered, its script running, and power.mode not stuck on"
               f" conserve?", "warn")
    sleep(idle)


def working():
    waiting["passes"] = 0
    charging["level"] = None
    charging["flat"] = 0


# --- departing ----------------------------------------------------------

def is_charging(vehicle) -> bool:
    """True while the station is still working on this vehicle.

    Read through try/except: status() belongs to the vehicle kinds that
    have one, and a kind without it reports "not charging" rather than
    stopping the loop.
    """
    try:
        return vehicle.status() in ["charging", "queued"]
    except Exception:
        return False


def charge_stalled(vehicle, level, passes=STALL_CHARGE_PASSES) -> bool:
    """True once the level has not risen for `passes` consecutive calls.

    A station that is shed, unpowered, or capped at depart level by
    conserve mode never announces itself - from the pad all three look
    like a battery that stopped climbing, so that is what is watched.
    The stored level is a high-water mark, so a dip does not reset it.
    """
    last = charging["level"]
    if last is None or level > last:
        charging["level"] = level
        charging["flat"] = 0
        return False
    charging["flat"] = charging["flat"] + 1
    return charging["flat"] >= passes


def ready_to_depart(vehicle, level, have_target=False,
                    depart=DEPART_FRACTION):
    """True when this vehicle should leave the pad on a new trip.

    Below `depart` it never leaves: that fraction is the floor a trip
    starts from, and it is the whole of the old rule. Above it, leaving
    immediately throws away everything the station was still about to
    put in - fleet.CHARGE_TARGET is a full battery, so departing at the
    floor costs half the range, every trip (D-027).

    Three things end the wait: the station finishing with us, the level
    going flat, or a reachable job already being in range - which is
    worth more than the last few percent.
    """
    if level < depart:
        return False
    if have_target:
        return True
    if charge_stalled(vehicle, level):
        return True
    if is_charging(vehicle):
        return False
    return charging["flat"] > 0        # not charging, and not still climbing


# --- status reporting ---------------------------------------------------

def report(vehicle, state, target=None, extra=None):
    """Broadcast this vehicle's live status for a fleet manager to read.

    A charging station script cannot see a vehicle's battery any other
    way, and the docs suggest exactly this: a manager that dispatches
    rescues before vehicles are dead. `target` names where it is headed,
    so two vehicles working apart is visible from the bus alone. `extra`
    carries what only one kind of vehicle has - a rover's cargo count, a
    Pioneer's job id. Values are kept JSON-safe.
    """
    position = vehicle.nav.get_position()
    payload = {
        "state": state,
        "battery": vehicle.battery.level(),
        "x": position.x,
        "y": position.y,
        "docked": docked_status(vehicle),
        "docked_at": docked_outpost(vehicle),
        "home": HOME["id"],
        "target": target,
    }
    if extra is not None:
        for field in extra:
            payload[field] = extra[field]
    bus.publish(bus.vehicle_channel(vehicle.id), payload)
