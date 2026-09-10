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


def matches(order, prefer) -> bool:
    """True when `prefer` names this order, its contractor, or its reward.

    One string covers every way a human refers to an order: the order id,
    the contractor id or display name, the order name, or the reward
    label. Matched case-insensitively as a substring, so
    `"vestibule"` and `"vestibule_logistics"` both work.
    """
    if prefer is None or prefer == "":
        return False
    wanted = prefer.lower()
    for field in [order.id, order.name, order.contractor_id,
                  order.contractor_name, order.reward_label]:
        if field is not None and wanted in field.lower():
            return True
    return False


def preferred(prefer, prefer_weekly=False):
    """The open order the operator asked for, or None.

    The three contractors each expose their current order at the same
    time, so a named one is available NOW - there is nothing to wait out.
    Feedability is deliberately not checked: assigning an order we cannot
    fill yet is exactly how the rest of the base is told to go fill it,
    through the `earth.demand` broadcast (D-025, D-028).
    """
    for order in earth.available_orders(prefer_weekly):
        if matches(order, prefer):
            return order
    return None


def pick_order(crates, prefer_weekly=False, prefer=None):
    """The best open order we can actually make progress on, or None.

    An operator preference wins outright: `score_order` ranks on progress
    already shipped, which is right when nothing else is asked for and
    wrong the moment a specific reward is being chased.

    Otherwise prefers an order with something already in the crates,
    because an order we cannot feed is a dock sitting idle with an
    assignment.
    """
    wanted = preferred(prefer, prefer_weekly)
    if wanted is not None:
        return wanted

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


def drain(dock_machine, crates) -> bool:
    """Eject every loaded slot back into local storage. True when empty.

    Dock cargo is physical and `set_order` refuses to reassign around it.
    `eject()` recovers it; `flush()` would destroy it, which is never the
    right answer for material the base spent ore and power making.
    """
    for slot in dock_machine.slots():
        if slot.item_id is None or slot.count <= 0:
            continue

        sink = storage.sink_for(crates, slot.item_id, slot.count)
        if sink is None:
            notify(f"Supply Dock holds {slot.count} x {slot.item_id} and"
                   f" nowhere local will take it back - free a bin or"
                   f" Inventory space before switching orders", "warn")
            return False

        out = dock_machine.input.eject(sink, slot.item_id, slot.count)
        if out.status not in ["ok", "partial", "no_op"]:
            notify(f"Supply Dock eject {slot.item_id} -> {sink}:"
                   f" {out.message}", "warn")
            return False

    return dock_machine.total() <= 0


def switch(dock_machine, order, crates) -> bool:
    """Move an already-assigned dock onto `order`. True when it took.

    Safe for campaign orders and only for those: `.shipped` is shared and
    permanent, so units already sent to the order being left behind stay
    credited and are there when it is picked up again. A weekly loses
    everything shipped at the next board refresh, so one is never
    abandoned here.
    """
    active = dock_machine.current_order()
    if active is None:
        return assign(dock_machine, order)
    if active.id == order.id:
        return True
    if active.kind == "weekly":
        return False

    notify(f"Supply Dock switching from {active.name} to {order.name}")
    released = dock_machine.clear_order()
    if released.status != "ok":
        notify(f"clear_order: {released.message}", "warn")
        return False
    if not drain(dock_machine, crates):
        return False
    return assign(dock_machine, order)


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


def run(dock_machine, crates, prefer_weekly=False, prefer=None, interval=10):
    """Serve Earth Orders from the crate bank, forever.

    dock_machine   the Supply Dock this script runs inside - pass `self`
    crates         bin ids to load from
    prefer_weekly  weigh expiring weekly orders ahead of campaign ones
    prefer         name, id or contractor of the order to chase, or None
                   to let score_order decide
    interval       seconds between passes

    The startup line reports `units/h` against a base rate of 25. Anything
    lower and no throughput research is unlocked means this outpost is
    over its soft building threshold and the overcrowding penalty is
    already being paid (D-021).
    """
    if not ports.require_feeders("Supply Dock"):
        return

    caps.report("Supply Dock online", caps.common() + [
        ("crates", crates),
        ("units/h", dock_machine.dispatch_rate()),
        ("prefer", prefer),
    ])

    if prefer is not None and preferred(prefer, prefer_weekly) is None:
        notify(f"Supply Dock: no open order matches \"{prefer}\" - falling"
               f" back to picking by progress and reward", "warn")

    announced = ""

    while True:
        active = dock_machine.current_order()

        # An assignment does not expire, so a preference set after the
        # dock latched onto something else would otherwise never take
        # effect. Campaign progress is shared and permanent, so the order
        # being left behind keeps every unit already shipped to it.
        if active is not None and not matches(active, prefer):
            wanted = preferred(prefer, prefer_weekly)
            if wanted is not None:
                if not switch(dock_machine, wanted, crates):
                    sleep(IDLE_INTERVAL)
                    continue
                active = dock_machine.current_order()

        # current_order() flips to None on completion or weekly expiry, and
        # dispatch is disabled with it - so the script has to choose again
        # rather than assume its assignment survived.
        if active is None:
            if announced != "":
                notify(f"Supply Dock order cleared: {announced}")
                announced = ""

            earth.publish_demand(None)

            target = pick_order(crates, prefer_weekly, prefer)
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