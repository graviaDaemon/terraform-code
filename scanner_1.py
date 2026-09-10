import grid

SWEEP_INTERVAL = 20        # seconds between full sweeps

while True:
    sleep(SWEEP_INTERVAL)
    for sector in grid.all_sectors():
        self.scan(sector)