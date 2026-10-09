from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from sqlalchemy.engine import Engine


def bootstrap_database(engine: Engine, sql_dir: str | Path) -> None:
    sql_path = Path(sql_dir)
    for filename in ("schema.sql", "functions.sql", "indexes.sql"):
        script_path = sql_path / filename
        script_text = script_path.read_text(encoding="utf-8")
        # Raw DBAPI cursor: SQLAlchemy >= 2.0.4x passes an immutabledict of params
        # to psycopg2 for multi-statement scripts, which raises TypeError.
        with engine.begin() as connection:
            cursor = connection.connection.cursor()
            try:
                cursor.execute(script_text)
            finally:
                cursor.close()


def write_boundary_table(engine: Engine, table_name: str, gdf: gpd.GeoDataFrame) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE")

    gdf.to_postgis(table_name, engine, if_exists="append", index=False)