#!/usr/bin/env python
"""
India Industrial Intelligence Platform — State / India Pipeline Orchestrator
=============================================================================

Execute the complete industrial data pipeline for one Indian state **or** for
all states in sequence.

Usage
-----
::

    # Single state — OSM only
    python run_pipeline.py --state "Uttar Pradesh" --skip-government

    # Single state — with government data
    python run_pipeline.py --state "Goa" \\
        --government-csv data/goa.csv \\
        --government-name-column "FacilityName"

    # All states — India-level mode (runs each state sequentially)
    python run_pipeline.py --state ALL --skip-government

    # All states — skip states already processed
    python run_pipeline.py --state ALL --skip-government
    # (Re-running is safe — completed states are skipped via checkpoints)

    # Force re-extraction of all districts (ignore district checkpoints)
    python run_pipeline.py --state "Goa" --refresh --skip-government

    # Dry run — no writes to PostGIS
    python run_pipeline.py --state "Goa" --dry-run --skip-government

    # Resume India run after interruption
    python run_pipeline.py --state ALL --skip-government
    # (Already-completed states have a .complete sentinel file)

Pipeline Hierarchy
------------------
::

    India
      └─ State  (--state ALL iterates every state in the `states` table)
           └─ District  (state_extractor.py iterates districts for the state)
                └─ OSM extraction  (overpass.py queries Overpass API)

Pipeline Steps (per state)
--------------------------
[1/8]  Load state boundary
[2/8]  Load district boundaries for the state
[3/8]  Extract / load OSM industrial features
[4/8]  Load / ingest government data
[5/8]  Cleaning summary
[6/8]  Entity matching (OSM ↔ Government)
[7/8]  Create master industrial_sites dataset
[8/8]  Save to PostGIS + export GeoJSON

Refresh Behaviour (--refresh flag)
-----------------------------------
When ``--refresh`` is passed:

* District checkpoint files are ignored — all districts are re-queried.
* The new OSM extract is compared against the existing ``osm_industries``
  table using ``src.osm.refresh.detect_osm_changes()``.
* ``osm_version`` and ``source_timestamp`` are used to identify updated
  features without requiring a full diff.
* ``first_seen`` is preserved for all features; ``last_seen`` is advanced.
* Features not present in the new fetch are **retained** (never deleted) but
  their ``operational_status`` is marked ``not_seen_on_refresh``.
* Entity matching is re-run so the master dataset reflects the updated data.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd

# Bootstrap sys.path so `src.*` imports work when run from industrial-data/
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from src.boundaries.config import load_config, default_config_path
from src.boundaries.repository import (
    load_all_states,
    load_districts_for_state,
)
from src.boundaries.storage import bootstrap_database as bootstrap_boundary_db
from src.database.connection import create_database_engine, get_database_url
from src.data_quality.reporting import (
    build_quality_report,
    get_data_quality_config,
    write_quality_report,
)
from src.government.config import (
    get_government_cleaning_config,
    get_government_schema_config,
    get_government_source_config,
)
from src.government.processing import clean_government_dataframe
from src.government.sources.registry import ingest_government_sources
from src.government.storage import (
    bootstrap_database as bootstrap_government_db,
    write_government_table,
)
from src.government.schema import standardize_column_name
from src.matching.config import (
    get_matching_output_config,
    get_matching_source_config,
    get_matching_threshold_config,
    get_matching_weight_config,
)
from src.matching.resolution import build_master_sites
from src.matching.site_identity import SiteIdentityIndex
from src.matching.storage import (
    bootstrap_database as bootstrap_matching_db,
    ensure_matching_tables,
    write_master_sites,
    write_match_results,
    write_site_source_records,
)
from src.osm.config import get_osm_source_config
from src.osm.refresh import build_refresh_merge, detect_osm_changes
from src.osm.state_extractor import extract_osm_for_state
from src.osm.storage import bootstrap_database as bootstrap_osm_db, write_osm_table


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def configure_logging(config: dict) -> None:
    level_name = config.get("logging", {}).get("level", "INFO")
    level = getattr(logging, level_name, logging.INFO)
    log_file = config.get("logging", {}).get("file")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_ALL_STATES_SENTINEL = "ALL"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description=(
            "Complete industrial data pipeline for one Indian state or ALL states.\n\n"
            "Use --state ALL to run every state in sequence (India-level mode).\n"
            "Use --state 'Uttar Pradesh' (etc.) for a single state."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # OSM-only — single state
  python run_pipeline.py --state "Goa" --skip-government

  # OSM-only — all states (India mode)
  python run_pipeline.py --state ALL --skip-government

  # Refresh existing data for one state (re-queries Overpass, preserves history)
  python run_pipeline.py --state "Goa" --refresh --skip-government

  # Government data included
  python run_pipeline.py --state "Goa" \\
      --government-csv data/goa.csv \\
      --government-name-column "FacilityName" \\
      --government-type-column "IndustryType"

  # Dry run — shows counts without writing
  python run_pipeline.py --state "Goa" --dry-run --skip-government
        """,
    )

    parser.add_argument(
        "--state",
        required=True,
        metavar="STATE_NAME|ALL",
        help=(
            'Indian state name (e.g. "Uttar Pradesh") or ALL to run every '
            "state in the states boundary table."
        ),
    )
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        metavar="PATH",
        help="Path to config.yaml (default: config/config.yaml)",
    )

    # OSM options
    osm_group = parser.add_argument_group("OSM extraction")
    osm_group.add_argument(
        "--skip-osm",
        action="store_true",
        help="Skip OSM extraction; load existing osm_industries table.",
    )
    osm_group.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Ignore district checkpoint files and re-extract every district. "
            "Refresh-aware: preserves first_seen, detects updated features, "
            "retains removed features."
        ),
    )
    osm_group.add_argument(
        "--checkpoint-dir",
        metavar="PATH",
        help="Directory for district checkpoint sentinels (default: processed/<state>/osm/).",
    )
    osm_group.add_argument(
        "--india-checkpoint-dir",
        metavar="PATH",
        default="processed/india/states",
        help=(
            "Directory for state-level checkpoint sentinels used in --state ALL mode "
            "(default: processed/india/states/)."
        ),
    )
    osm_group.add_argument(
        "--concurrency",
        type=int,
        default=1,
        metavar="N",
        help="Concurrent district Overpass queries (default: 1).",
    )

    # Government options
    gov_group = parser.add_argument_group("Government data")
    gov_group.add_argument(
        "--skip-government",
        action="store_true",
        help="OSM-only mode — skip government data ingestion.",
    )
    gov_group.add_argument(
        "--government-csv",
        metavar="PATH",
        help="Path to government industrial dataset (CSV/XLSX/GeoJSON/GPKG).",
    )
    gov_group.add_argument(
        "--government-table",
        default="government_industries_pipeline",
        metavar="TABLE",
        help="PostGIS table for government data (default: government_industries_pipeline).",
    )
    gov_group.add_argument(
        "--government-state-column",
        metavar="COL",
        help="Column in the government CSV holding the state name.",
    )
    gov_group.add_argument(
        "--government-name-column",
        metavar="COL",
        help="Column in the government CSV holding the facility name.",
    )
    gov_group.add_argument(
        "--government-type-column",
        metavar="COL",
        help="Column in the government CSV holding the industry type.",
    )
    gov_group.add_argument(
        "--government-id-column",
        metavar="COL",
        help="Column in the government CSV holding a unique facility identifier.",
    )

    # Output options
    out_group = parser.add_argument_group("Output")
    out_group.add_argument(
        "--export-path",
        metavar="PATH",
        help="GeoJSON export path (default: exports/<state>_industrial_sites.geojson).",
    )
    out_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and report without writing to PostGIS or disk.",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Terminal formatting
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"


def _header(state_name: str) -> None:
    label = f"{state_name} Industrial Data Pipeline"
    bar = "═" * 58
    print(f"\n{_BOLD}{_CYAN}╔{bar}╗{_RESET}")
    print(f"{_BOLD}{_CYAN}║{_RESET}  {_BOLD}{'India Industrial Intelligence Platform':^54}{_RESET}  {_BOLD}{_CYAN}║{_RESET}")
    print(f"{_BOLD}{_CYAN}║{_RESET}  {_CYAN}{label:^54}{_RESET}  {_BOLD}{_CYAN}║{_RESET}")
    print(f"{_BOLD}{_CYAN}╚{bar}╝{_RESET}\n")


def _india_header(total: int) -> None:
    bar = "═" * 58
    print(f"\n{_BOLD}{_CYAN}╔{bar}╗{_RESET}")
    print(f"{_BOLD}{_CYAN}║{_RESET}  {_BOLD}{'India Industrial Intelligence Platform':^54}{_RESET}  {_BOLD}{_CYAN}║{_RESET}")
    print(f"{_BOLD}{_CYAN}║{_RESET}  {_CYAN}{f'All-India Pipeline  ({total} states)':^54}{_RESET}  {_BOLD}{_CYAN}║{_RESET}")
    print(f"{_BOLD}{_CYAN}╚{bar}╝{_RESET}\n")


def _step(n: int, total: int, description: str) -> None:
    print(f"\n{_BOLD}[{n}/{total}]{_RESET} {description}...")


def _ok(message: str) -> None:
    print(f"      {_GREEN}✓{_RESET}  {message}")


def _warn(message: str) -> None:
    print(f"      {_YELLOW}⚠{_RESET}  {message}")


def _info(message: str) -> None:
    print(f"      {_DIM}{message}{_RESET}")


def _footer(state_name: str, elapsed: float, status: str = "COMPLETED") -> None:
    bar = "═" * 58
    color = _GREEN if status == "COMPLETED" else (_YELLOW if "WARNING" in status else _RED)
    label = f"{state_name.upper()} PIPELINE {status}"
    print(f"\n{_BOLD}{color}╔{bar}╗{_RESET}")
    print(f"{_BOLD}{color}║{_RESET}  {_BOLD}{label:^54}{_RESET}  {_BOLD}{color}║{_RESET}")
    print(f"{_BOLD}{color}║{_RESET}  {_DIM}{'Elapsed: ' + _format_elapsed(elapsed):^54}{_RESET}  {_BOLD}{color}║{_RESET}")
    print(f"{_BOLD}{color}╚{bar}╝{_RESET}\n")


def _india_footer(elapsed: float, passed: int, failed: int, skipped: int) -> None:
    bar = "═" * 58
    summary = f"{passed} passed  {failed} failed  {skipped} skipped"
    print(f"\n{_BOLD}{_GREEN}╔{bar}╗{_RESET}")
    print(f"{_BOLD}{_GREEN}║{_RESET}  {_BOLD}{'ALL-INDIA PIPELINE COMPLETED':^54}{_RESET}  {_BOLD}{_GREEN}║{_RESET}")
    print(f"{_BOLD}{_GREEN}║{_RESET}  {_DIM}{summary:^54}{_RESET}  {_BOLD}{_GREEN}║{_RESET}")
    print(f"{_BOLD}{_GREEN}║{_RESET}  {_DIM}{'Elapsed: ' + _format_elapsed(elapsed):^54}{_RESET}  {_BOLD}{_GREEN}║{_RESET}")
    print(f"{_BOLD}{_GREEN}╚{bar}╝{_RESET}\n")


def _format_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _n(count: int | None) -> str:
    if count is None:
        return "N/A"
    return f"{count:,}"


# ---------------------------------------------------------------------------
# Government CSV loader
# ---------------------------------------------------------------------------

def _load_government_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return pd.read_csv(p, sep="\t" if suffix == ".tsv" else ",")
    if suffix in {".json", ".geojson", ".gpkg", ".shp"}:
        return gpd.read_file(p)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(p)
    return pd.read_csv(p)


# ---------------------------------------------------------------------------
# State-level checkpoint helpers (for India mode)
# ---------------------------------------------------------------------------

def _state_checkpoint_path(india_checkpoint_dir: Path, state_name: str) -> Path:
    safe = state_name.replace(" ", "_").replace("/", "_")
    return india_checkpoint_dir / f"{safe}.complete"


def _state_is_complete(india_checkpoint_dir: Path, state_name: str) -> bool:
    return _state_checkpoint_path(india_checkpoint_dir, state_name).exists()


def _mark_state_complete(india_checkpoint_dir: Path, state_name: str) -> None:
    india_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    _state_checkpoint_path(india_checkpoint_dir, state_name).touch()


# ---------------------------------------------------------------------------
# Per-state pipeline logic
# ---------------------------------------------------------------------------

def _run_single_state(  # noqa: C901 — complexity is acceptable for an orchestrator
    state_name: str,
    args: argparse.Namespace,
    config: dict,
    engine,
) -> dict:
    """Run the full 8-step pipeline for one state.

    Parameters
    ----------
    state_name : str
        The state to process.
    args : argparse.Namespace
        Parsed CLI arguments.
    config : dict
        Loaded config.yaml dictionary.
    engine : SQLAlchemy Engine

    Returns
    -------
    dict
        Machine-readable pipeline summary for this state.
    """
    state_slug = state_name.replace(" ", "_")
    TOTAL_STEPS = 8

    _header(state_name)
    state_start = time.monotonic()

    default_export = Path("data") / "processed" / state_slug / "master_industrial_sites.geojson"
    export_path = Path(args.export_path or default_export)
    export_path.parent.mkdir(parents=True, exist_ok=True)

    output_config = get_matching_output_config(config)
    source_config_matching = get_matching_source_config(config)
    weight_config = get_matching_weight_config(config)
    threshold_config = get_matching_threshold_config(config)
    data_quality_config = get_data_quality_config(config)
    government_source_config = get_government_source_config(config)
    government_schema_config = get_government_schema_config(config)
    government_cleaning_config = get_government_cleaning_config(config)

    boundary_levels = config["boundaries"]["source"]["levels"]
    states_table = boundary_levels["states"]["table"]
    districts_table = boundary_levels["districts"]["table"]

    sql_dir = (
        Path(args.config).resolve().parents[1]
        / config.get("paths", {}).get("sql_dir", "sql")
    )

    # =========================================================================
    # [1/8] Load state boundary
    # =========================================================================
    _step(1, TOTAL_STEPS, "Loading state boundary")
    try:
        from sqlalchemy import text
        state_query = text(
            f"SELECT id, name, geometry FROM {states_table} "
            f"WHERE LOWER(name) = LOWER(:n) LIMIT 1"
        )
        state_gdf = gpd.read_postgis(
            state_query, engine, params={"n": state_name}, geom_col="geometry"
        )
        if state_gdf.empty:
            _warn(f"State '{state_name}' not in '{states_table}'. Run run_boundaries.py first.")
            return {"state": state_name, "status": "failed", "reason": "state_not_found"}
        _ok(f"State boundary loaded  ({state_name})")
    except Exception as exc:
        _warn(f"Could not load state boundary: {exc}")
        return {"state": state_name, "status": "failed", "reason": str(exc)}

    # =========================================================================
    # [2/8] Load district boundaries
    # =========================================================================
    _step(2, TOTAL_STEPS, "Loading district boundaries")
    try:
        districts_gdf = load_districts_for_state(
            engine,
            state_name=state_name,
            states_table=states_table,
            districts_table=districts_table,
        )
        _ok(f"{_n(len(districts_gdf))} districts loaded")
    except ValueError as exc:
        _warn(str(exc))
        return {"state": state_name, "status": "failed", "reason": str(exc)}

    # =========================================================================
    # [3/8] Fetch / load OSM industrial data
    # =========================================================================
    _step(3, TOTAL_STEPS, "Fetching OSM industrial data")

    osm_gdf: gpd.GeoDataFrame
    osm_summary: dict = {}
    osm_change_report = None

    if args.skip_osm:
        _info("--skip-osm: loading existing osm_industries table")
        try:
            osm_table = config["osm"]["output"]["table"]
            osm_gdf = gpd.read_postgis(
                f'SELECT * FROM "{osm_table}"', engine, geom_col="geometry"
            )
            _ok(f"{_n(len(osm_gdf))} OSM features loaded from existing table")
        except Exception as exc:
            _warn(f"Could not load osm_industries: {exc}")
            osm_gdf = gpd.GeoDataFrame()

    else:
        checkpoint_dir = (
            Path(args.checkpoint_dir)
            if args.checkpoint_dir
            else Path("processed") / state_slug / "osm"
        )
        _info(f"Checkpoint dir: {checkpoint_dir}")
        _info(f"Refresh mode: {'yes — history preserved' if args.refresh else 'no (cached districts reused)'}")

        # Load existing snapshot for change detection (before overwriting)
        existing_osm_gdf: gpd.GeoDataFrame | None = None
        if args.refresh:
            try:
                osm_table = config["osm"]["output"]["table"]
                existing_osm_gdf = gpd.read_postgis(
                    f'SELECT osm_type, osm_id, osm_version, source_timestamp, '
                    f'first_seen, last_seen, operational_status, geometry '
                    f'FROM "{osm_table}"',
                    engine,
                    geom_col="geometry",
                )
                _info(f"Loaded {_n(len(existing_osm_gdf))} existing OSM records for diff")
            except Exception:
                existing_osm_gdf = None

        try:
            osm_gdf, osm_summary = extract_osm_for_state(
                state_name=state_name,
                engine=engine,
                config=config,
                checkpoint_dir=checkpoint_dir,
                refresh=args.refresh,
                max_workers=args.concurrency,
                states_table=states_table,
                districts_table=districts_table,
            )
            _ok(f"All districts processed")
            _ok(f"{_n(len(osm_gdf))} OSM features found")

            if osm_summary.get("failed_districts"):
                _warn(
                    f"{osm_summary['failed_districts']} district(s) failed: "
                    f"{osm_summary.get('failed_district_names', [])[:5]}"
                )

            # Refresh-aware merge
            if args.refresh and existing_osm_gdf is not None:
                merge_result = build_refresh_merge(
                    existing_osm_gdf, osm_gdf, mark_removed_as="not_seen_on_refresh"
                )
                osm_change_report = merge_result.report
                osm_gdf = merge_result.merged_gdf
                _ok(f"Refresh diff: {osm_change_report}")
            else:
                osm_change_report = detect_osm_changes(existing_osm_gdf, osm_gdf)

            if not args.dry_run and not osm_gdf.empty:
                write_osm_table(engine, config["osm"]["output"]["table"], osm_gdf)
                _info("OSM data written to PostGIS")

        except RuntimeError as exc:
            _warn(f"OSM extraction failed: {exc}")
            return {"state": state_name, "status": "failed", "reason": str(exc)}

    # =========================================================================
    # [4/8] Load government industrial data
    # =========================================================================
    _step(4, TOTAL_STEPS, "Loading government industrial data")

    government_gdf: gpd.GeoDataFrame
    government_table_name: str = args.government_table

    def _empty_government_gdf() -> gpd.GeoDataFrame:
        gdf = gpd.GeoDataFrame(
            columns=[
                "source_id", "name", "normalized_name", "industry_type",
                "normalized_industry_type", "address", "state",
                "district", "geometry", "establishment_status",
                "establishment_date", "extraction_date", "first_seen",
                "last_seen", "operational_status", "source", "source_date",
            ],
            geometry="geometry",
        )
        return gdf.set_crs("EPSG:4326")

    if args.skip_government:
        _info("--skip-government: OSM-only mode")
        government_gdf = _empty_government_gdf()
        _ok("Government records skipped (OSM-only mode)")

    elif args.government_csv:
        # Legacy --government-csv path: load a single CSV directly
        _info(f"Reading: {args.government_csv}")
        try:
            raw_frame = _load_government_csv(args.government_csv)
            raw_frame.columns = [standardize_column_name(c) for c in raw_frame.columns]

            if args.government_state_column:
                state_col = standardize_column_name(args.government_state_column)
                if state_col in raw_frame.columns:
                    before = len(raw_frame)
                    raw_frame = raw_frame[
                        raw_frame[state_col].str.strip().str.lower() == state_name.lower()
                    ].reset_index(drop=True)
                    _info(f"State filter: {before} → {len(raw_frame)} rows")

            government_source_config = get_government_source_config(config)
            government_schema_config = get_government_schema_config(config)
            government_cleaning_config = get_government_cleaning_config(config)

            government_gdf, _ = clean_government_dataframe(
                raw_frame,
                source_name="government",
                source_table_name=government_table_name,
                source_date=None,
                source_id_column=standardize_column_name(args.government_id_column) if args.government_id_column else None,
                name_column=standardize_column_name(args.government_name_column) if args.government_name_column else None,
                industry_type_column=standardize_column_name(args.government_type_column) if args.government_type_column else None,
                address_column=None,
                state_column=standardize_column_name(args.government_state_column) if args.government_state_column else None,
                district_column=None,
                establishment_status_column=None,
                establishment_date_column=None,
                source_crs="EPSG:4326",
                output_crs=government_source_config.default_output_crs,
                schema_config=government_schema_config,
                exact_duplicate_coordinate_precision=government_cleaning_config.exact_duplicate_coordinate_precision,
            )
            _ok(f"{_n(len(government_gdf))} government records loaded")

            if not args.dry_run and not government_gdf.empty:
                bootstrap_government_db(engine, sql_dir)
                write_government_table(engine, government_table_name, government_gdf)
                _info("Government data written to PostGIS")

        except Exception as exc:
            _warn(f"Government CSV ingestion failed: {exc}")
            _info("Continuing with empty government dataset")
            government_gdf = _empty_government_gdf()

    else:
        # Use the registry-driven government source ingestion
        _info("Loading from configured government_sources in config.yaml")
        try:
            ingestion = ingest_government_sources(
                config, state_name=state_name, max_validation_errors=100
            )

            if ingestion.unavailable_sources:
                for ua in ingestion.unavailable_sources:
                    _warn(
                        f"Source '{ua['source_id']}' ({ua['source_name']}) not available:"
                    )
                    for line in ua["reason"].splitlines():
                        _info(f"  {line}")

            if not ingestion.has_data:
                _warn(f"No configured government dataset available for {state_name}")
                _info("Skipping government matching")
                government_gdf = _empty_government_gdf()
            else:
                government_gdf = ingestion.combined_gdf
                _ok(
                    f"{_n(ingestion.total_records)} government records loaded "
                    f"from {len(ingestion.available_sources)} source(s)"
                )
                for src_summary in ingestion.per_source_summaries:
                    _info(
                        f"  {src_summary['source_id']}: "
                        f"{src_summary['valid_row_count']} valid, "
                        f"{src_summary['rejected_row_count']} rejected, "
                        f"{src_summary.get('validation_error_count', 0)} validation errors"
                    )

                if not args.dry_run and not government_gdf.empty:
                    bootstrap_government_db(engine, sql_dir)
                    write_government_table(engine, government_table_name, government_gdf)
                    _info("Government data written to PostGIS")

        except Exception as exc:
            _warn(f"Government ingestion failed: {exc}")
            logging.exception("Government ingestion error", exc_info=exc)
            government_gdf = _empty_government_gdf()

    # =========================================================================
    # [5/8] Cleaning summary
    # =========================================================================
    _step(5, TOTAL_STEPS, "Cleaning data")

    osm_feature_count = len(osm_gdf) if osm_gdf is not None else 0
    gov_record_count = len(government_gdf) if government_gdf is not None else 0

    if osm_feature_count == 0 and gov_record_count == 0:
        _warn("Both datasets are empty — nothing to process.")
        return {"state": state_name, "status": "empty", "osm_features": 0, "government_records": 0}

    _ok("Invalid records removed")
    _ok("Source duplicates removed")
    _ok("Names normalized")
    _ok("Industry types normalized")
    _info(f"OSM features:        {_n(osm_feature_count)}")
    _info(f"Government records:  {_n(gov_record_count)}")
    if osm_change_report:
        _info(f"OSM diff: {osm_change_report}")

    # =========================================================================
    # [6/8] Entity matching
    # =========================================================================
    _step(6, TOTAL_STEPS, "Matching OSM and government records")

    master_gdf: gpd.GeoDataFrame
    matches_df: pd.DataFrame
    matched_count = 0

    if osm_gdf is None or osm_gdf.empty:
        _warn("OSM dataset empty — skipping entity matching")
        master_gdf = gpd.GeoDataFrame()
        matches_df = pd.DataFrame()
    else:
        identity_index: SiteIdentityIndex | None = None
        if not args.dry_run and engine is not None:
            try:
                existing_master = gpd.read_postgis(
                    f'SELECT * FROM "{output_config.master_table}"', engine, geom_col="geometry"
                )
                identity_index = SiteIdentityIndex.from_master_gdf(existing_master)
                if identity_index and len(identity_index._source_key_to_site_id) > 0:
                    _info(f"Loaded existing site identity index ({_n(len(identity_index._source_key_to_site_id))} source keys)")
            except Exception:
                identity_index = None

        try:
            master_gdf, matches_df = build_master_sites(
                osm_gdf,
                government_gdf,
                government_table_name,
                source_config_matching,
                weight_config,
                threshold_config,
                identity_index=identity_index,
            )
            if not matches_df.empty and "match_confidence" in matches_df.columns:
                matched_count = int(
                    (matches_df["match_confidence"] == "automatic_match").sum()
                )
            _ok(f"{_n(matched_count)} matches created")
            unmatched_osm = max(0, osm_feature_count - matched_count)
            unmatched_gov = max(0, gov_record_count - matched_count)
            _ok(f"{_n(unmatched_osm)} unmatched OSM records retained")
            _ok(f"{_n(unmatched_gov)} unmatched government records retained")
        except Exception as exc:
            logging.exception("Entity matching failed: %s", exc)
            _warn(f"Entity matching failed: {exc}")
            master_gdf = gpd.GeoDataFrame()
            matches_df = pd.DataFrame()

    # =========================================================================
    # [7/8] Create master dataset
    # =========================================================================
    _step(7, TOTAL_STEPS, "Creating master dataset")

    site_count = len(master_gdf) if master_gdf is not None else 0
    if site_count == 0:
        _warn("Master dataset is empty")
    else:
        _ok(f"{_n(site_count)} unique industrial sites created")
        if not matches_df.empty:
            _info(f"Candidate match pairs evaluated: {_n(len(matches_df))}")

    # =========================================================================
    # [8/8] Save results
    # =========================================================================
    _step(8, TOTAL_STEPS, "Saving results")

    if args.dry_run:
        _info("--dry-run: skipping all PostGIS writes")
    elif master_gdf is not None and not master_gdf.empty:
        try:
            ensure_matching_tables(engine, output_config.master_table, output_config.match_table)
            if not matches_df.empty:
                write_match_results(engine, output_config.match_table, matches_df)
            write_master_sites(engine, output_config.master_table, master_gdf)
            _ok("PostGIS updated")
            _info(f"  Table: {output_config.master_table}")

            # Phase 9: Persist site source records identity ledger
            try:
                from collections import defaultdict
                from src.matching.resolution import normalize_source_records
                from src.matching.site_identity import build_site_source_records

                records = normalize_source_records(osm_gdf, government_gdf, government_table_name)
                record_key_to_site = {}
                for _, row in master_gdf.iterrows():
                    sid = row["site_id"]
                    matched = row.get("matched_source_ids") or {}
                    keys = matched.get("all", []) if isinstance(matched, dict) else []
                    for k in keys:
                        record_key_to_site[k] = sid

                ledger_by_site = defaultdict(list)
                for r in records:
                    sid = record_key_to_site.get(r.source_key)
                    if sid:
                        ledger_by_site[sid].append(r)

                all_ledger_rows = []
                ext_date = datetime.now(UTC).date()
                for sid, rec_list in ledger_by_site.items():
                    all_ledger_rows.extend(build_site_source_records(sid, rec_list, ext_date))

                if all_ledger_rows:
                    ledger_df = pd.DataFrame(all_ledger_rows)
                    write_site_source_records(engine, ledger_df)
                    _info(f"  {_n(len(ledger_df))} records in site_source_records ledger")
            except Exception as ledger_exc:
                logging.warning("Could not write site_source_records: %s", ledger_exc)

        except Exception as exc:
            _warn(f"PostGIS write failed: {exc}")
            logging.exception("PostGIS write error", exc_info=exc)

    # GeoJSON export (Phase 11)
    if not args.dry_run and master_gdf is not None and not master_gdf.empty:
        try:
            from src.export.geojson import export_master_sites_geojson
            written_path = export_master_sites_geojson(master_gdf, export_path, validate=True)
            _ok("GeoJSON exported")
            _info(f"  {written_path}")
        except Exception as exc:
            _warn(f"GeoJSON export failed: {exc}")
    elif args.dry_run:
        _info(f"--dry-run: GeoJSON would be → {export_path}")

    # Data-quality report
    try:
        report = build_quality_report(
            pipeline_name=f"pipeline_{state_slug}",
            raw_frames={"osm": osm_gdf, "government": government_gdf},
            cleaned_frames={"master": master_gdf},
            summaries={
                "pipeline": {
                    "state": state_name,
                    "osm_features": osm_feature_count,
                    "government_records": gov_record_count,
                    "matched_pairs": matched_count,
                    "master_sites": site_count,
                    "total_records": osm_feature_count + gov_record_count,
                    "matched_records": matched_count,
                }
            },
            config=config,
        )
        if not args.dry_run:
            dq_path = (
                Path(data_quality_config.report_file).parent
                / f"{state_slug}_data_quality_report.json"
            )
            write_quality_report(report, str(dq_path))
    except Exception as exc:
        logging.warning("Data quality report failed: %s", exc)

    elapsed = time.monotonic() - state_start
    has_warnings = bool(osm_summary.get("failed_districts"))
    final_status = "COMPLETED WITH WARNINGS" if has_warnings else "COMPLETED"
    _footer(state_name, elapsed, status=final_status)

    summary = {
        "state": state_name,
        "status": final_status,
        "osm_features": osm_feature_count,
        "government_records": gov_record_count,
        "matched_pairs": matched_count,
        "master_sites": site_count,
        "export_path": str(export_path) if not args.dry_run else None,
        "dry_run": args.dry_run,
        "elapsed_seconds": round(elapsed, 1),
        "refresh": args.refresh,
        "osm_change_report": osm_change_report.to_dict() if osm_change_report else None,
        "failed_districts": osm_summary.get("failed_districts", 0),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    print("\n" + json.dumps(summary, indent=2, default=str))
    return summary


# ---------------------------------------------------------------------------
# India-level orchestrator
# ---------------------------------------------------------------------------

def _run_india_pipeline(args: argparse.Namespace, config: dict, engine) -> int:
    """Iterate every state in the states table and call _run_single_state().

    State-level checkpoints are written to ``--india-checkpoint-dir``
    (default: ``processed/india/states/``).  Re-running the command safely
    resumes from the last successfully completed state.

    India-level execution does not call ``--skip-osm`` or ``--government-csv``
    at a per-state level — those flags apply uniformly to every state.
    """
    india_checkpoint_dir = Path(args.india_checkpoint_dir)
    india_start = time.monotonic()

    boundary_levels = config["boundaries"]["source"]["levels"]
    states_table = boundary_levels["states"]["table"]

    try:
        all_states_gdf = load_all_states(engine, states_table=states_table)
    except ValueError as exc:
        print(f"\n{_RED}ERROR:{_RESET} {exc}", file=sys.stderr)
        return 1

    state_names = list(all_states_gdf["name"].dropna())
    total = len(state_names)
    _india_header(total)

    passed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    summaries: list[dict] = []

    for i, state_name in enumerate(state_names, start=1):
        bar = f"[{i}/{total}]"
        print(f"\n{_BOLD}{_CYAN}{bar}{_RESET} {state_name}")

        if not args.refresh and _state_is_complete(india_checkpoint_dir, state_name):
            print(f"      {_DIM}Already complete — skipping{_RESET}")
            skipped.append(state_name)
            summaries.append({"state": state_name, "status": "skipped"})
            continue

        try:
            result = _run_single_state(state_name, args, config, engine)
            summaries.append(result)

            if result.get("status") in ("ok", "empty"):
                _mark_state_complete(india_checkpoint_dir, state_name)
                passed.append(state_name)
            else:
                failed.append(state_name)
                logging.error("State '%s' failed: %s", state_name, result.get("reason"))

        except Exception as exc:
            logging.exception("Unhandled error for state '%s': %s", state_name, exc)
            failed.append(state_name)
            summaries.append({"state": state_name, "status": "failed", "reason": str(exc)})

    elapsed = time.monotonic() - india_start
    _india_footer(elapsed, len(passed), len(failed), len(skipped))

    india_summary = {
        "mode": "ALL",
        "total_states": total,
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "failed_states": failed,
        "elapsed_seconds": round(elapsed, 1),
        "timestamp": datetime.now(UTC).isoformat(),
        "state_summaries": summaries,
    }
    print("\n" + json.dumps(india_summary, indent=2, default=str))

    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    configure_logging(config)

    database_url = get_database_url(config)
    engine = create_database_engine(
        database_url, echo=config.get("database", {}).get("echo", False)
    )

    sql_dir = (
        Path(args.config).resolve().parents[1]
        / config.get("paths", {}).get("sql_dir", "sql")
    )

    # Bootstrap DB schemas (idempotent)
    bootstrap_boundary_db(engine, sql_dir)
    bootstrap_osm_db(engine, sql_dir)
    bootstrap_matching_db(engine, sql_dir)

    # Dispatch based on --state value
    if args.state.strip().upper() == _ALL_STATES_SENTINEL:
        return _run_india_pipeline(args, config, engine)
    else:
        state_start = time.monotonic()
        result = _run_single_state(args.state.strip(), args, config, engine)
        elapsed = time.monotonic() - state_start

        if result.get("status") in ("failed",):
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
