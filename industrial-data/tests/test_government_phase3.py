from __future__ import annotations

import pandas as pd

from src.government.processing import clean_government_dataframe, normalize_classification_value
from src.government.schema import detect_coordinate_columns, inspect_schema, standardize_column_names


def test_standardize_column_names_and_schema_inspection():
    frame = pd.DataFrame(
        {
            "Source ID": ["A1"],
            "Industry Type": ["Factory"],
            "Latitude (Deg)": [28.5],
            "Longitude (Deg)": [77.2],
        }
    )

    standardized = standardize_column_names(frame)
    report = inspect_schema(standardized)

    assert "source_id" in standardized.columns
    assert report["row_count"] == 1
    assert "latitude_deg" in report["columns"]


def test_detect_coordinate_columns_with_synonyms():
    frame = pd.DataFrame(
        {
            "lat": [28.5],
            "lng": [77.2],
            "name": ["Alpha"],
        }
    )

    columns = detect_coordinate_columns(
        frame,
        {
            "latitude": ["latitude", "lat", "y"],
            "longitude": ["longitude", "lng", "x"],
            "easting": ["easting"],
            "northing": ["northing"],
        },
    )

    assert columns.latitude == "lat"
    assert columns.longitude == "lng"


def test_clean_government_dataframe_builds_geometry_and_deduplicates():
    frame = pd.DataFrame(
        {
            "Source ID": ["A1", "A1"],
            "Name": ["Alpha Works", "Alpha Works"],
            "Industry Type": ["Factory", "Factory"],
            "Address": ["Industrial Area", "Industrial Area"],
            "State": ["State X", "State X"],
            "District": ["District Y", "District Y"],
            "Latitude": [28.5, 28.5],
            "Longitude": [77.2, 77.2],
            "Establishment Status": ["Operational", "Operational"],
            "Establishment Date": ["2020-01-01", "2020-01-01"],
        }
    )

    cleaned, summary = clean_government_dataframe(
        frame,
        source_name="government",
        source_table_name="government_industries_source_a",
        source_date="2026-09-03",
        source_id_column="source_id",
        name_column="name",
        industry_type_column="industry_type",
        address_column="address",
        state_column="state",
        district_column="district",
        establishment_status_column="establishment_status",
        establishment_date_column="establishment_date",
        source_crs="EPSG:4326",
        output_crs="EPSG:4326",
        schema_config={
            "missing_values": [""],
            "unknown_values": ["unknown"],
            "not_applicable_values": ["n/a"],
            "coordinate_candidates": {
                "latitude": ["latitude", "lat"],
                "longitude": ["longitude", "lon", "lng"],
                "easting": ["easting"],
                "northing": ["northing"],
            },
        },
        exact_duplicate_coordinate_precision=6,
    )

    assert len(cleaned) == 1
    assert summary["duplicate_rows_removed"] == 1
    assert cleaned.iloc[0]["source_id"] == "A1"
    assert cleaned.geometry.iloc[0].is_valid
    assert cleaned.crs.to_string() == "EPSG:4326"


def test_classification_normalization_distinguishes_unknown_and_not_applicable():
    assert normalize_classification_value("unknown", missing_values=[""], unknown_values=["unknown"], not_applicable_values=["n/a"]) == "unknown"
    assert normalize_classification_value("N/A", missing_values=[""], unknown_values=["unknown"], not_applicable_values=["n/a"]) == "not_applicable"
    assert normalize_classification_value("Operational", missing_values=[""], unknown_values=["unknown"], not_applicable_values=["n/a"]) == "Operational"
