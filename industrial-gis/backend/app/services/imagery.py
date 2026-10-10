"""Sentinel-2 L2A image chips for a thermal source (true colour + SWIR false colour).

Data: Copernicus Sentinel-2 L2A via the free Element84 Earth Search STAC API and its public COGs
(no key). Contains modified Copernicus Sentinel data. The clearest scene within +-5 days of the source's
last detection and under 30 % scene cloud cover is used; no scene -> an honest "not available".

SWIR composite: R = B12 (2190 nm), G = B11 (1610 nm), B = B8A (865 nm). Active flames, hot slag and fresh
burn scars stand out bright orange/red; vegetation is green, water black, clouds white.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import threading
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
WINDOW_DAYS = 5
MAX_CLOUD = 30.0
HALF_M = 1500.0          # 3 x 3 km chip
PX = 300                 # 10 m pixels
TRUE_BANDS = ("red", "green", "blue")
SWIR_BANDS = ("swir22", "swir16", "nir08")
MIN_VALID = 0.6          # share of non-zero pixels required (scene edge / missing tile otherwise)
UA = "ThermoTrace-imagery/1.0"

_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def pick_scenes(features: list[dict], centre: datetime, window_days: int = WINDOW_DAYS,
                max_cloud: float = MAX_CLOUD) -> list[dict]:
    """Candidates within the window and under the cloud limit, clearest first (ties: closest in time)."""
    out = []
    for f in features:
        props = f.get("properties", {})
        cloud = props.get("eo:cloud_cover")
        when = props.get("datetime")
        if cloud is None or when is None or cloud >= max_cloud:
            continue
        t = datetime.fromisoformat(when.replace("Z", "+00:00"))
        offset = (t - centre).total_seconds() / 86400
        if abs(offset) > window_days + 1:       # window is whole days around the detection day
            continue
        if not all(b in f.get("assets", {}) for b in TRUE_BANDS + SWIR_BANDS):
            continue
        out.append({"feature": f, "cloud": float(cloud), "offset_days": round(offset, 2), "time": t})
    return sorted(out, key=lambda c: (round(c["cloud"], 1), abs(c["offset_days"])))


def search(lon: float, lat: float, centre: datetime, timeout: int = 40) -> list[dict]:
    start = (centre - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    end = (centre + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT23:59:59Z")
    body = {"collections": [COLLECTION], "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{start}/{end}", "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}}, "limit": 50}
    req = urllib.request.Request(STAC_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("features", [])


def _use_bundled_proj_data() -> None:
    """Point PROJ at the data bundled with rasterio.

    Other software (e.g. a PostgreSQL/PostGIS install) often sets PROJ_DATA/PROJ_LIB to its own, possibly
    older proj.db, which makes UTM reprojection fail with "The EPSG code is unknown". Must run before GDAL loads.
    """
    spec = importlib.util.find_spec("rasterio")
    for base in (spec.submodule_search_locations or []) if spec else []:
        bundled = os.path.join(base, "proj_data")
        if os.path.isdir(bundled):
            os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = bundled
            return


def _read(href: str, lon: float, lat: float) -> np.ndarray:
    _use_bundled_proj_data()
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import from_bounds

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT="30",
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(href) as ds:
            x, y = transform("EPSG:4326", ds.crs, [lon], [lat])
            win = from_bounds(x[0] - HALF_M, y[0] - HALF_M, x[0] + HALF_M, y[0] + HALF_M, ds.transform)
            return ds.read(1, window=win, out_shape=(PX, PX), boundless=True, fill_value=0,
                           resampling=rasterio.enums.Resampling.bilinear).astype("float32")


def _png_data_uri(rgb: np.ndarray) -> str:
    from rasterio.io import MemoryFile
    with MemoryFile() as mem:
        with mem.open(driver="PNG", width=rgb.shape[2], height=rgb.shape[1], count=3, dtype="uint8") as dst:
            dst.write(rgb)
        data = mem.read()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def _stretch(bands: list[np.ndarray], top: float, gamma: float) -> np.ndarray:
    arr = np.stack(bands) / 10000.0           # L2A reflectance scale
    arr = np.clip(arr / top, 0, 1) ** gamma
    return (arr * 255).astype("uint8")


def render(feature: dict, lon: float, lat: float) -> dict[str, Any] | None:
    """Read both composites for one scene; None when the chip is mostly empty."""
    assets = feature["assets"]
    true = [_read(assets[b]["href"], lon, lat) for b in TRUE_BANDS]
    if np.count_nonzero(true[0]) / true[0].size < MIN_VALID:
        return None
    swir = [_read(assets[b]["href"], lon, lat) for b in SWIR_BANDS]
    return {"true_colour": _png_data_uri(_stretch(true, 0.30, 0.8)),
            "swir": _png_data_uri(_stretch(swir, 0.45, 0.8))}


def chip_for(source_id: str, lon: float, lat: float, last_seen: str) -> dict[str, Any]:
    """Cached per (source, last detection). Never raises for 'no scene'; raises only on service failure."""
    key = f"{source_id}|{last_seen}"
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    centre = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
    if centre.tzinfo is None:
        centre = centre.replace(tzinfo=timezone.utc)
    base = {"available": False, "window_km": HALF_M * 2 / 1000, "search": {
        "collection": COLLECTION, "days_either_side": WINDOW_DAYS, "max_cloud_percent": MAX_CLOUD,
        "centre_time": centre.isoformat(), "stac": STAC_URL}}
    candidates = pick_scenes(search(lon, lat, centre), centre)
    result = {**base, "reason": f"No Sentinel-2 scene under {MAX_CLOUD:.0f}% cloud within {WINDOW_DAYS} days of the "
                                f"last detection ({len(candidates)} candidate(s))."}
    for cand in candidates[:3]:
        images = render(cand["feature"], lon, lat)
        if images is None:
            continue
        f = cand["feature"]
        result = {**base, "available": True, "reason": None, **images, "scene": {
            "id": f["id"], "datetime": cand["time"].isoformat(), "cloud_cover_percent": cand["cloud"],
            "offset_days": cand["offset_days"], "platform": f["properties"].get("platform"),
            "mgrs_tile": f["properties"].get("grid:code"),
            "stac_item": next((l["href"] for l in f.get("links", []) if l.get("rel") == "self"), None)},
            "composites": {"true_colour": "B04/B03/B02", "swir": "B12/B11/B8A"},
            "attribution": "Contains modified Copernicus Sentinel data (ESA); Earth Search by Element 84"}
        break
    with _lock:
        _cache[key] = result
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return result
