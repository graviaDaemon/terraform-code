from comms import transmit_earth

reference = self.contract.earth_ref
alien_list = []

for sample in self.contract.samples:
    if sample not in reference:
        alien_list.append(sample)

transmit_earth(self.contract.id, alien_list)