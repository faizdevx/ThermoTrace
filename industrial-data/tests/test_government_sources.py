"""Tests for Phase 5 — government source adapter architecture.

All tests are pure Python — no PostGIS, no network, no real files.
File-backed adapter tests use tmp_path to create real temporary files.

Coverage:
  * SourceNotAvailable and SourceIngestionError exceptions
  * AbstractGovernmentSource protocol
  * CsvFileGovernmentSource.is_available() with and without file
  * CsvFileGovernmentSource.unavailability_reason() content
  * CsvFileGovernmentSource.ingest() with a real temp CSV
  * CsvFileGovernmentSource state-level filtering
  * Registry — load_configured_sources() with disabled source
  * Registry — load_configured_sources() with unknown adapter type
  * Registry — ingest_government_sources() with available source
  * Registry — ingest_government_sources() with unavailable source (honest report)
  * ValidationError dataclass
"""
from __future__ import annotations

import csv
import textwrap
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest

from src.government.sources.base import (
    AbstractGovernmentSource,
    SourceIngestionError,
    SourceIngestionResult,
    SourceNotAvailable,
    ValidationError,
)
from src.government.sources.csv_file import CsvFileGovernmentSource
from src.government.sources.registry import (
    IngestionSummary,
    ingest_government_sources,
    load_configured_sources,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config() -> dict[str, Any]:
    """Minimal config.yaml-equivalent dict for the government module."""
    return {
        "government": {
            "source": {
                "provider": "government",
                "default_table_prefix": "gov_",
                "default_output_crs": "EPSG:4326",
            },
            "schema": {
                "missing_values": ["", None],
                "unknown_values": ["unknown"],
                "not_applicable_values": ["n/a"],
                "coordinate_candidates": {
                    "latitude": ["latitude", "lat", "y"],
                    "longitude": ["longitude", "lon", "lng", "x"],
                    "easting": ["easting"],
                    "northing": ["northing"],
                },
                "operational_status_candidates": ["operational_status", "status"],
            },
            "cleaning": {
                "exact_duplicate_coordinate_precision": 6,
            },
        },
        "government_sources": [],
    }


def _write_temp_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a temporary CSV file and return its path."""
    fp = tmp_path / "data.csv"
    if rows:
        with fp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        fp.touch()
    return fp


def _source_config(file_path: Path, **overrides) -> dict[str, Any]:
    base = {
        "id": "test_source",
        "adapter": "csv_file",
        "name": "Test Government Source",
        "enabled": True,
        "file": str(file_path),
        "name_column": "FacilityName",
        "id_column": "RegNo",
        "type_column": "IndustryType",
        "state_column": "State",
        "district_column": "District",
        "source_crs": "EPSG:4326",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_source_not_available_is_exception(self):
        exc = SourceNotAvailable("test reason")
        assert isinstance(exc, Exception)
        assert "test reason" in str(exc)

    def test_source_ingestion_error_is_exception(self):
        exc = SourceIngestionError("ingestion failed")
        assert isinstance(exc, Exception)

    def test_validation_error_to_dict(self):
        err = ValidationError(row_index=5, column="latitude", value=None, reason="missing")
        d = err.to_dict()
        assert d["row_index"] == 5
        assert d["column"] == "latitude"
        assert d["reason"] == "missing"


# ---------------------------------------------------------------------------
# CsvFileGovernmentSource — availability
# ---------------------------------------------------------------------------

class TestCsvFileAvailability:
    def test_is_available_false_when_no_file_config(self, tmp_path):
        config = _minimal_config()
        sc = _source_config(tmp_path / "nonexistent.csv")
        adapter = CsvFileGovernmentSource(config, sc)
        assert not adapter.is_available()

    def test_is_available_true_when_file_exists(self, tmp_path):
        fp = _write_temp_csv(tmp_path, [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9}
        ])
        config = _minimal_config()
        sc = _source_config(fp)
        adapter = CsvFileGovernmentSource(config, sc)
        assert adapter.is_available()

    def test_unavailability_reason_mentions_file_path(self, tmp_path):
        config = _minimal_config()
        sc = _source_config(tmp_path / "missing.csv")
        adapter = CsvFileGovernmentSource(config, sc)
        reason = adapter.unavailability_reason()
        assert "missing.csv" in reason or "not found" in reason.lower()

    def test_unavailability_reason_includes_instructions(self, tmp_path):
        config = _minimal_config()
        sc = _source_config(tmp_path / "missing.csv")
        adapter = CsvFileGovernmentSource(config, sc)
        reason = adapter.unavailability_reason()
        # Must tell the user what to do
        assert any(word in reason.lower() for word in ["obtain", "place", "re-run", "not found"])


# ---------------------------------------------------------------------------
# CsvFileGovernmentSource — ingest
# ---------------------------------------------------------------------------

class TestCsvFileIngest:
    def _make_adapter(self, tmp_path, rows, **sc_overrides) -> CsvFileGovernmentSource:
        fp = _write_temp_csv(tmp_path, rows)
        config = _minimal_config()
        sc = _source_config(fp, **sc_overrides)
        return CsvFileGovernmentSource(config, sc)

    def test_ingest_raises_when_not_available(self, tmp_path):
        config = _minimal_config()
        sc = _source_config(tmp_path / "nonexistent.csv")
        adapter = CsvFileGovernmentSource(config, sc)
        with pytest.raises(SourceNotAvailable):
            adapter.ingest()

    def test_ingest_returns_source_ingestion_result(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        assert isinstance(result, SourceIngestionResult)

    def test_ingest_populates_source_id_field(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        assert result.source_id == "test_source"

    def test_ingest_result_has_valid_geodataframe(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        assert isinstance(result.gdf, gpd.GeoDataFrame)
        assert len(result.gdf) == 1
        assert result.gdf.crs is not None

    def test_ingest_preserves_raw_record(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        # raw_record must contain the original row data
        assert "raw_record" in result.gdf.columns
        raw = result.gdf.iloc[0]["raw_record"]
        assert raw is not None

    def test_state_filter_reduces_rows(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
            {"RegNo": "R2", "FacilityName": "Beta Works", "IndustryType": "Chemical",
             "State": "Kerala", "District": "Ernakulam", "Latitude": 9.9, "Longitude": 76.2},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest(state_name="Goa")
        assert result.valid_row_count == 1

    def test_state_filter_case_insensitive(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "GOA", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest(state_name="goa")
        assert result.valid_row_count == 1

    def test_records_with_missing_coordinates_rejected(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Good Record",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
            {"RegNo": "R2", "FacilityName": "Bad Record",
             "State": "Goa", "District": "South Goa", "Latitude": None, "Longitude": None},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        # Bad record should be rejected
        assert result.valid_row_count == 1
        assert result.rejected_row_count == 1

    def test_provenance_fields_set(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        assert result.extraction_date is not None
        assert result.state_filter is None  # no state filter

    def test_summary_has_required_keys(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        adapter = self._make_adapter(tmp_path, rows)
        result = adapter.ingest()
        summary = result.summary
        for key in ("source_id", "raw_row_count", "valid_row_count", "rejected_row_count",
                    "duplicate_row_count", "validation_error_count", "extraction_date"):
            assert key in summary, f"Missing key in summary: {key}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_disabled_source_not_loaded(self):
        config = _minimal_config()
        config["government_sources"] = [
            {"id": "disabled", "adapter": "csv_file", "enabled": False,
             "name": "Disabled Source", "file": "/nonexistent.csv"}
        ]
        adapters = load_configured_sources(config)
        assert len(adapters) == 0

    def test_unknown_adapter_type_skipped_with_warning(self, caplog):
        config = _minimal_config()
        config["government_sources"] = [
            {"id": "mystery", "adapter": "future_api_adapter",
             "enabled": True, "name": "Mystery Source"}
        ]
        import logging
        with caplog.at_level(logging.WARNING):
            adapters = load_configured_sources(config)
        assert len(adapters) == 0

    def test_enabled_csv_source_loaded(self, tmp_path):
        fp = tmp_path / "data.csv"
        fp.touch()
        config = _minimal_config()
        config["government_sources"] = [
            {"id": "test_src", "adapter": "csv_file", "enabled": True,
             "name": "Test", "file": str(fp)}
        ]
        adapters = load_configured_sources(config)
        assert len(adapters) == 1
        assert adapters[0].source_id == "test_src"

    def test_ingest_government_sources_empty_config(self):
        config = _minimal_config()
        config["government_sources"] = []
        result = ingest_government_sources(config, state_name="Goa")
        assert isinstance(result, IngestionSummary)
        assert not result.has_data
        assert result.total_records == 0

    def test_ingest_reports_unavailable_source(self, tmp_path):
        config = _minimal_config()
        config["government_sources"] = [
            {"id": "absent", "adapter": "csv_file", "enabled": True,
             "name": "Absent Source", "file": str(tmp_path / "nonexistent.csv")}
        ]
        result = ingest_government_sources(config, state_name="Goa")
        assert not result.has_data
        assert len(result.unavailable_sources) == 1
        assert result.unavailable_sources[0]["source_id"] == "absent"

    def test_ingest_returns_data_from_available_source(self, tmp_path):
        rows = [
            {"RegNo": "R1", "FacilityName": "Alpha Works", "IndustryType": "Textile",
             "State": "Goa", "District": "North Goa", "Latitude": 15.5, "Longitude": 73.9},
        ]
        fp = _write_temp_csv(tmp_path, rows)
        config = _minimal_config()
        config["government_sources"] = [
            {
                "id": "goa_test", "adapter": "csv_file", "enabled": True,
                "name": "Goa Test Source", "file": str(fp),
                "name_column": "FacilityName", "id_column": "RegNo",
                "type_column": "IndustryType", "state_column": "State",
                "district_column": "District", "source_crs": "EPSG:4326",
            }
        ]
        result = ingest_government_sources(config, state_name="Goa")
        assert result.has_data
        assert result.total_records == 1
        assert len(result.available_sources) == 1

    def test_ingestion_summary_to_dict(self):
        config = _minimal_config()
        config["government_sources"] = []
        result = ingest_government_sources(config)
        d = result.to_dict()
        assert "state_filter" in d
        assert "total_records" in d
        assert "available_sources" in d
        assert "unavailable_sources" in d


# ---------------------------------------------------------------------------
# Honest reporting — the key Phase 5 requirement
# ---------------------------------------------------------------------------

class TestHonestReporting:
    def test_no_fake_data_when_source_unavailable(self, tmp_path):
        """If a source file is missing, the GDF must be empty — not fabricated."""
        config = _minimal_config()
        config["government_sources"] = [
            {"id": "missing", "adapter": "csv_file", "enabled": True,
             "name": "Missing Source", "file": str(tmp_path / "not_here.csv")}
        ]
        result = ingest_government_sources(config, state_name="Uttar Pradesh")
        assert len(result.combined_gdf) == 0
        assert not result.has_data
        # Unavailability is reported
        assert result.unavailable_sources[0]["source_id"] == "missing"
        assert "reason" in result.unavailable_sources[0]

    def test_unavailable_reason_is_actionable(self, tmp_path):
        """The unavailability reason must contain actionable instructions."""
        config = _minimal_config()
        sc = {"id": "hint_test", "adapter": "csv_file", "enabled": True,
              "name": "Hint Test", "file": str(tmp_path / "missing.csv")}
        adapter = CsvFileGovernmentSource(config, sc)
        reason = adapter.unavailability_reason()
        # Must not be a vague message
        assert len(reason) > 20
        # Must mention the file
        assert "missing.csv" in reason
