"""Smelter pipeline: latch a recipe, feed ore in, drain metal out.

    import smelting
    smelting.run(self, "smelt_iron_ingot", ORE_BINS, INGOT_BINS)

Both buffers hold 50 units. Processing is asynchronous, so a completed
unit appears in the output buffer some time after the ore goes in - the
loop drains before it feeds, so output never blocks production.

Needs Auto Feeders research: self.input and self.output do not exist
without it. That is checked once at startup and reported plainly rather
than failing on the first transfer.
"""

import bus
import caps
import ports
import storage

# Units to pull per top-up. Small enough that a bin switch mid-run does
# not strand a large batch in the wrong buffer.
BATCH = 10

# Leave the input buffer this far from full so a take() is never wasted.
INPUT_HEADROOM = 5

# set_recipe outcomes worth retrying rather than giving up on.
RECIPE_TRANSIENT = ["busy", "offline"]


def resolve_recipe(smelter, recipe_id):
    """The Recipe object for `recipe_id`, or None with an explanation.

    find_recipe() returns None for unknown, locked, or wrong-machine ids
    alike, so the message covers all three.
    """
    recipe = smelter.find_recipe(recipe_id)
    if recipe is None:
        available = []
        for known in smelter.list_recipes():
            available.append(known.id)
        notify(f"Recipe '{recipe_id}' is not available on this smelter."
               f" Unlocked: {available}", "warn")
    return recipe


def latch_recipe(smelter, recipe) -> bool:
    """Select `recipe`, unless it is already the active one.

    Checks is_running() first: set_recipe() rejects with "busy" while a
    unit is in progress, and re-latching the recipe already running is
    pointless anyway.
    """
    if smelter.get_recipe() == recipe.id:
        return True
    if smelter.is_running():
        return False              # mid-unit; try again next pass

    result = smelter.set_recipe(recipe.id)
    if result.status == "ok":
        notify(f"Smelter set to {recipe.name}")
        return True
    if result.status in RECIPE_TRANSIENT:
        return False
    if result.status == "material_mismatch":
        notify(f"Smelter holds material for a different recipe -"
               f" drain it before switching to {recipe.id}", "warn")
        return False
    notify(f"set_recipe {recipe.id}: {result.message}", "warn")
    return False


def drain(smelter, recipe, sink_bins) -> int:
    """Push finished units to a bin that will take them. Returns units moved.

    Routed rather than fixed: with several bins the right destination is
    whichever one is already latched to this metal, falling back to an
    empty bin.
    """
    ready = smelter.get_output_count()
    if ready <= 0:
        return 0

    target = storage.sink_for(sink_bins, recipe.output_item, 1)
    if target is None:
        notify(f"No bin can accept {recipe.output_item} - smelter output"
               f" is backing up", "warn")
        return 0

    if smelter.output.connected_to() != target:
        linked = smelter.output.connect(target)
        if linked.status != "ok":
            notify(f"smelter output -> {target}: {linked.message}", "warn")
            return 0

    sent = smelter.output.send(recipe.output_item, ready)
    if sent.status not in ["ok", "partial", "no_op"]:
        print(f"output.send {recipe.output_item}: {sent.message}")
        return 0
    return sent.moved


def feed(smelter, recipe, source_bins) -> int:
    """Top up the input buffer from the ore bank. Returns units moved."""
    room = smelter.input.capacity() - smelter.input.count()
    if room <= INPUT_HEADROOM:
        return 0

    wanted = BATCH
    if room < wanted:
        wanted = room

    moved = 0
    for item_id in smelter.get_recipe_inputs():
        source = storage.source_for(source_bins, item_id, 1)
        if source is None:
            continue                  # nothing of this ore anywhere yet

        if smelter.input.connected_to() != source:
            linked = smelter.input.connect(source)
            if linked.status != "ok":
                notify(f"smelter input -> {source}: {linked.message}", "warn")
                continue

        taken = smelter.input.take(item_id, wanted)
        if taken.status in ["ok", "partial"]:
            moved = moved + taken.moved
        elif taken.status != "no_op":
            print(f"input.take {item_id}: {taken.message}")

    return moved


def run(smelter, recipe_id, source_bins, sink_bins, interval=2):
    """Run one smelter forever.

    smelter      the Smelter this script runs inside - pass `self`
    recipe_id    e.g. "smelt_iron_ingot"
    source_bins  bin ids that may hold the input ore
    sink_bins    bin ids that may receive the finished metal
    interval     seconds between passes
    """
    if not ports.require_feeders("Smelter"):
        return

    recipe = resolve_recipe(smelter, recipe_id)
    if recipe is None:
        return

    caps.report("Smelter online", caps.common() + [
        ("recipe", recipe.name),
        ("per craft", recipe.output_count),
        ("draw W", recipe.power_draw),
        ("sources", source_bins),
    ])

    while True:
        if not latch_recipe(smelter, recipe):
            sleep(interval)
            continue

        # Drain first. A full output buffer stalls production, and the
        # ore already fed in is wasted time until it clears.
        drain(smelter, recipe, sink_bins)

        # A smelter's draw is variable and only spent while processing, so
        # the throttle is simply "stop feeding it". The current unit
        # finishes; no new ore goes in until the base recovers.
        conserving = bus.read_fresh(bus.POWER_MODE, 30,
                                    bus.NORMAL) == bus.CONSERVE
        if not conserving:
            feed(smelter, recipe, source_bins)

        sleep(interval)