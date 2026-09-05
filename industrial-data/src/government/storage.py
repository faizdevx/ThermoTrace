from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Date, Float, JSON, MetaData, Table, Text, Column, text
from sqlalchemy.engine import Engine

from src.temporal import merge_temporal_snapshots


def _table_name_is_safe(table_name: str) -> bool:
    return table_name.replace("_", "").isalnum()


def ensure_government_table(engine: Engine, table_name: str) -> None:
    if not _table_name_is_safe(table_name):
        raise ValueError(f"Unsafe table name: {table_name}")

    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("source_id", Text),
        Column("name", Text),
        Column("industry_type", Text),
        Column("address", Text),
        Column("state", Text),
        Column("district", Text),
        Column("latitude", Float),
        Column("longitude", Float),
        Column("geometry", Geometry("GEOMETRY", srid=4326)),
        Column("establishment_status", Text),
        Column("establishment_date", Date),
        Column("extraction_date", Date),
        Column("first_seen", Date),
        Column("last_seen", Date),
        Column("operational_status", Text),
        Column("source", Text, nullable=False),
        Column("source_date", Text),
        Column("raw_record", JSON, nullable=False),
    )
    metadata.create_all(engine, tables=[table])

    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_geometry_gist ON "{table_name}" USING GIST (geometry)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_source_id ON "{table_name}" (source_id)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_source ON "{table_name}" (source)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_state ON "{table_name}" (state)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_district ON "{table_name}" (district)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_industry_type ON "{table_name}" (industry_type)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_extraction_date ON "{table_name}" (extraction_date)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_first_seen ON "{table_name}" (first_seen)')
        connection.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_last_seen ON "{table_name}" (last_seen)')


def bootstrap_database(engine: Engine, sql_dir: str | Path) -> None:
    sql_path = Path(sql_dir)
    for filename in ("schema.sql", "functions.sql", "indexes.sql"):
        script_path = sql_path / filename
        script_text = script_path.read_text(encoding="utf-8")
        with engine.begin() as connection:
            connection.exec_driver_sql(script_text)


def write_government_table(engine: Engine, table_name: str, gdf: gpd.GeoDataFrame) -> None:
    ensure_government_table(engine, table_name)
    with engine.begin() as connection:
        try:
            existing = gpd.read_postgis(f'SELECT * FROM "{table_name}"', connection, geom_col="geometry")
        except Exception:
            existing = None

    merged = merge_temporal_snapshots(existing, gdf, key_columns=["source", "source_id"])

    with engine.begin() as connection:
        connection.execute(text(f'DELETE FROM "{table_name}"'))

    dtype = {"geometry": Geometry("GEOMETRY", srid=4326)}
    merged.to_postgis(table_name, engine, if_exists="append", index=False, dtype=dtype)
