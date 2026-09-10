"""Solar generator tracking.

    import solar
    solar.run(self)

Base power (battery budget, power mode, breakers) is owned by
lib/power.py, which runs from solar_1 and calls track_sun and
check_output itself. Every other panel runs this loop - including the
panels at another outpost, whether or not their subnet is wired home.

There is exactly one supervisor, not one per grid. power.mode,
power.budget, power.shed and the power.ledger archive key are single
global names, so a second power.run() would be a second writer on all
four (house rule 4). A subnet with no consumers has nothing to supervise
anyway: no load to measure, no breaker that would buy it anything.
"""

import caps

CLOCK_ID = "clock"
RATED_OUTPUT = 50.0          # W - denominator for the output percentage
LOW_FRACTION = 0.05
DARK = ["night", "dusk"]


def track_sun(gen, clock):
    """Tilt the panel to face the sun: 0 degrees at zenith, 90 at the horizon."""
    elevation = clock.get_elevation()
    gen.set_tilt(90 - elevation)


def check_output(gen, clock):
    """Warn when daylight output has collapsed. Dusk and night are expected."""
    if gen.get_output() / RATED_OUTPUT >= LOW_FRACTION:
        return
    if clock.get_time_of_day() in DARK:
        return
    notify(f"{gen.name}: solar output very low in daylight", "warn")


def run(gen, interval=5):
    """Track the sun forever.

    gen       the solar generator this script runs inside - pass `self`
    interval  seconds to sleep between passes
    """
    clock = get_component(CLOCK_ID)
    outpost = caps.outpost_of(gen)
    where = "unknown"
    if outpost is not None:
        where = outpost.id
    caps.report(f"Solar {gen.name} online", caps.common() + [
        ("clock", clock is not None),
        ("outpost", where),
    ])

    while True:
        track_sun(gen, clock)
        check_output(gen, clock)
        sleep(interval)
