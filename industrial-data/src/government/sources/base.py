"""Abstract base class for government industrial data source adapters.

Every government data source is represented by a concrete subclass of
``AbstractGovernmentSource``.  The subclass is responsible for:

1. **Loading** — fetch or read the raw data from wherever it lives
   (a local file, a URL, an API endpoint, etc.)
2. **Preserving** — store the raw source data unmodified in ``raw_record``
3. **Normalizing** — map into the common schema via ``clean_government_dataframe``
4. **Provenance** — record ``source``, ``source_date``, ``extraction_date``
5. **Validation** — return a ``SourceIngestionResult`` that includes counts of
   rejected rows and validation errors

Adapters must be honest about what they cannot provide.  If credentials are
missing or a file is not present, the adapter must raise ``SourceNotAvailable``
rather than returning fabricated or empty data silently.

Usage
-----
::

    from src.government.sources.registry import load_government_source

    source = load_government_source("gpcb_gujarat", config)
    if source.is_available():
        result = source.ingest(state_name="Gujarat")
        print(result.summary)
    else:
        print(source.unavailability_reason())
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import geopandas as gpd


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------

class SourceNotAvailable(Exception):
    """Raised when a government source adapter cannot produce data.

    This is the *expected* path for unconfigured sources — it is not an
    error in the pipeline, it is honest reporting of missing configuration.
    """


class SourceIngestionError(Exception):
    """Raised when a source is configured and available but ingestion fails."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    row_index: int
    column: str | None
    value: Any
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "column": self.column,
            "value": str(self.value)[:200],
            "reason": self.reason,
        }


@dataclass
class SourceIngestionResult:
    """Complete result of ingesting one government data source.

    Attributes
    ----------
    source_id : str
        Unique identifier for this source adapter (e.g. ``"cpcb_consent"``).
    source_name : str
        Human-readable name (e.g. ``"CPCB Consent Database"``).
    state_filter : str | None
        State that was requested; ``None`` means national/unfiltered.
    extraction_date : date
        Date of this ingestion run.
    gdf : gpd.GeoDataFrame
        Cleaned, normalized GeoDataFrame ready for storage and matching.
        Columns match the common government schema.
    raw_row_count : int
        Total rows in the raw source.
    valid_row_count : int
        Rows that passed geometry and schema validation.
    rejected_row_count : int
        Rows dropped due to missing/invalid geometry or fatal schema errors.
    duplicate_row_count : int
        Rows dropped as exact duplicates.
    validation_errors : list[ValidationError]
        Detailed per-row validation errors (up to ``max_errors`` items).
    summary : dict[str, Any]
        Machine-readable summary of the ingestion run.
    """
    source_id: str
    source_name: str
    state_filter: str | None
    extraction_date: date
    gdf: gpd.GeoDataFrame
    raw_row_count: int
    valid_row_count: int
    rejected_row_count: int
    duplicate_row_count: int
    validation_errors: list[ValidationError] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "state_filter": self.state_filter,
            "extraction_date": str(self.extraction_date),
            "raw_row_count": self.raw_row_count,
            "valid_row_count": self.valid_row_count,
            "rejected_row_count": self.rejected_row_count,
            "duplicate_row_count": self.duplicate_row_count,
            "validation_error_count": len(self.validation_errors),
            "validation_errors": [e.to_dict() for e in self.validation_errors[:20]],
        }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AbstractGovernmentSource(abc.ABC):
    """Base class for all government industrial data source adapters.

    Subclasses must implement:
    - ``source_id`` property
    - ``source_name`` property
    - ``is_available()`` method
    - ``unavailability_reason()`` method
    - ``ingest()`` method

    The ``ingest()`` method MUST:
    - Call ``self._assert_available()`` first
    - Store raw source data in the ``raw_record`` column of the output GDF
    - Return a ``SourceIngestionResult``
    """

    def __init__(self, config: dict[str, Any], source_config: dict[str, Any]) -> None:
        """
        Parameters
        ----------
        config : dict
            The global pipeline config.yaml dict.
        source_config : dict
            The source-specific configuration block from the
            ``government_sources`` section of config.yaml.
        """
        self._config = config
        self._source_config = source_config

    @property
    @abc.abstractmethod
    def source_id(self) -> str:
        """Unique machine-readable identifier, e.g. ``"cpcb_consent"``."""

    @property
    @abc.abstractmethod
    def source_name(self) -> str:
        """Human-readable name, e.g. ``"CPCB Consent Database"``."""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if this source can produce data right now.

        This should check for file existence, credential presence, etc.
        It must NOT make network requests — it should only check local state.
        """

    @abc.abstractmethod
    def unavailability_reason(self) -> str:
        """Return a human-readable explanation of why the source is unavailable.

        Only called when ``is_available()`` returns False.
        Example: ``"File not found: data/cpcb_consent.csv"``
        """

    @abc.abstractmethod
    def ingest(
        self,
        *,
        state_name: str | None = None,
        max_validation_errors: int = 100,
    ) -> SourceIngestionResult:
        """Load, clean and return data from this source.

        Parameters
        ----------
        state_name : str | None
            If provided, filter to rows for this state only.
        max_validation_errors : int
            Maximum number of per-row validation errors to collect.

        Returns
        -------
        SourceIngestionResult

        Raises
        ------
        SourceNotAvailable
            If the source is not available (file missing, no credentials, etc.)
        SourceIngestionError
            If the source is available but ingestion fails.
        """

    def _assert_available(self) -> None:
        """Raise SourceNotAvailable if the source is not configured/accessible."""
        if not self.is_available():
            raise SourceNotAvailable(
                f"Government source '{self.source_id}' is not available: "
                f"{self.unavailability_reason()}"
            )
