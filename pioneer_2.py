import pioneer

# The power annex (D-020). Top power-purpose candidate on the scout's
# shortlist: the closest of the two legal coastal sites at 344 m, three
# unscanned contacts and room to grow - for solar, geology does not
# matter and every 10 m of distance is one more power-line segment to
# carry later. The centroid is written exactly as the scout validated it
# so plan_structure snaps to the same footprint anchor it probed.
OUTPOST_SITE = (-306.6666666666667, -156.66666666666666)

# The constructor rig: Nav + Constructor + Small Cargo Rack + five Small
# Battery Holders (D-016). Founding is just the first job in the shared
# blueprint queue; everything after it is the generic drain (D-014).
# The scout is pioneer_1.py and never enters this loop. See lib/pioneer.py.
pioneer.found_outpost(OUTPOST_SITE[0], OUTPOST_SITE[1])
pioneer.run(self)
