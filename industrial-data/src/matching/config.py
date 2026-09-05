from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MatchingSourceConfig:
    max_spatial_distance_meters: float
    require_state_district_consistency: bool
    candidate_name_similarity_min: int
    candidate_address_similarity_min: int
    candidate_industry_similarity_min: int


@dataclass(frozen=True)
class MatchingWeightConfig:
    spatial: float
    name: float
    industry: float
    address: float


@dataclass(frozen=True)
class MatchingThresholdConfig:
    automatic_match: float
    uncertain_match: float


@dataclass(frozen=True)
class MatchingOutputConfig:
    master_table: str
    match_table: str


def get_matching_source_config(config: dict[str, Any]) -> MatchingSourceConfig:
    source = config["matching"]["source"]
    return MatchingSourceConfig(
        max_spatial_distance_meters=float(source.get("max_spatial_distance_meters", 1500)),
        require_state_district_consistency=bool(source.get("require_state_district_consistency", True)),
        candidate_name_similarity_min=int(source.get("candidate_name_similarity_min", 70)),
        candidate_address_similarity_min=int(source.get("candidate_address_similarity_min", 70)),
        candidate_industry_similarity_min=int(source.get("candidate_industry_similarity_min", 60)),
    )


def get_matching_weight_config(config: dict[str, Any]) -> MatchingWeightConfig:
    weights = config["matching"]["weights"]
    return MatchingWeightConfig(
        spatial=float(weights.get("spatial", 0.4)),
        name=float(weights.get("name", 0.3)),
        industry=float(weights.get("industry", 0.2)),
        address=float(weights.get("address", 0.1)),
    )


def get_matching_threshold_config(config: dict[str, Any]) -> MatchingThresholdConfig:
    thresholds = config["matching"]["thresholds"]
    return MatchingThresholdConfig(
        automatic_match=float(thresholds.get("automatic_match", 0.85)),
        uncertain_match=float(thresholds.get("uncertain_match", 0.65)),
    )


def get_matching_output_config(config: dict[str, Any]) -> MatchingOutputConfig:
    output = config["matching"]["output"]
    return MatchingOutputConfig(
        master_table=output.get("master_table", "industrial_sites"),
        match_table=output.get("match_table", "industrial_entity_matches"),
    )
