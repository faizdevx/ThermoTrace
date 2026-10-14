"""Merge facility sources into one table, keeping every source id.

Greedy anchor clustering (not transitive, so chains of points cannot snowball): records are ordered by
specific-before-generic kind and source priority; each unassigned record becomes an anchor and absorbs
unassigned records within ``max_distance_m`` whose kind is compatible. The anchor supplies coordinates and
kind, the highest-priority named record the name; every member is kept in the links table
(``facility_sources``).

Capacity: summed within one source (a plant listed as several units/records), then the largest source
total is taken - never summed across sources, which describe the same plant.
"""
from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
from shapely import wkt as shapely_wkt
from sklearn.neighbors import BallTree

from src.thermal.sites import KINDS

from .sources import MAX_RADIUS_M, SOURCES

EARTH_R = 6_371_000.0
_NS = uuid.UUID("5a1d3c9e-4d8f-4a55-9d6e-7b7c0f3a2c11")
_GENERIC = "factory_generic"

FACILITY_COLUMNS = ["facility_id", "name", "kind", "lat", "lon", "radius_m", "capacity_mw", "status", "fuel",
                    "primary_source", "source_count", "sources", "attribution", "footprint"]
LINK_COLUMNS = ["facility_id", "source", "source_id", "name", "distance_m", "url", "licence", "attribution"]


def _compatible(a: str, b: str) -> bool:
    return a == b or _GENERIC in (a, b)


def _distance_to_anchor(df: pd.DataFrame) -> np.ndarray:
    a = np.radians(df.loc[df["_anchor"].to_numpy(), ["lat", "lon"]].to_numpy(dtype=float))
    p = np.radians(df[["lat", "lon"]].to_numpy(dtype=float))
    h = np.sin((p[:, 0] - a[:, 0]) / 2) ** 2 + np.cos(p[:, 0]) * np.cos(a[:, 0]) * np.sin((p[:, 1] - a[:, 1]) / 2) ** 2
    return np.round(2 * EARTH_R * np.arcsin(np.sqrt(h)), 1)


def _first(values) -> object:
    return next((v for v in values if isinstance(v, str) and v.strip()), None)


def _largest_footprint(values) -> str | None:
    best, best_area = None, 0.0
    for v in values:
        if isinstance(v, str) and v:
            try:
                area = shapely_wkt.loads(v).area
            except Exception:  # noqa: BLE001 - a broken polygon is skipped, never fatal
                continue
            if area > best_area:
                best, best_area = v, area
    return best


def merge(frames: list[pd.DataFrame], max_distance_m: float = 500.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(facilities, links)``; ``links`` has one row per source record."""
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=FACILITY_COLUMNS), pd.DataFrame(columns=LINK_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["_prio"] = df["source"].map(lambda s: SOURCES[s]["priority"])
    df["_generic"] = (df["kind"] == _GENERIC).astype(int)  # specific kinds anchor first
    df = df.sort_values(["_generic", "_prio", "source", "source_id"]).reset_index(drop=True)

    coords = np.radians(df[["lat", "lon"]].to_numpy(dtype=float))
    near = BallTree(coords, metric="haversine").query_radius(coords, r=max_distance_m / EARTH_R)
    anchor = np.full(len(df), -1, dtype=int)
    kinds = df["kind"].to_numpy()
    for i in range(len(df)):
        if anchor[i] >= 0:
            continue
        anchor[i] = i
        for j in near[i]:
            if anchor[j] < 0 and _compatible(kinds[i], kinds[j]):
                anchor[j] = i
    df["_anchor"] = anchor
    df["facility_id"] = [str(uuid.uuid5(_NS, f"{df.at[a, 'source']}:{df.at[a, 'source_id']}")) for a in anchor]
    df["distance_m"] = _distance_to_anchor(df)
    df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce")

    rows = []
    for a, grp in df.groupby("_anchor", sort=False):
        head = df.loc[a]
        specific = [k for k in grp["kind"] if k != _GENERIC]
        kind = head["kind"] if head["kind"] != _GENERIC or not specific else specific[0]
        per_source = grp.groupby("source")["capacity_mw"].sum(min_count=1)
        cap = per_source.max()
        order = sorted(set(grp["source"]), key=lambda s: SOURCES[s]["priority"])
        rows.append({
            "facility_id": head["facility_id"], "name": _first(grp["name"]), "kind": kind,
            "lat": float(head["lat"]), "lon": float(head["lon"]),
            # the merged circle covers every member's own circle: a heat source that matched the OSM
            # outline must still match after a WRI/GEM point (a few hundred metres away) becomes the anchor
            "radius_m": float(min(max((grp["radius_m"] + grp["distance_m"]).max(), KINDS[kind].radius_m), MAX_RADIUS_M)),
            "capacity_mw": None if pd.isna(cap) else float(cap),
            "status": _first(grp["status"]), "fuel": _first(grp["fuel"]),
            "primary_source": head["source"], "source_count": len(order), "sources": ",".join(order),
            "attribution": "; ".join(SOURCES[s]["attribution"] for s in order),
            "footprint": _largest_footprint(grp["footprint"]),
        })
    links = df[["facility_id", "source", "source_id", "name", "distance_m", "url"]].copy()
    links["licence"] = links["source"].map(lambda s: SOURCES[s]["licence"])
    links["attribution"] = links["source"].map(lambda s: SOURCES[s]["attribution"])
    return pd.DataFrame(rows, columns=FACILITY_COLUMNS), links[LINK_COLUMNS].reset_index(drop=True)
