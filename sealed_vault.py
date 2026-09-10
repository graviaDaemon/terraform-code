from comms import transmit_earth

vault = self.contract.vault

# Opposite directions for backtracking
OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east"
}

# (row, col) deltas
OFFSETS = {
    "north": (-1,  0),
    "south": ( 1,  0),
    "east":  ( 0,  1),
    "west":  ( 0, -1)
}

# Exit is at (size-1, size-1), so bias towards it: south and east first
ORDER = ["south", "east", "north", "west"]

visited = set()     # cells I've stood in
closed = set()      # (cell, direction) edges proven blocked
stack = []          # directions taken, start -> here
key = None

while True:
    here = (vault.position.row, vault.position.col)
    visited.add(here)

    moved = False
    found = False

    for direction in ORDER:
        dr, dc = OFFSETS[direction]
        target = (here[0] + dr, here[1] + dc)

        if target in visited:
            continue
        if (here, direction) in closed:
            continue

        # move() returns an ActionResult, not a bare string. Comparing the
        # object itself to "wall"/"path"/"exit" is always False, which makes
        # every probe look like a no-op and the maze look unsolvable.
        result = vault.move(direction)

        if result.status == "wall":
            closed.add((here, direction))
        elif result.status == "path":
            stack.append(direction)
            moved = True
            break
        elif result.status == "exit":
            stack.append(direction)
            escaped = vault.escape()
            if escaped.status == "ok":
                key = escaped.key          # .key is the payload; the result is not the key
            else:
                notify(f"escape: {escaped.message}", "warn")
            found = True
            break

    if found:
        break

    if not moved:
        if not stack:
            notify("Maze exhausted -- no reachable exit", "warn")
            break
        back = stack.pop()
        retreat = vault.move(OPPOSITE[back])
        if retreat.status != "path":
            notify("Backtrack failed -- stack desynced from position", "warn")
            break

if key is not None:
    transmit_earth(self.contract.id, key)