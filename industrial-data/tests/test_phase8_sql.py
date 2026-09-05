from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)


class _FakeBeginContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _FakeConnection:
        return self.connection

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def begin(self) -> _FakeBeginContext:
        return _FakeBeginContext(self.connection)


def _load_government_storage_module():
    sys.modules.setdefault("geopandas", types.SimpleNamespace(GeoDataFrame=object, read_postgis=lambda *args, **kwargs: None))
    sys.modules.setdefault("pandas", types.SimpleNamespace(DataFrame=object, read_sql=lambda *args, **kwargs: None))
    sys.modules.setdefault("geoalchemy2", types.SimpleNamespace(Geometry=lambda *args, **kwargs: None))

    if "sqlalchemy" not in sys.modules:
        sqlalchemy_module = types.ModuleType("sqlalchemy")

        class _FakeTable:
            def __init__(self, name, metadata, *args, **kwargs):
                self.name = name

        class _FakeMetaData:
            def create_all(self, engine, tables=None):
                return None

        class _FakeColumn:
            def __init__(self, *args, **kwargs):
                return None

        sqlalchemy_module.BigInteger = object()
        sqlalchemy_module.Date = object()
        sqlalchemy_module.Float = object()
        sqlalchemy_module.JSON = object()
        sqlalchemy_module.MetaData = _FakeMetaData
        sqlalchemy_module.Table = _FakeTable
        sqlalchemy_module.Text = object()
        sqlalchemy_module.Column = _FakeColumn
        sqlalchemy_module.text = lambda value: value

        sqlalchemy_engine_module = types.ModuleType("sqlalchemy.engine")

        class _FakeEngineType:
            pass

        sqlalchemy_engine_module.Engine = _FakeEngineType
        sqlalchemy_module.engine = sqlalchemy_engine_module
        sys.modules["sqlalchemy"] = sqlalchemy_module
        sys.modules["sqlalchemy.engine"] = sqlalchemy_engine_module

    return importlib.import_module("src.government.storage")


def test_government_table_bootstrap_emits_phase8_indexes(monkeypatch):
    storage_module = _load_government_storage_module()
    captured_tables: list[str] = []

    def fake_create_all(self, engine, tables=None):
        captured_tables.extend(table.name for table in tables or [])

    monkeypatch.setattr(storage_module.MetaData, "create_all", fake_create_all)

    engine = _FakeEngine()
    storage_module.ensure_government_table(engine, "government_industries_source_a")

    assert captured_tables == ["government_industries_source_a"]
    assert any('CREATE INDEX IF NOT EXISTS idx_government_industries_source_a_geometry_gist' in statement for statement in engine.connection.statements)
    assert any('CREATE INDEX IF NOT EXISTS idx_government_industries_source_a_source_id' in statement for statement in engine.connection.statements)
    assert any('CREATE INDEX IF NOT EXISTS idx_government_industries_source_a_extraction_date' in statement for statement in engine.connection.statements)
    assert any('CREATE INDEX IF NOT EXISTS idx_government_industries_source_a_last_seen' in statement for statement in engine.connection.statements)


def test_phase8_sql_helpers_and_idempotent_constraints_exist():
    repo_root = Path(__file__).resolve().parents[1]

    functions_sql = (repo_root / "sql" / "functions.sql").read_text(encoding="utf-8")
    schema_sql = (repo_root / "sql" / "schema.sql").read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm;" in functions_sql
    assert "calculate_distance_meters" in functions_sql
    assert "point_within_district" in functions_sql
    assert "generate_spatial_candidate_pairs" in functions_sql
    assert "detect_duplicate_clusters" in functions_sql

    assert "DO $$" in schema_sql
    assert "industrial_sites_source_count_nonnegative" in schema_sql
    assert "industrial_entity_matches_score_range" in schema_sql
    assert "industrial_sites_score_range" in schema_sql