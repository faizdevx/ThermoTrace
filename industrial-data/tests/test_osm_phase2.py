from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Polygon

from src.osm.overpass import build_overpass_query, overpass_elements_to_geodataframe
from src.osm.processing import normalize_osm_gdf
from src.osm.tags import normalize_industrial_type, normalize_free_text


def test_build_overpass_query_includes_curated_tags():
    district = gpd.GeoSeries([Polygon([(77.0, 28.0), (77.0, 28.1), (77.1, 28.1), (77.1, 28.0), (77.0, 28.0)])], crs="EPSG:4326")
    config = {
        "osm": {
            "source": {"request_timeout_seconds": 120},
            "tags": {
                "landuse": ["industrial"],
                "industrial": ["*"],
                "building": ["industrial"],
                "craft": ["*"],
                "man_made": ["works"],
                "amenity": ["recycling"],
            },
        }
    }

    query = build_overpass_query(config, district.iloc[0])

    assert '["landuse"="industrial"]' in query
    assert '["building"="industrial"]' in query
    assert '["industrial"]' in query
    assert '["craft"]' in query
    assert '["man_made"~"^(works)$"]' in query


def test_overpass_response_parsing_handles_nodes_ways_and_relations():
    payload = {
        "elements": [
            {"type": "node", "id": 1, "lat": 28.5, "lon": 77.2, "tags": {"name": "Alpha Works", "industrial": "plant"}, "timestamp": "2026-01-01T00:00:00Z"},
            {"type": "way", "id": 2, "geometry": [{"lat": 28.5, "lon": 77.2}, {"lat": 28.51, "lon": 77.2}, {"lat": 28.51, "lon": 77.21}, {"lat": 28.5, "lon": 77.2}], "tags": {"name": "Beta Yard", "building": "industrial"}, "timestamp": "2026-01-02T00:00:00Z"},
            {"type": "relation", "id": 3, "geometry": [{"lat": 28.52, "lon": 77.22}, {"lat": 28.53, "lon": 77.23}], "tags": {"name": "Gamma Estate", "landuse": "industrial"}, "timestamp": "2026-01-03T00:00:00Z"},
        ]
    }

    gdf = overpass_elements_to_geodataframe(payload)

    assert len(gdf) == 3
    assert list(gdf["osm_type"]) == ["node", "way", "relation"]
    assert gdf.iloc[0]["name"] == "Alpha Works"
    assert gdf.geometry.iloc[0].geom_type == "Point"


def test_normalize_osm_gdf_repairs_and_deduplicates():
    invalid_polygon = Polygon([(77.2, 28.5), (77.3, 28.6), (77.2, 28.6), (77.3, 28.5), (77.2, 28.5)])
    gdf = gpd.GeoDataFrame(
        [
            {
                "osm_id": 10,
                "osm_type": "way",
                "name": "Alpha Industrial Estate",
                "industrial_type": "building=industrial",
                "source_timestamp": "2026-01-01T00:00:00Z",
                "raw_tags": {"name": "Alpha Industrial Estate", "building": "industrial"},
                "geometry": invalid_polygon,
            },
            {
                "osm_id": 10,
                "osm_type": "way",
                "name": "Alpha Industrial Estate",
                "industrial_type": "building=industrial",
                "source_timestamp": "2026-01-01T00:00:00Z",
                "raw_tags": {"name": "Alpha Industrial Estate", "building": "industrial"},
                "geometry": invalid_polygon,
            },
            {
                "osm_id": 11,
                "osm_type": "way",
                "name": "Alpha Indl Estate",
                "industrial_type": "building=industrial",
                "source_timestamp": "2026-01-01T00:00:00Z",
                "raw_tags": {"name": "Alpha Indl Estate", "building": "industrial"},
                "geometry": Polygon([(77.2002, 28.5002), (77.3002, 28.6002), (77.2002, 28.6002), (77.3002, 28.5002), (77.2002, 28.5002)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )

    cleaned, summary = normalize_osm_gdf(
        gdf,
        source_name="openstreetmap",
        district_name="Test District",
        district_source_id="IND-TEST-001",
        source_url="https://overpass-api.de/api/interpreter",
        exact_duplicate_coordinate_precision=6,
        probable_duplicate_distance_meters=1000,
        probable_duplicate_name_similarity=80,
        probable_duplicate_industry_similarity=80,
    )

    assert len(cleaned) == 2
    assert summary["exact_duplicates_removed"] == 1
    assert summary["probable_duplicate_rows"] >= 1
    assert cleaned.crs.to_string() == "EPSG:4326"


def test_tag_normalization_preserves_original_values():
    assert normalize_free_text("  Alpha   Works ") == "alpha works"
    assert normalize_industrial_type({"building": "industrial"}) == "building=industrial"
