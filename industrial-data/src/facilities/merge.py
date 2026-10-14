"""Merge facility sources into one table, keeping every source id.

Greedy anchor clustering (not transitive, so chains of points cannot snowball): records are ordered by
source priority; each unassigned record becomes an anchor and absorbs unassigned records within
``max_distance_m`` whose kind is compatible. The anchor supplies coordinates, kind and name; every member
is kept in the links table (``facility_sources``).
"""
from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from src.thermal.sites import KINDS

from .sources import SOURCES

EARTH_R = 6_371_000.0
_NS = uuid.UUID("5a1d3c9e-4d8f-4a55-9d6e-7b7c0f3a2c11")
_GENERIC = "factory_generic"


def _compatible(a: str, b: str) -> bool:
    return a == b or _GENERIC in (a, b)


def _distance_to_anchor(df: pd.DataFrame) -> np.ndarray:
    a = np.radians(df.loc[df["_anchor"].to_numpy(), ["lat", "lon"]].to_numpy())
    p = np.radians(df[["lat", "lon"]].to_numpy())
    h = np.sin((p[:, 0] - a[:, 0]) / 2) ** 2 + np.cos(p[:, 0]) * np.cos(a[:, 0]) * np.sin((p[:, 1] - a[:, 1]) / 2) ** 2
    return np.round(2 * EARTH_R * np.arcsin(np.sqrt(h)), 1)


def merge(frames: list[pd.DataFrame], max_distance_m: float = 500.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(facilities, links)``; ``links`` has one row per source record."""
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["_prio"] = df["source"].map(lambda s: SOURCES[s]["priority"])
    df["_generic"] = (df["kind"] == _GENERIC).astype(int)  # specific kinds anchor first
    df = df.sort_values(["_generic", "_prio", "source", "source_id"]).reset_index(drop=True)

    coords = np.radians(df[["lat", "lon"]].to_numpy())
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

    rows = []
    for a, grp in df.groupby("_anchor", sort=False):
        head = df.loc[a]
        names = [n for n in grp["name"] if isinstance(n, str) and n.strip()]
        specific = [k for k in grp["kind"] if k != _GENERIC]
        kind = head["kind"] if head["kind"] != _GENERIC or not specific else specific[0]
        cap = pd.to_numeric(grp["capacity_mw"], errors="coerce").max()
        order = sorted(set(grp["source"]), key=lambda s: SOURCES[s]["priority"])
        rows.append({
            "facility_id": head["facility_id"], "name": names[0] if names else None, "kind": kind,
            "lat": float(head["lat"]), "lon": float(head["lon"]),
            "radius_m": float(max(grp["radius_m"].max(), KINDS[kind].radius_m)),
            "capacity_mw": None if pd.isna(cap) else float(cap), "status": head["status"],
            "primary_source": head["source"], "source_count": len(order), "sources": ",".join(order),
            "attribution": "; ".join(SOURCES[s]["attribution"] for s in order),
        })
    links = df[["facility_id", "source", "source_id", "name", "distance_m", "url"]].copy()
    links["licence"] = links["source"].map(lambda s: SOURCES[s]["licence"])
    links["attribution"] = links["source"].map(lambda s: SOURCES[s]["attribution"])
    return pd.DataFrame(rows), links.reset_index(drop=True)
