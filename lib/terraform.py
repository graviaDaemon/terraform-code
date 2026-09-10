"""Controllers for the three terraforming tracks: heat, oxygen, pressure.

Every terraforming machine of a given type runs the same loop, so the loop
lives here and each machine script is two lines:

    import terraform
    terraform.run_oxygen(self)

`self` does not exist inside a library, so the machine is passed in. That
matters more here than elsewhere: set_power(), set_intake(), sync() and
dump_waste() are all SELF ONLY and will error if you reach them through
get_component(). Pass `self`, never get_component("heater_1").
"""

import bus
import caps

# How stale the power broadcast may be before readers ignore it and run at
# full power. A publisher that stops must not leave the fleet throttled.
POWER_MAX_AGE = 30

# The fluid each track needs from Mk III on. A starved machine silently
# falls back to the previous tier's output, so starvation is announced.
HEAT_FEED = "steam_in"
WATER_FEED = "water_in"


def _feed_status(gen, port_name):
    """What feeds `port_name` on `gen`, read inside try/except so Mk I and
    Mk II machines, which may lack the port, are unaffected."""
    try:
        port = getattr(gen, port_name)
        source = port.connected_to()
        level = port.level()
    except Exception:
        return "no such port"
    if source == "":
        return "nothing connected"
    return f"fed by {source}, {round(level, 2)} t buffered"


def _announce_tier(label, gen):
    caps.report(f"{label} {gen.name} online", caps.common() + [
        ("tier", caps.tier(gen)),
        ("degraded", caps.degraded(gen)),
    ])


def _watch_degraded(label, gen, port_name, seen):
    """Name the starved port the moment is_degraded() flips to True."""
    now = caps.degraded(gen)
    if now and not seen["degraded"]:
        notify(f"{label} {gen.name}: Mk {caps.tier(gen)} pack starved of"
               f" {port_name} ({_feed_status(gen, port_name)}) - running a"
               f" tier down until the feed returns", "warn")
    elif seen["degraded"] and not now:
        notify(f"{label} {gen.name}: {port_name} feed restored")
    seen["degraded"] = now

# --- heat ---------------------------------------------------------------

HEATER_TABLE_KEY = "terraform.heater_optimal"
MIN_POWER = 1              # 0 means off, so calibration starts at 1
MAX_POWER = 10             # set_power clamps outside 0-10

# --- oxygen -------------------------------------------------------------

ATMOSPHERE_ID = "atmosphere"
INTAKE_DIVISOR = 10        # peak efficiency is exactly ambient CO2 / 10
WASTE_DUMP_LOW = 50        # the penalty-free dump window is 50-60
WASTE_DUMP_HIGH = 60


def conserving() -> bool:
    """True when the power monitor is currently asking for restraint.

    Falls back to False when the broadcast is missing or stale, so a stopped
    solar script releases the fleet rather than freezing it at zero power.
    """
    return bus.read_fresh(bus.POWER_MODE, POWER_MAX_AGE, bus.NORMAL) == bus.CONSERVE


def _notebook():
    """The Data Archive component, or None before that research unlocks."""
    return get_component("notebook")


def _shared_table():
    """Best available {thermal_state: watts}, merging both sinks.

    Reads the bus first, then lets the Data Archive override it, so:

      - before Data Archive research, the bus copy is the whole table. The
        docs say save/load preserves a channel's latest value, so it is a
        real stand-in rather than a same-session cache.
      - the day Data Archive unlocks, the Archive starts empty while the
        bus still holds everything learned so far. Merging carries that
        across instead of re-sweeping every state from scratch; the next
        _publish_table() writes the merged result into the Archive.
      - afterwards the Archive is authoritative, and the bus is the fast
        path that reaches a sibling heater mid-run.
    """
    table = dict(bus.read(bus.HEATER_TABLE, {}))
    book = _notebook()
    if book is not None:
        stored = book.get(HEATER_TABLE_KEY, {})
        for state in stored:
            table[state] = stored[state]
    return table


def _publish_table(table):
    """Write the learned table everywhere it can be shared.

    Both sinks, always: the Archive for durability once it exists, and the
    bus so a sibling heater mid-sweep picks it up immediately rather than
    on its next restart.
    """
    book = _notebook()
    if book is not None:
        written = book.set(HEATER_TABLE_KEY, table)
        if written.status != "ok":
            print(f"notebook.set {HEATER_TABLE_KEY}: {written.message}")
    bus.publish(bus.HEATER_TABLE, table)


def _merge_shared(optimal):
    """Fold in anything another heater has learned since this script started."""
    shared = _shared_table()
    for state in shared:
        if state not in optimal:
            optimal[state] = shared[state]
    return optimal


def calibrate_heater(gen, state):
    """Find the power setting that reads 100% efficiency for `state`.

    Efficiency peaks at exactly one setting and falls off steeply - roughly
    31% one step away and a 10% floor beyond that - so this scans 1..10 and
    stops at the first 100%. Only ten ticks, and only once per state.

    Falls back to the best setting seen if nothing reads 100%, which happens
    when the heater is degraded or unpowered during the sweep.
    """
    best_power = MIN_POWER
    best_efficiency = -1
    for watts in range(MIN_POWER, MAX_POWER + 1):
        gen.set_power(watts)
        efficiency = gen.efficiency()
        if efficiency > best_efficiency:
            best_efficiency = efficiency
            best_power = watts
        if efficiency >= 100:
            return watts
    notify(f"Heater: no 100% setting for '{state}',"
           f" best was {best_efficiency}% at {best_power}W", "warn")
    return best_power


def _announce_heater(gen, state, watts):
    notify(f"heater {gen.name}: {state} -> {watts} W,"
           f" efficiency {round(gen.efficiency())} %,"
           f" output {round(gen.output(), 1)}/h")


def run_heater(gen, interval=5):
    """Hold the heater at the optimal power for the current thermal state.

    gen       the Heat Generator this script runs inside - pass `self`
    interval  seconds between checks

    Holds the optimal setting in every power mode. Conserve mode used to
    drop the heater to 0W; with the batteries never recovering past the
    resume line that latched the heaters off for days and froze the
    Temperature research track, which gates the fixes to the power problem
    itself (plan/00-decisions.md D-002). A heater draws at most 10W.

    Every applied state change is announced as a toast, so a heater that
    is silently producing nothing is visible without opening its card.

    thermal_state() is stable for a whole game day and has only four values,
    so the table is learned once and then it is a lookup. With Data Archive
    researched the table survives restarts and is shared by every heater.
    """
    optimal = _shared_table()
    applied_state = ""
    seen = {"degraded": False}
    _announce_tier("Heater", gen)

    while True:
        _watch_degraded("Heater", gen, HEAT_FEED, seen)
        state = gen.thermal_state()

        if state != applied_state:
            if state not in optimal:
                # A sibling heater may have learned this state while this
                # script was running. Ask before paying for a ten-step sweep.
                optimal = _merge_shared(optimal)

            if state not in optimal:
                optimal[state] = calibrate_heater(gen, state)
                notify(f"Heater calibrated: {state} -> {optimal[state]}W")
            gen.set_power(optimal[state])
            applied_state = state
            _announce_heater(gen, state, optimal[state])
            # Written on every state change, not just after a sweep, so a
            # table learned in memory before Data Archive research is
            # persisted automatically the first day after it unlocks.
            _publish_table(optimal)

        elif gen.efficiency() < 100:
            # The cached value no longer peaks - a stale or borrowed table.
            # Drop it and re-learn on the next pass.
            notify(f"Heater: '{state}' setting no longer optimal, recalibrating", "warn")
            optimal.pop(state, None)
            applied_state = ""
            continue

        sleep(interval)


def run_oxygen(gen, atmosphere_id=ATMOSPHERE_ID, interval=1):
    """Track the CO2 sweet spot and dump waste inside the clean window.

    gen            the Oxygen Generator this script runs inside - pass `self`
    atmosphere_id  component to read ambient CO2 from
    interval       seconds between passes

    set_intake() must be called every iteration, not once at startup: the
    sweet spot is ambient CO2 / 10 and ambient CO2 falls as the fleet works,
    so a fixed intake drifts off peak and stays there.
    """
    atmosphere = get_component(atmosphere_id)
    seen = {"degraded": False}
    _announce_tier("Oxygen", gen)

    while True:
        _watch_degraded("Oxygen", gen, WATER_FEED, seen)
        gen.set_intake(atmosphere.get_co2() / INTAKE_DIVISOR)

        waste = gen.waste()
        if waste >= WASTE_DUMP_LOW and waste <= WASTE_DUMP_HIGH:
            dumped = gen.dump_waste()
            if dumped.status != "ok":
                print(f"dump_waste: {dumped.message}")
            elif dumped.penalty > 0:
                notify(f"O2 dump inside the clean window still cost"
                       f" {dumped.penalty} penalty", "warn")
        elif waste > WASTE_DUMP_HIGH:
            # Past the clean window output is already falling, and at 100 it
            # stalls outright. A penalised dump beats a stall.
            dumped = gen.dump_waste()
            notify(f"O2 waste reached {waste} - dumped late,"
                   f" penalty {dumped.penalty}", "warn")

        sleep(interval)


def run_pressure(gen, interval=1):
    """Sync once inside every resonance window.

    gen       the Pressure Generator this script runs inside - pass `self`
    interval  seconds between gauge reads

    A hit is +25% efficiency and a miss is -10%, and a sweep with no sync at
    all counts as a miss - so the loop tracks whether this sweep has been
    synced and says so out loud when one is missed, rather than quietly
    bleeding efficiency.
    """
    synced = False
    last_gauge = -1
    seen = {"degraded": False}
    _announce_tier("Pressure", gen)

    while True:
        _watch_degraded("Pressure", gen, WATER_FEED, seen)
        gauge = gen.gauge()

        if gauge < last_gauge:            # the sweep wrapped past 100
            if not synced:
                notify("Pressure: sync window missed (-10% efficiency)", "warn")
            synced = False
        last_gauge = gauge

        if not synced:
            low = gen.next_window_low()
            high = gen.next_window_high()
            # Inclusive: the edges are inside the window.
            if low <= gauge and gauge <= high:
                result = gen.sync()
                if result.status == "ok":
                    synced = True
                else:
                    print(f"sync: {result.message}")

        sleep(interval)