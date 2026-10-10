"""FIRMS archive access (needs a free MAP_KEY: env FIRMS_MAP_KEY). Never log or commit the key.

* ``fetch_area``  one area-API call (<= 5 day range) -> normalised DataFrame
* ``download_range`` resumable chunked download of India-wide detections to parquet files
* ``fetch_around`` small-window query used to test a documented incident against satellite data

Archive products: *_SP (standard processing, ~2-3 months behind) and *_NRT (near real time).
Coverage is read from /api/data_availability so the right product is chosen per date.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from .firms import BASE, INDIA_BBOX, UA

logger = logging.getLogger(__name__)
MAX_DAYS = 5
KEEP = ["latitude", "longitude", "acq_datetime", "frp", "bright_ti4", "bright_ti5", "daynight", "confidence",
        "instrument", "scan", "track"]
_CONF = {"l": "low", "n": "nominal", "h": "high", "low": "low", "nominal": "nominal", "high": "high"}


class ArchiveError(RuntimeError):
    pass


def key_from_env() -> str:
    key = os.getenv("FIRMS_MAP_KEY", "").strip()
    if not key:
        raise ArchiveError("FIRMS_MAP_KEY is not set")
    return key


def availability(key: str, session: requests.Session | None = None) -> dict[str, tuple[date, date]]:
    r = (session or requests).get(f"{BASE}/api/data_availability/csv/{key}/ALL", headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(pd.io.common.StringIO(r.text))
    return {row.data_id: (date.fromisoformat(row.min_date), date.fromisoformat(row.max_date)) for row in df.itertuples()}


def pick_product(avail: dict, family: str, day: date) -> str:
    """family: 'SNPP' | 'NOAA20'. Prefer the standard-processing archive, fall back to NRT."""
    for suffix in ("SP", "NRT"):
        name = f"VIIRS_{family}_{suffix}"
        if name in avail and avail[name][0] <= day <= avail[name][1]:
            return name
    raise ArchiveError(f"no VIIRS_{family} product covers {day}")


def _normalise(text: str, product: str) -> pd.DataFrame:
    if not text.strip() or "latitude" not in text.splitlines()[0]:
        raise ArchiveError(f"unexpected response for {product}: {text[:100]!r}")
    df = pd.read_csv(pd.io.common.StringIO(text), dtype={"acq_time": str, "confidence": str})
    if df.empty:
        return pd.DataFrame(columns=KEEP)
    t = df["acq_time"].str.zfill(4)
    df["acq_datetime"] = pd.to_datetime(df["acq_date"] + " " + t.str[:2] + ":" + t.str[2:], utc=True)
    df["confidence"] = df["confidence"].str.lower().map(_CONF).fillna("nominal")
    df["frp"] = pd.to_numeric(df["frp"], errors="coerce").fillna(0.0).astype("float32")
    df["daynight"] = df["daynight"].str.upper().str[0]
    df["instrument"] = product
    for c in ("bright_ti4", "bright_ti5", "scan", "track"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df[KEEP]


def fetch_area(key: str, product: str, bbox: tuple[float, float, float, float], start: date, days: int = MAX_DAYS,
               session: requests.Session | None = None, retries: int = 4) -> pd.DataFrame:
    if not 1 <= days <= MAX_DAYS:
        raise ValueError("days must be 1..5")
    w, s, e, n = bbox
    url = f"{BASE}/api/area/csv/{key}/{product}/{w},{s},{e},{n}/{days}/{start.isoformat()}"
    http = session or requests.Session()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = http.get(url, headers={"User-Agent": UA}, timeout=180)
            if r.status_code == 429 or "exceeded" in r.text[:200].lower():
                time.sleep(60 * (attempt + 1))
                continue
            r.raise_for_status()
            return _normalise(r.text, product)
        except (requests.RequestException, ArchiveError) as exc:
            last = exc
            time.sleep(5 * (2 ** attempt))
    raise ArchiveError(f"{product} {start}: {type(last).__name__}")  # message never includes the URL / key


def fetch_around(key: str, lat: float, lon: float, start: date, end: date, radius_deg: float = 0.5,
                 families: tuple[str, ...] = ("SNPP", "NOAA20"), avail: dict | None = None) -> pd.DataFrame:
    """All VIIRS detections inside a box around a point between ``start`` and ``end`` (inclusive)."""
    avail = avail or availability(key)
    box = (lon - radius_deg, lat - radius_deg, lon + radius_deg, lat + radius_deg)
    frames = []
    for fam in families:
        day = start
        while day <= end:
            try:
                product = pick_product(avail, fam, day)
            except ArchiveError:
                day += timedelta(days=1)
                continue
            span = min(MAX_DAYS, (end - day).days + 1)
            frames.append(fetch_area(key, product, box, day, span))
            day += timedelta(days=span)
    out = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else pd.DataFrame(columns=KEEP)
    return out.drop_duplicates(["latitude", "longitude", "acq_datetime", "instrument"]).reset_index(drop=True)


def coverage_days(avail: dict, families: tuple[str, ...], start: date, end: date) -> dict[str, bool]:
    """Which families have archive coverage for the whole [start, end] window (honest 'inconclusive' basis)."""
    out = {}
    for fam in families:
        try:
            pick_product(avail, fam, start)
            pick_product(avail, fam, end)
            out[fam] = True
        except ArchiveError:
            out[fam] = False
    return out


def download_range(out_dir: Path, start: date, end: date, family: str = "SNPP", bbox=INDIA_BBOX,
                   pause: float = 0.5) -> int:
    """Resumable India-wide download in 5-day chunks to ``out_dir/<family>_<YYYYMMDD>.parquet``."""
    key = key_from_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    avail = availability(key)
    session = requests.Session()
    done = 0
    day = start
    while day <= end:
        span = min(MAX_DAYS, (end - day).days + 1)
        target = out_dir / f"{family}_{day:%Y%m%d}_{span}d.parquet"
        if not target.exists():
            product = pick_product(avail, family, day)
            df = fetch_area(key, product, bbox, day, span, session)
            df.to_parquet(target, index=False)
            logger.info("%s %s +%dd -> %d rows", product, day, span, len(df))
            done += 1
            time.sleep(pause)
        day += timedelta(days=span)
    return done


def load_archive(directory: Path, family: str | None = None) -> pd.DataFrame:
    files = sorted(directory.glob(f"{family or '*'}_*.parquet"))
    if not files:
        return pd.DataFrame(columns=KEEP)
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["acq_datetime"] = pd.to_datetime(df["acq_datetime"], utc=True)
    return df.drop_duplicates(["latitude", "longitude", "acq_datetime", "instrument"]).reset_index(drop=True)
