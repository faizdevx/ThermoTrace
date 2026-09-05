"""Industry taxonomy normalization engine.

Maps raw industry type strings from heterogeneous government and OSM sources
into a controlled set of normalized categories defined in
``config/industry_taxonomy.yaml``.

Design principles
-----------------
* **Explicit aliases** — every mapping is a deliberate, named alias.
  Substring matching is NOT used, which prevents misclassification
  (e.g. "polymer" should not accidentally match "polymer chemistry lab").
* **Case-insensitive exact match** on the cleaned alias string.
* **Preserve original** — raw values are stored in ``industrial_type`` /
  ``original_industry_type``; this module only produces ``normalized_industry_type``.
* **Stable codes** — category codes (e.g. ``TEXTILE``) are written to the
  database and must never be renamed.
* **Fallback** — unrecognized values map to the ``fallback_category``
  defined in the YAML (default: ``OTHER``).
* **Configurable** — the taxonomy is a YAML file the user can extend without
  touching Python code.
* **Fast** — the alias lookup table is built once at load time.

Usage
-----
::

    from src.cleaning.industry_taxonomy import load_taxonomy, classify_industry_type

    taxonomy = load_taxonomy("config/industry_taxonomy.yaml")

    code = classify_industry_type(taxonomy, "Garment Factory")
    # → "TEXTILE"

    code = classify_industry_type(taxonomy, "XYZ Corp Unknown Stuff")
    # → "OTHER"
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class TaxonomyCategory:
    """One normalized industry category."""

    def __init__(self, code: str, label: str, aliases: list[str], osm_tags: list[str]) -> None:
        self.code = code
        self.label = label
        self.aliases = aliases
        self.osm_tags = osm_tags

    def __repr__(self) -> str:
        return f"TaxonomyCategory(code={self.code!r}, aliases={len(self.aliases)})"


class IndustryTaxonomy:
    """Loaded taxonomy with O(1) alias lookup.

    Attributes
    ----------
    categories : list[TaxonomyCategory]
    fallback_code : str
        Code to assign when no alias matches (e.g. ``"OTHER"``).
    """

    def __init__(
        self,
        categories: list[TaxonomyCategory],
        fallback_code: str,
    ) -> None:
        self.categories = categories
        self.fallback_code = fallback_code

        # Build O(1) lookup tables
        self._alias_to_code: dict[str, str] = {}
        self._osm_tag_to_code: dict[str, str] = {}

        for cat in categories:
            for alias in cat.aliases:
                key = _clean_key(alias)
                if key in self._alias_to_code:
                    logger.warning(
                        "Taxonomy alias '%s' maps to both '%s' and '%s' — using first.",
                        alias,
                        self._alias_to_code[key],
                        cat.code,
                    )
                else:
                    self._alias_to_code[key] = cat.code
            for tag in cat.osm_tags:
                osm_key = tag.strip().lower()
                self._osm_tag_to_code[osm_key] = cat.code

    def classify(self, raw_value: str | None) -> str:
        """Map a raw industry type string to a taxonomy code.

        Parameters
        ----------
        raw_value : str | None
            The raw value from the source data.

        Returns
        -------
        str
            A taxonomy code (e.g. ``"TEXTILE"``, ``"OTHER"``).
        """
        if raw_value is None or not str(raw_value).strip():
            return self.fallback_code

        key = _clean_key(raw_value)
        return self._alias_to_code.get(key, self.fallback_code)

    def classify_osm_tag(self, osm_industrial_type: str | None) -> str:
        """Map an OSM industrial_type string to a taxonomy code.

        OSM industrial_type values are produced by
        ``src.osm.tags.normalize_industrial_type()`` and look like
        ``"landuse=industrial"``, ``"craft=weaving"``, etc.

        Parameters
        ----------
        osm_industrial_type : str | None
            Value from the ``industrial_type`` column.

        Returns
        -------
        str
            Taxonomy code or fallback.
        """
        if not osm_industrial_type:
            return self.fallback_code
        key = osm_industrial_type.strip().lower()
        return self._osm_tag_to_code.get(key, self.fallback_code)

    def get_label(self, code: str) -> str | None:
        """Return the human-readable label for a taxonomy code."""
        for cat in self.categories:
            if cat.code == code:
                return cat.label
        return None

    def all_codes(self) -> list[str]:
        return [cat.code for cat in self.categories]

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation for quality reports."""
        return {
            "fallback_code": self.fallback_code,
            "category_count": len(self.categories),
            "total_aliases": len(self._alias_to_code),
            "categories": [
                {
                    "code": cat.code,
                    "label": cat.label,
                    "alias_count": len(cat.aliases),
                    "osm_tag_count": len(cat.osm_tags),
                }
                for cat in self.categories
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_key(value: str) -> str:
    """Normalize a string to a canonical comparison key.

    Steps:
    1. Strip whitespace
    2. Lowercase
    3. Collapse internal whitespace to a single space

    This is intentionally minimal — no stemming, no phonetic matching.
    Explicit aliases in the YAML cover the necessary variation.
    """
    import re
    return re.sub(r"\s+", " ", str(value).strip().lower())


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "industry_taxonomy.yaml"

_TAXONOMY_CACHE: dict[str, IndustryTaxonomy] = {}


def load_taxonomy(
    taxonomy_path: str | Path | None = None,
    *,
    force_reload: bool = False,
) -> IndustryTaxonomy:
    """Load and cache the industry taxonomy from a YAML file.

    Parameters
    ----------
    taxonomy_path : str | Path | None
        Path to ``industry_taxonomy.yaml``.  Defaults to
        ``config/industry_taxonomy.yaml`` relative to the project root.
    force_reload : bool
        Bypass the module-level cache and re-read the file.

    Returns
    -------
    IndustryTaxonomy

    Raises
    ------
    FileNotFoundError
        If the taxonomy YAML does not exist.
    ValueError
        If the YAML is malformed.
    """
    path = Path(taxonomy_path) if taxonomy_path else _DEFAULT_TAXONOMY_PATH
    cache_key = str(path.resolve())

    if not force_reload and cache_key in _TAXONOMY_CACHE:
        return _TAXONOMY_CACHE[cache_key]

    if not path.exists():
        raise FileNotFoundError(
            f"Industry taxonomy file not found: {path.resolve()}\n"
            f"Expected at: config/industry_taxonomy.yaml"
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed taxonomy YAML at {path}: {exc}") from exc

    fallback_code = str(raw.get("fallback_category", "OTHER"))
    categories: list[TaxonomyCategory] = []

    for entry in raw.get("categories", []):
        code = str(entry.get("code", "")).strip().upper()
        if not code:
            logger.warning("Taxonomy entry missing 'code' — skipping: %s", entry)
            continue
        label = str(entry.get("label", code))
        aliases = [str(a) for a in entry.get("aliases", [])]
        osm_tags = [str(t) for t in entry.get("osm_tags", [])]
        categories.append(TaxonomyCategory(code=code, label=label, aliases=aliases, osm_tags=osm_tags))

    taxonomy = IndustryTaxonomy(categories=categories, fallback_code=fallback_code)
    _TAXONOMY_CACHE[cache_key] = taxonomy

    logger.debug(
        "Loaded industry taxonomy: %d categories, %d aliases from %s",
        len(categories),
        len(taxonomy._alias_to_code),
        path,
    )
    return taxonomy


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def classify_industry_type(
    taxonomy: IndustryTaxonomy,
    raw_value: str | None,
) -> str:
    """Classify a raw industry type value using the taxonomy.

    This is a thin wrapper around ``taxonomy.classify()`` provided for
    explicit-import convenience.

    Parameters
    ----------
    taxonomy : IndustryTaxonomy
    raw_value : str | None

    Returns
    -------
    str
        Taxonomy code (e.g. ``"TEXTILE"`` or ``"OTHER"``).
    """
    return taxonomy.classify(raw_value)


def classify_osm_industrial_type(
    taxonomy: IndustryTaxonomy,
    osm_industrial_type: str | None,
) -> str:
    """Classify an OSM ``industrial_type`` string using the taxonomy.

    Parameters
    ----------
    taxonomy : IndustryTaxonomy
    osm_industrial_type : str | None
        Value like ``"landuse=industrial"``, ``"craft=weaving"``.

    Returns
    -------
    str
        Taxonomy code.
    """
    return taxonomy.classify_osm_tag(osm_industrial_type)


def classify_series(
    taxonomy: IndustryTaxonomy,
    series: "pd.Series",
    *,
    osm_mode: bool = False,
) -> "pd.Series":
    """Vectorized classification of a pandas Series.

    Parameters
    ----------
    taxonomy : IndustryTaxonomy
    series : pd.Series
        Raw industry type values.
    osm_mode : bool
        If True, use OSM tag classification (``classify_osm_tag``).
        If False, use alias classification (``classify``).

    Returns
    -------
    pd.Series
        Taxonomy codes with the same index as the input.
    """
    import pandas as pd

    if osm_mode:
        return series.map(lambda v: taxonomy.classify_osm_tag(v))
    return series.map(lambda v: taxonomy.classify(v))
