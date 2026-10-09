"""End-to-end thermal analysis: FIRMS detections -> sources -> context -> classification."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from . import classifier, landcover, sources
from .sites import SiteIndex

logger = logging.getLogger(__name__)
GB_API = "https://www.geoboundaries.org/api/current"


@dataclass
class Analysis:
    detections: pd.DataFrame          # one row per FIRMS detection, with its source cluster + class
    sources: pd.DataFrame             # one row per thermal source, classified
    summary: dict = field(default_factory=dict)


def load_boundary(level: str, cache_dir: Path) -> gpd.GeoDataFrame:
    """geoBoundaries gbOpen (CC BY 4.0) ADM0/ADM1 for India, cached on disk."""
    from src.boundaries.source import create_retry_session, fetch_geoboundaries_metadata
    cache = cache_dir / f"india_{level.lower()}.geojson"
    if cache.exists():
        return gpd.read_file(cache)
    meta = fetch_geoboundaries_metadata(GB_API, "gbOpen", "IND", level)
    resp = create_retry_session().get(meta.download_url, timeout=180)
    resp.raise_for_status()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(resp.content)
    return gpd.read_file(cache)


def clip_to_india(det: pd.DataFrame, cache_dir: Path) -> pd.DataFrame:
    india = load_boundary("ADM0", cache_dir).to_crs(4326).union_all()
    inside = shapely.contains_xy(india, det.longitude.to_numpy(), det.latitude.to_numpy())
    logger.info("clip to India: kept %d of %d detections", inside.sum(), len(det))
    return det[inside].reset_index(drop=True)


def assign_state(src: pd.DataFrame, cache_dir: Path) -> pd.Series:
    states = load_boundary("ADM1", cache_dir).to_crs(4326)[["shapeName", "geometry"]]
    pts = gpd.GeoDataFrame(src[["cluster"]], geometry=gpd.points_from_xy(src.lon, src.lat), crs=4326)
    joined = gpd.sjoin(pts, states, how="left", predicate="within").drop_duplicates("cluster")
    return joined.set_index("cluster")["shapeName"].reindex(src.cluster).reset_index(drop=True)


def stable_ids(src: pd.DataFrame) -> list[str]:
    """Id from the ~1 km grid cell of the FRP-weighted centroid, so a source keeps its id across runs
    (cluster numbers change every run). Collisions inside one run get a deterministic suffix."""
    seen: dict[str, int] = {}
    ids: list[str] = []
    for lat, lon in zip(src.lat, src.lon):
        base = f"ts-{round(lat * 100):d}_{round(lon * 100):d}".replace("-", "m")
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}x{seen[base]}")
    return ids


def analyse(
    det: pd.DataFrame,
    site_index: SiteIndex | None,
    *,
    window_days: int = 7,
    use_landcover: bool = True,
    cache_dir: Path = Path("data/thermal"),
    clip: bool = True,
    with_states: bool = True,
) -> Analysis:
    if det.empty:
        return Analysis(det, pd.DataFrame(), {"detections": 0, "sources": 0})
    if clip:
        det = clip_to_india(det, cache_dir)
    det = sources.cluster_detections(det)
    src = sources.summarise(det, window_days)

    matches = site_index.match(src.lat.to_numpy(), src.lon.to_numpy()) if site_index is not None else []
    if matches:
        src["site_ref"] = [m.site_ref for m in matches]
        src["site_name"] = [m.site_name for m in matches]
        src["site_kind"] = [m.kind for m in matches]
        src["site_distance_m"] = [m.distance_m for m in matches]
        src["site_within"] = [m.within for m in matches]
        src["site_continuous"] = [m.continuous_prior for m in matches]
    else:
        for col, val in (("site_ref", None), ("site_name", None), ("site_kind", None),
                         ("site_distance_m", np.nan), ("site_within", False), ("site_continuous", 0.0)):
            src[col] = val

    if use_landcover:
        lc = landcover.sample_many(src[["lat", "lon"]], cache_dir / "landcover_cache.json")
        src = pd.concat([src, lc], axis=1)
    else:
        for col in ("lc_vegetation", "lc_crop", "lc_built", "lc_bare", "lc_water", "lc_dominant"):
            src[col] = np.nan

    month = int(src["last_seen"].dt.month.mode().iloc[0])
    src = classifier.classify_sources(src, month)
    if with_states:
        try:
            src["state"] = assign_state(src, cache_dir)
        except Exception as exc:  # noqa: BLE001 - boundary download is best effort
            logger.warning("state assignment skipped: %s", exc)
            src["state"] = None
    src["source_id"] = stable_ids(src)

    det = det.merge(src[["cluster", "source_id", "thermal_class"]], on="cluster", how="left")
    summary = {
        "detections": int(len(det)), "sources": int(len(src)),
        "window_days": window_days, "analysed_month": month,
        "by_class": src.thermal_class.value_counts().to_dict(),
        "detections_by_class": det.thermal_class.value_counts().to_dict(),
        "alerts": int(src.alert_level.notna().sum()),
        "facilities_indexed": 0 if site_index is None else len(site_index),
        "landcover_used": use_landcover,
        "latest_detection": str(det.acq_datetime.max()),
        "earliest_detection": str(det.acq_datetime.min()),
    }
    return Analysis(det, src, summary)


def to_geojson(a: Analysis) -> dict:
    feats = []
    for r in a.sources.to_dict("records"):
        props = {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v) for k, v in r.items()
                 if k not in ("lat", "lon", "cluster") and not (isinstance(v, float) and np.isnan(v))}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                      "properties": props})
    return {"type": "FeatureCollection", "features": feats,
            "metadata": {"summary": a.summary,
                         "attribution": ["NASA FIRMS (LANCE)", "OpenStreetMap contributors (ODbL)",
                                         landcover.ATTRIBUTION]}}


def write_outputs(a: Analysis, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "thermal_sources.geojson").write_text(json.dumps(to_geojson(a), default=str), encoding="utf-8")
    cols = [c for c in a.sources.columns if c not in ("class_scores", "reasons")]
    a.sources[cols].assign(reasons=a.sources.reasons.map(" | ".join)).to_csv(out_dir / "thermal_sources.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(a.summary, indent=2, default=str), encoding="utf-8")
