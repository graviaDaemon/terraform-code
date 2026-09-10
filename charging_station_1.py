import fleet

# Tops up docked vehicles and dispatches the rescue drone to stranded ones.
# charge() and dispatch_rescue() are SELF ONLY, so this must run on the
# station itself. Respects the "power.mode" broadcast, since a charging
# station draws bay_count x bay_rate from the grid. See lib/fleet.py.
fleet.run(self)