import pioneer

# The constructor rig: Nav + Constructor + Small Cargo Rack + five Small
# Battery Holders (D-016). Founding is just the first job in the shared
# blueprint queue; everything after it is the generic drain (D-014).
#
# There is no site here any more. lib/pioneer.py picks it off the scout's
# shortlist by purpose, against pioneer.WANTED and a ledger of what it has
# already founded, and founds nothing without an Outpost Kit in Inventory
# (D-041, D-042, D-043). The scout is pioneer_1.py and never enters this
# loop. See lib/pioneer.py.
pioneer.run(self)
