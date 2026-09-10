"""What the Control Room cards read, as plain dicts.

No drawing here - `lib/dash.py` is the other half. Everything a card
shows comes back from one of these functions already merged, already
aged and already grouped by outpost, so a card is a layout and nothing
else. `alerts()` has no canvas in mind at all: a future notifier can use
it unchanged.

Two rules run through the whole file.

**Discovery is cached; values are not.** A card is a `while True` loop
running at ten ticks a second against a per-tick step budget, and walking
every outpost's building list that often spends the budget on an answer
that changes when a Pioneer finishes an outpost. Live readings - output(),
efficiency(), progress, battery - are read on every call.

**The bus is never trusted blind.** Switching a machine off pauses its
script, so a shed machine's channel keeps its last value while nothing is
publishing. Every bus read here is an aged read, and the caller is handed
`stale` rather than a value that merely looks current.

Nothing here names a machine or an outpost: machines are found through
`caps.buildings(type_id)`, which walks the whole outpost network. The day
smelting moves off Nocturna Base it becomes a second outpost heading and
no card needs an edit.
"""

import bus
import caps

ATMOS_TYPES = ["oxygen_generator", "pressure_generator", "temp_heater"]
PROD_TYPES = ["smelter", "fabricator", "refiner", "feed_maker", "fuel_assembler"]
DOCK_TYPES = ["supply_dock"]

CLOCK_ID = "clock"
ATMOSPHERE_ID = "atmosphere"
CONTROL_ID = "power_control"
FLEET_ID = "fleet"

# 10 ticks/sec, so ~5 seconds between building walks.
REFRESH_TICKS = 50

# The same figure lib/fleet.py reads intents at, and lib/terraform.py the
# power mode: a few times the publisher's own interval.
INTENT_MAX_AGE = 30
POWER_MAX_AGE = 30

# One duty sample per refresh, so ~5 minutes of production history.
DUTY_SAMPLES = 60

# Painted in this order, because the card lays them out as three columns.
PILLARS = [
    {"key": "o2", "type": "oxygen_generator", "unit": "ppt/h", "label": "OXYGEN"},
    {"key": "pressure", "type": "pressure_generator", "unit": "kPa/h",
     "label": "PRESSURE"},
    {"key": "heat", "type": "temp_heater", "unit": "hu/h", "label": "HEAT"},
]

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

_state = {"tick": None, "machines": {}, "duty": {}, "alerts": None}


# --- discovery ----------------------------------------------------------

def refresh(force=False):
    """Re-walk the building graph when the cadence says so.

    True when it actually ran. Every public function calls this first, so
    a card never has to remember to; the tick comparison is what makes
    that cheap enough to do ten times a second.
    """
    now = _tick()
    if not force and now is not None and _state["tick"] is not None:
        if (now - _state["tick"]) < REFRESH_TICKS:
            return False
    _state["tick"] = now
    found = {}
    for type_id in ATMOS_TYPES + PROD_TYPES + DOCK_TYPES:
        found[type_id] = _discover(type_id)
    _state["machines"] = found
    _sample_duty()
    _state["alerts"] = None
    return True


def _tick():
    clock = caps.component(CLOCK_ID)
    if clock is None:
        return None
    return clock.tick()


def _discover(type_id):
    made = []
    for ref in caps.buildings(type_id):
        outpost = "?"
        if ref.outpost is not None:
            outpost = ref.outpost.name
        made.append({"id": ref.id, "name": ref.name, "type": ref.type_id,
                     "outpost": outpost, "outpost_id": ref.outpost_id,
                     "powered": ref.powered})
    return made


def _known(type_ids):
    made = []
    for type_id in type_ids:
        for record in _state["machines"].get(type_id, []):
            made.append(record)
    return made


def _call(machine, name, fallback):
    """Call an optional zero-argument reader, or return `fallback`.

    Not every machine of a discovered type has every reader - a refiner
    is not a smelter, a Mk I has no fluid port - and a missing one is a
    normal answer rather than a fault. House rule 2, applied to methods.
    """
    method = getattr(machine, name, None)
    if method is None:
        return fallback
    try:
        return method()
    except Exception:
        return fallback


def _fraction(value, total):
    if value is None or total is None or total <= 0:
        return 0.0
    return value / total


# --- clock --------------------------------------------------------------

def clock_line():
    """Day, wall clock, daylight phase and sun elevation."""
    clock = caps.component(CLOCK_ID)
    if clock is None:
        return {"day": None, "hh": 0, "mm": 0, "phase": "unknown",
                "elevation": 0.0, "game_hours": None}
    moment = clock.get_time()
    return {"day": clock.get_day(), "hh": moment[0], "mm": moment[1],
            "phase": clock.get_time_of_day(), "elevation": clock.get_elevation(),
            "game_hours": clock.elapsed_game_hours()}


# --- power --------------------------------------------------------------

def power():
    """Every grid, plus what the supervisor is saying about the budget.

    `supervised` is the honest flag: without it a silent supervisor and a
    base with nothing shed look identical, because both come back with an
    empty shed record.
    """
    refresh()
    control = caps.component(CONTROL_ID)
    grids = []
    if control is not None:
        for grid in control.grids():
            grids.append({
                "anchor": grid.anchor_id,
                "outposts": grid.outpost_ids,
                "generated": grid.generated,
                "consumed": grid.consumed,
                "net": grid.net,
                "stored": grid.stored,
                "capacity": grid.capacity,
                "level": _fraction(grid.stored, grid.capacity),
                "has_generator": grid.has_generator,
            })
    budget = bus.read_fresh(bus.POWER_BUDGET, POWER_MAX_AGE)
    supervised = budget is not None
    if budget is None:
        budget = {}
    shed = bus.read_fresh(bus.POWER_SHED, POWER_MAX_AGE, {})
    return {
        "grids": grids,
        "mode": bus.read_fresh(bus.POWER_MODE, POWER_MAX_AGE),
        "supervised": supervised,
        "shed": shed,
        "stored": budget.get("stored"),
        "capacity": budget.get("capacity"),
        "level": _fraction(budget.get("stored"), budget.get("capacity")),
        "net": budget.get("net"),
        "need_wh": budget.get("need_wh"),
        "hours_to_dawn": budget.get("hours_to_dawn"),
    }


# --- atmosphere ---------------------------------------------------------

def atmos():
    """The three terraforming pillars: level, rate, and the machines behind it.

    A list rather than a dict keyed by pillar, because the order is part
    of the layout the card paints and a dict would leave it to chance.
    """
    refresh()
    atmosphere = caps.component(ATMOSPHERE_ID)
    control = caps.component(CONTROL_ID)
    made = []
    for pillar in PILLARS:
        machines = []
        total_rate = 0.0
        total_efficiency = 0.0
        counted = 0
        for record in _state["machines"].get(pillar["type"], []):
            machine = caps.component(record["id"])
            if machine is None:
                continue
            powered = True
            if control is not None:
                powered = control.is_powered(record["id"])
            output = _call(machine, "output", 0.0)
            efficiency = _call(machine, "efficiency", 0.0)
            total_rate = total_rate + output
            total_efficiency = total_efficiency + efficiency
            counted = counted + 1
            machines.append({
                "id": record["id"],
                "name": record["name"],
                "outpost": record["outpost"],
                "output": output,
                "efficiency": efficiency,
                "tier": caps.tier(machine),
                "degraded": caps.degraded(machine),
                "powered": powered,
            })
        mean = 0.0
        if counted > 0:
            mean = total_efficiency / counted
        made.append({
            "key": pillar["key"],
            "label": pillar["label"],
            "unit": pillar["unit"],
            "level": _level(atmosphere, pillar["key"]),
            "extra": _extra(atmosphere, pillar["key"]),
            "rate": total_rate,
            "efficiency": mean,
            "machines": machines,
        })
    return made


def _level(atmosphere, key):
    """The pillar's current reading, or None when its sensor is not repaired.

    Oxygen and pressure both need their sensor; heat never does. None is
    a real answer here and the card prints it as one.
    """
    if atmosphere is None:
        return None
    try:
        if key == "o2":
            return atmosphere.get_o2()
        if key == "pressure":
            return atmosphere.get_pressure()
        return atmosphere.get_heat()
    except Exception:
        return None


def _extra(atmosphere, key):
    """The second reading a pillar has, when it has one.

    Heat is the only one: get_heat() is the progression metric research
    compares against, and degrees C is the number a human recognises.
    """
    if atmosphere is None or key != "heat":
        return None
    try:
        return atmosphere.get_temperature()
    except Exception:
        return None


# --- production ---------------------------------------------------------

def production():
    """Every production machine found anywhere, ordered by outpost then name."""
    refresh()
    control = caps.component(CONTROL_ID)
    made = []
    for record in _known(PROD_TYPES):
        machine = caps.component(record["id"])
        if machine is None:
            continue
        recipe_id = _call(machine, "get_recipe", "")
        running = _call(machine, "is_running", False)
        powered = True
        if control is not None:
            powered = control.is_powered(record["id"])
        made.append({
            "id": record["id"],
            "name": record["name"],
            "type": record["type"],
            "outpost": record["outpost"],
            "recipe": recipe_id,
            "recipe_name": _recipe_name(machine, recipe_id),
            "rate": _nameplate(machine, recipe_id),
            "duty": _duty_of(record["id"]),
            "progress": _call(machine, "get_progress", 0.0),
            "running": running,
            "powered": powered,
            "state": _prod_state(powered, running, recipe_id),
            "input": _input_line(machine, record["type"]),
            "output": _call(machine, "get_output_count", 0),
        })
    return sorted(made, key=_prod_order)


def _prod_order(row):
    return row["outpost"] + "/" + row["name"]


def _prod_state(powered, running, recipe_id):
    """The word the card prints and colours the dot from."""
    if not powered:
        return "off"
    if running:
        return "producing"
    if recipe_id is None or recipe_id == "":
        return "no recipe"
    return "idle"


def _nameplate(machine, recipe_id):
    """Units per hour at this recipe, taken from the recipe itself.

    The machine's ceiling, not what it is achieving: a smelter starved of
    ore reports its full nameplate while producing nothing. `duty` is the
    other half of that answer and the two are always shown together.
    """
    recipe = _recipe(machine, recipe_id)
    if recipe is None:
        return None
    hours = recipe.duration_game_hours
    if hours is None or hours <= 0:
        return None
    return recipe.output_count / hours


def _recipe_name(machine, recipe_id):
    if recipe_id is None or recipe_id == "":
        return "-"
    recipe = _recipe(machine, recipe_id)
    if recipe is None:
        return recipe_id
    return recipe.name


def _recipe(machine, recipe_id):
    if recipe_id is None or recipe_id == "":
        return None
    finder = getattr(machine, "find_recipe", None)
    if finder is None:
        return None
    try:
        return finder(recipe_id)
    except Exception:
        return None


def _input_line(machine, type_id):
    """How much material is staged, in whichever way this machine counts it.

    A smelter has one input buffer and reports a count; a fabricator has
    a shared stockpile and reports used against a capacity.
    """
    used = _call(machine, "get_stockpile_used", None)
    if used is not None:
        return {"used": used, "capacity": _call(machine, "get_stockpile_capacity", None)}
    return {"used": _call(machine, "get_input_count", 0), "capacity": None}


def _sample_duty():
    """One running/not-running sample per production machine, per refresh.

    Counting real completions would need a longer memory than a card that
    restarts on every save/load can keep. A duty cycle over the last few
    minutes costs one boolean per machine and separates "running slowly"
    from "not running most of the time", which are different problems.
    """
    for record in _known(PROD_TYPES):
        machine = caps.component(record["id"])
        if machine is None:
            continue
        running = 0
        if _call(machine, "is_running", False):
            running = 1
        series = _state["duty"].get(record["id"])
        if series is None:
            series = []
            _state["duty"][record["id"]] = series
        series.append(running)
        while len(series) > DUTY_SAMPLES:
            series.pop(0)


def _duty_of(machine_id):
    series = _state["duty"].get(machine_id)
    if series is None or len(series) == 0:
        return None
    total = 0
    for value in series:
        total = total + value
    return total / len(series)


# --- vehicles -----------------------------------------------------------

def vehicles():
    """Every ground vehicle: the engine's facts, plus our own intent.

    `VehicleRef.status` is the engine truth and is always trusted. The bus
    adds what only the vehicle's own script knows - why it is idle, where
    it is headed, what it is carrying - and only while that script is
    still publishing. A vehicle whose channel has aged out is marked
    `stale` and keeps the engine's word; it is never given a stale verb.
    """
    index = caps.component(FLEET_ID)
    if index is None:
        return []
    made = []
    for ref in index.vehicles():
        intent = bus.read_fresh(bus.vehicle_channel(ref.id), INTENT_MAX_AGE)
        stale = intent is None
        if intent is None:
            intent = {}
        made.append({
            "id": ref.id,
            "name": ref.name,
            "kind": ref.kind,
            "status": ref.status,
            "state": intent.get("state", ref.status),
            "target": intent.get("target"),
            "cargo": intent.get("cargo"),
            "job": intent.get("job"),
            "battery": ref.battery_level,
            "battery_wh": ref.battery_wh,
            "x": ref.x,
            "y": ref.y,
            "docked": ref.is_docked,
            "docked_at": ref.docked_at,
            "rescued": ref.is_being_rescued,
            "rescue": ref.rescue_status,
            "stale": stale,
        })
    return sorted(made, key=_vehicle_order)


def _vehicle_order(row):
    return row["kind"] + "/" + row["name"]


# --- Earth --------------------------------------------------------------

def orders():
    """Each Supply Dock, its Earth Order, and what that order is still owed."""
    refresh()
    made = []
    for record in _known(DOCK_TYPES):
        dock = caps.component(record["id"])
        if dock is None:
            continue
        order = _call(dock, "current_order", None)
        items = []
        name = "no order"
        contractor = ""
        if order is not None:
            name = order.name
            if order.contractor_name is not None:
                contractor = order.contractor_name
            for item_id in order.requires:
                items.append({
                    "item": item_id,
                    "required": order.requires[item_id],
                    "shipped": order.shipped.get(item_id, 0),
                })
        made.append({
            "id": record["id"],
            "name": record["name"],
            "outpost": record["outpost"],
            "order": name,
            "contractor": contractor,
            "items": items,
            "rate": _call(dock, "dispatch_rate", None),
            "enabled": _call(dock, "is_enabled", False),
            "sending": _call(dock, "current_dispatch", None),
            "progress": _call(dock, "dispatch_progress", 0.0),
            "loaded": _call(dock, "total", 0),
            "owed": _call(dock, "capacity", 0),
        })
    return made


# --- alerts -------------------------------------------------------------

def alerts():
    """Everything that wants the operator, worst first.

    Rebuilt on the refresh cadence rather than every tick: it sweeps the
    whole base, and an alert that is five seconds old is still an alert.
    Severity is a dash colour token, so a card paints a pill straight
    from it.
    """
    refresh()
    if _state["alerts"] is None:
        _state["alerts"] = _build_alerts()
    return _state["alerts"]


def _build_alerts():
    found = []
    supply = power()
    if not supply["supervised"]:
        found.append({"severity": "error", "text": "power supervisor silent"})
    else:
        if supply["mode"] == bus.CONSERVE:
            found.append({"severity": "warning", "text": "conserving power"})
        for machine_id in supply["shed"]:
            found.append({"severity": "warning", "text": "shed " + machine_id})

    for pillar in atmos():
        for machine in pillar["machines"]:
            if machine["degraded"]:
                found.append({"severity": "warning",
                              "text": machine["name"] + " degraded"})
            elif not machine["powered"]:
                found.append({"severity": "warning",
                              "text": machine["name"] + " unpowered"})

    for row in production():
        if row["state"] == "no recipe":
            found.append({"severity": "info",
                          "text": row["name"] + " has no recipe"})
        elif row["duty"] is not None and row["duty"] < 0.1 and row["powered"]:
            found.append({"severity": "warning",
                          "text": row["name"] + " starved"})

    for vehicle in vehicles():
        if vehicle["status"] == "stranded":
            found.append({"severity": "error",
                          "text": vehicle["name"] + " stranded"})
        elif vehicle["rescued"]:
            found.append({"severity": "warning",
                          "text": vehicle["name"] + " " + vehicle["rescue"]})
        elif vehicle["stale"]:
            found.append({"severity": "info",
                          "text": vehicle["name"] + " not reporting"})

    return sorted(found, key=_severity_rank)


def _severity_rank(item):
    return SEVERITY_ORDER.get(item["severity"], 3)
