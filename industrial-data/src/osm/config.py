from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OsmSourceConfig:
    provider: str
    overpass_url: str
    backup_overpass_urls: list[str]
    request_timeout_seconds: int
    retry_attempts: int
    retry_backoff_seconds: int
    query_pause_seconds: int


@dataclass(frozen=True)
class OsmCleaningConfig:
    exact_duplicate_coordinate_precision: int
    probable_duplicate_distance_meters: float
    probable_duplicate_name_similarity: int
    probable_duplicate_industry_similarity: int


def get_osm_source_config(config: dict[str, Any]) -> OsmSourceConfig:
    source = config["osm"]["source"]
    return OsmSourceConfig(
        provider=source["provider"],
        overpass_url=source["overpass_url"],
        backup_overpass_urls=list(source.get("backup_overpass_urls", [])),
        request_timeout_seconds=int(source.get("request_timeout_seconds", 180)),
        retry_attempts=int(source.get("retry_attempts", 3)),
        retry_backoff_seconds=int(source.get("retry_backoff_seconds", 2)),
        query_pause_seconds=int(source.get("query_pause_seconds", 1)),
    )


def get_osm_cleaning_config(config: dict[str, Any]) -> OsmCleaningConfig:
    cleaning = config["osm"]["cleaning"]
    return OsmCleaningConfig(
        exact_duplicate_coordinate_precision=int(cleaning.get("exact_duplicate_coordinate_precision", 6)),
        probable_duplicate_distance_meters=float(cleaning.get("probable_duplicate_distance_meters", 50)),
        probable_duplicate_name_similarity=int(cleaning.get("probable_duplicate_name_similarity", 88)),
        probable_duplicate_industry_similarity=int(cleaning.get("probable_duplicate_industry_similarity", 80)),
    )
