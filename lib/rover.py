"""Rover prospecting and mining: sonar sweep, survey, drive, drill, return.

    import rover
    rover.run(self)

`self` does not exist in a library, so the vehicle is passed in. nav, sonar
and drill are all `self.<module>` on the vehicle, so this is one script per
rover rather than machines coordinating - the Signal Bus is used only to
report status outward, not to run the loop.

The rule the whole file is built around, from Vehicle Proximity & Service:
GET NEAR, PARK, THEN WORK. get_distance_to() reaching tolerance means close
enough, NOT stopped, so nav.brake() comes before every scan, survey, drill
or transfer.

Two rovers running this same file stay out of each other's way without a
manager: they start at different points on the waypoint ring, they claim a
site on the bus before driving to it, and what they survey goes into the
Data Archive so either of them can work it after a restart.
"""

import bus
import caps
import earth
import storage

PLANET_ID = "nocturna"
STATION_TYPE = "charging_station"
FLEET_ID = "fleet"

# Where "home" is. (0, 0) is only the coordinate origin; the point that
# matters is the charging station's docking position, resolved by
# find_home() when the script starts. Docked means parked inside the
# outpost footprint plus a ~2 m margin, and a rover parked 3 m short of
# that footprint waits for a charge that never comes.
HOME = {"x": 0, "y": 0, "id": "origin"}

# Arrival tolerance. The docs say a loop needs > 2 rather than exact zero.
ARRIVE_TOLERANCE = 3.0

# Home leg tolerance. Must land inside the ~2 m service margin, so it is
# tighter than the field tolerance.
HOME_TOLERANCE = 1.5

# One throttle for every leg, and a low one. The docs are blunt about this:
# a full Rover at full throttle lasts about 5 hours, at half throttle closer
# to 20. Racing home is what strands a rover, not dawdling. Using a single
# value also keeps the learned Wh/metre figure meaningful - a blend of two
# throttles would describe neither.
CRUISE_THROTTLE = 0.5
RETURN_THROTTLE = 0.5

# Battery fractions. Below 0.1 the docs call the rover close to stranded.
RESERVE_FRACTION = 0.30        # stop mining, head home
STRANDED_FRACTION = 0.12       # stop everything, call for help
DEPART_FRACTION = 0.45         # do not start a new trip below this

# Learned energy cost, persisted once Data Archive exists.
DRAIN_KEY = "rover.wh_per_meter"
DEFAULT_DRAIN = 0.05           # Wh per meter, deliberately pessimistic
DRAIN_SMOOTHING = 0.3          # weight of each new sample

# Surveyed sites, kept across restarts: {site_id: record}.
SITES_KEY = "rover.sites"

# How long a published claim on a site counts as held, in simulation
# seconds. Long enough to cover the drive out and a full hold, short
# enough that a rover stopped mid-trip frees the site on its own.
CLAIM_TTL = 600

# A drive that makes no progress for this many checks is treated as blocked.
STALL_LIMIT = 20

# Seconds to wait between passes when there is nothing to do.
IDLE_INTERVAL = 10

# Passes to wait at base before pointing out that nothing is charging us.
CHARGE_WARN_AFTER = 30

# scan() statuses that mean "the sweep finished but something out there is
# beyond this sonar". The sweep is not a failure and .sites is not readable.
SWEEP_PARTIAL = ["too_hard", "tier_too_low", "research_required",
                 "wrong_scanner"]


# --- learned drive cost -------------------------------------------------

def _notebook():
    """The Data Archive component, or None before that research."""
    return get_component("notebook")


def drain_per_meter():
    """Learned Wh cost per meter driven, or a pessimistic default.

    Being wrong high is cheap: the rover comes home early. Being wrong low
    strands it in the field, so the default errs high until real samples
    replace it.
    """
    book = _notebook()
    if book is None:
        return bus.read(DRAIN_KEY, DEFAULT_DRAIN)
    return book.get(DRAIN_KEY, DEFAULT_DRAIN)


def record_drain(wh_used, meters):
    """Fold one observed trip into the running estimate."""
    if meters <= 0 or wh_used <= 0:
        return
    sample = wh_used / meters
    blended = (drain_per_meter() * (1 - DRAIN_SMOOTHING)
               + sample * DRAIN_SMOOTHING)
    book = _notebook()
    if book is not None:
        book.set(DRAIN_KEY, blended)
    bus.publish(DRAIN_KEY, blended)


def can_reach_and_return(vehicle, x, y):
    """True when the current charge covers the round trip with reserve left."""
    distance = vehicle.nav.get_distance_to(x, y)
    home_leg = _distance_between(x, y, HOME["x"], HOME["y"])
    needed = (distance + home_leg) * drain_per_meter()
    usable = vehicle.battery.wh() - (vehicle.battery.capacity()
                                     * RESERVE_FRACTION)
    return usable > needed


def _distance_between(ax, ay, bx, by):
    return sqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by))


# --- home and docking ---------------------------------------------------

def find_home(vehicle):
    """Resolve HOME to the nearest charging station's docking point.

    The docs say to target a building's own position for at-building
    work rather than the outpost footprint anchor, and charging is
    at-building work. Every outpost is searched and the station nearest
    the rover's current position wins, so a rover working out of a
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
        return

    network = caps.component("outpost_network")
    if network is not None:
        outpost = network.home()
        coords = outpost.coords()
        HOME["x"] = coords[0]
        HOME["y"] = coords[1]
        HOME["id"] = outpost.id
        notify("No Vehicle Charging Station found - homing on the outpost"
               " anchor instead", "warn")
        return

    notify("No outpost network readable - homing on (0, 0)", "warn")


def docked_status(vehicle):
    """The outpost's own verdict on whether this rover is docked.

    This is the exact flag the charging station's manager gates on, read
    from fleet telemetry. None when it cannot be read, in which case the
    caller falls back to distance.
    """
    component = get_component(FLEET_ID)
    if component is None:
        return None
    for ref in component.vehicles():
        if ref.id == vehicle.id:
            return ref.is_docked
    return None


def at_home(vehicle):
    """True when this rover counts as docked, or is within HOME_TOLERANCE
    of the docking point if docking cannot be read."""
    docked = docked_status(vehicle)
    if docked is not None:
        return docked
    return vehicle.nav.get_distance_to(HOME["x"], HOME["y"]) <= HOME_TOLERANCE


undocked = {"passes": 0}


def undocked_warning(vehicle):
    """Parked on the home target but the outpost says not docked. The home
    coordinate is wrong, or the station sits outside the footprint - say
    so once, not every pass."""
    undocked["passes"] = undocked["passes"] + 1
    if undocked["passes"] % CHARGE_WARN_AFTER != 1:
        return
    position = vehicle.nav.get_position()
    notify(f"Rover parked at ({round(position.x, 1)}, {round(position.y, 1)})"
           f" targeting {HOME['id']} at ({round(HOME['x'], 1)},"
           f" {round(HOME['y'], 1)}) but the outpost does not count it as"
           f" docked - it will not be charged here", "warn")


# --- movement -----------------------------------------------------------

def drive_to(vehicle, x, y, throttle=CRUISE_THROTTLE,
             tolerance=ARRIVE_TOLERANCE):
    """Drive to (x, y) and PARK there. True on arrival.

    Returns False without parking when the battery falls to the stranding
    floor or the rover stops making progress - the caller decides what to
    do about it, because "head home" and "give up here" are different.
    """
    targeted = vehicle.nav.set_target(x, y)
    if targeted.status != "ok":
        notify(f"nav.set_target({x}, {y}): {targeted.message}", "warn")
        return False

    vehicle.nav.set_throttle(throttle)
    stalled = 0

    while vehicle.nav.get_distance_to(x, y) > tolerance:
        if vehicle.battery.level() < STRANDED_FRACTION:
            vehicle.nav.brake()
            return False
        if vehicle.nav.get_speed() <= 0:
            stalled = stalled + 1
            if stalled >= STALL_LIMIT:
                vehicle.nav.brake()
                notify(f"Rover made no progress toward ({x}, {y}) - blocked?",
                       "warn")
                return False
        else:
            stalled = 0
        sleep(1)

    vehicle.nav.brake()            # near is not stopped; work needs parked
    return True


def go_home(vehicle):
    """Drive to the docking point and park. True once the outpost counts
    the rover as docked, or on arrival when docking cannot be read.

    Arriving within tolerance is not the same as being docked, and only
    docked vehicles get charged. So arrival is checked against the
    outpost's flag, and a parked-but-undocked rover is reported rather
    than left looking idle.
    """
    if not drive_to(vehicle, HOME["x"], HOME["y"], RETURN_THROTTLE,
                    HOME_TOLERANCE):
        return False

    sleep(1)                       # telemetry is a snapshot; let it see us parked
    if docked_status(vehicle) is False:
        undocked_warning(vehicle)
        sleep(IDLE_INTERVAL)
        return False

    undocked["passes"] = 0
    return True


# --- prospecting memory -------------------------------------------------

# What the next archive transaction should write. The updater handed to
# notebook.transaction() must be a small pure function of the stored
# value, so the record it is to merge is passed in module state rather
# than captured.
pending = {"id": None, "record": None}

# Sites already worked this session, so the rover moves on to new ground
# instead of re-picking its best remembered site every pass.
visited = {}


def record_of(site):
    """A flat, JSON-safe snapshot of one surveyed mineral site.

    Site objects do not survive a restart, and their revealed fields are
    only real on the object survey() handed back, so everything worth
    keeping is copied out now.
    """
    return {
        "id": site.id,
        "name": site.name,
        "x": site.x,
        "y": site.y,
        "item_id": site.item_id,
        "hardness": site.hardness,
        "purity": site.purity,
        "exhausted": False,
    }


def _merge(stored):
    """Updater: add or refresh one record.

    A site already written off stays written off. Sonar re-finds a
    worked-out deposit's coordinates on every later sweep, and letting a
    fresh survey overwrite the record would send rovers back to it for
    the rest of the game.
    """
    if stored is None:
        stored = {}
    known = stored.get(pending["id"], None)
    if known is not None and known.get("exhausted", False):
        return stored
    stored[pending["id"]] = pending["record"]
    return stored


def _exhaust(stored):
    """Updater: flag one record worked out."""
    if stored is None:
        stored = {}
    known = stored.get(pending["id"], None)
    if known is None:
        return stored
    known["exhausted"] = True
    stored[pending["id"]] = known
    return stored


def _write_sites(updater, what):
    """Run one archive transaction, or do nothing before that research."""
    book = _notebook()
    if book is None:
        return
    result = book.transaction(SITES_KEY, {}, updater)
    if result.status != "ok":
        print(f"transaction {SITES_KEY} ({what}): {result.message}")


def remember(record):
    """Persist a surveyed site so the other rover, and this one after a
    restart, can go straight to it without a sweep."""
    pending["id"] = record["id"]
    pending["record"] = record
    _write_sites(_merge, "remember")


def forget(record):
    """Mark a site worked out, so nothing drives back to it."""
    record["exhausted"] = True
    pending["id"] = record["id"]
    _write_sites(_exhaust, "exhausted")


def known_sites():
    """Every remembered site still worth a visit, read fresh.

    Re-read rather than cached: the other rover is writing to the same
    key, so a cached list would miss its finds and keep sending this
    rover to deposits it has already worked out.
    """
    book = _notebook()
    if book is None:
        return []
    stored = book.get(SITES_KEY, {})
    if not isinstance(stored, dict):
        return []

    records = []
    for site_id in stored:
        record = stored[site_id]
        if not isinstance(record, dict):
            continue
        if record.get("exhausted", False):
            continue
        if visited.get(site_id, False):
            continue
        records.append(record)
    return records


# --- claims -------------------------------------------------------------

def claim_holder(site_id):
    """The rover id holding a fresh claim on this site, or None.

    A claim is a broadcast that ages out rather than a queued job. There
    is nothing to pre-fill, and a rover that stops mid-trip cannot leave
    a deposit locked forever.
    """
    return bus.read_fresh(bus.claim_channel(site_id), CLAIM_TTL, None)


def claimed_by_other(vehicle, site_id) -> bool:
    holder = claim_holder(site_id)
    return holder is not None and holder != vehicle.id


def claim(vehicle, site_id):
    """Take, or refresh, this rover's claim on a site."""
    bus.publish(bus.claim_channel(site_id), vehicle.id)


def release(site_id):
    """Hand a claim back, channel and all - see bus.clear()."""
    bus.clear(bus.claim_channel(site_id))


# --- prospecting --------------------------------------------------------

def prospect_points(sonar_range, rings=3):
    """Waypoints on expanding rings, spaced so consecutive sweeps overlap.

    Sonar is a point sweep, so the rover has to physically cover ground.
    Ring spacing equals the sonar range: a sweep at radius R covers out to
    R in every direction, so rings one range apart overlap instead of
    leaving an unswept annulus. Home is swept first, otherwise the disc
    around the base itself is never covered.
    """
    step = sonar_range
    points = [(HOME["x"], HOME["y"])]
    for ring in range(1, rings + 1):
        radius = step * ring
        count = 4 * ring
        for i in range(count):
            angle = 2 * 3.141592653589793 * i / count
            points.append((HOME["x"] + radius * cos(angle),
                           HOME["y"] + radius * sin(angle)))
    return points


def rover_number(vehicle_id):
    """The trailing number in a vehicle id ("rover_2" -> 2), or 1."""
    parts = vehicle_id.split("_")
    tail = parts[len(parts) - 1]
    if tail.isdigit():
        return int(tail)
    return 1


def rover_count():
    """How many rovers the fleet owns. Counted, never assumed - a third
    rover bought later has to spread the ring without a code change."""
    component = get_component(FLEET_ID)
    if component is None:
        return 2
    count = 0
    for ref in component.vehicles():
        if ref.kind == "rover":
            count = count + 1
    if count < 1:
        return 1
    return count


def ring_offset(vehicle, total_points):
    """Where on the waypoint ring this rover starts.

    Both rovers walking one list from index 0 sweep the same ground in
    the same order and survey the same deposits. Spacing the starts
    evenly by the id's trailing number puts rover_2 half a rotation from
    rover_1, and three rovers a third apart, with neither of them being
    told the other exists.
    """
    if total_points <= 0:
        return 0
    place = rover_number(vehicle.id) - 1
    return (place * total_points // rover_count()) % total_points


def in_bounds(x, y, planet):
    """True when the planet accepts this coordinate."""
    if planet is None:
        return True
    return planet.contains(x, y)


def sweep(vehicle):
    """Sonar sweep from where the rover is parked. Returns a list of sites.

    Only "ok" documents the .sites payload, so partial statuses return an
    empty list rather than reading a field that is not promised.
    """
    result = vehicle.sonar.scan()

    if result.status == "ok":
        return result.sites
    if result.status in SWEEP_PARTIAL:
        # The sweep completed; something nearby is beyond this sonar's
        # tier, hardness limit, or unlocked research. Not an error.
        return []
    if result.status == "busy":
        sleep(1)
        return []
    if result.status == "no_power":
        return []
    notify(f"sonar.scan: {result.message}", "warn")
    return []


def survey(vehicle, site):
    """Survey `site` and return the FRESH site object, or None.

    Mining-site fields are snapshots: the pre-survey object keeps its
    hidden values forever, so the object survey() returns is the only one
    with real item_id, hardness and purity on it.
    """
    result = vehicle.sonar.survey(site)
    if result.status == "ok":
        return result.site
    if result.status == "busy":
        sleep(1)
        return None
    if result.status in ["tier_too_low", "too_hard", "research_required",
                         "out_of_range", "not_discovered", "no_power"]:
        return None
    notify(f"sonar.survey: {result.message}", "warn")
    return None


def is_minable(record, hardness_limit):
    """True when a remembered site is ore this drill can still work.

    Records are only ever written from a surveyed mineral site, so what
    is left to check is the drill's reach and whether the deposit is
    still there.
    """
    if record is None:
        return False
    if record.get("exhausted", False):
        return False
    if record.get("item_id", None) is None:
        return False
    hardness = record.get("hardness", None)
    if hardness is None:
        return False
    return hardness <= hardness_limit


# Weight given to a site whose ore a live Earth Order is waiting for.
# Sized to outrank purity: a standard-grade site of the ore Earth wants
# beats a pure site of something nothing is asking for.
EARTH_DEMAND_BONUS = 2500


def score(record, vehicle, wanted_by_earth={}):
    """Rank surveyed sites: wanted beats rich, rich beats close.

    Purity is a straight yield multiplier, so it outweighs a longer drive
    up to a point; distance breaks ties between equal grades. Earth demand
    outranks both - ore nobody is waiting for just fills a crate.
    """
    grade = 1
    if record["purity"] == "rich":
        grade = 2
    elif record["purity"] == "pure":
        grade = 3

    value = grade * 1000 - vehicle.nav.get_distance_to(record["x"],
                                                       record["y"])
    if wanted_by_earth.get(record["item_id"], 0) > 0:
        value = value + EARTH_DEMAND_BONUS
    return value


def best_site(vehicle, records, hardness_limit):
    """Highest-scoring reachable, unclaimed, minable site, or None.

    A site the other rover has claimed is skipped rather than queued for:
    it will have been mined by the time this rover could get there, and
    the ground it has not swept is worth more than a shared deposit.
    """
    # Read the demand once rather than per candidate site.
    wanted_by_earth = earth.demand()

    best = None
    best_score = 0
    for record in records:
        if not is_minable(record, hardness_limit):
            continue
        if claimed_by_other(vehicle, record["id"]):
            continue
        if not can_reach_and_return(vehicle, record["x"], record["y"]):
            continue
        value = score(record, vehicle, wanted_by_earth)
        if best is None or value > best_score:
            best = record
            best_score = value
    return best


# --- mining -------------------------------------------------------------

def mine_out(vehicle, record):
    """Drill until the hold is full, the site refuses, or reserve is hit.

    The rover must already be parked at the site. Returns
    {"mined": units, "gone": True when the deposit is no longer there}.

    mine() has no site-empty outcome: a worked-out deposit simply stops
    being a site, and the next call reports "not_at_site". That means
    exhausted only once this visit has pulled a unit out of the ground -
    before that, the identical status means the rover parked short.

    The claim is refreshed on every unit. A full hold takes longer than
    a claim stays fresh, and a claim that expires under the drill is an
    invitation for the other rover to drive to the same deposit.
    """
    mined = 0
    gone = False

    while not vehicle.cargo.full():
        if vehicle.battery.level() < RESERVE_FRACTION:
            break

        claim(vehicle, record["id"])
        result = vehicle.drill.mine()

        if result.status == "ok":
            mined = mined + 1
        elif result.status in ["busy", "no_power"]:
            sleep(1)                       # transient, per the outcome table
        elif result.status == "no_cargo_space":
            break
        elif result.status == "not_at_site" and mined > 0:
            gone = True
            break
        elif result.status in ["not_at_site", "not_surveyed", "too_hard",
                               "not_enough_power", "not_mounted"]:
            notify(f"drill.mine at {record['name']}: {result.message}", "warn")
            break
        else:
            notify(f"drill.mine: {result.message}", "warn")
            break

    return {"mined": mined, "gone": gone}


def work_site(vehicle, record):
    """Claim a site, drive to it, mine it out, hand the claim back.

    The claim goes up before the drive, not on arrival: the other rover
    has to see the site taken while this one is still on its way, or
    both spend the trip converging on the same deposit.

    The site counts as visited from the moment it is picked, including
    when the drive fails. A blocked route does not become unblocked by
    trying it again immediately, and this rover would otherwise pick the
    same unreachable deposit every pass instead of prospecting.
    """
    site_id = record["id"]
    visited[site_id] = True
    claim(vehicle, site_id)

    report(vehicle, "driving", site_id)
    if not drive_to(vehicle, record["x"], record["y"]):
        release(site_id)
        return 0

    report(vehicle, "drilling", site_id)
    outcome = mine_out(vehicle, record)

    if outcome["gone"]:
        forget(record)
        notify(f"{record['name']} is worked out")
    release(site_id)

    if outcome["mined"] > 0:
        notify(f"Mined {outcome['mined']} x {record['item_id']}"
               f" ({record['purity']}) at {record['name']}")
    return outcome["mined"]


def unload(vehicle, sinks):
    """Empty the hold into the storage bank. The rover must be parked there.

    `sinks` is a list of bin ids (or a single id). Each stack is routed
    separately, because a bin holds one material at a time and a mixed
    hold would otherwise jam against the first bin's latch.

    Requires Auto Feeders research: without it self.output does not exist
    and the hold has to be emptied by hand. Returns True when the hold is
    empty afterwards.
    """
    if vehicle.cargo.count() <= 0:
        return True

    targets = sinks
    if not isinstance(targets, list):
        targets = [targets]

    for stack in vehicle.output.stacks():
        destination = storage.sink_for(targets, stack.id, 1)
        if destination is None:
            # "Nothing accepts it" has two very different causes and they
            # look identical from here. Say which one it is.
            absent = storage.missing(targets)
            if len(absent) == len(targets):
                notify(f"Cannot unload {stack.id}: none of {targets} exist."
                       f" Storage bins found: {storage.discover()}", "warn")
            else:
                notify(f"Nowhere to put {stack.id} - every store is full"
                       f" or latched to another material", "warn")
            continue

        if vehicle.output.connected_to() != destination:
            linked = vehicle.output.connect(destination)
            if linked.status != "ok":
                notify(f"rover output -> {destination}: {linked.message}",
                       "warn")
                continue

        sent = vehicle.output.send(stack.id, stack.count,
                                   stack.properties, "exact")
        if sent.status not in ["ok", "partial", "no_op"]:
            print(f"output.send {stack.id}: {sent.message}")

    return vehicle.cargo.count() <= 0


# --- waiting ------------------------------------------------------------

waiting = {"passes": 0}


def wait_for_charge(vehicle, level):
    """Park at base and wait for the battery to come up.

    A parked vehicle costs nothing, so waiting is free. But nothing
    charges a rover on its own: it needs a Vehicle Charging Station to be
    parked at, or a rescue drone. If neither exists this wait never ends,
    so say so rather than looking idle forever.
    """
    waiting["passes"] = waiting["passes"] + 1
    if waiting["passes"] == CHARGE_WARN_AFTER:
        notify(f"Rover docked at {HOME['id']} at {round(level * 100)}% and"
               f" not charging - is the station powered, its script running,"
               f" and power.mode not stuck on conserve?", "warn")
    sleep(IDLE_INTERVAL)


def working():
    waiting["passes"] = 0


# --- status reporting ---------------------------------------------------

def report(vehicle, state, target=None):
    """Broadcast this rover's live status for a fleet manager to read.

    A charging station script cannot see a rover's battery any other way,
    and the docs suggest exactly this: a manager that dispatches rescues
    before vehicles are dead. `target` names the site being driven to or
    drilled, so two rovers working apart is visible from the bus alone.
    Values are kept JSON-safe.
    """
    position = vehicle.nav.get_position()
    bus.publish(bus.rover_channel(vehicle.id), {
        "state": state,
        "battery": vehicle.battery.level(),
        "cargo": vehicle.cargo.count(),
        "x": position.x,
        "y": position.y,
        "docked": docked_status(vehicle),
        "home": HOME["id"],
        "target": target,
    })


# --- main loop ----------------------------------------------------------

def run(vehicle, sink=None, rings=3, start_index=None):
    """Prospect, mine and return home, forever.

    vehicle      the rover this script runs inside - pass `self`
    sink         where to unload: a store id, or a LIST of them to route
                 across. "inventory" works only while parked at home.
                 Leave it None to discover every Storage Bin automatically -
                 which beats hardcoding, because component ids are not
                 guessable and a wrong one looks exactly like a full bank.
    rings        how many expanding rings of prospecting waypoints to walk
    start_index  where on that ring to start. Leave it None and the rover
                 works it out from its own id, so two rovers sweep
                 different ground without being configured to.
    """
    if sink is None:
        sink = storage.discover()
        if len(sink) == 0:
            sink = [storage.INVENTORY]
        notify(f"Rover unloading to: {sink}")
    else:
        if not isinstance(sink, list):
            sink = [sink]
        sink = storage.check(sink, "Rover unload")
        if len(sink) == 0:
            sink = [storage.INVENTORY]
            notify("Rover falling back to base Inventory", "warn")

    find_home(vehicle)
    planet = get_component(PLANET_ID)
    hardness_limit = vehicle.drill.hardness_limit()
    waypoints = prospect_points(vehicle.sonar.range(), rings)
    if start_index is None:
        start_index = ring_offset(vehicle, len(waypoints))

    caps.report("Rover online", caps.common() + [
        ("fleet", get_component(FLEET_ID) is not None),
        ("planet", planet is not None),
        ("drill hardness", hardness_limit),
        ("sonar", f"{vehicle.sonar.range()}m {vehicle.sonar.tier()}"),
        ("home", f"{HOME['id']} at ({round(HOME['x'], 1)},"
                 f" {round(HOME['y'], 1)})"),
        ("remembered sites", len(known_sites())),
        ("first waypoint", f"{start_index} of {len(waypoints)}"),
    ])

    index = start_index

    while True:

        level = vehicle.battery.level()
        home = at_home(vehicle)

        # --- stranded in the field ---------------------------------------
        # Only in the field. A flat rover sitting at base is not stranded,
        # it is waiting for a charge, and calling that "stranded" would
        # send a rescue drone to a vehicle already parked at the station.
        if level < STRANDED_FRACTION and not home:
            report(vehicle, "stranded")
            notify("Rover battery critical - needs a rescue dispatch", "warn")
            sleep(IDLE_INTERVAL)
            continue

        # --- hold full, or too low to keep working -------------------------
        if vehicle.cargo.full() or level < RESERVE_FRACTION:

            if not home:
                report(vehicle, "returning")
                go_home(vehicle)
                continue               # re-evaluate from the top on arrival

            if vehicle.cargo.count() > 0:
                report(vehicle, "unloading")
                if not unload(vehicle, sink):
                    sleep(IDLE_INTERVAL)
                continue

            # Home, empty, and low on charge. There is nothing left to do
            # but wait. This branch is why the loop needs an explicit stop:
            # go_home() and unload() BOTH return immediately once they are
            # no-ops, so without it the loop spins between "returning" and
            # "unloading" forever without ever sleeping.
            report(vehicle, "waiting_for_charge")
            wait_for_charge(vehicle, level)
            continue

        # --- not enough to start a new trip --------------------------------
        if level < DEPART_FRACTION:
            if not home:
                # Waiting to charge only makes sense at the base. Sitting
                # in the field waiting for a charge that cannot arrive is
                # how a rover quietly stops working forever.
                report(vehicle, "returning")
                go_home(vehicle)
                continue
            report(vehicle, "waiting_for_charge")
            wait_for_charge(vehicle, level)
            continue

        # --- a site we already know beats sweeping for a new one -----------
        # Prospecting is what this rover does when it has nothing better
        # to mine. After a restart the archive usually holds a surveyed
        # deposit, and driving to that is worth more than re-sweeping
        # ground the fleet has already covered.
        working()
        remembered = best_site(vehicle, known_sites(), hardness_limit)
        if remembered is not None:
            work_site(vehicle, remembered)
            continue

        # --- prospect ------------------------------------------------------
        (x, y) = waypoints[index]
        index = (index + 1) % len(waypoints)

        if not in_bounds(x, y, planet):
            continue

        report(vehicle, "prospecting")
        start_wh = vehicle.battery.wh()
        distance = vehicle.nav.get_distance_to(x, y)

        if not drive_to(vehicle, x, y):
            continue

        record_drain(start_wh - vehicle.battery.wh(), distance)

        report(vehicle, "scanning")
        contacts = sweep(vehicle)
        if len(contacts) == 0:
            continue

        # --- survey what we found ------------------------------------------
        # Everything surveyed is written down, not just the one that gets
        # mined: the rest is work for the other rover, and for this one
        # after the next restart.
        report(vehicle, "surveying")
        surveyed = []
        for contact in contacts:
            if contact.kind() != "mineral":
                continue
            fresh = survey(vehicle, contact)
            if fresh is None:
                continue
            if not fresh.surveyed:
                continue
            found = record_of(fresh)
            remember(found)
            surveyed.append(found)

        target = best_site(vehicle, surveyed, hardness_limit)
        if target is None:
            continue

        # --- go mine it ----------------------------------------------------
        work_site(vehicle, target)