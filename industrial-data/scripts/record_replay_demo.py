"""Record the replay demo video: the real dashboard (Vite dev server -> replay API) while each scenario replays.

Prerequisites (see docs/REPLAY.md):
  * replay database + API:   python -m src.thermal.cli replay --init-db
                             ANALYST_TOKEN=... docker compose --profile replay up -d api-replay
  * dashboard on the replay API:  VITE_PROXY_TARGET=http://127.0.0.1:8001 npm run dev      (in industrial-gis/frontend)
  * Edge or Chrome, ffmpeg on PATH.

    python industrial-data/scripts/record_replay_demo.py [--speed=auto|600x] [scenario-id ...]   (auto: each scenario plays in about 25 s)

Each scenario: title card -> dashboard frames while the replay runs (the banner shows the virtual clock) -> alert inbox
and the source panel with imagery -> result card with the verdict from the replay report. Output:
docs/demo/thermotrace_replay_demo.mp4 (+ one poster frame per scenario).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_dashboard import BROWSERS, Cdp, H, PORT, W, wait  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demo"
URL = os.getenv("REPLAY_UI_URL", "http://localhost:5173/")
FPS = 4.0


def font(size: int, bold: bool = False):
    for name in ("segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def card(path: Path, title: str, lines: list[str], tone=(15, 23, 42)) -> None:
    img = Image.new("RGB", (W, H), tone)
    d = ImageDraw.Draw(img)
    size = 46 if len(title) <= 52 else 34 if len(title) <= 72 else 28       # long scenario titles must fit the card
    d.text((80, 120), title, font=font(size, True), fill=(255, 255, 255))
    y = 230
    for ln in lines:
        d.text((80, y), ln, font=font(28), fill=(203, 213, 225))
        y += 46
    d.text((80, H - 70), "ThermoTrace replay - archived NASA FIRMS data through the live pipeline", font=font(20), fill=(100, 116, 139))
    img.save(path, quality=90)          # JPEG like the screenshots: ffmpeg's concat demuxer needs one codec


def wrap(text: str, width: int = 86) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])


class Recorder:
    def __init__(self, folder: Path):
        self.folder = folder
        self.frames: list[tuple[Path, float]] = []   # (file, seconds on screen)

    def add_card(self, path: Path, seconds: float) -> None:
        self.frames.append((path, seconds))

    def add_shot(self, cdp: Cdp, seconds: float | None = None, last=[None]) -> None:
        now = time.time()
        data = cdp.call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
        p = self.folder / f"f{len(self.frames):05d}.jpg"
        p.write_bytes(base64.b64decode(data))
        if seconds is None:                            # fixed 4 fps: a slow stretch of computing is shown fast-forwarded
            seconds = 1.0 / FPS
        last[0] = now
        self.frames.append((p, seconds))

    def compress(self, start: int, max_frames: int = 120) -> None:
        """Time-lapse the live segment [start:] to at most ``max_frames`` frames (the last 8 are always kept)."""
        seg = self.frames[start:]
        if len(seg) <= max_frames:
            return
        keep = max_frames - 8
        idx = sorted({round(i * (len(seg) - 8 - 1) / (keep - 1)) for i in range(keep)} | set(range(len(seg) - 8, len(seg))))
        self.frames[start:] = [seg[i] for i in idx]

    def encode(self, out: Path) -> None:
        lst = self.folder / "frames.txt"
        with lst.open("w", encoding="utf-8") as f:
            for p, sec in self.frames:
                f.write(f"file '{p.as_posix()}'\nduration {sec:.3f}\n")
            f.write(f"file '{self.frames[-1][0].as_posix()}'\n")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
                        "scale=1280:-2,format=yuv420p", "-r", "10", "-c:v", "libx264", "-crf", "26", "-preset", "medium", str(out)], check=True)


def scenario_view(sc) -> tuple[float, float, int]:
    if sc.focus:
        return sc.focus[0], sc.focus[1], 12
    x0, y0, x1, y1 = sc.bbox
    return (y0 + y1) / 2, (x0 + x1) / 2, 8


def reencode(folder: Path) -> None:
    """Rebuild the video from a work folder of an earlier run (frames.txt), time-lapsing each long live segment."""
    rec = Recorder(folder)
    items = []
    cur = None
    for line in (folder / "frames.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("file "):
            cur = Path(line[6:-1])
        elif line.startswith("duration ") and cur is not None:
            items.append((cur, float(line.split()[1])))
    start = None
    for i, (p, d) in enumerate(items):
        name = p.name
        if name.startswith("intro"):
            if start is not None:
                rec.frames = items[:0] + rec.frames
            rec.frames.append((p, d))
            start = len(rec.frames)
            continue
        rec.frames.append((p, d))
        if name.startswith("result") or d == 0.3:           # end of the live segment: inbox frames / result card
            if start is not None:
                live_end = len(rec.frames) - 1
                seg = rec.frames[start:live_end]
                tmp = Recorder(folder)
                tmp.frames = seg
                tmp.compress(0)
                rec.frames[start:live_end] = tmp.frames
            start = None
    rec.encode(OUT / "thermotrace_replay_demo.mp4")
    print("wrote", OUT / "thermotrace_replay_demo.mp4", f"({sum(s for _, s in rec.frames):.0f} s)")


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--reencode":
        reencode(Path(sys.argv[2]))
        return
    sys.path.insert(0, str(ROOT / "industrial-data"))
    from src.thermal import replay as R

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    speed_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--speed=")), "auto")
    target_seconds = 25.0
    scenarios = R.load_scenarios()
    chosen = [scenarios[i] for i in (args or ["vatva-gidc", "barauni-refinery", "amudan-chemicals", "nahorkatiya-flare", "punjab-burning-day"])]
    live = os.getenv("DATABASE_URL")
    dsn = R.replay_dsn(live) if live else None
    analyst_token = os.getenv("REPLAY_ANALYST_TOKEN", "")      # the replay API's ANALYST_TOKEN: lets the video show a decision
    work = Path(tempfile.mkdtemp(prefix="tt-demo-"))
    rec = Recorder(work)
    OUT.mkdir(parents=True, exist_ok=True)

    exe = next(b for b in BROWSERS if Path(b).exists())
    proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--disable-extensions", "--disable-sync", "--no-first-run", "--no-default-browser-check", f"--window-size={W},{H}",
                             f"--remote-debugging-port={PORT}", f"--user-data-dir={work / 'profile'}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        cdp = Cdp(next(t for t in tabs if t["type"] == "page" and (t["url"] in ("about:blank", "") or t["url"].startswith("http://localhost")))["webSocketDebuggerUrl"])
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        for n, sc in enumerate(chosen, 1):
            days = (R.clock_range(sc)[1] - R.clock_range(sc)[0]).total_seconds() / 86400
            speed = f"{days * 86400 / target_seconds:.0f}x" if speed_arg == "auto" else speed_arg      # auto: ~25 s per scenario
            print(f"[{n}/{len(chosen)}] {sc.id} at {speed}", flush=True)
            exp = sc.expect or {}
            want = ("an industrial class and an alert" if exp.get("alert") else
                    "the right class and NO industrial alert (a flare / crop fire is not an industrial fire)")
            intro = work / f"intro{n}.jpg"
            card(intro, sc.title, wrap(sc.note or "") + [""] + wrap(f"Replay {sc.start} to {sc.end} at {speed} of virtual time per second. "
                 f"Detections become visible 3 h after the overpass, like the live feed. Expected: {want}.") +
                 ["", "The baseline uses only detections before the replay starts."])
            rec.add_card(intro, 5.0)
            if dsn:                                             # an empty replay database and no clock: nothing of the previous scenario on screen
                R.reset_db(dsn)
                (R.DATA_ROOT / "thermal" / "replay" / "status.json").unlink(missing_ok=True)
            cdp.call("Page.navigate", url=URL)
            wait(cdp, "document.readyState === 'complete'", 60, "page load")
            time.sleep(3)
            lat, lon, zoom = scenario_view(sc)
            cdp.js(f"(() => {{ window.__thermotraceMap?.setView([{lat}, {lon}], {zoom}, {{animate: false}}); return 0; }})()")
            report = work / f"{sc.id}.json"
            env = {**os.environ, "PYTHONPATH": str(ROOT / "industrial-data"), "PYTHONIOENCODING": "utf-8"}
            job = subprocess.Popen([sys.executable, "-m", "src.thermal.cli", "replay", "--scenario", sc.id, "--speed", speed,
                                    "--report", str(report)], cwd=ROOT, env=env, stdout=open(work / f"{sc.id}.log", "w"), stderr=subprocess.STDOUT)
            live_start = len(rec.frames)
            t0 = time.time()
            while job.poll() is None and time.time() - t0 < 2700:
                rec.add_shot(cdp)
                time.sleep(0.15)
            for _ in range(8):                                   # the last tick's SSE refetch lands
                rec.add_shot(cdp)
                time.sleep(0.25)
            rec.compress(live_start)
            poster = OUT / f"{sc.id}.png"
            Path(poster).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", format="png")["data"]))
            rep = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
            if rep.get("alerts") and cdp.js("!!document.querySelector('[data-testid=open-inbox]')"):
                cdp.js("(() => { document.querySelector('[data-testid=open-inbox]').click(); return 0; })()")
                time.sleep(1.0)
                if cdp.js("!!document.querySelector('aside button[aria-expanded=false]')"):
                    cdp.js("(() => { document.querySelector('aside button[aria-expanded=false]').click(); return 0; })()")
                    time.sleep(1.5)
                for _ in range(10):
                    rec.add_shot(cdp, 0.3)
                    time.sleep(0.1)
                if analyst_token and n == 1:                     # close the loop on camera: the analyst confirms the first alert
                    fill = """(() => { const set = (el, v) => { Object.getOwnPropertyDescriptor(el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(el, v);
                        el.dispatchEvent(new Event('input', {bubbles: true})); };
                        set(document.querySelector('input[aria-label="Analyst name"]'), 'analyst-1');
                        set(document.querySelector('input[aria-label="Analyst token"]'), %s);
                        set(document.querySelector('textarea[aria-label="Note"]'), 'Replay demo: strong night-time SWIR signature at the estate, confirmed as industrial fire.');
                        return 0; })()""" % json.dumps(analyst_token)
                    cdp.js(fill)
                    for _ in range(6):
                        rec.add_shot(cdp, 0.3)
                        time.sleep(0.1)
                    cdp.js("(() => { [...document.querySelectorAll('aside button')].find(b => /Confirm fire/.test(b.innerText))?.click(); return 0; })()")
                    time.sleep(1.5)
                    for _ in range(14):
                        rec.add_shot(cdp, 0.3)
                        time.sleep(0.1)
                cdp.js("(() => { [...document.querySelectorAll('aside button')].find(b => /Show on map/.test(b.innerText))?.click(); return 0; })()")
                try:
                    wait(cdp, "!!document.querySelector('[data-testid=imagery-panels]') || /Satellite imagery service unavailable|No Sentinel/.test(document.body.innerText)", 90, "imagery")
                except TimeoutError:
                    pass
                cdp.js("(() => { document.querySelector('[data-testid=imagery-panels]')?.scrollIntoView({block: 'center'}); return 0; })()")
                for _ in range(14):
                    rec.add_shot(cdp, 0.3)
                    time.sleep(0.1)
            v = rep.get("verdict", {})
            res = work / f"result{n}.jpg"
            lines = [f"Class shown to the analyst: {v.get('class_shown') or (rep.get('final') or {}).get('focus', {}).get('class') or 'n/a'}"
                     f"   (dominant class in the area: {max((rep.get('final') or {}).get('by_class', {'-': 0}), key=(rep.get('final') or {}).get('by_class', {'-': 0}).get)})",
                     f"Alerts raised: {len(rep.get('alerts', []))}" + "".join(f"  [{a['severity']} {a['trigger']} {a['at'][:16]}Z]" for a in rep.get("alerts", [])[:3]),
                     f"Result: {'PASS' if v.get('pass') else 'MISS'} - expected {want}"]
            card(res, ("PASS: " if v.get("pass") else "MISS: ") + sc.title, lines, tone=(6, 78, 59) if v.get("pass") else (127, 29, 29))
            rec.add_card(res, 5.0)
            try:
                (OUT / f"{sc.id}.report.json").write_text(json.dumps({k: rep.get(k) for k in ("scenario", "title", "start", "end", "verdict", "final", "alerts", "baseline")},
                                                                  indent=1, default=str), encoding="utf-8")
            except OSError:
                pass
        rec.encode(OUT / "thermotrace_replay_demo.mp4")
        print("wrote", OUT / "thermotrace_replay_demo.mp4", f"({sum(s for _, s in rec.frames):.0f} s)")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
