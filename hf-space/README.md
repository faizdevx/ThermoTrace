---
title: ThermoTrace
emoji: 🔥
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Industrial fires vs natural fires from NASA FIRMS, live map
---

# ThermoTrace (public read-only demo)

NASA FIRMS VIIRS heat detections, classified every 10 minutes against 12,762 mapped industrial facilities and each site's own
365-day baseline. Map, 3D globe, dashboard and alerts. Source: https://github.com/faizdevx/ThermoTrace

The Space builds the `deployment` branch of the GitHub repository and loads the demo data in `bundle/` on every start (free Spaces
have no persistent disk). It sleeps after 48 hours without visitors and takes a few minutes to wake.
