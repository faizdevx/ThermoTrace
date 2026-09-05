from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Polygon

from src.boundaries.processing import clean_boundary_gdf, repair_geometry
from src.boundaries.source import geojson_dict_to_geodataframe


def test_repair_geometry_returns_valid_multipolygon():
    invalid_geometry = Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])

    repaired_geometry, was_repaired, status = repair_geometry(invalid_geometry)

    assert was_repaired is True
    assert status in {"make_valid", "buffer_0"}
    assert repaired_geometry is not None
    assert repaired_geometry.is_valid
    assert repaired_geometry.geom_type == "MultiPolygon"


def test_clean_boundary_gdf_drops_exact_duplicates(valid_boundary_gdf):
    duplicated = gpd.GeoDataFrame(
        [valid_boundary_gdf.iloc[0].to_dict(), valid_boundary_gdf.iloc[0].to_dict()],
        geometry="geometry",
        crs="EPSG:4326",
    )

    cleaned, summary = clean_boundary_gdf(
        duplicated,
        level_name="districts",
        table_name="districts",
        source_name="geoboundaries",
        metadata={"source_url": "https://example.test", "source_date": None},
        name_candidates=["shapeName"],
        administrative_code_candidates=["shapeID"],
        source_id_candidates=["shapeID"],
        storage_crs="EPSG:4326",
        source_assumed_crs="EPSG:4326",
    )

    assert len(cleaned) == 1
    assert summary["duplicate_rows_removed"] == 1
    assert summary["valid_rows"] == 1


def test_clean_boundary_gdf_converts_crs(valid_boundary_gdf):
    projected = valid_boundary_gdf.to_crs("EPSG:3857")

    cleaned, summary = clean_boundary_gdf(
        projected,
        level_name="districts",
        table_name="districts",
        source_name="geoboundaries",
        metadata={"source_url": "https://example.test", "source_date": None},
        name_candidates=["shapeName"],
        administrative_code_candidates=["shapeID"],
        source_id_candidates=["shapeID"],
        storage_crs="EPSG:4326",
        source_assumed_crs="EPSG:3857",
    )

    assert cleaned.crs.to_string() == "EPSG:4326"
    assert summary["valid_rows"] == 1


def test_geojson_dict_to_geodataframe_parses_features():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"shapeName": "Sample State", "shapeID": "IND-STATE-001"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
                },
            }
        ],
    }

    gdf = geojson_dict_to_geodataframe(payload, target_crs="EPSG:4326")

    assert len(gdf) == 1
    assert gdf.iloc[0]["shapeName"] == "Sample State"
    assert gdf.geometry.iloc[0].is_valid
    assert gdf.crs.to_string() == "EPSG:4326"