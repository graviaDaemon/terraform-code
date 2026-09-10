import dash
import readout

# Control Room card: what does Earth still want?
#
# The small card, so it shows the first Supply Dock in full and names how
# many others exist rather than cramming them. `earth.demand` is what
# makes the rovers mine iron and the smelter pick a recipe, so the number
# that matters here is what is still owed, not what has shipped.

while True:
    panel.clear()
    area = dash.frame(panel, "EARTH")
    docks = readout.orders()

    if len(docks) == 0:
        dash.empty(panel, area, "no supply dock deployed")
    else:
        dock = docks[0]
        x = area["x"]
        w = area["w"]

        panel.draw_text(x, area["y"] + 12, dash.fit(dock["order"], w, dash.TEXT),
                        dash.TEXT, dash.BRIGHT)
        subtitle = dock["contractor"]
        if subtitle == "":
            subtitle = dock["outpost"]
        dash.note(panel, x, area["y"] + 28, dash.fit(subtitle, w, dash.TINY),
                  dash.DIM, dash.TINY)

        bay = dash.box(x, area["y"] + 40, w, area["h"] - 62)
        items = dock["items"]
        if len(items) == 0:
            dash.empty(panel, bay, "dock is idle")
        else:
            slots = dash.rows(bay, 26, 2)
            shown = 0
            for slot in slots:
                if shown >= len(items):
                    break
                item = items[shown]
                done = 0.0
                if item["required"] > 0:
                    done = item["shipped"] / item["required"]
                panel.draw_text(slot["x"], slot["y"] + 9,
                                dash.fit(item["item"], slot["w"] * 0.52,
                                         dash.SMALL),
                                dash.SMALL, dash.SOFT)
                panel.draw_text(slot["x"] + (slot["w"] * 0.55), slot["y"] + 9,
                                dash.num(item["shipped"], 0) + " / "
                                + dash.num(item["required"], 0),
                                dash.SMALL, dash.VALUE)
                dash.meter(panel, slot["x"], slot["y"] + 15, slot["w"], done,
                           None, 6)
                shown = shown + 1
            dash.overflow(panel, bay, shown, len(items))

        footer = area["y"] + area["h"] - 12
        state = "paused"
        color = dash.WARN
        if dock["enabled"]:
            state = "shipping"
            color = dash.OK
        panel.status_dot(x + 4, footer - 4, dash.DOT_R, dash.dot_for(state))
        panel.draw_text(x + 16, footer, state, dash.TINY, color)
        panel.draw_text(x + (w * 0.32), footer,
                        dash.rate(dock["rate"], "u/h"), dash.TINY, dash.SOFT)
        if len(docks) > 1:
            panel.draw_text(x + (w * 0.70), footer,
                            "+" + str(len(docks) - 1) + " more docks",
                            dash.TINY, dash.DIM)
