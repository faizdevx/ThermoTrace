from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from src.boundaries.config import default_config_path, load_config
from src.boundaries.repository import load_district_geometry_from_geojson, load_district_geometry_from_postgis
from src.boundaries.storage import bootstrap_database as bootstrap_boundary_database
from src.database.connection import create_database_engine, get_database_url
from src.data_quality.reporting import build_quality_report, get_data_quality_config, write_quality_report
from src.osm.config import get_osm_cleaning_config, get_osm_source_config
from src.osm.overpass import build_overpass_query, execute_overpass_query
from src.osm.processing import normalize_osm_gdf
from src.osm.storage import bootstrap_database as bootstrap_osm_database, write_osm_table


def configure_logging(config: dict) -> None:
    logging.basicConfig(
        level=getattr(logging, config.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract industrial OSM data for one district")
    parser.add_argument("--config", default=str(default_config_path()), help="Path to config.yaml")
    parser.add_argument("--district-geojson", help="Local GeoJSON file containing a single district geometry")
    parser.add_argument("--district-name", help="District name to load from PostGIS")
    parser.add_argument("--district-source-id", help="District source_id to load from PostGIS")
    parser.add_argument("--bbox", help="Bounding box as south,west,north,east")
    parser.add_argument("--dry-run", action="store_true", help="Process data without writing to PostGIS")
    return parser.parse_args()


def _load_district_geometry(args: argparse.Namespace, engine, config: dict) -> gpd.GeoDataFrame | None:
    if args.bbox:
        return None
    if args.district_geojson:
        return load_district_geometry_from_geojson(args.district_geojson)
    if args.district_name or args.district_source_id:
        return load_district_geometry_from_postgis(
            engine,
            table_name=config["boundaries"]["source"]["levels"]["districts"]["table"],
            district_name=args.district_name,
            district_source_id=args.district_source_id,
        )
    raise ValueError("Provide --district-geojson, --district-name, --district-source-id, or --bbox")


def _geometry_from_args(args: argparse.Namespace, district_gdf: gpd.GeoDataFrame | None):
    if args.bbox:
        south, west, north, east = [float(value) for value in args.bbox.split(",")]
        return box(west, south, east, north), True
    if district_gdf is None or district_gdf.empty:
        raise ValueError("A district geometry is required when --bbox is not provided")
    return district_gdf.geometry.iloc[0], False


def run() -> int:
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config)

    database_url = get_database_url(config)
    engine = create_database_engine(database_url, echo=config.get("database", {}).get("echo", False))
    sql_dir = Path(args.config).resolve().parents[1] / config.get("paths", {}).get("sql_dir", "sql")
    bootstrap_boundary_database(engine, sql_dir)
    bootstrap_osm_database(engine, sql_dir)

    source_config = get_osm_source_config(config)
    cleaning_config = get_osm_cleaning_config(config)
    data_quality_config = get_data_quality_config(config)
    district_gdf = _load_district_geometry(args, engine, config)
    if district_gdf is not None and not district_gdf.empty:
        if district_gdf.crs is None:
            district_gdf = district_gdf.set_crs("EPSG:4326")
        else:
            district_gdf = district_gdf.to_crs("EPSG:4326")

    district_name = str(district_gdf.iloc[0].get("name")) if district_gdf is not None and not district_gdf.empty else None
    district_source_id = str(district_gdf.iloc[0].get("source_id")) if district_gdf is not None and not district_gdf.empty else None
    geometry, use_bbox = _geometry_from_args(args, district_gdf)

    query = build_overpass_query(config, geometry, use_bbox=use_bbox)
    logging.info("Querying Overpass for district=%s source_id=%s", district_name, district_source_id)
    overpass_urls = [source_config.overpass_url, *source_config.backup_overpass_urls]
    payload = None
    last_error: Exception | None = None
    for overpass_url in overpass_urls:
        try:
            payload = execute_overpass_query(
                overpass_url,
                query,
                request_timeout_seconds=source_config.request_timeout_seconds,
                retry_attempts=source_config.retry_attempts,
                retry_backoff_seconds=source_config.retry_backoff_seconds,
            )
            break
        except Exception as error:  # pragma: no cover - network fallback path
            last_error = error
            logging.warning("Overpass request failed for %s: %s", overpass_url, error)

    if payload is None:
        raise RuntimeError(f"All Overpass endpoints failed: {last_error}")
    time.sleep(source_config.query_pause_seconds)

    from src.osm.overpass import overpass_elements_to_geodataframe

    raw_gdf = overpass_elements_to_geodataframe(payload)

    cleaned_gdf, summary = normalize_osm_gdf(
        raw_gdf,
        source_name=source_config.provider,
        district_name=district_name,
        district_source_id=district_source_id,
        source_url=source_config.overpass_url,
        exact_duplicate_coordinate_precision=cleaning_config.exact_duplicate_coordinate_precision,
        probable_duplicate_distance_meters=cleaning_config.probable_duplicate_distance_meters,
        probable_duplicate_name_similarity=cleaning_config.probable_duplicate_name_similarity,
        probable_duplicate_industry_similarity=cleaning_config.probable_duplicate_industry_similarity,
    )

    if not args.dry_run:
        write_osm_table(engine, config["osm"]["output"]["table"], cleaned_gdf)

    report = build_quality_report(
        pipeline_name="osm",
        raw_frames={"osm_raw": raw_gdf},
        cleaned_frames={"osm": cleaned_gdf},
        summaries={"osm": summary},
        config=config,
    )
    write_quality_report(report, data_quality_config.report_file)

    print(
        json.dumps(
            {
                "district": district_name,
                "district_source_id": district_source_id,
                "osm": summary,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())