import solar

# Outpost panel. There is no second supervisor: power.run stays on
# solar_1, which reports this subnet as an unmanaged grid until the power
# line lands (D-022) and then covers it with no change here.
solar.run(self)
