"""Long baseline per facility and per 1 km cell from the FIRMS archive.

A persistent flare, kiln or coal-seam fire is "normal" for its place; a new fire is not. Seven days of
history cannot tell the two apart, so every thermal source is compared with 365 days of its own history.

Definitions (identical to ``sql/migrations/006_thermal_baseline.sql``; the PostGIS test checks the two agree):

  window       365 days ending ``EXCLUDE_RECENT_DAYS`` before the newest detection date, so the live
               window being judged is not part of its own baseline
  covered day  a window day on which the archive holds any detection (a missing archive day is not "quiet")
  detection    low-confidence pixels dropped; the same physical detection held as SP and NRT is counted once
  active day   a covered day with >= 1 detection assigned to the entity
  entity       the facility whose (radius + 500 m) contains the detection with the largest margin,
               plus the 1 km EPSG:6933 cell of every detection
  daily FRP    per-day sum of FRP, mean/std over ALL covered days (inactive days count as 0 MW)

  z-score      (FRP today - daily mean) / max(daily std, MIN_STD_MW); "today" is the entity's latest day
               in the analysed window, summed over every detection of that entity on that day.

Commands:
    python -m src.thermal.baseline download --start 2025-10-01 --family SNPP --family NOAA20 --out ../data/archive
    python -m src.thermal.baseline report  --archive ../data/archive/snpp --archive ../data/archive/noaa20
    python -m src.thermal.baseline load    --archive ... [--dsn postgresql://...]   # parquet -> thermal_detections
    python -m src.thermal.baseline compute --archive ... --facilities ../data/facilities/facilities.geojson
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .sites import SiteIndex

logger = logging.getLogger(__name__)

WINDOW_DAYS = 365
EXCLUDE_RECENT_DAYS = 14
MIN_STD_MW = 1.0
CELL_M = 1000.0
COLUMNS = ["scope", "key", "active_days", "covered_days", "active_fraction", "monthly_profile", "frp_median",
           "frp_p95", "night_share", "n_detections", "daily_frp_mean", "daily_frp_std", "window_start", "window_end"]


# --------------------------------------------------------------------------- geometry
def cell_xy(lat, lon) -> tuple[np.ndarray, np.ndarray]:
    """1 km cell indices in EPSG:6933 (equal-area, metres); same grid as the SQL function."""
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 6933, always_xy=True)
    x, y = tr.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return np.floor(np.asarray(x) / CELL_M).astype(int), np.floor(np.asarray(y) / CELL_M).astype(int)


def cell_keys(lat, lon) -> np.ndarray:
    x, y = cell_xy(lat, lon)
    return np.array([f"{a}_{b}" for a, b in zip(x, y)], dtype=object)


def assign_geo(det: pd.DataFrame, sites: SiteIndex | None) -> pd.DataFrame:
    """Add ``facility_id`` (None when outside every facility radius) and ``cell_key`` to detections."""
    out = det.copy()
    out["cell_key"] = cell_keys(out.latitude, out.longitude) if len(out) else []
    if sites is None or len(sites) == 0 or out.empty:
        out["facility_id"] = None
        return out
    # vectorised twin of SiteIndex.match (same rule: largest radius+buffer-distance margin among the k nearest)
    from .sites import EARTH_R, VIIRS_BUFFER_M
    refs = sites.sites["ref"].to_numpy(dtype=object)
    radii = sites.sites["radius_m"].to_numpy(dtype=float)
    k = min(8, len(sites))
    lat, lon = out.latitude.to_numpy(dtype=float), out.longitude.to_numpy(dtype=float)
    chosen = np.empty(len(out), dtype=object)
    for i in range(0, len(out), 200_000):
        sl = slice(i, i + 200_000)
        dist, idx = sites._tree.query(np.radians(np.column_stack([lat[sl], lon[sl]])), k=k)
        margin = radii[idx] + VIIRS_BUFFER_M - dist * EARTH_R
        best = margin.argmax(axis=1)
        rows = np.arange(len(best))
        ok = margin[rows, best] >= 0
        pick = refs[idx[rows, best]]
        chosen[sl] = np.where(ok, pick, None)
    out["facility_id"] = chosen
    return out


def detection_id(product: str, lat: float, lon: float, ts: pd.Timestamp) -> str:
    """Same id as ``firms.parse_csv`` so archive and live rows of one product never duplicate."""
    return hashlib.sha1(f"{product}|{lat:.5f}|{lon:.5f}|{ts.isoformat()}".encode()).hexdigest()[:20]


# --------------------------------------------------------------------------- baseline
def physical(det: pd.DataFrame) -> pd.DataFrame:
    """One row per physical detection: low confidence dropped, SP preferred over NRT."""
    d = det[det["confidence"] != "low"].copy()
    parts = d["instrument"].str.split("_", expand=True)
    d["_sat"] = parts[1]
    d["_sp"] = (parts[2] == "SP").astype(int)
    d["_la"] = d.latitude.round(4)
    d["_lo"] = d.longitude.round(4)
    d = d.sort_values(["_sat", "acq_datetime", "_la", "_lo", "_sp"], ascending=[True, True, True, True, False])
    return d.drop_duplicates(["_sat", "acq_datetime", "_la", "_lo"]).drop(columns=["_sat", "_sp", "_la", "_lo"])


def window_bounds(det: pd.DataFrame, anchor: date | None = None) -> tuple[date, date]:
    anchor = anchor or det["acq_datetime"].max().date()
    end = anchor - timedelta(days=EXCLUDE_RECENT_DAYS)
    return end - timedelta(days=WINDOW_DAYS - 1), end


def compute_baseline(det: pd.DataFrame, anchor: date | None = None) -> pd.DataFrame:
    """``det`` needs latitude, longitude, acq_datetime, frp, daynight, confidence, instrument, facility_id, cell_key."""
    if det.empty:
        return pd.DataFrame(columns=COLUMNS)
    start, end = window_bounds(det, anchor)
    d = det.assign(day=det["acq_datetime"].dt.date)
    in_window = d[(d.day >= start) & (d.day <= end)]
    covered = sorted(in_window.day.unique())
    n_cov = len(covered)
    if n_cov == 0:
        return pd.DataFrame(columns=COLUMNS)
    cov_month = pd.Series([x.month for x in covered]).value_counts().to_dict()

    p = physical(in_window)
    p["night"] = (p.daynight == "N").astype(int)
    parts = []
    for scope, keycol in (("facility", "facility_id"), ("cell", "cell_key")):
        q = p[p[keycol].notna()]
        if q.empty:
            continue
        g = q.groupby(keycol)
        stats = g.agg(n_detections=("frp", "size"), frp_median=("frp", "median"), night_share=("night", "mean"))
        stats["frp_p95"] = g.frp.quantile(0.95)          # linear interpolation = SQL percentile_cont
        per_day = q.groupby([keycol, "day"]).frp.sum().reset_index()
        per_day["month"] = pd.to_datetime(per_day["day"]).dt.month
        per_day["frp2"] = per_day.frp ** 2
        dstats = per_day.groupby(keycol).agg(active_days=("day", "size"), s1=("frp", "sum"), s2=("frp2", "sum"))
        months = per_day.groupby([keycol, "month"]).size().unstack(fill_value=0)
        denom = np.array([cov_month.get(m, 0) for m in range(1, 13)], dtype=float)
        counts = np.column_stack([months[m].to_numpy() if m in months.columns else np.zeros(len(months), dtype=int)
                                  for m in range(1, 13)])
        prof = np.where(denom > 0, np.round(counts / np.where(denom > 0, denom, 1), 4), np.nan)
        profile = {key: [None if np.isnan(v) else float(v) for v in row] for key, row in zip(months.index, prof)}
        out = stats.join(dstats)
        out["scope"], out["key"] = scope, out.index
        out["covered_days"] = n_cov
        out["active_fraction"] = out.active_days / n_cov
        out["monthly_profile"] = [profile[k] for k in out.index]
        out["daily_frp_mean"] = out.s1 / n_cov
        out["daily_frp_std"] = np.sqrt(np.maximum(out.s2 / n_cov - out.daily_frp_mean ** 2, 0.0))
        out["window_start"], out["window_end"] = start, end
        parts.append(out[COLUMNS].reset_index(drop=True))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)


@dataclass
class BaselineIndex:
    frame: pd.DataFrame
    covered_days: int
    window_start: date | None
    window_end: date | None

    def __post_init__(self):
        self._rows = {(r.scope, r.key): r for r in self.frame.itertuples(index=False)} if len(self.frame) else {}

    def __len__(self) -> int:
        return len(self.frame)

    def get(self, scope: str, key: str | None):
        return self._rows.get((scope, key))

    @classmethod
    def from_detections(cls, det: pd.DataFrame, sites: SiteIndex | None, anchor: date | None = None) -> "BaselineIndex":
        geo = assign_geo(det, sites)
        frame = compute_baseline(geo, anchor)
        if frame.empty:
            return cls(frame, 0, None, None)
        return cls(frame, int(frame.covered_days.iloc[0]), frame.window_start.iloc[0], frame.window_end.iloc[0])

    @classmethod
    def from_postgis(cls, dsn: str) -> "BaselineIndex":
        import psycopg2
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.thermal_baseline')")
            if cur.fetchone()[0] is None:
                return cls(pd.DataFrame(columns=COLUMNS), 0, None, None)
            cur.execute(f"SELECT {', '.join(COLUMNS)} FROM thermal_baseline")
            frame = pd.DataFrame(cur.fetchall(), columns=COLUMNS)
        if frame.empty:
            return cls(frame, 0, None, None)
        return cls(frame, int(frame.covered_days.iloc[0]), frame.window_start.iloc[0], frame.window_end.iloc[0])

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "thermal_baseline.parquet"
        tmp = path.with_suffix(".tmp")
        self.frame.assign(monthly_profile=self.frame.monthly_profile.map(json.dumps)).to_parquet(tmp, index=False)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, directory: Path) -> "BaselineIndex | None":
        path = directory / "thermal_baseline.parquet"
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        frame["monthly_profile"] = frame["monthly_profile"].map(json.loads)
        if frame.empty:
            return cls(frame, 0, None, None)
        return cls(frame, int(frame.covered_days.iloc[0]), frame.window_start.iloc[0], frame.window_end.iloc[0])


# --------------------------------------------------------------------------- attach to sources
BASELINE_COLUMNS = ["baseline_scope", "baseline_key", "baseline_known", "baseline_active_fraction",
                    "baseline_covered_days", "baseline_frp_median", "baseline_frp_p95", "baseline_night_share",
                    "baseline_monthly", "baseline_window_end", "frp_today", "frp_today_date", "frp_z"]


def attach(src: pd.DataFrame, det: pd.DataFrame, index: BaselineIndex | None, sites: SiteIndex | None) -> pd.DataFrame:
    """Add baseline_* columns and ``frp_z`` to the source table.

    ``det`` carries the analysed window's detections (columns latitude, longitude, acq_datetime, frp,
    cluster). Entity = the source's facility when it sits inside one, else its 1 km cell.
    """
    out = src.copy()
    if index is None or src.empty:
        for c in BASELINE_COLUMNS:
            out[c] = None
        out["baseline_known"] = False
        return out

    d = assign_geo(det, sites)
    d["day"] = d["acq_datetime"].dt.date
    fac_today = d[d.facility_id.notna()].groupby(["facility_id", "day"]).frp.sum()
    cell_today = d.groupby(["cell_key", "day"]).frp.sum()

    def latest(series: pd.Series, key: str):
        if key not in series.index.get_level_values(0):
            return None, None
        s = series.loc[key]
        day = max(s.index)
        return float(s.loc[day]), day

    rows = []
    # a cluster can span cells and its centroid can land in one with no detection: use the cell that holds
    # most of the cluster's own detections (centroid cell only when the cluster has none in ``det``)
    modal = d.groupby("cluster").cell_key.agg(lambda s: s.value_counts().index[0]).to_dict() if "cluster" in d else {}
    src_cells = [modal.get(c, fallback) for c, fallback in zip(out["cluster"], cell_keys(out.lat, out.lon))]         if "cluster" in out.columns else cell_keys(out.lat, out.lon)
    for r, cell in zip(out.itertuples(), src_cells):
        in_fac = bool(getattr(r, "site_within", False)) and getattr(r, "site_ref", None)
        scope, key = ("facility", r.site_ref) if in_fac else ("cell", cell)
        today, today_day = latest(fac_today if scope == "facility" else cell_today, key)
        if today is None:   # facility key not present at detection level (centroid/cluster mismatch): use own cell
            scope, key = "cell", cell
            today, today_day = latest(cell_today, key)
        b = index.get(scope, key)
        mean = float(b.daily_frp_mean) if b is not None else 0.0
        std = float(b.daily_frp_std) if b is not None else 0.0
        rows.append({
            "baseline_scope": scope, "baseline_key": key, "baseline_known": b is not None,
            "baseline_active_fraction": float(b.active_fraction) if b is not None else 0.0,
            "baseline_covered_days": index.covered_days,
            "baseline_frp_median": float(b.frp_median) if b is not None else None,
            "baseline_frp_p95": float(b.frp_p95) if b is not None else None,
            "baseline_night_share": float(b.night_share) if b is not None else None,
            "baseline_monthly": list(b.monthly_profile) if b is not None else None,
            "baseline_window_end": index.window_end,
            "frp_today": today, "frp_today_date": today_day,
            "frp_z": None if today is None else round((today - mean) / max(std, MIN_STD_MW), 2),
        })
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


# --------------------------------------------------------------------------- archive IO
def load_archives(dirs: list[Path]) -> pd.DataFrame:
    from .archive import load_archive
    frames = [f for f in (load_archive(Path(d)) for d in dirs) if len(f)]
    if not frames:
        return pd.DataFrame()
    det = pd.concat(frames, ignore_index=True)
    det["confidence"] = det["confidence"].fillna("nominal")
    return det


def coverage_report(det: pd.DataFrame) -> dict:
    """Days with no detection at all in India are archive gaps, not quiet days."""
    if det.empty:
        return {"days": 0}
    days = pd.to_datetime(det["acq_datetime"].dt.date)
    full = pd.date_range(days.min(), days.max(), freq="D")
    present = set(days.unique())
    missing = [d.date().isoformat() for d in full if d not in present]
    by_sat = det.assign(sat=det.instrument.str.split("_").str[1], prod=det.instrument.str.split("_").str[2]) \
        .groupby(["sat", "prod"]).acq_datetime.agg(["min", "max", "size"])
    return {"first": str(full[0].date()), "last": str(full[-1].date()), "days": len(full), "missing_days": missing,
            "by_satellite_product": {f"{s}_{p}": {"first": str(r["min"].date()), "last": str(r["max"].date()),
                                                   "detections": int(r["size"])} for (s, p), r in by_sat.iterrows()}}


def load_to_postgis(dsn: str, det: pd.DataFrame, batch: int = 250_000) -> dict:
    """Bulk-load archive detections into ``thermal_detections`` (existing ids untouched), then assign
    facility/cell and refresh the baseline view."""
    import psycopg2

    d = det.copy()
    d["detection_id"] = [detection_id(i, la, lo, t) for i, la, lo, t in
                         zip(d.instrument, d.latitude, d.longitude, d.acq_datetime)]
    d = d.drop_duplicates("detection_id")
    conn = psycopg2.connect(dsn, application_name="thermotrace-baseline")
    inserted = 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""CREATE TEMP TABLE _arch (detection_id text, instrument text, acq_datetime timestamptz,
                           lon double precision, lat double precision, frp double precision, ti4 double precision,
                           ti5 double precision, confidence text, daynight text) ON COMMIT DROP""")
            for i in range(0, len(d), batch):
                chunk = d.iloc[i:i + batch]
                buf = io.StringIO()
                chunk.assign(acq_datetime=chunk.acq_datetime.dt.strftime("%Y-%m-%d %H:%M:%S+00"))[
                    ["detection_id", "instrument", "acq_datetime", "longitude", "latitude", "frp", "bright_ti4",
                     "bright_ti5", "confidence", "daynight"]].to_csv(buf, index=False, header=False)
                buf.seek(0)
                cur.copy_expert("COPY _arch FROM STDIN WITH (FORMAT csv, NULL '')", buf)
            cur.execute("""INSERT INTO thermal_detections (detection_id, instrument, acq_datetime, geometry, frp,
                               bright_ti4, bright_ti5, confidence, daynight)
                           SELECT detection_id, instrument, acq_datetime, ST_SetSRID(ST_MakePoint(lon, lat), 4326),
                                  frp, ti4, ti5, confidence, daynight FROM _arch
                           ON CONFLICT (detection_id) DO NOTHING""")
            inserted = cur.rowcount
        with conn, conn.cursor() as cur:
            cur.execute("SELECT assign_thermal_geo()")
            assigned = cur.fetchone()[0]
        with conn, conn.cursor() as cur:
            cur.execute("REFRESH MATERIALIZED VIEW thermal_baseline")
            cur.execute("SELECT count(*), count(*) FILTER (WHERE scope='facility') FROM thermal_baseline")
            total, fac = cur.fetchone()
    finally:
        conn.close()
    return {"rows_offered": int(len(d)), "inserted": int(inserted), "geo_assigned": int(assigned),
            "baseline_rows": int(total), "baseline_facility_rows": int(fac)}


def refresh_postgis(dsn: str) -> dict:
    """Incremental step for the live worker: assign geo to newly stored detections, refresh the view."""
    import psycopg2
    conn = psycopg2.connect(dsn, application_name="thermotrace-baseline")
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT assign_thermal_geo()")
            assigned = cur.fetchone()[0]
            cur.execute("REFRESH MATERIALIZED VIEW thermal_baseline")
    finally:
        conn.close()
    return {"geo_assigned": int(assigned)}


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(prog="thermal-baseline")
    ap.add_argument("command", choices=["download", "report", "load", "compute"])
    ap.add_argument("--archive", action="append", default=[], help="folder of archive parquet files (repeatable)")
    ap.add_argument("--start", help="download: first day (default: 395 days before the newest available day)")
    ap.add_argument("--end", help="download: last day (default: newest available)")
    ap.add_argument("--family", action="append", choices=["SNPP", "NOAA20", "NOAA21"],
                    help="download: satellite family (repeatable; default SNPP and NOAA20)")
    ap.add_argument("--facilities", default="data/facilities/facilities.geojson")
    ap.add_argument("--out", default="data/baseline")
    ap.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    args = ap.parse_args(argv)
    if args.command == "download":
        from dotenv import load_dotenv
        from .archive import availability, download_range, key_from_env
        load_dotenv(".env")
        key = key_from_env()
        avail = availability(key)
        out = Path(args.out if args.out != "data/baseline" else "data/archive")
        for family in args.family or ["SNPP", "NOAA20"]:
            newest = max(v[1] for k, v in avail.items() if k.startswith(f"VIIRS_{family}_"))
            end = date.fromisoformat(args.end) if args.end else newest
            start = date.fromisoformat(args.start) if args.start else end - timedelta(days=WINDOW_DAYS + 30)
            n = download_range(out / family.lower(), start, min(end, newest), family)
            print(f"{family}: {n} new chunk(s) {start} .. {min(end, newest)} -> {out / family.lower()}")
        return 0
    if not args.archive:
        raise SystemExit("--archive <folder> is required (repeatable)")
    det = load_archives([Path(a) for a in args.archive])
    if args.command == "report":
        print(json.dumps(coverage_report(det), indent=2))
        return 0
    if args.command == "load":
        if not args.dsn:
            raise SystemExit("--dsn or DATABASE_URL required")
        dsn = args.dsn.replace("postgresql+psycopg2://", "postgresql://")
        print(json.dumps(load_to_postgis(dsn, det), indent=2))
        return 0
    sites = SiteIndex.from_geojson(args.facilities) if Path(args.facilities).exists() else None
    index = BaselineIndex.from_detections(det, sites)
    path = index.save(Path(args.out))
    print(json.dumps({"entities": len(index), "covered_days": index.covered_days,
                      "window": [str(index.window_start), str(index.window_end)], "file": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
