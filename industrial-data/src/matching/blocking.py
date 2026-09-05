"""Scalable candidate generation for entity matching.

Problem
-------
The original ``generate_candidate_matches()`` is an O(N×M) cross-join of every
OSM record against every government record.  For a full Indian state with
50 000 OSM features and 30 000 government records, that is 1.5 billion pairs
— obviously infeasible.

Solution: Blocking
------------------
Blocking reduces the search space by grouping records into *blocks* and only
comparing records in the same block.  A good blocking strategy is one that:

1. Rarely separates true matches into different blocks (high recall).
2. Maximally reduces the number of pairs to compare (high efficiency).

Blocking strategies implemented
---------------------------------
``district``
    Records in the same district are candidates.  Requires the district
    attribute to be populated.  Very effective for government data which
    is typically district-coded.

``spatial_grid``
    The bounding box of all features is divided into a grid of cells.
    Records in the same or adjacent cells are candidates.  Grid resolution
    is configurable via ``spatial_grid_degrees`` (default 0.5°, ≈ 55 km).

``geohash``
    Features are assigned a geohash prefix of configurable length.
    Adjacent geohash cells are also included.  Default precision 4
    (≈ 40×20 km cells).

``name_prefix``
    Features with the same first character of their normalized name are
    candidates.  Fast but coarse — best used in combination with spatial
    blocking.

``industry_type``
    Features with the same taxonomy code (``normalized_industry_type``) are
    candidates.  Very selective when combined with spatial blocking.

``spatial_radius``
    For each OSM record, find all government records within
    ``max_spatial_distance_meters`` using an STRtree.  This is the most
    accurate blocking strategy and is used as the default when only one
    strategy is requested.

Combining strategies
--------------------
By default, candidate generation uses the UNION of candidates from all
enabled strategies — this maximizes recall.  The ``min_strategies`` parameter
can be set to require that a pair appears in at least N strategies before being
considered a candidate (intersection → reduces pairs, may lose recall).

Usage
-----
::

    from src.matching.blocking import generate_blocked_candidates, BlockingConfig

    config = BlockingConfig(
        strategies=["spatial_radius", "district"],
        max_spatial_distance_meters=1000.0,
        spatial_grid_degrees=0.5,
        min_strategies=1,
    )
    pairs = generate_blocked_candidates(osm_records, government_records, config)
    # pairs is a list of (osm_record, gov_record) tuples — much smaller than N×M
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
from pyproj import CRS
from shapely.strtree import STRtree

from src.matching.resolution import IndustrialRecord

logger = logging.getLogger(__name__)

# Type alias
RecordPair = tuple[IndustrialRecord, IndustrialRecord]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BlockingConfig:
    """Configuration for candidate generation / blocking.

    Attributes
    ----------
    strategies : list[str]
        Blocking strategies to apply (union of results).
        Available: "spatial_radius", "district", "spatial_grid",
                   "name_prefix", "industry_type", "geohash"
        Default: ["spatial_radius", "district"]
    max_spatial_distance_meters : float
        Used by ``spatial_radius`` strategy.
    spatial_grid_degrees : float
        Used by ``spatial_grid`` strategy.  Default 0.5° ≈ 55 km.
    geohash_precision : int
        Used by ``geohash`` strategy.  Default 4 (≈ 40×20 km).
    min_strategies : int
        A pair must appear in at least this many strategies to be included.
        1 = union (maximize recall); >1 = intersection (reduce pairs).
    max_candidates_per_osm : int | None
        Cap on candidates per OSM record.  Helps prevent pathological cases
        where a record in a very dense area generates too many candidates.
        None = no cap.
    """
    strategies: list[str] = field(
        default_factory=lambda: ["spatial_radius", "district"]
    )
    max_spatial_distance_meters: float = 1000.0
    spatial_grid_degrees: float = 0.5
    geohash_precision: int = 4
    min_strategies: int = 1
    max_candidates_per_osm: int | None = 50


@dataclass
class BlockingStats:
    """Diagnostics from a blocking run."""
    osm_count: int
    government_count: int
    max_possible_pairs: int
    candidate_pairs: int
    reduction_ratio: float
    per_strategy_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "osm_count": self.osm_count,
            "government_count": self.government_count,
            "max_possible_pairs": self.max_possible_pairs,
            "candidate_pairs": self.candidate_pairs,
            "reduction_ratio": round(self.reduction_ratio, 4),
            "per_strategy_counts": self.per_strategy_counts,
        }


# ---------------------------------------------------------------------------
# Individual blocking strategies
# ---------------------------------------------------------------------------

def _block_by_spatial_radius(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
    max_distance_meters: float,
) -> set[tuple[int, int]]:
    """STRtree spatial blocking: pairs within max_distance_meters.

    Returns set of (osm_index, gov_index) pairs.
    """
    if not osm_records or not gov_records:
        return set()

    # Project to metric CRS
    all_geoms = [r.geometry for r in osm_records + gov_records if r.geometry is not None]
    if not all_geoms:
        return set()

    geo_series = gpd.GeoSeries(all_geoms, crs="EPSG:4326")
    metric_crs = geo_series.estimate_utm_crs() or CRS.from_epsg(3857)

    osm_geoms_proj = [
        gpd.GeoSeries([r.geometry], crs="EPSG:4326").to_crs(metric_crs).iloc[0]
        if r.geometry else None
        for r in osm_records
    ]
    gov_geoms_proj = [
        gpd.GeoSeries([r.geometry], crs="EPSG:4326").to_crs(metric_crs).iloc[0]
        if r.geometry else None
        for r in gov_records
    ]

    # Build STRtree on government geometries
    gov_geoms_valid = [g for g in gov_geoms_proj if g is not None]
    gov_index_map = [i for i, g in enumerate(gov_geoms_proj) if g is not None]

    if not gov_geoms_valid:
        return set()

    tree = STRtree(gov_geoms_valid)
    pairs: set[tuple[int, int]] = set()

    for osm_i, osm_geom in enumerate(osm_geoms_proj):
        if osm_geom is None:
            continue
        buffered = osm_geom.buffer(max_distance_meters)
        hits = tree.query(buffered, predicate="intersects")
        for tree_j in hits:
            gov_j = gov_index_map[int(tree_j)]
            pairs.add((osm_i, gov_j))

    return pairs


def _block_by_district(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
) -> set[tuple[int, int]]:
    """Block by matching district attribute."""
    pairs: set[tuple[int, int]] = set()

    # Build dict: normalized_district → list of gov indices
    gov_by_district: dict[str, list[int]] = {}
    for j, rec in enumerate(gov_records):
        d = (rec.district or "").strip().lower()
        if d:
            gov_by_district.setdefault(d, []).append(j)

    if not gov_by_district:
        return pairs

    for i, osm_rec in enumerate(osm_records):
        d = (osm_rec.district or "").strip().lower()
        if d and d in gov_by_district:
            for j in gov_by_district[d]:
                pairs.add((i, j))

    return pairs


def _block_by_spatial_grid(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
    grid_degrees: float,
) -> set[tuple[int, int]]:
    """Block by spatial grid cell (including adjacent cells)."""
    pairs: set[tuple[int, int]] = set()

    def _cell(lon: float, lat: float) -> tuple[int, int]:
        return (int(math.floor(lon / grid_degrees)), int(math.floor(lat / grid_degrees)))

    # Build dict: cell → list of gov indices
    gov_by_cell: dict[tuple[int, int], list[int]] = {}
    for j, rec in enumerate(gov_records):
        if rec.geometry is None:
            continue
        cx, cy = rec.geometry.centroid.x, rec.geometry.centroid.y
        cell = _cell(cx, cy)
        gov_by_cell.setdefault(cell, []).append(j)

    for i, osm_rec in enumerate(osm_records):
        if osm_rec.geometry is None:
            continue
        cx, cy = osm_rec.geometry.centroid.x, osm_rec.geometry.centroid.y
        base_cell = _cell(cx, cy)
        # Check base cell and 8 adjacent cells
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor = (base_cell[0] + dx, base_cell[1] + dy)
                for j in gov_by_cell.get(neighbor, []):
                    pairs.add((i, j))

    return pairs


def _block_by_name_prefix(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
    prefix_length: int = 2,
) -> set[tuple[int, int]]:
    """Block by normalized name prefix."""
    pairs: set[tuple[int, int]] = set()

    gov_by_prefix: dict[str, list[int]] = {}
    for j, rec in enumerate(gov_records):
        name = (rec.normalized_name or "").strip()
        prefix = name[:prefix_length] if len(name) >= prefix_length else name
        if prefix:
            gov_by_prefix.setdefault(prefix, []).append(j)

    for i, osm_rec in enumerate(osm_records):
        name = (osm_rec.normalized_name or "").strip()
        prefix = name[:prefix_length] if len(name) >= prefix_length else name
        if prefix and prefix in gov_by_prefix:
            for j in gov_by_prefix[prefix]:
                pairs.add((i, j))

    return pairs


def _block_by_industry_type(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
) -> set[tuple[int, int]]:
    """Block by normalized industry type."""
    pairs: set[tuple[int, int]] = set()

    gov_by_type: dict[str, list[int]] = {}
    for j, rec in enumerate(gov_records):
        t = (rec.normalized_industry_type or "").strip().lower()
        if t:
            gov_by_type.setdefault(t, []).append(j)

    for i, osm_rec in enumerate(osm_records):
        t = (osm_rec.normalized_industry_type or "").strip().lower()
        if t and t in gov_by_type:
            for j in gov_by_type[t]:
                pairs.add((i, j))

    return pairs


def _block_by_geohash(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
    precision: int = 4,
) -> set[tuple[int, int]]:
    """Block by geohash prefix (with adjacent cells if pygeohash is available).

    Falls back to spatial_grid if pygeohash is not installed.
    """
    try:
        import pygeohash as pgh  # type: ignore[import]
    except ImportError:
        # Graceful fallback: approximate with spatial_grid
        logger.debug("pygeohash not installed; falling back to spatial_grid for geohash blocking")
        grid_deg = 0.5 if precision <= 4 else 0.1
        return _block_by_spatial_grid(osm_records, gov_records, grid_deg)

    pairs: set[tuple[int, int]] = set()

    def _gh(rec: IndustrialRecord) -> str | None:
        if rec.geometry is None:
            return None
        try:
            return pgh.encode(rec.geometry.centroid.y, rec.geometry.centroid.x, precision)
        except Exception:
            return None

    gov_by_gh: dict[str, list[int]] = {}
    for j, rec in enumerate(gov_records):
        gh = _gh(rec)
        if gh:
            gov_by_gh.setdefault(gh, []).append(j)

    for i, osm_rec in enumerate(osm_records):
        gh = _gh(osm_rec)
        if gh:
            # Check cell + adjacent cells
            try:
                neighbors = {gh} | set(pgh.neighbors(gh).values())
            except Exception:
                neighbors = {gh}
            for n in neighbors:
                for j in gov_by_gh.get(n, []):
                    pairs.add((i, j))

    return pairs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_STRATEGY_MAP = {
    "spatial_radius": _block_by_spatial_radius,
    "district": _block_by_district,
    "spatial_grid": _block_by_spatial_grid,
    "name_prefix": _block_by_name_prefix,
    "industry_type": _block_by_industry_type,
    "geohash": _block_by_geohash,
}


def generate_blocked_candidates(
    osm_records: list[IndustrialRecord],
    gov_records: list[IndustrialRecord],
    config: BlockingConfig,
) -> list[RecordPair]:
    """Generate candidate (OSM, government) pairs using blocking.

    Parameters
    ----------
    osm_records : list[IndustrialRecord]
    gov_records : list[IndustrialRecord]
    config : BlockingConfig

    Returns
    -------
    list[RecordPair]
        Deduplicated list of (osm_record, gov_record) candidate pairs.
        Always a strict subset of the O(N×M) cross-product.
    """
    if not osm_records or not gov_records:
        stats = BlockingStats(
            osm_count=len(osm_records),
            government_count=len(gov_records),
            max_possible_pairs=0,
            candidate_pairs=0,
            reduction_ratio=1.0,
            per_strategy_counts={},
        )
        return [], stats

    per_strategy_counts: dict[str, int] = {}
    pair_strategy_count: dict[tuple[int, int], int] = {}

    for strategy_name in config.strategies:
        strategy_fn = _STRATEGY_MAP.get(strategy_name)
        if strategy_fn is None:
            logger.warning("Unknown blocking strategy: '%s' — skipping", strategy_name)
            continue

        try:
            if strategy_name == "spatial_radius":
                raw_pairs = strategy_fn(osm_records, gov_records, config.max_spatial_distance_meters)
            elif strategy_name == "spatial_grid":
                raw_pairs = strategy_fn(osm_records, gov_records, config.spatial_grid_degrees)
            elif strategy_name == "geohash":
                raw_pairs = strategy_fn(osm_records, gov_records, config.geohash_precision)
            else:
                raw_pairs = strategy_fn(osm_records, gov_records)
        except Exception as exc:
            logger.warning("Blocking strategy '%s' failed: %s", strategy_name, exc)
            raw_pairs = set()

        per_strategy_counts[strategy_name] = len(raw_pairs)
        for pair in raw_pairs:
            pair_strategy_count[pair] = pair_strategy_count.get(pair, 0) + 1

    # Apply min_strategies filter
    min_s = max(1, config.min_strategies)
    selected_pairs = {p for p, count in pair_strategy_count.items() if count >= min_s}

    # Apply per-OSM cap
    if config.max_candidates_per_osm is not None:
        capped: set[tuple[int, int]] = set()
        osm_candidate_counts: dict[int, int] = {}
        for osm_i, gov_j in sorted(selected_pairs):
            current = osm_candidate_counts.get(osm_i, 0)
            if current < config.max_candidates_per_osm:
                capped.add((osm_i, gov_j))
                osm_candidate_counts[osm_i] = current + 1
        selected_pairs = capped

    result: list[RecordPair] = [
        (osm_records[osm_i], gov_records[gov_j])
        for osm_i, gov_j in selected_pairs
    ]

    max_pairs = len(osm_records) * len(gov_records)
    reduction = 1.0 - (len(result) / max_pairs) if max_pairs > 0 else 0.0

    stats = BlockingStats(
        osm_count=len(osm_records),
        government_count=len(gov_records),
        max_possible_pairs=max_pairs,
        candidate_pairs=len(result),
        reduction_ratio=reduction,
        per_strategy_counts=per_strategy_counts,
    )
    logger.info(
        "Blocking: %d OSM × %d gov = %d max pairs → %d candidates (%.1f%% reduction). "
        "Strategies: %s",
        len(osm_records), len(gov_records), max_pairs, len(result),
        reduction * 100,
        per_strategy_counts,
    )

    return result, stats
