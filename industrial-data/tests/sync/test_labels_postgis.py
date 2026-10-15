"""labels table: migration 007 applies, persist_postgis loads a label file, constraints hold."""
from __future__ import annotations

import json

import pandas as pd
import psycopg2
import pytest

from src.labels import build as B
from src.labels import rules as R

from .conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set (needs PostGIS)")


def _frame() -> pd.DataFrame:
    rows = []
    for i, (label, split) in enumerate([("industrial_persistent", "train"), ("agricultural_burning", "test"),
                                        ("vegetation_fire", "train"), ("industrial_fire", "incident_test")]):
        rows.append({"label_id": f"t-{i}", "unit_type": "incident" if label == "industrial_fire" else "cell_window",
                     "cell_key": None if label == "industrial_fire" else f"c{i}", "source_id": "IN-002" if label == "industrial_fire" else None,
                     "lat": 21.0 + i, "lon": 83.0, "state": "Odisha", "window_start": "2026-04-02", "window_end": "2026-04-08",
                     "label": label, "label_source": f"rule:{label}@{R.RULE_VERSION}", "evidence_url": "https://example.org/e",
                     "labelled_by": "rules:test", "rule_version": R.RULE_VERSION, "split": split, "region_group": "train_regions",
                     "n_detections": 3, "n_days": 2, "frp_max": 5.5, "night_fraction": 1.0, "detail": json.dumps({"k": i})})
    return pd.DataFrame(rows)


def test_persist_replaces_the_version_and_keeps_constraints(dsn, tmp_path):
    path = tmp_path / "labels_v1.parquet"
    _frame().to_parquet(path, index=False)
    assert B.persist_postgis(dsn, path) == 4
    assert B.persist_postgis(dsn, path) == 4          # re-run replaces the same version, no duplicates
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT label, split, ST_Y(geometry), detail->>'k' FROM labels ORDER BY label_id")
        got = cur.fetchall()
        assert [g[0] for g in got] == ["industrial_persistent", "agricultural_burning", "vegetation_fire", "industrial_fire"]
        assert got[3][1] == "incident_test" and got[0][2] == 21.0 and got[2][3] == "2"
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("""INSERT INTO labels (label_id, unit_type, geometry, window_start, window_end, label, label_source,
                           labelled_by, rule_version, split) VALUES ('bad', 'cell_window', ST_SetSRID(ST_MakePoint(1, 1), 4326),
                           '2026-01-01', '2026-01-07', 'x', 'x', 'x', 'v', 'random')""")
