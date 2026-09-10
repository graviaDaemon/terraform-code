import caps
import dock
import storage

# Discovered, not hardcoded: this dock's own outpost is searched for
# Storage Bins, and its local store (base Inventory at Nocturna Base, a
# Warehouse or Bin elsewhere) is added as a freight endpoint.
CRATES = storage.discover(machine=self)
LOCAL = caps.local_store(self)
if LOCAL is not None and LOCAL not in CRATES:
    CRATES.append(LOCAL)

# Chase Vestibule's queue: its current order rewards the Power Line
# Segment recipe, which is what unblocks wiring outpost 2 into the home
# grid (D-022). Set to None to let the dock rank orders by progress and
# reward on its own again.
PREFER = None

# prefer_weekly=True weighs expiring weekly orders ahead of campaign ones.
# Weekly progress is lost entirely when the board refreshes every 7 days.
dock.run(self, CRATES, prefer_weekly=False, prefer=PREFER)
