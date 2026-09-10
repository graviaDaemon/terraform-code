"""Signal Bus helpers: live values shared between running scripts.

Libraries share code, the Signal Bus shares live values, the Data Archive
stores learned knowledge. This is the middle one.

Every function degrades safely when `get_component("comms")` returns None,
so the same scripts run before and after Signal Bus research. Readers fall
back to their own local reads; they never block on the bus being there.

Channel ids are declared here so a typo is an import error rather than a
channel that silently never receives anything.
"""

# --- channels -----------------------------------------------------------

POWER_MODE = "power.mode"            # broadcast: "normal" | "conserve"
POWER_BUDGET = "power.budget"        # broadcast: {stored, capacity, net, need_wh, hours_to_dawn, mode}
POWER_SHED = "power.shed"            # broadcast: {machine_id: reason} switched off by the supervisor
POWER_LEDGER = "power.ledger"        # broadcast: night/day ledger, Data Archive fallback
BIO_WANTED = "bio.wanted"            # broadcast: list of wanted fragment ids
HEATER_TABLE = "terraform.heater_table"   # broadcast: {thermal_state: watts}
LAB_STATUS = "bio.lab.status"        # heartbeat from the Bio Lab
COLLECTOR_STATUS = "bio.collector.status"
EXCHANGE_STATUS = "bio.exchange.status"
VEHICLE_STATUS = "vehicle.status"    # prefix: "vehicle.status:<vehicle_id>"
ROVER_CLAIM = "rover.claim"          # prefix: "rover.claim:<site_id>" = rover id, fresh 10 min
EARTH_DEMAND = "earth.demand"        # broadcast: {item_id: units still owed}
SCOUT_CANDIDATES = "scout.candidates"     # broadcast: ranked outpost shortlist


def vehicle_channel(vehicle_id) -> str:
    """Per-vehicle status channel, e.g. "vehicle.status:rover_1"."""
    return VEHICLE_STATUS + ":" + vehicle_id


def claim_channel(site_id) -> str:
    """Per-site claim channel, e.g. "rover.claim:site_12"."""
    return ROVER_CLAIM + ":" + site_id

# --- power modes --------------------------------------------------------

NORMAL = "normal"
CONSERVE = "conserve"
CRITICAL = "critical"                # only in power.budget; power.mode says "conserve"


def comms():
    """The Signal Bus component, or None before Signal Bus research."""
    return get_component("comms")


def publish(channel, value) -> bool:
    """Broadcast `value` on `channel`. True when it landed.

    Broadcasts are not consumed - every reader sees this value until it is
    replaced. Publishing also resets the channel's age, so a loop that
    publishes every pass doubles as that script's heartbeat.

    `value` must be JSON-safe: None, booleans, finite numbers, strings,
    lists, and dicts with STRING keys. A component, a set, or a dict keyed
    by tuples will be rejected as "invalid_value".
    """
    bus = comms()
    if bus is None:
        return False
    result = bus.broadcast(channel, value)
    if result.status != "ok":
        print(f"broadcast {channel}: {result.message}")
        return False
    return True


def clear(channel) -> bool:
    """Drop a channel's broadcast and its queue. True when it held something.

    Publishing None blanks a channel but keeps it, and a save holds only
    128 channels. A channel id minted per subject - one claim per mining
    site - has to be given back rather than blanked, or the bus fills up
    with dead sites and the next broadcast is rejected.
    """
    bus = comms()
    if bus is None:
        return False
    result = bus.clear(channel)
    if result.status not in ["ok", "no_op"]:
        print(f"clear {channel}: {result.message}")
        return False
    return result.count > 0


def read(channel, default=None):
    """Current broadcast value on `channel`, or `default` if there is none.

    Does not check how old the value is - use read_fresh() when a stale
    value would be worse than no value.
    """
    bus = comms()
    if bus is None:
        return default
    value = bus.latest(channel)
    if value is None:
        return default
    return value


def read_fresh(channel, max_age_seconds, default=None):
    """Current value, but only while its publisher is still reporting.

    Returns `default` when there is no bus, nothing has been published,
    the age is unknown, or the last publication is older than
    `max_age_seconds`. Age is in simulation seconds - the same clock as
    sleep() - so set the threshold to a few times the publisher's interval.
    """
    bus = comms()
    if bus is None:
        return default
    info = bus.latest_info(channel)
    if info is None:
        return default
    if info.age_seconds is None:
        return default
    if info.age_seconds > max_age_seconds:
        return default
    return info.value


def is_alive(channel, max_age_seconds) -> bool:
    """True when something published on `channel` recently enough.

    Use it to tell "that script is busy" apart from "that script is not
    running", which is otherwise invisible from the outside.

    Returns True when there is no Signal Bus at all: without one there is
    no way to tell, and crying wolf every pass is worse than staying quiet.
    """
    bus = comms()
    if bus is None:
        return True
    info = bus.latest_info(channel)
    if info is None:
        return False
    if info.age_seconds is None:
        return False
    return info.age_seconds <= max_age_seconds