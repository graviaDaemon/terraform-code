import power

# This panel hosts the power supervisor: it tracks the sun like the others
# and is the ONE script that publishes power.mode / power.budget /
# power.shed and flips breakers. See lib/power.py.
power.run(self)
