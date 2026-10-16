"""Desktop screenshots of the national view and the dashboard tab (default: the local production stack).

    python industrial-data/scripts/capture_overview.py [url]

Saves docs/images/national_view.png (heat-density grid + industrial points), national_zoomed.png (points at zoom 8) and
dashboard_tab.png.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_dashboard import BROWSERS, Cdp, OUT, PORT, W, H, wait  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443/"


def main() -> int:
    exe = next(b for b in BROWSERS if Path(b).exists())
    profile = tempfile.mkdtemp(prefix="tt-overview-")
    proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--disable-extensions", "--disable-sync", "--no-first-run",
                             "--no-default-browser-check", "--ignore-certificate-errors", f"--window-size={W},{H}", f"--remote-debugging-port={PORT}",
                             f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        cdp = Cdp(next(t for t in tabs if t["type"] == "page" and t["url"] in ("about:blank", ""))["webSocketDebuggerUrl"])
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        cdp.call("Page.navigate", url=URL)
        wait(cdp, "document.readyState === 'complete'", 60, "page load")
        wait(cdp, "/sources from/.test(document.body.innerText) && document.querySelectorAll('canvas').length > 0", 90, "thermal data")
        time.sleep(5)

        def shot(name: str) -> None:
            Path(OUT / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", format="png")["data"]))
            print("saved", name)

        # dev builds expose the Leaflet map; production builds do not, so drive the zoom with the zoom control instead
        shot("national_view.png")
        for _ in range(2):
            cdp.js("(() => { document.querySelector('.leaflet-control-zoom-in').click(); return 0; })()")
            time.sleep(1.5)
        time.sleep(3)
        shot("national_zoomed.png")
        cdp.js("(() => { document.querySelector('[data-testid=tab-dashboard]').click(); return 0; })()")
        wait(cdp, "!!document.querySelector('[data-testid=model-card]')", 30, "dashboard")
        time.sleep(2)
        shot("dashboard_tab.png")
    finally:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
