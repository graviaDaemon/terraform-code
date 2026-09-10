pressure = get_component("pressure_sensor")
read_value = pressure.get_value()
value = read_value + (read_value % 2)
pressure.stabilize(value)