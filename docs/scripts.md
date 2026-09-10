# Machine scripts

One script per machine or vehicle, named for the component it runs inside. Most are
two or three lines: the loop lives in a library, because every heater runs the same
loop and only the machine differs.

`self` is the machine the script runs inside. It exists only in a machine script,
never in a library, which is why every library takes the machine as an argument.

- [Terraforming](#terraforming)
- [Power](#power)
- [Vehicles](#vehicles)
- [Industry and freight](#industry-and-freight)
- [Biology](#biology)
- [Surface survey](#surface-survey)
- [Sensors and one-shots](#sensors-and-one-shots)
- [Contract puzzles](#contract-puzzles)

## Terraforming

### `heater_1.py`, `heater_2.py`, `heater_3.py`

Runs on a Heat Generator. Calls `terraform.run_heater(self)`.

Holds the heater at the power setting that reads 100% efficiency for the current
thermal state. Efficiency peaks at exactly one setting and collapses on either side,
so the script sweeps 1–10 W once per thermal state and then it is a lookup. There are
only four thermal states and each is stable for a whole game day.

The learned table is shared through the Signal Bus and persisted to the Data Archive,
so a second heater never pays for the same sweep and a restart starts from what was
already known.

Heaters are held at their optimal setting in **every** power mode. Answering conserve
mode with `set_power(0)` is what froze the Temperature research track in this save,
and that track gates every fix to the power problem itself. A heater draws at most
10 W.

### `o2gen_1.py`, `o2gen_2.py`, `o2gen_3.py`

Runs on an Oxygen Generator. Calls `terraform.run_oxygen(self)`.

Sets intake to ambient CO2 ÷ 10 on **every** pass, because the sweet spot moves as
ambient CO2 falls and a fixed intake drifts off peak and stays there. Dumps waste
inside the penalty-free 50–60 window, and dumps late with a warning past it, since a
penalised dump beats the stall at 100.

### `pressure_1.py`, `pressure_2.py`, `pressure_3.py`

Runs on a Pressure Generator. Calls `terraform.run_pressure(self)`.

Syncs once inside every resonance window. A hit is +25% efficiency, a miss is −10%,
and a sweep with no sync at all counts as a miss — so the loop tracks whether the
current sweep has been synced and announces any window it misses instead of quietly
bleeding efficiency.

## Power

### `solar_1.py`

Runs on a solar generator, and hosts the **power supervisor**. Calls
`power.run(self)`.

This is the one script that publishes `power.mode`, `power.budget` and `power.shed`,
and the only one that flips breakers. It also tracks the sun like any other panel.

It measures the night rather than guessing at it: night length, night load and day
generation are folded into a ledger in the Data Archive, and the budget it defends is
"watt-hours needed to reach dawn" times a safety factor. Below that it conserves,
well below it goes critical, and it sheds batch loads in a fixed order — charging
station, smelter, supply dock, then the biology loop. Terraforming machines, panels
and batteries are never touched.

Machines it switched off are recorded, so a restart restores them. The game does not
return a manually powered-off machine to service on its own.

It also switches off machines that have nothing to do — a smelter with no ore
anywhere, a dock with no order, the bio loop with no wanted fragments — and brings
them back when work appears.

### `solar_2.py` … `solar_5.py`

Runs on a solar generator. Calls `solar.run(self)`.

Tilts the panel to face the sun and warns when daylight output has collapsed. Base
power is supervised from `solar_1`; these panels only track.

## Vehicles

### `rover_1.py`, `rover_2.py`

Runs on a Rover. Calls `rover.run(self)`.

Prospect, mine, come home, unload, repeat. In detail:

- Home is the charging station's own docking point, resolved at startup, not (0,0).
  A rover parked three metres outside the outpost footprint is not docked and will
  never be charged.
- Waypoints lie on expanding rings spaced one sonar range apart, so consecutive
  sweeps overlap. Each rover starts at a different point on that ring, worked out
  from its own id, so two rovers do not sweep the same ground in the same order.
- Everything surveyed is written to the Data Archive, so a restart goes straight to a
  known deposit instead of re-sweeping, and either rover can work what the other
  found.
- Before driving to a deposit a rover claims it on the Signal Bus and refreshes the
  claim while drilling. The other rover skips claimed ground. A claim ages out, so a
  rover stopped mid-trip does not lock a site forever.
- Range is decided from a learned watt-hours-per-metre figure, kept deliberately
  pessimistic: being wrong high brings the rover home early, being wrong low strands
  it.
- The unload sink is discovered rather than configured. Each stack is routed
  separately, because a Storage Bin latches to one material.

Leave the arguments off — `rover.run(self)` — and everything above is automatic.

### `pioneer_1.py`

Empty. The Pioneer has a nav module and a cargo rack, but without Battery Holder
research it cannot leave the base, so there is nothing useful to automate yet.

## Industry and freight

### `charging_station_1.py`

Runs on a Vehicle Charging Station. Calls `fleet.run(self)`.

Tops up docked vehicles and dispatches the rescue drone to stranded ones. Both
commands are self-only, so this has to live on the station.

A rescue is not free: dispatching one *stops* the target so the drone can reach it,
which would halt a rover driving home under its own power. So a vehicle that reports
it is already returning is left alone unless it is below the critical floor.

While the base is conserving power the station charges only to the critical floor —
it draws bay count × bay rate the whole time anything is charging — except for a
vehicle that reports it is waiting for a charge, which is taken to the level a rover
needs before it will start a trip. Leaving a rover at the critical floor through a run
of lean days parks it permanently.

When a rover says it is waiting and nothing is charging it, the station names the
reason once: not counted as docked, or conserve mode.

### `smelter_1.py`

Runs on a Smelter. Discovers its own outpost's Storage Bins plus the local store, then
calls `smelting.run(self, ...)`.

Latches a recipe, drains finished metal before feeding new ore — a full output buffer
stalls production — and routes output to whichever bin is already latched to that
metal. While the base is conserving, it simply stops feeding: the current unit
finishes and no new ore goes in.

### `supply_dock_1.py`

Runs on a Supply Dock. Discovers its crate bank, then calls `dock.run(self, ...)`.

Picks the best open Earth Order it can actually make progress on, loads it from the
crates, and enables dispatch. The dock pulls: its input is order-aware and refuses
overshoot, so a rover never needs to push into it.

It publishes what Earth still wants on `earth.demand` every pass, which is how a rover
half a map away knows to prefer a deposit of the ore an order is waiting for.

Orders already part-shipped are preferred, because abandoning progress wastes it — and
for weekly orders it is worse than waste, since everything shipped toward an unfinished
weekly is lost when the board refreshes.

## Biology

The three machines form a loop, and each one degrades honestly when its neighbour is
not running rather than idling silently.

### `bio_collector_1.py`

Runs on a Bio Collector. Inline script.

Sweeps the scanned field pulling one fragment per node per sweep. `scan()` returns
nodes nearest-first and collecting does not deplete them, so without that limit the
collector would re-pull the closest node forever instead of working the whole field.

Its cargo slot only empties when the Bio Lab calls `take_from()`. If the Lab is not
running this loop idles forever, which is correct — so it says so out loud after a
dozen passes rather than looking broken.

### `bio_lab_1.py`

Runs on a Bio Lab. Inline script.

Takes a specimen from the collector, analyses it, checks the fragment against what
open Bio Orders still want, buys and stages the reagents its recipe needs, then
extracts. Output is drained first every pass, because a full output blocks extraction,
discarding and reagent unloading alike.

An unreadable order list and an empty one are treated differently: when the orders
cannot be read the specimen is kept, because discarding good work on a failed read is
worse than holding it.

### `bio_exchange_1.py`

Runs on a Bio Exchange. Inline script.

Picks a Bio Order it can feed from local stock, stages samples, and delivers.
Publishes the wanted fragment ids on `bio.wanted`, which is what the collector and lab
read, and what tells the power supervisor whether the bio loop has work.

Surplus returned when another Exchange completes a shared order first is drained back
to the local store at the top of each pass.

## Surface survey

### `scanner_1.py`

Runs on the surface scanner. Sweeps every sector of the A1–H24 grid, then sleeps 20
seconds and does it again.

### `harvester_1.py`

Runs on the harvester. Walks the grid collecting what the scanner found, nearest
first, and stores it.

Movement is heat-limited: an empty step costs seven heat and a step onto an item
costs one, so the script checks it can afford the next step before taking it and waits
to cool rather than overheating mid-route. Unknown cells are assumed empty, which is
the conservative guess. Cleared sectors are remembered in memory only — a reload
starts a fresh pass, which is the behaviour you want.

## Sensors and one-shots

| Script | What it does |
| --- | --- |
| `oxygen_sensor.py` | Reads the sensor and calibrates it to the scaled value. One shot. |
| `pressure_sensor.py` | Reads the gauge and stabilises to the next even value. One shot. |
| `uplink.py` | Reads the thermometer and transmits it to Earth as telemetry. |
| `planet_power.py` | `activate_power()`. |
| `planet_sensors.py` | `activate_sensors()`. |
| `boot.py` | `boot()`. |

## Contract puzzles

One-shot scripts that solve a contract and transmit the answer through
`comms.transmit_earth()`. They are here because they are part of the save, not because
they generalise.

| Script | The puzzle |
| --- | --- |
| `corrupted_archive.py` | Every word in the grid appears an even number of times. Flip in reading order, park the first sighting of a word, pair it with the second. |
| `data_tablet.py` | Probe returns a distance to the target character. Distance zero means this is the one; walk the grid and build the message. |
| `relay_hack.py` | Six tumblers, a hundred values each. Solved one tumbler at a time so the search is 100 tries per tumbler, not 100⁶. |
| `sealed_vault.py` | Walk the maze, remembering the opposite of each move so a dead end can be backtracked, and escape at the exit. |
| `terminal_breach.py` | Fifteen digits, each 1–5, with correct/misplaced feedback. Establishes a baseline from a uniform guess, then solves position by position. |
| `xenogenetics.py` | Diff the sample list against the Earth reference and transmit what is not on it. |
