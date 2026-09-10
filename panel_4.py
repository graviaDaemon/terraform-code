import dash
import readout

# Control Room card: is the base making things, and how fast?
#
# One row per production machine, under an outpost heading. The heading
# is there even while there is only one outpost, because the day smelting
# moves to its own base this card grows a second group and needs no edit.
#
# Rate and duty are always shown together. The rate is the recipe's
# nameplate - what the machine would do if it never ran dry - and duty is
# the share of the last few minutes it was actually running. A smelter
# starved of ore reports a full rate at near-zero duty.

HEAD_H = 16
ROW_H = 22
GAP = 3

STATE_COLOR = {"producing": dash.OK, "idle": dash.WARN,
               "no recipe": dash.DIM, "off": dash.DIM}

while True:
    panel.clear()
    area = dash.frame(panel, "PRODUCTION")
    machines = readout.production()

    if len(machines) == 0:
        dash.empty(panel, area, "no production machines deployed")
    else:
        # Flattened into one paint list so a heading and a machine row
        # share a single cursor, and the card can count what it dropped.
        items = []
        group = None
        for row in machines:
            if row["outpost"] != group:
                group = row["outpost"]
                items.append({"head": group, "row": None})
            items.append({"head": None, "row": row})

        x = area["x"]
        w = area["w"]
        y = area["y"]
        bottom = area["y"] + area["h"]
        painted = 0

        for item in items:
            if item["head"] is not None:
                # A heading with no room for a row under it is worse than
                # no heading, so it is only painted when one will follow.
                if (y + HEAD_H + GAP + ROW_H) > bottom:
                    break
                dash.heading(panel, x, y + 10, item["head"])
                y = y + HEAD_H + GAP
                continue

            if (y + ROW_H) > bottom:
                break
            row = item["row"]

            panel.status_dot(x + 4, y + 9, dash.DOT_R, dash.dot_for(row["state"]))
            panel.draw_text(x + 16, y + 14,
                            dash.fit(row["name"], w * 0.13, dash.SMALL),
                            dash.SMALL, dash.BRIGHT)
            panel.draw_text(x + (w * 0.17), y + 14,
                            dash.fit(row["recipe_name"], w * 0.15, dash.SMALL),
                            dash.SMALL, dash.SOFT)
            panel.draw_text(x + (w * 0.34), y + 14,
                            dash.fit(row["state"], w * 0.09, dash.SMALL),
                            dash.SMALL, STATE_COLOR.get(row["state"], dash.SOFT))
            dash.meter(panel, x + (w * 0.45), y + 8, w * 0.12, row["progress"],
                       dash.ACCENT, 7)
            panel.draw_text(x + (w * 0.60), y + 14,
                            dash.rate(row["rate"], "u/h"), dash.SMALL, dash.VALUE)

            duty_color = dash.DIM
            if row["duty"] is not None:
                duty_color = dash.color_for(row["duty"])
            panel.draw_text(x + (w * 0.72), y + 14, "duty " + dash.pct(row["duty"]),
                            dash.SMALL, duty_color)

            buffers = "in " + dash.num(row["input"]["used"], 0)
            if row["input"]["capacity"] is not None:
                buffers = buffers + "/" + dash.num(row["input"]["capacity"], 0)
            buffers = buffers + "  out " + dash.num(row["output"], 0)
            panel.draw_text(x + (w * 0.83), y + 14,
                            dash.fit(buffers, w * 0.17, dash.SMALL),
                            dash.SMALL, dash.DIM)

            y = y + ROW_H + GAP
            painted = painted + 1

        dash.overflow(panel, area, painted, len(machines))
