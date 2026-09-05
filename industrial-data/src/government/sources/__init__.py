"""Government sources adapter package."""
from src.government.sources.base import (
    AbstractGovernmentSource,
    SourceIngestionError,
    SourceIngestionResult,
    SourceNotAvailable,
    ValidationError,
)
from src.government.sources.registry import (
    IngestionSummary,
    ingest_government_sources,
    load_configured_sources,
    register_adapter,
)

__all__ = [
    "AbstractGovernmentSource",
    "IngestionSummary",
    "SourceIngestionError",
    "SourceIngestionResult",
    "SourceNotAvailable",
    "ValidationError",
    "ingest_government_sources",
    "load_configured_sources",
    "register_adapter",
]
