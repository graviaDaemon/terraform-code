"""Pioneer scouting: rank where an outpost should go, and prove it is legal.

    import scout
    scout.run(self)

`self` does not exist in a library, so the Pioneer is passed in. Driving,
parking, docking and the learned Wh-per-meter figure are the vehicle
layer's job; what is left here is map work.

This file BUILDS NOTHING. It surveys, ranks, validates and reports, and
stops there. There is exactly one Outpost Kit, so the choice of where it
goes is a conversation, not a script decision (D-013).

Two rules shape the loop:

  - The desk survey comes first (D-018). `points_of_interest()` hands
    over real coordinates for every permanent map contact at zero cost,
    and the Journal already holds everything any sonar has classified.
    With a 50 m sweep, ring-walking covers almost no ground per trip, so
    the first shortlist is published before the Pioneer moves at all.
  - A candidate is only reported after the game itself has accepted the
    footprint (D-012). The clearance radius around an outpost is not
    documented, so any radius modelled here would be a guess that rots.
    `plan_structure` is asked instead, and the unpaid ghost is retracted
    immediately, which makes probing free.

Sweeps that come back `too_hard`, `tier_too_low` or `research_required`
are not failures: each one means "the sweep finished and there is an
unresolved contact here", at a known position. That is the one kind of
map knowledge a hardness-1 sonar can still produce about a deposit it
cannot classify, so it is written down (D-018).
"""

import bus
import caps
import survey
import vehicle
from rover import SWEEP_PARTIAL, prospect_points
from vehicle import HOME, DEPART_FRACTION, IDLE_INTERVAL, RESERVE_FRACTION, STRANDED_FRACTION, at_home, can_reach_and_return, drive_to, find_home, go_home, ready_to_depart, record_drain, wait_for_charge, working

ARCHIVE_ID = "notebook"
BLUEPRINT_ID = "construction_blueprint"
MARKERS_ID = "markers"
OUTPOST_KIND = "outpost"

# Where the shortlist and the unresolved contacts are kept between
# sessions. The bus carries the live list; the archive is what survives
# a restart, which is the whole point of a shortlist a human reads later.
CANDIDATES_KEY = "scout.candidates"
UNRESOLVED_KEY = "scout.unresolved"

# Marker id family. The prefix names the controlling script so two
# scripts cannot overwrite each other's pins.
MARKER_PREFIX = "scout.candidate."

# survey() statuses that mean "there is a contact here this sonar cannot
# resolve" rather than "that call failed".
SURVEY_PARTIAL = ["too_hard", "tier_too_low", "research_required"]

# Kinds that make a place worth an outpost for what can be piped out of
# it rather than for what can be dug out of it.
UTILITY_KINDS = ["thermal", "water", "oil", "exotic"]

# Kinds that are a contact without being a resource yet.
OPEN_KINDS = ["unknown", "unresolved"]

# Two contacts this close together are the same thing seen twice - a
# Journal site and the map's own "?" for it - and must not be counted
# as two.
SAME_SITE = 5.0

# Mineral sites in one cluster before it is worth calling harvesting
# ground rather than a stop on the way.
DENSE_MINERALS = 3

# Within this many meters of home, ground with nothing to dig is worth
# more as production floor than as another mining outpost.
NEAR_HOME = 250.0

# Scoring weights. Distance is in meters and subtracted directly, so one
# classified site is worth about 300 m of extra drive and a vent about
# 900 m - a vent or a well is the only thing here that cannot be moved
# to and mined from somewhere else.
SITE_VALUE = 300
CONTACT_VALUE = 120
UTILITY_VALUE = 900
DISTANCE_WEIGHT = 1.0

# How many candidates to probe and report. Every probe is a
# plan_structure call, and a shortlist nobody can read through is not a
# shortlist.
PROBE_LIMIT = 12
SHORTLIST = 5

# Member names carried in a reported candidate, so the payload stays
# small enough to publish and archive whole.
NEARBY_NAMES = 5

# Rings of filler waypoints, walked only once the "?" list is exhausted.
FILLER_RINGS = 3

# Idle passes between rebuilds of the shortlist. Idling is free; a
# rebuild is not, because it probes placement for real.
RERANK_PASSES = 30


def _notebook():
    """The Data Archive component, or None before that research."""
    return caps.component(ARCHIVE_ID)


def blueprint():
    """The Construction Blueprint component, or None before that research."""
    return caps.component(BLUEPRINT_ID)


def markers():
    """The Map Markers component, or None before Cartography."""
    return caps.component(MARKERS_ID)


def point_id(x, y) -> str:
    """A stable id for a bare coordinate, e.g. "at:120:-40"."""
    return f"at:{int(round(x))}:{int(round(y))}"


# --- the Pioneer's own rig ----------------------------------------------

def slots(pioneer):
    """Every chassis slot, or [] when the rig cannot be inspected."""
    try:
        return pioneer.modules()
    except Exception:
        return []


def mounted(pioneer, capability) -> bool:
    """True when a module whose id names `capability` is mounted.

    Asked of the chassis rather than assumed from a loadout note: the
    scout and the constructor Pioneer are the same `.kind` carrying
    different modules (D-016), and either can be re-rigged at a service
    point without this file hearing about it.
    """
    for slot in slots(pioneer):
        module_id = slot.module_id
        if module_id is not None and capability in module_id:
            return True
    return False


def sonar_label(pioneer) -> str:
    """Sonar range and tier as one string, or "none" when unmounted."""
    if not mounted(pioneer, "sonar"):
        return "none"
    try:
        return f"{round(pioneer.sonar.range())}m {pioneer.sonar.tier()}"
    except Exception:
        return "unreadable"


def cargo_bins(pioneer) -> int:
    """Portable bins installed across every Cargo Rack on the rig."""
    total = 0
    for slot in slots(pioneer):
        module_id = slot.module_id
        if module_id is None or "cargo" not in module_id:
            continue
        total = total + len(slot.internal_items)
    return total


# --- unresolved contacts ------------------------------------------------

# What the next archive transaction should write. The updater handed to
# notebook.transaction() must be a pure function of the stored value, so
# the record it is to merge travels in module state rather than captured.
pending = {"key": "", "record": {}}

# Coordinates swept this session, so a contact whose "?" never clears -
# because the sonar cannot classify what is under it - is not driven to
# again and again for the rest of the run.
swept = {}


def _merge_unresolved(stored):
    """Updater: add or refresh one unresolved contact."""
    if stored is None:
        stored = {}
    stored[pending["key"]] = pending["record"]
    return stored


def remember_unresolved(x, y, status):
    """Write down a contact this sonar finished on but could not classify.

    Kept so a later Wide or Deep Sonar trip is targeted rather than a
    re-sweep of the whole map from scratch, and so a cluster of these
    counts toward a candidate's score today (D-018).
    """
    book = _notebook()
    if book is None:
        return
    pending["key"] = point_id(x, y)
    pending["record"] = {"x": x, "y": y, "status": status}
    result = book.transaction(UNRESOLVED_KEY, {}, _merge_unresolved)
    if result.status != "ok":
        print(f"transaction {UNRESOLVED_KEY}: {result.message}")


def unresolved():
    """Every unresolved contact recorded so far, as flat records."""
    book = _notebook()
    if book is None:
        return []
    stored = book.get(UNRESOLVED_KEY, {})
    if not isinstance(stored, dict):
        return []
    records = []
    for key in stored:
        record = stored[key]
        if isinstance(record, dict):
            records.append(record)
    return records


# --- what is known ------------------------------------------------------

def _near_any(records, x, y, tolerance=SAME_SITE) -> bool:
    for record in records:
        if survey.distance(x, y, record["x"], record["y"]) <= tolerance:
            return True
    return False


def contacts():
    """Every map contact worth clustering, from all three known sources.

    Classified sites come from the Journal, the "?" points from the map
    itself, and the unresolved ones from our own sweeps. A contact
    already present as a classified site is not added twice: the map
    keeps a "?" and the Journal keeps the site, and they are one place.
    """
    records = survey.sites()

    for point in survey.unscanned_points():
        if _near_any(records, point["x"], point["y"]):
            continue
        records.append({
            "id": point_id(point["x"], point["y"]),
            "name": "unscanned contact",
            "x": point["x"],
            "y": point["y"],
            "kind": "unknown",
            "surveyed": False,
        })

    for record in unresolved():
        if _near_any(records, record["x"], record["y"]):
            continue
        records.append({
            "id": point_id(record["x"], record["y"]),
            "name": f"unresolved ({record.get('status', 'unknown')})",
            "x": record["x"],
            "y": record["y"],
            "kind": "unresolved",
            "surveyed": False,
        })

    return records


# --- ranking ------------------------------------------------------------

def home_distance(x, y):
    """Meters from the docking point this Pioneer calls home."""
    return survey.distance(x, y, HOME["x"], HOME["y"])


def nearest_outpost(x, y):
    """(id, meters) of the closest outpost owned, or (None, None).

    Reported rather than scored against: whether a spot is too close to
    an existing outpost is `plan_structure`'s answer to give, not ours
    (D-012). What this adds is the human-readable "and it is 380 m from
    Nocturna Base", which is what the choice actually turns on.
    """
    nearest = None
    nearest_distance = 0
    for outpost in caps.outposts():
        gap = survey.distance(x, y, outpost.x, outpost.y)
        if nearest is None or gap < nearest_distance:
            nearest = outpost.id
            nearest_distance = gap
    if nearest is None:
        return (None, None)
    return (nearest, nearest_distance)


def score_candidate(group):
    """Rank one cluster: what is there, less how far away it is.

    A vent or a well outweighs everything else in the mix because it is
    the only thing that cannot be driven to and carried home - piping
    needs a structure on top of it. Classified sites come next, open
    contacts count for less because they may turn out to be nothing, and
    inert formations count for nothing at all.

    Distance is a straight subtraction in meters. It is deliberately not
    a multiplier: a power annex is sited for distance and legality alone
    (D-020), so distance has to be able to decide between two otherwise
    equal candidates without swamping a genuinely better one.
    """
    value = 0
    kinds = group["kinds"]
    for kind in kinds:
        count = kinds[kind]
        if kind in UTILITY_KINDS:
            value = value + UTILITY_VALUE * count
        elif kind in OPEN_KINDS:
            value = value + CONTACT_VALUE * count
        elif kind == "inert":
            continue
        else:
            value = value + SITE_VALUE * count
    return value - home_distance(group["x"], group["y"]) * DISTANCE_WEIGHT


def purpose(group, distance_home):
    """What this place would be good at, from its mix and its position.

    A label, not a decision: which specialization actually goes where is
    the conversation this shortlist exists to inform.

    Biome is reported alongside but is not scored. Nothing in the manual
    makes a machine's output depend on the biome it stands in - solar
    follows sun elevation against panel tilt, which `lib/solar.py`
    already tracks (D-020) - so a biome weight would be invented.
    """
    kinds = group["kinds"]
    for kind in UTILITY_KINDS:
        if kinds.get(kind, 0) > 0:
            return "utility"
    if kinds.get("mineral", 0) >= DENSE_MINERALS:
        return "harvesting"
    if distance_home <= NEAR_HOME:
        return "production"
    return "power"


def _nearby(group, limit=NEARBY_NAMES):
    """A few member names, so a candidate line says what is actually there."""
    names = []
    for member in group["members"]:
        if len(names) >= limit:
            return names
        names.append(member["name"])
    return names


def candidate_of(group):
    """One cluster as the flat, JSON-safe record that gets reported.

    The members themselves are dropped and summarised: this payload is
    published on the bus and written to the archive, both of which have
    size limits, and a reader wants the shape of the place rather than
    every contact in it.
    """
    distance_home = home_distance(group["x"], group["y"])
    (outpost_id, outpost_gap) = nearest_outpost(group["x"], group["y"])
    return {
        "x": group["x"],
        "y": group["y"],
        "score": score_candidate(group),
        "purpose": purpose(group, distance_home),
        "distance_home": distance_home,
        "nearest_outpost": outpost_id,
        "nearest_outpost_m": outpost_gap,
        "biome": survey.biome_at(group["x"], group["y"]),
        "count": group["count"],
        "kinds": group["kinds"],
        "nearby": _nearby(group),
        "placement": None,
    }


# --- validation ---------------------------------------------------------

def validate(candidate) -> bool:
    """Ask the game whether an outpost may stand here, then undo the ask.

    Records the verdict on the candidate either way - "clearance" is
    useful to a reader, not just to the filter - and retracts the ghost
    the moment it comes back "ok", so probing leaves nothing on the map
    and costs nothing (D-012).
    """
    planner = blueprint()
    if planner is None:
        candidate["placement"] = "no_blueprint"
        return False

    result = planner.plan_structure(OUTPOST_KIND, candidate["x"],
                                    candidate["y"])
    candidate["placement"] = result.status
    if result.status != "ok":
        return False

    for blueprint_id in result.blueprint_ids:
        cancelled = planner.cancel(blueprint_id)
        if cancelled.status != "ok":
            notify(f"scout: probe ghost {blueprint_id} at"
                   f" ({round(candidate['x'], 1)}, {round(candidate['y'], 1)})"
                   f" would not retract: {cancelled.message} - cancel it by"
                   f" hand before it gets built", "warn")
    return True


# What the last round of placement probes came back with, counted by
# status. An empty shortlist is otherwise indistinguishable between "the
# whole map is taken" and "Construction Blueprint is not researched yet",
# and those want very different things done about them.
probed = {"verdicts": {}}


def verdicts() -> str:
    """The last round of placement verdicts, as one readable phrase."""
    if len(probed["verdicts"]) == 0:
        return "nothing was close enough to a contact to probe"
    parts = []
    for verdict in probed["verdicts"]:
        parts.append(f"{verdict} x{probed['verdicts'][verdict]}")
    return ", ".join(parts)


def shortlist(radius=survey.CLUSTER_RADIUS, probe=PROBE_LIMIT,
              limit=SHORTLIST):
    """The ranked, validated candidate list. No vehicle, no driving.

    Everything this reads is already known, so it can be called before
    the Pioneer has moved and again after every sweep (D-018). Only the
    best `probe` clusters are put to `plan_structure`, because a probe
    is a real call and the tail of the ranking is not worth one.
    """
    groups = survey.cluster(contacts(), radius)

    ranked = []
    for group in groups:
        ranked.append(candidate_of(group))
    ranked = sorted(ranked, key=lambda candidate: candidate["score"],
                    reverse=True)

    probed["verdicts"] = {}
    accepted = []
    checked = 0
    for candidate in ranked:
        if checked >= probe or len(accepted) >= limit:
            return accepted
        checked = checked + 1
        accepted_here = validate(candidate)
        verdict = candidate["placement"]
        probed["verdicts"][verdict] = probed["verdicts"].get(verdict, 0) + 1
        if accepted_here:
            accepted.append(candidate)
    return accepted


# --- reporting ----------------------------------------------------------

# The last shortlist announced, so re-ranking after every sweep does not
# put the same three coordinates on screen every ten seconds.
# "unset" rather than "": an empty shortlist digests to "", and the very
# first one is worth saying out loud.
announced = {"digest": "unset"}


def _digest(candidates) -> str:
    parts = []
    for candidate in candidates:
        parts.append(point_id(candidate["x"], candidate["y"]))
    return ",".join(parts)


def _archive(candidates):
    book = _notebook()
    if book is None:
        return
    result = book.set(CANDIDATES_KEY, candidates)
    if result.status != "ok":
        print(f"set {CANDIDATES_KEY}: {result.message}")


def _pin(candidates):
    """Drop one map marker per candidate, when Cartography is present.

    The whole family is cleared and re-placed rather than added to, so
    the pins on the map always describe the current shortlist instead of
    accumulating every list this script has ever believed.
    """
    pins = markers()
    if pins is None:
        return
    pins.clear(MARKER_PREFIX)
    rank = 1
    for candidate in candidates:
        result = pins.place(MARKER_PREFIX + str(rank),
                            candidate["x"], candidate["y"],
                            f"#{rank} {candidate['purpose']}",
                            "flag", "accent",
                            f"{candidate['count']} contacts,"
                            f" {round(candidate['distance_home'])} m from home")
        if result.status != "ok":
            print(f"markers.place #{rank}: {result.message}")
            return
        rank = rank + 1


def _line(rank, candidate) -> str:
    nearby = ", ".join(candidate["nearby"])
    if nearby == "":
        nearby = "nothing classified yet"
    return (f"{rank}. ({round(candidate['x'], 1)},"
            f" {round(candidate['y'], 1)}) {candidate['purpose']}"
            f" - {round(candidate['distance_home'])} m from home,"
            f" {candidate['biome']}, {candidate['count']} contacts:"
            f" {nearby}")


def report(candidates):
    """Publish the shortlist: on screen, on the bus, in the archive, on the map.

    Only when it has changed. This is called after every sweep, and the
    shortlist usually does not move.
    """
    digest = _digest(candidates)
    if digest == announced["digest"]:
        return
    announced["digest"] = digest

    bus.publish(bus.SCOUT_CANDIDATES, candidates)
    _archive(candidates)
    _pin(candidates)

    if len(candidates) == 0:
        notify(f"Scout: no legal outpost site to report - {verdicts()}",
               "warn")
        return

    lines = []
    rank = 1
    for candidate in candidates:
        lines.append(_line(rank, candidate))
        rank = rank + 1
    notify("Scout shortlist (every one accepted by plan_structure):\n"
           + "\n".join(lines))


def status(pioneer, state, target=None, extra=None):
    """Broadcast this Pioneer's live status for the fleet manager to read.

    Taking the Pioneer as `pioneer` rather than `vehicle` is what lets
    this function still reach the `vehicle` module it delegates to.
    """
    vehicle.report(pioneer, state, target, extra)


resting = {"passes": 0}


def idle(pioneer, radius=survey.CLUSTER_RADIUS, passes=RERANK_PASSES):
    """Wait, and re-rank every so often.

    There is nothing in reach left to sweep. The Journal keeps growing
    while this Pioneer sits still - the rovers survey into it - so the
    shortlist is worth rebuilding, but not every ten seconds: every
    rebuild probes placement for real.
    """
    resting["passes"] = resting["passes"] + 1
    if resting["passes"] % passes == 0:
        report(shortlist(radius))
    status(pioneer, "idle")
    sleep(IDLE_INTERVAL)


# --- sweeping -----------------------------------------------------------

def sweep(pioneer):
    """Sonar sweep from where the Pioneer is parked. Returns (status, sites).

    Unlike the rover's, this one hands the status back: a partial is a
    contact at a known position that this sonar cannot classify, and
    that is worth recording rather than discarding (D-018). Only "ok"
    documents the .sites payload, so every other status returns [].
    """
    result = pioneer.sonar.scan()

    if result.status == "ok":
        return (result.status, result.sites)
    if result.status in SWEEP_PARTIAL:
        return (result.status, [])
    if result.status == "busy":
        sleep(1)
        return (result.status, [])
    if result.status == "no_power":
        notify("scout: the sonar has no power - is the battery flat or the"
               " module unmounted?", "warn")
        return (result.status, [])
    notify(f"sonar.scan: {result.message}", "warn")
    return (result.status, [])


def resolve(pioneer, contact):
    """Survey one contact so the Journal holds its real fields.

    Every kind is surveyed, not only minerals: a thermal vent or a water
    well is exactly what makes a candidate `utility` ground, and the
    rovers survey neither.
    """
    if contact.surveyed:
        return True
    result = pioneer.sonar.survey(contact)
    if result.status == "ok":
        return True
    if result.status == "busy":
        sleep(1)
        return False
    if result.status in SURVEY_PARTIAL:
        remember_unresolved(contact.x, contact.y, result.status)
        return False
    if result.status in ["out_of_range", "not_discovered", "no_power"]:
        return False
    notify(f"sonar.survey: {result.message}", "warn")
    return False


def sweep_at(pioneer, x, y):
    """Drive to (x, y), park, sweep, and survey what comes back.

    True when a sweep actually happened, which is the caller's cue that
    the shortlist may have moved. False when the drive did not finish -
    the caller decides whether that means go home or try elsewhere.
    """
    start_wh = pioneer.battery.wh()
    metres = pioneer.nav.get_distance_to(x, y)

    status(pioneer, "driving", point_id(x, y))
    if not drive_to(pioneer, x, y):
        # Two different failures wearing one return value. A drive that
        # stopped at the battery floor is worth retrying after a charge.
        # A route that made no progress is not, and retrying it makes
        # this contact the nearest target forever - so it is written off
        # for this session, `swept` meaning "do not retry now" exactly as
        # the rover's `visited` does (D-026).
        if pioneer.battery.level() >= STRANDED_FRACTION:
            swept[point_id(x, y)] = True
        return False

    record_drain(pioneer, start_wh - pioneer.battery.wh(), metres)

    status(pioneer, "scanning", point_id(x, y))
    (state, found) = sweep(pioneer)

    # Anything but "busy" is an answer, and an answer means move on.
    # Without this a contact under a deposit this sonar cannot classify
    # keeps its "?" forever, and it is the nearest target every pass.
    if state != "busy":
        swept[point_id(x, y)] = True

    if state in SWEEP_PARTIAL:
        remember_unresolved(x, y, state)

    if len(found) == 0:
        return state != "busy"

    status(pioneer, "surveying", point_id(x, y))
    for contact in found:
        resolve(pioneer, contact)
    return True


def next_target(pioneer):
    """The nearest unscanned "?" this charge can reach and return from.

    None when there is nothing left in reach, which is the signal to
    fall back to ring waypoints - or, at base, to stop leaving.
    """
    reachable = []
    for point in survey.unscanned_points():
        if swept.get(point_id(point["x"], point["y"]), False):
            continue
        if not survey.contains(point["x"], point["y"]):
            continue
        if not can_reach_and_return(pioneer, point["x"], point["y"]):
            continue
        point["distance"] = pioneer.nav.get_distance_to(point["x"],
                                                        point["y"])
        reachable.append(point)
    if len(reachable) == 0:
        return None
    return sorted(reachable, key=lambda point: point["distance"])[0]


def next_filler(pioneer, waypoints, start):
    """The next ring waypoint worth driving to, and where to resume.

    Ring waypoints are filler, not the plan: at 50 m a sweep covers
    almost no ground, so this is only what the Pioneer does with a
    charge it has nothing better to spend (D-018).

    Returns (point, index). The point is None when a full turn of the
    ring finds nothing left unswept and in reach - which means there is
    nothing to do but wait, and spinning the list again would cost a
    pass for nothing.
    """
    index = start
    for step in range(len(waypoints)):
        (x, y) = waypoints[index]
        index = (index + 1) % len(waypoints)
        if swept.get(point_id(x, y), False):
            continue
        if not survey.contains(x, y):
            continue
        if not can_reach_and_return(pioneer, x, y):
            continue
        return ({"x": x, "y": y}, index)
    return (None, index)


# --- main loop ----------------------------------------------------------

def run(pioneer, radius=survey.CLUSTER_RADIUS, rings=FILLER_RINGS):
    """Survey, rank, validate, report. Forever, and without building.

    pioneer   the Pioneer this script runs inside - pass `self`
    radius    how far apart two contacts can be and still be one place
    rings     rings of filler waypoints, walked only once every "?" in
              reach has been swept

    The shortlist is published once before anything moves, and again
    whenever a sweep changes it.
    """
    find_home(pioneer)
    can_sweep = mounted(pioneer, "sonar")

    caps.report("Pioneer online", caps.common() + [
        ("sonar", sonar_label(pioneer)),
        ("battery", f"{round(pioneer.battery.capacity())} Wh"),
        ("cargo bins", cargo_bins(pioneer)),
        ("constructor", mounted(pioneer, "constructor")),
        ("journal", survey.journal() is not None),
        ("blueprint", blueprint() is not None),
        ("markers", markers() is not None),
        ("unscanned points", len(survey.unscanned_points())),
        ("home", f"{HOME['id']} at ({round(HOME['x'], 1)},"
                 f" {round(HOME['y'], 1)})"),
    ])

    # The desk survey, before the vehicle moves (D-018).
    report(shortlist(radius))

    waypoints = [(HOME["x"], HOME["y"])]
    if can_sweep:
        waypoints = prospect_points(pioneer.sonar.range(), rings)
    else:
        notify("Scout: no Sonar Module mounted - it can rank what is"
               " already known but cannot find anything new", "warn")

    index = 0

    while True:

        level = pioneer.battery.level()
        home = at_home(pioneer)

        # --- stranded in the field ---------------------------------------
        # Only in the field. A flat Pioneer sitting at base is waiting for
        # a charge, not stranded, and calling that stranded would send a
        # rescue drone to a vehicle already parked at the station.
        if level < STRANDED_FRACTION and not home:
            status(pioneer, "stranded")
            notify("Pioneer battery critical - needs a rescue dispatch",
                   "warn")
            sleep(IDLE_INTERVAL)
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

        # --- nothing to sweep with ---------------------------------------
        if not can_sweep:
            idle(pioneer, radius)
            continue

        # Picked before the depart gate, because a target in hand is that
        # gate's have_target: next_target() has already filtered through
        # can_reach_and_return, so a point coming back means the round
        # trip fits on the charge we are sitting on. Only a real "?"
        # counts - leaving the pad early for a filler waypoint would
        # trade half the range for ground worth almost nothing.
        target = next_target(pioneer)

        # --- not enough to start a new trip ------------------------------
        # In the field the floor is the whole rule; on the pad the
        # station's own signal decides, because that is the only place
        # the signal means anything (D-027).
        if not home:
            if level < DEPART_FRACTION:
                status(pioneer, "returning")
                go_home(pioneer)
                continue
        elif not ready_to_depart(pioneer, level, target is not None):
            status(pioneer, "waiting_for_charge")
            wait_for_charge(pioneer, level)
            continue

        if target is None:
            (target, index) = next_filler(pioneer, waypoints, index)

        if target is None:
            if not home:
                status(pioneer, "returning")
                go_home(pioneer)
                continue
            idle(pioneer, radius)
            continue

        working()
        if sweep_at(pioneer, target["x"], target["y"]):
            report(shortlist(radius))
