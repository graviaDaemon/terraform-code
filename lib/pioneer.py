"""Pioneer construction: drain the shared blueprint queue, forever.

    import pioneer
    pioneer.found_outpost(x, y)
    pioneer.run(self)

`self` does not exist in a library, so the Pioneer is passed in. Driving,
parking, docking and the learned Wh-per-meter figure are the vehicle
layer's job; what is left here is construction.

This file does not know what an outpost is. Every job in the queue
carries its own `.required_item` and `.required_count`, and the manual is
explicit that those fields - never `.kind` - say what to load. That one
rule is the whole of the generic worker: the same loop that founds an
outpost builds pipes, power lines, bridges, drills and deconstruction
jobs without a line of new code (D-014).

Founding the outpost is therefore one job in that queue, not a mode. Its
coordinates come from a human reading the scout's shortlist, because
there is exactly one Outpost Kit and an outpost cannot be moved (D-013).

Interruptions are normal and recoverable. Stop, power loss, leaving the
site, a rescue or unmounting the Constructor Module pauses paid work
without losing progress or materials, and the owning Pioneer can rejoin
it - including after a save/load. So `paused_constructions()` is the
FIRST list read every pass, most-completed first: that is where material
already spent is sitting.
"""

import caps
import vehicle
from vehicle import CRUISE_THROTTLE, DEPART_FRACTION, HOME, IDLE_INTERVAL, RESERVE_FRACTION, STRANDED_FRACTION, at_home, can_reach_and_return, drive_to, find_home, go_home, ready_to_depart, record_drain, wait_for_charge, working

BLUEPRINT_ID = "construction_blueprint"
NETWORK_ID = "outpost_network"
OUTPOST_KIND = "outpost"

# execute() statuses that mean "ask again next pass". None of these is a
# failure: the module is mid-action, the subnet is dark, or the job is
# already running - all of which resolve on their own.
RETRY = ["busy", "already_active", "paused", "paused_no_power", "not_ready"]

# Second approach after "wrong_position". ARRIVE_TOLERANCE means "close
# enough to have arrived", which is not the same as "inside interaction
# range", so the retry parks harder rather than giving up.
CLOSE_TOLERANCE = 1.0

# How far a founded outpost's anchor may sit from the coordinate that was
# asked for and still be the same outpost. plan_structure() snaps an
# Outpost to its footprint anchor, so the two are never exactly equal.
SAME_SITE = 30.0

# Times a job may come back "insufficient_materials" before it is left
# alone. Once is a stale cargo read and worth a reload; twice means the
# queue's numbers and the bins disagree, which a retry cannot fix.
SHORT_LIMIT = 2


def queue():
    """The Construction Blueprint component, or None before that research."""
    return caps.component(BLUEPRINT_ID)


def constructor(pioneer):
    """The mounted Constructor Module, or None when the rig has no builder."""
    try:
        return pioneer.constructor
    except Exception:
        return None


# Messages already said. A shortfall that recurs every pass is the same
# fact, not news.
told = {"messages": []}


def _once(message, level="warn"):
    """Say `message` exactly once per session."""
    if message in told["messages"]:
        return
    told["messages"].append(message)
    notify(message, level)


# --- what a job asks for ------------------------------------------------

def needs(job):
    """`(item_id, count)` this job wants in cargo, or `(None, 0)`.

    None is a real answer, not a gap: deconstruction returns parts rather
    than consuming them, and a paused job that has already started has
    its material sunk into the site. Both build with an empty hold.
    """
    if job.required_item is None:
        return (None, 0)
    return (job.required_item, job.required_count)


def carried(pioneer, item_id) -> int:
    """Units of `item_id` aboard, summed across every bin."""
    total = 0
    for stack in pioneer.cargo.stacks():
        if stack.id == item_id:
            total = total + stack.count
    return total


def have(pioneer, item_id, count) -> bool:
    """True when the hold already covers this job's requirement."""
    if item_id is None:
        return True
    return carried(pioneer, item_id) >= count


def room_for(pioneer, item_id) -> int:
    """Units of `item_id` the installed bins can still take.

    Not `cargo.full()`, which asks whether every bin is full. A Portable
    Bin latches to ONE item id, so a rack holding a half-empty bin of
    iron ore has no room at all for an Outpost Kit - the space is real
    and unusable. Only empty bins and bins already latched to this item
    are counted.
    """
    space = 0
    for rack in pioneer.cargo.racks():
        for hold in rack.bins:
            if hold is None:
                continue
            if hold.item_id is not None and hold.item_id != item_id:
                continue
            space = space + (hold.capacity - hold.count)
    return space


# --- loading ------------------------------------------------------------

def dock_station():
    """The BuildingRef of the charging station HOME resolved to, or None."""
    for ref in caps.buildings(vehicle.STATION_TYPE):
        if ref.id == HOME["id"]:
            return ref
    return None


def source_store():
    """What to load from while parked at HOME, or None.

    `caps.local_store()` answers this for anything that knows its own
    outpost, and a vehicle is exactly the thing that does not - a Pioneer
    has no `.outpost`. The charging station it is docked at does have
    one, so the station ref is what gets asked: Inventory at Nocturna
    Base, a Warehouse or Storage Bin at a founded outpost.
    """
    station = dock_station()
    if station is None:
        return caps.INVENTORY
    return caps.local_store(station)


def load(pioneer, item_id, count) -> bool:
    """Put `count` units of `item_id` aboard. True once they are in cargo.

    Only at home, and only with Auto Feeders: `input.take()` reaches
    Inventory only while the Pioneer is parked at Nocturna Base, and the
    port does not exist without that research. The manual names cargo
    left sitting in base Inventory as the usual reason nothing builds, so
    a shortfall is reported BY NAME here rather than discovered in the
    field as `insufficient_materials` after a drive out.
    """
    if have(pioneer, item_id, count):
        return True
    if not at_home(pioneer):
        return False
    if not caps.research(caps.FEEDERS):
        _once(f"{pioneer.name} cannot load {item_id}: Auto Feeders research"
              f" is required before a vehicle input port works")
        return False

    source = source_store()
    if source is None:
        return False

    short = count - carried(pioneer, item_id)
    space = room_for(pioneer, item_id)
    if space <= 0:
        _once(f"{pioneer.name} has no bin free or latched to {item_id} -"
              f" every Portable Bin holds one item id, so the rack needs an"
              f" empty bin before this job can be loaded")
        return False
    if space < short:
        short = space

    if pioneer.input.connected_id() != source:
        linked = pioneer.input.connect(source)
        if linked.status != "ok":
            notify(f"{pioneer.name} input -> {source}: {linked.message}",
                   "warn")
            return False

    taken = pioneer.input.take(item_id, short)
    if taken.status not in ["ok", "partial", "no_op"]:
        print(f"{pioneer.name} input.take {item_id}: {taken.message}")

    if have(pioneer, item_id, count):
        return True

    _once(f"{pioneer.name} needs {count} x {item_id} for the next"
          f" construction job and {source} can only supply"
          f" {carried(pioneer, item_id)} - nothing will build until that"
          f" item is in stock")
    return False


# --- the queue ----------------------------------------------------------

# Jobs this Pioneer has executed this session. A Construction snapshot
# names no owner and another Pioneer's active job cannot be stolen, so
# this is the only workable meaning of ownership from in here.
mine = {"ids": []}

# Jobs left alone for the rest of the session, and how many times each
# has come back short of materials.
skipped = {"ids": []}
short_of = {"counts": {}}


def _by_progress(found):
    """`found`, most-completed first."""
    return sorted(found, key=lambda job: job.progress, reverse=True)


def jobs():
    """Every queued job worth trying, in the order to try them.

    Paused first and most-completed first - the manual's own advice, and
    what protects material already spent. Then work this Pioneer started
    and can rejoin. Then everything still waiting for a worker.
    """
    planner = queue()
    if planner is None:
        return []

    found = []
    for job in _by_progress(planner.paused_constructions()):
        found.append(job)
    for job in planner.active_constructions():
        if job.id in mine["ids"]:
            found.append(job)
    for job in planner.pending_constructions():
        found.append(job)
    return found


def next_job(pioneer):
    """The first job this Pioneer can reach and come home from, or None.

    Reachability is checked here rather than after loading, so the answer
    doubles as the departure gate's `have_target`: a job coming back
    means the round trip fits on the charge already in the battery.
    """
    reachable = None
    waiting = 0

    for job in jobs():
        if job.id in skipped["ids"]:
            continue
        waiting = waiting + 1
        if reachable is not None:
            continue
        if can_reach_and_return(pioneer, job.position.x, job.position.y):
            reachable = job

    if reachable is None and waiting > 0 and pioneer.battery.level() > DEPART_FRACTION:
        _once(f"{pioneer.name}: {waiting} construction job(s) queued and none"
              f" within round-trip range of {HOME['id']} on"
              f" {round(pioneer.battery.capacity())} Wh - the rig needs more"
              f" battery holders, or the work needs a nearer outpost")
    return reachable


# --- building -----------------------------------------------------------

def label(job) -> str:
    """A job as one readable phrase: `outpost at (-307, -157)`."""
    return (f"{job.kind} at ({round(job.position.x)},"
            f" {round(job.position.y)})")


def marks(job):
    """The job fields that ride along on this Pioneer's status channel."""
    return {"job": job.id, "kind": job.kind}


def status(pioneer, state, target=None, extra=None):
    """Broadcast this Pioneer's live status for the fleet manager to read.

    Taking the Pioneer as `pioneer` rather than `vehicle` is what lets
    this function still reach the `vehicle` module it delegates to.
    """
    vehicle.report(pioneer, state, target, extra)


def _skip(pioneer, job, result):
    """Leave this job alone for the session, having said why once."""
    if job.id not in skipped["ids"]:
        skipped["ids"].append(job.id)
    notify(f"{pioneer.name} skipping {label(job)}: {result.message}", "warn")


def do_job(pioneer, job) -> bool:
    """Load, drive, park, build. True only when the job actually finished.

    The order is fixed by the manual and every step of it fails quietly
    when skipped: the material must be in Pioneer cargo rather than base
    Inventory, arrival tolerance means close enough rather than stopped,
    and `execute()` answers `wrong_position` for a vehicle still rolling.
    """
    (item_id, count) = needs(job)

    if not have(pioneer, item_id, count):
        if not at_home(pioneer):
            status(pioneer, "returning", label(job), marks(job))
            go_home(pioneer)
            return False
        status(pioneer, "loading", label(job), marks(job))
        if not load(pioneer, item_id, count):
            status(pioneer, "blocked", label(job), marks(job))
            return False

    status(pioneer, "driving", label(job), marks(job))
    meters = pioneer.nav.get_distance_to(job.position.x, job.position.y)
    charge = pioneer.battery.wh()
    if not drive_to(pioneer, job.position.x, job.position.y):
        status(pioneer, "returning", label(job), marks(job))
        go_home(pioneer)
        return False
    record_drain(pioneer, charge - pioneer.battery.wh(), meters)

    status(pioneer, "constructing", label(job), marks(job))
    if job.id not in mine["ids"]:
        mine["ids"].append(job.id)

    built = pioneer.constructor.execute(job.id)

    if built.status == "wrong_position":
        drive_to(pioneer, job.position.x, job.position.y, CRUISE_THROTTLE,
                 CLOSE_TOLERANCE)
        built = pioneer.constructor.execute(job.id)

    if built.status == "ok":
        notify(f"{pioneer.name} finished {label(job)}")
        return True

    if built.status in RETRY:
        print(f"{pioneer.name} {label(job)}: {built.message}")
        return False

    if built.status == "insufficient_materials":
        seen = short_of["counts"].get(job.id, 0) + 1
        short_of["counts"][job.id] = seen
        if seen >= SHORT_LIMIT:
            _skip(pioneer, job, built)
            return False
        print(f"{pioneer.name} {label(job)}: {built.message} - reloading")
        status(pioneer, "returning", label(job), marks(job))
        go_home(pioneer)
        return False

    _skip(pioneer, job, built)
    return False


# --- founding -----------------------------------------------------------

def outpost_at(x, y, tolerance=SAME_SITE):
    """An owned outpost already standing at (x, y), or None."""
    network = caps.component(NETWORK_ID)
    if network is None:
        return None
    for outpost in network.outposts():
        gap = sqrt((x - outpost.x) * (x - outpost.x)
                   + (y - outpost.y) * (y - outpost.y))
        if gap <= tolerance:
            return outpost
    return None


def outpost_ghosts():
    """Every queued outpost blueprint, at any stage of being built."""
    planner = queue()
    if planner is None:
        return []
    found = []
    for job in planner.paused_constructions():
        if job.kind == OUTPOST_KIND:
            found.append(job)
    for job in planner.active_constructions():
        if job.kind == OUTPOST_KIND:
            found.append(job)
    for job in planner.pending_constructions():
        if job.kind == OUTPOST_KIND:
            found.append(job)
    return found


def found_outpost(x, y):
    """Queue an outpost ghost at (x, y). Returns its blueprint id, or None.

    Planning needs no vehicle at the site: the ghost enters the shared
    queue immediately and the drain loop then treats it as any other job.

    Refuses while an unbuilt outpost is already queued, and says nothing
    at all once one stands here. This call re-runs on every restart, and
    without those two checks a restart would plan a second ghost and
    spend a second 15,000 cr kit on it (D-013).
    """
    standing = outpost_at(x, y)
    if standing is not None:
        print(f"{standing.name} already stands at ({round(standing.x)},"
              f" {round(standing.y)}) - nothing to found")
        return None

    planner = queue()
    if planner is None:
        notify("No Construction Blueprint component - Outpost Construction"
               " research is what creates the shared queue", "warn")
        return None

    queued = outpost_ghosts()
    if len(queued) > 0:
        job = queued[0]
        print(f"outpost blueprint {job.id} already queued at"
              f" ({round(job.position.x)}, {round(job.position.y)}) at"
              f" {round(job.progress * 100)}% - not planning a second one")
        return job.id

    result = planner.plan_structure(OUTPOST_KIND, x, y)
    if result.status != "ok":
        notify(f"plan_structure(outpost, {round(x, 1)}, {round(y, 1)}):"
               f" {result.message}", "warn")
        return None
    if len(result.blueprint_ids) == 0:
        notify(f"plan_structure(outpost) reported ok at ({round(x, 1)},"
               f" {round(y, 1)}) but named no blueprint - check the Planet"
               f" Map before loading a kit", "warn")
        return None

    notify(f"Outpost blueprint queued at ({round(x, 1)}, {round(y, 1)})")
    return result.blueprint_ids[0]


# --- capacity -----------------------------------------------------------

capacity = {"line": ""}


def capacity_line() -> str:
    """Each outpost's building count against its soft threshold.

    The threshold does not block deployment - it throttles productive and
    service output at every counted building above it (D-021). That makes
    it a number worth naming out loud rather than a limit that announces
    itself later by making everything slower.
    """
    network = caps.component(NETWORK_ID)
    if network is None:
        return "no outpost network"
    parts = []
    for outpost in network.outposts():
        over = ""
        if outpost.is_full:
            over = " OVER"
        parts.append(f"{outpost.id} {outpost.buildings_used}/"
                     f"{outpost.buildings_capacity}{over}")
    return ", ".join(parts)


def watch_capacity():
    """Say the capacity line at startup and whenever it changes."""
    line = capacity_line()
    if line == capacity["line"]:
        return
    capacity["line"] = line
    notify(f"Outpost capacity: {line}")


# --- main loop ----------------------------------------------------------

def run(pioneer, interval=IDLE_INTERVAL):
    """Build whatever is queued, forever.

    pioneer   the Pioneer this script runs inside - pass `self`
    interval  seconds between passes with nothing to do

    A Pioneer with no Constructor Module says so and stops here rather
    than idling on a queue it can never work: the scout rig shares this
    `.kind` and would otherwise sit reporting `idle` forever (D-016).
    """
    find_home(pioneer)

    caps.report("Pioneer constructor online", caps.common() + [
        ("constructor", constructor(pioneer) is not None),
        ("queue", queue() is not None),
        ("battery", f"{round(pioneer.battery.capacity())} Wh"),
        ("cargo", f"{pioneer.cargo.count()}/{pioneer.cargo.capacity()}"),
        ("queued jobs", len(jobs())),
        ("home", f"{HOME['id']} at ({round(HOME['x'], 1)},"
                 f" {round(HOME['y'], 1)})"),
    ])
    watch_capacity()

    if constructor(pioneer) is None:
        notify(f"{pioneer.name} has no Constructor Module mounted - it cannot"
               f" build, so it is not joining the construction queue", "warn")
        return

    while True:

        level = pioneer.battery.level()
        home = at_home(pioneer)

        # --- stranded in the field ---------------------------------------
        # Only in the field. A flat Pioneer on the pad is waiting for a
        # charge, and calling that stranded sends a rescue drone to a
        # vehicle already parked at the station.
        if level < STRANDED_FRACTION and not home:
            status(pioneer, "stranded")
            notify(f"{pioneer.name} battery critical - needs a rescue"
                   f" dispatch", "warn")
            sleep(interval)
            continue

        # --- too low to keep working -------------------------------------
        if level < RESERVE_FRACTION:
            if not home:
                status(pioneer, "returning")
                go_home(pioneer)
                continue
            status(pioneer, "waiting_for_charge")
            wait_for_charge(pioneer, level)
            continue

        # Picked before the depart gate, because a job in hand is that
        # gate's have_target: next_job() has already filtered through
        # can_reach_and_return.
        job = next_job(pioneer)

        # --- not enough to start a new trip ------------------------------
        # In the field the floor is the whole rule; on the pad the
        # station's own signal decides, because that is the only place
        # the signal means anything (D-027).
        if not home:
            if level < DEPART_FRACTION:
                status(pioneer, "returning")
                go_home(pioneer)
                continue
        elif not ready_to_depart(pioneer, level, job is not None):
            status(pioneer, "waiting_for_charge")
            wait_for_charge(pioneer, level)
            continue

        # --- nothing queued ----------------------------------------------
        if job is None:
            if not home:
                status(pioneer, "returning")
                go_home(pioneer)
                continue
            watch_capacity()
            status(pioneer, "idle")
            sleep(interval)
            continue

        working()
        if not do_job(pioneer, job):
            sleep(interval)
        watch_capacity()
