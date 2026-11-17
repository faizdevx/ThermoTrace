"""Load a demo bundle (deploy/demo_bundle.py make) straight into the local database, without docker compose.

    python hf-space/restore.py /bundle      # uses DATABASE_URL
"""
from __future__ import annotations

import gzip
import io
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from demo_bundle import TABLES  # noqa: E402  (same table list and order as the docker restore)


def main(bundle: Path) -> int:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(t for t, _ in TABLES) + " CASCADE")
        for table, _ in TABLES:
            f = bundle / f"{table}.csv.gz"
            if not f.exists():
                print("skip", table)
                continue
            raw = gzip.decompress(f.read_bytes())
            cols = raw.split(b"\n", 1)[0].decode().strip()
            cur.copy_expert(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER)", io.BytesIO(raw))
            print("loaded", table)
        cur.execute("SELECT setval(pg_get_serial_sequence('alerts', 'alert_id'), COALESCE((SELECT max(alert_id) FROM alerts), 1))")
        cur.execute("UPDATE data_revision SET revision = revision + 1, changed_at = NOW() WHERE id = 1")
    print("restored", bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "/bundle")))
