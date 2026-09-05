from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class DetectedCoordinateColumns:
    latitude: str | None
    longitude: str | None
    easting: str | None
    northing: str | None


def standardize_column_name(column_name: str) -> str:
    cleaned = column_name.strip().lower()
    for character in (" ", "-", ".", "/", "(", ")", "[", "]"):
        cleaned = cleaned.replace(character, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def standardize_column_names(frame: pd.DataFrame) -> pd.DataFrame:
    renamed = frame.copy()
    renamed.columns = [standardize_column_name(column) for column in renamed.columns]
    return renamed


def inspect_schema(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "row_count": int(len(frame)),
        "columns": list(frame.columns),
        "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "null_counts": {column: int(frame[column].isna().sum()) for column in frame.columns},
    }


def _find_candidate_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    available = {standardize_column_name(column): column for column in frame.columns}
    for candidate in candidates:
        standardized_candidate = standardize_column_name(candidate)
        if standardized_candidate in available:
            return available[standardized_candidate]
    return None


def detect_coordinate_columns(frame: pd.DataFrame, coordinate_candidates: dict[str, list[str]]) -> DetectedCoordinateColumns:
    return DetectedCoordinateColumns(
        latitude=_find_candidate_column(frame, coordinate_candidates.get("latitude", [])),
        longitude=_find_candidate_column(frame, coordinate_candidates.get("longitude", [])),
        easting=_find_candidate_column(frame, coordinate_candidates.get("easting", [])),
        northing=_find_candidate_column(frame, coordinate_candidates.get("northing", [])),
    )
