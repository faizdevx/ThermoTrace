"""Label rules (version ``labels-v1``) - the single source of truth, also written into the label manifest.

Every class lists the INPUTS its rule reads. A classifier that is scored against these labels must not be
given the same inputs in the same form (leakage); see ``forbidden_inputs`` and docs/LABELS.md.
"""
from __future__ import annotations

from datetime import date

RULE_VERSION = "labels-v1"
WINDOW_DAYS = 7
# Labelled windows start where both VIIRS satellites (S-NPP + NOAA-20) are in the archive.
FIRST_WINDOW = date(2025, 10, 1)
# Time split: windows starting on/after this date are test windows (and only in held-out regions).
TEST_FROM = date(2026, 4, 1)

AUTHORITATIVE_SOURCES = ("wri_gppd", "gem", "ggfr", "vnf")   # citable facility lists (not OSM)
PERSISTENT_MAX_DISTANCE_M = 1000.0
PERSISTENT_MIN_ACTIVE_FRACTION = 0.20
NO_FACILITY_RADIUS_M = 3000.0
EPISODIC_MAX_ACTIVE_FRACTION = 0.05          # cell active on < 5 % of the 365 baseline days
CROP_MIN = 0.70
WOODY_MIN = 0.70
AGRI_STATES = ("Punjab", "Haryana")           # plus Uttar Pradesh west of WEST_UP_MAX_LON
WEST_UP_MAX_LON = 79.5
AGRI_MONTHS = (10, 11, 4, 5)
INCIDENT_DAYS_BEFORE = 1
INCIDENT_DAYS_AFTER = 2

CLASSES = ("industrial_persistent", "agricultural_burning", "vegetation_fire", "industrial_fire")

RULES: dict[str, dict] = {
    "industrial_persistent": {
        "rule": (f"cell within {PERSISTENT_MAX_DISTANCE_M:.0f} m of a WRI GPPD / GEM / flare-site facility AND that facility "
                 f"was active on >= {PERSISTENT_MIN_ACTIVE_FRACTION:.0%} of the 365 baseline days"),
        "inputs": ["distance_to_authoritative_facility_m", "facility_baseline_active_fraction"],
        "label_source": "rule:industrial_persistent",
    },
    "agricultural_burning": {
        "rule": (f"WorldCover cropland >= {CROP_MIN:.0%}; Punjab / Haryana / Uttar Pradesh west of {WEST_UP_MAX_LON} E; window in "
                 f"Oct-Nov or Apr-May; no facility (any source) within {NO_FACILITY_RADIUS_M:.0f} m; cell episodic "
                 f"(< {EPISODIC_MAX_ACTIVE_FRACTION:.0%} active days)"),
        "inputs": ["lc_crop", "state", "window_month", "distance_to_any_facility_m", "cell_baseline_active_fraction"],
        "label_source": "rule:agricultural_burning",
    },
    "vegetation_fire": {
        "rule": (f"WorldCover tree+shrub+mangrove >= {WOODY_MIN:.0%}; no facility (any source) within {NO_FACILITY_RADIUS_M:.0f} m; "
                 f"cell episodic (< {EPISODIC_MAX_ACTIVE_FRACTION:.0%} active days). NOT confirmed by MODIS MCD64A1 burned "
                 "area (needs an Earthdata login) - weaker than the plan asks for"),
        "inputs": ["lc_woody", "distance_to_any_facility_m", "cell_baseline_active_fraction"],
        "label_source": "rule:vegetation_fire",
    },
    "industrial_fire": {
        "rule": ("documented industrial fire: public source text confirms event, place and date; location resolved to a "
                 f"facility or industrial area; window = event date -{INCIDENT_DAYS_BEFORE} d / +{INCIDENT_DAYS_AFTER} d"),
        "inputs": ["event_date", "curated_location"],
        "label_source": "curated:incident",
    },
}


def forbidden_inputs(label_class: str) -> list[str]:
    """Classifier features that must not be used when scoring this class against its own labels."""
    return list(RULES[label_class]["inputs"])
