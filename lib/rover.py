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

Driving, parking, docking and the learned Wh-per-meter figure are not rover
work and live in `lib/vehicle.py`, which the Pioneer shares. What is left
here is prospecting: sweep, survey, score, claim, drill, unload.
"""

import bus
import caps
import recipes
import storage
import vehicle
# Named import, not `import survey`: this file already has its own
# survey() and the module would be shadowed by it everywhere below.
from survey import surveyed
from vehicle import HOME, RESERVE_FRACTION, STRANDED_FRACTION,DEPART_FRACTION, IDLE_INTERVAL, at_home,can_reach_and_return, drive_to, find_home, go_home,in_bounds, ready_to_depart, record_drain,wait_for_charge, working

PLANET_ID = "nocturna"
FLEET_ID = "fleet"

# Surveyed sites, kept across restarts: {site_id: record}.
SITES_KEY = "rover.sites"

# How long a published claim on a site counts as held, in simulation
# seconds. Long enough to cover the drive out and a full hold, short
# enough that a rover stopped mid-trip frees the site on its own.
CLAIM_TTL = 600

# scan() statuses that mean "the sweep finished but something out there is
# beyond this sonar". The sweep is not a failure and .sites is not readable.
SWEEP_PARTIAL = ["too_hard", "tier_too_low", "research_required",
                 "wrong_scanner"]


# --- prospecting memory -------------------------------------------------

def _notebook():
    """The Data Archive component, or None before that research."""
    return get_component("notebook")


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
    """Every known mineral site still worth a visit, read fresh.

    Two sources, one list, same record shape. The Journal is planet-wide
    and restart-safe, so a deposit surveyed by anything - the other
    rover, a scouting Pioneer, a sweep from three sessions ago - reaches
    this rover through it (D-010). The archive holds the one thing the
    Journal cannot, our own `exhausted` flag, so where both describe the
    same site the archived record wins: a worked-out deposit is still
    surveyed, and the Journal would happily offer it back forever.

    Re-read rather than cached: the other rover is writing to the same
    key, so a cached list would miss its finds and keep sending this
    rover to deposits it has already worked out.
    """
    stored = {}
    book = _notebook()
    if book is not None:
        archived = book.get(SITES_KEY, {})
        if isinstance(archived, dict):
            stored = archived

    records = []
    archived_ids = {}
    for site_id in stored:
        record = stored[site_id]
        if not isinstance(record, dict):
            continue
        archived_ids[site_id] = True
        if record.get("exhausted", False):
            continue
        if visited.get(site_id, False):
            continue
        records.append(record)

    for record in surveyed("mineral"):
        site_id = record["id"]
        if archived_ids.get(site_id, False):
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

    `wanted_by_earth` is RAW material, keyed by ore id - what the orders
    resolve to once the recipe graph has walked them back (D-025). Handed
    the finished goods an order literally names, this test can never fire.
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
    wanted_by_earth = recipes.raw_demand()

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
    {"mined": units, "gone": True when the deposit is no longer there,
    "full": True when WE stopped rather than the deposit}.

    `full` is what tells a rich deposit apart from a dead one. Running out
    of hold or of charge says nothing about the ore still in the ground,
    and the site has to stay on the list to be worked again (D-026).

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
    full = False

    while not vehicle.cargo.full():
        if vehicle.battery.level() < RESERVE_FRACTION:
            full = True
            break

        claim(vehicle, record["id"])
        result = vehicle.drill.mine()

        if result.status == "ok":
            mined = mined + 1
        elif result.status in ["busy", "no_power"]:
            sleep(1)                       # transient, per the outcome table
        elif result.status == "no_cargo_space":
            full = True
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

    if vehicle.cargo.full():
        full = True
    return {"mined": mined, "gone": gone, "full": full}


def work_site(vehicle, record):
    """Claim a site, drive to it, mine it out, hand the claim back.

    The claim goes up before the drive, not on arrival: the other rover
    has to see the site taken while this one is still on its way, or
    both spend the trip converging on the same deposit.

    The site counts as visited from the moment it is picked, including
    when the drive fails. A blocked route does not become unblocked by
    trying it again immediately, and this rover would otherwise pick the
    same unreachable deposit every pass instead of prospecting.

    That mark means "do not retry right now", not "never again" (D-026).
    A visit that brought ore back and ended because the hold filled or
    the charge ran down clears it: the ore is still in the ground and the
    rover is about to be empty again. A visit that produced nothing keeps
    it, exactly like a failed drive - including the rover that reached a
    good deposit on its last few percent, which would otherwise re-pick
    that same site every time it charged and never drill a single unit.
    Only exhaustion is permanent, and that goes through forget(), which
    the archive remembers.
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
    elif outcome["full"] and outcome["mined"] > 0:
        visited.pop(site_id, None)
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


# --- status reporting ---------------------------------------------------

def report(rover, state, target=None):
    """Broadcast this rover's live status for a fleet manager to read.

    The shared fields are the vehicle layer's; the hold is the one thing
    only a rover has, so it rides along as `extra`. `target` names the
    site being driven to or drilled, so two rovers working apart is
    visible from the bus alone. Values are kept JSON-safe.

    Taking the rover as `rover` rather than `vehicle` is what lets this
    one function still reach the `vehicle` module it delegates to.
    """
    vehicle.report(rover, state, target, {"cargo": rover.cargo.count()})


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
        ("wanted ore", list(recipes.raw_demand())),
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

        # --- is there a job worth leaving for? -----------------------------
        # Prospecting is what this rover does when it has nothing better
        # to mine. After a restart the archive usually holds a surveyed
        # deposit, and driving to that is worth more than re-sweeping
        # ground the fleet has already covered.
        #
        # Picked BEFORE the depart gate, because "a site came back" is the
        # gate's have_target: best_site() has already filtered through
        # can_reach_and_return, so a site in hand means the round trip
        # fits on the charge we are sitting on. The site is then worked
        # rather than re-picked, so this costs no extra archive reads.
        remembered = best_site(vehicle, known_sites(), hardness_limit)

        # --- not enough to start a new trip --------------------------------
        # Two different questions, and only one of them is about the
        # station. In the field the floor is the whole rule, as it always
        # was: a rover already out there with charge to spare carries on
        # working rather than driving home to be topped up. On the pad the
        # station's own signal decides, because that is the only place the
        # signal means anything (D-027).
        if not home:
            if level < DEPART_FRACTION:
                # Waiting to charge only makes sense at the base. Sitting
                # in the field waiting for a charge that cannot arrive is
                # how a rover quietly stops working forever.
                report(vehicle, "returning")
                go_home(vehicle)
                continue
        elif not ready_to_depart(vehicle, level, remembered is not None):
            report(vehicle, "waiting_for_charge")
            wait_for_charge(vehicle, level)
            continue

        working()
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

        record_drain(vehicle, start_wh - vehicle.battery.wh(), distance)

        report(vehicle, "scanning")
        contacts = sweep(vehicle)
        if len(contacts) == 0:
            continue

        # --- survey what we found ------------------------------------------
        # Everything surveyed is written down, not just the one that gets
        # mined: the rest is work for the other rover, and for this one
        # after the next restart.
        report(vehicle, "surveying")
        resolved = []
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
            resolved.append(found)

        target = best_site(vehicle, resolved, hardness_limit)
        if target is None:
            continue

        # --- go mine it ----------------------------------------------------
        work_site(vehicle, target)