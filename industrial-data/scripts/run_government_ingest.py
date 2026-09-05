from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.boundaries.config import default_config_path, load_config
from src.database.connection import create_database_engine, get_database_url
from src.data_quality.reporting import build_quality_report, get_data_quality_config, write_quality_report
from src.government.config import get_government_cleaning_config, get_government_schema_config, get_government_source_config
from src.government.processing import clean_government_dataframe
from src.government.storage import bootstrap_database, write_government_table
from src.government.schema import standardize_column_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a government industrial dataset into its own PostGIS table")
    parser.add_argument("--config", default=str(default_config_path()), help="Path to config.yaml")
    parser.add_argument("--input", required=True, help="Path to the raw government dataset")
    parser.add_argument("--table-name", required=True, help="Target source-specific table name")
    parser.add_argument("--source-name", default="government", help="Source label for provenance")
    parser.add_argument("--source-id-column", help="Original source ID column name")
    parser.add_argument("--name-column", help="Original name column name")
    parser.add_argument("--industry-type-column", help="Original industry type column name")
    parser.add_argument("--address-column", help="Original address column name")
    parser.add_argument("--state-column", help="Original state column name")
    parser.add_argument("--district-column", help="Original district column name")
    parser.add_argument("--establishment-status-column", help="Original establishment status column name")
    parser.add_argument("--establishment-date-column", help="Original establishment date column name")
    parser.add_argument("--source-crs", default="EPSG:4326", help="CRS of the raw coordinate columns")
    parser.add_argument("--output-crs", help="Output CRS for storage, defaults to config")
    parser.add_argument("--dry-run", action="store_true", help="Process data without writing to PostGIS")
    return parser.parse_args()


def load_input_dataset(path: str) -> pd.DataFrame:
    input_path = Path(path)
    suffix = input_path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return pd.read_csv(input_path, sep="\t" if suffix == ".tsv" else ",")
    if suffix in {".json", ".geojson", ".gpkg", ".shp"}:
        return gpd.read_file(input_path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(input_path)
    return pd.read_csv(input_path)


def run() -> int:
    args = parse_args()
    config = load_config(args.config)

    database_url = get_database_url(config)
    engine = create_database_engine(database_url, echo=config.get("database", {}).get("echo", False))
    sql_dir = Path(args.config).resolve().parents[1] / config.get("paths", {}).get("sql_dir", "sql")
    bootstrap_database(engine, sql_dir)

    source_config = get_government_source_config(config)
    schema_config = get_government_schema_config(config)
    cleaning_config = get_government_cleaning_config(config)
    data_quality_config = get_data_quality_config(config)
    output_crs = args.output_crs or source_config.default_output_crs

    raw_frame = load_input_dataset(args.input)
    standardized_columns = {column: standardize_column_name(column) for column in raw_frame.columns}
    raw_frame = raw_frame.rename(columns=standardized_columns)
    source_id_column = standardize_column_name(args.source_id_column) if args.source_id_column else None
    name_column = standardize_column_name(args.name_column) if args.name_column else None
    industry_type_column = standardize_column_name(args.industry_type_column) if args.industry_type_column else None
    address_column = standardize_column_name(args.address_column) if args.address_column else None
    state_column = standardize_column_name(args.state_column) if args.state_column else None
    district_column = standardize_column_name(args.district_column) if args.district_column else None
    establishment_status_column = standardize_column_name(args.establishment_status_column) if args.establishment_status_column else None
    establishment_date_column = standardize_column_name(args.establishment_date_column) if args.establishment_date_column else None
    cleaned_gdf, summary = clean_government_dataframe(
        raw_frame,
        source_name=args.source_name,
        source_table_name=args.table_name,
        source_date=None,
        source_id_column=source_id_column,
        name_column=name_column,
        industry_type_column=industry_type_column,
        address_column=address_column,
        state_column=state_column,
        district_column=district_column,
        establishment_status_column=establishment_status_column,
        establishment_date_column=establishment_date_column,
        source_crs=args.source_crs,
        output_crs=output_crs,
        schema_config=schema_config,
        exact_duplicate_coordinate_precision=cleaning_config.exact_duplicate_coordinate_precision,
    )

    if not args.dry_run:
        write_government_table(engine, args.table_name, cleaned_gdf)

    report = build_quality_report(
        pipeline_name="government",
        raw_frames={"government_raw": raw_frame},
        cleaned_frames={"government": cleaned_gdf},
        summaries={"government": summary},
        config=config,
    )
    write_quality_report(report, data_quality_config.report_file)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())