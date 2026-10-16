"""Lean data bundle for the public demo VM (the full database is ~1.5 GB, mostly 1.9 M archived detections).

    python deploy/demo_bundle.py make     [--out demo-bundle]    # on the machine that has the data (docker compose stack running)
    python deploy/demo_bundle.py restore  [--bundle demo-bundle] # on the VM after `docker compose up -d db migrate`

``make`` writes gzip CSVs (one per table, with header) plus ``baseline/thermal_baseline.parquet``; ``restore`` truncates those
tables in the target database and loads them. Only what the dashboard shows is included: boundaries, the merged facility
database, the current thermal sources with their last 21 days of detections, alerts and decisions, labels, the model record.
The 365-day baseline is the parquet file the worker reads when its database view is empty.

Use ``--db NAME`` to target another database of the same server (e.g. a scratch copy to test a restore).
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# table -> SELECT for ``make`` (None = SELECT *); order matters for restore (parents first)
TABLES: list[tuple[str, str | None]] = [
    ("states", None), ("districts", None), ("india_boundary", None),
    ("facilities", None), ("facility_sources", None),
    ("thermal_runs", "SELECT * FROM thermal_runs ORDER BY id DESC LIMIT 400"),
    ("thermal_sources", "SELECT * FROM thermal_sources WHERE is_current"),
    ("thermal_detections", "SELECT d.detection_id, d.instrument, d.acq_datetime, d.geometry, d.frp, d.bright_ti4, d.bright_ti5, d.confidence, d.daynight, d.source_id, d.loaded_at "  # generated columns are not copied
                           "FROM thermal_detections d JOIN thermal_sources s ON s.source_id = d.source_id AND s.is_current "
                           "WHERE d.acq_datetime >= now() - interval '21 days'"),
    ("thermal_model", None), ("alerts", "SELECT * FROM alerts WHERE NOT is_test"), ("alert_decisions", "SELECT d.* FROM alert_decisions d JOIN alerts a USING (alert_id) WHERE NOT a.is_test"), ("labels", "SELECT * FROM labels WHERE rule_version = 'analyst-v1'"),
]


def compose_base() -> list[str]:
    """``docker compose`` plus DEMO_COMPOSE_ARGS (e.g. "-f docker-compose.yml -f docker-compose.prod.yml" or "-p other-project")."""
    return ["docker", "compose", *os.getenv("DEMO_COMPOSE_ARGS", "").split()]


def compose(*args: str, stdin=None, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run([*compose_base(), *args], cwd=ROOT, stdin=stdin, check=True, capture_output=capture)


def env() -> tuple[str, str]:
    values = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values.get("POSTGRES_USER", "thermotrace"), values.get("POSTGRES_DB", "thermotrace")


def psql(db: str, user: str, sql: str, stdin=None, capture=False):
    return compose("exec", "-T", "db", "psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-c", sql, stdin=stdin, capture=capture)


def make(out: Path, db: str, user: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for table, query in TABLES:
        q = query or f"SELECT * FROM {table}"
        data = psql(db, user, f"\\copy ({q}) to stdout with (format csv, header)", capture=True).stdout
        (out / f"{table}.csv.gz").write_bytes(gzip.compress(data))
        print(f"{table:<20} {len(data) / 1e6:8.2f} MB raw")
    src = ROOT / "data" / "baseline" / "thermal_baseline.parquet"
    if src.exists():
        (out / "baseline").mkdir(exist_ok=True)
        shutil.copy2(src, out / "baseline" / "thermal_baseline.parquet")
        print(f"baseline parquet     {src.stat().st_size / 1e6:8.2f} MB")
    print("bundle:", out, f"({sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) / 1e6:.1f} MB)")


def restore(bundle: Path, db: str, user: str) -> None:
    psql(db, user, "TRUNCATE " + ", ".join(t for t, _ in TABLES) + " CASCADE")
    for table, _ in TABLES:
        f = bundle / f"{table}.csv.gz"
        if not f.exists():
            print("skip", table)
            continue
        raw = gzip.decompress(f.read_bytes())
        cols = raw.split(b"\n", 1)[0].decode().strip()
        copy = f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER)"
        subprocess.run([*compose_base(), "exec", "-T", "db", "psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-c", copy],
                       cwd=ROOT, input=raw, check=True)
        print("loaded", table)
    psql(db, user, "SELECT setval(pg_get_serial_sequence('alerts', 'alert_id'), COALESCE((SELECT max(alert_id) FROM alerts), 1))")
    psql(db, user, "UPDATE data_revision SET revision = revision + 1, changed_at = NOW() WHERE id = 1")
    print("restored into", db)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["make", "restore"])
    ap.add_argument("--out", default="demo-bundle")
    ap.add_argument("--bundle", default="demo-bundle")
    ap.add_argument("--db")
    a = ap.parse_args()
    user, default_db = env()
    db = a.db or default_db
    if a.command == "make":
        make(Path(a.out), db, user)
    else:
        if not a.db:
            raise SystemExit("restore wipes the bundle's tables in the target database: name it explicitly with --db")
        restore(Path(a.bundle), db, user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
