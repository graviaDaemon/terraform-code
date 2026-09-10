"""Earth Order arithmetic and the base-wide demand signal.

Two separate things live here on purpose:

  - Pure maths over Order objects, usable anywhere (the Supply Dock owns
    the orders, but the rover and smelter want to know about them).
  - The `earth.demand` broadcast, which is how a machine that is nowhere
    near the dock finds out what Earth is waiting for.

Earth Orders are supply lines; Contracts are puzzles. Campaign orders
never expire; weekly orders do, and everything shipped toward an
unfinished weekly is lost at refresh - so `expires_day` is worth weighing
when choosing.
"""

import bus

ORDERS_ID = "orders"

# How stale the demand broadcast may be before readers ignore it. A dock
# that stops publishing must not leave the base reserving ore forever.
DEMAND_MAX_AGE = 60


def board():
    """The Earth Orders component, or None if it is unavailable."""
    return get_component(ORDERS_ID)


def is_open(order) -> bool:
    """True while the order still wants units."""
    return order.status != "completed"


def outstanding(order, item_id) -> int:
    """Units of `item_id` this order still needs.

    `.shipped` counts what has already landed. Several docks can serve one
    order and share shipped progress, so this is the shared remainder, not
    this dock's private one.
    """
    required = order.requires[item_id]
    landed = order.shipped.get(item_id, 0)
    return required - landed


def needs(order):
    """{item_id: units still owed} for one order. Empty when satisfied."""
    if order is None:
        return {}
    remaining = {}
    for item_id in order.requires:
        short = outstanding(order, item_id)
        if short > 0:
            remaining[item_id] = short
    return remaining


def available_orders(prefer_weekly=False):
    """Open Earth Orders, weekly first when asked. [] when none exist.

    Weekly orders pay credits only and expire as a board every seven days;
    campaign orders never expire and can unlock recipes or technology.
    """
    component = board()
    if component is None:
        return []

    campaign = component.list_orders()
    weekly = component.list_weekly_orders()

    ordered = campaign + weekly
    if prefer_weekly:
        ordered = weekly + campaign

    open_orders = []
    for order in ordered:
        if is_open(order):
            open_orders.append(order)
    return open_orders


def publish_demand(order):
    """Broadcast what Earth still wants, for machines away from the dock.

    Published every pass so the channel's age stays fresh - readers use
    that to tell a running dock from a stopped one. Publishing an empty
    dict is meaningful: it says "the dock is alive and wants nothing".
    """
    bus.publish(bus.EARTH_DEMAND, needs(order))


def demand():
    """{item_id: units} Earth currently wants, or {} when unknown.

    Returns {} when there is no dock, no bus, or the broadcast has gone
    stale. Readers must treat {} as "no reason to reserve anything" and
    carry on with their normal work.
    """
    published = bus.read_fresh(bus.EARTH_DEMAND, DEMAND_MAX_AGE, {})
    if published is None:
        return {}
    return published


def wanted_by_earth(item_id) -> bool:
    """True when a live Earth Order still needs `item_id`."""
    return demand().get(item_id, 0) > 0