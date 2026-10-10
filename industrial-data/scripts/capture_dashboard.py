"""Capture README screenshots of the running dashboard through the Chrome DevTools protocol.

Needs Edge/Chrome installed and the app served (default http://localhost:5173) with data loaded.
Waits until the thermal layer has finished loading, then saves:
  docs/images/dashboard.png      the map with the thermal layer
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

    finally:
        proc.kill()


if __name__ == "__main__":
    main()
