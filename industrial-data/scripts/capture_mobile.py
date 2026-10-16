"""Phone-sized screenshots of a running dashboard (default: the local production stack, https://localhost:8443).

    python industrial-data/scripts/capture_mobile.py [url]

Saves docs/images/mobile_map.png, mobile_dashboard.png and mobile_alerts.png (390 x 844, touch, device scale 2) and prints
whether the page scrolls sideways (it must not).
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
from capture_dashboard import BROWSERS, Cdp, OUT, PORT, wait  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443/"
W, H = 390, 844


def main() -> int:
    exe = next(b for b in BROWSERS if Path(b).exists())
    profile = tempfile.mkdtemp(prefix="tt-mobile-")
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
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=2, mobile=True)
        cdp.call("Emulation.setTouchEmulationEnabled", enabled=True)
        cdp.call("Page.navigate", url=URL)
        wait(cdp, "document.readyState === 'complete'", 60, "page load")
        wait(cdp, "/THERMAL\\s+SOURCES/i.test(document.body.innerText) && !/Loading/.test(document.body.innerText.slice(0, 600))", 90, "thermal data")
        time.sleep(4)

        def shot(name: str) -> None:
            Path(OUT / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", format="png")["data"]))
            print("saved", name)

        overflow = lambda: cdp.js("document.documentElement.scrollWidth - window.innerWidth")  # noqa: E731
        shot("mobile_map.png")
        print("horizontal overflow (px) on the map view:", overflow())
        cdp.js("(() => { document.querySelector('[data-testid=tab-dashboard]').click(); return 0; })()")
        wait(cdp, "!!document.querySelector('[data-testid=dashboard]')", 30, "dashboard")
        time.sleep(1.5)
        shot("mobile_dashboard.png")
        print("horizontal overflow (px) on the dashboard:", overflow())
        cdp.js("(() => { document.querySelector('[data-testid=tab-map]').click(); return 0; })()")
        time.sleep(1)
        if cdp.js("!!document.querySelector('[data-testid=open-inbox]')"):
            cdp.js("(() => { document.querySelector('[data-testid=open-inbox]').click(); return 0; })()")
            wait(cdp, "!!document.querySelector('aside button[aria-expanded]')", 30, "inbox")
            cdp.js("(() => { document.querySelector('aside button[aria-expanded=false]').click(); return 0; })()")
            time.sleep(2)
            shot("mobile_alerts.png")
            print("horizontal overflow (px) with the inbox open:", overflow())
    finally:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
