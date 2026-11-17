"""Save the API's answers as the static site's data files (read by industrial-gis/frontend/src/services/staticApi.ts).

    python deploy/pages/crawl.py OUT_DIR [API_BASE]     # API_BASE defaults to http://127.0.0.1:8000/api
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

FILES = {
    "health.json": "/health", "sync_status.json": "/sync/status", "auth_me.json": "/auth/me",
    "thermal/classes.json": "/thermal/classes", "thermal/status.json": "/thermal/status",
    "thermal/sources.json": "/thermal/sources?limit=20000",
    "thermal/stats.json": "/thermal/stats", "thermal/stats_h24.json": "/thermal/stats?hours=24", "thermal/stats_h72.json": "/thermal/stats?hours=72",
    "facilities.json": "/facilities?limit=20000", "boundaries_states.json": "/boundaries/states", "boundaries_india.json": "/boundaries/india",
    "dashboard.json": "/dashboard", "alerts.json": "/alerts", "statistics.json": "/statistics", "filter_options.json": "/filters/options",
}


def main(out: Path, api: str) -> int:
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    failed = []
    for name, path in FILES.items():
        r = s.get(api + path, timeout=120)
        if not r.ok:
            failed.append(f"{path}: HTTP {r.status_code}")
            continue
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(r.text, encoding="utf-8")
        print(f"{name:<28} {len(r.content) / 1e6:6.2f} MB")
    # one file per alert for the inbox detail view
    alerts = json.loads((out / "alerts.json").read_text(encoding="utf-8")).get("alerts", []) if (out / "alerts.json").exists() else []
    (out / "alerts").mkdir(exist_ok=True)
    for a in alerts:
        r = s.get(f"{api}/alerts/{a['alert_id']}", timeout=60)
        if r.ok:
            (out / "alerts" / f"{a['alert_id']}.json").write_text(r.text, encoding="utf-8")
    print(f"alerts/*.json                {len(alerts)} files")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    # the thermal layer is the site; without it there is nothing worth publishing
    return 1 if any(p.startswith("/thermal/sources") or p.startswith("/health") for p in failed) else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000/api"))
