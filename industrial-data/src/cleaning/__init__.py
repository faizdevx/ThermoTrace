"""Centralized data cleaning pipeline.

Public API
----------
from src.cleaning import normalize_facility_name, load_taxonomy, classify_industry_type
"""
from src.cleaning.names import (
    NormalizationResult,
    extract_legal_suffix,
    normalize_facility_name,
    normalize_facility_name_with_details,
)
from src.cleaning.industry_taxonomy import (
    IndustryTaxonomy,
    TaxonomyCategory,
    classify_industry_type,
    classify_osm_industrial_type,
    classify_series,
    load_taxonomy,
)

__all__ = [
    "IndustryTaxonomy",
    "NormalizationResult",
    "TaxonomyCategory",
    "classify_industry_type",
    "classify_osm_industrial_type",
    "classify_series",
    "extract_legal_suffix",
    "load_taxonomy",
    "normalize_facility_name",
    "normalize_facility_name_with_details",
]
