"""Power supervisor: measure the night, shed batch loads, restore them.

    import power
    power.run(self)          # from solar_1 only

The single publisher of "power.mode", "power.budget" and "power.shed",
and the only script that flips breakers (plan/00-decisions.md D-004).
The panel it runs on keeps tracking the sun through lib/solar.py.

The budget is "Wh needed to reach dawn", from a measured night load and
night length kept in the Data Archive (D-003). Terraforming machines,
solar panels and batteries are never touched (D-002).

It supervises ONE grid - the one its own panel sits on - not the planet
(D-019). Generation, storage and demand pool across a connected subnet
and stop dead at its edge, so an outpost that is not wired to this one is
energy this base cannot spend and machines whose breakers buy it nothing.
"""

import bio
import bus
import caps
import earth
import solar
import storage

INTERVAL = 10
SAFETY = 1.25
CRITICAL_BELOW = 0.4       # of need_wh: shed everything at once
CRITICAL_EXIT = 0.5        # leave critical only above this
CONSERVE_EXIT = 1.2        # leave conserve only above this
DAYLIGHT_FRACTION = 0.5    # normal in daylight with net > 0 above this
MIN_HOURS_TO_DAWN = 1.0
DEFAULT_NIGHT_HOURS = 12.0
EMA_ALPHA = 0.3
IDLE_PASSES = 30
WANTED_MAX_AGE = 60

CONSERVE_SHED = ["charging_station", "smelter", "supply_dock"]
CRITICAL_SHED = CONSERVE_SHED + ["bio_collector", "bio_lab", "bio_exchange"]
IDLE_TYPES = ["smelter", "supply_dock", "bio_collector", "bio_lab", "bio_exchange"]
BIO_TYPES = ["bio_collector", "bio_lab", "bio_exchange"]
DAYLIGHT = ["dawn", "day"]

# Never breaker-switched (D-002): terraforming, generation, storage, and
# passive stores. Anything deployed outside this and CRITICAL_SHED is
# reported at startup as unmanaged.
UNTOUCHED = ["temp_heater", "oxygen_generator", "pressure_generator",
             "solar_generator", "battery", "battery_large", "nuclear_battery",
             "lightning_rod", "oil_generator", "reactor", "steam_turbine",
             "storage_bin", "warehouse", "large_warehouse"]

LEDGER_KEY = "power.ledger"
SHED_KEY = "power.shed"

NORMAL = bus.NORMAL
CONSERVE = bus.CONSERVE
CRITICAL = bus.CRITICAL
IDLE = "idle"

state = {
    "mode": NORMAL,
    "phase": None,
    "phase_since": 0.0,
    "phase_complete": False,
    "phase_gen_wh": 0.0,
    "phase_con_wh": 0.0,
    "last_hours": None,
    "peak_consumed": 0.0,
    "night_min": None,
    "empty_wanted_passes": 0,
    "dawn_reported_day": None,
    "warned": {},
    "grid_machines": None,
}


# --- persistence --------------------------------------------------------

def _notebook():
    return get_component("notebook")


def _load(key, channel, default):
    book = _notebook()
    if book is not None:
        stored = book.get(key, None)
        if stored is not None:
            return stored
    published = bus.read(channel, None)
    if published is None:
        return default
    return published


def _save(key, channel, value):
    book = _notebook()
    if book is not None:
        written = book.set(key, value)
        if written.status != "ok":
            print(f"archive {key}: {written.message}")
    else:
        bus.publish(channel, value)


def default_ledger():
    return {
        "grid": None,
        "night_hours": DEFAULT_NIGHT_HOURS,
        "day_hours": None,
        "night_load_w": None,
        "day_gen_w": None,
        "day_load_w": None,
        "nights": 0,
        "days": 0,
        "night_began": None,
        "night_min_stored": None,
    }


def load_ledger():
    ledger = default_ledger()
    stored = _load(LEDGER_KEY, bus.POWER_LEDGER, {})
    for key in stored:
        ledger[key] = stored[key]
    return ledger


def save_ledger(ledger):
    _save(LEDGER_KEY, bus.POWER_LEDGER, ledger)


def load_shed():
    record = _load(SHED_KEY, bus.POWER_SHED, {})
    return dict(record)


def save_shed(record):
    _save(SHED_KEY, bus.POWER_SHED, record)
    bus.publish(bus.POWER_SHED, record)


# --- ledger -------------------------------------------------------------

def _ema(old, new, count):
    if old is None or count == 0:
        return new
    return old + EMA_ALPHA * (new - old)


def fold_phase(ledger, phase, hours, gen_wh, con_wh):
    if hours <= 0:
        return
    if phase == "night":
        n = ledger["nights"]
        ledger["night_hours"] = _ema(ledger["night_hours"], hours, n)
        ledger["night_load_w"] = _ema(ledger["night_load_w"], con_wh / hours, n)
        ledger["night_min_stored"] = state["night_min"]
        ledger["nights"] = n + 1
    elif phase == "day":
        n = ledger["days"]
        ledger["day_hours"] = _ema(ledger["day_hours"], hours, n)
        ledger["day_gen_w"] = _ema(ledger["day_gen_w"], gen_wh / hours, n)
        ledger["day_load_w"] = _ema(ledger["day_load_w"], con_wh / hours, n)
        ledger["days"] = n + 1


def dawn_report(ledger, clock):
    day = clock.get_day()
    if state["dawn_reported_day"] == day:
        return
    state["dawn_reported_day"] = day
    load = ledger["night_load_w"]
    load_text = "unmeasured" if load is None else f"{round(load, 1)} W"
    low = ledger["night_min_stored"]
    low_text = "unmeasured" if low is None else f"{round(low)} Wh"
    notify(f"Dawn, day {day}: night {round(ledger['night_hours'], 1)} h,"
           f" load {load_text}, minimum stored {low_text}")


def account(ledger, summary, clock):
    """Accumulate this pass into the current phase; fold on phase change."""
    now = clock.elapsed_game_hours()
    phase = clock.get_time_of_day()

    if summary.consumed > state["peak_consumed"]:
        state["peak_consumed"] = summary.consumed

    if state["last_hours"] is not None:
        dt = now - state["last_hours"]
        if dt > 0:
            state["phase_gen_wh"] = state["phase_gen_wh"] + summary.generated * dt
            state["phase_con_wh"] = state["phase_con_wh"] + summary.consumed * dt
    state["last_hours"] = now

    if phase == "night":
        if state["night_min"] is None or summary.stored < state["night_min"]:
            state["night_min"] = summary.stored

    if phase == state["phase"]:
        return

    previous = state["phase"]
    if previous is not None and state["phase_complete"]:
        fold_phase(ledger, previous, now - state["phase_since"],
                   state["phase_gen_wh"], state["phase_con_wh"])

    state["phase"] = phase
    state["phase_since"] = now
    state["phase_complete"] = previous is not None
    state["phase_gen_wh"] = 0.0
    state["phase_con_wh"] = 0.0

    if phase == "night":
        ledger["night_began"] = now
        state["night_min"] = summary.stored
    if phase == "dawn" and previous == "night":
        dawn_report(ledger, clock)
    save_ledger(ledger)


# --- budget and mode ----------------------------------------------------

def night_load(ledger):
    if ledger["nights"] > 0 and ledger["night_load_w"] is not None:
        return ledger["night_load_w"]
    return state["peak_consumed"]


def hours_to_dawn(ledger, phase, now):
    if phase != "night":
        return ledger["night_hours"]
    began = ledger["night_began"]
    if began is None:
        return ledger["night_hours"]
    remaining = ledger["night_hours"] - (now - began)
    if remaining < MIN_HOURS_TO_DAWN:
        return MIN_HOURS_TO_DAWN
    return remaining


def next_mode(current, stored, need, net, phase):
    if need <= 0:
        return NORMAL
    if stored < need * CRITICAL_BELOW:
        return CRITICAL
    if current == CRITICAL and stored < need * CRITICAL_EXIT:
        return CRITICAL
    exit_line = need
    if current != NORMAL:
        exit_line = need * CONSERVE_EXIT
    if stored >= exit_line:
        return NORMAL
    if phase in DAYLIGHT and net > 0 and stored >= need * DAYLIGHT_FRACTION:
        return NORMAL
    return CONSERVE


def publish(mode, summary, need, hours):
    bus.publish(bus.POWER_BUDGET, {
        "stored": round(summary.stored, 1),
        "capacity": round(summary.capacity, 1),
        "net": round(summary.net, 1),
        "need_wh": round(need, 1),
        "hours_to_dawn": round(hours, 2),
        "mode": mode,
    })
    published = mode
    if mode == CRITICAL:
        published = CONSERVE
    bus.publish(bus.POWER_MODE, published)


# --- the grid this supervisor is on -------------------------------------

def resolve_grid(control, gen):
    """The PowerGrid this panel sits on, or None.

    None is a normal answer, not an error: grid() returns None for a
    mobile, under-construction, non-grid or currently unmapped target.
    """
    return control.grid(gen.id)


def grid_anchor(grid):
    """The grid's anchor id, or None when there is no grid to name."""
    if grid is None:
        return None
    return grid.anchor_id


def supply(control, grid):
    """The figures to budget from: this grid's, or a planet-wide fallback.

    control.total() is "a planet-wide PowerSummary across every
    independent grid". With one grid that is right by accident; the
    moment a second outpost exists and is not yet wired, it pools solar
    and batteries this base physically cannot reach into the night budget
    and skips conserving on a night it should have (D-019). Both objects
    carry the same generated/consumed/net/stored/capacity fields, so
    every reader below takes either one.
    """
    if grid is not None:
        return grid
    _warn_once("power_grid", "Power: power_control.grid() does not place"
                             " this panel on a grid - budgeting from the"
                             " planet-wide total, which pools every outpost"
                             " whether it is wired to this one or not")
    return control.total()


def scope_to(grid):
    """Limit machines_of() to `grid`'s machines. None means no limit."""
    if grid is None:
        state["grid_machines"] = None
    else:
        state["grid_machines"] = grid.machine_ids


def on_grid(machine_id) -> bool:
    """True when this machine shares the supervisor's grid.

    A machine on another grid is not sheddable relief: its breaker costs
    this subnet nothing, because its draw was never funded from here.
    """
    scope = state["grid_machines"]
    if scope is None:
        return True
    return machine_id in scope


def other_grids(control, anchor):
    """Every PowerGrid this supervisor is not managing."""
    others = []
    for grid in control.grids():
        if grid.anchor_id == anchor:
            continue
        others.append(grid)
    return others


def describe_grid(grid) -> str:
    """One unmanaged grid, including the energy this supervisor cannot reach.

    The stored figure is the only evidence an unwired power outpost is
    working: no script reads that subnet, and its panels have no
    consumers there to prove themselves against (D-022).
    """
    generation = "has a generator"
    if not grid.has_generator:
        generation = "no generator"
    return (f"{grid.anchor_id} ({generation}, {round(grid.stored)} of"
            f" {round(grid.capacity)} Wh, net {round(grid.net, 1)} W)")


def watch_grids(control, anchor, seen):
    """Name the grids this supervisor is NOT managing, when that changes.

    An unwired outpost is a separate subnet whose energy this base cannot
    reach and whose machines it cannot relieve. It should be a line in
    the log, not a silence.

    Change is judged on membership and generation, never on the live
    figures - they move every pass, and a report that fires every pass is
    telemetry, not a notification.
    """
    others = other_grids(control, anchor)
    names = []
    described = []
    for grid in others:
        names.append(f"{grid.anchor_id}/{grid.has_generator}")
        described.append(describe_grid(grid))
    if names == seen["others"]:
        return
    seen["others"] = names
    where = anchor
    if where is None:
        where = "planet-wide fallback"
    if len(names) == 0:
        notify(f"Power: supervising {where} - 1 grid, 0 unmanaged")
        return
    notify(f"Power: supervising {where} - {len(names)} unmanaged grid(s): "
           + ", ".join(described), "warn")


# --- breakers -----------------------------------------------------------

def machines_of(type_ids):
    """[(id, type_id)] on THIS grid, in the order of `type_ids`.

    caps.buildings() spans every outpost, so without the grid filter the
    supervisor would flip breakers on machines that are not on its subnet
    and get no relief from doing so.
    """
    found = []
    for type_id in type_ids:
        for ref in caps.buildings(type_id):
            if on_grid(ref.id):
                found.append((ref.id, type_id))
    return found


def unmanaged_types():
    """Deployed building types this supervisor neither sheds nor guards.

    Reported once at startup so a new machine type is added to the shed
    lists deliberately rather than silently left running through a
    critical night.
    """
    return [type_id for type_id in caps.building_types()
            if type_id not in CRITICAL_SHED and type_id not in UNTOUCHED]


def wants_shed(mode, type_id) -> bool:
    if mode == CRITICAL:
        return type_id in CRITICAL_SHED
    if mode == CONSERVE:
        return type_id in CONSERVE_SHED
    return False


def switch_off(control, machine_id, reason) -> bool:
    if not control.can_power_off(machine_id):
        return False
    result = control.set_powered(machine_id, False)
    if result.status == "ok":
        notify(f"Power: {machine_id} off ({reason})", "warn")
        return True
    if result.status != "not_toggleable":
        print(f"set_powered {machine_id} off: {result.message}")
    return False


def switch_on(control, machine_id, reason):
    """"ok" when the machine is on, "gone" when it never will be, else "retry"."""
    if control.is_powered(machine_id):
        return "ok"
    result = control.set_powered(machine_id, True)
    if result.status == "ok":
        notify(f"Power: {machine_id} back on (was off: {reason})")
        return "ok"
    if result.status in ["not_found", "not_toggleable"]:
        notify(f"Power: cannot restore {machine_id}: {result.message}", "warn")
        return "gone"
    print(f"set_powered {machine_id} on: {result.message}")
    return "retry"


def shed(control, record, mode, net):
    if mode == CONSERVE:
        if net >= 0:
            return
        for (machine_id, type_id) in machines_of(CONSERVE_SHED):
            if machine_id in record or not control.is_powered(machine_id):
                continue
            if switch_off(control, machine_id, CONSERVE):
                record[machine_id] = CONSERVE
                return
    elif mode == CRITICAL:
        for (machine_id, type_id) in machines_of(CRITICAL_SHED):
            if machine_id in record or not control.is_powered(machine_id):
                continue
            if switch_off(control, machine_id, CRITICAL):
                record[machine_id] = CRITICAL


def restore_one(control, record):
    """Bring back the most recently shed non-idle machine. True when one came on."""
    ids = list(record)
    for k in range(len(ids)):
        machine_id = ids[len(ids) - 1 - k]
        reason = record[machine_id]
        if reason == IDLE:
            continue
        outcome = switch_on(control, machine_id, reason)
        if outcome == "retry":
            return False
        record.pop(machine_id)
        if outcome == "ok":
            return True
    return False


def restore_all(control, record):
    ids = list(record)
    for k in range(len(ids)):
        machine_id = ids[len(ids) - 1 - k]
        outcome = switch_on(control, machine_id, record[machine_id])
        if outcome != "retry":
            record.pop(machine_id)


# --- idle rules ---------------------------------------------------------

def _warn_once(machine_id, text):
    if state["warned"].get(machine_id):
        return
    state["warned"][machine_id] = True
    notify(text, "warn")


def smelter_idle(smelter):
    """True only when NOTHING this smelter could convert is to be had.

    A smelter with no recipe set is never judged idle (D-007): it has no
    inputs, so "no bin holds any input" would be vacuously true and its
    breaker could never come back.

    Every unlocked recipe is tested, not just the latched one. The recipe
    is chosen at runtime from live demand now, and judging the latched
    one alone deadlocks: a smelter that latches titanium with no titanium
    ore in the bins is switched off, and its own script - the only thing
    that would ever re-read demand and switch back - is paused with it
    (D-028). Staying powered whenever there is anything to convert is
    also the safe direction of the two.
    """
    if len(smelter.get_recipe_inputs()) == 0:
        return None
    if smelter.is_running():
        return False
    if smelter.get_input_count() > 0 or smelter.get_output_count() > 0:
        return False
    sources = storage.discover(machine=smelter) + [storage.INVENTORY]
    for recipe in smelter.list_recipes():
        for item_id in recipe.inputs:
            if storage.source_for(sources, item_id, 1) is not None:
                return False
    return True


def dock_idle(dock):
    if dock.current_order() is not None:
        return False
    return len(earth.available_orders()) == 0


def wanted_fragments():
    """Wanted fragment ids, from the bus while the Exchange publishes, else read direct."""
    published = bus.read_fresh(bus.BIO_WANTED, WANTED_MAX_AGE, None)
    if published is not None:
        return published
    wanted = {}
    seen = False
    for (machine_id, type_id) in machines_of(["bio_exchange"]):
        exchange = get_component(machine_id)
        if exchange is None:
            continue
        found = bio.wanted_fragments(exchange.orders())
        if found is None:
            continue
        seen = True
        for fragment_id in found:
            wanted[fragment_id] = True
    if not seen:
        return None
    return list(wanted)


def bio_idle():
    wanted = wanted_fragments()
    if wanted is None:
        return None
    if len(wanted) > 0:
        state["empty_wanted_passes"] = 0
        return False
    state["empty_wanted_passes"] = state["empty_wanted_passes"] + 1
    if state["empty_wanted_passes"] >= IDLE_PASSES:
        return True
    return None


def idle_verdict(machine_id, type_id, bio_verdict):
    """True = idle, False = has work, None = cannot tell (hold)."""
    if type_id in BIO_TYPES:
        return bio_verdict
    machine = get_component(machine_id)
    if machine is None:
        return None
    try:
        if type_id == "smelter":
            return smelter_idle(machine)
        if type_id == "supply_dock":
            return dock_idle(machine)
    except Exception as error:
        _warn_once(machine_id, f"Power: cannot read {machine_id} while judging"
                               f" idle, keeping it as is: {error}")
    return None


def idle_pass(control, record, mode):
    bio_verdict = bio_idle()
    for (machine_id, type_id) in machines_of(IDLE_TYPES):
        verdict = idle_verdict(machine_id, type_id, bio_verdict)
        if verdict is None:
            continue
        if verdict:
            if machine_id in record or not control.is_powered(machine_id):
                continue
            if switch_off(control, machine_id, IDLE):
                record[machine_id] = IDLE
            continue
        if record.get(machine_id) != IDLE:
            continue
        if wants_shed(mode, type_id):
            record[machine_id] = mode
            continue
        if switch_on(control, machine_id, IDLE) != "retry":
            record.pop(machine_id)


# --- main loop ----------------------------------------------------------

def run(gen, interval=INTERVAL):
    """Track the sun on `gen` and supervise base power forever."""
    clock = get_component("clock")
    control = get_component("power_control")
    if clock is None or control is None:
        notify("Power supervisor: no clock or power_control component;"
               " tracking sun only", "warn")
        solar.run(gen, interval)
        return

    ledger = load_ledger()
    record = load_shed()

    grid = resolve_grid(control, gen)
    scope_to(grid)
    anchor = grid_anchor(grid)
    grids = {"others": None}
    watch_grids(control, anchor, grids)

    # The measured night load describes one grid. Say so when it starts
    # describing a different one, rather than quietly averaging the two
    # across a rewire.
    if anchor is not None:
        if ledger["grid"] is not None and ledger["grid"] != anchor:
            notify(f"Power: the ledger was measured on grid {ledger['grid']}"
                   f" but this panel is now on {anchor} - its night load will"
                   f" re-average onto the merged grid", "warn")
        ledger["grid"] = anchor
        save_ledger(ledger)

    caps.report("Power supervisor online", caps.common() + [
        ("grid", anchor if anchor is not None else "planet-wide fallback"),
        ("nights measured", ledger["nights"]),
        ("to restore", len(record)),
        ("unmanaged types", unmanaged_types()),
    ])
    caps.watch_capacity()

    restore_all(control, record)
    save_shed(record)

    while True:
        solar.track_sun(gen, clock)
        solar.check_output(gen, clock)

        grid = resolve_grid(control, gen)
        scope_to(grid)
        watch_grids(control, grid_anchor(grid), grids)
        caps.watch_capacity()

        summary = supply(control, grid)
        account(ledger, summary, clock)

        phase = state["phase"]
        hours = hours_to_dawn(ledger, phase, clock.elapsed_game_hours())
        need = night_load(ledger) * hours * SAFETY

        previous = state["mode"]
        mode = next_mode(previous, summary.stored, need, summary.net, phase)
        if mode != previous:
            state["mode"] = mode
            notify(f"Power mode -> {mode}: stored {round(summary.stored)} Wh,"
                   f" need {round(need)} Wh, {round(hours, 1)} h to dawn", "warn")

        before = dict(record)
        if mode == NORMAL:
            if summary.net > 0:
                restore_one(control, record)
        else:
            shed(control, record, mode, summary.net)
        idle_pass(control, record, mode)
        if record != before:
            save_shed(record)

        publish(mode, summary, need, hours)
        sleep(interval)
