from comms import transmit_earth
# Manhattan Distance:
# point one (x1, y1) and point two (x2, y2)
# Calculation is (x1 - x2) + (y1 - y2)
# if result is 0 (zero) then we are at the correct character

tablet = self.contract.tablet
message = ""

for row in range(tablet.rows):
    for col in range(tablet.cols):
        result = tablet.probe(row, col)
        if result.distance == 0:
            message += result.char

transmit_earth(self.contract.id, message)