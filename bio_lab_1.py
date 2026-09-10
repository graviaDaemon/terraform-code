import bio
import bus
import caps
import ports
from results import is_transient, transfer_fine, moved

COLLECTOR_ID = "bio_collector_1"
EXCHANGE_ID = "bio_exchange_1"

# Local store for this outpost, discovered: base Inventory at Nocturna
# Base, a same-outpost Warehouse or Storage Bin elsewhere.
REAGENT_SOURCE = caps.local_store(self)
SAMPLE_SINK = REAGENT_SOURCE

# How stale the Exchange broadcast may be before the Lab reads orders itself.
WANTED_MAX_AGE = 30

collector = get_component(COLLECTOR_ID)
exchange = get_component(EXCHANGE_ID)
commander = get_component("commander")
shop = get_component("shop")

caps.report("Bio Lab online", caps.common() + [
    ("collector", collector is not None),
    ("exchange", exchange is not None),
    ("shop", shop is not None),
    ("store", REAGENT_SOURCE),
])


# --- helpers ------------------------------------------------------------

def shared_wanted():
    """Wanted fragment ids, preferring the Exchange broadcast.

    Falls back to reading the order list directly when the Exchange script
    is stopped, so the Lab never depends on another script being alive.
    """
    published = bus.read_fresh(bus.BIO_WANTED, WANTED_MAX_AGE)
    if published is not None:
        return published
    return bio.wanted_fragments(exchange.orders())


def catalogue_cost(item_id):
    """Unit price of `item_id`, or None when the shop does not stock it."""
    for item in shop.get_catalogue():
        if item.id == item_id:
            return item.cost
    return None


def buy_reagent(reagent_id, count):
    """Buy `count` units into base Inventory. All-or-nothing per the docs."""
    cost = catalogue_cost(reagent_id)
    if cost is None:
        notify(f"Reagent not in catalogue: {reagent_id}", "warn")
        return False
    if commander.get_credits() < cost * count:
        notify(f"Not enough credits for {count} x {reagent_id}", "warn")
        return False
    result = shop.buy(reagent_id, count)
    if result.status != "ok":
        notify(f"Buy {reagent_id}: {result.message}", "warn")
        return False
    return True


def stage_reagent(reagent_id, shortfall):
    """Route `shortfall` units into the latched input, then load them."""
    have = self.input.count()

    if have < shortfall:
        taken = self.input.take(reagent_id, shortfall - have)
        if not transfer_fine(taken):
            print(taken.message)
        have = self.input.count()

    if have < shortfall:
        if not buy_reagent(reagent_id, shortfall - have):
            return False
        taken = self.input.take(reagent_id, shortfall - have)
        if not moved(taken):
            print(taken.message)
            return False
        have = self.input.count()

    if have < shortfall:
        return False

    loaded = self.load(reagent_id, shortfall)
    if loaded.status == "ok":
        return True
    if is_transient(loaded):
        sleep(1)
        return False
    notify(f"Load {reagent_id}: {loaded.message}", "warn")
    return False


def stage_recipe(recipe):
    """Stage every reagent the recipe calls for, one latched id at a time."""
    # Anything staged that this recipe does not want causes
    # recipe_mismatch and destroys the lot. Return it to the output first.
    for staged_id in self.loaded_reagents:
        if staged_id not in recipe:
            notify(f"Wrong reagents staged - unloading {staged_id}", "warn")
            cleared = self.unload_reagents()
            if cleared.status == "output_full":
                ports.drain(self.output)
            return False

    for reagent_id in recipe:
        already = self.loaded_reagents.get(reagent_id, 0)
        shortfall = recipe[reagent_id] - already
        if shortfall <= 0:
            continue
        if not stage_reagent(reagent_id, shortfall):
            return False

    return True


# --- main loop ----------------------------------------------------------

if REAGENT_SOURCE is None or not ports.connect_pair(
        self.input, self.output, REAGENT_SOURCE, SAMPLE_SINK, "Bio Lab"):
    notify("Bio Lab halted: ports not connected", "warn")
else:
    while True:

        # Publish first: this is what lets the Collector tell "the Lab is
        # busy" apart from "the Lab is not running".
        bus.publish(bus.LAB_STATUS, "running")

        # Output first: a full output blocks extract(), discard() and
        # unload_reagents(), so clear it before anything else.
        ports.drain(self.output)

        # --- get a specimen ---------------------------------------------
        if self.specimen is None:
            taken = self.take_from(collector)
            if is_transient(taken):
                sleep(1)
                continue
            if taken.status != "ok":
                notify(f"take_from: {taken.message}", "warn")
                sleep(5)
                continue

        # --- analyse it --------------------------------------------------
        if self.specimen.stage == "collected":
            analysis = self.analyze()
            if is_transient(analysis):
                sleep(1)
                continue
            if analysis.status == "invalid_specimen":
                notify("Unanalysable specimen - discarding", "warn")
                self.discard()
                continue
            if analysis.status != "ok":
                notify(f"analyze: {analysis.message}", "warn")
                continue

        # --- is it worth reagents? ---------------------------------------
        wanted = shared_wanted()
        if not bio.is_wanted(wanted, self.specimen.fragment_id):
            notify(f"Discarding unwanted: {self.specimen.fragment_id}")
            dropped = self.discard()
            if dropped.status == "output_full":
                ports.drain(self.output)
            continue

        # --- stage reagents ----------------------------------------------
        if not stage_recipe(self.specimen.recipe):
            sleep(5)          # waiting on credits, stock or an output drain
            continue

        # --- extract ------------------------------------------------------
        result = self.extract()

        if result.status == "ok":
            ports.drain(self.output)
        elif result.status == "output_full":
            notify("Lab output full - draining, extraction preserved", "warn")
            ports.drain(self.output)
            sleep(2)
        elif result.status == "recipe_mismatch":
            # reagents destroyed, specimen preserved - re-stage and retry
            notify("Recipe mismatch - reagents lost, restaging", "warn")
        elif is_transient(result):
            sleep(1)
        else:
            notify(f"extract: {result.message}", "warn")
            sleep(1)