from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.boundaries.config import default_config_path, get_boundary_level_config, load_config
from src.boundaries.processing import clean_boundary_gdf
from src.boundaries.source import fetch_boundary_layer
from src.boundaries.storage import bootstrap_database, write_boundary_table
from src.database.connection import create_database_engine, get_database_url
from src.data_quality.reporting import build_quality_report, get_data_quality_config, write_quality_report


def configure_logging(config: dict) -> None:
    logging.basicConfig(
        level=getattr(logging, config.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build India administrative boundaries")
    parser.add_argument("--config", default=str(default_config_path()), help="Path to config.yaml")
    parser.add_argument("--level", choices=["all", "india", "states", "districts"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Process data without writing to PostGIS")
    return parser.parse_args()


def selected_levels(level_argument: str) -> list[str]:
    if level_argument == "all":
        return ["india", "states", "districts"]
    return [level_argument]


def run() -> int:
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config)

    database_url = get_database_url(config)
    engine = create_database_engine(database_url, echo=config.get("database", {}).get("echo", False))

    sql_dir = Path(args.config).resolve().parents[1] / config.get("paths", {}).get("sql_dir", "sql")
    bootstrap_database(engine, sql_dir)

    source_config = config["boundaries"]["source"]
    storage_crs = config["crs"]["storage"]
    source_assumed_crs = config["crs"]["source_assumed"]
    data_quality_config = get_data_quality_config(config)
    summaries: list[dict] = []
    cleaned_frames = {}

    for level_name in selected_levels(args.level):
        level_config = get_boundary_level_config(config, level_name)
        logging.info("Processing %s boundaries", level_name)
        metadata, raw_gdf = fetch_boundary_layer(
            api_base_url=source_config["api_base_url"],
            dataset_type=source_config["dataset_type"],
            country_iso3=source_config["country_iso3"],
            level=level_config.level,
            target_crs=source_assumed_crs,
        )

        cleaned_gdf, summary = clean_boundary_gdf(
            raw_gdf,
            level_name=level_name,
            table_name=level_config.table,
            source_name=source_config["provider"],
            metadata={
                "source_url": metadata.source_url,
                "source_date": metadata.raw_metadata.get("boundaryDate") or metadata.raw_metadata.get("boundaryUpdated"),
            },
            name_candidates=level_config.name_candidates,
            administrative_code_candidates=level_config.administrative_code_candidates,
            source_id_candidates=level_config.source_id_candidates,
            storage_crs=storage_crs,
            source_assumed_crs=source_assumed_crs,
            allow_make_valid=config.get("validation", {}).get("allow_make_valid", True),
            remove_exact_duplicates=config.get("validation", {}).get("remove_exact_duplicates", True),
        )

        cleaned_frames[level_name] = cleaned_gdf

        if not args.dry_run:
            write_boundary_table(engine, level_config.table, cleaned_gdf)

        summaries.append(summary)

    report = build_quality_report(
        pipeline_name="boundaries",
        raw_frames={},
        cleaned_frames=cleaned_frames,
        summaries={"boundaries": {"total_records": sum(item["total_rows"] for item in summaries), "matched_records": 0}},
        config=config,
    )
    write_quality_report(report, data_quality_config.report_file)

    print(json.dumps({"boundaries": summaries}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())