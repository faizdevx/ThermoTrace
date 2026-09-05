"""Government source registry and loader.

The registry maps adapter type strings to concrete adapter classes.  New
adapter types are registered by adding them to ``_ADAPTER_REGISTRY``.

Configuration
-------------
Government sources are declared in ``config.yaml`` under the
``government_sources`` key.  The pipeline calls
``load_configured_sources(config)`` to get all configured adapters, then
calls ``is_available()`` on each one to decide whether to ingest.

Example config.yaml section
----------------------------
::

    government_sources:
      - id: cpcb_consent
        adapter: csv_file
        name: "CPCB Consent Order Database"
        enabled: true
        file: "government/sources/cpcb_consent/data.csv"
        source_date: "2026-01-01"
        name_column: "UnitName"
        id_column: "ConsentNo"
        type_column: "IndustryCategory"
        state_column: "State"
        district_column: "District"
        source_crs: "EPSG:4326"

      - id: gpcb_gujarat
        adapter: csv_file
        name: "GPCB Gujarat Industrial Registry"
        enabled: false      # disabled until data file is available
        file: "government/sources/gpcb_gujarat/data.csv"
        name_column: "UnitName"
        id_column: "ConsentNo"
        source_crs: "EPSG:4326"

Adapter types
-------------
``csv_file``
    Local file backed (CSV, XLSX, GeoJSON, GPKG).
    See :mod:`src.government.sources.csv_file`.

Adding a new adapter
--------------------
1. Create ``src/government/sources/<name>.py`` with a class that subclasses
   ``AbstractGovernmentSource``.
2. Register it in ``_ADAPTER_REGISTRY`` below with a unique string key.
3. Add a configuration block in ``config.yaml``.
"""
from __future__ import annotations

import logging
from typing import Any

from src.government.sources.base import AbstractGovernmentSource, SourceNotAvailable
from src.government.sources.csv_file import CsvFileGovernmentSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTER_REGISTRY: dict[str, type[AbstractGovernmentSource]] = {
    CsvFileGovernmentSource.ADAPTER_ID: CsvFileGovernmentSource,
}


def register_adapter(adapter_id: str, adapter_class: type[AbstractGovernmentSource]) -> None:
    """Register a new adapter type.

    Parameters
    ----------
    adapter_id : str
        The ``adapter:`` key used in config.yaml.
    adapter_class : type
        A concrete subclass of ``AbstractGovernmentSource``.
    """
    _ADAPTER_REGISTRY[adapter_id] = adapter_class


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_configured_sources(config: dict[str, Any]) -> list[AbstractGovernmentSource]:
    """Instantiate all government source adapters declared in ``config.yaml``.

    Returns only sources where ``enabled: true`` (or ``enabled`` is absent,
    defaulting to True).

    Parameters
    ----------
    config : dict
        Loaded config.yaml dictionary.

    Returns
    -------
    list[AbstractGovernmentSource]
        Instantiated adapter objects.  Empty list if no sources configured.
    """
    source_blocks: list[dict[str, Any]] = config.get("government_sources", []) or []
    adapters: list[AbstractGovernmentSource] = []

    for block in source_blocks:
        source_id = block.get("id", "<unnamed>")
        enabled = block.get("enabled", True)
        if not enabled:
            logger.debug("Government source '%s' is disabled — skipping.", source_id)
            continue

        adapter_type = block.get("adapter", "csv_file")
        adapter_class = _ADAPTER_REGISTRY.get(adapter_type)

        if adapter_class is None:
            logger.warning(
                "Unknown adapter type '%s' for source '%s'. "
                "Available types: %s",
                adapter_type,
                source_id,
                list(_ADAPTER_REGISTRY.keys()),
            )
            continue

        adapters.append(adapter_class(config, block))
        logger.debug("Registered government source adapter: '%s' (%s)", source_id, adapter_type)

    return adapters


def ingest_government_sources(
    config: dict[str, Any],
    *,
    state_name: str | None = None,
    max_validation_errors: int = 100,
) -> "IngestionSummary":
    """Load all configured and available government sources.

    Parameters
    ----------
    config : dict
        Pipeline configuration.
    state_name : str | None
        Filter rows to this state.  Passed to each adapter's ``ingest()``.
    max_validation_errors : int
        Max per-source validation errors to collect.

    Returns
    -------
    IngestionSummary
        Contains a combined GeoDataFrame plus per-source summaries and the
        list of unavailable sources.
    """
    import geopandas as gpd
    import pandas as pd
    from src.government.sources.base import SourceIngestionResult

    adapters = load_configured_sources(config)

    if not adapters:
        return IngestionSummary(
            state_filter=state_name,
            available_sources=[],
            unavailable_sources=[],
            combined_gdf=_empty_gdf(),
            per_source_summaries=[],
            total_records=0,
        )

    available: list[str] = []
    unavailable: list[dict[str, str]] = []
    results: list[SourceIngestionResult] = []

    for adapter in adapters:
        if not adapter.is_available():
            reason = adapter.unavailability_reason()
            unavailable.append({
                "source_id": adapter.source_id,
                "source_name": adapter.source_name,
                "reason": reason,
            })
            logger.info(
                "Government source '%s' not available: %s", adapter.source_id, reason
            )
            continue

        available.append(adapter.source_id)
        try:
            result = adapter.ingest(
                state_name=state_name,
                max_validation_errors=max_validation_errors,
            )
            results.append(result)
            logger.info(
                "Source '%s': %d valid records ingested",
                adapter.source_id, result.valid_row_count,
            )
        except SourceNotAvailable as exc:
            unavailable.append({
                "source_id": adapter.source_id,
                "source_name": adapter.source_name,
                "reason": str(exc),
            })
        except Exception as exc:
            logger.error(
                "Source '%s' ingestion error: %s", adapter.source_id, exc, exc_info=True
            )
            unavailable.append({
                "source_id": adapter.source_id,
                "source_name": adapter.source_name,
                "reason": f"Ingestion error: {exc}",
            })

    gdfs = [r.gdf for r in results if r.gdf is not None and not r.gdf.empty]
    combined = (
        gpd.GeoDataFrame(
            pd.concat(gdfs, ignore_index=True),
            crs="EPSG:4326",
        )
        if gdfs
        else _empty_gdf()
    )

    return IngestionSummary(
        state_filter=state_name,
        available_sources=available,
        unavailable_sources=unavailable,
        combined_gdf=combined,
        per_source_summaries=[r.summary for r in results],
        total_records=len(combined),
    )


def _empty_gdf() -> "gpd.GeoDataFrame":
    import geopandas as gpd
    gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs="EPSG:4326"))
    return gdf


# ---------------------------------------------------------------------------
# Summary type
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field  # noqa: E402


@dataclass
class IngestionSummary:
    """Result of ingesting all configured government sources."""
    state_filter: str | None
    available_sources: list[str]
    unavailable_sources: list[dict[str, str]]
    combined_gdf: "gpd.GeoDataFrame"
    per_source_summaries: list[dict]
    total_records: int

    @property
    def has_data(self) -> bool:
        return self.total_records > 0

    def to_dict(self) -> dict:
        return {
            "state_filter": self.state_filter,
            "configured_sources": len(self.available_sources) + len(self.unavailable_sources),
            "available_sources": self.available_sources,
            "unavailable_sources": self.unavailable_sources,
            "total_records": self.total_records,
            "per_source_summaries": self.per_source_summaries,
        }
