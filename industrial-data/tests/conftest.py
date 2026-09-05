from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon


@pytest.fixture()
def valid_boundary_gdf() -> gpd.GeoDataFrame:
    geometry = Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])
    return gpd.GeoDataFrame(
        [
            {
                "shapeName": "Test District",
                "shapeID": "IND-TEST-001",
                "geometry": geometry,
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )


@pytest.fixture()
def invalid_boundary_gdf() -> gpd.GeoDataFrame:
    geometry = Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])
    return gpd.GeoDataFrame(
        [
            {
                "shapeName": "Invalid Boundary",
                "shapeID": "IND-INVALID-001",
                "geometry": geometry,
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )