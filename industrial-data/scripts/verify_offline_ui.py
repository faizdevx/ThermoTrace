"""Open the dashboard in a browser that cannot resolve any outside host and show that it still works.

    python industrial-data/scripts/verify_offline_ui.py [url] [username] [password]

Chrome/Edge is started with ``--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost"``: every name except localhost fails to
resolve. The script signs in, lets the map draw from the self-hosted PMTiles file, opens the dashboard and the admin tab, and
lists every host the page tried to reach (Network events). Exit code 1 when anything outside localhost was requested.
Screenshots: docs/images/login_page.png, offline_map.png, admin_panel.png.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_dashboard import BROWSERS, Cdp, OUT, PORT, W, H, wait  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8445/"
USER = sys.argv[2] if len(sys.argv) > 2 else "siteadmin"
PASS = sys.argv[3] if len(sys.argv) > 3 else "Site-Operator-Phrase-2026!"


def main() -> int:
    exe = next(b for b in BROWSERS if Path(b).exists())
    profile = tempfile.mkdtemp(prefix="tt-offline-")
    proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--disable-extensions", "--disable-sync", "--no-first-run",
                             "--no-default-browser-check", "--ignore-certificate-errors", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost",
                             f"--window-size={W},{H}", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hosts: Counter = Counter()
    failed: list[str] = []
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        cdp = Cdp(next(t for t in tabs if t["type"] == "page" and t["url"] in ("about:blank", ""))["webSocketDebuggerUrl"])
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        cdp.call("Network.enable")
        cdp.call("Runtime.enable")

        def pump(seconds: float) -> None:
            """Collect network events for a while (the Cdp helper only reads replies, so read raw frames here)."""
            end = time.time() + seconds
            cdp.ws.recv_timeout = None
            while time.time() < end:
                try:
                    raw = cdp.ws.recv(timeout=0.3)
                except TimeoutError:
                    continue
                msg = json.loads(raw)
                m = msg.get("method")
                if m == "Network.requestWillBeSent":
                    hosts[urlparse(msg["params"]["request"]["url"]).hostname or "?"] += 1
                elif m == "Network.loadingFailed":
                    failed.append(msg["params"].get("errorText", ""))
                elif m == "Runtime.exceptionThrown":
                    print("PAGE ERROR:", (msg["params"]["exceptionDetails"].get("exception") or {}).get("description", "")[:400])
                elif m == "Runtime.consoleAPICalled" and msg["params"].get("type") == "error":
                    print("CONSOLE ERROR:", " ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"].get("args", []))[:300])

        def shot(name: str) -> None:
            Path(OUT / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", format="png")["data"]))
            print("saved", name)

        cdp.ws.send(json.dumps({"id": 9001, "method": "Page.navigate", "params": {"url": URL}}))
        pump(6)
        def wait_text(expr: str, t: float, label: str) -> None:
            end = time.time() + t
            while time.time() < end:
                pump(1.0)
                if cdp.js(expr):
                    return
            print("TIMEOUT", label, "| page text:", " | ".join((cdp.js("document.body.innerText.slice(0, 300)") or "").splitlines()))
            raise TimeoutError(label)
        wait_text("!!document.querySelector('form[aria-label=\"Sign in\"]')", 30, "sign-in page")
        shot("login_page.png")
        cdp.js("""(() => { const set = (el, v) => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
            el.dispatchEvent(new Event('input', {bubbles: true})); };
            set(document.getElementById('login-user'), %s); set(document.getElementById('login-pass'), %s);
            document.querySelector('form[aria-label="Sign in"] button[type=submit]').click(); return 0; })()""" % (json.dumps(USER), json.dumps(PASS)))
        wait_text("/sources from|THERMAL\\s+SOURCES/i.test(document.body.innerText) && document.querySelectorAll('canvas').length > 0", 60, "map after sign-in")
        pump(5)
        for _ in range(2):
            cdp.js("(() => { document.querySelector('.leaflet-control-zoom-in').click(); return 0; })()")
            pump(1.5)
        cdp.js("(() => { document.querySelector('.leaflet-control-zoom-in').click(); document.querySelector('.leaflet-control-zoom-in').click(); return 0; })()")
        pump(6)
        shot("offline_map.png")
        cdp.js("(() => { document.querySelector('[data-testid=tab-admin]').click(); return 0; })()")
        wait_text("!!document.querySelector('[data-testid=admin-audit]') && document.querySelectorAll('[data-testid=admin-audit] tbody tr').length > 0", 30, "admin panel")
        pump(2)
        shot("admin_panel.png")
    finally:
        proc.kill()
    outside = {h: n for h, n in hosts.items() if h not in ("localhost", "127.0.0.1")}
    print("hosts requested by the page:", dict(hosts))
    print("failed requests:", Counter(failed).most_common(5))
    print("OUTSIDE REQUESTS:" if outside else "no request left localhost", outside or "")
    return 1 if outside else 0


if __name__ == "__main__":
    sys.exit(main())
