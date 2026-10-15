"""Satellite imagery verification for a thermal source (shared by the API and the worker).

Data (all open, no key):
  * Copernicus Sentinel-2 L2A via the Element 84 Earth Search STAC API and its public COGs
    (contains modified Copernicus Sentinel data, ESA)
  * Landsat 8/9 Collection 2 Level-2 surface temperature (USGS) from the same catalogue

What is produced for one source (3 x 3 km window around it):
  * AFTER scene   clearest Sentinel-2 scene within +-5 days of the last detection, scene cloud < 30 %
  * BEFORE scene  clearest scene 2-40 days earlier (same tile preferred) - the reference for burn scars
  * chips         true colour (B04/B03/B02) and SWIR false colour (B12/B11/B8A) for each, PNG data URIs
  * NBR / dNBR    NBR = (B8A - B12) / (B8A + B12); dNBR = NBR_before - NBR_after on cloud-free land pixels,
                  summarised as p95, burned-area fraction (dNBR > 0.1) and a USGS severity class
  * SWIR hot pixels  pixels with B12 >= 0.25 reflectance and B12 > 1.1 x B11 (flames / hot slag raise SWIR2 above SWIR1)
  * Landsat TIRS  independent thermal check: surface-temperature maximum and its excess over the chip median

Honest limits: Sentinel-2 passes at ~10:30 local time, so it sees only what is burning or scarred then; a scene may
not exist (cloud, monsoon) and the result then says so. Co-registration between two orbits is ~1 pixel at best.
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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
# Landsat on Earth Search lives in a requester-pays S3 bucket; Planetary Computer serves the same USGS data over HTTPS
# with an anonymous short-lived SAS token (no account).
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/landsateuwest/landsat-c2"
S2 = "sentinel-2-l2a"
LANDSAT = "landsat-c2-l2"
WINDOW_DAYS = 5
LANDSAT_WINDOW_DAYS = 8             # 16-day revisit per satellite, 8 with Landsat 8 + 9
BEFORE_MIN_DAYS, BEFORE_MAX_DAYS = 2, 40
MAX_CLOUD = 30.0
HALF_M = 1500.0                     # 3 x 3 km
PX = 300                            # chip pixels (10 m)
TRUE_BANDS = ("red", "green", "blue")
SWIR_BANDS = ("swir22", "swir16", "nir08")      # B12, B11, B8A
MIN_VALID = 0.6
HOT_B12 = 0.25
HOT_RATIO = 1.1
BURNED_DNBR = 0.1
UA = "ThermoTrace-imagery/2.0"
# SCL classes treated as clean land for the indices: dark features 2 (coal, slag, shadowed relief), cloud shadow 3
# (SCL flags dark mine surfaces as shadow; NBR is a normalised ratio and barely moves under shadow), vegetation 4,
# bare 5, unclassified 7. Excluded: no data 0, saturated 1, water 6, cloud 8-10, snow 11.
SCL_LAND = (2, 3, 4, 5, 7)
SCL_CLOUD = (8, 9, 10)
MAX_CHIP_CLOUD = 0.4          # a scene whose 3 x 3 km chip is more than 40 % cloud is passed over when a better one exists

_lock = threading.Lock()
_mem: "OrderedDict[str, dict]" = OrderedDict()


# --------------------------------------------------------------------------- STAC
def stac_search(collection: str, lon: float, lat: float, start: datetime, end: datetime, max_cloud: float = MAX_CLOUD,
                timeout: int = 40, url: str = STAC_URL) -> list[dict]:
    body = {"collections": [collection], "intersects": {"type": "Point", "coordinates": [lon, lat]},
            "datetime": f"{start:%Y-%m-%dT00:00:00Z}/{end:%Y-%m-%dT23:59:59Z}",
            "query": {"eo:cloud_cover": {"lt": max_cloud}}, "limit": 100}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("features", [])


def _parse_time(value: str) -> datetime:
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _usable(f: dict, bands: tuple[str, ...], max_cloud: float) -> tuple[float, datetime] | None:
    props = f.get("properties", {})
    cloud, when = props.get("eo:cloud_cover"), props.get("datetime")
    if cloud is None or when is None or cloud >= max_cloud:
        return None
    if not all(b in f.get("assets", {}) for b in bands):
        return None
    return float(cloud), _parse_time(when)


def pick_after(features: list[dict], centre: datetime, window_days: int = WINDOW_DAYS, max_cloud: float = MAX_CLOUD,
               bands: tuple[str, ...] = TRUE_BANDS + SWIR_BANDS, not_before: datetime | None = None) -> list[dict]:
    """Candidates within the window and under the cloud limit, clearest first (ties: closest in time).

    ``not_before``: a scene acquired before the first detection cannot show the fire or its scar; it is only useful
    as the BEFORE reference, so it is not a candidate for AFTER."""
    out = []
    for f in features:
        u = _usable(f, bands, max_cloud)
        if u is None:
            continue
        cloud, t = u
        offset = (t - centre).total_seconds() / 86400
        if abs(offset) > window_days + 1:           # whole days around the detection day
            continue
        if not_before is not None and t < not_before - timedelta(hours=12):
            continue
        out.append({"feature": f, "cloud": cloud, "offset_days": round(offset, 2), "time": t})
    return sorted(out, key=lambda c: (round(c["cloud"], 1), abs(c["offset_days"])))


def pick_before(features: list[dict], after: dict, min_days: int = BEFORE_MIN_DAYS, max_days: int = BEFORE_MAX_DAYS,
                max_cloud: float = MAX_CLOUD) -> dict | None:
    """Clearest earlier scene 2-40 days before the AFTER scene; the same MGRS tile / orbit is preferred because it
    removes most geometric and illumination differences."""
    tile = after["feature"]["properties"].get("grid:code")
    orbit = after["feature"]["properties"].get("sat:relative_orbit")
    best = []
    for f in features:
        u = _usable(f, TRUE_BANDS + SWIR_BANDS, max_cloud)
        if u is None or f["id"] == after["feature"]["id"]:
            continue
        cloud, t = u
        gap = (after["time"] - t).total_seconds() / 86400
        if not (min_days <= gap <= max_days):
            continue
        same = f["properties"].get("grid:code") == tile
        same_orbit = f["properties"].get("sat:relative_orbit") == orbit
        best.append({"feature": f, "cloud": cloud, "time": t, "gap_days": round(gap, 1), "same_tile": same,
                     "_rank": (0 if same and same_orbit else 1 if same else 2, round(cloud, 1), gap)})
    if not best:
        return None
    pick = min(best, key=lambda c: c["_rank"])
    pick.pop("_rank")
    return pick


# --------------------------------------------------------------------------- raster reads
def _use_bundled_proj_data() -> None:
    """Point PROJ at the data bundled with rasterio (a PostgreSQL install often exports an older proj.db)."""
    spec = importlib.util.find_spec("rasterio")
    for base in (spec.submodule_search_locations or []) if spec else []:
        bundled = os.path.join(base, "proj_data")
        if os.path.isdir(bundled):
            os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = bundled
            return


def read_window(href: str, lon: float, lat: float, out_px: int = PX, half_m: float = HALF_M,
                nearest: bool = False) -> np.ndarray:
    _use_bundled_proj_data()
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform
    from rasterio.windows import from_bounds

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT="30", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(href) as ds:
            x, y = transform("EPSG:4326", ds.crs, [lon], [lat])
            win = from_bounds(x[0] - half_m, y[0] - half_m, x[0] + half_m, y[0] + half_m, ds.transform)
            return ds.read(1, window=win, out_shape=(out_px, out_px), boundless=True, fill_value=0,
                           resampling=Resampling.nearest if nearest else Resampling.bilinear).astype("float32")


def reflectance(dn: np.ndarray, asset: dict) -> np.ndarray:
    """DN -> surface reflectance using the asset's own scale / offset (L2A baseline >= 04.00 carries -0.1)."""
    rb = (asset.get("raster:bands") or [{}])[0]
    scale, offset = float(rb.get("scale", 1e-4)), float(rb.get("offset", 0.0))
    out = dn * scale + offset
    out[dn == 0] = np.nan                       # no data
    return out


def chip_cloud_fraction(feature: dict, lon: float, lat: float) -> float | None:
    """Cloud share of the 3 x 3 km chip from the scene classification (the scene-level number can be far off)."""
    assets = feature["assets"]
    if "scl" not in assets:
        return None
    try:
        scl = read_window(assets["scl"]["href"], lon, lat, out_px=60, nearest=True)
    except Exception:  # noqa: BLE001
        return None
    valid = scl > 0
    if valid.mean() < MIN_VALID:
        return 1.0                       # chip mostly outside the scene
    return float(np.isin(scl, SCL_CLOUD)[valid].mean())


def read_scene(feature: dict, lon: float, lat: float) -> dict[str, np.ndarray] | None:
    """Reflectance arrays (true colour, SWIR) + scene classification for one Sentinel-2 scene; None if mostly empty."""
    assets = feature["assets"]
    red = read_window(assets["red"]["href"], lon, lat)
    if np.count_nonzero(red) / red.size < MIN_VALID:
        return None
    out = {"red": reflectance(red, assets["red"])}
    for b in ("green", "blue") + SWIR_BANDS:
        out[b] = reflectance(read_window(assets[b]["href"], lon, lat), assets[b])
    if "scl" in assets:
        out["scl"] = read_window(assets["scl"]["href"], lon, lat, nearest=True)
    return out


# --------------------------------------------------------------------------- indices
def nbr(b8a: np.ndarray, b12: np.ndarray, min_sum: float = 0.05) -> np.ndarray:
    """Normalised burn ratio; NaN where the two bands are too dark for a ratio to mean anything (deep shadow, water,
    negative reflectance after the L2A offset), which would otherwise blow up to +-1000."""
    with np.errstate(divide="ignore", invalid="ignore"):
        total = b8a + b12
        out = np.where((total >= min_sum) & (b8a >= 0) & (b12 >= 0), (b8a - b12) / total, np.nan)
    return np.clip(out, -1.0, 1.0)


def land_mask(scene: dict[str, np.ndarray]) -> np.ndarray:
    ok = np.isfinite(scene["nir08"]) & np.isfinite(scene["swir22"])
    if "scl" in scene:
        ok &= np.isin(scene["scl"], SCL_LAND)
    return ok


def hot_pixels(scene: dict[str, np.ndarray]) -> dict[str, Any]:
    b12, b11 = scene["swir22"], scene["swir16"]
    with np.errstate(invalid="ignore"):
        hot = (b12 >= HOT_B12) & (b12 > HOT_RATIO * b11)
    if "scl" in scene:
        hot &= ~np.isin(scene["scl"], (8, 9, 10, 11))        # not cloud / snow
    n = int(np.nansum(hot))
    return {"count": n, "fraction": float(n / hot.size), "mask": hot}


def severity(dnbr: float) -> str:
    """USGS burn-severity classes for dNBR."""
    if dnbr < 0.1:
        return "unburned / no change"
    if dnbr < 0.27:
        return "low severity"
    if dnbr < 0.44:
        return "moderate-low severity"
    if dnbr < 0.66:
        return "moderate-high severity"
    return "high severity"


def dnbr_summary(before: dict[str, np.ndarray], after: dict[str, np.ndarray]) -> dict[str, Any] | None:
    valid = land_mask(before) & land_mask(after)
    if valid.mean() < 0.25:            # too cloudy / watery for a statement about the chip
        return None
    pre, post = nbr(before["nir08"], before["swir22"]), nbr(after["nir08"], after["swir22"])
    valid &= np.isfinite(pre) & np.isfinite(post)
    d = (pre - post)[valid]
    d = d[np.isfinite(d)]
    if d.size < 200:
        return None
    p95 = float(np.percentile(d, 95))
    return {"nbr_before": float(np.nanmedian(pre[valid])), "nbr_after": float(np.nanmedian(post[valid])),
            "dnbr_mean": float(d.mean()), "dnbr_p95": p95, "burned_fraction": float((d > BURNED_DNBR).mean()),
            "valid_fraction": float(valid.mean()), "severity": severity(p95)}


# --------------------------------------------------------------------------- rendering
def _png_data_uri(rgb: np.ndarray) -> str:
    from rasterio.io import MemoryFile
    with MemoryFile() as mem:
        with mem.open(driver="PNG", width=rgb.shape[2], height=rgb.shape[1], count=3, dtype="uint8") as dst:
            dst.write(rgb)
        data = mem.read()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def _stretch(bands: list[np.ndarray], top: float, gamma: float) -> np.ndarray:
    arr = np.nan_to_num(np.stack(bands), nan=0.0)
    arr = np.clip(arr / top, 0, 1) ** gamma
    return (arr * 255).astype("uint8")


def render_scene(scene: dict[str, np.ndarray]) -> dict[str, str]:
    return {"true_colour": _png_data_uri(_stretch([scene["red"], scene["green"], scene["blue"]], 0.30, 0.8)),
            "swir": _png_data_uri(_stretch([scene["swir22"], scene["swir16"], scene["nir08"]], 0.45, 0.8))}


# --------------------------------------------------------------------------- Landsat thermal
_pc_token: dict[str, Any] = {}


def _planetary_token(timeout: int = 30) -> str:
    """Anonymous SAS token for the Landsat container, cached until shortly before it expires."""
    now = datetime.now(timezone.utc)
    if _pc_token and _pc_token["expiry"] - now > timedelta(minutes=5):
        return _pc_token["token"]
    req = urllib.request.Request(PC_TOKEN_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        d = json.load(resp)
    _pc_token.update(token=d["token"], expiry=_parse_time(d["msft:expiry"]))
    return d["token"]


def landsat_thermal(lon: float, lat: float, centre: datetime, timeout: int = 40) -> dict[str, Any]:
    """Independent thermal check from Landsat 8/9 TIRS surface temperature within +-8 days (may be absent)."""
    out: dict[str, Any] = {"available": False, "window_days": LANDSAT_WINDOW_DAYS}
    try:
        feats = stac_search(LANDSAT, lon, lat, centre - timedelta(days=LANDSAT_WINDOW_DAYS), centre + timedelta(days=LANDSAT_WINDOW_DAYS),
                            timeout=timeout, url=PC_STAC_URL)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"Landsat catalogue unavailable ({type(exc).__name__})"
        return out
    cands = pick_after(feats, centre, LANDSAT_WINDOW_DAYS, MAX_CLOUD, bands=("lwir11",))
    if not cands:
        out["reason"] = f"No Landsat 8/9 scene under {MAX_CLOUD:.0f}% cloud within {LANDSAT_WINDOW_DAYS} days"
        return out
    for c in cands[:2]:
        f = c["feature"]
        asset = f["assets"]["lwir11"]
        try:
            dn = read_window(asset["href"] + "?" + _planetary_token(), lon, lat, out_px=100)
        except Exception as exc:  # noqa: BLE001
            out["reason"] = f"Landsat read failed ({type(exc).__name__})"
            continue
        rb = (asset.get("raster:bands") or [{}])[0]
        scale, offset = float(rb.get("scale", 0.00341802)), float(rb.get("offset", 149.0))
        kelvin = np.where(dn > 0, dn * scale + offset, np.nan)
        if np.isfinite(kelvin).mean() < MIN_VALID:
            continue
        med = float(np.nanmedian(kelvin))
        mx = float(np.nanmax(kelvin))
        out.update(available=True, reason=None, scene={"id": f["id"], "datetime": c["time"].isoformat(), "cloud_cover_percent": c["cloud"],
                                                    "offset_days": c["offset_days"], "platform": f["properties"].get("platform")},
                   st_max_k=round(mx, 1), st_p99_k=round(float(np.nanpercentile(kelvin, 99)), 1), st_median_k=round(med, 1),
                   st_excess_k=round(mx - med, 1), source="Microsoft Planetary Computer (USGS Landsat Collection 2 Level-2)")
        return out
    return out


# --------------------------------------------------------------------------- one source
def analyse_source(lon: float, lat: float, last_seen: datetime, with_landsat: bool = True, with_images: bool = True,
                   first_seen: datetime | None = None) -> dict[str, Any]:
    """Full imagery verification for one location/time. Never raises for "no scene"; raises on service failure."""
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    base = {"available": False, "window_km": HALF_M * 2 / 1000,
            "search": {"collection": S2, "days_either_side": WINDOW_DAYS, "max_cloud_percent": MAX_CLOUD,
                       "centre_time": last_seen.isoformat(), "stac": STAC_URL, "before_window_days": [BEFORE_MIN_DAYS, BEFORE_MAX_DAYS]},
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    feats = stac_search(S2, lon, lat, last_seen - timedelta(days=BEFORE_MAX_DAYS + WINDOW_DAYS + 1), last_seen + timedelta(days=WINDOW_DAYS + 1))
    if first_seen is not None and first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    after_candidates = pick_after(feats, last_seen, not_before=first_seen)
    result = {**base, "reason": f"No Sentinel-2 scene under {MAX_CLOUD:.0f}% cloud within {WINDOW_DAYS} days of the last "
                                f"detection and after its first detection ({len(after_candidates)} candidate(s)).",
              "landsat": {"available": False}}
    # rank the best few by what is above THIS chip, not by the scene average
    for cand in after_candidates[:4]:
        cand["chip_cloud"] = chip_cloud_fraction(cand["feature"], lon, lat)
    ranked = sorted(after_candidates[:4], key=lambda c: (c["chip_cloud"] if c["chip_cloud"] is not None else 0.5, c["cloud"]))
    for cand in ranked:
        if cand["chip_cloud"] is not None and cand["chip_cloud"] > 0.9:
            continue
        after = read_scene(cand["feature"], lon, lat)
        if after is None:
            continue
        f = cand["feature"]
        result.update(available=True, reason=None, scene=_scene_json(cand), composites={"true_colour": "B04/B03/B02", "swir": "B12/B11/B8A"},
                      attribution="Contains modified Copernicus Sentinel data (ESA); Earth Search by Element 84; Landsat courtesy of USGS")
        hp = hot_pixels(after)
        result["hot_pixels"] = {"count": hp["count"], "fraction": round(hp["fraction"], 5),
                                "rule": f"B12 >= {HOT_B12} and B12 > {HOT_RATIO} x B11"}
        images = render_scene(after) if with_images else {}
        result.update({k: v for k, v in images.items()})                  # legacy keys: true_colour, swir
        result["images"] = {"after": images}
        before_c = pick_before(feats, cand)
        result["before_scene"] = None
        result["indices"] = None
        if before_c is not None:
            before = read_scene(before_c["feature"], lon, lat)
            if before is not None:
                result["before_scene"] = {**_scene_json(before_c), "gap_days": before_c["gap_days"], "same_tile": before_c["same_tile"]}
                if with_images:
                    result["images"]["before"] = render_scene(before)
                result["indices"] = dnbr_summary(before, after)
        break
    if with_landsat:
        result["landsat"] = landsat_thermal(lon, lat, last_seen)
    return result


def _scene_json(c: dict) -> dict[str, Any]:
    f = c["feature"]
    return {"id": f["id"], "datetime": c["time"].isoformat(), "cloud_cover_percent": c["cloud"],
            "offset_days": c.get("offset_days"), "platform": f["properties"].get("platform"),
            "chip_cloud_fraction": c.get("chip_cloud"), "mgrs_tile": f["properties"].get("grid:code"),
            "stac_item": next((l["href"] for l in f.get("links", []) if l.get("rel") == "self"), None)}


# --------------------------------------------------------------------------- features + reasons
def imagery_features(result: dict[str, Any] | None) -> dict[str, float | None]:
    """The numbers the model / reasons use (None where there was no usable scene)."""
    r = result or {}
    idx = r.get("indices") or {}
    ls = r.get("landsat") or {}
    return {
        "s2_available": 1.0 if r.get("available") else 0.0,
        "s2_cloud": (r.get("scene") or {}).get("cloud_cover_percent"),
        "s2_dnbr_p95": idx.get("dnbr_p95"), "s2_dnbr_mean": idx.get("dnbr_mean"), "s2_burned_fraction": idx.get("burned_fraction"),
        "s2_hot_pixels": float((r.get("hot_pixels") or {}).get("count")) if r.get("available") else None,
        "landsat_st_excess_k": ls.get("st_excess_k") if ls.get("available") else None,
    }


def imagery_reasons(result: dict[str, Any] | None) -> list[str]:
    """Plain-language lines for the source's reasons (dNBR, hot pixels, Landsat)."""
    if not result:
        return []
    out = []
    sc = result.get("scene") or {}
    if not result.get("available"):
        return [f"no clear Sentinel-2 scene within {WINDOW_DAYS} days ({MAX_CLOUD:.0f}% cloud limit): imagery could not confirm or exclude a fire"]
    day = str(sc.get("datetime", ""))[:10]
    idx, bs = result.get("indices"), result.get("before_scene")
    if idx and bs:
        cc = sc.get("chip_cloud_fraction")
        out.append(f"Sentinel-2 {day} ({sc.get('cloud_cover_percent', 0):.0f}% scene cloud"
                   f"{f', {cc:.0%} over the chip' if cc is not None else ''}) vs {str(bs['datetime'])[:10]}: dNBR p95 "
                   f"{idx['dnbr_p95']:+.2f} ({idx['severity']}), {idx['burned_fraction']:.0%} of the 3 x 3 km chip changed like a burn scar")
    elif bs is None:
        out.append(f"Sentinel-2 {day} found but no clear earlier scene within {BEFORE_MAX_DAYS} days: no dNBR")
    hp = result.get("hot_pixels") or {}
    if hp.get("count"):
        out.append(f"{hp['count']} SWIR hot pixel(s) in the {day} scene (B12 flame / hot-slag signature)")
    ls = result.get("landsat") or {}
    if ls.get("available"):
        out.append(f"Landsat TIRS {str(ls['scene']['datetime'])[:10]}: surface temperature up to {ls['st_max_k']:.0f} K, "
                   f"{ls['st_excess_k']:.0f} K above the chip median")
    return out


# --------------------------------------------------------------------------- cache
class DiskCache:
    """JSON files keyed by (source, last-seen day). Safe for one writer (worker) and many readers (API)."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def path(self, source_id: str, last_seen: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in source_id)
        return self.dir / f"{safe}__{str(last_seen)[:10]}.json"

    def get(self, source_id: str, last_seen: str) -> dict | None:
        p = self.path(source_id, last_seen)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            return None

    def put(self, source_id: str, last_seen: str, result: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(source_id, last_seen)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(result), encoding="utf-8")
        os.replace(tmp, p)

    def prune(self, keep_days: int = 14) -> int:
        n, cutoff = 0, datetime.now(timezone.utc).timestamp() - keep_days * 86400
        for f in self.dir.glob("*.json") if self.dir.exists() else []:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                n += 1
        return n


def default_cache_dir() -> Path:
    return Path(os.getenv("THERMAL_IMAGERY_DIR", "data/thermal/imagery"))


def cached_analysis(source_id: str, lon: float, lat: float, last_seen: str, cache: DiskCache | None = None,
                    first_seen: str | None = None, **kw) -> dict:
    """Disk cache first, then compute and store. In-memory LRU in front for the API."""
    key = f"{source_id}|{str(last_seen)[:10]}"
    with _lock:
        if key in _mem:
            _mem.move_to_end(key)
            return _mem[key]
    cache = cache or DiskCache(default_cache_dir())
    hit = cache.get(source_id, last_seen)
    if hit is None:
        when = _parse_time(str(last_seen))
        hit = analyse_source(lon, lat, when, first_seen=_parse_time(str(first_seen)) if first_seen else None, **kw)
        try:
            cache.put(source_id, last_seen, hit)
        except OSError as exc:      # read-only volume (API container): serve without persisting
            logger.debug("imagery cache not writable: %s", exc)
    with _lock:
        _mem[key] = hit
        while len(_mem) > 64:
            _mem.popitem(last=False)
    return hit
