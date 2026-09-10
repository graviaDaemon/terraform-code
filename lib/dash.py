"""Layout and formatting for Control Room cards.

A card is a script bound to a canvas that repaints every tick:

    while True:
        panel.clear()
        ...

`panel` is a script-owner local, so it does not exist inside a library.
Every helper here therefore takes it as its first argument. That is also
why this library is called `dash` and not `panel`: a Library name is
shadowed by a local of the same name, and every card has that local.

Nothing here reads the game - `lib/readout.py` is the other half. A box
is a plain dict of x/y/w/h, so a layout can be sliced and passed around
without a class.
"""

PAD = 12
TITLE_H = 32
ROW_H = 20
GAP = 10
DOT_R = 4

TEXT = 12
SMALL = 11
TINY = 10

# draw_text is monospace and nothing clips for us: a string wider than
# its column paints straight over the next one. Advance is measured as a
# fraction of the nominal size so trim() can work in pixels.
CHAR_RATIO = 0.6

OK = "success"
WARN = "warning"
BAD = "error"
DIM = "text-muted"
SOFT = "text-secondary"
BRIGHT = "text-bright"
VALUE = "text-value"
ACCENT = "accent"

# status_dot knows exactly four words. Every state any publisher in this
# base uses has to land on one of them, so the mapping lives here rather
# than being re-guessed in five cards.
ERROR_STATES = ["stranded", "error", "stalled_no_battery", "stalled_no_oil",
                "stalled_no_route", "scrambled", "blocked"]
PAUSED_STATES = ["waiting_for_charge", "charging", "queued", "being_rescued",
                 "waiting_service", "waiting_bay", "waiting_oil", "refueling",
                 "holding_weather", "paused", "stale", "off"]
IDLE_STATES = ["idle", "", "unknown", "no recipe"]

SEVERITY_COLOR = {"error": BAD, "warning": WARN, "info": SOFT}

# An alert severity is not a machine state: "warning" is not a verb any
# publisher uses, so dot_for() would read it as "something is running"
# and paint it green. Severities get their own mapping.
SEVERITY_DOT = {"error": "error", "warning": "paused", "info": "idle"}


# --- boxes --------------------------------------------------------------

def box(x, y, w, h):
    """A rectangle as a dict. The unit every layout helper speaks in."""
    return {"x": x, "y": y, "w": w, "h": h}


def frame(panel, title):
    """Draw the card's outer frame; return the content box inside it.

    Sized from width()/height() rather than the card's nominal span, so a
    card the player set to a different size reflows instead of painting
    off the canvas.
    """
    width = panel.width()
    height = panel.height()
    panel.card(0, 0, width, height, title)
    return box(PAD, TITLE_H, width - (PAD * 2), height - TITLE_H - PAD)


def columns(area, count, gap=GAP):
    """`count` equal-width column boxes across `area`."""
    if count < 1:
        return []
    width = (area["w"] - (gap * (count - 1))) / count
    made = []
    for index in range(count):
        made.append(box(area["x"] + (index * (width + gap)),
                        area["y"], width, area["h"]))
    return made


def rows(area, height=ROW_H, gap=4, top=0):
    """Row boxes down `area` - only as many as actually fit.

    Returning the count that fits rather than one per record is what lets
    a card say "3 more" instead of painting the fourth machine over its
    own footer.
    """
    made = []
    y = area["y"] + top
    while (y + height) <= (area["y"] + area["h"]):
        made.append(box(area["x"], y, area["w"], height))
        y = y + height + gap
    return made


def inset(area, left=0, top=0, right=0, bottom=0):
    """`area` with each edge pulled in by the given amount."""
    return box(area["x"] + left, area["y"] + top,
               area["w"] - left - right, area["h"] - top - bottom)


# --- text ---------------------------------------------------------------

def text_w(text, size=TEXT):
    """Painted width of `text` in pixels."""
    return len(str(text)) * size * CHAR_RATIO


def chars_for(width, size=TEXT):
    """How many monospace characters fit in `width` pixels."""
    per = size * CHAR_RATIO
    if per <= 0:
        return 0
    return int(width / per)


def trim(text, chars):
    """`text` cut to `chars`, ending in `..` when something was cut."""
    text = str(text)
    if chars <= 0:
        return ""
    if len(text) <= chars:
        return text
    if chars <= 2:
        return text[:chars]
    return text[:chars - 2] + ".."


def fit(text, width, size=TEXT):
    """`text` trimmed to what fits in `width` pixels at `size`."""
    return trim(text, chars_for(width, size))


# --- formatting ---------------------------------------------------------

def num(value, places=1):
    """A number at fixed precision, without a pointless trailing `.0`."""
    if value is None:
        return "-"
    rounded = round(value, places)
    if rounded == int(rounded):
        return str(int(rounded))
    return str(rounded)


def pct(fraction):
    """A 0-1 fraction as a whole-number percentage."""
    if fraction is None:
        return "-"
    return str(int(round(fraction * 100))) + "%"


def pct_of_100(value):
    """A reading the game already scales 0-100, as a percentage."""
    if value is None:
        return "-"
    return str(int(round(value))) + "%"


def rate(value, unit):
    """A production rate with its unit, or `-` when there is none."""
    if value is None:
        return "-"
    return num(value, 1) + " " + unit


def wh(value):
    """Watt-hours, switching to kWh once the number stops being readable."""
    if value is None:
        return "-"
    if value >= 1000 or value <= -1000:
        return num(value / 1000, 1) + " kWh"
    return num(value, 0) + " Wh"


def watts(value):
    """Signed watts, so a grid that is losing ground reads as negative."""
    if value is None:
        return "-"
    if value > 0:
        return "+" + num(value, 0) + " W"
    return num(value, 0) + " W"


def coords(x, y):
    """A map position, rounded to whole metres."""
    if x is None or y is None:
        return "-"
    return str(int(round(x))) + ", " + str(int(round(y)))


def hhmm(hours, minutes):
    """A zero-padded wall clock."""
    text = str(int(minutes))
    if len(text) < 2:
        text = "0" + text
    return str(int(hours)) + ":" + text


# --- status -------------------------------------------------------------

def dot_for(state):
    """One of the four words status_dot understands, for any of ours."""
    if state is None:
        return "idle"
    if state in ERROR_STATES:
        return "error"
    if state in PAUSED_STATES:
        return "paused"
    if state in IDLE_STATES:
        return "idle"
    return "running"


def color_for(fraction, invert=False):
    """Traffic-light colour for a 0-1 reading, thresholded in one place.

    `invert` for readings where high is the bad end - a waste level, an
    output buffer filling up with nothing draining it.
    """
    if fraction is None:
        return DIM
    value = fraction
    if invert:
        value = 1.0 - fraction
    if value >= 0.6:
        return OK
    if value >= 0.3:
        return WARN
    return BAD


def severity_color(severity):
    """The colour token for an alert severity from `readout.alerts()`."""
    return SEVERITY_COLOR.get(severity, DIM)


def severity_dot(severity):
    """The status_dot word for an alert severity. Not the same as dot_for()."""
    return SEVERITY_DOT.get(severity, "idle")


# --- composites ---------------------------------------------------------

def heading(panel, x, y, text):
    """A section caption inside a card - an outpost name, a pillar name."""
    panel.label(x, y, text, "caption")


def note(panel, x, y, text, color=DIM, size=SMALL):
    """A quiet line of prose: a footer, a reason, a count of what is hidden."""
    panel.draw_text(x, y, str(text), size, color)


def kv(panel, x, y, key, value, color=VALUE, size=TEXT):
    """A muted key with its value after it. The pair used all over the board."""
    panel.draw_text(x, y, key, size, DIM)
    panel.draw_text(x + text_w(key, size) + 6, y, str(value), size, color)
    return x + text_w(key, size) + 6 + text_w(value, size)


def meter(panel, x, y, w, fraction, color=None, height=8):
    """A progress bar that clamps and tolerates a missing reading."""
    if fraction is None:
        fraction = 0.0
    if fraction < 0:
        fraction = 0.0
    if fraction > 1:
        fraction = 1.0
    if color is None:
        color = color_for(fraction)
    panel.progress_bar(x, y, w, height, fraction, color)


def empty(panel, area, text):
    """What a card paints instead of a blank space when it has no rows."""
    panel.draw_text(area["x"], area["y"] + 18, text, TEXT, DIM)


def overflow(panel, area, shown, total):
    """The `+N more` line, when more records exist than rows that fit."""
    if total <= shown:
        return False
    note(panel, area["x"], area["y"] + area["h"] - 2,
         "+" + str(total - shown) + " more", DIM, TINY)
    return True


# --- history ------------------------------------------------------------

def history_new(size=48):
    """A fixed-length ring buffer for spark_line.

    A factory rather than module state on purpose: a library's scope is
    shared by every script that imports it, so one buffer up here would
    have five cards interleaving their samples into a single series.
    """
    return {"size": size, "values": [], "stamp": None}


def history_push(store, value, stamp=None):
    """Append `value`, dropping the oldest once the buffer is full.

    `stamp` throttles: pass a game hour or a day number and the sample is
    taken only when it differs from the last one. A card repaints ten
    times a second, which would otherwise fill a 48-slot buffer with five
    seconds of history and call it a trend.
    """
    if value is None:
        return False
    if stamp is not None and store["stamp"] == stamp:
        return False
    store["stamp"] = stamp
    store["values"].append(value)
    while len(store["values"]) > store["size"]:
        store["values"].pop(0)
    return True


def history_values(store):
    """The samples, oldest first."""
    return store["values"]


def spark(panel, x, y, w, h, store):
    """The trend line, or a quiet note while there is not yet a trend.

    spark_line no-ops on an empty or single-value series, which would
    leave an unexplained gap on the card. Saying so is better.
    """
    values = history_values(store)
    if len(values) < 2:
        note(panel, x, y + (h / 2), "collecting history", DIM, TINY)
        return False
    panel.spark_line(x, y, w, h, values)
    return True
