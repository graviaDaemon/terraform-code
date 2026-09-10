from comms import transmit_earth

lock = self.contract.terminal
result = None

#------------------------------------------
# digit:    01,02,03,04,05,06,07,08,09,10,11,12,13,14,15
# Digits must be between 1 and 5
# Responses are { correct: int, misplaced: int }

template = [ 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
baseline_guess = lock.guess(template).correct

for digit in range(len(template)):
    for guess in range(1, 6):
        template[digit] = guess
        res = lock.guess(template)
        if res.correct > baseline_guess:
            # this position's digit is correct
            baseline_guess = res.correct
            break
        elif res.correct < baseline_guess:
            # this position's digit was the previous digit
            template[digit] = guess - 1
            break
        # otherwise we just continue with the next digit in the list of 5 digits
        

result = template
#------------------------------------------

transmit_earth(self.contract.id, result)