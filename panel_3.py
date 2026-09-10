import dash
import readout

# Control Room card: is the planet actually changing?
#
# Three columns, one per terraforming pillar: the level now, the rate per
# hour, mean efficiency on the dial, the machines producing it, and the
# level's trend along the bottom.

HISTORY = 48

# One buffer per pillar, held by the card rather than by the library: a
# library's scope is shared by every script that imports it, so buffers
# living in `dash` would have five cards interleaving their samples.
trend = {
    "o2": dash.history_new(HISTORY),
    "pressure": dash.history_new(HISTORY),
    "heat": dash.history_new(HISTORY),
}

while True:
    panel.clear()
    area = dash.frame(panel, "TERRAFORM")
    pillars = readout.atmos()
    when = readout.clock_line()

    # Sampled once per game hour. The card repaints ten times a second,
    # which would otherwise fill the buffer with five seconds of history
    # and draw it as a trend.
    stamp = None
    if when["game_hours"] is not None:
        stamp = int(when["game_hours"])

    zones = dash.columns(area, len(pillars))
    for index in range(len(pillars)):
        pillar = pillars[index]
        zone = zones[index]
        dash.history_push(trend[pillar["key"]], pillar["level"], stamp)

        dash.heading(panel, zone["x"], zone["y"], pillar["label"])

        level = "-"
        if pillar["level"] is not None:
            level = dash.num(pillar["level"], 2)
        panel.counter(zone["x"], zone["y"] + 22, level, "level", 20)

        if pillar["level"] is None:
            dash.note(panel, zone["x"], zone["y"] + 62, "sensor not repaired",
                      dash.WARN)
        elif pillar["extra"] is not None:
            dash.note(panel, zone["x"], zone["y"] + 62,
                      dash.num(pillar["extra"], 1) + " C surface", dash.SOFT)

        dash.note(panel, zone["x"], zone["y"] + 82,
                  dash.rate(pillar["rate"], pillar["unit"]), dash.BRIGHT,
                  dash.TEXT)

        panel.gauge(zone["x"] + (zone["w"] / 2), zone["y"] + 148, 32,
                    pillar["efficiency"] / 100.0,
                    dash.pct_of_100(pillar["efficiency"]))
        dash.note(panel, zone["x"] + (zone["w"] / 2) - 24, zone["y"] + 190,
                  "efficiency", dash.DIM, dash.TINY)

        dash.heading(panel, zone["x"], zone["y"] + 212, "MACHINES")
        bay = dash.box(zone["x"], zone["y"] + 224, zone["w"], 80)
        slots = dash.rows(bay, 18, 2)
        machines = pillar["machines"]
        if len(machines) == 0:
            dash.empty(panel, bay, "none deployed")
        else:
            shown = 0
            for slot in slots:
                if shown >= len(machines):
                    break
                machine = machines[shown]
                state = "running"
                color = dash.SOFT
                if not machine["powered"]:
                    state = "off"
                    color = dash.DIM
                elif machine["degraded"]:
                    state = "degraded"
                    color = dash.WARN
                panel.status_dot(slot["x"] + 4, slot["y"] + 6, dash.DOT_R,
                                 dash.dot_for(state))
                panel.draw_text(slot["x"] + 16, slot["y"] + 10,
                                dash.fit(machine["name"], slot["w"] * 0.5,
                                         dash.SMALL),
                                dash.SMALL, color)
                panel.draw_text(slot["x"] + (slot["w"] * 0.56), slot["y"] + 10,
                                "Mk" + str(machine["tier"]) + "  "
                                + dash.pct_of_100(machine["efficiency"]),
                                dash.SMALL, dash.VALUE)
                shown = shown + 1
            dash.overflow(panel, bay, shown, len(machines))

        spark_top = zone["y"] + zone["h"] - 42
        dash.note(panel, zone["x"], spark_top - 4, "trend", dash.DIM, dash.TINY)
        dash.spark(panel, zone["x"], spark_top + 4, zone["w"], 32,
                   trend[pillar["key"]])
