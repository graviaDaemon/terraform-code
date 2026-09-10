from comms import transmit_earth

thermometer = get_component("thermometer")
value = thermometer.get_value()
print(value)

transmit_earth("current_temperature", value)