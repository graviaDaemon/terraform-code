oxygen = get_component("oxygen_sensor")
value = oxygen.get_value() * 100
oxygen.calibrate(value)
