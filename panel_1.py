notebook = get_component("notebook")
canditates = notebook.get("scout.candidates")

while True:
    panel.clear()
    panel.label(0, 22, "CANDIDATES", "caption")
    panel.status_dot(20, 50, 5, "running")
    baseX = 15
    baseY = 50
    for i in range(len(canditates)):
        panel.draw_text(baseX + (i * 3), baseY + (i * 3), canditates[i], 15)