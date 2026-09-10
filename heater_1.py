import terraform

# Learns the optimal power for each of the four thermal states once, then
# it is a lookup. With Data Archive researched the table is shared across
# every heater and survives restarts. See lib/terraform.py.
terraform.run_heater(self)