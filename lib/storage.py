"""Routing across a bank of stores: Storage Bins, and base Inventory.

A Storage Bin holds ONE material at a time: the first deposit latches it
and the lock clears only when the bin drains empty. With several bins that
turns every transfer into a routing decision, and these helpers make it
once.

Two things here are easy to get wrong, and both look identical from the
outside - like a full bank:

  - Component ids are NOT guessable. A bin's id is whatever the game gave
    it, shown on the machine card's info panel. Use discover() rather than
    writing ids by hand, and check() a list before trusting it.
  - Inventory is not a Storage Bin. It holds many materials at once and
    its has_space() takes an ITEM ID, not an amount. Calling the bin API
    on it raises instead of returning False.
"""

import caps

STORAGE_BIN_TYPE = "storage_bin"
INVENTORY = caps.INVENTORY


def discover(type_id=STORAGE_BIN_TYPE, machine=None):
    """Ids of every `type_id` building, discovered rather than guessed.

    Pass a building (smelter, dock) to search its own outpost. Vehicles
    have no `.outpost`, so with no machine - or with a vehicle - this
    falls back to every outpost in the network.

    Returns [] when nothing matches, which is a real answer: no such
    building is deployed, rather than a failed lookup.
    """
    return [ref.id for ref in caps.buildings(type_id, machine)]


def bin_of(store_id):
    """Component for `store_id`, or None when no such component exists."""
    return get_component(store_id)


def missing(store_ids):
    """Ids in `store_ids` that do not resolve to a component.

    A misspelt or invented id behaves exactly like a full bin at every
    call site, which sends you looking at your storage instead of at your
    configuration. Call this once at startup.
    """
    absent = []
    for store_id in store_ids:
        if bin_of(store_id) is None:
            absent.append(store_id)
    return absent


def check(store_ids, label):
    """Validate store ids, naming any that do not exist and what does.

    Returns only the ids that resolve, so a partly-wrong list still works
    for the stores that are real.
    """
    absent = missing(store_ids)
    if len(absent) > 0:
        notify(f"{label}: no such store {absent}."
               f" Storage bins found: {discover()}", "warn")

    live = []
    for store_id in store_ids:
        if store_id not in absent:
            live.append(store_id)
    return live


def material(store_id):
    """The latched item id, or "" for a store that accepts anything.

    Inventory is multi-material and has no latch, so it reports "".
    """
    if store_id == INVENTORY:
        return ""
    store = bin_of(store_id)
    if store is None:
        return ""
    return store.get_material()


def accepts(store_id, item_id, amount=1) -> bool:
    """True when this store would take `amount` units of `item_id`.

    A bin accepts an item when it is already latched to it, or when it is
    empty and therefore unlatched. Inventory takes anything with a free
    slot - and its has_space() wants the item id, not a count.
    """
    store = bin_of(store_id)
    if store is None:
        return False

    if store_id == INVENTORY:
        return store.has_space(item_id)

    latched = store.get_material()
    if latched != "" and latched != item_id:
        return False
    return store.has_space(amount)


def sink_for(store_ids, item_id, amount=1):
    """Best store to deposit `item_id` into, or None if none will take it.

    Prefers a store already holding this item, so empty bins stay
    unlatched and available for materials with nowhere else to go.

    None means "nothing here accepts it", which covers a genuinely full
    bank AND a list of ids that do not exist. Use missing() to tell those
    two apart before reporting either.
    """
    fallback = None
    for store_id in store_ids:
        if not accepts(store_id, item_id, amount):
            continue
        if material(store_id) == item_id:
            return store_id
        if fallback is None:
            fallback = store_id
    return fallback


def source_for(store_ids, item_id, amount=1):
    """A store holding at least `amount` of `item_id`, or None.

    count(item_id) is common to Inventory, Storage Bins, Warehouses and
    Lead Casks, so one helper searches all of them.
    """
    for store_id in store_ids:
        store = bin_of(store_id)
        if store is None:
            continue
        if store.count(item_id) >= amount:
            return store_id
    return None


def total_of(store_ids, item_id) -> int:
    """Units of `item_id` across the whole bank."""
    total = 0
    for store_id in store_ids:
        store = bin_of(store_id)
        if store is not None:
            total = total + store.count(item_id)
    return total


def snapshot(store_ids):
    """{store_id: {"item": ..., "count": ...}} for dashboards.

    JSON-safe, so it can go straight onto a Signal Bus channel.
    """
    report = {}
    for store_id in store_ids:
        store = bin_of(store_id)
        if store is None:
            continue
        latched = material(store_id)
        report[store_id] = {
            "item": latched,
            "count": store.count(latched) if latched != "" else 0,
        }
    return report