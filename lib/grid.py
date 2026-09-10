"""Sector maths for the A1-H24 surface grid.

A sector name is a row letter followed by a column number: "E13".
Row index 0 is "A". Movement is cardinal only, so manhattan distance is
exact rather than estimate.
"""

ROW_ORIGIN = 65         # ord("A")
ROW_COUNT = 8           # A-H
COL_MIN = 1
COL_MAX = 24

def parse(sector):
    """"E13" -> (4, 13). Row letter first, everything after is the column."""
    return (ord(sector[0]) - ROW_ORIGIN, int(sector[1:]))


def sector_name(row_index, col) -> str:
    """(4, 13) -> "E13"."""
    return chr(ROW_ORIGIN + row_index) + str(col)


def in_bounds(row_index, col) -> bool:
    """True when (row_index, col) lands inside A1-H24."""
    if row_index < 0 or row_index >= ROW_COUNT:
        return False
    if col < COL_MIN or col > COL_MAX:
        return False
    return True


def all_sectors():
    """Every sector name in row-major order: A1, A2 ... H23, H24."""
    out = []
    for row_index in range(ROW_COUNT):
        for col in range(COL_MIN, COL_MAX + 1):
            out.append(sector_name(row_index, col))

    return out


def distance(a, b) -> int:
    """Manhattan distance in steps between two sector names.

    Exact, because diagonal moves are invalid: the rover pays one step
    per row AND one per column.
    """
    (ar, ac) = parse(a)
    (br, bc) = parse(b)
    return int(abs(ar - br) + abs(ac - bc))


def step_toward(here, goal):
    """The single adjacent sector to move to next, or None if already there.

    Columns are closed first, then rows - one axis per call, because
    diagonal moves are invalid.
    """
    (hr, hc) = parse(here)
    (gr, gc) = parse(goal)

    if hc < gc and in_bounds(hr, hc + 1):
        return sector_name(hr, hc + 1)
    if hc > gc and in_bounds(hr, hc - 1):
        return sector_name(hr, hc - 1)
    if hr < gr and in_bounds(hr + 1, hc):
        return sector_name(hr + 1, hc)
    if hr > gr and in_bounds(hr - 1, hc):
        return sector_name(hr - 1, hc)
    return None


def nearest(here, sectors):
    """The closest sector name in `sectors`, or None when it's empty.

    `sectors may be any iterable of sector names, including a dict - 
    iterating a dict yields it keys.
    """
    best = None
    best_distance = 0
    for sector in sectors:
        d = distance(here, sector)
        if best is None or d < best_distance:
            best = sector
            best_distance = d
    return best
