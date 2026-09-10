import bio
import bus
import caps
import ports
from results import is_transient, moved

# Local store for this outpost, discovered: base Inventory at Nocturna
# Base, a same-outpost Warehouse or Storage Bin elsewhere.
SAMPLE_SOURCE = caps.local_store(self)
SURPLUS_SINK = SAMPLE_SOURCE

store = None
if SAMPLE_SOURCE is not None:
    store = get_component(SAMPLE_SOURCE)

caps.report("Bio Exchange online", caps.common() + [("store", SAMPLE_SOURCE)])


def publish_wanted(orders):
    """Broadcast the ids some open order still needs.

    The Exchange is the only machine that owns the order list, so it is the
    natural publisher. The Collector and Lab read this instead of each
    calling exchange.orders() and recomputing the same set every pass.

    A list of ids is JSON-safe and works with bio.is_wanted() unchanged,
    since that only does `in` and len().
    """
    wanted = bio.wanted_fragments(orders)
    if wanted is None:
        return
    bus.publish(bus.BIO_WANTED, list(wanted))


def stock_of(fragment_id):
    """Units of `fragment_id` sitting in the local store."""
    return store.count(fragment_id)


def pick_order(orders):
    """Prefer an order we can feed right now; otherwise the first open one."""
    fallback = None
    for order in orders:
        if not bio.is_open(order):
            continue
        if fallback is None:
            fallback = order
        if bio.can_feed_now(order, stock_of):
            return order
    return fallback


def stage_samples(order):
    """Route matching samples from the store into self.input.

    matches_order() confirms the exact item/property identity satisfies
    the order before anything moves, which matters once glow, gene,
    Forged and Conditioned variants are in play.
    """
    staged = 0
    for fragment_id in order.requires:
        room = bio.outstanding(order, fragment_id)
        if room <= 0:
            continue
        available = stock_of(fragment_id)
        if available <= 0:
            continue
        if not self.matches_order(fragment_id, None):
            continue
        want = min(room, available)
        taken = self.input.take(fragment_id, want)
        if moved(taken):
            staged = staged + taken.moved
        elif taken.status != "no_op":
            print(taken.message)
    return staged


# --- main loop ----------------------------------------------------------

if store is None or not ports.connect_pair(
        self.input, self.output, SAMPLE_SOURCE, SURPLUS_SINK, "Bio Exchange"):
    notify("Bio Exchange halted: ports not connected", "warn")
else:
    while True:

        # Returns surplus that came back when another Exchange completed
        # the shared order first.
        ports.drain(self.output)

        orders = self.orders()
        publish_wanted(orders)
        bus.publish(bus.EXCHANGE_STATUS, "running")

        target = pick_order(orders)

        if target is None:
            notify(f"All bio orders complete - "
                   f"{self.lifetime_credits()} cr earned here")
            break

        # Only re-assign when the goal actually changes.
        active = self.active_order()
        if active is None or active.id != target.id:
            assigned = self.set_order(target.id)
            if assigned.status == "completed":
                continue          # finished elsewhere; pick_order moves on
            if assigned.status != "ok":
                notify(f"set_order {target.id}: {assigned.message}", "warn")
                sleep(5)
                continue
            active = self.active_order()

        # --- make sure the input has something to deliver ----------------
        if self.input.count() <= 0:
            if stage_samples(active) <= 0:
                # Lab hasn't extracted a match yet. Not an error.
                sleep(5)
                continue

        # --- deliver ------------------------------------------------------
        result = self.deliver()

        if result.status == "ok":
            continue
        elif result.status == "complete":
            notify(f"Order complete: {active.name} (+{active.reward} cr)")
            continue              # pick_order() moves to the next one
        elif result.status == "no_input":
            sleep(2)
        elif result.status == "output_full":
            ports.drain(self.output)
            sleep(2)
        elif is_transient(result):
            sleep(1)
        elif result.status == "no_active":
            continue              # re-assign next pass
        else:
            notify(f"deliver: {result.message}", "warn")
            sleep(1)