import dash
import readout

# Control Room card: is anything wrong right now?
#
# Left is the world clock, middle the power budget, right the alert stack.
# An empty right-hand column is the point of the card - it means nothing
# needs the operator.

MODE_COLOR = {"normal": dash.OK, "conserve": dash.WARN, "critical": dash.BAD}

while True:
    panel.clear()
    area = dash.frame(panel, "STATUS")
    when = readout.clock_line()
    supply = readout.power()
    warnings = readout.alerts()

    left = dash.box(area["x"], area["y"], area["w"] * 0.22, area["h"])
    middle = dash.box(area["x"] + (area["w"] * 0.25), area["y"],
                      area["w"] * 0.29, area["h"])
    right = dash.box(area["x"] + (area["w"] * 0.56), area["y"],
                     area["w"] * 0.44, area["h"])

    # --- when it is
    day = "-"
    if when["day"] is not None:
        day = when["day"]
    panel.counter(left["x"], left["y"] + 20, day, "day", 24)
    dash.note(panel, left["x"], left["y"] + 74,
              dash.hhmm(when["hh"], when["mm"]), dash.BRIGHT, dash.TEXT)
    dash.note(panel, left["x"], left["y"] + 92, when["phase"], dash.SOFT)
    panel.gauge(left["x"] + left["w"] - 32, left["y"] + 58, 26,
                when["elevation"] / 90.0, str(int(round(when["elevation"]))))
    dash.note(panel, left["x"] + left["w"] - 46, left["y"] + 96, "sun", dash.DIM,
              dash.TINY)

    # --- power
    dash.heading(panel, middle["x"], middle["y"], "POWER")
    if not supply["supervised"]:
        # No budget on the bus means the supervisor is not running. Its
        # shed record is empty either way, so saying "nothing shed" here
        # would be a guess dressed up as a reading.
        panel.pill(middle["x"], middle["y"] + 14, "supervisor silent", dash.BAD)
        dash.note(panel, middle["x"], middle["y"] + 50,
                  "solar_1 is not publishing", dash.DIM)
    else:
        mode = supply["mode"]
        if mode is None:
            mode = "unknown"
        panel.pill(middle["x"], middle["y"] + 14, mode,
                   MODE_COLOR.get(mode, dash.DIM))
        dash.note(panel, middle["x"], middle["y"] + 46, "stored")
        dash.meter(panel, middle["x"], middle["y"] + 58, middle["w"] - 10,
                   supply["level"])
        dash.note(panel, middle["x"], middle["y"] + 82,
                  dash.wh(supply["stored"]) + " of " + dash.wh(supply["capacity"]),
                  dash.SOFT)
        net_color = dash.OK
        if supply["net"] is not None and supply["net"] < 0:
            net_color = dash.WARN
        dash.kv(panel, middle["x"], middle["y"] + 102, "net",
                dash.watts(supply["net"]), net_color)
        if supply["hours_to_dawn"] is not None:
            dash.kv(panel, middle["x"], middle["y"] + 120, "dawn in",
                    dash.num(supply["hours_to_dawn"], 1) + " h", dash.SOFT)

    # --- what wants attention
    dash.heading(panel, right["x"], right["y"], "ALERTS")
    slots = dash.rows(dash.inset(right, 0, 16, 0, 14), 18, 2)
    if len(warnings) == 0:
        dash.note(panel, right["x"], right["y"] + 34, "all clear", dash.OK,
                  dash.TEXT)
    else:
        shown = 0
        for slot in slots:
            if shown >= len(warnings):
                break
            item = warnings[shown]
            panel.status_dot(slot["x"] + 4, slot["y"] + 6, dash.DOT_R,
                             dash.severity_dot(item["severity"]))
            panel.draw_text(slot["x"] + 16, slot["y"] + 10,
                            dash.fit(item["text"], slot["w"] - 20, dash.SMALL),
                            dash.SMALL, dash.severity_color(item["severity"]))
            shown = shown + 1
        dash.overflow(panel, right, shown, len(warnings))
