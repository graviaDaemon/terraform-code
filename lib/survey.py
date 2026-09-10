"""Map knowledge: what the Journal and the planet already know.

    import survey
    survey.surveyed("mineral")
    survey.unscanned_points()

There is no vehicle in this file. Everything here reads components that
answer whether or not anything is driving, and that is the point: the
Journal is written by every sonar sweep any vehicle has ever done and it
survives restarts, vehicle changes and save/load, so a Pioneer with no
sonar can still read ground the rovers covered (D-010).

Site objects do not survive a restart, and a pre-survey object keeps its
hidden fields forever, so everything is flattened now into the same
JSON-safe dict shape `lib/rover.py` already writes to its archive. One
shape means the rover's scoring path takes a Journal record and one of
its own remembered records without knowing the difference.

The Journal has no depletion flag, which is why `rover.sites` does not
simply go away: it is where `exhausted` lives (D-009).
"""

import caps

PLANET_ID = "nocturna"
JOURNAL_ID = "journal"

# Fields that exist only on some site subtypes. Read through getattr()
# so a MiningSite's item_id and a ThermalVent's absence of one are the
# same question with two different answers, rather than an exception.
MINERAL_FIELDS = ["item_id", "hardness", "purity"]

# How far apart two sites can sit and still describe one place worth an
# outpost. An outpost serves a footprint, not a point, so sites a short
# drive apart are one candidate and not two - but a radius wide enough to
# swallow the whole map would rank every candidate identically.
CLUSTER_RADIUS = 120.0


def journal():
    """The Journal component, or None before that research."""
    return caps.component(JOURNAL_ID)


def planet():
    """The planet component, or None when it cannot be read."""
    return caps.component(PLANET_ID)


def distance(ax, ay, bx, by):
    """Straight-line meters between two world coordinates."""
    return sqrt((ax - bx) * (ax - bx) + (ay - by) * (ay - by))


# --- sites --------------------------------------------------------------

def record_of(site):
    """A flat, JSON-safe snapshot of one site of any kind.

    Minerals always carry the three mineral keys, even when they are
    None: a caller that reads `record["purity"]` on an unsurveyed
    deposit should get None rather than a KeyError, because "found but
    not yet resolved" is a normal state for a Journal entry.
    """
    kind = site.kind()
    record = {
        "id": site.id,
        "name": site.name,
        "x": site.x,
        "y": site.y,
        "kind": kind,
        "surveyed": site.surveyed,
    }
    if kind == "mineral":
        for field in MINERAL_FIELDS:
            record[field] = getattr(site, field, None)
    return record


def _flatten(found, kind):
    records = []
    for site in found:
        if kind is not None and site.kind() != kind:
            continue
        records.append(record_of(site))
    return records


def sites(kind=None):
    """Every site sonar has ever classified on this planet, flattened.

    Pass a kind - "mineral", "thermal", "water", "oil", "exotic",
    "inert" - to narrow it. [] before the Journal research, which is a
    real answer: there is no planet-wide record to read yet.
    """
    book = journal()
    if book is None:
        return []
    return _flatten(book.discovered_sites(PLANET_ID), kind)


def surveyed(kind=None):
    """As sites(), but only the fully-resolved ones.

    These are the records with real item_id, hardness and purity on
    them, so this is the list a rover can score.
    """
    book = journal()
    if book is None:
        return []
    return _flatten(book.surveyed_sites(PLANET_ID), kind)


# --- points of interest -------------------------------------------------

def points():
    """Every permanent "?" contact on the map, as flat records.

    Costs nothing to read and needs no sweep: this is the map's own list
    of where something is, without saying what.
    """
    world = planet()
    if world is None:
        return []
    found = []
    for poi in world.points_of_interest():
        found.append({
            "x": poi.x,
            "y": poi.y,
            "kind": poi.kind,
            "scanned": poi.scanned,
        })
    return found


def unscanned_points():
    """The contacts nobody has scanned yet - the scout's target list.

    Driving to one of these and sweeping beats walking blind rings: the
    coordinates are already known, so the sonar range only has to cover
    the contact rather than find it.
    """
    unscanned = []
    for point in points():
        if not point["scanned"]:
            unscanned.append(point)
    return unscanned


# --- geography ----------------------------------------------------------

def bounds():
    """The planet's coordinate bounds, or None when unreadable."""
    world = planet()
    if world is None:
        return None
    return world.get_bounds()


def contains(x, y) -> bool:
    """True when the planet accepts this coordinate.

    True as well when there is no planet component to ask: refusing to
    drive anywhere because the map cannot be read is worse than trying
    and letting nav reject the target.
    """
    world = planet()
    if world is None:
        return True
    return world.contains(x, y)


def biome_at(x, y):
    """Biome id at a coordinate, or None when the planet is unreadable."""
    world = planet()
    if world is None:
        return None
    return world.biome_at(x, y)


# --- clustering ---------------------------------------------------------

def _kinds(members):
    """How many of each kind are in one group, keyed by kind name."""
    counts = {}
    for member in members:
        kind = member.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _centre(members):
    """The mean position of a group's members."""
    total_x = 0
    total_y = 0
    for member in members:
        total_x = total_x + member["x"]
        total_y = total_y + member["y"]
    return (total_x / len(members), total_y / len(members))


def cluster(records, radius=CLUSTER_RADIUS):
    """Group records that sit within `radius` of each other.

    This is what turns "sites" into "places worth an outpost": one
    deposit is a stop, six deposits inside a footprint's drive is a
    reason to build. Returns one dict per group with the centroid, the
    member count, the kinds present and the members themselves.

    Greedy and single-pass: each record joins the first group whose
    centre it is near, and that centre then moves. Ordering therefore
    affects the grouping at the edges, which is acceptable - this ranks
    candidates for a human to choose between, it does not partition the
    planet.
    """
    groups = []
    for record in records:
        joined = None
        for group in groups:
            if distance(record["x"], record["y"],
                        group["x"], group["y"]) <= radius:
                joined = group
                break
        if joined is None:
            groups.append({"x": record["x"], "y": record["y"],
                           "members": [record]})
            continue
        joined["members"].append(record)
        (joined["x"], joined["y"]) = _centre(joined["members"])

    for group in groups:
        group["count"] = len(group["members"])
        group["kinds"] = _kinds(group["members"])
    return groups
