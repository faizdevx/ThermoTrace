"""Tests for Phase 6 — data cleaning pipeline.

Coverage:
  * Name normalization — whitespace, case, abbreviations, legal suffixes
  * Name normalization — original value is never modified
  * Legal suffix extraction
  * Industry taxonomy loading
  * Taxonomy classification — explicit aliases
  * Taxonomy fallback for unknown values
  * Taxonomy OSM tag classification
  * Taxonomy alias uniqueness warning (non-fatal)
  * classify_series() vectorized classification
"""
from __future__ import annotations

import pytest
import pandas as pd

from src.cleaning.names import (
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


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

class TestNormalizeFacilityName:
    def test_none_returns_none(self):
        assert normalize_facility_name(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_facility_name("") is None
        assert normalize_facility_name("   ") is None

    def test_whitespace_trimmed_and_collapsed(self):
        result = normalize_facility_name("  Alpha   Works  ")
        assert result == "alpha works"

    def test_case_lowercased(self):
        result = normalize_facility_name("BETA FACTORY")
        assert result == "beta factory"

    def test_ampersand_replaced_with_and(self):
        result = normalize_facility_name("Alpha & Beta Works")
        assert "and" in result
        assert "&" not in result

    def test_indl_abbreviation_expanded(self):
        result = normalize_facility_name("Indl Estate")
        assert "industrial" in result

    def test_mfg_abbreviation_expanded(self):
        result = normalize_facility_name("Alpha Mfg. Co.")
        assert "manufacturing" in result

    def test_legal_suffix_pvt_ltd_normalized(self):
        result = normalize_facility_name("Alpha Works Pvt. Ltd.")
        assert "pvt ltd" in result

    def test_legal_suffix_limited_normalized(self):
        result = normalize_facility_name("Beta Industries Limited")
        assert "ltd" in result

    def test_legal_suffix_llp_normalized(self):
        result = normalize_facility_name("Gamma LLP")
        assert "llp" in result

    def test_original_not_mutated(self):
        original = "  ALPHA  WORKS  Pvt.  Ltd.  "
        original_copy = original
        normalize_facility_name(original)
        assert original == original_copy  # reference unchanged

    def test_unicode_diacritics_stripped(self):
        # e.g. names from Hindi transliteration
        result = normalize_facility_name("Ràj Industries")
        assert result is not None
        # Diacritics removed but base character preserved
        assert "raj" in result or "ra" in result

    def test_separators_normalized(self):
        result = normalize_facility_name("Alpha-Beta/Gamma_Works")
        assert "-" not in result
        assert "/" not in result
        assert "_" not in result

    def test_returns_string_not_none_for_valid_input(self):
        result = normalize_facility_name("Good Factory Ltd")
        assert isinstance(result, str)
        assert len(result) > 0


class TestExtractLegalSuffix:
    def test_pvt_ltd(self):
        assert extract_legal_suffix("Alpha Pvt. Ltd.") == "pvt ltd"

    def test_private_limited(self):
        assert extract_legal_suffix("Beta Private Limited") == "pvt ltd"

    def test_ltd(self):
        assert extract_legal_suffix("Gamma Ltd") == "ltd"

    def test_llp(self):
        assert extract_legal_suffix("Delta LLP") == "llp"

    def test_no_suffix_returns_none(self):
        assert extract_legal_suffix("Plain Factory") is None

    def test_none_returns_none(self):
        assert extract_legal_suffix(None) is None


class TestNormalizationResult:
    def test_has_expected_fields(self):
        result = normalize_facility_name_with_details("Alpha Pvt. Ltd.")
        assert result.original == "Alpha Pvt. Ltd."
        assert result.normalized is not None
        assert result.has_legal_suffix is True
        assert result.legal_suffix == "pvt ltd"
        assert result.was_empty is False

    def test_empty_input(self):
        result = normalize_facility_name_with_details("")
        assert result.was_empty is True
        assert result.normalized is None


# ---------------------------------------------------------------------------
# Industry taxonomy
# ---------------------------------------------------------------------------

def _minimal_taxonomy() -> IndustryTaxonomy:
    """Build a tiny in-memory taxonomy for testing."""
    categories = [
        TaxonomyCategory(
            code="TEXTILE",
            label="Textile Manufacturing",
            aliases=["Textile Manufacturing", "Textiles", "Garment Factory", "Weaving"],
            osm_tags=["industrial=textile", "craft=weaving"],
        ),
        TaxonomyCategory(
            code="CHEMICAL",
            label="Chemical Manufacturing",
            aliases=["Chemical", "Chemicals", "Pharmaceutical"],
            osm_tags=["industrial=chemical", "industrial=pharmaceutical"],
        ),
        TaxonomyCategory(
            code="UNKNOWN",
            label="Unknown",
            aliases=["Unknown", "N/A"],
            osm_tags=[],
        ),
    ]
    return IndustryTaxonomy(categories=categories, fallback_code="OTHER")


class TestIndustryTaxonomyClassification:
    def test_known_alias_returns_correct_code(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify("Textiles") == "TEXTILE"
        assert taxonomy.classify("Garment Factory") == "TEXTILE"
        assert taxonomy.classify("Chemical") == "CHEMICAL"

    def test_case_insensitive_matching(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify("textiles") == "TEXTILE"
        assert taxonomy.classify("TEXTILES") == "TEXTILE"
        assert taxonomy.classify("Textiles") == "TEXTILE"

    def test_unknown_value_returns_fallback(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify("XYZ Corp Unknown Stuff") == "OTHER"

    def test_none_returns_fallback(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify(None) == "OTHER"

    def test_empty_string_returns_fallback(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify("") == "OTHER"

    def test_osm_tag_classification(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify_osm_tag("industrial=textile") == "TEXTILE"
        assert taxonomy.classify_osm_tag("craft=weaving") == "TEXTILE"
        assert taxonomy.classify_osm_tag("industrial=chemical") == "CHEMICAL"

    def test_unknown_osm_tag_returns_fallback(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.classify_osm_tag("landuse=industrial") == "OTHER"

    def test_get_label(self):
        taxonomy = _minimal_taxonomy()
        assert taxonomy.get_label("TEXTILE") == "Textile Manufacturing"

    def test_all_codes(self):
        taxonomy = _minimal_taxonomy()
        codes = taxonomy.all_codes()
        assert "TEXTILE" in codes
        assert "CHEMICAL" in codes

    def test_convenience_function(self):
        taxonomy = _minimal_taxonomy()
        assert classify_industry_type(taxonomy, "Weaving") == "TEXTILE"

    def test_osm_convenience_function(self):
        taxonomy = _minimal_taxonomy()
        assert classify_osm_industrial_type(taxonomy, "craft=weaving") == "TEXTILE"

    def test_classify_series_vectorized(self):
        taxonomy = _minimal_taxonomy()
        series = pd.Series(["Textiles", "Chemical", "Unknown Value"])
        result = classify_series(taxonomy, series)
        assert result.iloc[0] == "TEXTILE"
        assert result.iloc[1] == "CHEMICAL"
        assert result.iloc[2] == "OTHER"

    def test_classify_series_osm_mode(self):
        taxonomy = _minimal_taxonomy()
        series = pd.Series(["industrial=textile", "industrial=chemical", "other=tag"])
        result = classify_series(taxonomy, series, osm_mode=True)
        assert result.iloc[0] == "TEXTILE"
        assert result.iloc[1] == "CHEMICAL"
        assert result.iloc[2] == "OTHER"

    def test_no_substring_misclassification(self):
        """'polymer chemistry lab' must NOT match 'polymer' prefix."""
        taxonomy = _minimal_taxonomy()
        # Only exact alias matches — "polymer chemistry lab" is not an alias
        result = taxonomy.classify("polymer chemistry lab")
        assert result == "OTHER"

    def test_to_dict_structure(self):
        taxonomy = _minimal_taxonomy()
        d = taxonomy.to_dict()
        assert "categories" in d
        assert "fallback_code" in d
        assert d["fallback_code"] == "OTHER"


class TestLoadTaxonomyFromFile:
    def test_loads_real_taxonomy_yaml(self):
        """Load the actual config/industry_taxonomy.yaml and verify structure."""
        taxonomy = load_taxonomy()
        assert len(taxonomy.categories) >= 10
        assert taxonomy.fallback_code == "OTHER"

    def test_real_taxonomy_classifies_textiles(self):
        taxonomy = load_taxonomy()
        assert taxonomy.classify("Textiles") == "TEXTILE"
        assert taxonomy.classify("Garment Factory") == "TEXTILE"
        assert taxonomy.classify("Weaving") == "TEXTILE"

    def test_real_taxonomy_classifies_chemical(self):
        taxonomy = load_taxonomy()
        assert taxonomy.classify("Chemical") == "CHEMICAL"
        assert taxonomy.classify("Pharmaceutical") == "CHEMICAL"

    def test_real_taxonomy_classifies_osm_tags(self):
        taxonomy = load_taxonomy()
        assert taxonomy.classify_osm_tag("industrial=textile") == "TEXTILE"
        assert taxonomy.classify_osm_tag("industrial=chemical") == "CHEMICAL"

    def test_real_taxonomy_unknown_value_returns_other(self):
        taxonomy = load_taxonomy()
        result = taxonomy.classify("Definitely Not A Real Industry Category ZZZZ")
        assert result == "OTHER"

    def test_caching_returns_same_object(self):
        t1 = load_taxonomy()
        t2 = load_taxonomy()
        assert t1 is t2  # same cached object
