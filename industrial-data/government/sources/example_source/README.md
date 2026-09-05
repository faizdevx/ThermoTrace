# Example Government Source — Template

This directory is a template for adding a real government industrial data
source to the pipeline.

## What to do

1. **Obtain the dataset** from the relevant government authority.
2. **Place the data file** here (e.g. `data.csv`, `data.xlsx`, `data.geojson`).
3. **Configure it** in `config.yaml` under `government_sources:`.
4. **Run the pipeline.**

## Directory structure

```
government/
    sources/
        <source_id>/         ← this directory
            README.md        ← this file
            data.csv         ← YOUR data file goes here (not committed to git)
            .gitkeep         ← keeps the directory in git
```

## Configuration block for `config.yaml`

Copy this block into `config.yaml` under `government_sources:` and fill in the
column names for your dataset.

```yaml
government_sources:
  - id: example_source              # unique machine-readable ID
    adapter: csv_file               # adapter type (currently: csv_file)
    name: "Example Source Name"     # human-readable name for logs/reports
    enabled: true                   # set false to skip without removing config
    file: "government/sources/example_source/data.csv"
    source_date: "2026-01-01"       # ISO date of dataset (publication date)

    # Column mappings — use the ORIGINAL column names from your file
    name_column: "FacilityName"
    id_column: "RegistrationNumber"
    type_column: "IndustryType"
    state_column: "State"
    district_column: "District"
    address_column: "Address"
    establishment_status_column: "Status"
    establishment_date_column: "EstablishmentDate"
    source_crs: "EPSG:4326"         # CRS of lat/lon in your file
```

## Required columns

Your data file must have **at minimum**:
- A latitude column (e.g. `Latitude`, `Lat`, `Y`)
- A longitude column (e.g. `Longitude`, `Lon`, `Lng`, `X`)

All other columns are optional but improve matching quality.

## What the pipeline does with your data

1. Loads the raw file and stores every raw row in `raw_record` (no data loss).
2. Standardizes column names (case-insensitive, spaces → underscores).
3. Filters to the requested state if `state_column` is configured.
4. Builds Point geometries from lat/lon columns.
5. Removes exact coordinate duplicates.
6. Normalizes `industry_type` against the taxonomy in `config/industry_taxonomy.yaml`.
7. Records `source_id`, `extraction_date`, `first_seen`, `last_seen`.
8. Matches against OSM data via entity resolution.

## If you cannot obtain the data

Set `enabled: false` in the config block.  The pipeline will skip this source
and report clearly:

```
[4/8] Loading government industrial data...
      Source 'example_source' not available: File not found: ...
      Skipping government matching for this source.
```

This is expected and correct behaviour — **no fabrication**.

## Known Indian government industrial data sources

The following sources exist but require manual download or registration.
They are **not** included in this repository.

| Source | Description | URL |
|--------|-------------|-----|
| CPCB Consent Database | Central Pollution Control Board | https://cpcb.nic.in |
| MSME Udyam Registry | Ministry of MSME registrations | https://udyamregistration.gov.in |
| DPIIT Industrial Parks | Dept. for Promotion of Industry | https://dpiit.gov.in |
| State PCB portals | Each state has own consent data | varies |

Each of these requires registration, download, or API access.  Once obtained,
place the file here and configure the adapter.
