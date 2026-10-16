"""Record the 2-minute overview video: national view, a source, the alert inbox, the PDF report, the dashboard, the phone layout,
an excerpt of the replay, the accuracy slide and its limits. Screens are the real dashboard (Vite dev server -> live API);
captions are burned in; no narration.

    (frontend)  npm run dev                      # proxies /api to http://127.0.0.1:8000
    python industrial-data/scripts/record_overview_demo.py

Output: docs/demo/thermotrace_overview.mp4
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_dashboard import BROWSERS, Cdp, H, PORT, W, wait  # noqa: E402
from record_replay_demo import Recorder, card, font, wrap  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demo"
IMG = ROOT / "docs" / "images"
URL = "http://localhost:5173/"
FPS = 4.0


def captioned(jpg: bytes, text: str | None) -> bytes:
    if not text:
        return jpg
    img = Image.open(io.BytesIO(jpg)).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    f = font(26, True)
    lines = wrap(text, 78)
    h = 20 + 36 * len(lines)
    d.rectangle([0, img.height - h, img.width, img.height], fill=(15, 23, 42, 215))
    for i, ln in enumerate(lines):
        d.text((24, img.height - h + 12 + 36 * i), ln, font=f, fill=(255, 255, 255, 255))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


class Cam:
    def __init__(self, cdp: Cdp, rec: Recorder):
        self.cdp, self.rec = cdp, rec

    def hold(self, seconds: float, caption: str | None = None) -> None:
        end = time.time() + seconds
        while time.time() < end:
            data = base64.b64decode(self.cdp.call("Page.captureScreenshot", format="jpeg", quality=72)["data"])
            p = self.rec.folder / f"f{len(self.rec.frames):05d}.jpg"
            p.write_bytes(captioned(data, caption))
            self.rec.frames.append((p, 1.0 / FPS))
            time.sleep(max(0.0, 1.0 / FPS - 0.12))

    def still(self, path: Path, seconds: float, caption: str | None = None) -> None:
        img = Image.open(path).convert("RGB")
        canvas = Image.new("RGB", (W, H), (15, 23, 42))
        s = min(W / img.width, (H - 70) / img.height)
        img = img.resize((int(img.width * s), int(img.height * s)))
        canvas.paste(img, ((W - img.width) // 2, (H - 70 - img.height) // 2 + 10))
        out = io.BytesIO()
        canvas.save(out, "JPEG", quality=88)
        p = self.rec.folder / f"s{len(self.rec.frames):05d}.jpg"
        p.write_bytes(captioned(out.getvalue(), caption))
        self.rec.frames.append((p, seconds))


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="tt-overview-"))
    rec = Recorder(work)
    exe = next(b for b in BROWSERS if Path(b).exists())
    proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--disable-extensions", "--disable-sync", "--no-first-run",
                             "--no-default-browser-check", f"--window-size={W},{H}", f"--remote-debugging-port={PORT}",
                             f"--user-data-dir={work / 'profile'}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        cdp = Cdp(next(t for t in tabs if t["type"] == "page" and t["url"] in ("about:blank", ""))["webSocketDebuggerUrl"])
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        cam = Cam(cdp, rec)

        c1 = work / "c1.jpg"
        card(c1, "ThermoTrace", ["Which heat is an industrial fire?", "", "NASA FIRMS heat sources, classified against 12,762 facilities and a 365-day baseline,",
                                 "with imagery, alerts and an analyst loop.", "", "2-minute tour. Real data, real screens, no mock-ups."])
        rec.add_card(c1, 5.0)

        cdp.call("Page.navigate", url=URL)
        wait(cdp, "document.readyState === 'complete'", 60, "page load")
        wait(cdp, "/sources from/.test(document.body.innerText) && document.querySelectorAll('canvas').length > 0", 90, "thermal data")
        time.sleep(4)
        cam.hold(8, "National view: a heat-density grid for crop and forest fires, points for industrial sources. 3,073 sources, 9% industrial.")

        coal = cdp.js("""fetch('/api/thermal/sources?thermal_class=industrial_persistent&limit=5000').then(r => r.json()).then(d => {
            const f = d.features.filter(x => x.properties.baseline_known && x.properties.frp_z != null)
                .sort((a, b) => (b.properties.baseline_active_fraction * b.properties.frp_total) - (a.properties.baseline_active_fraction * a.properties.frp_total))[0];
            return f ? {lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], id: f.properties.source_id} : null; })""")
        if coal:
            cdp.js(f"(() => {{ window.__thermotraceMap.setView([{coal['lat']}, {coal['lon']}], 7, {{animate: false}}); return 0; }})()")
            cam.hold(5, "Zoom in: the grid finer, industrial sources stay as points in their class colour.")
            cdp.js(f"(() => {{ window.__thermotraceMap.setView([{coal['lat']}, {coal['lon']}], 11, {{animate: false}}); return 0; }})()")
            time.sleep(3)
            pt = cdp.js(f"""(() => {{ const m = window.__thermotraceMap; const p = m.latLngToContainerPoint([{coal['lat']}, {coal['lon']}]);
                const r = m.getContainer().getBoundingClientRect(); return {{x: r.left + p.x, y: r.top + p.y}}; }})()""")
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=pt["x"] - 6, y=pt["y"] - 6)
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=pt["x"], y=pt["y"])
            time.sleep(0.5)
            for kind in ("mousePressed", "mouseReleased"):
                cdp.call("Input.dispatchMouseEvent", type=kind, x=pt["x"], y=pt["y"], button="left", clickCount=1)
            try:
                wait(cdp, "!!document.querySelector('[data-testid=baseline-headline]')", 40, "source panel")
                cdp.js("(() => { document.querySelector('[data-testid=baseline-headline]').scrollIntoView({block: 'center'}); return 0; })()")
            except TimeoutError:
                pass
            cam.hold(8, "Click a source: class, the reasons, the model's second opinion and how it compares with its own 365-day history.")
            cdp.js("(() => { document.querySelector('button[aria-label=Close], .leaflet-popup-close-button')?.click(); return 0; })()")

        if cdp.js("!!document.querySelector('[data-testid=open-inbox]')"):
            cdp.js("(() => { window.__thermotraceMap.setView([22.5, 80.5], 5, {animate: false}); document.querySelector('[data-testid=open-inbox]').click(); return 0; })()")
            time.sleep(1.5)
            if cdp.js("!!document.querySelector('aside button[aria-expanded=false]')"):
                cdp.js("(() => { document.querySelector('aside button[aria-expanded=false]').click(); return 0; })()")
            cam.hold(8, "Alert inbox: severity, the reasons, a timeline of detections, imagery and delivery - and Confirm / Reject / Unsure for the analyst.")
            cdp.js("(() => { document.querySelector('[aria-label=\"Close inbox\"]')?.click(); return 0; })()")
        cam.still(IMG / "report_page1.png", 7.0, "One click from the alert: a PDF incident report - maps, Sentinel-2 chips, reasons, timeline.")

        cdp.js("(() => { document.querySelector('[data-testid=tab-dashboard]').click(); return 0; })()")
        wait(cdp, "!!document.querySelector('[data-testid=model-card]')", 30, "dashboard")
        time.sleep(1.5)
        cam.hold(9, "Dashboard: alerts today, class mix over time, top states, and the model card with its caveat.")

        m1, m2 = Image.open(IMG / "mobile_map.png").convert("RGB"), Image.open(IMG / "mobile_dashboard.png").convert("RGB")
        both = Image.new("RGB", (m1.width * 2 + 40, m1.height), (15, 23, 42))
        both.paste(m1, (0, 0))
        both.paste(m2, (m1.width + 40, 0))
        both.save(work / "mobile_both.png")
        cam.still(work / "mobile_both.png", 6.0, "On a phone: 390 px wide, no sideways scroll.")

        # an excerpt of the replay video: Vatva, alert, analyst decision, imagery
        clip = work / "replay_clip"
        clip.mkdir()
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "6", "-t", "30", "-i", str(OUT / "thermotrace_replay_demo.mp4"), "-vf", f"fps={FPS},scale={W}:{H}",
                        "-q:v", "4", str(clip / "r%04d.jpg")], check=True)
        frames = sorted(clip.glob("r*.jpg"))
        step = max(1, len(frames) // 70)                      # about 17 s
        for k, fpath in enumerate(frames[::step]):
            p = work / f"rp{k:04d}.jpg"
            p.write_bytes(captioned(fpath.read_bytes(), "Replay: Vatva GIDC 2020-12-09 through the live pipeline on a virtual clock. The analyst confirms; the decision lands in labels."))
            rec.frames.append((p, 1.0 / FPS))

        c2 = work / "c2.jpg"
        card(c2, "How good is it? Honest numbers", [
            "Model macro-F1 0.993 vs rules 0.809 on held-out regions and months,",
            "but the labels are rule-derived: that is agreement with the rules.",
            "Heat behaviour alone: 0.633. This part the satellite signal earns.",
            "", "34 documented industrial fires: VIIRS saw 20. Model flags all 20 at 0.25 false alarms/day, rules 16.",
            "Replayed through the live pipeline: 22 detected, only 5 raise an alert."], tone=(30, 41, 59))
        rec.add_card(c2, 10.0)
        c3 = work / "c3.jpg"
        card(c3, "What is not done", [
            "A refinery fire looks like its own flare at 375 m. A 54 MW event with no mapped facility is missed.",
            "Not hosted yet: compose, HTTPS, read-only mode and rate limits are ready and tested locally.",
            "Telegram / email delivery tested against local fakes, not a real bot.", "",
            "github.com/faizdevx/ThermoTrace  -  docs/EVALUATION.md, docs/REPLAY.md, docs/DEPLOY.md"], tone=(127, 29, 29))
        rec.add_card(c3, 10.0)

        rec.encode(OUT / "thermotrace_overview.mp4")
        print("wrote", OUT / "thermotrace_overview.mp4", f"({sum(s for _, s in rec.frames):.0f} s)")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
