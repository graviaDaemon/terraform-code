"""The recipe graph: what the base must MINE to fill what Earth ordered.

    import recipes
    recipes.raw_demand()          # {"iron_ore": 300}

An Earth Order only ever asks for finished goods, and a mineral site's
item_id is only ever one of seven raw ores, so "Earth wants 150
iron_ingot" and "drive to an iron_ore deposit" never once matched. This
file is the link between them: it walks Recipe.output_item back to
Recipe.inputs across every deployed machine that has list_recipes(),
until nothing wanted is something the base could make for itself (D-025).

Nothing here is hardcoded to smelters or to iron. A Fabricator, a Feed
Maker and a Refiner all answer list_recipes(), and a blueprint unlocked
tomorrow joins the graph on the next re-probe.

The plural name is deliberate: `recipe` and `recipe_id` are parameter
names throughout lib/smelting.py, and a library whose name a parameter
shadows cannot be reached from inside the functions that use it (D-023).
"""

import bus
import caps
import earth

# Passes before the machine list and their recipes are probed again
# (D-005). Buildings and blueprints change on a human timescale, so this
# is cheap to keep slow.
REPROBE_PASSES = 30

# How many times raw_demand() expands wanted items into their inputs.
# One round covers ore -> ingot, two covers ore -> ingot -> part. The
# cap is only insurance against a future pair of recipes that produce
# each other; today no chain comes close to it.
EXPAND_ROUNDS = 4

state = {"table": None, "passes": 0}


def machines():
    """(machine_id, component) for every deployed building that has recipes.

    Discovered by asking, not by listing type ids: anything that answers
    list_recipes() belongs in the graph, and anything that raises is
    simply not a crafting machine.
    """
    found = []
    for type_id in caps.building_types():
        for ref in caps.buildings(type_id):
            machine = caps.component(ref.id)
            if machine is None:
                continue
            try:
                machine.list_recipes()
            except Exception:
                continue
            found.append((ref.id, machine))
    return found


def table():
    """{output_item: [(machine_id, recipe)]} across every crafting machine.

    Cached, and re-probed on a slow counter, so a newly unlocked
    blueprint or a second smelter reaches the rovers without a restart.
    """
    state["passes"] = state["passes"] - 1
    if state["table"] is not None and state["passes"] > 0:
        return state["table"]

    built = {}
    for (machine_id, machine) in machines():
        try:
            known = machine.list_recipes()
        except Exception:
            continue
        for recipe in known:
            makers = built.get(recipe.output_item, None)
            if makers is None:
                makers = []
                built[recipe.output_item] = makers
            makers.append((machine_id, recipe))

    state["table"] = built
    state["passes"] = REPROBE_PASSES
    return built


def producers(item_id):
    """The unlocked (machine_id, recipe) pairs that make `item_id`. [] when
    nothing does, which is how a raw material identifies itself."""
    return table().get(item_id, [])


def live_demand():
    """{item_id: units} of FINISHED goods Earth is still waiting for.

    The dock's broadcast while the dock is running; the order board read
    directly when it is not. set_powered(id, False) pauses the machine's
    script, so the moment the supervisor conserve-sheds the Supply Dock
    the demand channel goes stale within a minute - and silence there
    means "nobody is publishing", never "nothing is wanted". Same shape,
    and the same reason, as D-007's dark Exchange.

    The board covers every open order rather than only the dock's
    assignment, which is the right answer when the dock is not running
    to have one.
    """
    published = bus.read_fresh(bus.EARTH_DEMAND, earth.DEMAND_MAX_AGE, None)
    if published is not None:
        return published

    wanted = {}
    for order in earth.available_orders():
        owed = earth.needs(order)
        for item_id in owed:
            _add(wanted, item_id, owed[item_id])
    return wanted


def _add(totals, item_id, units):
    totals[item_id] = totals.get(item_id, 0) + units


def raw_demand():
    """{item_id: units} of MINEABLE material the base needs.

    Every wanted item some recipe can produce is replaced by that
    recipe's inputs, scaled by how many runs the shortfall takes; what no
    recipe produces is already raw and passes through untouched. Rounded
    up with integer arithmetic, because a part-run of a recipe still
    needs the whole input.

    This is the shortfall the ORDERS name, not what is still missing once
    the crates are counted - netting off stock would flap as bins fill
    and drain, and one live order does not need it yet.
    """
    wanted = live_demand()
    graph = table()          # once, not once per item per round

    for attempt in range(EXPAND_ROUNDS):
        expanded = {}
        changed = False
        for item_id in wanted:
            units = wanted[item_id]
            makers = graph.get(item_id, [])
            if len(makers) == 0:
                _add(expanded, item_id, units)
                continue

            # Several machines can make the same item; their inputs are
            # the same materials, so the first is as good as any.
            recipe = makers[0][1]
            per_run = recipe.output_count
            if per_run < 1:
                per_run = 1
            changed = True
            for input_id in recipe.inputs:
                per_craft = recipe.inputs[input_id]
                _add(expanded, input_id,
                     (units * per_craft + per_run - 1) // per_run)

        wanted = expanded
        if not changed:
            break

    return wanted


def best_recipe(machine, wanted):
    """The recipe on `machine` whose output `wanted` wants most, or None.

    `wanted` is finished goods, as live_demand() reports them - this
    picks what to MAKE, where raw_demand() picks what to mine.
    """
    try:
        known = machine.list_recipes()
    except Exception:
        return None

    best = None
    best_units = 0
    for recipe in known:
        units = wanted.get(recipe.output_item, 0)
        if units > best_units:
            best = recipe
            best_units = units
    return best
