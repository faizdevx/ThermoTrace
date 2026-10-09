"""NASA FIRMS active-fire ingestion (VIIRS 375 m).

Two access paths, both official and documented by NASA FIRMS:

* Public regional CSV files (no key): ``.../data/active_fire/<product>/csv/<file>_South_Asia_{24h,48h,7d}.csv``
  Refreshed by FIRMS roughly every few hours (near real time, NRT).
* Area API (needs a free MAP_KEY, env ``FIRMS_MAP_KEY``): ``/api/area/csv/<key>/<source>/<bbox>/<days>``.

NOTE: the host is ``firms.modaps.eosdis.nasa.gov`` (``modaps`` - an easy typo is ``modap``).
FIRMS data are open; credit NASA FIRMS / LANCE (https://www.earthdata.nasa.gov/data/tools/firms).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from email.utils import parsedate_to_datetime
from dataclasses import dataclass

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://firms.modaps.eosdis.nasa.gov"
UA = "ThermoTrace-thermal/1.0 (+https://github.com/faizdevx/ThermoTrace)"

# instrument label -> (public directory, file prefix, API source name)
PRODUCTS: dict[str, tuple[str, str, str]] = {
    "VIIRS_SNPP_NRT": ("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2", "VIIRS_SNPP_NRT"),
    "VIIRS_NOAA20_NRT": ("noaa-20-viirs-c2", "J1_VIIRS_C2", "VIIRS_NOAA20_NRT"),
    "VIIRS_NOAA21_NRT": ("noaa-21-viirs-c2", "J2_VIIRS_C2", "VIIRS_NOAA21_NRT"),
}
# India bounding box used to request/clip data (neighbouring countries removed later by polygon).
INDIA_BBOX = (68.0, 6.0, 97.6, 37.2)  # west, south, east, north

_CONF = {"l": "low", "n": "nominal", "h": "high", "low": "low", "nominal": "nominal", "high": "high"}


class FirmsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FirmsConfig:
    products: tuple[str, ...] = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
    span: str = "7d"                    # 24h | 48h | 7d  (public files)
    region: str = "South_Asia"
    timeout: int = 90
    map_key: str | None = None          # when set, the area API is used instead

    @staticmethod
    def from_env() -> "FirmsConfig":
        return FirmsConfig(map_key=os.getenv("FIRMS_MAP_KEY") or None,
                           span=os.getenv("FIRMS_SPAN", "7d"))


def public_url(product: str, cfg: FirmsConfig) -> str:
    folder, prefix, _ = PRODUCTS[product]
    return f"{BASE}/data/active_fire/{folder}/csv/{prefix}_{cfg.region}_{cfg.span}.csv"


def api_url(product: str, cfg: FirmsConfig, days: int = 5) -> str:
    west, south, east, north = INDIA_BBOX
    return f"{BASE}/api/area/csv/{cfg.map_key}/{PRODUCTS[product][2]}/{west},{south},{east},{north}/{days}"


def parse_csv(text: str, product: str) -> pd.DataFrame:
    """Normalise one FIRMS VIIRS CSV into the schema used downstream."""
    if not text.strip() or "latitude" not in text.splitlines()[0]:
        raise FirmsUnavailable(f"unexpected FIRMS payload for {product}: {text[:120]!r}")
    df = pd.read_csv(io.StringIO(text), dtype={"acq_time": str, "confidence": str, "satellite": str})
    if df.empty:
        return df.assign(instrument=product)
    df["acq_time"] = df["acq_time"].str.zfill(4)
    df["acq_datetime"] = pd.to_datetime(
        df["acq_date"] + " " + df["acq_time"].str[:2] + ":" + df["acq_time"].str[2:], utc=True)
    df["confidence"] = df["confidence"].str.lower().map(_CONF).fillna("nominal")
    df["frp"] = pd.to_numeric(df["frp"], errors="coerce").fillna(0.0)
    df["instrument"] = product
    df["daynight"] = df["daynight"].str.upper().str[0].fillna("D")
    keep = ["latitude", "longitude", "bright_ti4", "bright_ti5", "frp", "scan", "track", "acq_datetime",
            "satellite", "confidence", "daynight", "instrument", "version"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["detection_id"] = [
        hashlib.sha1(f"{product}|{lat:.5f}|{lon:.5f}|{ts.isoformat()}".encode()).hexdigest()[:20]
        for lat, lon, ts in zip(df.latitude, df.longitude, df.acq_datetime)
    ]
    return df.drop_duplicates("detection_id").reset_index(drop=True)


def fetch(cfg: FirmsConfig | None = None, session: requests.Session | None = None) -> pd.DataFrame:
    """Download all configured instruments and return one de-duplicated detection table."""
    cfg = cfg or FirmsConfig.from_env()
    http = session or requests.Session()
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    published: list = []
    for product in cfg.products:
        url = api_url(product, cfg) if cfg.map_key else public_url(product, cfg)
        shown = url.replace(cfg.map_key, "***") if cfg.map_key else url
        try:
            r = http.get(url, headers={"User-Agent": UA}, timeout=cfg.timeout)
            r.raise_for_status()
            frames.append(parse_csv(r.text, product))
            lm = r.headers.get("Last-Modified")
            if lm:
                published.append(parsedate_to_datetime(lm))
            logger.info("FIRMS %s: %d rows", product, len(frames[-1]))
        except (requests.RequestException, FirmsUnavailable) as exc:
            errors.append(f"{product}: {exc}")
            logger.warning("FIRMS %s failed: %s (%s)", product, exc, shown)
    if not frames:
        raise FirmsUnavailable("; ".join(errors) or "no FIRMS products configured")
    df = pd.concat(frames, ignore_index=True)
    w, s, e, n = INDIA_BBOX
    df = df[(df.longitude >= w) & (df.longitude <= e) & (df.latitude >= s) & (df.latitude <= n)]
    df.attrs["errors"] = errors
    df.attrs["published_at"] = max(published) if published else None
    return df.reset_index(drop=True)
