"""CSV / XLSX / GeoJSON / GPKG file-backed government source adapter.

This adapter covers the most common case: a government dataset that has been
downloaded and placed at a known local path.

Configuration block in ``config.yaml``
---------------------------------------
::

    government_sources:
      - id: example_source
        adapter: csv_file
        name: "Example Government Industrial Registry"
        enabled: true
        file: "government/sources/example_source/data.csv"
        source_date: "2026-01-01"      # Optional ISO date of the dataset
        state_column: "State"           # Column holding state name (optional)
        name_column: "FacilityName"     # Column holding facility name
        id_column: "RegistrationNumber" # Column holding unique facility ID
        type_column: "IndustryType"     # Column holding industry type
        address_column: "Address"       # Column holding address (optional)
        district_column: "District"     # Column holding district (optional)
        establishment_status_column: "Status"   # optional
        establishment_date_column: "EstDate"    # optional
        source_crs: "EPSG:4326"         # CRS of coordinates in the file

Notes
-----
* ``file`` must be a path relative to the ``industrial-data/`` working
  directory, or an absolute path.
* If the file does not exist, ``is_available()`` returns False and the pipeline
  skips this source with an honest log message.
* The original row values are preserved in ``raw_record`` as a JSON dict.
* No fabrication: if the file is absent, ingestion is skipped and reported.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from src.government.config import (
    GovernmentSchemaConfig,
    get_government_cleaning_config,
    get_government_schema_config,
    get_government_source_config,
)
from src.government.processing import clean_government_dataframe
from src.government.schema import standardize_column_name
from src.government.sources.base import (
    AbstractGovernmentSource,
    SourceIngestionResult,
    ValidationError,
)
from src.temporal import current_extraction_date

logger = logging.getLogger(__name__)


class CsvFileGovernmentSource(AbstractGovernmentSource):
    """Government source backed by a local CSV, XLSX, GeoJSON, or GPKG file.

    See module docstring for configuration details.
    """

    ADAPTER_ID = "csv_file"

    # --------------------------------------------------------------------- #
    # Properties                                                              #
    # --------------------------------------------------------------------- #

    @property
    def source_id(self) -> str:
        return str(self._source_config.get("id", "csv_file_source"))

    @property
    def source_name(self) -> str:
        return str(self._source_config.get("name", "CSV File Government Source"))

    # --------------------------------------------------------------------- #
    # Availability                                                            #
    # --------------------------------------------------------------------- #

    def _file_path(self) -> Path:
        raw = self._source_config.get("file", "")
        return Path(raw)

    def is_available(self) -> bool:
        fp = self._file_path()
        return bool(str(fp)) and fp.exists()

    def unavailability_reason(self) -> str:
        fp = self._file_path()
        if not str(fp):
            return (
                f"No 'file' path configured for source '{self.source_id}'. "
                f"Add 'file: path/to/data.csv' to the source config block."
            )
        if not fp.exists():
            return (
                f"File not found: {fp.resolve()}\n"
                f"  To use this source:\n"
                f"    1. Obtain the dataset from the relevant government authority.\n"
                f"    2. Place it at: {fp.resolve()}\n"
                f"    3. Re-run the pipeline."
            )
        return f"File path '{fp}' is not accessible."

    # --------------------------------------------------------------------- #
    # Loading                                                                 #
    # --------------------------------------------------------------------- #

    def _load_file(self) -> pd.DataFrame:
        fp = self._file_path()
        suffix = fp.suffix.lower()
        logger.info("Loading government source '%s' from %s", self.source_id, fp)
        if suffix in {".csv", ".tsv"}:
            return pd.read_csv(fp, sep="\t" if suffix == ".tsv" else ",")
        if suffix in {".json", ".geojson", ".gpkg", ".shp"}:
            return gpd.read_file(fp)
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(fp)
        # Default — try CSV
        return pd.read_csv(fp)

    # --------------------------------------------------------------------- #
    # Ingestion                                                               #
    # --------------------------------------------------------------------- #

    def ingest(
        self,
        *,
        state_name: str | None = None,
        max_validation_errors: int = 100,
    ) -> SourceIngestionResult:
        self._assert_available()

        raw_frame = self._load_file()
        raw_row_count = len(raw_frame)
        logger.info(
            "Source '%s': loaded %d raw rows from %s",
            self.source_id, raw_row_count, self._file_path(),
        )

        # Standardize column names so config keys match
        raw_frame = raw_frame.copy()
        raw_frame.columns = [standardize_column_name(c) for c in raw_frame.columns]

        sc = self._source_config
        state_col = standardize_column_name(sc["state_column"]) if sc.get("state_column") else None
        name_col = standardize_column_name(sc["name_column"]) if sc.get("name_column") else None
        id_col = standardize_column_name(sc["id_column"]) if sc.get("id_column") else None
        type_col = standardize_column_name(sc["type_column"]) if sc.get("type_column") else None
        address_col = standardize_column_name(sc["address_column"]) if sc.get("address_column") else None
        district_col = standardize_column_name(sc["district_column"]) if sc.get("district_column") else None
        status_col = standardize_column_name(sc["establishment_status_column"]) if sc.get("establishment_status_column") else None
        date_col = standardize_column_name(sc["establishment_date_column"]) if sc.get("establishment_date_column") else None
        source_crs = sc.get("source_crs", "EPSG:4326")
        source_date = sc.get("source_date")

        # State-level filter
        before_filter = len(raw_frame)
        if state_name and state_col and state_col in raw_frame.columns:
            raw_frame = raw_frame[
                raw_frame[state_col].astype(str).str.strip().str.lower()
                == state_name.lower()
            ].reset_index(drop=True)
            logger.info(
                "Source '%s': state filter '%s' → %d rows (from %d)",
                self.source_id, state_name, len(raw_frame), before_filter,
            )

        if raw_frame.empty:
            logger.warning(
                "Source '%s': no rows for state '%s'", self.source_id, state_name
            )
            empty_gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs="EPSG:4326"))
            return SourceIngestionResult(
                source_id=self.source_id,
                source_name=self.source_name,
                state_filter=state_name,
                extraction_date=current_extraction_date(),
                gdf=empty_gdf,
                raw_row_count=raw_row_count,
                valid_row_count=0,
                rejected_row_count=0,
                duplicate_row_count=0,
            )

        schema_config = get_government_schema_config(self._config)
        cleaning_config = get_government_cleaning_config(self._config)
        government_source_config = get_government_source_config(self._config)

        output_crs = sc.get("output_crs", government_source_config.default_output_crs)

        # Collect validation errors before clean (rows with no lat/lon)
        validation_errors: list[ValidationError] = []
        coord_cands = schema_config.coordinate_candidates
        lat_candidates = coord_cands.get("latitude", [])
        lon_candidates = coord_cands.get("longitude", [])

        def _find_col(candidates: list[str]) -> str | None:
            for c in candidates:
                if c in raw_frame.columns:
                    return c
            return None

        lat_col = _find_col(lat_candidates)
        lon_col = _find_col(lon_candidates)

        if lat_col and lon_col and len(validation_errors) < max_validation_errors:
            for i, row in raw_frame.iterrows():
                lat_val = row.get(lat_col)
                lon_val = row.get(lon_col)
                if pd.isna(lat_val) or pd.isna(lon_val):
                    validation_errors.append(
                        ValidationError(
                            row_index=int(i),
                            column=f"{lat_col}/{lon_col}",
                            value=f"({lat_val}, {lon_val})",
                            reason="Missing latitude or longitude — row will be rejected",
                        )
                    )
                    if len(validation_errors) >= max_validation_errors:
                        break

        try:
            cleaned_gdf, clean_summary = clean_government_dataframe(
                raw_frame,
                source_name=self.source_id,
                source_table_name=self.source_id,
                source_date=source_date,
                source_id_column=id_col,
                name_column=name_col,
                industry_type_column=type_col,
                address_column=address_col,
                state_column=state_col,
                district_column=district_col,
                establishment_status_column=status_col,
                establishment_date_column=date_col,
                source_crs=source_crs,
                output_crs=output_crs,
                schema_config=schema_config,
                exact_duplicate_coordinate_precision=cleaning_config.exact_duplicate_coordinate_precision,
            )
        except Exception as exc:
            from src.government.sources.base import SourceIngestionError
            raise SourceIngestionError(
                f"Source '{self.source_id}': clean_government_dataframe failed: {exc}"
            ) from exc

        return SourceIngestionResult(
            source_id=self.source_id,
            source_name=self.source_name,
            state_filter=state_name,
            extraction_date=current_extraction_date(),
            gdf=cleaned_gdf,
            raw_row_count=raw_row_count,
            valid_row_count=clean_summary["valid_rows"],
            rejected_row_count=clean_summary["rejected_rows"],
            duplicate_row_count=clean_summary["duplicate_rows_removed"],
            validation_errors=validation_errors,
        )
