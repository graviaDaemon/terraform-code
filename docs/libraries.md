# Library reference

Sixteen in-game Libraries. Machine scripts import them by name; libraries import each
other the same way. No library ever touches `self` — the machine is passed in.

Functions prefixed with `_` are internal and omitted here.

**Foundations** — [caps](#libcapspy) · [bus](#libbuspy) · [results](#libresultspy) ·
[ports](#libportspy) · [storage](#libstoragepy) · [comms](#libcommspy) ·
[grid](#libgridpy)

**Domain arithmetic** — [bio](#libbiopy) · [earth](#libearthpy)

**Machine controllers** — [terraform](#libterraformpy) · [solar](#libsolarpy) ·
[power](#libpowerpy) · [fleet](#libfleetpy) · [rover](#libroverpy) ·
[smelting](#libsmeltingpy) · [dock](#libdockpy)

---

## `lib/caps.py`

What exists right now, never what a snapshot said. Every other library asks this one
whether something is available.

```python
import caps
caps.report("Smelter online", caps.common() + [("tier", caps.tier(self))])
```

| Function | Purpose |
| --- | --- |
| `component(component_id)` | The component, or `None` when it does not exist yet. Spelled out so a call site reads as a probe, not a lookup expected to succeed. |
| `research(research_id)` | `True` only when that research is unlocked. `False` when the research component itself is absent. |
| `outpost_of(machine)` | The machine's outpost ref, or `None` for vehicles, which have none. |
| `outposts(machine=None)` | That machine's own outpost, or every outpost owned when no machine is given. |
| `buildings(type_id=None, machine=None)` | Building refs of a type across those outposts. `[]` is a real answer: none deployed. |
| `building_types(machine=None)` | Distinct building type ids deployed, in discovery order. |
| `local_store(machine)` | A same-outpost store id for this machine's ports. Base Inventory at the home outpost, else a Warehouse, then a Storage Bin. Warns and returns `None` when the outpost has no store at all. |
| `tier(machine)` | Installed Mk tier, or `1` for a machine that has no tiers. |
| `degraded(machine)` | `True` while a Mk III pack is starved of its fluid; `False` for machines without tiers. |
| `common()` | The three probes every long-running script cares about: bus, archive, Auto Feeders. |
| `report(label, wants)` | One startup line — `Smelter online: bus yes, archive yes, feeders no, tier 2`. Booleans print as yes/no, anything else as itself. |

**Why Inventory comes first at the home outpost:** the shop delivers there, and it
holds many materials at once. A Storage Bin holds one. Connecting a Bio Lab to a
single-material bin at the home base breaks the loop on day one.

## `lib/bus.py`

Signal Bus helpers: live values shared between running scripts. Every function
degrades safely when the Signal Bus research is not unlocked, so the same scripts run
before and after it.

Channel ids are declared here so a typo is an import error rather than a channel that
silently never receives anything.

| Function | Purpose |
| --- | --- |
| `rover_channel(rover_id)` | Per-vehicle status channel, `rover.status:rover_1`. |
| `claim_channel(site_id)` | Per-site mining claim channel, `rover.claim:<site id>`. |
| `comms()` | The Signal Bus component, or `None` before that research. |
| `publish(channel, value)` | Broadcast a value. `True` when it landed. Publishing also resets the channel's age, so a loop that publishes each pass doubles as that script's heartbeat. |
| `clear(channel)` | Drop a channel's broadcast **and** the channel itself. A save holds 128 channels, so ids minted per subject have to be handed back rather than blanked. |
| `read(channel, default=None)` | Current value, without checking its age. |
| `read_fresh(channel, max_age_seconds, default=None)` | Current value only while its publisher is still reporting. This is the one to use when a stale value is worse than no value. |
| `is_alive(channel, max_age_seconds)` | Whether something published recently. Returns `True` when there is no bus at all: without one there is no way to tell, and crying wolf every pass is worse than staying quiet. |

Values must be JSON-safe. A component, a set, or a dict keyed by tuples is rejected.

## `lib/results.py`

Every world-changing command returns `.status` (stable, machine-readable) and
`.message` (localised prose). Branch on status only.

`"ok"` is not the universal spelling of success — some commands report `"started"`,
`"queued"`, `"charging"` or `"complete"`. These helpers cover the groups this base
uses; check a command's own outcome table before trusting a helper on a new one.

| Function | Purpose |
| --- | --- |
| `is_ok(result)` | Exactly `"ok"`. |
| `is_transient(result)` | The status means "ask again shortly", not "this failed". |
| `moved(result)` | A transfer moved at least one unit (`ok` or `partial`). |
| `transfer_fine(result)` | A transfer succeeded, or correctly had nothing to do. |
| `complain(result, context)` | Print an unexpected result to the console. Always returns `False`. |
| `alert(result, context)` | Raise an on-screen toast for an unexpected result. Returns `False`. |

## `lib/ports.py`

Input/output port helpers for machines that route items. These ports exist only once
Auto Feeders is researched.

| Function | Purpose |
| --- | --- |
| `feeders_unlocked()` | Whether Auto Feeders is researched. |
| `require_feeders(label)` | Check it once at startup and say plainly what is missing, instead of failing on the first transfer with a confusing error. |
| `connect(port, target, label)` | Latch one port onto a named store, notifying on failure. |
| `connect_pair(input_port, output_port, source, sink, label)` | Latch both ports. `True` only when both succeeded. |
| `drain(output_port)` | Push every stack to its connected sink, with exact property matching so glow, gene, Forged and Conditioned variants are preserved rather than collapsed. A full output blocks extraction and discarding, so call this first. |

## `lib/storage.py`

Routing across a bank of stores. A Storage Bin holds **one** material at a time: the
first deposit latches it and the lock clears only when it drains empty. With several
bins every transfer becomes a routing decision, and this makes it once.

Two mistakes look identical from the outside, and both look like a full bank:
component ids are not guessable, and Inventory is not a Storage Bin — it holds many
materials and its `has_space()` takes an item id, not an amount.

| Function | Purpose |
| --- | --- |
| `discover(type_id="storage_bin", machine=None)` | Ids of every building of that type. Pass a building to search its own outpost; vehicles have no outpost, so they search the whole network. |
| `bin_of(store_id)` | The component for a store id, or `None`. |
| `missing(store_ids)` | Which ids do not resolve. A misspelt id behaves exactly like a full bin at every call site. |
| `check(store_ids, label)` | Validate a list at startup, naming what is wrong and what exists, and return only the ids that resolve. |
| `material(store_id)` | The latched item id, or `""` for a store that accepts anything. |
| `accepts(store_id, item_id, amount=1)` | Whether this store would take that much of that item. |
| `sink_for(store_ids, item_id, amount=1)` | Best store to deposit into. Prefers one already holding the item, so empty bins stay unlatched for materials with nowhere else to go. |
| `source_for(store_ids, item_id, amount=1)` | A store holding at least that much. |
| `total_of(store_ids, item_id)` | Units across the whole bank. |
| `snapshot(store_ids)` | `{store_id: {item, count}}`, JSON-safe, ready for a bus channel. |

## `lib/comms.py`

Earth uplink. The transmitter has to be connected to a destination before it sends
anything, and every submission is the same three calls.

| Function | Purpose |
| --- | --- |
| `transmit_earth(channel, payload, transmitter_id="transmitter")` | Connect the transmitter to Earth and send. `channel` is `self.contract.id` for a contract, or a plain name for telemetry. |

## `lib/grid.py`

Sector maths for the A1–H24 surface grid. Row index 0 is `"A"`. Movement is cardinal
only, so Manhattan distance is exact rather than an estimate.

| Function | Purpose |
| --- | --- |
| `parse(sector)` | `"E13"` → `(4, 13)`. |
| `sector_name(row_index, col)` | `(4, 13)` → `"E13"`. |
| `in_bounds(row_index, col)` | Whether that lands inside A1–H24. |
| `all_sectors()` | Every sector name in row-major order. |
| `distance(a, b)` | Manhattan distance in steps. |
| `step_toward(here, goal)` | The single adjacent sector to move to next, or `None` if already there. Columns close first, then rows, because diagonal moves are invalid. |
| `nearest(here, sectors)` | The closest of an iterable of sector names, including a dict. |

## `lib/bio.py`

Bio Order arithmetic. Nothing here touches a component, so it behaves identically
whether the orders came from `self.orders()` on an Exchange or from a component
lookup elsewhere.

| Function | Purpose |
| --- | --- |
| `outstanding(order, fragment_id)` | Units this order still has **uncommitted** room for. Subtracts delivered and in-transit, so two machines do not both fill the same slot. |
| `is_open(order)` | Whether the order still has work left. |
| `wanted_fragments(orders)` | Set-shaped dict of fragment ids some open order still has room for. Returns `None` when the order list could not be read, which is not the same as "nothing is wanted". |
| `is_wanted(wanted, fragment_id)` | Whether a fragment is worth keeping. Treats both `None` and empty as "keep it" — discarding specimens because a read failed throws away good work. |
| `can_feed_now(order, stock_of)` | Whether local stock can satisfy some part of this order now. `stock_of` is a callable taking a fragment id. |

## `lib/earth.py`

Earth Order arithmetic, plus the base-wide demand signal — how a machine nowhere near
the dock finds out what Earth is waiting for.

Campaign orders never expire. Weekly orders do, and everything shipped toward an
unfinished weekly is lost at refresh.

| Function | Purpose |
| --- | --- |
| `board()` | The Earth Orders component, or `None`. |
| `is_open(order)` | Whether the order still wants units. |
| `outstanding(order, item_id)` | Units still needed. Several docks can serve one order and share shipped progress, so this is the shared remainder, not one dock's private one. |
| `needs(order)` | `{item_id: units still owed}`. Empty when satisfied. |
| `available_orders(prefer_weekly=False)` | Open orders, weekly first when asked. |
| `publish_demand(order)` | Broadcast what Earth still wants. Published every pass so readers can tell a live dock from a stopped one; an empty dict means "alive and wants nothing". |
| `demand()` | `{item_id: units}` currently wanted, or `{}` when there is no dock, no bus, or the broadcast has gone stale. |
| `wanted_by_earth(item_id)` | Whether a live order still needs that item. |

## `lib/terraform.py`

Controllers for the three terraforming tracks. `set_power`, `set_intake`, `sync` and
`dump_waste` are all self-only, so pass `self`, never a component lookup.

| Function | Purpose |
| --- | --- |
| `conserving()` | Whether the supervisor is asking for restraint. Falls back to `False` when the broadcast is missing or stale, so a stopped supervisor releases the fleet rather than freezing it. |
| `calibrate_heater(gen, state)` | Scan 1–10 W and stop at the first setting that reads 100% efficiency. Ten ticks, once per thermal state. Falls back to the best seen if nothing reads 100%, which happens when the heater is degraded or unpowered during the sweep. |
| `run_heater(gen, interval=5)` | Hold the optimal power for the current thermal state, forever. Learns each state once, shares the table through the bus and the archive, and recalibrates if a cached setting stops peaking. |
| `run_oxygen(gen, atmosphere_id="atmosphere", interval=1)` | Track the CO2 sweet spot and dump waste inside the clean window. |
| `run_pressure(gen, interval=1)` | Sync once inside every resonance window, and announce any window missed. |

All three announce their tier at startup and name the starved port the moment a Mk III
pack loses its feed, because a starved machine silently falls back to the previous
tier's output.

## `lib/solar.py`

Panel tracking. Base power is owned by `lib/power.py`, which runs on `solar_1` and
calls the first two of these itself.

| Function | Purpose |
| --- | --- |
| `track_sun(gen, clock)` | Tilt to face the sun: 0° at zenith, 90° at the horizon. |
| `check_output(gen, clock)` | Warn when daylight output has collapsed. Dusk and night are expected. |
| `run(gen, interval=5)` | Track the sun forever. |

## `lib/power.py`

The power supervisor. The single publisher of `power.mode`, `power.budget` and
`power.shed`, and the only script that flips breakers.

The budget is watt-hours needed to reach dawn, computed from a **measured** night load
and night length rather than a fixed battery percentage. The game pauses the whole
subnet when a tick is unfunded, so the only number that matters is whether stored
energy bridges the dark hours.

Learned state is written to the Data Archive when it exists and to the Signal Bus
otherwise, so the supervisor works before that research and improves after it.

### Ledger and budget

| Function | Purpose |
| --- | --- |
| `default_ledger()` | A fresh ledger: default night hours, nothing measured yet. |
| `load_ledger()` / `save_ledger(ledger)` | Read and write the ledger, archive first, bus as fallback. |
| `load_shed()` / `save_shed(record)` | Read and write the record of what was switched off. |
| `fold_phase(ledger, phase, hours, gen_wh, con_wh)` | Fold a completed day or night into the running averages. |
| `account(ledger, summary, clock)` | Accumulate this pass into the current phase and fold on a phase change. |
| `dawn_report(ledger, clock)` | One line at dawn: night length, night load, minimum stored. |
| `night_load(ledger)` | Measured night load, or the highest consumption seen so far before the first night completes. |
| `hours_to_dawn(ledger, phase, now)` | Hours remaining, never dropping below one while it is still night. A need of zero at the tail of the night would restore every load onto empty batteries. |
| `next_mode(current, stored, need, net, phase)` | Normal, conserve or critical. Exit lines are higher than entry lines so the mode does not flap. |
| `publish(mode, summary, need, hours)` | Broadcast the budget, and the mode. Critical publishes as `conserve` on `power.mode` for readers that only know two modes. |

### Breakers

| Function | Purpose |
| --- | --- |
| `machines_of(type_ids)` | `[(id, type_id)]` across every outpost, in the order given. |
| `unmanaged_types()` | Deployed types this supervisor neither sheds nor guards. Reported at startup so a new machine type is added deliberately rather than left running through a critical night. |
| `wants_shed(mode, type_id)` | Whether this mode sheds that type. |
| `switch_off(control, machine_id, reason)` / `switch_on(control, machine_id, reason)` | Flip one breaker, with the outcome named. |
| `shed(control, record, mode, net)` | Shed one machine per pass while conserving and net draw is negative; shed everything at once when critical. |
| `restore_one(control, record)` / `restore_all(control, record)` | Bring back what was shed, most recent first. `restore_all` runs at startup, because the game does not return a manually powered-off machine to service on its own. |

### Idle rules

A machine with nothing to do is switched off regardless of power mode, and switched
back on by its own rule rather than by the mode pass.

| Function | Purpose |
| --- | --- |
| `smelter_idle(smelter)` | Idle when no bin anywhere holds any input. A smelter with no recipe set is never judged idle — with no inputs, "no bin holds any input" is vacuously true and it could never come back. |
| `dock_idle(dock)` | Idle when there is no active order and none available. |
| `wanted_fragments()` | Wanted fragment ids, from the bus while the Exchange publishes, read directly once that goes stale. A dark Exchange stops publishing, so the bus alone can never say the orders returned. |
| `bio_idle()` | Idle only after the wanted list has been empty for many consecutive passes. |
| `idle_verdict(machine_id, type_id, bio_verdict)` | `True` idle, `False` has work, `None` cannot tell — and `None` holds the current state. |
| `idle_pass(control, record, mode)` | Apply the idle rules once. |

### Entry point

| Function | Purpose |
| --- | --- |
| `run(gen, interval=10)` | Track the sun on this panel and supervise base power forever. Falls back to plain sun tracking when there is no clock or power control component. |

## `lib/fleet.py`

Charge docked vehicles, rescue stranded ones. `charge()`, `dispatch_rescue()` and
`cancel_rescue()` are self-only, so this must run on the station.

Telemetry gives battery, position, docked and rescue state for every owned vehicle
with no Signal Bus at all. The bus is consulted for one thing telemetry cannot
express: a vehicle's own **intent**.

| Function | Purpose |
| --- | --- |
| `vehicles()` | Snapshots of every owned ground vehicle. Re-query rather than holding a ref across a sleep. |
| `intent_of(vehicle_id)` | What that vehicle says it is doing, or `None` if it is not reporting. Absence is normal. |
| `is_waiting(vehicle)` | Whether it says it is parked waiting for a charge. |
| `should_rescue(vehicle)` | Whether a drone is needed. A rescue *stops* the target, so a vehicle already driving home is left alone unless it is below the critical floor. |
| `rescue_worst(station)` | Send the one drone to the lowest vehicle that needs it. |
| `charge_docked(station, conserving)` | Queue every docked vehicle below its target. While conserving that target is the critical floor, except for a vehicle reporting `waiting_for_charge`, which is taken to depart level. |
| `station_position(station)` | This station's own docking point. |
| `explain_waiting(station, conserving)` | Name the reason a waiting rover is not being charged — once per episode, not every pass. From the rover's side, "docked and ignored" and "parked just outside the footprint" look identical. |
| `watch_bays(station, bays)` | Re-read bay count and rate each pass, so an upgrade applied mid-run is announced. |
| `run(station, interval=10)` | Charge and rescue forever. |

## `lib/rover.py`

Prospecting and mining: sweep, survey, drive, drill, return. One script per rover
rather than machines coordinating, because nav, sonar and drill are all modules on the
vehicle.

The rule the whole file is built around: **get near, park, then work.** Reaching
tolerance means close enough, not stopped, so `nav.brake()` comes before every scan,
survey, drill or transfer.

### Range and drive cost

| Function | Purpose |
| --- | --- |
| `drain_per_meter()` | Learned Wh per metre, or a pessimistic default. Wrong high brings the rover home early; wrong low strands it. |
| `record_drain(wh_used, meters)` | Fold one observed trip into the running estimate. |
| `can_reach_and_return(vehicle, x, y)` | Whether the current charge covers the round trip with reserve left. |

### Home and docking

| Function | Purpose |
| --- | --- |
| `find_home(vehicle)` | Resolve home to the nearest charging station's own docking point, across every outpost. Falls back to the outpost anchor, then the origin, and says which it picked. |
| `docked_status(vehicle)` | The outpost's own verdict, read from fleet telemetry — the exact flag the charging station gates on. |
| `at_home(vehicle)` | Docked, or within tolerance of the docking point when docking cannot be read. |
| `undocked_warning(vehicle)` | Parked on the home target but not counted as docked. Says so once per episode. |

### Movement

| Function | Purpose |
| --- | --- |
| `drive_to(vehicle, x, y, throttle, tolerance)` | Drive there and park. Returns `False` without parking when the battery hits the stranding floor or progress stops, because "head home" and "give up here" are different decisions. |
| `go_home(vehicle)` | Drive to the docking point and park. `True` only once the outpost counts the rover as docked. |

### Prospecting memory

Records are flat and JSON-safe: id, name, position, item, hardness, purity, exhausted.
Site objects do not survive a restart, and a site's revealed fields are only real on
the object `survey()` handed back.

| Function | Purpose |
| --- | --- |
| `record_of(site)` | Snapshot one surveyed mineral site. |
| `remember(record)` | Persist it, so the other rover and the next restart can use it. |
| `forget(record)` | Mark it worked out. |
| `known_sites()` | Every remembered site still worth a visit, read fresh each time. The other rover writes to the same key, so a cached list would miss its finds and re-target its dead sites. |

### Claims

| Function | Purpose |
| --- | --- |
| `claim_holder(site_id)` | The rover holding a fresh claim, or `None`. |
| `claimed_by_other(vehicle, site_id)` | Whether someone else holds it. |
| `claim(vehicle, site_id)` | Take or refresh this rover's claim. |
| `release(site_id)` | Hand it back, channel and all. |

A claim is a broadcast that ages out, not a queued job: nothing has to be pre-filled,
and a rover stopped mid-trip cannot lock a deposit forever.

### Surveying and choosing

| Function | Purpose |
| --- | --- |
| `prospect_points(sonar_range, rings=3)` | Waypoints on expanding rings spaced one sonar range apart, so consecutive sweeps overlap. Home is swept first, otherwise the disc around the base is never covered. |
| `rover_number(vehicle_id)` | The trailing number in an id. |
| `rover_count()` | How many rovers the fleet owns, counted rather than assumed. |
| `ring_offset(vehicle, total_points)` | Where on the ring this rover starts, so rovers spread evenly without being told about each other. |
| `in_bounds(x, y, planet)` | Whether the planet accepts a coordinate. |
| `sweep(vehicle)` | Sonar sweep from where the rover is parked. Only `"ok"` documents the payload, so partial statuses return an empty list rather than reading a field that is not promised. |
| `survey(vehicle, site)` | Survey and return the **fresh** site object. The pre-survey object keeps its hidden values forever. |
| `is_minable(record, hardness_limit)` | Whether it is ore this drill can still work. |
| `score(record, vehicle, wanted_by_earth={})` | Wanted beats rich, rich beats close. Earth demand outranks both — ore nobody is waiting for just fills a crate. |
| `best_site(vehicle, records, hardness_limit)` | Highest-scoring reachable, unclaimed, minable site. |

### Mining and unloading

| Function | Purpose |
| --- | --- |
| `mine_out(vehicle, record)` | Drill until the hold is full, the site refuses, or reserve is hit. Returns units mined and whether the deposit is gone. |
| `work_site(vehicle, record)` | Claim, drive, drill, release. The claim goes up before the drive, so the other rover picks different ground while this one is still on its way. |
| `unload(vehicle, sinks)` | Empty the hold into the bank, routing each stack separately because a bin holds one material. Needs Auto Feeders. |
| `wait_for_charge(vehicle, level)` / `working()` | Park and wait, saying once when nothing is charging the rover. |
| `report(vehicle, state, target=None)` | Broadcast state, battery, cargo, position, docked and current target. |
| `run(vehicle, sink=None, rings=3, start_index=None)` | The whole loop. Leave every argument off and the sink, the ring offset and the site list are all worked out at runtime. |

**On exhaustion:** `drill.mine()` has no site-empty outcome. A worked-out deposit
simply stops being a site, and the next call reports `not_at_site`. That means
exhausted only once this visit has pulled a unit out of the ground — the identical
status with nothing mined means the rover parked short.

## `lib/smelting.py`

Latch a recipe, feed ore in, drain metal out. Both buffers hold 50 units and
processing is asynchronous, so the loop drains before it feeds and output never blocks
production.

| Function | Purpose |
| --- | --- |
| `resolve_recipe(smelter, recipe_id)` | The recipe, or `None` with an explanation. `find_recipe()` returns `None` for unknown, locked and wrong-machine ids alike, so the message covers all three. |
| `latch_recipe(smelter, recipe)` | Select it, unless it is already active or a unit is mid-craft. |
| `drain(smelter, recipe, sink_bins)` | Push finished units to whichever bin is already latched to that metal, falling back to an empty one. |
| `feed(smelter, recipe, source_bins)` | Top up the input buffer from the ore bank. |
| `run(smelter, recipe_id, source_bins, sink_bins, interval=2)` | Run one smelter forever. Stops feeding while the base conserves: a smelter's draw is variable and spent only while processing, so the throttle is simply not giving it more ore. |

## `lib/dock.py`

Supply Dock: assign an Earth Order, load it, ship it, choose the next.

The dock **pulls**. Its input is order-aware and accepts only what the active order
still needs, so the loop connects it to a crate and takes — a rover cannot push into a
dock and does not need to. Dispatch is continuous once enabled, so the script's real
work is the four transitions: pick, load, enable, and react when the order clears.

| Function | Purpose |
| --- | --- |
| `score_order(order)` | Rank open orders. Part-shipped orders win, because abandoning progress wastes it. A recipe or tech reward outranks a credit payout. |
| `pick_order(crates, prefer_weekly=False)` | The best order we can actually make progress on. An order we cannot feed is a dock sitting idle with an assignment. |
| `assign(dock_machine, order)` | Latch an order onto the dock, naming the reason when it refuses — loaded cargo from a previous order is physical and blocks reassignment. |
| `load(dock_machine, order, crates)` | Pull what the order still needs out of the crate bank. |
| `enable(dock_machine)` | Start the dispatcher if it is not already running. |
| `run(dock_machine, crates, prefer_weekly=False, interval=10)` | Serve Earth Orders forever, publishing demand every pass. |
