"""Master industrial site dataset assembly.

This module is the canonical implementation of Phase 10.  It takes the outputs
of entity matching and produces a master ``industrial_sites`` GeoDataFrame that:

1. Contains **one row per industrial site** — a site is a cluster of one or
   more matching source records (OSM + government).
2. Preserves **all unmatched records** — an unmatched OSM feature becomes a
   singleton site.  An unmatched government record becomes a singleton site.
3. **Never deletes original source records** — ``osm_industries`` and the
   government tables are untouched.
4. Carries **full provenance**: source keys, OSM IDs, government IDs, match
   scores, method, confidence, review flag.
5. Uses **stable site_ids** via ``SiteIdentityIndex`` (Phase 9) so that
   refresh runs update existing rows instead of creating duplicates.

Master site schema (mirrors ``industrial_sites`` table)
---------------------------------------------------------
Column                   | Type       | Source
-------------------------|------------|--------------------------------
site_id                  | TEXT       | uuid5 (stable, anchor-based)
name                     | TEXT       | most common across cluster
normalized_name          | TEXT       | most common normalized
industry_type            | TEXT       | most common raw
normalized_industry_type | TEXT       | most common taxonomy code
geometry                 | GEOMETRY   | union of cluster geometries
state                    | TEXT       | most common
district                 | TEXT       | most common
address                  | TEXT       | most common
establishment_status     | TEXT       | most common
establishment_date       | DATE       | earliest in cluster
extraction_date          | DATE       | latest in cluster
first_seen               | DATE       | earliest in cluster
last_seen                | DATE       | latest in cluster
operational_status       | TEXT       | most common
osm_ids                  | JSONB      | list of OSM IDs
government_ids           | JSONB      | list of gov source IDs
matched_source_ids       | JSONB      | {osm: [...], government: [...], all: [...]}
source_count             | INTEGER    | number of source records
source_confidence        | FLOAT      | avg match score within cluster
match_score              | FLOAT      | max match score in cluster
match_method             | TEXT       | concatenated methods
match_confidence         | TEXT       | automatic_match / review / singleton
review_required          | BOOLEAN    | any uncertain matches in cluster
last_verified            | DATE       | most recent source date
created_at               | TIMESTAMPTZ|
updated_at               | TIMESTAMPTZ|

Singleton sites (unmatched records)
------------------------------------
When a record has no automatic_match partner, it becomes a singleton site:
  - match_confidence = "singleton"
  - match_score = 1.0, source_confidence = 1.0
  - review_required = False
  - osm_ids / government_ids contains only that record's ID

Public API
----------
``assemble_master_sites(osm_gdf, government_gdf, candidate_matches,
                        government_table_name, identity_index)``
    → ``(master_gdf, source_records_df)``

``MasterSiteRow``
    NamedTuple with all columns for type-safe construction.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any, Iterable, NamedTuple

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from src.matching.resolution import (
    CandidateMatch,
    IndustrialRecord,
    _cluster_records,
    _normalized_records_from_government_gdf,
    _normalized_records_from_osm_gdf,
)
from src.matching.site_identity import (
    SiteIdentityIndex,
    build_site_source_records,
    resolve_site_id,
)
from src.temporal import current_extraction_date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _pick_most_common(values: Iterable[Any]) -> Any:
    """Return the most frequent non-null value, or None."""
    filtered = [v for v in values if v is not None and not (isinstance(v, float) and pd.isna(v))]
    if not filtered:
        return None
    return Counter(filtered).most_common(1)[0][0]


def _pick_earliest(values: Iterable[Any]) -> date | None:
    dates = [v for v in values if v is not None]
    return min(dates) if dates else None


def _pick_latest(values: Iterable[Any]) -> date | None:
    dates = [v for v in values if v is not None]
    return max(dates) if dates else None


def _merge_geometry(geometries: Iterable[Any]) -> Any:
    geoms = [g for g in geometries if g is not None and not getattr(g, "is_empty", True)]
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    return unary_union(geoms)


def _cluster_match_fields(
    cluster: list[IndustrialRecord],
    candidate_matches: list[CandidateMatch],
) -> tuple[float, float, str, bool, str]:
    """Return (source_confidence, match_score, match_method, review_required, match_confidence_label)."""
    cluster_keys = {r.source_key for r in cluster}
    relevant = [
        c for c in candidate_matches
        if {c.osm_source_key, c.government_source_key} <= cluster_keys
    ]

    if not relevant:
        # Singleton
        return 1.0, 1.0, "singleton", False, "singleton"

    auto = [c for c in relevant if c.match_confidence == "automatic_match"]
    uncertain = [c for c in relevant if c.match_confidence == "uncertain_review"]
    review_required = len(uncertain) > 0

    if auto:
        scores = [c.match_score for c in auto]
        methods = list({c.match_method for c in auto})
        avg_score = sum(scores) / len(scores)
        max_score = max(scores)
        method = "+".join(sorted(set(m for full in methods for m in full.split("+") if m)))
        confidence_label = "review_required" if review_required else "automatic_match"
        return avg_score, max_score, method, review_required, confidence_label

    # Only uncertain matches
    scores = [c.match_score for c in uncertain]
    return sum(scores) / len(scores), max(scores), "uncertain", True, "review_required"


# ---------------------------------------------------------------------------
# Core assembly
# ---------------------------------------------------------------------------

def assemble_master_sites(
    osm_gdf: gpd.GeoDataFrame,
    government_gdf: gpd.GeoDataFrame,
    candidate_matches: list[CandidateMatch],
    government_table_name: str,
    identity_index: SiteIdentityIndex,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Assemble the master industrial_sites table.

    Parameters
    ----------
    osm_gdf : GeoDataFrame
        Normalized OSM records from ``normalize_osm_gdf()``.
    government_gdf : GeoDataFrame
        Normalized government records (may be empty).
    candidate_matches : list[CandidateMatch]
        Output of ``generate_candidate_matches()``.
    government_table_name : str
        Source table name for government records.
    identity_index : SiteIdentityIndex
        Existing site identity index (from previous master table run).

    Returns
    -------
    (master_gdf, source_records_df)
        master_gdf: GeoDataFrame ready for ``write_master_sites()``.
        source_records_df: DataFrame ready for ``site_source_records`` insert.
    """
    extraction_date = current_extraction_date()
    now = datetime.now(UTC)

    # Normalize all records into IndustrialRecord objects
    osm_records = _normalized_records_from_osm_gdf(osm_gdf)
    gov_records = _normalized_records_from_government_gdf(government_gdf, government_table_name)
    all_records = osm_records + gov_records

    if not all_records:
        empty = gpd.GeoDataFrame(columns=_MASTER_COLUMNS, geometry="geometry", crs="EPSG:4326")
        return empty, pd.DataFrame()

    # Cluster records using the existing union-find from resolution
    # Only automatic_match edges form clusters; others remain singletons
    clusters = _cluster_records(all_records, candidate_matches)

    master_rows: list[dict[str, Any]] = []
    source_record_rows: list[dict[str, Any]] = []

    for cluster in clusters:
        if not cluster:
            continue

        # --- Stable site_id ---
        site_id = resolve_site_id(cluster, identity_index)

        # --- Aggregate fields ---
        names = [r.name for r in cluster]
        norm_names = [r.normalized_name for r in cluster]
        industry_types = [r.industry_type for r in cluster]
        norm_industry_types = [r.normalized_industry_type for r in cluster]
        states = [r.state for r in cluster]
        districts = [r.district for r in cluster]
        addresses = [r.address for r in cluster]
        op_statuses = [r.operational_status for r in cluster]
        first_seen_vals = [r.first_seen for r in cluster if r.first_seen]
        last_seen_vals = [r.last_seen for r in cluster if r.last_seen]
        source_dates = [r.source_date for r in cluster if r.source_date]
        source_timestamps = [r.source_timestamp.date() for r in cluster if r.source_timestamp]

        geometry = _merge_geometry(r.geometry for r in cluster)
        if geometry is None or getattr(geometry, "is_empty", True):
            logger.warning("Cluster site_id=%s has no valid geometry — skipping", site_id)
            continue

        osm_ids = [int(r.source_id) for r in cluster if r.source_type == "osm"]
        gov_ids = [r.source_id for r in cluster if r.source_type == "government" and r.source_id]

        matched_source_ids = {
            "osm": [r.source_key for r in cluster if r.source_type == "osm"],
            "government": [r.source_key for r in cluster if r.source_type == "government"],
            "all": [r.source_key for r in cluster],
        }

        source_confidence, match_score, match_method, review_required, match_confidence = \
            _cluster_match_fields(cluster, candidate_matches)

        last_verified = _pick_latest(source_dates + source_timestamps) if source_dates or source_timestamps else None

        master_rows.append({
            "site_id": site_id,
            "name": _pick_most_common(names),
            "normalized_name": _pick_most_common(norm_names),
            "industry_type": _pick_most_common(industry_types),
            "normalized_industry_type": _pick_most_common(norm_industry_types),
            "geometry": geometry,
            "state": _pick_most_common(states),
            "district": _pick_most_common(districts),
            "address": _pick_most_common(addresses),
            "establishment_status": _pick_most_common(op_statuses),
            "establishment_date": _pick_earliest(source_dates),
            "extraction_date": _pick_latest(r.extraction_date for r in cluster if r.extraction_date) or extraction_date,
            "first_seen": _pick_earliest(first_seen_vals) or extraction_date,
            "last_seen": _pick_latest(last_seen_vals) or extraction_date,
            "operational_status": _pick_most_common(op_statuses),
            "osm_ids": osm_ids,
            "government_ids": gov_ids,
            "matched_source_ids": matched_source_ids,
            "source_count": len(cluster),
            "source_confidence": source_confidence,
            "match_score": match_score,
            "match_method": match_method,
            "match_confidence": match_confidence,
            "review_required": review_required,
            "last_verified": last_verified,
            "created_at": now,
            "updated_at": now,
        })

        # Source record provenance rows
        source_record_rows.extend(
            build_site_source_records(site_id, cluster, extraction_date)
        )

    master_gdf = gpd.GeoDataFrame(master_rows, geometry="geometry", crs="EPSG:4326") \
        if master_rows \
        else gpd.GeoDataFrame(columns=_MASTER_COLUMNS, geometry="geometry", crs="EPSG:4326")

    source_records_df = pd.DataFrame(source_record_rows) if source_record_rows else pd.DataFrame()

    logger.info(
        "Master assembly: %d OSM + %d gov records → %d sites "
        "(%d singletons, %d multi-source)",
        len(osm_records), len(gov_records), len(master_gdf),
        sum(1 for r in master_rows if r["source_count"] == 1),
        sum(1 for r in master_rows if r["source_count"] > 1),
    )

    return master_gdf, source_records_df


# Columns expected by write_master_sites() and the DB table
_MASTER_COLUMNS = [
    "site_id", "name", "normalized_name", "industry_type", "normalized_industry_type",
    "geometry", "state", "district", "address", "establishment_status",
    "establishment_date", "extraction_date", "first_seen", "last_seen",
    "operational_status", "osm_ids", "government_ids", "matched_source_ids",
    "source_count", "source_confidence", "match_score", "match_method",
    "match_confidence", "review_required", "last_verified", "created_at", "updated_at",
]
