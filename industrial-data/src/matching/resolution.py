from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from rapidfuzz import fuzz
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from src.government.processing import normalize_classification_value
from src.osm.tags import normalize_free_text
from src.temporal import current_extraction_date

from .config import MatchingSourceConfig, MatchingThresholdConfig, MatchingWeightConfig


# Lazy import to avoid circular dependency at module load time
def _get_blocking_module():
    from src.matching.blocking import BlockingConfig, generate_blocked_candidates
    return BlockingConfig, generate_blocked_candidates


@dataclass(frozen=True)
class IndustrialRecord:
    source_type: str
    source_key: str
    source_id: str
    source_table: str
    name: str | None
    normalized_name: str | None
    industry_type: str | None
    normalized_industry_type: str | None
    address: str | None
    normalized_address: str | None
    state: str | None
    district: str | None
    geometry: BaseGeometry
    source_date: date | None
    source_timestamp: datetime | None
    extraction_date: date | None = None
    first_seen: date | None = None
    last_seen: date | None = None
    operational_status: str | None = None
    raw_source_id: str | None = None


@dataclass(frozen=True)
class CandidateMatch:
    candidate_id: str
    osm_source_key: str
    government_source_key: str
    osm_id: int
    government_source_id: str | None
    government_table: str
    spatial_distance_meters: float
    name_score: float
    industry_score: float
    address_score: float
    match_score: float
    match_confidence: str
    match_method: str
    review_required: bool
    matched_source_ids: dict[str, Any]
    state_consistent: bool
    district_consistent: bool
    extraction_date: date | None = None
    site_id: str | None = None


def _clean_match_text(value: Any) -> str | None:
    normalized = normalize_free_text(value)
    if normalized in {"unknown", "not_applicable"}:
        return None
    return normalized


def _clean_identifier(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _parse_date_value(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _parse_datetime_value(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _text_similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return fuzz.token_set_ratio(left, right) / 100.0


_GENERIC_INDUSTRIAL_TYPES = {
    "industrial",
    "building industrial",
    "landuse industrial",
    "factory",
    "works",
    "plant",
    "manufacturing",
    "industry",
    "industrial estate",
    "industrial area",
    "general industries",
    "general",
    "unit",
}


def _is_generic_industrial(text: str | None) -> bool:
    if not text:
        return False
    cleaned = text.strip().lower()
    return cleaned in _GENERIC_INDUSTRIAL_TYPES


def _distance_meters(left_geometry: BaseGeometry, right_geometry: BaseGeometry, metric_crs: CRS) -> float:
    left_series = gpd.GeoSeries([left_geometry], crs="EPSG:4326").to_crs(metric_crs)
    right_series = gpd.GeoSeries([right_geometry], crs="EPSG:4326").to_crs(metric_crs)
    return float(left_series.iloc[0].distance(right_series.iloc[0]))


def _shared_state_district(left_state: str | None, right_state: str | None, left_district: str | None, right_district: str | None) -> tuple[bool, bool]:
    state_consistent = True
    district_consistent = True

    if left_state and right_state and left_state != right_state:
        state_consistent = False
    if left_district and right_district and left_district != right_district:
        district_consistent = False

    return state_consistent, district_consistent


def _normalized_records_from_osm_gdf(gdf: gpd.GeoDataFrame) -> list[IndustrialRecord]:
    records: list[IndustrialRecord] = []
    for _, row in gdf.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        source_id = f"{row['osm_type']}:{row['osm_id']}"
        normalized_name = _clean_match_text(row.get("normalized_name") or row.get("name"))
        normalized_industry_type = _clean_match_text(row.get("normalized_industrial_type") or row.get("industrial_type"))
        records.append(
            IndustrialRecord(
                source_type="osm",
                source_key=f"osm:{source_id}",
                source_id=str(row["osm_id"]),
                source_table="osm_industries",
                name=row.get("name"),
                normalized_name=normalized_name,
                industry_type=row.get("industrial_type"),
                normalized_industry_type=normalized_industry_type,
                address=None,
                normalized_address=None,
                state=_clean_match_text(row.get("state")),
                district=_clean_match_text(row.get("district_name") or row.get("district")),
                geometry=geometry,
                source_date=_parse_date_value(row.get("source_timestamp")),
                source_timestamp=_parse_datetime_value(row.get("source_timestamp")),
                extraction_date=current_extraction_date(),
                first_seen=_parse_date_value(row.get("first_seen")) or current_extraction_date(),
                last_seen=_parse_date_value(row.get("last_seen")) or current_extraction_date(),
                operational_status=_clean_match_text(row.get("operational_status") or row.get("status")),
            )
        )
    return records


def _normalized_records_from_government_gdf(gdf: gpd.GeoDataFrame, source_table_name: str) -> list[IndustrialRecord]:
    records: list[IndustrialRecord] = []
    for _, row in gdf.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        source_id = _clean_identifier(row.get("source_id") or row.get("id"))
        normalized_name = _clean_match_text(row.get("name"))
        normalized_industry_type = _clean_match_text(row.get("industry_type"))
        records.append(
            IndustrialRecord(
                source_type="government",
                source_key=f"government:{source_table_name}:{source_id}",
                source_id=source_id or str(row.get("id")),
                source_table=source_table_name,
                name=row.get("name"),
                normalized_name=normalized_name,
                industry_type=row.get("industry_type"),
                normalized_industry_type=normalized_industry_type,
                address=row.get("address"),
                normalized_address=_clean_match_text(row.get("address")),
                state=_clean_match_text(row.get("state")),
                district=_clean_match_text(row.get("district")),
                geometry=geometry,
                source_date=_parse_date_value(row.get("source_date") or row.get("establishment_date")),
                source_timestamp=_parse_datetime_value(row.get("source_date")),
                extraction_date=_parse_date_value(row.get("extraction_date")) or current_extraction_date(),
                first_seen=_parse_date_value(row.get("first_seen")) or _parse_date_value(row.get("extraction_date")) or current_extraction_date(),
                last_seen=_parse_date_value(row.get("last_seen")) or _parse_date_value(row.get("extraction_date")) or current_extraction_date(),
                operational_status=normalize_classification_value(
                    row.get("operational_status") or row.get("status"),
                    missing_values=[""],
                    unknown_values=["unknown"],
                    not_applicable_values=["n/a"],
                ),
            )
        )
    return records


def normalize_source_records(osm_gdf: gpd.GeoDataFrame, government_gdf: gpd.GeoDataFrame, government_table_name: str) -> list[IndustrialRecord]:
    return [*_normalized_records_from_osm_gdf(osm_gdf), *_normalized_records_from_government_gdf(government_gdf, government_table_name)]


def generate_candidate_matches(
    osm_records: list[IndustrialRecord],
    government_records: list[IndustrialRecord],
    source_config: MatchingSourceConfig,
    weight_config: MatchingWeightConfig,
    threshold_config: MatchingThresholdConfig,
    *,
    blocking_strategies: list[str] | None = None,
    max_candidates_per_osm: int | None = 50,
) -> list[CandidateMatch]:
    """Generate candidate matches using blocking for scalability.

    Matching pipeline:
      OSM Record
        ↓ Blocking (district / spatial_radius / grid / name_prefix / industry)
        ↓ Small candidate set  (vs original O(N×M))
        ↓ Spatial score
        ↓ Name similarity (RapidFuzz token_set_ratio)
        ↓ Address score
        ↓ Industry score
        ↓ Weighted match_score
        ↓ Confidence classification + review_required

    Parameters
    ----------
    osm_records, government_records : list[IndustrialRecord]
    source_config : MatchingSourceConfig
    weight_config : MatchingWeightConfig
    threshold_config : MatchingThresholdConfig
    blocking_strategies : list[str] | None
        Override the default blocking strategies.
        Default: ["spatial_radius", "district"]
    max_candidates_per_osm : int | None
        Maximum candidates per OSM record.  None = no cap.

    Returns
    -------
    list[CandidateMatch]
    """
    if not osm_records or not government_records:
        return []

    BlockingConfig, generate_blocked_candidates = _get_blocking_module()

    strategies = blocking_strategies or ["spatial_radius"]
    blocking_config = BlockingConfig(
        strategies=strategies,
        max_spatial_distance_meters=source_config.max_spatial_distance_meters,
        min_strategies=1,
        max_candidates_per_osm=max_candidates_per_osm,
    )

    candidate_pairs, blocking_stats = generate_blocked_candidates(
        osm_records, government_records, blocking_config
    )

    if not candidate_pairs:
        return []

    # Compute metric CRS once for all pairs
    sample_geoms = [
        r.geometry for r in osm_records[:5] + government_records[:5]
        if r.geometry is not None
    ]
    if sample_geoms:
        geo_series = gpd.GeoSeries(sample_geoms, crs="EPSG:4326")
        metric_crs = geo_series.estimate_utm_crs() or CRS.from_epsg(3857)
    else:
        metric_crs = CRS.from_epsg(3857)

    blocking_tag = "+".join(sorted(set(strategies)))
    candidates: list[CandidateMatch] = []

    for osm_record, government_record in candidate_pairs:
        state_consistent, district_consistent = _shared_state_district(
            osm_record.state,
            government_record.state,
            osm_record.district,
            government_record.district,
        )
        if source_config.require_state_district_consistency and (not state_consistent or not district_consistent):
            continue

        distance_meters = _distance_meters(osm_record.geometry, government_record.geometry, metric_crs)
        name_score = _text_similarity(osm_record.normalized_name, government_record.normalized_name)
        address_score = _text_similarity(osm_record.normalized_address, government_record.normalized_address)
        industry_score = _text_similarity(osm_record.normalized_industry_type, government_record.normalized_industry_type)

        # Handle generic industrial terminology (e.g. building=industrial vs Factory)
        if industry_score < 0.5 and osm_record.normalized_industry_type and government_record.normalized_industry_type:
            if _is_generic_industrial(osm_record.normalized_industry_type) and _is_generic_industrial(government_record.normalized_industry_type):
                industry_score = 1.0

        candidate_reasons = []
        if distance_meters <= source_config.max_spatial_distance_meters:
            candidate_reasons.append("spatial")
        if name_score >= (source_config.candidate_name_similarity_min / 100.0):
            candidate_reasons.append("name")
        if address_score >= (source_config.candidate_address_similarity_min / 100.0):
            candidate_reasons.append("address")
        if industry_score >= (source_config.candidate_industry_similarity_min / 100.0):
            candidate_reasons.append("industry")

        if not candidate_reasons:
            continue

        spatial_score = 0.0
        if source_config.max_spatial_distance_meters > 0:
            spatial_score = max(0.0, 1.0 - min(distance_meters / source_config.max_spatial_distance_meters, 1.0))

        # Dynamically normalize weights over available comparable attributes
        total_weight = 0.0
        weighted_score = 0.0

        if source_config.max_spatial_distance_meters > 0 and weight_config.spatial > 0:
            weighted_score += spatial_score * weight_config.spatial
            total_weight += weight_config.spatial

        if weight_config.name > 0:
            weighted_score += name_score * weight_config.name
            total_weight += weight_config.name

        if weight_config.industry > 0:
            if osm_record.normalized_industry_type and government_record.normalized_industry_type:
                weighted_score += industry_score * weight_config.industry
                total_weight += weight_config.industry

        if weight_config.address > 0:
            if osm_record.normalized_address and government_record.normalized_address:
                weighted_score += address_score * weight_config.address
                total_weight += weight_config.address

        match_score = (weighted_score / total_weight) if total_weight > 0 else 0.0
        match_confidence = classify_match_confidence(match_score, threshold_config)
        review_required = match_confidence == "uncertain_review"
        # Preserve blocking provenance in match_method
        match_method = "+".join(sorted(set(candidate_reasons))) + f"[blocked:{blocking_tag}]"
        candidate_id = str(uuid5(NAMESPACE_URL, f"candidate:{osm_record.source_key}|{government_record.source_key}"))

        candidates.append(
            CandidateMatch(
                candidate_id=candidate_id,
                osm_source_key=osm_record.source_key,
                government_source_key=government_record.source_key,
                osm_id=int(osm_record.source_id),
                government_source_id=government_record.source_id,
                government_table=government_record.source_table,
                spatial_distance_meters=distance_meters,
                name_score=name_score,
                industry_score=industry_score,
                address_score=address_score,
                match_score=match_score,
                match_confidence=match_confidence,
                match_method=match_method,
                review_required=review_required,
                matched_source_ids={"osm": [osm_record.source_key], "government": [government_record.source_key], "all": [osm_record.source_key, government_record.source_key]},
                state_consistent=state_consistent,
                district_consistent=district_consistent,
                extraction_date=current_extraction_date(),
            )
        )

    return candidates



def classify_match_confidence(match_score: float, threshold_config: MatchingThresholdConfig) -> str:
    if match_score >= threshold_config.automatic_match:
        return "automatic_match"
    if match_score >= threshold_config.uncertain_match:
        return "uncertain_review"
    return "non_match"


def _source_record_key(record: IndustrialRecord) -> str:
    return record.source_key


def _normalize_cluster_geometry(geometries: Iterable[BaseGeometry]) -> BaseGeometry:
    geometries = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    if not geometries:
        return None
    return unary_union(geometries)


def _pick_first_non_null(values: Iterable[Any]) -> Any:
    for value in values:
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return value
    return None


def _pick_most_common_text(values: Iterable[str | None]) -> str | None:
    filtered = [value for value in values if value]
    if not filtered:
        return None
    return Counter(filtered).most_common(1)[0][0]


def _aggregate_review_required(records: list[IndustrialRecord], candidate_matches: list[CandidateMatch]) -> bool:
    record_keys = {_source_record_key(record) for record in records}
    for candidate in candidate_matches:
        if candidate.match_confidence == "uncertain_review" and ({candidate.osm_source_key, candidate.government_source_key} & record_keys):
            return True
    return False


def _cluster_scores(records: list[IndustrialRecord], candidate_matches: list[CandidateMatch]) -> tuple[float, float, str]:
    cluster_keys = {_source_record_key(record) for record in records}
    automatic_scores = [candidate.match_score for candidate in candidate_matches if candidate.match_confidence == "automatic_match" and {candidate.osm_source_key, candidate.government_source_key}.issubset(cluster_keys)]
    methods = [candidate.match_method for candidate in candidate_matches if candidate.match_confidence == "automatic_match" and {candidate.osm_source_key, candidate.government_source_key}.issubset(cluster_keys)]
    if automatic_scores:
        return float(sum(automatic_scores) / len(automatic_scores)), float(max(automatic_scores)), "+".join(sorted(set(methods)))
    return 1.0, 1.0, "singleton"


def _stable_site_id(records: list[IndustrialRecord], identity_index: Any | None = None) -> str:
    try:
        from src.matching.site_identity import resolve_site_id
        return resolve_site_id(records, identity_index)
    except Exception:
        canonical = "|".join(sorted(_source_record_key(record) for record in records))
        return str(uuid5(NAMESPACE_URL, f"industrial-site:{canonical}"))


def _cluster_records(records: list[IndustrialRecord], candidate_matches: list[CandidateMatch]) -> list[list[IndustrialRecord]]:
    parent = {record.source_key: record.source_key for record in records}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for candidate in candidate_matches:
        if candidate.match_confidence == "automatic_match":
            union(candidate.osm_source_key, candidate.government_source_key)

    clusters: dict[str, list[IndustrialRecord]] = defaultdict(list)
    for record in records:
        clusters[find(record.source_key)].append(record)
    return list(clusters.values())


def build_master_sites(
    osm_gdf: gpd.GeoDataFrame,
    government_gdf: gpd.GeoDataFrame,
    government_table_name: str,
    source_config: MatchingSourceConfig,
    weight_config: MatchingWeightConfig,
    threshold_config: MatchingThresholdConfig,
    *,
    identity_index: Any | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    records = normalize_source_records(osm_gdf, government_gdf, government_table_name)
    osm_records = [record for record in records if record.source_type == "osm"]
    government_records = [record for record in records if record.source_type == "government"]
    candidate_matches = generate_candidate_matches(osm_records, government_records, source_config, weight_config, threshold_config)

    clusters = _cluster_records(records, candidate_matches)
    site_id_by_record_key: dict[str, str] = {}
    for cluster in clusters:
        cluster_site_id = _stable_site_id(cluster, identity_index)
        for record in cluster:
            site_id_by_record_key[record.source_key] = cluster_site_id

    match_rows = []
    master_rows = []

    for cluster in clusters:
        site_id = site_id_by_record_key[cluster[0].source_key]
        review_required = _aggregate_review_required(cluster, candidate_matches)
        source_confidence, match_score, match_method = _cluster_scores(cluster, candidate_matches)
        matched_source_ids = {
            "osm": [record.source_key for record in cluster if record.source_type == "osm"],
            "government": [record.source_key for record in cluster if record.source_type == "government"],
            "all": [record.source_key for record in cluster],
        }

        osm_ids = [int(record.source_id) for record in cluster if record.source_type == "osm"]
        government_ids = [record.source_id for record in cluster if record.source_type == "government" and record.source_id is not None]
        names = [record.name for record in cluster]
        normalized_names = [record.normalized_name for record in cluster]
        industry_types = [record.industry_type for record in cluster]
        states = [record.state for record in cluster]
        districts = [record.district for record in cluster]
        addresses = [record.address for record in cluster]
        establishment_dates = [record.source_date for record in cluster]
        operational_statuses = [record.operational_status for record in cluster]
        first_seen_values = [record.first_seen for record in cluster if record.first_seen is not None]
        last_seen_values = [record.last_seen for record in cluster if record.last_seen is not None]
        last_verified_values = [record.source_date for record in cluster if record.source_date is not None]
        if not last_verified_values:
            last_verified_values = [record.source_timestamp.date() for record in cluster if record.source_timestamp is not None]

        geometry = _normalize_cluster_geometry(record.geometry for record in cluster)
        if geometry is None or geometry.is_empty:
            continue

        match_confidence = "automatic_match" if len(cluster) > 1 and not review_required else ("review_required" if review_required else "singleton")

        master_rows.append(
            {
                "site_id": site_id,
                "name": _pick_most_common_text(names),
                "normalized_name": _pick_most_common_text(normalized_names),
                "industry_type": _pick_most_common_text(industry_types),
                "normalized_industry_type": _pick_most_common_text(record.normalized_industry_type for record in cluster),
                "geometry": geometry,
                "state": _pick_most_common_text(states),
                "district": _pick_most_common_text(districts),
                "address": _pick_most_common_text(addresses),
                "establishment_status": _pick_most_common_text(operational_statuses),
                "establishment_date": min((value for value in establishment_dates if value is not None), default=None),
                "extraction_date": max((record.extraction_date for record in cluster if record.extraction_date is not None), default=current_extraction_date()),
                "first_seen": min(first_seen_values, default=current_extraction_date()),
                "last_seen": max(last_seen_values, default=current_extraction_date()),
                "operational_status": _pick_most_common_text(operational_statuses),
                "osm_ids": osm_ids,
                "government_ids": government_ids,
                "matched_source_ids": matched_source_ids,
                "source_count": len(cluster),
                "source_confidence": source_confidence,
                "match_score": match_score,
                "match_method": match_method,
                "match_confidence": match_confidence,
                "review_required": review_required,
                "last_verified": max(last_verified_values, default=None),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

    for candidate in candidate_matches:
        match_rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "site_id": site_id_by_record_key.get(candidate.osm_source_key) if candidate.match_confidence == "automatic_match" else None,
                "osm_source_key": candidate.osm_source_key,
                "government_source_key": candidate.government_source_key,
                "osm_id": candidate.osm_id,
                "government_source_id": candidate.government_source_id,
                "government_table": candidate.government_table,
                "match_score": candidate.match_score,
                "match_confidence": candidate.match_confidence,
                "match_method": candidate.match_method,
                "review_required": candidate.review_required,
                "matched_source_ids": candidate.matched_source_ids,
                "spatial_distance_meters": candidate.spatial_distance_meters,
                "name_score": candidate.name_score,
                "industry_score": candidate.industry_score,
                "address_score": candidate.address_score,
                "state_consistent": candidate.state_consistent,
                "district_consistent": candidate.district_consistent,
                "extraction_date": candidate.extraction_date or current_extraction_date(),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )

    master_gdf = gpd.GeoDataFrame(master_rows, geometry="geometry", crs="EPSG:4326") if master_rows else gpd.GeoDataFrame(columns=["site_id"], geometry=[], crs="EPSG:4326")
    matches_gdf = pd.DataFrame(match_rows)

    return master_gdf, matches_gdf
