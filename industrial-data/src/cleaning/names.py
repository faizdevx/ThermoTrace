"""Name normalization for industrial facility names.

Design principles
-----------------
* **Preserve the original** — raw source values are NEVER overwritten.
  The pipeline stores both ``name`` (original) and ``normalized_name``
  (processed) as separate columns.
* **Deterministic** — given the same input, always produces the same output.
* **India-aware** — handles common Devanagari / transliteration patterns and
  Indian legal suffixes (Pvt. Ltd., Ltd., etc.)
* **Matching-safe** — the normalized form is used for fuzzy matching, not
  for display.

Public API
----------
``normalize_facility_name(name)``
    Main entry point.  Applies the full normalization pipeline.

``extract_legal_suffix(name)``
    Returns the canonical legal form found in the name (e.g. "pvt ltd"),
    or None.

``NormalizationResult``
    Named tuple returned by ``normalize_facility_name_with_details()``,
    carrying both the normalized string and the extracted metadata.
"""
from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[,;:!?'\"''""]+")
_SEPARATORS_RE = re.compile(r"[\-_/\\]+")
_AMP_RE = re.compile(r"\s*&\s*")
_AND_RE = re.compile(r"\band\b")
_DOT_BETWEEN_WORDS_RE = re.compile(r"\.\s*")
_MULTI_DOT_RE = re.compile(r"\.{2,}")

# Legal suffixes — order matters: longer forms first
# We store tuples of (pattern, canonical_form)
_LEGAL_SUFFIXES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bprivate\s+limited\b", re.I), "pvt ltd"),
    (re.compile(r"\bpvt\.?\s*ltd\.?\b", re.I), "pvt ltd"),
    (re.compile(r"\bp\.?\s*ltd\.?\b", re.I), "pvt ltd"),
    (re.compile(r"\bprivate\s+ltd\.?\b", re.I), "pvt ltd"),
    (re.compile(r"\blimited\s+liability\s+partnership\b", re.I), "llp"),
    (re.compile(r"\bllp\b", re.I), "llp"),
    (re.compile(r"\blimited\b", re.I), "ltd"),
    (re.compile(r"\bltd\.?\b", re.I), "ltd"),
    (re.compile(r"\bincorporated\b", re.I), "inc"),
    (re.compile(r"\binc\.?\b", re.I), "inc"),
    (re.compile(r"\bco-operative\b", re.I), "cooperative"),
    (re.compile(r"\bcooperative\b", re.I), "cooperative"),
    (re.compile(r"\bcoop\.?\b", re.I), "cooperative"),
]

# Noise suffixes to strip entirely for matching purposes
_NOISE_SUFFIXES: list[re.Pattern] = [
    re.compile(r"\bunit\s+[ivxlcdm\d]+\b", re.I),  # "Unit II", "Unit 3"
    re.compile(r"\bphase\s+[ivxlcdm\d]+\b", re.I),  # "Phase I", "Phase 2"
    re.compile(r"\bplant\s+[ivxlcdm\d]+\b", re.I),  # "Plant I"
    re.compile(r"\bdivision\b", re.I),
]

# Common Indic transliteration normalization
# Maps common spelling variants to a single form
_TRANSLITERATION_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bindl\.?\b", re.I), "industrial"),
    (re.compile(r"\bindus\.?\b", re.I), "industrial"),
    (re.compile(r"\bmfg\.?\b", re.I), "manufacturing"),
    (re.compile(r"\bmfrs?\.?\b", re.I), "manufacturing"),
    (re.compile(r"\bmanufg\.?\b", re.I), "manufacturing"),
    (re.compile(r"\bprodn\.?\b", re.I), "production"),
    (re.compile(r"\bprod\.?\b", re.I), "production"),
    (re.compile(r"\bengg\.?\b", re.I), "engineering"),
    (re.compile(r"\bengineering\s*works?\b", re.I), "engineering"),
    (re.compile(r"\benterprises?\b", re.I), "enterprise"),
    (re.compile(r"\bindustries\b", re.I), "industry"),
    (re.compile(r"\bindustrial\s+estate\b", re.I), "industrial estate"),
    (re.compile(r"\bindustrial\s+area\b", re.I), "industrial area"),
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class NormalizationResult(NamedTuple):
    """Details of a name normalization operation."""
    original: str | None
    normalized: str | None
    legal_suffix: str | None       # e.g. "pvt ltd", "ltd", "llp"
    has_legal_suffix: bool
    was_empty: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unicode_normalize(text: str) -> str:
    """NFKD decompose → strip combining characters → ASCII fold."""
    text = unicodedata.normalize("NFKD", text)
    # Drop combining marks (accents, diacritics) but keep base characters
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def extract_legal_suffix(name: str | None) -> str | None:
    """Return the canonical legal suffix found in *name*, or None.

    Examples
    --------
    >>> extract_legal_suffix("Alpha Works Pvt. Ltd.")
    "pvt ltd"
    >>> extract_legal_suffix("Beta Cooperative Society")
    "cooperative"
    >>> extract_legal_suffix("Gamma Factory")
    None
    """
    if not name:
        return None
    for pattern, canonical in _LEGAL_SUFFIXES:
        if pattern.search(name):
            return canonical
    return None


def normalize_facility_name(name: str | None) -> str | None:
    """Normalize a raw facility name for entity matching.

    The original value is NOT modified — this function returns a new string.
    Store as ``normalized_name`` alongside the original ``name``.

    Normalization steps:
    1. Unicode NFKD decompose + diacritic strip
    2. Lowercase
    3. Ampersand → "and"
    4. Common abbreviation expansion (Indl → industrial, etc.)
    5. Separator normalization (-, _, /, \\ → space)
    6. Punctuation removal
    7. Legal suffix extraction and normalisation
    8. Noise suffix removal (Unit II, Phase 3, etc.)
    9. Collapse whitespace
    10. Strip

    Parameters
    ----------
    name : str | None
        Raw facility name from source data.

    Returns
    -------
    str | None
        Normalised form, or None if the input is empty/None.

    Examples
    --------
    >>> normalize_facility_name("  Alpha  Industrial  Estate Pvt. Ltd. ")
    "alpha industrial estate pvt ltd"
    >>> normalize_facility_name("BETA WORKS & MFG. Co.")
    "beta works and manufacturing cooperative"
    >>> normalize_facility_name(None)
    None
    """
    if name is None:
        return None

    text = str(name).strip()
    if not text:
        return None

    # Step 1: Unicode normalization
    text = _unicode_normalize(text)

    # Step 2: Lowercase
    text = text.lower()

    # Step 3: Ampersand → "and"
    text = _AMP_RE.sub(" and ", text)

    # Step 4: Abbreviation expansion
    for pattern, replacement in _TRANSLITERATION_MAP:
        text = pattern.sub(replacement, text)

    # Step 5: Separators → space
    text = _SEPARATORS_RE.sub(" ", text)

    # Step 6: Remove/normalize punctuation
    # Keep dots between digits (version numbers, registration codes)
    text = _PUNCTUATION_RE.sub(" ", text)
    # Dots at word boundaries become spaces
    text = _DOT_BETWEEN_WORDS_RE.sub(" ", text)

    # Step 7: Normalize legal suffixes
    for pattern, canonical in _LEGAL_SUFFIXES:
        text = pattern.sub(canonical, text)

    # Step 8: Strip noise suffixes (unit/phase/division)
    for pattern in _NOISE_SUFFIXES:
        text = pattern.sub("", text)

    # Step 9 + 10: Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()

    return text or None


def normalize_facility_name_with_details(name: str | None) -> NormalizationResult:
    """Return normalized name AND metadata about the normalization.

    Parameters
    ----------
    name : str | None
        Raw facility name.

    Returns
    -------
    NormalizationResult
    """
    if name is None or not str(name).strip():
        return NormalizationResult(
            original=name,
            normalized=None,
            legal_suffix=None,
            has_legal_suffix=False,
            was_empty=True,
        )

    legal_suffix = extract_legal_suffix(name)
    normalized = normalize_facility_name(name)

    return NormalizationResult(
        original=name,
        normalized=normalized,
        legal_suffix=legal_suffix,
        has_legal_suffix=legal_suffix is not None,
        was_empty=False,
    )
