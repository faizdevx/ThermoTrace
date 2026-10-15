"""Capture README screenshots of the running dashboard through the Chrome DevTools protocol.

Needs Edge/Chrome installed and the app served (default http://localhost:5173) with data loaded.
Waits until the thermal layer has finished loading, then saves:
  docs/images/dashboard.png             the map with the thermal layer + merged facilities
  docs/images/facility_provenance.png   a multi-source power plant: OSM outline + every source record + licence
  docs/images/source_baseline.png       a persistent industrial source compared with its own 365-day history
(the provenance shot needs the dev server: it drives the Leaflet map through window.__thermotraceMap)
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images"
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173/"
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
PORT = 9333
W, H = 1500, 900


class Cdp:
    def __init__(self, ws_url):
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024)
        self.n = 0

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r["result"].get("value")

    def shot(self, path):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        Path(path).write_bytes(base64.b64decode(data))


def wait(cdp, expr, timeout=90, label=""):
    end = time.time() + timeout
    while time.time() < end:
        if cdp.js(expr):
            return
        time.sleep(1)
    try:
        cdp.shot(OUT / "_timeout_debug.png")     # what the page looked like (not committed)
    except Exception:  # noqa: BLE001
        pass
    raise TimeoutError(f"timed out waiting for {label or expr}")


def main():
    exe = next((b for b in BROWSERS if Path(b).exists()), None)
    if not exe:
        raise SystemExit("no Edge/Chrome found")
    profile = tempfile.mkdtemp(prefix="tt-shot-")
    proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{H}",
                             f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        page = next(t for t in tabs if t["type"] == "page")
        cdp = Cdp(page["webSocketDebuggerUrl"])
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        cdp.call("Page.navigate", url=URL)
        wait(cdp, "document.readyState === 'complete'", 60, "page load")
        wait(cdp, "!!document.getElementById('thermal-controls') && !/Loading/.test(document.getElementById('thermal-controls').innerText)"
                  " && /sources from/.test(document.getElementById('thermal-controls').innerText)", 120, "thermal data")
        wait(cdp, "!/Loading/.test(document.body.innerText.slice(0, 3000)) && document.querySelectorAll('canvas').length > 0", 60, "map")
        time.sleep(4)                       # tiles + marker paint
        OUT.mkdir(parents=True, exist_ok=True)
        cdp.shot(OUT / "dashboard.png")
        print("saved dashboard.png")

        # --- facility provenance: biggest plant that has an OSM outline and 2+ sources -------------------
        plant = cdp.js("""fetch('/api/facilities?kind=power_thermal&limit=20000').then(r => r.json()).then(d => {
            const f = d.features.filter(x => x.properties.source_count > 1 && x.properties.has_footprint)
                .sort((a, b) => (b.properties.capacity_mw || 0) - (a.properties.capacity_mw || 0))[0];
            return f ? {lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], name: f.properties.name} : null; })""")
        if plant:
            cdp.js("(() => { [...document.querySelectorAll('#thermal-controls label')].find(l => /Show thermal layer/.test(l.innerText))?.querySelector('input')?.click(); return 0; })()")   # thermal markers would win the click
            time.sleep(1)
            cdp.js(f"(() => {{ document.getElementById('toggle-states')?.click(); "
                   f"window.__thermotraceMap.setView([{plant['lat']}, {plant['lon']}], 12, {{animate: false}}); }})()")
            time.sleep(4)
            pt = cdp.js(f"""(() => {{ const m = window.__thermotraceMap; const p = m.latLngToContainerPoint([{plant['lat']}, {plant['lon']}]);
                const r = m.getContainer().getBoundingClientRect(); return {{x: r.left + p.x, y: r.top + p.y}}; }})()""")
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=pt["x"] - 6, y=pt["y"] - 6)
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=pt["x"], y=pt["y"])     # canvas hit-test needs hover
            time.sleep(0.5)
            for kind in ("mousePressed", "mouseReleased"):
                cdp.call("Input.dispatchMouseEvent", type=kind, x=pt["x"], y=pt["y"], button="left", clickCount=1)
            wait(cdp, "!!document.querySelector('.leaflet-popup-content') && /Sources \(/.test(document.querySelector('.leaflet-popup-content').innerText)",
                 30, "facility popup")
            time.sleep(2)
            cdp.shot(OUT / "facility_provenance.png")
            print("saved facility_provenance.png for", plant["name"])
            cdp.js("(() => { [...document.querySelectorAll('#thermal-controls label')].find(l => /Show thermal layer/.test(l.innerText))?.querySelector('input')?.click(); return 0; })()")   # thermal layer back on
            time.sleep(2)


        # --- baseline: a persistent industrial source with a known history and a clearly non-zero z-score -----
        src = cdp.js("""fetch('/api/thermal/sources?thermal_class=industrial_persistent&limit=5000').then(r => r.json()).then(d => {
            const f = d.features.filter(x => x.properties.baseline_known && x.properties.frp_z != null)
                .sort((a, b) => (b.properties.baseline_active_fraction * (b.properties.frp_total)) -
                                (a.properties.baseline_active_fraction * (a.properties.frp_total)))[0];
            return f ? {lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], id: f.properties.source_id,
                        name: f.properties.site_name} : null; })""")
        if src:
            cdp.js("(() => { document.querySelector('.leaflet-popup-close-button')?.click(); return 0; })()")
            cdp.js(f"(() => {{ window.__thermotraceMap.setView([{src['lat']}, {src['lon']}], 11, {{animate: false}}); return 0; }})()")
            time.sleep(4)
            pt = cdp.js(f"""(() => {{ const m = window.__thermotraceMap; const p = m.latLngToContainerPoint([{src['lat']}, {src['lon']}]);
                const r = m.getContainer().getBoundingClientRect(); return {{x: r.left + p.x, y: r.top + p.y}}; }})()""")
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=pt["x"] - 6, y=pt["y"] - 6)
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=pt["x"], y=pt["y"])
            time.sleep(0.5)
            for kind in ("mousePressed", "mouseReleased"):
                cdp.call("Input.dispatchMouseEvent", type=kind, x=pt["x"], y=pt["y"], button="left", clickCount=1)
            wait(cdp, "!!document.querySelector('[data-testid=baseline-headline]')", 40, "baseline row")
            cdp.js("(() => { document.querySelector('[data-testid=baseline-headline]').scrollIntoView({block: 'center'}); return 0; })()")
            time.sleep(2)
            cdp.shot(OUT / "source_baseline.png")
            print("saved source_baseline.png for", src["name"] or src["id"])

    finally:
        proc.kill()


if __name__ == "__main__":
    main()
