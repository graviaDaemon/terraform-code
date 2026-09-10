"""Bio Order arithmetic, shared by the collector, the lab and the exchange.

Pure maths over order objects - nothing here touches a component, so it
behaves identically whether the orders came from `self.orders()` on an
Exchange or from `get_component("bio_exchange_1").orders()` elsewhere.
"""


def outstanding(order, fragment_id) -> int:
    """Units of `fragment_id` this order still has uncommitted room for.

    Subtracts delivered units AND in_transit units, so two machines
    working the same order do not both try to fill the same slot.
    """
    required = order.requires[fragment_id]
    done = order.delivered.get(fragment_id, 0)
    committed = order.in_transit.get(fragment_id, 0)
    return required - done - committed


def is_open(order) -> bool:
    """True when the order still has work left on it."""
    return order.status != "complete"


def wanted_fragments(orders):
    """Set-shaped dict of fragment ids some open order still has room for.

    Pass the list from `exchange.orders()` or `self.orders()`.

    Returns None when `orders` is None, meaning "the order list could not
    be read". That is a different situation from "nothing is wanted", and
    callers must not collapse the two - see is_wanted().
    """
    if orders is None:
        return None
    wanted = {}
    for order in orders:
        if not is_open(order):
            continue
        for fragment_id in order.requires:
            if outstanding(order, fragment_id) > 0:
                wanted[fragment_id] = True
    return wanted


def is_wanted(wanted, fragment_id) -> bool:
    """True when `fragment_id` is worth keeping.

    Treats None (orders unreadable) and an empty dict (nothing wanted
    right now) as "keep it". Discarding specimens because a read failed
    throws away good work.
    """
    if wanted is None or len(wanted) == 0:
        return True
    return fragment_id in wanted


def can_feed_now(order, stock_of) -> bool:
    """True when local stock can satisfy some part of this order today.

    `stock_of` is a one-argument callable taking a fragment id and
    returning the units on hand, e.g. `store.count`.
    """
    for fragment_id in order.requires:
        if outstanding(order, fragment_id) > 0:
            if stock_of(fragment_id) > 0:
                return True
    return False