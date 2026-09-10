from comms import transmit_earth

# Premise: every word appears in the grid an even number of times.
# Flip cells in reading order; the first sighting of a word is parked in
# `seen`, the second pairs with it and clears the entry, so a word that
# appears four times yields two pairs.

archive = self.contract.archive
seen = {}
pairs = []

for r in range(archive.rows):
    for c in range(archive.cols):
        word = archive.flip(r, c)
        if word not in seen:
            seen[word] = (r, c)
        else:
            (r1, c1) = seen.pop(word)
            pairs.append([r1, c1, r, c])

transmit_earth(self.contract.id, pairs)