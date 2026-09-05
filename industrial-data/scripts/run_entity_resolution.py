from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd

from src.boundaries.config import default_config_path, load_config
from src.database.connection import create_database_engine, get_database_url
from src.data_quality.reporting import build_quality_report, get_data_quality_config, write_quality_report
from src.matching.config import get_matching_output_config, get_matching_source_config, get_matching_threshold_config, get_matching_weight_config
from src.matching.resolution import build_master_sites
from src.matching.storage import bootstrap_database, ensure_matching_tables, write_master_sites, write_match_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve industrial sites between OSM and government datasets")
    parser.add_argument("--config", default=str(default_config_path()), help="Path to config.yaml")
    parser.add_argument("--osm-table", default="osm_industries", help="Source OSM table")
    parser.add_argument("--government-table", required=True, help="Source government table")
    parser.add_argument("--dry-run", action="store_true", help="Resolve without writing results to PostGIS")
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    config = load_config(args.config)
    database_url = get_database_url(config)
    engine = create_database_engine(database_url, echo=config.get("database", {}).get("echo", False))

    sql_dir = Path(args.config).resolve().parents[1] / config.get("paths", {}).get("sql_dir", "sql")
    bootstrap_database(engine, sql_dir)

    output_config = get_matching_output_config(config)
    source_config = get_matching_source_config(config)
    weight_config = get_matching_weight_config(config)
    threshold_config = get_matching_threshold_config(config)
    data_quality_config = get_data_quality_config(config)

    osm_gdf = gpd.read_postgis(f'SELECT * FROM "{args.osm_table}"', engine, geom_col="geometry")
    government_gdf = gpd.read_postgis(f'SELECT * FROM "{args.government_table}"', engine, geom_col="geometry")

    master_gdf, matches_gdf = build_master_sites(
        osm_gdf,
        government_gdf,
        args.government_table,
        source_config,
        weight_config,
        threshold_config,
    )

    if not args.dry_run:
        ensure_matching_tables(engine, output_config.master_table, output_config.match_table)
        write_match_results(engine, output_config.match_table, matches_gdf)
        write_master_sites(engine, output_config.master_table, master_gdf)

    report = build_quality_report(
        pipeline_name="matching",
        raw_frames={"osm": osm_gdf, "government": government_gdf},
        cleaned_frames={"osm": osm_gdf, "government": government_gdf, "matches": matches_gdf, "master": master_gdf},
        summaries={
            "matching": {
                "osm_rows": int(len(osm_gdf)),
                "government_rows": int(len(government_gdf)),
                "candidate_matches": int(len(matches_gdf)),
                "matched_records": int((matches_gdf["match_confidence"] == "automatic_match").sum()) if not matches_gdf.empty and "match_confidence" in matches_gdf.columns else 0,
                "total_records": int(len(osm_gdf) + len(government_gdf)),
            }
        },
        config=config,
    )
    write_quality_report(report, data_quality_config.report_file)

    summary = {
        "osm_rows": int(len(osm_gdf)),
        "government_rows": int(len(government_gdf)),
        "candidate_matches": int(len(matches_gdf)),
        "master_sites": int(len(master_gdf)),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())