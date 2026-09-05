from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BoundaryLevelConfig:
    level: str
    table: str
    name_candidates: list[str]
    administrative_code_candidates: list[str]
    source_id_candidates: list[str]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return project_root() / "config" / "config.yaml"


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else default_config_path()
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def get_boundary_level_config(config: dict[str, Any], level_name: str) -> BoundaryLevelConfig:
    level_config = config["boundaries"]["source"]["levels"][level_name]
    return BoundaryLevelConfig(
        level=level_config["level"],
        table=level_config["table"],
        name_candidates=list(level_config["name_candidates"]),
        administrative_code_candidates=list(level_config["administrative_code_candidates"]),
        source_id_candidates=list(level_config["source_id_candidates"]),
    )