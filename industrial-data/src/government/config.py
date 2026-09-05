from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GovernmentSourceConfig:
    provider: str
    default_table_prefix: str
    default_output_crs: str


@dataclass(frozen=True)
class GovernmentSchemaConfig:
    missing_values: list[str]
    unknown_values: list[str]
    not_applicable_values: list[str]
    coordinate_candidates: dict[str, list[str]]
    operational_status_candidates: list[str]


@dataclass(frozen=True)
class GovernmentCleaningConfig:
    exact_duplicate_coordinate_precision: int


def get_government_source_config(config: dict[str, Any]) -> GovernmentSourceConfig:
    source = config["government"]["source"]
    return GovernmentSourceConfig(
        provider=source["provider"],
        default_table_prefix=source["default_table_prefix"],
        default_output_crs=source.get("default_output_crs", "EPSG:4326"),
    )


def get_government_schema_config(config: dict[str, Any]) -> GovernmentSchemaConfig:
    schema = config["government"]["schema"]
    return GovernmentSchemaConfig(
        missing_values=list(schema.get("missing_values", ["", None])),
        unknown_values=list(schema.get("unknown_values", [])),
        not_applicable_values=list(schema.get("not_applicable_values", [])),
        coordinate_candidates={key: list(values) for key, values in schema.get("coordinate_candidates", {}).items()},
        operational_status_candidates=list(schema.get("operational_status_candidates", ["operational_status", "status", "facility_status"])),
    )


def get_government_cleaning_config(config: dict[str, Any]) -> GovernmentCleaningConfig:
    cleaning = config["government"]["cleaning"]
    return GovernmentCleaningConfig(
        exact_duplicate_coordinate_precision=int(cleaning.get("exact_duplicate_coordinate_precision", 6)),
    )
