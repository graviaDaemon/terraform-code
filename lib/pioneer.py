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

Founding an outpost is therefore one job in that queue, not a mode. Its
coordinates come off the scout's shortlist, filtered by purpose against a
ledger of what has already been founded, and the pace is set by how many
Outpost Kits are sitting in Inventory - no script ever buys one
(D-041, D-042, D-043).

Interruptions are normal and recoverable. Stop, power loss, leaving the
site, a rescue or unmounting the Constructor Module pauses paid work
without losing progress or materials, and the owning Pioneer can rejoin
it - including after a save/load. So `paused_constructions()` is the
FIRST list read every pass, most-completed first: that is where material
already spent is sitting.
"""

import bus
import caps
import vehicle
from vehicle import CRUISE_THROTTLE, DEPART_FRACTION, HOME, IDLE_INTERVAL, RESERVE_FRACTION, STRANDED_FRACTION, at_home, can_reach_and_return, drive_to, find_home, go_home, ready_to_depart, record_drain, wait_for_charge, working

BLUEPRINT_ID = "construction_blueprint"
NETWORK_ID = "outpost_network"
ARCHIVE_ID = "notebook"
OUTPOST_KIND = "outpost"
OUTPOST_KIT = "outpost_kit"

# The founded-outpost ledger, and the scout's shortlist. Both are Data
# Archive keys; the ledger doubles as its own bus channel when there is
# no archive, and the shortlist's live copy is bus.SCOUT_CANDIDATES.
LEDGER_KEY = "pioneer.outposts"
CANDIDATES_KEY = "scout.candidates"

# The roadmap, in the order it gets built (D-016). One editable list is
# the whole of the siting policy: nothing wants "utility" today, so vent
# and well sites are never founded until this list says so (D-041).
WANTED = ["power", "harvesting", "production"]

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

# Idle passes between attempts to found the next outpost. Matched to
# scout.RERANK_PASSES: the shortlist only changes when the scout
# re-ranks, so asking more often just re-reads the same list.
FOUND_EVERY = 30

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

def _gap(ax, ay, bx, by):
    return sqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by))


def outpost_at(x, y, tolerance=SAME_SITE):
    """An owned outpost already standing at (x, y), or None."""
    network = caps.component(NETWORK_ID)
    if network is None:
        return None
    for outpost in network.outposts():
        if _gap(x, y, outpost.x, outpost.y) <= tolerance:
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


def ghost_at(x, y, tolerance=SAME_SITE):
    """An unbuilt outpost blueprint already queued at (x, y), or None."""
    for job in outpost_ghosts():
        if _gap(x, y, job.position.x, job.position.y) <= tolerance:
            return job
    return None


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


# --- the ledger of what has been founded --------------------------------

def _notebook():
    """The Data Archive component, or None before that research."""
    return caps.component(ARCHIVE_ID)


def _ledger():
    """Every founded-outpost record, or None when they cannot be read.

    None is not [] and the difference is 15,000 cr. An empty list means
    nothing has been founded; None means the ledger is unreadable, and
    founding is declined rather than risk a second kit on a purpose that
    is already covered (D-043).
    """
    book = _notebook()
    if book is not None:
        return book.get(LEDGER_KEY, [])
    if bus.comms() is not None:
        return bus.read(LEDGER_KEY, [])
    return None


def _save_ledger(records) -> bool:
    """Persist the ledger. Archive first, the bus as the fallback."""
    book = _notebook()
    if book is None:
        return bus.publish(LEDGER_KEY, records)
    result = book.set(LEDGER_KEY, records)
    if result.status != "ok":
        print(f"set {LEDGER_KEY}: {result.message}")
        return False
    return True


def _near_record(records, x, y, tolerance=SAME_SITE) -> bool:
    """True when the ledger already holds a record at (x, y)."""
    for record in records:
        if _gap(x, y, record["x"], record["y"]) <= tolerance:
            return True
    return False


def purpose_at(x, y, tolerance=SAME_SITE):
    """The shortlist's purpose label for this site, or "unknown".

    An outpost's coordinates are its footprint anchor and a candidate's
    are the cluster centroid, so the two are never equal - SAME_SITE is
    the same 30 m slack found_outpost() already works in.
    """
    for candidate in candidates():
        if _gap(x, y, candidate["x"], candidate["y"]) <= tolerance:
            return candidate["purpose"]
    return "unknown"


def reconcile():
    """Bring the ledger back in line with what actually stands.

    Three rules, in order. A record whose site now holds an owned
    outpost becomes `built` with that outpost's real id. A record whose
    outpost is gone and has no ghost waiting is dropped, because that
    purpose is open again. And every non-home outpost the ledger does
    not know about is ADOPTED.

    Adoption is what makes the first run correct rather than expensive:
    the power outpost already stands and predates the ledger, so it is
    taken as the `power` record and no second one is ever planned. It is
    equally the answer to a save/load, a hand-built outpost and a
    cleared archive (D-043).
    """
    records = _ledger()
    if records is None:
        return None

    kept = []
    for record in records:
        standing = outpost_at(record["x"], record["y"])
        if standing is not None:
            record["outpost"] = standing.id
            record["state"] = "built"
            kept.append(record)
        elif ghost_at(record["x"], record["y"]) is not None:
            record["state"] = "queued"
            kept.append(record)

    for outpost in caps.outposts():
        if outpost.is_home:
            continue
        known = False
        for record in kept:
            if record["outpost"] == outpost.id:
                known = True
        if known:
            continue
        kept.append({
            "purpose": purpose_at(outpost.x, outpost.y),
            "x": outpost.x,
            "y": outpost.y,
            "outpost": outpost.id,
            "blueprint": None,
            "state": "built",
        })

    _save_ledger(kept)
    return kept


def purposes_done():
    """Purposes holding a record in either state, or None when unreadable."""
    records = _ledger()
    if records is None:
        return None
    found = []
    for record in records:
        if record["purpose"] not in found:
            found.append(record["purpose"])
    return found


def wanted_purpose():
    """The first entry of WANTED with no outpost yet, or None."""
    done = purposes_done()
    if done is None:
        return None
    for purpose in WANTED:
        if purpose not in done:
            return purpose
    return None


def ledger_line() -> str:
    """The ledger as `purpose -> outpost`, for the startup line."""
    records = _ledger()
    if records is None:
        return "unreadable"
    if len(records) == 0:
        return "nothing yet"
    parts = []
    for record in records:
        where = record["outpost"]
        if where is None:
            where = record["state"]
        parts.append(f"{record['purpose']} -> {where}")
    return ", ".join(parts)


def wanted_line() -> str:
    """What the roadmap is waiting on, for the startup line."""
    if purposes_done() is None:
        return "unknown - no archive and no bus"
    purpose = wanted_purpose()
    if purpose is None:
        return "nothing - the roadmap is covered"
    return purpose


# --- picking the site off the shortlist ---------------------------------

def candidates():
    """The scout's ranked shortlist, best first, or [].

    The bus first, because that is the live copy the scout republishes
    on every re-rank; the archive second, because that is the copy that
    survives a restart of either script.
    """
    published = bus.read(bus.SCOUT_CANDIDATES, None)
    if published is not None:
        return published
    book = _notebook()
    if book is not None:
        return book.get(CANDIDATES_KEY, [])
    return []


def offered() -> str:
    """The purposes the current shortlist is actually offering.

    Named out loud when nothing matches WANTED, because the alternative
    is a founder that silently never founds: scout.purpose() checks the
    utility kinds first, so a cluster holding one vent is labelled
    `utility` however many mineral sites sit beside it (D-041).
    """
    seen = []
    for candidate in candidates():
        if candidate["purpose"] not in seen:
            seen.append(candidate["purpose"])
    if len(seen) == 0:
        return "nothing"
    return ", ".join(seen)


def site_for(purpose):
    """The best free candidate carrying `purpose`, or None.

    None is a normal answer meaning the scout has not found anywhere for
    this yet. The shortlist is already score-sorted, so the first match
    that is not already taken is the best one.
    """
    records = _ledger()
    if records is None:
        return None
    for candidate in candidates():
        if candidate["purpose"] != purpose:
            continue
        if candidate["placement"] != "ok":
            continue
        x = candidate["x"]
        y = candidate["y"]
        if outpost_at(x, y) is not None:
            continue
        if ghost_at(x, y) is not None:
            continue
        if _near_record(records, x, y):
            continue
        return candidate
    return None


# --- founding the next one ----------------------------------------------

def kits_in_stock() -> int:
    """Outpost Kits sitting in base Inventory.

    Inventory's read-only methods are visible planet-wide, so this is
    the budget question answered without a drive. Kit stock IS the
    budget: the constructor founds exactly as many outposts as there are
    kits, and buying one stays a human act (D-042).
    """
    store = caps.component(caps.INVENTORY)
    if store is None:
        return 0
    return store.count(OUTPOST_KIT)


def found_next():
    """Found the next outpost the roadmap wants. Its blueprint id, or None.

    Five gates, in order, returning quietly at the first that does not
    pass. Only the last two say anything, and each says it once: no kit
    and no site are the two states a human has to act on, and everything
    above them is ordinary.
    """
    if queue() is None:
        return None
    if len(outpost_ghosts()) > 0:
        return None

    if purposes_done() is None:
        _once("No Data Archive and no Signal Bus, so there is no record of"
              " which outposts have already been founded - founding is"
              " declined rather than spend a second 15,000 cr kit on a"
              " purpose that may already be covered")
        return None

    purpose = wanted_purpose()
    if purpose is None:
        return None

    if kits_in_stock() <= 0:
        _once(f"The roadmap wants a {purpose} outpost and there is no"
              f" {OUTPOST_KIT} in Inventory. A kit costs 15,000 cr and each"
              f" one after that costs more, so no script buys one - put one"
              f" in Inventory and it will be founded automatically")
        return None

    site = site_for(purpose)
    if site is None:
        _once(f"The roadmap wants a {purpose} outpost and the scout's"
              f" shortlist offers no free {purpose} site - it currently"
              f" offers {offered()}. Edit pioneer.WANTED, or send the scout"
              f" further out")
        return None

    blueprint_id = found_outpost(site["x"], site["y"])
    if blueprint_id is None:
        return None

    records = _ledger()
    if records is None:
        records = []
    records.append({
        "purpose": purpose,
        "x": site["x"],
        "y": site["y"],
        "outpost": None,
        "blueprint": blueprint_id,
        "state": "queued",
    })
    _save_ledger(records)
    reconcile()
    return blueprint_id


# --- main loop ----------------------------------------------------------

# Idle passes since the loop started, counted only so found_next() runs
# on the FOUND_EVERY cadence rather than on every ten-second pass.
founding = {"passes": 0}


def run(pioneer, interval=IDLE_INTERVAL):
    """Build whatever is queued, forever.

    pioneer   the Pioneer this script runs inside - pass `self`
    interval  seconds between passes with nothing to do

    A Pioneer with no Constructor Module says so and stops here rather
    than idling on a queue it can never work: the scout rig shares this
    `.kind` and would otherwise sit reporting `idle` forever (D-016).
    """
    find_home(pioneer)
    reconcile()

    caps.report("Pioneer constructor online", caps.common() + [
        ("constructor", constructor(pioneer) is not None),
        ("queue", queue() is not None),
        ("battery", f"{round(pioneer.battery.capacity())} Wh"),
        ("cargo", f"{pioneer.cargo.count()}/{pioneer.cargo.capacity()}"),
        ("queued jobs", len(jobs())),
        ("home", f"{HOME['id']} at ({round(HOME['x'], 1)},"
                 f" {round(HOME['y'], 1)})"),
        ("founded", ledger_line()),
        ("wanted", wanted_line()),
        (OUTPOST_KIT, kits_in_stock()),
    ])
    caps.watch_capacity()

    if constructor(pioneer) is None:
        notify(f"{pioneer.name} has no Constructor Module mounted - it cannot"
               f" build, so it is not joining the construction queue", "warn")
        return

    found_next()

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
            caps.watch_capacity()
            status(pioneer, "idle", wanted_purpose())
            founding["passes"] = founding["passes"] + 1
            if founding["passes"] % FOUND_EVERY == 0:
                found_next()
            sleep(interval)
            continue

        working()
        if not do_job(pioneer, job):
            sleep(interval)
        caps.watch_capacity()
