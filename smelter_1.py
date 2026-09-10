import caps
import smelting
import storage

# Discovered, not hardcoded: this smelter's own outpost is searched for
# Storage Bins, and its local store (base Inventory at Nocturna Base, a
# Warehouse or Bin elsewhere) is added as a fallback source and sink.
CRATES = storage.discover(machine=self)
LOCAL = caps.local_store(self)
if LOCAL is not None and LOCAL not in CRATES:
    CRATES.append(LOCAL)

smelting.run(self, "smelt_iron_ingot", CRATES, CRATES)
