from __future__ import annotations

import pandas as pd

from src.temporal import current_extraction_date, merge_temporal_snapshots


def test_merge_temporal_snapshots_preserves_first_and_last_seen():
    existing = pd.DataFrame(
        [
            {
                "source": "government",
                "source_id": "A1",
                "first_seen": pd.Timestamp("2026-01-01").date(),
                "last_seen": pd.Timestamp("2026-01-02").date(),
                "extraction_date": pd.Timestamp("2026-01-02").date(),
                "operational_status": "operational",
                "created_at": pd.Timestamp("2026-01-01T00:00:00Z"),
                "updated_at": pd.Timestamp("2026-01-02T00:00:00Z"),
            }
        ]
    )
    current = pd.DataFrame(
        [
            {
                "source": "government",
                "source_id": "A1",
                "first_seen": pd.Timestamp("2026-01-05").date(),
                "last_seen": pd.Timestamp("2026-01-05").date(),
                "extraction_date": pd.Timestamp("2026-01-05").date(),
                "operational_status": "temporarily_closed",
                "created_at": pd.Timestamp("2026-01-05T00:00:00Z"),
                "updated_at": pd.Timestamp("2026-01-05T00:00:00Z"),
            }
        ]
    )

    merged = merge_temporal_snapshots(existing, current, key_columns=["source", "source_id"])

    assert len(merged) == 1
    assert merged.iloc[0]["first_seen"] == pd.Timestamp("2026-01-01").to_pydatetime()
    assert merged.iloc[0]["last_seen"] == pd.Timestamp("2026-01-05").to_pydatetime()
    assert merged.iloc[0]["operational_status"] == "temporarily_closed"


def test_current_extraction_date_returns_date():
    extraction_date = current_extraction_date()
    assert extraction_date.__class__.__name__ == "date"
