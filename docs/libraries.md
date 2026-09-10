# Library reference

Twenty-one in-game Libraries. Machine scripts import them by name; libraries import each
other the same way. No library ever touches `self` — the machine is passed in.

Functions prefixed with `_` are internal and omitted here.

**Foundations** — [caps](#libcapspy) · [bus](#libbuspy) · [results](#libresultspy) ·
[ports](#libportspy) · [storage](#libstoragepy) · [comms](#libcommspy) ·
[grid](#libgridpy)

**Domain arithmetic** — [bio](#libbiopy) · [earth](#libearthpy) ·
[recipes](#librecipespy) · [survey](#libsurveypy)

**Machine controllers** — [terraform](#libterraformpy) · [solar](#libsolarpy) ·
[power](#libpowerpy) · [fleet](#libfleetpy) · [vehicle](#libvehiclepy) ·
[rover](#libroverpy) · [scout](#libscoutpy) · [pioneer](#libpioneerpy) ·
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
| `vehicle_channel(vehicle_id)` | Per-vehicle status channel, `vehicle.status:rover_1`. |
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

## `lib/recipes.py`

The recipe graph: what the base must **mine** to fill what Earth ordered. An Earth
Order only ever asks for finished goods, and a mineral site's `item_id` is only ever
one of seven raw ores, so "Earth wants 150 `iron_ingot`" and "drive to an `iron_ore`
deposit" never matched on their own. This walks `Recipe.output_item` back to
`Recipe.inputs` until nothing wanted is something the base could make for itself.

Nothing is hardcoded to smelters or to iron: any deployed building that answers
`list_recipes()` joins the graph, and a blueprint unlocked today joins it on the next
re-probe.

| Function | Purpose |
| --- | --- |
| `machines()` | `(machine_id, component)` for every deployed building that has recipes — discovered by asking, since anything that raises is simply not a crafting machine. |
| `table()` | `{output_item: [(machine_id, recipe)]}` across those machines. Cached, re-probed on a slow counter. |
| `producers(item_id)` | The unlocked pairs that make that item. `[]` is how a raw material identifies itself. |
| `live_demand()` | `{item_id: units}` of **finished goods** Earth wants. The dock's broadcast while it is running, the order board read directly when it is not. |
| `raw_demand()` | `{item_id: units}` of **mineable** material the base needs. What `rover.best_site()` scores against. |
| `best_recipe(machine, wanted)` | The recipe on that machine whose output is most wanted, or `None`. Picks what to *make*, where `raw_demand()` picks what to *mine*. |

**Why the board fallback is not optional:** `set_powered(id, False)` pauses the
machine's script. The moment the supervisor conserve-sheds the Supply Dock, the
`earth.demand` channel goes stale within a minute — so silence there means "nobody is
publishing", never "nothing is wanted". Same shape, and the same reason, as the dark
Exchange in [power](#libpowerpy).

**The plural name is deliberate:** `recipe` and `recipe_id` are parameter names all
through [smelting](#libsmeltingpy), and a library whose name a parameter shadows
cannot be reached from inside the functions that use it.

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

It supervises **one grid** — the one its own panel sits on — not the planet.

### The grid

| Function | Purpose |
| --- | --- |
| `resolve_grid(control, gen)` | The `PowerGrid` this panel sits on. `None` is a normal answer for a mobile, under-construction, non-grid or unmapped target. |
| `grid_anchor(grid)` | The grid's anchor id, or `None` when there is no grid to name. |
| `supply(control, grid)` | The figures to budget from: this grid's, or `control.total()` with a one-time warning. Both carry the same generation/consumption/storage fields. |
| `scope_to(grid)` / `on_grid(machine_id)` | Limit `machines_of()` to this grid's machines. |
| `other_grids(control, anchor)` / `watch_grids(control, anchor, seen)` | Name every grid this supervisor is not managing, at startup and whenever that set changes. |

**Why not `control.total()`:** the manual defines it as "a planet-wide `PowerSummary`
across every independent grid". With one grid that is right by accident. The moment a
second outpost exists and is not yet wired, it pools solar and batteries this base
physically cannot reach into the night budget and skips conserving on a night it
should have — and a subnet that cannot fund a tick pauses *every* live consumer on it.
Shedding has the same flaw: `caps.buildings()` spans every outpost, so the supervisor
would flip breakers on machines that are not on its grid and get no relief. An unwired
outpost should be a line in the log, not a silence.

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
| `machines_of(type_ids)` | `[(id, type_id)]` on this supervisor's grid, in the order given. |
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
| `smelter_idle(smelter)` | Idle only when no bin anywhere holds an input of **any unlocked recipe**, not just the latched one. A smelter with no recipe set is never judged idle — with no inputs, "no bin holds any input" is vacuously true and it could never come back. |
| `dock_idle(dock)` | Idle when there is no active order and none available. |
| `wanted_fragments()` | Wanted fragment ids, from the bus while the Exchange publishes, read directly once that goes stale. A dark Exchange stops publishing, so the bus alone can never say the orders returned. |
| `bio_idle()` | Idle only after the wanted list has been empty for many consecutive passes. |
| `idle_verdict(machine_id, type_id, bio_verdict)` | `True` idle, `False` has work, `None` cannot tell — and `None` holds the current state. |
| `idle_pass(control, record, mode)` | Apply the idle rules once. |

**Why the smelter rule judges every recipe:** the smelter picks its recipe from live
demand now. Judging only the latched one deadlocks — a smelter that latches titanium
with no titanium ore in the bins is judged idle, gets its breaker pulled, and its own
script, the only thing that would ever re-read demand and switch back, is paused with
it. Staying powered whenever there is anything to convert is also the safer direction.

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

## `lib/vehicle.py`

Drive, park, dock, and never strand yourself — for every ground vehicle. A Rover, a
scouting Pioneer and a constructing Pioneer are the same thing from this file's point
of view, because everything here touches only `nav`, `battery` and fleet telemetry.

The rule the whole layer is built around: **get near, park, then work.** Reaching
tolerance means close enough, not stopped, so `nav.brake()` comes before every scan,
survey, drill or transfer.

Every fraction and tolerance is a module constant **and** a default argument
(`RESERVE_FRACTION`, `STRANDED_FRACTION`, `DEPART_FRACTION`, `CRUISE_THROTTLE`,
`RETURN_THROTTLE`, `ARRIVE_TOLERANCE`, `HOME_TOLERANCE`, `STALL_LIMIT`,
`IDLE_INTERVAL`, `CHARGE_WARN_AFTER`), because the Pioneer's are not the Rover's.

### Range and drive cost

| Function | Purpose |
| --- | --- |
| `drain_key(vehicle)` | That vehicle's own cost key, `vehicle.wh_per_meter:rover_1`. |
| `drain_per_meter(vehicle)` | Learned Wh per metre, or a pessimistic default. Wrong high brings the vehicle home early; wrong low strands it. |
| `record_drain(vehicle, wh_used, meters)` | Fold one observed trip into that vehicle's running estimate. |
| `can_reach_and_return(vehicle, x, y, reserve)` | Whether the current charge covers the round trip with reserve left. |

**Keyed per vehicle id, not per kind:** each mounted module adds to the draw while
moving, so two Pioneers on the same `.kind` carrying different rigs genuinely cost
different amounts per metre. A vehicle with no history uses the pessimistic default
and comes home early until it has learned its own figure. Ids also survive the module
swaps both Pioneers are due when Large Battery Holders unlock.

### Home and docking

| Function | Purpose |
| --- | --- |
| `find_home(vehicle)` | Resolve home to the nearest charging station's own docking point, across every outpost. Falls back to the outpost anchor, then the origin, and says which it picked. |
| `docked_status(vehicle)` | The outpost's own verdict, read from fleet telemetry — the exact flag the charging station gates on. |
| `at_home(vehicle, tolerance)` | Docked, or within tolerance of the docking point when docking cannot be read. |
| `undocked_warning(vehicle, warn_after)` | Parked on the home target but not counted as docked. Says so once per episode. |

### Movement, waiting and status

| Function | Purpose |
| --- | --- |
| `drive_to(vehicle, x, y, throttle, tolerance, stranded, stall_limit)` | Drive there and park. Returns `False` without parking when the battery hits the stranding floor or progress stops, because "head home" and "give up here" are different decisions. |
| `go_home(vehicle, throttle, tolerance, idle, warn_after)` | Drive to the docking point and park. `True` only once the outpost counts the vehicle as docked. |
| `in_bounds(x, y, planet)` | Whether the planet accepts a coordinate. |
| `wait_for_charge(vehicle, level, warn_after, idle)` / `working()` | Park and wait, saying once when nothing is charging the vehicle. `working()` clears both the wait counter and the charge tracker below. |
| `report(vehicle, state, target=None, extra=None)` | Broadcast state, battery, position, docked, home and target on `vehicle.status:<id>`. `extra` carries what only one kind of vehicle has — a rover's cargo count, a Pioneer's job id. |

### Departing

| Function | Purpose |
| --- | --- |
| `is_charging(vehicle)` | Whether the station is still working on this vehicle. Read through `try`/`except`, so a kind without `status()` reports "not charging" rather than stopping the loop. |
| `charge_stalled(vehicle, level, passes)` | Whether the level has stopped rising for several consecutive calls. The stored level is a high-water mark, so a dip does not reset it. |
| `ready_to_depart(vehicle, level, have_target, depart)` | The whole rule, in one place. |

`DEPART_FRACTION` is a floor, not a target: below it a vehicle never leaves. Above it,
leaving immediately throws away everything the station was still about to put in —
`fleet.CHARGE_TARGET` is a full battery, so departing at the floor costs half the range
every trip. Three things end the wait: the station finishing, the level going flat, or
a reachable job already being in range.

The stall fallback is what covers a station that is shed, unpowered, or capped at
depart level by conserve mode. None of those announce themselves; from the pad all
three look like a battery that stopped climbing. It is also why waiting on the
station's signal is correct in conserve mode, where a fixed higher threshold would wait
for a charge that is never coming.

`lib/fleet.py` imports `DEPART_FRACTION` from here rather than declaring its own: the
station tops a waiting rover to exactly the level the rover then refuses to leave
below, so the two numbers are load-bearing together.

## `lib/rover.py`

Prospecting and mining: sweep, survey, score, claim, drill, unload. One script per
rover rather than machines coordinating, because nav, sonar and drill are all modules
on the vehicle. Driving, parking, docking and the learned drive cost are delegated to
[vehicle](#libvehiclepy).

### Prospecting memory

Records are flat and JSON-safe: id, name, position, item, hardness, purity, exhausted.
Site objects do not survive a restart, and a site's revealed fields are only real on
the object `survey()` handed back.

| Function | Purpose |
| --- | --- |
| `record_of(site)` | Snapshot one surveyed mineral site. |
| `remember(record)` | Persist it, so the other rover and the next restart can use it. |
| `forget(record)` | Mark it worked out. |
| `known_sites()` | Every known mineral site still worth a visit, read fresh each time — the archive and the Journal ([survey](#libsurveypy)) merged, so a deposit surveyed by anything reaches the rovers. Where both describe the same site the archived record wins: it is the only one carrying `exhausted`, and the Journal would offer a worked-out deposit back forever. |

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
| `sweep(vehicle)` | Sonar sweep from where the rover is parked. Only `"ok"` documents the payload, so partial statuses return an empty list rather than reading a field that is not promised. |
| `survey(vehicle, site)` | Survey and return the **fresh** site object. The pre-survey object keeps its hidden values forever. |
| `is_minable(record, hardness_limit)` | Whether it is ore this drill can still work. |
| `score(record, vehicle, wanted_by_earth={})` | Wanted beats rich, rich beats close. Earth demand outranks both — ore nobody is waiting for just fills a crate. `wanted_by_earth` is **raw material**, keyed by ore id; handed the finished goods an order literally names, this test can never fire. |
| `best_site(vehicle, records, hardness_limit)` | Highest-scoring reachable, unclaimed, minable site. Scores against [`recipes.raw_demand()`](#librecipespy), read once per call rather than per candidate. |

### Mining and unloading

| Function | Purpose |
| --- | --- |
| `mine_out(vehicle, record)` | Drill until the hold is full, the site refuses, or reserve is hit. Returns units mined, whether the deposit is gone, and whether **we** were what stopped — no room, no charge. |
| `work_site(vehicle, record)` | Claim, drive, drill, release. The claim goes up before the drive, so the other rover picks different ground while this one is still on its way. |
| `unload(vehicle, sinks)` | Empty the hold into the bank, routing each stack separately because a bin holds one material. Needs Auto Feeders. |
| `report(rover, state, target=None)` | Broadcast the vehicle-layer status fields plus the hold count. |
| `run(vehicle, sink=None, rings=3, start_index=None)` | The whole loop. Leave every argument off and the sink, the ring offset and the site list are all worked out at runtime. |

**On exhaustion:** `drill.mine()` has no site-empty outcome. A worked-out deposit
simply stops being a site, and the next call reports `not_at_site`. That means
exhausted only once this visit has pulled a unit out of the ground — the identical
status with nothing mined means the rover parked short.

**A full hold is not a worked-out deposit.** Sites picked this session are marked so
the rover moves on rather than re-picking its best remembered one every pass, but that
mark means "do not retry right now", not "never again". A visit that brought ore back
and ended because the hold filled or the charge ran down clears it — the ore is still
in the ground and the rover is about to be empty again. A visit that produced nothing
keeps it, exactly like a failed drive. Only exhaustion is permanent, and that is
archive-backed.

## `lib/smelting.py`

Latch a recipe, feed ore in, drain metal out. Both buffers hold 50 units and
processing is asynchronous, so the loop drains before it feeds and output never blocks
production.

| Function | Purpose |
| --- | --- |
| `resolve_recipe(smelter, recipe_id)` | The recipe, or `None` with an explanation. `find_recipe()` returns `None` for unknown, locked and wrong-machine ids alike, so the message covers all three. |
| `follow_demand(smelter, recipe)` | The recipe to run this pass: what [live demand](#librecipespy) wants, when it is safe to switch, else the one already latched. |
| `latch_recipe(smelter, recipe)` | Select it, unless it is already active or a unit is mid-craft. |
| `drain(smelter, recipe, sink_bins)` | Push finished units to whichever bin is already latched to that metal, falling back to an empty one. |
| `feed(smelter, recipe, source_bins)` | Top up the input buffer from the ore bank. |
| `run(smelter, recipe_id, source_bins, sink_bins, interval=2)` | Run one smelter forever. Stops feeding while the base conserves: a smelter's draw is variable and spent only while processing, so the throttle is simply not giving it more ore. |

**`recipe_id` is a fallback, not a pin.** The active Earth Order picks the recipe, so
the machine script does not have to be hand-edited every time the board turns over. A
switch only happens with the machine stopped and both buffers empty — that is what
keeps `set_recipe()` away from `busy` and `material_mismatch` — and demand that names
nothing this machine can make leaves the current recipe alone rather than clearing it,
because a smelter with no recipe set can never be judged idle again.

## `lib/dock.py`

Supply Dock: assign an Earth Order, load it, ship it, choose the next.

The dock **pulls**. Its input is order-aware and accepts only what the active order
still needs, so the loop connects it to a crate and takes — a rover cannot push into a
dock and does not need to. Dispatch is continuous once enabled, so the script's real
work is the four transitions: pick, load, enable, and react when the order clears.

| Function | Purpose |
| --- | --- |
| `score_order(order)` | Rank open orders. Part-shipped orders win, because abandoning progress wastes it. A recipe or tech reward outranks a credit payout. |
| `matches(order, prefer)` | Whether one string names this order — its id, name, contractor id or name, or reward label, matched case-insensitively as a substring. |
| `preferred(prefer, prefer_weekly=False)` | The open order the operator asked for. Feedability is deliberately not checked: assigning an order we cannot fill *is* how the rest of the base is told to go fill it. |
| `pick_order(crates, prefer_weekly=False, prefer=None)` | The best order we can actually make progress on. A stated preference wins outright; otherwise an order we cannot feed is a dock sitting idle with an assignment. |
| `assign(dock_machine, order)` | Latch an order onto the dock, naming the reason when it refuses — loaded cargo from a previous order is physical and blocks reassignment. |
| `drain(dock_machine, crates)` | Eject every loaded slot back into local storage. `eject()`, never `flush()`: flushing destroys material the base spent ore and power making. |
| `switch(dock_machine, order, crates)` | Move an already-assigned dock onto another order. Campaign only — `.shipped` is shared and permanent, so the order left behind keeps every unit sent to it, while a weekly would lose everything at the next refresh. |
| `load(dock_machine, order, crates)` | Pull what the order still needs out of the crate bank. |
| `enable(dock_machine)` | Start the dispatcher if it is not already running. |
| `run(dock_machine, crates, prefer_weekly=False, prefer=None, interval=10)` | Serve Earth Orders forever, publishing demand every pass. |

**The three contractors expose their current orders simultaneously.** Helios Orbital,
Spire Research and Vestibule Logistics each show the first unfinished order in their
own authored queue, so a named one is available now — there is never anything to wait
out. `prefer` is what turns that into control.

**The startup line's `units/h` is an overcrowding gauge.** `dispatch_rate()` already
includes the outpost's overcrowding penalty, and the base rate is 25 with no throughput
research. Below 25 means the outpost is over its soft building threshold.

## `lib/survey.py`

Map knowledge: what the Journal and the planet already know. No vehicle in this file —
everything here reads components that answer whether or not anything is driving.

That is the point. The Journal is written by every sonar sweep any vehicle has ever
done and survives restarts, vehicle changes and save/load, so a Pioneer with no sonar
can still read ground the rovers covered. Our own `rover.sites` archive only ever held
what our two rovers surveyed while the script happened to be running.

Sites are flattened into the same JSON-safe dict shape `lib/rover.py` writes to its
archive — `id`, `name`, `x`, `y`, `kind`, plus `item_id` / `hardness` / `purity` for
minerals. One shape means the rover's scoring path takes a Journal record and one of
its own remembered records without knowing the difference.

| Function | Purpose |
| --- | --- |
| `journal()` | The Journal component, or `None` before that research. |
| `planet()` | The planet component, or `None` when it cannot be read. |
| `distance(ax, ay, bx, by)` | Straight-line meters between two world coordinates. |
| `record_of(site)` | One site of any kind as a flat, JSON-safe record. Minerals always carry the three mineral keys, `None` included: "found but not yet resolved" is a normal state. |
| `sites(kind=None)` | Every site sonar has ever classified, flattened. `[]` before the Journal research is a real answer. |
| `surveyed(kind=None)` | As `sites()`, but only the fully-resolved ones — the records with real item, hardness and purity on them. |
| `points()` | Every permanent "?" contact on the map, as flat records. Costs nothing and needs no sweep. |
| `unscanned_points()` | The contacts nobody has scanned yet — the scout's target list. |
| `bounds()` / `contains(x, y)` / `biome_at(x, y)` | Thin, `None`-safe wrappers on the planet. `contains()` answers `True` with no planet component: refusing to drive because the map cannot be read is worse than letting nav reject the target. |
| `cluster(records, radius=120)` | Group records within `radius` of each other and return each group's centroid, member count and kind mix. This is what turns "sites" into "places worth an outpost". |

## `lib/scout.py`

The Pioneer's scouting loop: rank where an outpost should go, and prove it is legal.
Built on [vehicle](#libvehiclepy) and [survey](#libsurveypy). **It builds nothing** —
there is exactly one Outpost Kit, so the choice is a conversation, not a script
decision.

Two rules shape it. The desk survey comes first: at 50 m a sweep covers almost no
ground, while `points_of_interest()` hands over real coordinates for free, so the first
shortlist is published before the Pioneer moves. And a candidate is reported only after
`plan_structure` has accepted the footprint — the clearance radius around an outpost is
not documented anywhere, so any radius modelled here would be a guess that rots.

### The rig

| Function | Purpose |
| --- | --- |
| `slots(pioneer)` | Every chassis slot, or `[]` when the rig cannot be inspected. |
| `mounted(pioneer, capability)` | `True` when a module naming that capability is mounted. Asked of the chassis, not assumed from a loadout note: both Pioneers share a `.kind` and either can be re-rigged. |
| `sonar_label(pioneer)` | Sonar range and tier as one string, or `"none"`. |
| `cargo_bins(pioneer)` | Portable bins installed across every Cargo Rack. |

### Unresolved contacts

`too_hard`, `tier_too_low` and `research_required` are not failures. Each means "the
sweep finished and there is an unresolved contact here", at a known position — the one
kind of map knowledge a hardness-1 sonar can still produce about a deposit it cannot
classify. Kept under the Data Archive key `scout.unresolved`, they make a later Wide or
Deep Sonar trip targeted instead of a re-sweep from scratch, and they count toward a
candidate's score today.

| Function | Purpose |
| --- | --- |
| `point_id(x, y)` | A stable id for a bare coordinate, `at:120:-40`. |
| `remember_unresolved(x, y, status)` | Write down a contact the sonar finished on but could not classify. |
| `unresolved()` | Every unresolved contact recorded so far. |
| `contacts()` | Every map contact worth clustering, from all three sources — Journal sites, map "?" points, our own unresolved sweeps — with the same place never counted twice. |

### Ranking and validation

| Function | Purpose |
| --- | --- |
| `home_distance(x, y)` | Meters from the docking point this Pioneer calls home. |
| `nearest_outpost(x, y)` | `(id, meters)` of the closest outpost owned. Reported, not scored against: whether a spot is too close is `plan_structure`'s answer to give. |
| `score_candidate(group)` | What is there, less how far away it is. A vent or well outweighs everything else because it is the only thing that cannot be driven to and carried home. Distance is subtracted in meters, not multiplied, so it decides between equals without swamping a better candidate. |
| `purpose(group, distance_home)` | A label from the mix — `utility`, `harvesting`, `production`, `power`. Biome is reported alongside but not scored: nothing in the manual makes output depend on it, so a weight would be invented. |
| `candidate_of(group)` | One cluster as the flat record that gets reported, members summarised so the payload publishes and archives whole. |
| `validate(candidate)` | Ask the game whether an outpost may stand here, record the verdict either way, and retract the ghost the moment it comes back `ok`. |
| `verdicts()` | The last round of placement verdicts as one phrase, so an empty shortlist says whether the map is taken or the research is missing. |
| `shortlist(radius, probe=12, limit=5)` | The ranked, validated list. No vehicle, no driving — callable before the Pioneer has moved and again after every sweep. |

### Reporting and the loop

| Function | Purpose |
| --- | --- |
| `report(candidates)` | Publish the shortlist on screen, on the `scout.candidates` bus channel, in the archive and on the map — but only when it has changed. |
| `status(pioneer, state, target=None, extra=None)` | This Pioneer's live status, delegated to `vehicle.report`. |
| `idle(pioneer, radius, passes=30)` | Wait, re-ranking now and then. Idling is free; a rebuild probes placement for real. |
| `sweep(pioneer)` | Sonar sweep from where the Pioneer is parked. Returns `(status, sites)` — unlike the rover's, this one hands the status back. |
| `resolve(pioneer, contact)` | Survey one contact of any kind. Vents and wells are exactly what make a candidate `utility` ground, and the rovers survey neither. |
| `sweep_at(pioneer, x, y)` | Drive, park, sweep, survey. `True` when a sweep happened, which is the cue that the shortlist may have moved. |
| `next_target(pioneer)` | The nearest unscanned "?" this charge can reach and return from. |
| `next_filler(pioneer, waypoints, start)` | The next ring waypoint worth driving to, and where to resume. `None` after a full turn finds nothing left — spinning the list again would cost a pass for nothing. |
| `run(pioneer, radius=120, rings=3)` | Survey, rank, validate, report. Forever, and without building. |

## `lib/pioneer.py`

The Pioneer's construction loop: drain the shared blueprint queue, forever. Built on
[vehicle](#libvehiclepy).

It has no idea what an outpost is. Every job carries its own `.required_item` and
`.required_count`, and the manual is explicit that those fields — never `.kind` — say
what to load. That one rule is the whole of the generic worker: the same loop that
founds an outpost builds pipes, power lines, bridges, drills and deconstruction jobs
with no new code. Founding is one job in the queue, not a mode.

Interruptions are normal. Stop, power loss, leaving the site, a rescue or unmounting
the Constructor Module pauses paid work without losing progress or materials, so
`paused_constructions()` is the first list read every pass, most-completed first —
that is where material already spent is sitting.

### What a job asks for

| Function | Purpose |
| --- | --- |
| `queue()` | The Construction Blueprint component, or `None` before that research. |
| `constructor(pioneer)` | The mounted Constructor Module, or `None` when the rig has no builder. |
| `needs(job)` | `(item_id, count)` this job wants in cargo, or `(None, 0)`. `None` is a real answer: deconstruction returns parts, and a paused job has its material already sunk into the site. |
| `carried(pioneer, item_id)` | Units of that item aboard, summed across every bin. |
| `have(pioneer, item_id, count)` | `True` when the hold already covers the requirement. |
| `room_for(pioneer, item_id)` | Units the installed bins can still take. Not `cargo.full()` — a bin latches to one item id, so a half-empty bin of iron ore is real space that an Outpost Kit cannot use. |

### Loading

| Function | Purpose |
| --- | --- |
| `dock_station()` | The `BuildingRef` of the charging station `HOME` resolved to. |
| `source_store()` | What to load from while parked at home. `caps.local_store()` needs something that knows its own outpost and a vehicle is exactly what does not, so the station it is docked at is asked instead — Inventory at Nocturna Base, a Warehouse or Storage Bin elsewhere. |
| `load(pioneer, item_id, count)` | Put the material aboard. Only at home, only with Auto Feeders. A shortfall is named at the pad rather than discovered in the field as `insufficient_materials` after a drive out. |

### The queue and the build

| Function | Purpose |
| --- | --- |
| `jobs()` | Every queued job worth trying, in the order to try them: paused and most-completed first, then work this Pioneer started and can rejoin, then everything still waiting. |
| `next_job(pioneer)` | The first job this charge can reach and come home from. Doubles as the departure gate's target. |
| `label(job)` / `marks(job)` | A job as one readable phrase, and the id/kind fields that ride along on the status channel. |
| `status(pioneer, state, target=None, extra=None)` | This Pioneer's live status, delegated to `vehicle.report`. States are `idle`, `loading`, `driving`, `constructing`, `returning`, `waiting_for_charge`, `blocked`, `stranded`. |
| `do_job(pioneer, job)` | Load, drive, park, build. `True` only when the job actually finished. `wrong_position` re-approaches harder — arrival tolerance means close enough, not stopped; `insufficient_materials` goes home and reloads once; transient statuses come back next pass. |

### Founding and capacity

| Function | Purpose |
| --- | --- |
| `outpost_at(x, y, tolerance=30)` | An owned outpost already standing there, or `None`. |
| `outpost_ghosts()` | Every queued outpost blueprint, at any stage of being built. |
| `found_outpost(x, y)` | Queue the outpost ghost and return its blueprint id. Planning needs no vehicle at the site. Refuses while an unbuilt outpost is queued and says nothing once one stands there, so a restart cannot spend a second kit. |
| `capacity_line()` / `watch_capacity()` | Each outpost's `buildings_used` against its soft threshold, said at startup and whenever it changes. The threshold throttles output rather than blocking deployment, which is why it is worth naming. |
| `run(pioneer, interval=10)` | Build whatever is queued, forever. A Pioneer with no Constructor Module says so and stops rather than idling on a queue it can never work. |
