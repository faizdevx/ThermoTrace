from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import shape
from urllib3.util.retry import Retry


@dataclass(frozen=True)
class GeoBoundariesMetadata:
    level: str
    source_url: str
    download_url: str | None
    raw_metadata: dict[str, Any]


def create_retry_session(total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def build_metadata_url(api_base_url: str, dataset_type: str, country_iso3: str, level: str) -> str:
    return f"{api_base_url.rstrip('/')}/{dataset_type}/{country_iso3}/{level}/"


def fetch_geoboundaries_metadata(
    api_base_url: str,
    dataset_type: str,
    country_iso3: str,
    level: str,
    session: requests.Session | None = None,
) -> GeoBoundariesMetadata:
    request_session = session or create_retry_session()
    metadata_url = build_metadata_url(api_base_url, dataset_type, country_iso3, level)
    response = request_session.get(metadata_url, timeout=60)
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        payload = payload[0] if payload else {}

    download_url = payload.get("gjDownloadURL") or payload.get("downloadURL") or payload.get("boundaryDownloadURL")
    return GeoBoundariesMetadata(
        level=level,
        source_url=metadata_url,
        download_url=download_url,
        raw_metadata=payload,
    )


def download_json_payload(download_url: str, session: requests.Session | None = None) -> dict[str, Any]:
    request_session = session or create_retry_session()
    response = request_session.get(download_url, timeout=120)
    response.raise_for_status()
    return response.json()


def geojson_dict_to_geodataframe(payload: dict[str, Any], target_crs: str | None = None) -> gpd.GeoDataFrame:
    features = payload.get("features", [])
    rows: list[dict[str, Any]] = []
    geometries = []

    for feature in features:
        properties = dict(feature.get("properties") or {})
        geometry = shape(feature.get("geometry"))
        rows.append(properties)
        geometries.append(geometry)

    return gpd.GeoDataFrame(rows, geometry=geometries, crs=target_crs)


def fetch_boundary_layer(
    api_base_url: str,
    dataset_type: str,
    country_iso3: str,
    level: str,
    target_crs: str,
    session: requests.Session | None = None,
) -> tuple[GeoBoundariesMetadata, gpd.GeoDataFrame]:
    metadata = fetch_geoboundaries_metadata(api_base_url, dataset_type, country_iso3, level, session=session)
    if not metadata.download_url:
        raise ValueError(f"No download URL found in GeoBoundaries metadata for {level}")

    payload = download_json_payload(metadata.download_url, session=session)
    gdf = geojson_dict_to_geodataframe(payload, target_crs=target_crs)
    return metadata, gdf