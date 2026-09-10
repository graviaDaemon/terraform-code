"""Fleet manager: keep docked vehicles charged, rescue stranded ones.

    import fleet
    fleet.run(self)

Runs on a Vehicle Charging Station. charge(), dispatch_rescue() and
cancel_rescue() are SELF ONLY, so this has to live in the station's own
script - it cannot be driven from a rover.

Telemetry comes from get_component("fleet").vehicles(), which reports
.battery_level, .x, .y, .is_docked, .is_being_rescued and .rescue_status
for every owned vehicle. No Signal Bus is needed for any of that. The bus
is consulted for one thing only: a vehicle's own INTENT, which telemetry
cannot express - see should_rescue().
"""

import bus
import caps
from vehicle import DEPART_FRACTION

FLEET_ID = "fleet"

# Battery fractions.
CHARGE_TARGET = 1       # top-up goal for a docked vehicle
RESCUE_BELOW = 0.15        # dispatch a rescue under this in the field
RESCUE_TARGET = 0.60       # enough to drive home; a full remote fill is slow
CRITICAL = 0.08            # charge this vehicle even while conserving power

# What a vehicle needs before it will start a trip is the vehicle's own
# figure, imported rather than written down twice: the station tops a
# waiting rover to exactly the level the rover then refuses to leave
# below, so the two numbers are load-bearing together (D-027).

# How stale a rover's own status may be before it is ignored.
INTENT_MAX_AGE = 30

# charge() outcomes that mean the request landed or was unnecessary.
CHARGE_FINE = ["charging", "queued", "target_reached"]

WAITING = "waiting_for_charge"


def vehicles():
    """Snapshots of every owned ground vehicle, or [] if fleet is missing.

    These are snapshots: re-query for fresh positions rather than holding
    a ref across a sleep.
    """
    component = get_component(FLEET_ID)
    if component is None:
        return []
    return component.vehicles()


def intent_of(vehicle_id):
    """What that vehicle says it is doing, or None if it is not reporting.

    Requires the vehicle to be running a script that broadcasts on its
    vehicle.status channel. Absence is normal, not an error.
    """
    return bus.read_fresh(bus.vehicle_channel(vehicle_id), INTENT_MAX_AGE)


def is_waiting(vehicle) -> bool:
    """True when this vehicle says it is parked waiting for a charge.

    Telemetry cannot express it: a waiting rover and an idle one look
    identical from here. A vehicle running no script never says it, and
    is treated as not waiting.
    """
    status = intent_of(vehicle.id)
    return status is not None and status.get("state") == WAITING


def should_rescue(vehicle) -> bool:
    """True when this vehicle needs a drone sent to it.

    dispatch_rescue() STOPS the target so the drone can reach it. That
    makes a needless rescue actively harmful: a rover driving home under
    its own power would be halted mid-route and then wait for a slow
    drone. So a vehicle that reports it is already returning is left
    alone unless it has fallen below the critical floor.
    """
    if vehicle.is_docked:
        return False
    if vehicle.is_being_rescued:
        return False
    if vehicle.battery_level >= RESCUE_BELOW:
        return False

    if vehicle.battery_level > CRITICAL:
        status = intent_of(vehicle.id)
        if status is not None and status.get("state") == "returning":
            return False        # it is handling this itself - do not halt it

    return True


def rescue_worst(station) -> bool:
    """Send the rescue drone to the lowest vehicle that needs it.

    Exactly one drone exists, so this picks the worst case and leaves the
    rest for later passes.
    """
    if station.is_rescuing():
        return False            # avoids the "already_dispatched" rejection

    worst = None
    for vehicle in vehicles():
        if not should_rescue(vehicle):
            continue
        if worst is None or vehicle.battery_level < worst.battery_level:
            worst = vehicle

    if worst is None:
        return False

    result = station.dispatch_rescue(worst.id, RESCUE_TARGET)
    if result.status == "ok":
        notify(f"Rescue dispatched to {worst.name}"
               f" at {round(worst.battery_level * 100)}%", "warn")
        return True
    if result.status == "already_dispatched":
        return False
    notify(f"dispatch_rescue {worst.name}: {result.message}", "warn")
    return False


def charge_docked(station, conserving) -> bool:
    """Queue every docked vehicle that is below the charge target.

    While the base is conserving power, a vehicle is topped to the
    critical floor only: a station draws bay_count x bay_rate from the
    grid the whole time anything is charging, which on a Mk III is 240 W
    - more than the whole terraforming fleet.

    The exception is a vehicle that says it is waiting for a charge. A
    rover will not start a trip under its depart level, so leaving it at
    the critical floor through a run of lean days parks it for good,
    which costs more than the charge does. It is taken to depart level
    and no further. This only ever runs while the station is powered,
    and the supervisor sheds this station first.
    """
    for vehicle in vehicles():
        if not vehicle.is_docked:
            continue

        target = CHARGE_TARGET
        if conserving:
            target = CRITICAL + 0.1     # just enough to be safe, no more
            if is_waiting(vehicle):
                target = DEPART_FRACTION
            elif vehicle.battery_level >= CRITICAL:
                continue

        if vehicle.battery_level >= target:
            continue

        result = station.charge(vehicle.id, target)
        if result.status in CHARGE_FINE:
            continue
        if result.status == "station_offline":
            return False                # transient; try again next pass
        if result.status == "not_docked":
            continue                    # the snapshot went stale
        notify(f"charge {vehicle.name}: {result.message}", "warn")

    return True


STATION_TYPE = "charging_station"

# Last reason given per vehicle, so each episode is explained once.
explained = {}


def station_position(station):
    """This station's own docking point as [x, y], or None."""
    outpost = station.outpost
    if outpost is None:
        return None
    for ref in outpost.buildings(STATION_TYPE):
        if ref.id == station.id:
            return ref.position
    return None


def explain_waiting(station, conserving):
    """Say why a rover that reports waiting_for_charge is not being charged.

    From the rover's side, "docked and ignored" and "parked just outside
    the footprint" look identical: it sits at base, idle, not charging.
    The manager can see both the flag it gates on and the power mode, so
    it names the reason - once per episode, not every pass.
    """
    pad = station_position(station)

    for vehicle in vehicles():
        waiting = is_waiting(vehicle)

        reason = None
        if waiting and not vehicle.is_docked:
            reason = "the outpost does not count it as docked"
        elif (waiting and conserving
              and vehicle.battery_level >= DEPART_FRACTION):
            reason = "power.mode is conserve and it is already at depart level"

        if reason is None:
            explained[vehicle.id] = None
            continue
        if explained.get(vehicle.id) == reason:
            continue
        explained[vehicle.id] = reason

        where = ""
        if pad is not None:
            dx = vehicle.x - pad[0]
            dy = vehicle.y - pad[1]
            where = f", {round(sqrt(dx * dx + dy * dy), 1)} m from {station.id}"
        notify(f"{vehicle.name} is waiting for a charge at"
               f" ({round(vehicle.x, 1)}, {round(vehicle.y, 1)}){where}"
               f" but {reason}", "warn")


def watch_bays(station, bays):
    """Re-read bay count and rate every pass, so an upgrade pack applied
    mid-run is announced rather than discovered at the next restart."""
    count = station.get_bay_count()
    rate = station.get_bay_rate()
    if count == bays["count"] and rate == bays["rate"]:
        return
    if bays["count"] is not None:
        notify(f"Charging station now Mk {caps.tier(station)}: {count} bays"
               f" at {rate} W, {count * rate} W from the grid while charging")
    bays["count"] = count
    bays["rate"] = rate


def run(station, interval=10):
    """Charge and rescue forever.

    station   the Vehicle Charging Station this script runs inside - `self`
    interval  seconds between passes
    """
    bays = {"count": None, "rate": None}
    watch_bays(station, bays)
    caps.report("Fleet manager online", caps.common() + [
        ("fleet", get_component(FLEET_ID) is not None),
        ("tier", caps.tier(station)),
        ("bays", bays["count"]),
        ("bay W", bays["rate"]),
    ])

    while True:
        watch_bays(station, bays)
        conserving = bus.read_fresh(bus.POWER_MODE, 30,
                                    bus.NORMAL) == bus.CONSERVE

        # Rescue first, and regardless of power mode. A stranded vehicle
        # costs more than the drone's draw.
        rescue_worst(station)

        charge_docked(station, conserving)

        explain_waiting(station, conserving)

        sleep(interval)