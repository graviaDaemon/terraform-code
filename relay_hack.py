from comms import transmit_earth

TUMBLERS = 6
VALUES = 100

lock = self.contract.lock

# Solve one tumbler at a time. Once tumbler t reads correct, template[t]
# stays True while we work on t+1, so the expected pattern grows left to
# right and each search is 100 tries rather than 100**6.
attempt = [0] * TUMBLERS
template = [False] * TUMBLERS

for t in range(TUMBLERS):
    template[t] = True
    for v in range(VALUES):
        attempt[t] = v
        if lock.intercept(attempt) == template:
            break

transmit_earth(self.contract.id, attempt)