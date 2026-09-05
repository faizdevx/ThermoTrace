from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from src.temporal import merge_temporal_snapshots


def bootstrap_database(engine: Engine, sql_dir: str | Path) -> None:
    sql_path = Path(sql_dir)
    for filename in ("schema.sql", "functions.sql", "indexes.sql"):
        script_path = sql_path / filename
        script_text = script_path.read_text(encoding="utf-8")
        with engine.begin() as connection:
            connection.exec_driver_sql(script_text)


def write_osm_table(engine: Engine, table_name: str, gdf: gpd.GeoDataFrame) -> None:
    dtype = {
        "raw_tags": JSONB,
        "geometry": Geometry("GEOMETRY", srid=4326),
    }
    with engine.begin() as connection:
        try:
            existing = gpd.read_postgis(f'SELECT * FROM "{table_name}"', connection, geom_col="geometry")
        except Exception:
            existing = None

    merged = merge_temporal_snapshots(existing, gdf, key_columns=["source", "osm_type", "osm_id"])

    with engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE")

    merged.to_postgis(table_name, engine, if_exists="append", index=False, dtype=dtype)
