"""Industrial facility index used to decide whether a thermal source sits on a known facility.

Facilities come from OpenStreetMap (GeoJSON baseline from scripts/fetch_thermal_sites.py, or PostGIS
``industrial_sites`` when a database is available). Each facility is mapped to a *thermal kind* with a
prior for how likely it is to show a persistent signature and a matching radius (VIIRS pixels are
375 m and geolocation error is a few hundred metres, so a buffer is always added).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_R = 6_371_000.0
VIIRS_BUFFER_M = 500.0


@dataclass(frozen=True)
class Kind:
    name: str
    continuous: float      # prior that the facility emits a (near-)continuous thermal signature
    radius_m: float        # default matching radius for point-represented facilities


KINDS: dict[str, Kind] = {
    "refinery_petrochem": Kind("refinery_petrochem", 0.95, 1500),
    "gas_flare": Kind("gas_flare", 1.00, 400),
    "steel_metal": Kind("steel_metal", 0.90, 1500),
    "power_thermal": Kind("power_thermal", 0.85, 1500),
    "cement": Kind("cement", 0.80, 1200),
    "chemical_fertilizer": Kind("chemical_fertilizer", 0.70, 1200),
    "brick_kiln": Kind("brick_kiln", 0.60, 400),
    "mining_quarry": Kind("mining_quarry", 0.45, 1800),
    "landfill": Kind("landfill", 0.35, 1000),
    "factory_generic": Kind("factory_generic", 0.30, 800),
}


def kind_from_tags(tags: dict[str, Any]) -> str | None:
    """Map OSM tags to a thermal kind (None = not thermally relevant)."""
    g = lambda k: str(tags.get(k, "")).lower()  # noqa: E731
    if g("man_made") == "flare":
        return "gas_flare"
    ind = g("industrial")
    if g("power") in ("plant", "generator"):
        src = g("plant:source") or g("generator:source")
        if any(x in src for x in ("solar", "wind", "hydro", "tidal", "geothermal")) and "coal" not in src:
            return None
        return "power_thermal" if not src or any(x in src for x in ("coal", "gas", "oil", "diesel", "biomass", "waste", "nuclear")) else None
    if ind in ("refinery", "oil", "gas", "petroleum") or g("man_made") == "petroleum_well":
        return "refinery_petrochem"
    if ind in ("steel", "metal", "metal_processing", "smelter", "foundry"):
        return "steel_metal"
    if ind == "cement":
        return "cement"
    if ind in ("chemical", "fertilizer"):
        return "chemical_fertilizer"
    if ind == "brickworks":
        return "brick_kiln"
    if ind in ("mine", "mining", "quarry") or g("landuse") == "quarry":
        return "mining_quarry"
    if g("landuse") == "landfill":
        return "landfill"
    if g("man_made") == "works" or ind in ("factory", "sugar", "power"):
        return "factory_generic"
    return None


@dataclass
class SiteMatch:
    site_ref: str | None
    site_name: str | None
    kind: str | None
    distance_m: float | None
    within: bool           # inside the facility's matching radius (+ VIIRS buffer)
    continuous_prior: float


class SiteIndex:
    def __init__(self, sites: pd.DataFrame):
        """``sites`` columns: ref, name, kind, lat, lon, radius_m."""
        self.sites = sites.reset_index(drop=True)
        self._tree = BallTree(np.radians(self.sites[["lat", "lon"]].to_numpy()), metric="haversine") \
            if len(self.sites) else None

    def __len__(self) -> int:
        return len(self.sites)

    @classmethod
    def from_geojson(cls, path: str | Path) -> "SiteIndex":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = []
        for f in data["features"]:
            props = f.get("properties", {})
            tags = props.get("tags") or {}
            kind = props.get("kind") or kind_from_tags(tags)
            if kind is None or f["geometry"]["type"] != "Point":
                continue
            lon, lat = f["geometry"]["coordinates"][:2]
            rows.append({"ref": props.get("source_ref") or props.get("site_id"), "name": props.get("name"),
                         "kind": kind, "lat": lat, "lon": lon,
                         "radius_m": props.get("radius_m") or KINDS[kind].radius_m})
        return cls(pd.DataFrame(rows, columns=["ref", "name", "kind", "lat", "lon", "radius_m"]))

    @classmethod
    def from_postgis(cls, dsn: str) -> "SiteIndex":
        import psycopg2
        sql = """SELECT site_id, name, tags, ST_Y(ST_PointOnSurface(geometry)), ST_X(ST_PointOnSurface(geometry)),
                        CASE WHEN GeometryType(geometry) LIKE '%%POLYGON'
                             THEN sqrt(ST_Area(geometry::geography) / pi()) END
                 FROM industrial_sites WHERE is_active"""
        rows = []
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            for site_id, name, tags, lat, lon, eq_radius in cur:
                kind = kind_from_tags(tags or {})
                if kind is None:
                    continue
                radius = max(KINDS[kind].radius_m, float(eq_radius)) if eq_radius else KINDS[kind].radius_m
                rows.append({"ref": site_id, "name": name, "kind": kind, "lat": lat, "lon": lon, "radius_m": min(radius, 6000)})
        return cls(pd.DataFrame(rows, columns=["ref", "name", "kind", "lat", "lon", "radius_m"]))

    def match(self, lats: np.ndarray, lons: np.ndarray, k: int = 8) -> list[SiteMatch]:
        """Best facility per point: the one with the highest (radius - distance) margin."""
        none = SiteMatch(None, None, None, None, False, 0.0)
        if self._tree is None or len(lats) == 0:
            return [none] * len(lats)
        k = min(k, len(self.sites))
        dist, idx = self._tree.query(np.radians(np.column_stack([lats, lons])), k=k)
        radii = self.sites["radius_m"].to_numpy()
        out: list[SiteMatch] = []
        for drow, irow in zip(dist * EARTH_R, idx):
            margin = radii[irow] + VIIRS_BUFFER_M - drow
            j = int(np.argmax(margin))
            site = self.sites.iloc[int(irow[j])]
            within = bool(margin[j] >= 0)
            nearest = int(np.argmin(drow))
            if not within:   # report the nearest facility for context even if outside its radius
                site = self.sites.iloc[int(irow[nearest])]
                d = float(drow[nearest])
            else:
                d = float(drow[j])
            out.append(SiteMatch(site["ref"], site["name"], site["kind"], d, within,
                                 KINDS[site["kind"]].continuous if within else 0.0))
        return out


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))
