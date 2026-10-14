"""Loaders that turn each citable source into one normalised table.

Columns: source, source_id, name, kind, lat, lon, radius_m, capacity_mw, status, fuel, url, footprint.
``footprint`` is a WKT polygon where the source has one (OSM ways/relations), else None.
Licence and attribution live in ``SOURCES`` (one place, written to every facility_sources row).
Nothing is downloaded here: each loader reads a file the operator obtained under that source's terms.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import shape

from src.thermal.sites import KINDS, kind_from_tags

COLUMNS = ["source", "source_id", "name", "kind", "lat", "lon", "radius_m", "capacity_mw", "status", "fuel", "url",
           "footprint"]
MAX_RADIUS_M = 6000.0

# priority: lower number wins the facility's name / coordinates when sources are merged
SOURCES: dict[str, dict] = {
    "gem": {"priority": 1, "licence": "CC-BY-4.0 (Global Energy Monitor; check the terms of each tracker)",
            "attribution": "Global Energy Monitor", "url": "https://globalenergymonitor.org/"},
    "wri_gppd": {"priority": 2, "licence": "CC-BY-4.0", "attribution": "World Resources Institute, Global Power Plant Database",
                 "url": "https://datasets.wri.org/dataset/globalpowerplantdatabase"},
    "vnf": {"priority": 3, "licence": "EOG VIIRS Nightfire terms (CC BY 4.0 as published; verify)",
            "attribution": "Earth Observation Group, Colorado School of Mines (VIIRS Nightfire)",
            "url": "https://eogdata.mines.edu/products/vnf/"},
    "osm": {"priority": 4, "licence": "ODbL-1.0", "attribution": "(c) OpenStreetMap contributors",
            "url": "https://www.openstreetmap.org/copyright"},
}

_WRI_THERMAL = {"coal", "gas", "oil", "biomass", "waste", "petcoke", "cogeneration", "nuclear", "other"}
# GEM tracker -> thermal kind. "gas_plant" is the Global Oil and Gas PLANT Tracker (GOGPT): oil/gas-fired POWER
# plants, not refineries. "oil_gas_extraction" (GOGET) is upstream fields/wells, where flaring happens.
GEM_TRACKERS = {"coal_plant": "power_thermal", "gas_plant": "power_thermal", "steel": "steel_metal",
                "coal_mine": "mining_quarry", "cement": "cement", "oil_gas_extraction": "refinery_petrochem"}
# file-name patterns used by ``discover_raw`` (GEM file names: Global-Coal-Plant-Tracker-July-2026.xlsx, ...)
GEM_FILE_PATTERNS = [(r"coal.?plant|gcpt", "coal_plant"), (r"coal.?mine|gcmt", "coal_mine"),
                     (r"steel|gist", "steel"), (r"cement|gcct", "cement"),
                     (r"oil.?and.?gas.?plant|gogpt", "gas_plant"), (r"extraction|goget", "oil_gas_extraction")]
# Only infrastructure that physically exists can be a heat source. Proposed / announced / permitted /
# pre-construction / cancelled / shelved / retired units are dropped; blank status is kept (unknown).
_KEEP_STATUS = {"operating", "construction", "mothballed", "in construction", "under construction", "active",
                "operating pre-retirement", "idle", ""}
_FIRST = {"lat": ("latitude", "lat", "y"), "lon": ("longitude", "lon", "lng", "x"),
          "name": ("plant name", "plant name (english)", "name", "unit name", "mine name", "project name",
                   "facility name", "plant"),
          "id": ("gem location id", "gem plant id", "gem unit/phase id", "gem unit id", "gem mine id", "gem id",
                 "plant id", "id", "gppd_idnr", "unit id"),
          "status": ("status", "plant status", "operating status", "capacity operating status"),
          "cap": ("capacity (mw)", "capacity_mw", "nominal crude steel capacity (ttpa)", "capacity (mtpa)",
                  "capacity", "capacity (bcm/y)"),
          "country": ("country", "country/area", "country_long", "country_name"),
          "url": ("url", "wiki url", "wiki page", "gem wiki page (eng)")}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def _pick(df: pd.DataFrame, key: str) -> str | None:
    lower = {str(c).lower().strip(): c for c in df.columns}
    for cand in _FIRST[key]:
        if cand in lower:
            return lower[cand]
    return None


def equivalent_radius_m(geom) -> float:
    """Radius of the circle with the same area as ``geom`` (lon/lat degrees), local equirectangular metres."""
    lat0 = math.radians(geom.centroid.y)
    area_deg2 = geom.area
    return math.sqrt(area_deg2 * 111_320.0 ** 2 * math.cos(lat0) / math.pi)


def _finish(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df.copy()
    df["source"] = source
    df = df[pd.notna(df["lat"]) & pd.notna(df["lon"]) & df["lat"].between(5, 38) & df["lon"].between(67, 99)]
    base = np.array([KINDS[k].radius_m for k in df["kind"]], dtype=float)
    if "radius_m" in df and len(df):
        extra = pd.to_numeric(df["radius_m"], errors="coerce").fillna(0).to_numpy(dtype=float)
        df["radius_m"] = np.minimum(np.maximum(base, extra), MAX_RADIUS_M)
    else:
        df["radius_m"] = base
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    return df[COLUMNS].drop_duplicates(["source", "source_id"]).reset_index(drop=True)


def load_osm(geojson: str | Path) -> pd.DataFrame:
    """OSM facility cache from scripts/fetch_thermal_sites.py.

    Uses the footprint polygon where the cache has one (equivalent-area radius), else the element's
    bounding box (equivalent-area radius of the box), else the kind's default radius.
    """
    data = json.loads(Path(geojson).read_text(encoding="utf-8"))
    rows = []
    for f in data["features"]:
        p = f.get("properties", {})
        kind = p.get("kind") or kind_from_tags(p.get("tags") or {})
        if kind is None or f["geometry"]["type"] != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        ref = p.get("source_ref") or p.get("site_id")
        footprint, radius = None, None
        if p.get("footprint"):
            try:
                geom = shape(p["footprint"])
                if geom.is_valid and not geom.is_empty and geom.area > 0:
                    footprint, radius = geom.wkt, equivalent_radius_m(geom)
            except (ValueError, TypeError, AttributeError):
                footprint = None
        if radius is None and p.get("bounds"):
            b = p["bounds"]
            dy = (b["maxlat"] - b["minlat"]) * 110_540.0
            dx = (b["maxlon"] - b["minlon"]) * 111_320.0 * math.cos(math.radians(lat))
            radius = math.sqrt(max(dx * dy, 0.0) / math.pi) if dx > 0 and dy > 0 else None
        rows.append({"source_id": ref, "name": p.get("name"), "kind": kind, "lat": lat, "lon": lon,
                     "radius_m": radius, "footprint": footprint,
                     "url": f"https://www.openstreetmap.org/{ref}" if ref and "/" in str(ref) else None})
    return _finish(pd.DataFrame(rows), "osm") if rows else _empty()


def load_wri_gppd(csv_path: str | Path) -> pd.DataFrame:
    """WRI Global Power Plant Database CSV; keeps Indian thermal plants (fuel-based kind)."""
    df = pd.read_csv(csv_path, low_memory=False)
    country = "country_long" if "country_long" in df else "country"
    df = df[df[country].astype(str).str.lower().isin(["india", "ind"])]
    df = df[df["primary_fuel"].astype(str).str.lower().isin(_WRI_THERMAL)]
    out = pd.DataFrame({"source_id": df["gppd_idnr"], "name": df["name"], "kind": "power_thermal",
                        "lat": pd.to_numeric(df["latitude"], errors="coerce"),
                        "lon": pd.to_numeric(df["longitude"], errors="coerce"),
                        "capacity_mw": pd.to_numeric(df["capacity_mw"], errors="coerce"),
                        "status": None,  # GPPD lists plants without an operating status column
                        "fuel": df["primary_fuel"].astype(str),
                        "url": df["url"] if "url" in df else None})
    return _finish(out, "wri_gppd")


def load_gem(path: str | Path, tracker: str) -> pd.DataFrame:
    """A Global Energy Monitor tracker export (csv/xlsx). ``tracker`` picks the thermal kind.

    GEM trackers are often unit-level: units of one plant share the plant's location id, so rows are
    collapsed per location (capacity summed over the kept units, status of the most advanced unit).
    """
    if tracker not in GEM_TRACKERS:
        raise ValueError(f"tracker must be one of {sorted(GEM_TRACKERS)}")
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        def has_coords(f: pd.DataFrame) -> bool:
            names = {str(c).lower().strip() for c in f.columns}
            return bool(_pick(f, "lat") and _pick(f, "lon")) or "coordinates" in names
        df = next((f for f in pd.read_excel(p, sheet_name=None).values() if has_coords(f)), None)
        if df is None:
            raise ValueError("no sheet with latitude/longitude columns")
    else:
        df = pd.read_csv(p, low_memory=False)
    col = {k: _pick(df, k) for k in _FIRST}
    if not (col["lat"] and col["lon"]):
        coords = next((c for c in df.columns if str(c).lower().strip() in ("coordinates", "coordinate", "lat/lon")), None)
        if coords is None:
            raise ValueError("latitude/longitude columns not found")
        parts = df[coords].astype(str).str.split(",", n=1, expand=True)   # GEM steel/cement: "21.12, 83.45"
        df = df.assign(_lat=pd.to_numeric(parts[0].str.strip(), errors="coerce"),
                       _lon=pd.to_numeric(parts[1].str.strip(), errors="coerce") if parts.shape[1] > 1 else np.nan)
        col["lat"], col["lon"] = "_lat", "_lon"
    if col["country"]:
        df = df[df[col["country"]].astype(str).str.strip().str.lower().isin(["india", "ind", "in"])]
    status = (df[col["status"]].astype(str).str.lower().str.strip().replace({"nan": ""})
              if col["status"] else pd.Series("", index=df.index))
    df, status = df[status.isin(_KEEP_STATUS)], status[status.isin(_KEEP_STATUS)]
    lat = pd.to_numeric(df[col["lat"]], errors="coerce").round(5)
    lon = pd.to_numeric(df[col["lon"]], errors="coerce").round(5)
    ident = df[col["id"]].astype(str) if col["id"] else lat.astype(str) + "," + lon.astype(str)
    rows = pd.DataFrame({"source_id": f"{tracker}:" + ident, "kind": GEM_TRACKERS[tracker],
                         "name": df[col["name"]] if col["name"] else None, "lat": lat, "lon": lon,
                         "capacity_mw": pd.to_numeric(df[col["cap"]], errors="coerce") if col["cap"] else np.nan,
                         "status": status.replace({"": None}),
                         "url": df[col["url"]] if col["url"] else None})
    rank = {"operating": 0, "operating pre-retirement": 0, "active": 0, "construction": 1, "in construction": 1,
            "under construction": 1, "mothballed": 2, "idle": 2}
    rows["_rank"] = rows["status"].map(lambda s: rank.get(s, 3) if isinstance(s, str) else 3)
    rows = rows.sort_values("_rank")
    out = rows.groupby("source_id", sort=False).agg(
        name=("name", "first"), kind=("kind", "first"), lat=("lat", "first"), lon=("lon", "first"),
        capacity_mw=("capacity_mw", lambda s: s.sum(min_count=1)), status=("status", "first"),
        url=("url", "first")).reset_index()
    return _finish(out, "gem")


def load_vnf(csv_path: str | Path, min_days: int = 5, min_temp_k: float = 1400.0, cell_deg: float = 0.005) -> pd.DataFrame:
    """VIIRS Nightfire detections (daily or annual CSV) -> persistent flare sites.

    A site is a ~550 m cell with >= ``min_days`` distinct nights of a hot (>= ``min_temp_k``) source.
    A file that has no date column is taken as a list of sites as it is.
    """
    df = pd.read_csv(csv_path, low_memory=False)
    low = {c.lower(): c for c in df.columns}
    lat = low.get("lat_gmtco") or low.get("latitude") or low.get("lat")
    lon = low.get("lon_gmtco") or low.get("longitude") or low.get("lon")
    if not (lat and lon):
        raise ValueError("no latitude/longitude columns in VNF file")
    df = df.assign(_lat=pd.to_numeric(df[lat], errors="coerce"),
                   _lon=pd.to_numeric(df[lon], errors="coerce")).dropna(subset=["_lat", "_lon"])
    temp, date = low.get("temp_bb"), low.get("date_mscan") or low.get("date")
    if temp:
        df = df[pd.to_numeric(df[temp], errors="coerce") >= min_temp_k]
    df["_cell"] = ((df["_lat"] / cell_deg).round().astype(int).astype(str) + "_"
                   + (df["_lon"] / cell_deg).round().astype(int).astype(str))
    if date:
        g = df.groupby("_cell").agg(lat=("_lat", "mean"), lon=("_lon", "mean"), days=(date, "nunique"))
        g = g[g.days >= min_days]
    else:
        g = df.groupby("_cell").agg(lat=("_lat", "mean"), lon=("_lon", "mean"))
        g["days"] = np.nan
    out = pd.DataFrame({"source_id": "vnf:" + g.index.astype(str), "name": None, "kind": "gas_flare",
                        "lat": g.lat.to_numpy(), "lon": g.lon.to_numpy(),
                        "status": [f"{int(d)} nights" if pd.notna(d) else None for d in g.days]})
    return _finish(out, "vnf")


def discover_raw(raw_dir: str | Path) -> dict:
    """Find source files dropped into ``raw_dir`` (any depth): WRI CSV, GEM tracker exports, VNF CSVs.

    Returns {"wri": path | None, "gem": [(path, tracker), ...], "vnf": [path, ...], "unrecognised": [...]}.
    """
    found: dict = {"wri": None, "gem": [], "vnf": [], "unrecognised": []}
    for f in sorted(Path(raw_dir).rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".csv", ".xlsx", ".xls"):
            continue
        name = f.name.lower()
        if "power_plant_database" in name:
            found["wri"] = f
        elif "vnf" in name or "nightfire" in name or "vnf" in [p.lower() for p in f.parent.parts]:
            found["vnf"].append(f)
        elif (tracker := next((t for pat, t in GEM_FILE_PATTERNS if re.search(pat, name)), None)):
            found["gem"].append((f, tracker))
        else:
            found["unrecognised"].append(f)
    return found
