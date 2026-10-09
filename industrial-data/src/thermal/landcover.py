"""Land cover from ESA WorldCover 2021 v200 (10 m, CC BY 4.0) read straight from the public COGs.

Only a small window per source is read over HTTP range requests; results are cached on disk by a
0.002 degree cell so reruns are instant. Tiles are 3x3 degree COGs on a public S3 bucket (no key).
"""
from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TILE_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
            "ESA_WorldCover_10m_2021_v200_{ns}{lat:02d}{ew}{lon:03d}_Map.tif")
CLASSES = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare",
           70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}
GROUPS = {"vegetation": ("tree", "shrub", "grass", "mangrove", "wetland", "moss"),
          "crop": ("crop",), "built": ("built",), "bare": ("bare",), "water": ("water", "snow")}
ATTRIBUTION = "ESA WorldCover 2021 v200 © ESA, CC BY 4.0"
_local = threading.local()


def tile_for(lat: float, lon: float) -> tuple[str, int]:
    tlat = math.floor(lat / 3) * 3
    tlon = math.floor(lon / 3) * 3
    url = TILE_URL.format(ns="N" if tlat >= 0 else "S", lat=abs(tlat), ew="E" if tlon >= 0 else "W", lon=abs(tlon))
    return url, 0


def _open(url: str):
    import rasterio
    cache = getattr(_local, "ds", None)
    if cache is None:
        cache = _local.ds = {}
    if url not in cache:
        try:
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                              GDAL_HTTP_MULTIPLEX="YES", GDAL_HTTP_TIMEOUT="30"):
                cache[url] = rasterio.open(url)
        except Exception:  # noqa: BLE001 - tile missing (open ocean) or network error
            cache[url] = None
    return cache[url]


def sample_one(lat: float, lon: float, radius_m: float = 150.0) -> dict[str, float]:
    """Fractions of land-cover groups within ``radius_m`` of the point (empty dict if no data)."""
    import rasterio
    from rasterio.windows import Window
    url, _ = tile_for(lat, lon)
    ds = _open(url)
    if ds is None:
        return {}
    row, col = ds.index(lon, lat)
    half = max(int(radius_m / 10), 1)
    win = Window(col - half, row - half, 2 * half + 1, 2 * half + 1)
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            arr = ds.read(1, window=win, boundless=True, fill_value=0)
    except Exception:  # noqa: BLE001
        return {}
    yy, xx = np.ogrid[-half:half + 1, -half:half + 1]
    arr = arr[(yy ** 2 + xx ** 2) <= half ** 2]
    arr = arr[arr > 0]
    if arr.size == 0:
        return {}
    names = np.vectorize(lambda v: CLASSES.get(int(v), "other"))(arr)
    out = {g: float(np.isin(names, members).mean()) for g, members in GROUPS.items()}
    dominant = pd.Series(names).value_counts()
    out["dominant"] = dominant.index[0]
    return out


def sample_many(points: pd.DataFrame, cache_path: str | Path | None = None, workers: int = 8) -> pd.DataFrame:
    """``points`` needs lat/lon. Returns same index with lc_* columns (NaN when unavailable)."""
    cache: dict[str, dict] = {}
    cache_file = Path(cache_path) if cache_path else None
    if cache_file and cache_file.exists():
        cache = pd.read_json(cache_file, orient="index").to_dict("index")
    keys = points.apply(lambda r: f"{round(r.lat / 0.002) * 0.002:.3f},{round(r.lon / 0.002) * 0.002:.3f}", axis=1)
    todo = sorted({k for k in keys if k not in cache})
    logger.info("land cover: %d cells to read (%d cached)", len(todo), len(set(keys)) - len(todo))

    def work(key: str) -> tuple[str, dict]:
        lat, lon = (float(x) for x in key.split(","))
        return key, sample_one(lat, lon)

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, res in pool.map(work, todo):
                cache[key] = res or {"vegetation": None, "crop": None, "built": None, "bare": None,
                                     "water": None, "dominant": None}
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame.from_dict(cache, orient="index").to_json(cache_file, orient="index")
    frame = pd.DataFrame([cache.get(k, {}) for k in keys], index=points.index)
    for col in ("vegetation", "crop", "built", "bare", "water", "dominant"):
        if col not in frame:
            frame[col] = np.nan
    return frame[["vegetation", "crop", "built", "bare", "water", "dominant"]].add_prefix("lc_")
