import rover

# Leave the sink out and the rover finds every Storage Bin itself.
# Component ids are not guessable - a wrong one behaves exactly like a
# full bank - so discovery beats a hardcoded list. Falls back to base
# Inventory if no bins are deployed. See lib/rover.py.
rover.run(self)