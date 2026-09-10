import grid
from results import is_transient, alert

SCANNER_ID = "scanner_1"

ITEM_MOVE_HEAT = 1
EMPTY_MOVE_HEAT = 7

scanner = get_component(SCANNER_ID)

# Sectors we have already emptied. In-memory only: a save/load restarts
# this script from line 1 and every sector becomes a candidate again.
cleared = {}


def item_sectors():
    """Fresh snapshot of scanned sectors that should still hold something."""
    targets = {}
    for sector, result in scanner.get_scanned().items():
        if result.status != "ok":
            continue
        if sector in cleared:
            continue
        targets[sector] = result
    return targets


def step_cost(sector, targets):
    """Heat the next step will add.

    Unknown cells are assumed empty, which is the conservative guess -
    it makes us stop earlier rather than overheat mid-route.
    """
    if sector in targets:
        return ITEM_MOVE_HEAT
    return EMPTY_MOVE_HEAT


# ------------------ Main loop ------------------------------------------

while True:
    held = self.get_held()
    if held != "":
        stored = self.store()
        if stored.status == "inventory_full":
            notify(f"Inventory full - harvester still holding {held}", "warn")
            sleep(10)
        continue

    if self.is_overheated():
        sleep(1)
        continue

    targets = item_sectors()
    if len(targets) == 0:
        sleep(10)                  # nothing known; let the scanner sweep again
        continue

    here = self.get_position()

    if here in targets:
        picked = self.collect()
        if picked.status in ["ok", "empty"]:
            # "empty" means the snapshot was stale and the cell is bare.
            # Mark it either way so we never pay that cost twice.
            cleared[here] = True
        elif is_transient(picked) or picked.status == "overheated":
            sleep(1)
        else:
            alert(picked, f"collect {here}")
            sleep(1)
        continue

    goal = grid.nearest(here, targets)
    if goal is None:
        sleep(5)
        continue

    next_sector = grid.step_toward(here, goal)
    if next_sector is None:
        continue

    if self.get_heat() + step_cost(next_sector, targets) >= self.get_max_heat():
        sleep(1)
        continue

    moved = self.move(next_sector)
    if moved.status in ["ok", "already_here"]:
        continue
    elif is_transient(moved) or moved.status == "overheated":
        sleep(1)
    else:
        alert(moved, f"move {next_sector}")
        sleep(1)