import dash
import readout

# Control Room card: where is everyone and what are they doing?
#
# The engine's own `fleet.vehicles()` is the hard fact - position,
# battery, docked, stranded. The Signal Bus adds the intent only that
# vehicle's script knows: why it is idle, where it is headed, what it is
# carrying. A vehicle whose channel has aged out keeps the engine's word
# and is marked `stale`; it is never shown a stale verb, because a script
# that stopped and a rover that is genuinely idle look identical
# otherwise.

ROW_H = 24
GAP = 3

KIND_COLOR = {"rover": dash.ACCENT, "pioneer": dash.OK}

while True:
    panel.clear()
    area = dash.frame(panel, "FLEET")
    crew = readout.vehicles()

    if len(crew) == 0:
        dash.empty(panel, area, "no vehicles owned")
    else:
        x = area["x"]
        w = area["w"]
        slots = dash.rows(area, ROW_H, GAP)
        shown = 0

        for slot in slots:
            if shown >= len(crew):
                break
            unit = crew[shown]
            y = slot["y"]

            panel.status_dot(x + 4, y + 10, dash.DOT_R, dash.dot_for(unit["state"]))
            panel.draw_text(x + 16, y + 15,
                            dash.fit(unit["name"], w * 0.11, dash.SMALL),
                            dash.SMALL, dash.BRIGHT)
            panel.pill(x + (w * 0.14), y + 3, unit["kind"],
                       KIND_COLOR.get(unit["kind"], dash.DIM))

            state_color = dash.SOFT
            if unit["stale"]:
                state_color = dash.DIM
            panel.draw_text(x + (w * 0.24), y + 15,
                            dash.fit(unit["state"], w * 0.13, dash.SMALL),
                            dash.SMALL, state_color)

            target = unit["target"]
            if target is None:
                target = "-"
            panel.draw_text(x + (w * 0.38), y + 15,
                            dash.fit("to " + str(target), w * 0.15, dash.SMALL),
                            dash.SMALL, dash.DIM)

            dash.meter(panel, x + (w * 0.54), y + 9, w * 0.11, unit["battery"],
                       None, 7)
            panel.draw_text(x + (w * 0.66), y + 15, dash.pct(unit["battery"]),
                            dash.SMALL, dash.color_for(unit["battery"]))

            # Only a rover carries cargo and only a Pioneer carries a job,
            # so whichever the bus supplied is the one worth the column.
            load = ""
            if unit["cargo"] is not None:
                load = "cargo " + dash.num(unit["cargo"], 0)
            elif unit["job"] is not None:
                load = str(unit["job"])
            panel.draw_text(x + (w * 0.72), y + 15,
                            dash.fit(load, w * 0.11, dash.SMALL),
                            dash.SMALL, dash.VALUE)

            where = dash.coords(unit["x"], unit["y"])
            if unit["docked"]:
                where = "docked"
            panel.draw_text(x + (w * 0.84), y + 15,
                            dash.fit(where, w * 0.10, dash.SMALL),
                            dash.SMALL, dash.SOFT)

            if unit["stale"]:
                panel.draw_text(x + (w * 0.95), y + 15, "stale", dash.TINY,
                                dash.WARN)

            shown = shown + 1

        dash.overflow(panel, area, shown, len(crew))
