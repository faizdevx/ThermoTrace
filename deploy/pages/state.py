"""Carry the pipeline's state between GitHub Actions runs (the runner starts empty every time).

    python deploy/pages/state.py load STATE_DIR    # bundle tables -> database, cached files -> THERMAL_DATA_DIR
    python deploy/pages/state.py save STATE_DIR    # changing tables + cached files -> STATE_DIR (pushed to the pages-state branch)

STATE_DIR holds the demo bundle (deploy/demo_bundle.py, one gzip CSV per table, baseline/thermal_baseline.parquet) and live/*.gz
(the worker's caches: boundaries, land cover, detection history, status). Boundaries and facilities never change, so ``save``
rewrites only the tables a cycle changes. Uses DATABASE_URL.
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo_bundle import TABLES  # noqa: E402  (same tables, queries and load order as the VM bundle)

STATIC = {"states", "districts", "india_boundary", "facilities", "facility_sources"}
LIVE_FILES = ["status.json", "landcover_cache.json", "detections_history.csv", "india_adm0.geojson", "india_adm1.geojson"]


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://"))


def load(state: Path, live: Path) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(t for t, _ in TABLES) + " CASCADE")
        for table, _ in TABLES:
            f = state / f"{table}.csv.gz"
            if not f.exists():
                print("skip", table)
                continue
            raw = gzip.decompress(f.read_bytes())
            cols = raw.split(b"\n", 1)[0].decode().strip()
            cur.copy_expert(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER)", io.BytesIO(raw))
            print("loaded", table)
        cur.execute("SELECT setval(pg_get_serial_sequence('alerts', 'alert_id'), COALESCE((SELECT max(alert_id) FROM alerts), 1))")
        # nobody closes alerts on the public site: keep the last ALERT_KEEP_DAYS (dedupe needs only 6 h of them)
        cur.execute("DELETE FROM alerts WHERE created_at < now() - make_interval(days => %s)", (int(os.getenv("ALERT_KEEP_DAYS", "7")),))
        print("pruned", cur.rowcount, "old alerts")
        cur.execute("SELECT setval(pg_get_serial_sequence('thermal_runs', 'id'), COALESCE((SELECT max(id) FROM thermal_runs), 1))")
    live.mkdir(parents=True, exist_ok=True)
    for name in LIVE_FILES:
        f = state / "live" / f"{name}.gz"
        if f.exists():
            (live / name).write_bytes(gzip.decompress(f.read_bytes()))
    baseline = state / "baseline" / "thermal_baseline.parquet"
    target = Path(os.getenv("THERMAL_BASELINE_DIR", "data/baseline"))
    if baseline.exists():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(baseline, target / baseline.name)


def save(state: Path, live: Path) -> None:
    with _connect() as conn, conn.cursor() as cur:
        for table, query in TABLES:
            if table in STATIC:
                continue
            buf = io.BytesIO()
            cur.copy_expert(f"COPY ({query or f'SELECT * FROM {table}'}) TO STDOUT WITH (FORMAT csv, HEADER)", buf)
            (state / f"{table}.csv.gz").write_bytes(gzip.compress(buf.getvalue(), mtime=0))
            print("saved", table, len(buf.getvalue()))
    (state / "live").mkdir(exist_ok=True)
    for name in LIVE_FILES:
        f = live / name
        if f.exists():
            (state / "live" / f"{name}.gz").write_bytes(gzip.compress(f.read_bytes(), mtime=0))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("load", "save"):
        raise SystemExit(__doc__)
    live_dir = Path(os.getenv("THERMAL_DATA_DIR", "data/thermal/live"))
    (load if sys.argv[1] == "load" else save)(Path(sys.argv[2]), live_dir)
