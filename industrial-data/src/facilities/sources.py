"""Loaders that turn each citable source into one normalised table.

Columns: source, source_id, name, kind, lat, lon, radius_m, capacity_mw, status, url.
Licence and attribution live in ``SOURCES`` (one place, written to every facility_sources row).
Nothing is downloaded here: each loader reads a file the operator obtained under that source's terms.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.thermal.sites import KINDS, kind_from_tags

COLUMNS = ["source", "source_id", "name", "kind", "lat", "lon", "radius_m", "capacity_mw", "status", "url"]

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
GEM_TRACKERS = {"coal_plant": "power_thermal", "gas_plant": "power_thermal", "steel": "steel_metal",
                "coal_mine": "mining_quarry", "oil_gas": "refinery_petrochem", "cement": "cement"}
_DROP_STATUS = {"cancelled", "canceled", "shelved", "retired", "decommissioned"}
_FIRST = {"lat": ("latitude", "lat", "y"), "lon": ("longitude", "lon", "lng", "x"),
          "name": ("plant name", "name", "unit name", "mine name", "project name", "facility name", "plant"),
          "id": ("gem unit id", "gem location id", "gem id", "plant id", "id", "gppd_idnr", "unit id"),
          "status": ("status", "plant status", "operating status"),
          "cap": ("capacity (mw)", "capacity_mw", "capacity (mtpa)", "capacity", "capacity (bcm/y)"),
          "country": ("country", "country/area", "country_long", "country_name"),
          "url": ("url", "wiki url", "wiki page")}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def _pick(df: pd.DataFrame, key: str) -> str | None:
    lower = {str(c).lower().strip(): c for c in df.columns}
    for cand in _FIRST[key]:
        if cand in lower:
            return lower[cand]
    return None


def _finish(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df.copy()
    df["source"] = source
    df = df[pd.notna(df["lat"]) & pd.notna(df["lon"]) & df["lat"].between(5, 38) & df["lon"].between(67, 99)]
    df["radius_m"] = [KINDS[k].radius_m for k in df["kind"]]
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    return df[COLUMNS].drop_duplicates(["source", "source_id"]).reset_index(drop=True)


def load_osm(geojson: str | Path) -> pd.DataFrame:
    data = json.loads(Path(geojson).read_text(encoding="utf-8"))
    rows = []
    for f in data["features"]:
        p = f.get("properties", {})
        kind = p.get("kind") or kind_from_tags(p.get("tags") or {})
        if kind is None or f["geometry"]["type"] != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        ref = p.get("source_ref") or p.get("site_id")
        rows.append({"source_id": ref, "name": p.get("name"), "kind": kind, "lat": lat, "lon": lon,
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
                        "status": df["primary_fuel"].astype(str),
                        "url": df["url"] if "url" in df else None})
    return _finish(out, "wri_gppd")


def load_gem(path: str | Path, tracker: str) -> pd.DataFrame:
    """A Global Energy Monitor tracker export (csv/xlsx). ``tracker`` picks the thermal kind."""
    if tracker not in GEM_TRACKERS:
        raise ValueError(f"tracker must be one of {sorted(GEM_TRACKERS)}")
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = next((f for f in pd.read_excel(p, sheet_name=None).values() if _pick(f, "lat") and _pick(f, "lon")), None)
        if df is None:
            raise ValueError("no sheet with latitude/longitude columns")
    else:
        df = pd.read_csv(p, low_memory=False)
    col = {k: _pick(df, k) for k in _FIRST}
    if not (col["lat"] and col["lon"]):
        raise ValueError("latitude/longitude columns not found")
    if col["country"]:
        df = df[df[col["country"]].astype(str).str.strip().str.lower().isin(["india", "ind", "in"])]
    if col["status"]:
        df = df[~df[col["status"]].astype(str).str.lower().str.strip().isin(_DROP_STATUS)]
    idx = df[col["id"]] if col["id"] else pd.Series(df.index.astype(str), index=df.index)
    out = pd.DataFrame({"source_id": f"{tracker}:" + idx.astype(str), "kind": GEM_TRACKERS[tracker],
                        "name": df[col["name"]] if col["name"] else None,
                        "lat": pd.to_numeric(df[col["lat"]], errors="coerce"),
                        "lon": pd.to_numeric(df[col["lon"]], errors="coerce"),
                        "capacity_mw": pd.to_numeric(df[col["cap"]], errors="coerce") if col["cap"] else np.nan,
                        "status": df[col["status"]].astype(str) if col["status"] else None,
                        "url": df[col["url"]] if col["url"] else None})
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
