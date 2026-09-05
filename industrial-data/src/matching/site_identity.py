"""Stable site identity management.

Problem with the old approach
-------------------------------
The previous ``_stable_site_id()`` computed a UUID5 over the **sorted set of
all source keys** in a cluster.  This means:

  cluster = {osm:way:12345}
  site_id = uuid5("industrial-site:osm:way:12345")    ← run 1

  cluster = {osm:way:12345, government:cpcb:REG001}   ← after match
  site_id = uuid5("industrial-site:government:cpcb:REG001|osm:way:12345")  ← DIFFERENT

Every time a new source is matched to an existing site, the site_id changed,
breaking every foreign key and making refresh-aware pipelines impossible.

Canonical anchor strategy
--------------------------
A site's identity is anchored to its **primary source key** — the single most
reliable identifier chosen at site creation time:

1. If the cluster contains an OSM record, the smallest ``osm_type:osm_id``
   string (lexicographic) is chosen as the canonical anchor.
   OSM IDs are globally stable and persistent.

2. If the cluster is OSM-only, the smallest osm source key is the anchor.

3. If the cluster is government-only (no OSM match), the smallest
   ``government:<table>:<source_id>`` string is the anchor.

4. If the cluster has neither (shouldn't happen), fall back to the full
   sorted-set UUID5 (backward-compatible).

The site_id is then:
    uuid5(NAMESPACE_URL, f"industrial-site:v2:{canonical_anchor}")

The "v2:" prefix ensures no collision with old v1 IDs that used the full set.

Refresh semantics
-----------------
On a refresh run, before assigning any new site_id to a cluster, the pipeline
looks up whether any source key in the cluster already has a known site_id in
the ``site_source_records`` table (or the in-memory existing-sites index built
from the previous master table).  If a match is found:

  - The existing site_id is reused → no foreign key breakage
  - ``updated_at`` is bumped
  - ``source_records`` mapping is expanded

If no match is found, a new site_id is generated from the canonical anchor and
is stable for future refreshes (because the OSM ID / gov source_id is stable).

Source version tracking
-----------------------
The ``site_source_records`` table tracks one row per (site_id, source_system,
source_id) with a ``source_version`` that mirrors ``osm_version`` or
``ingestion_timestamp``.  The pipeline can check whether the source record has
changed before re-running the full match.

Public API
----------
``SiteIdentityIndex``
    In-memory index built from the existing master table + source records.
    Used to look up whether a cluster already has a known site_id.

``resolve_site_id(cluster, index)``
    Returns the existing site_id if any source key is known, otherwise
    generates a new stable one from the canonical anchor.

``canonical_anchor(cluster)``
    Returns the string used to seed the UUID5 for a new site.

``build_site_source_records(site_id, cluster, extraction_date)``
    Returns a list of dicts suitable for inserting into site_source_records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pandas as pd

from src.matching.resolution import IndustrialRecord

logger = logging.getLogger(__name__)

# Namespace prefix that distinguishes v2 site IDs from old full-set IDs
_V2_PREFIX = "industrial-site:v2:"


# ---------------------------------------------------------------------------
# Canonical anchor
# ---------------------------------------------------------------------------

def canonical_anchor(records: list[IndustrialRecord]) -> str:
    """Return the canonical identity anchor string for a cluster.

    The anchor is the single most stable source key from the cluster:
    - OSM records are preferred (OSM IDs are globally persistent).
    - Among OSM records, the lexicographically smallest source key is chosen
      to give a deterministic result regardless of processing order.
    - If no OSM records, use the smallest government source key.
    - Fallback: sorted join of all keys (matches old behaviour, labelled v1).

    Parameters
    ----------
    records : list[IndustrialRecord]

    Returns
    -------
    str
        The canonical anchor string.  Pass this to ``_make_site_id()``.
    """
    osm_keys = sorted(
        r.source_key for r in records if r.source_type == "osm"
    )
    if osm_keys:
        return osm_keys[0]

    gov_keys = sorted(
        r.source_key for r in records if r.source_type == "government"
    )
    if gov_keys:
        return gov_keys[0]

    # Fallback (should never reach this in normal operation)
    return "|".join(sorted(r.source_key for r in records))


def _make_site_id(anchor: str) -> str:
    """Generate a stable UUID5 site_id from a canonical anchor."""
    return str(uuid5(NAMESPACE_URL, _V2_PREFIX + anchor))


# ---------------------------------------------------------------------------
# Identity index
# ---------------------------------------------------------------------------

@dataclass
class SiteIdentityIndex:
    """In-memory lookup from source key → existing site_id.

    Built once per pipeline run from:
    1. The existing ``industrial_sites`` master table (via ``osm_ids`` /
       ``government_ids`` JSONB columns).
    2. Optionally from a ``site_source_records`` table if it exists.

    The index is immutable during a run; new site_ids generated during the
    run are added to a pending dict and written at the end.
    """
    # source_key → site_id (populated from existing master)
    _source_key_to_site_id: dict[str, str] = field(default_factory=dict)
    # site_id → canonical_anchor (for diagnostics)
    _site_id_to_anchor: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_master_gdf(cls, existing_master: "gpd.GeoDataFrame | None") -> "SiteIdentityIndex":
        """Build an identity index from an existing master GeoDataFrame.

        Parameters
        ----------
        existing_master : GeoDataFrame | None
            The current ``industrial_sites`` table contents.
            If None or empty, returns an empty index.
        """
        index = cls()
        if existing_master is None or len(existing_master) == 0:
            return index

        for _, row in existing_master.iterrows():
            site_id = row.get("site_id")
            if not site_id:
                continue

            # Reconstruct source keys from the JSONB arrays
            osm_ids = row.get("osm_ids") or {}
            gov_ids = row.get("government_ids") or {}
            matched = row.get("matched_source_ids") or {}

            # "all" is the most complete list
            all_keys = []
            if isinstance(matched, dict):
                all_keys = matched.get("all", [])
            if not all_keys:
                if isinstance(osm_ids, list):
                    all_keys += osm_ids
                if isinstance(gov_ids, list):
                    all_keys += gov_ids

            for key in all_keys:
                if key and isinstance(key, str):
                    index._source_key_to_site_id[key] = site_id

        logger.debug(
            "SiteIdentityIndex: loaded %d source-key mappings from existing master",
            len(index._source_key_to_site_id),
        )
        return index

    def lookup(self, records: list[IndustrialRecord]) -> str | None:
        """Return the existing site_id if any record in the cluster is known.

        Parameters
        ----------
        records : list[IndustrialRecord]

        Returns
        -------
        str | None
            Existing site_id, or None if this is a genuinely new site.
        """
        for record in records:
            site_id = self._source_key_to_site_id.get(record.source_key)
            if site_id:
                return site_id
        return None

    def register(self, site_id: str, records: list[IndustrialRecord]) -> None:
        """Register a new site_id → source key mapping (for intra-run lookup)."""
        for record in records:
            self._source_key_to_site_id[record.source_key] = site_id

    def __len__(self) -> int:
        return len(self._source_key_to_site_id)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_site_id(
    records: list[IndustrialRecord],
    index: SiteIdentityIndex,
) -> str:
    """Return a stable site_id for a cluster of records.

    Strategy (in order):
    1. Check the identity index — if any record in the cluster is already
       known to an existing site, reuse that site_id.
    2. Generate a new site_id from the canonical anchor of the cluster.
    3. Register the new mapping in the index for intra-run consistency.

    Parameters
    ----------
    records : list[IndustrialRecord]
        All records in the cluster (OSM + government).
    index : SiteIdentityIndex
        The identity index for this pipeline run.

    Returns
    -------
    str
        A stable, deterministic site_id UUID.
    """
    # Step 1: look up existing site
    existing = index.lookup(records)
    if existing:
        return existing

    # Step 2: generate from canonical anchor
    anchor = canonical_anchor(records)
    site_id = _make_site_id(anchor)

    # Step 3: register in index for intra-run dedup
    index.register(site_id, records)

    return site_id


# ---------------------------------------------------------------------------
# Source record rows (for site_source_records table)
# ---------------------------------------------------------------------------

def build_site_source_records(
    site_id: str,
    records: list[IndustrialRecord],
    extraction_date: date,
) -> list[dict[str, Any]]:
    """Build rows for the ``site_source_records`` table.

    One row per (site_id, source_system, source_id).  The ``source_version``
    is populated from ``osm_version`` / ``source_timestamp`` where available.

    Parameters
    ----------
    site_id : str
    records : list[IndustrialRecord]
    extraction_date : date

    Returns
    -------
    list[dict]
        Rows ready for bulk insert into ``site_source_records``.
    """
    rows = []
    now = datetime.now(UTC)
    for record in records:
        # Derive source_version: prefer osm_version if carried; else timestamp
        source_version: str | None = None
        if record.source_type == "osm" and record.raw_source_id:
            # raw_source_id may carry "version=N" from Overpass
            source_version = str(record.raw_source_id)
        elif record.source_timestamp:
            source_version = record.source_timestamp.isoformat()

        rows.append({
            "site_id": site_id,
            "source_system": record.source_type,
            "source_key": record.source_key,
            "source_id": record.source_id,
            "source_table": record.source_table,
            "source_version": source_version,
            "first_linked": extraction_date,
            "last_linked": extraction_date,
            "created_at": now,
            "updated_at": now,
        })
    return rows


# ---------------------------------------------------------------------------
# Schema migration helper
# ---------------------------------------------------------------------------

SITE_SOURCE_RECORDS_DDL = """
-- Stable source identity registry
-- Each row maps one (site_id, source_system, source_id) → site.
-- This is the ground truth for refresh-aware site resolution.
CREATE TABLE IF NOT EXISTS site_source_records (
    id              BIGSERIAL PRIMARY KEY,
    site_id         TEXT NOT NULL,
    source_system   TEXT NOT NULL,         -- 'osm' | 'government'
    source_key      TEXT NOT NULL,         -- full qualified key e.g. "osm:way:12345"
    source_id       TEXT NOT NULL,         -- raw ID in the source system
    source_table    TEXT NOT NULL,         -- table / dataset name
    source_version  TEXT,                  -- osm_version or ingestion timestamp
    first_linked    DATE NOT NULL,
    last_linked     DATE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT site_source_records_unique UNIQUE (site_id, source_system, source_key)
);

CREATE INDEX IF NOT EXISTS idx_ssr_site_id
    ON site_source_records (site_id);
CREATE INDEX IF NOT EXISTS idx_ssr_source_key
    ON site_source_records (source_key);
CREATE INDEX IF NOT EXISTS idx_ssr_source_system
    ON site_source_records (source_system);
"""
