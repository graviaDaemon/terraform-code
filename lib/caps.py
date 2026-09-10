"""Capability probes: what exists right now, never what a snapshot said.

Script hygiene rule (plan/03-capability-layer.md, D-005):

  - a machine script is `import X` / `X.run(self)`; libraries never touch
    `self`, the machine is passed in;
  - every id that can be discovered is discovered: bins, stations,
    stores, sibling machines - never written by hand;
  - every optional component is probed, never assumed: research, the
    Signal Bus, the Data Archive, a tier, a fluid port. Absence is a
    normal answer, and a script says once at startup what it is missing.

    import caps
    caps.report("Smelter online", caps.common() + [("tier", caps.tier(self))])
"""

RESEARCH_ID = "research"
NETWORK_ID = "outpost_network"
BUS_ID = "comms"
ARCHIVE_ID = "notebook"
FEEDERS = "research_auto_feeders"

INVENTORY = "inventory"
STORE_TYPES = ["warehouse", "large_warehouse", "storage_bin"]


def component(component_id):
    """The component, or None when it does not exist yet.

    The same call as get_component(); spelled out so a call site reads
    as a probe rather than a lookup that is expected to succeed.
    """
    return get_component(component_id)


def research(research_id) -> bool:
    """True only when `research_id` is unlocked. False without the component."""
    tree = component(RESEARCH_ID)
    if tree is None:
        return False
    return tree.is_unlocked(research_id)


def outpost_of(machine):
    """The machine's outpost ref, or None for vehicles, which have none."""
    try:
        return machine.outpost
    except Exception:
        return None


def outposts(machine=None):
    """The machine's own outpost when given, else every outpost owned."""
    if machine is not None:
        outpost = outpost_of(machine)
        if outpost is not None:
            return [outpost]
    network = component(NETWORK_ID)
    if network is None:
        return []
    return network.outposts()


def buildings(type_id=None, machine=None):
    """BuildingRefs of `type_id` (every type when None) across `outposts(machine)`.

    [] is a real answer: nothing of that type is deployed.
    """
    found = []
    for outpost in outposts(machine):
        if type_id is None:
            refs = outpost.buildings()
        else:
            refs = outpost.buildings(type_id)
        for ref in refs:
            found.append(ref)
    return found


def building_types(machine=None):
    """Distinct building type ids deployed, in discovery order."""
    seen = []
    for ref in buildings(None, machine):
        if ref.type_id not in seen:
            seen.append(ref.type_id)
    return seen


def local_store(machine):
    """A same-outpost store id for this machine's ports, or None.

    Base Inventory at the home outpost: it holds many materials at once
    and is where the shop delivers. Elsewhere a Warehouse, then a
    Storage Bin. None, with a warning, when the outpost has no store at
    all - a port connected to nothing is worse than a halted script.
    """
    outpost = outpost_of(machine)
    if outpost is not None and outpost.is_home:
        return INVENTORY
    for type_id in STORE_TYPES:
        for ref in buildings(type_id, machine):
            return ref.id
    notify(f"{machine.id}: no Storage Bin or Warehouse at its outpost and"
           f" it is not the home outpost, so there is no local store", "warn")
    return None


def tier(machine) -> int:
    """Installed Mk tier, or 1 for a machine that has no tiers."""
    try:
        return machine.tier()
    except Exception:
        return 1


def degraded(machine) -> bool:
    """True while a Mk III pack is starved of its fluid; False without tiers."""
    try:
        return machine.is_degraded()
    except Exception:
        return False


def common():
    """The three probes every long-running script cares about."""
    return [
        ("bus", component(BUS_ID) is not None),
        ("archive", component(ARCHIVE_ID) is not None),
        ("feeders", research(FEEDERS)),
    ]


def report(label, wants):
    """One startup line: `label: bus yes, archive yes, feeders no, tier 2`.

    `wants` is a list of (name, value). A boolean prints as yes/no;
    anything else prints as itself.
    """
    parts = []
    for (name, value) in wants:
        if value is True:
            parts.append(f"{name} yes")
        elif value is False:
            parts.append(f"{name} no")
        else:
            parts.append(f"{name} {value}")
    notify(f"{label}: " + ", ".join(parts))
