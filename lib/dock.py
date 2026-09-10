"""Supply Dock: assign an Earth Order, load it, ship it, choose the next.

    import dock
    dock.run(self, CRATES)

The dock PULLS. Its `.input` is order-aware and accepts only what the
active Order still needs, so the loop connects it to a crate and takes -
a rover cannot push into a dock, and does not need to. The rover's job is
to get ore into the crate bank; the dock helps itself from there.

Dispatch is continuous at dispatch_rate() units/h once enabled, so
shipping is slow and entirely hands-off. The script's real work is the
four transitions: pick, load, enable, and react when the order clears.

Needs Auto Feeders research for `.input`, and Supply Logistics research
(Terraform Index 110,000) for the dock itself.
"""

import bus
import caps
import earth
import ports
import storage

# Units to pull per take(). The dock refuses overshoot, so a big ask is
# safe, but small batches keep one item from hogging a pass.
BATCH = 25

# Wait this long when there is no order to serve or nothing to load with.
IDLE_INTERVAL = 30

# set_order outcomes worth retrying rather than treating as fatal.
ORDER_TRANSIENT = ["cargo_present"]


def score_order(order):
    """Rank open orders. Higher is better.

    Prefers orders already part-shipped, because abandoning progress wastes
    it - and for weekly orders it is worse than waste, since everything
    shipped toward an unfinished weekly is lost when the board refreshes.
    A recipe or tech reward outranks a pure credit payout.
    """
    landed = 0
    for item_id in order.requires:
        landed = landed + order.shipped.get(item_id, 0)

    value = landed * 10
    if order.reward_kind is not None:
        value = value + 500          # recipes and tech beat credits
    return value


def pick_order(crates, prefer_weekly=False):
    """The best open order we can actually make progress on, or None.

    Prefers an order with something already in the crates, because an
    order we cannot feed is a dock sitting idle with an assignment.
    """
    best = None
    best_score = 0
    fallback = None

    for order in earth.available_orders(prefer_weekly):
        if fallback is None:
            fallback = order

        feedable = False
        for item_id in earth.needs(order):
            if storage.total_of(crates, item_id) > 0:
                feedable = True

        if not feedable:
            continue

        value = score_order(order)
        if best is None or value > best_score:
            best = order
            best_score = value

    if best is not None:
        return best
    return fallback


def assign(dock_machine, order) -> bool:
    """Latch `order` onto this dock. True when it is the active order."""
    active = dock_machine.current_order()
    if active is not None and active.id == order.id:
        return True

    result = dock_machine.set_order(order.id)

    if result.status == "ok":
        notify(f"Supply Dock assigned: {order.name}")
        return True
    if result.status == "completed":
        return False              # already done elsewhere; pick again
    if result.status == "cargo_present":
        # Loaded cargo is physical and blocks a reassignment. It has to be
        # drained by a local machine or vehicle, or ejected, first.
        notify(f"Supply Dock holds cargo from a previous order -"
               f" eject or drain it before switching", "warn")
        return False
    notify(f"set_order {order.id}: {result.message}", "warn")
    return False


def load(dock_machine, order, crates) -> int:
    """Pull what the order still needs out of the crate bank.

    The dock's input accepts only the active order's remaining need and
    refuses overshoot, so this asks for what is short and lets the port
    clamp it.
    """
    moved = 0

    for item_id in earth.needs(order):
        short = earth.outstanding(order, item_id) - dock_machine.count(item_id)
        if short <= 0:
            continue                # already loaded enough of this one

        wanted = BATCH
        if short < wanted:
            wanted = short

        source = storage.source_for(crates, item_id, 1)
        if source is None:
            continue                # none in the bank yet

        if dock_machine.input.connected_to() != source:
            linked = dock_machine.input.connect(source)
            if linked.status != "ok":
                notify(f"dock input -> {source}: {linked.message}", "warn")
                continue

        taken = dock_machine.input.take(item_id, wanted)
        if taken.status in ["ok", "partial"]:
            moved = moved + taken.moved
        elif taken.status != "no_op":
            print(f"dock input.take {item_id}: {taken.message}")

    return moved


def enable(dock_machine):
    """Start the dispatcher if it is not already running."""
    if dock_machine.is_enabled():
        return
    result = dock_machine.set_enabled(True)
    if result.status != "ok":
        notify(f"set_enabled: {result.message}", "warn")


def run(dock_machine, crates, prefer_weekly=False, interval=10):
    """Serve Earth Orders from the crate bank, forever.

    dock_machine   the Supply Dock this script runs inside - pass `self`
    crates         bin ids to load from
    prefer_weekly  weigh expiring weekly orders ahead of campaign ones
    interval       seconds between passes
    """
    if not ports.require_feeders("Supply Dock"):
        return

    caps.report("Supply Dock online", caps.common() + [
        ("crates", crates),
        ("units/h", dock_machine.dispatch_rate()),
    ])

    announced = ""

    while True:
        active = dock_machine.current_order()

        # current_order() flips to None on completion or weekly expiry, and
        # dispatch is disabled with it - so the script has to choose again
        # rather than assume its assignment survived.
        if active is None:
            if announced != "":
                notify(f"Supply Dock order cleared: {announced}")
                announced = ""

            earth.publish_demand(None)

            target = pick_order(crates, prefer_weekly)
            if target is None:
                sleep(IDLE_INTERVAL)
                continue
            if not assign(dock_machine, target):
                sleep(IDLE_INTERVAL)
                continue
            active = dock_machine.current_order()
            if active is None:
                sleep(IDLE_INTERVAL)
                continue

        announced = active.name

        # Tell the rest of the base what Earth is waiting for. Published
        # every pass so readers can tell a live dock from a stopped one.
        earth.publish_demand(active)

        load(dock_machine, active, crates)
        enable(dock_machine)

        sleep(interval)