"""Deduplication sub-package.

Provides source-level, geographic, and entity-level duplicate detection.
"""
from src.deduplication.source_dedup import (
    DeduplicationSummary,
    dedup_by_geometry,
    dedup_government_by_source_id,
    dedup_osm_by_id,
)
from src.deduplication.geographic_dedup import (
    GeographicDedupResult,
    detect_geographic_duplicates,
)

__all__ = [
    "DeduplicationSummary",
    "GeographicDedupResult",
    "dedup_by_geometry",
    "dedup_government_by_source_id",
    "dedup_osm_by_id",
    "detect_geographic_duplicates",
]
