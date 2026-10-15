"""Build the labelled evaluation set (weak rule labels + curated incidents) from the FIRMS archive.

Unit = (1 km EPSG:6933 cell, 7-day window) with >= 1 non-low-confidence VIIRS detection, the same notion of
"a source in a window" the live pipeline scores. Everything is deterministic (seeded) and re-runnable.

Outputs (data/labels/):
  labels_v1.parquet / labels_v1.csv.gz   the labels
  manifest_v1.json                       counts, rule text, inputs, split, sha256 of every input and output
Optionally the ``labels`` table in PostGIS (migration 007).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from src.thermal import baseline as bl
from src.thermal import landcover
from src.thermal.sites import EARTH_R, SiteIndex

from . import rules as R

logger = logging.getLogger(__name__)

LABEL_COLUMNS = ["label_id", "unit_type", "cell_key", "source_id", "lat", "lon", "state", "window_start", "window_end",
                 "label", "label_source", "evidence_url", "labelled_by", "rule_version", "split", "region_group",
                 "n_detections", "n_days", "frp_max", "night_fraction", "detail"]


# --------------------------------------------------------------------------- helpers
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _window_index(days: pd.Series) -> pd.Series:
    return ((days - pd.Timestamp(R.FIRST_WINDOW)).dt.days // R.WINDOW_DAYS).astype(int)


def window_start(idx: int) -> date:
    return R.FIRST_WINDOW + timedelta(days=int(idx) * R.WINDOW_DAYS)


def choose_held_out(units: pd.DataFrame, min_support: int = 150, max_per_facility: int | None = None) -> list[str]:
    """Pick the held-out regions greedily so every class has train AND test support (deterministic).

    train support of a class = its labelled units in held-in states before TEST_FROM; test support = its labelled
    units in held-out states from TEST_FROM. Objective: maximise the smallest of those numbers over classes, adding
    one state at a time (ties by state name) until it stops improving or every class reaches ``min_support``.
    """
    flags = {"industrial_persistent": "is_industrial_persistent", "agricultural_burning": "is_agricultural_burning",
             "vegetation_fire": "is_vegetation_fire"}
    late = pd.to_datetime(units.window_start) >= pd.Timestamp(R.TEST_FROM)
    pre, post = {}, {}
    for cls, col in flags.items():
        sel = units[units[col]]
        l = late.loc[sel.index]
        if cls == "industrial_persistent" and max_per_facility and "authoritative_facility_id" in sel:
            # support that survives the per-facility cap applied later (a plant with 300 windows counts as 30)
            def capped(frame):
                return frame.groupby(["state", "authoritative_facility_id"]).size().clip(upper=max_per_facility).groupby(level=0).sum()
            pre[cls], post[cls] = capped(sel[~l]), capped(sel[l])
        else:
            pre[cls] = sel[~l].groupby("state").size()
            post[cls] = sel[l].groupby("state").size()
    states = sorted(units.state.dropna().unique())

    def score(held: set[str]) -> tuple[int, int]:
        vals = []
        for cls in flags:
            train = sum(v for s_, v in pre[cls].items() if s_ not in held)
            test = sum(v for s_, v in post[cls].items() if s_ in held)
            vals.append(min(train, test))
        return min(vals), sum(vals)

    held: set[str] = set()
    best = (-1, -1)
    while True:
        cand = [(score(held | {s_}), s_) for s_ in states if s_ not in held]
        if not cand:
            break
        top = max(cand, key=lambda t: (t[0], -states.index(t[1])))
        if top[0] <= best and best[0] >= 0:
            break
        held.add(top[1])
        best = top[0]
        if best[0] >= min_support:
            break
    return sorted(held)


# --------------------------------------------------------------------------- units
def build_units(det: pd.DataFrame) -> pd.DataFrame:
    """Aggregate physical detections into (cell, window) units."""
    d = bl.physical(det)
    d = d[d.acq_datetime >= pd.Timestamp(R.FIRST_WINDOW, tz="UTC")].copy()
    d["day"] = d.acq_datetime.dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    d["window"] = _window_index(d["day"])
    d["night"] = (d.daynight == "N").astype(int)
    g = d.groupby(["cell_key", "window"])
    units = g.agg(lat=("latitude", "mean"), lon=("longitude", "mean"), n_detections=("frp", "size"),
                  n_days=("day", "nunique"), frp_max=("frp", "max"), night_fraction=("night", "mean")).reset_index()
    units["window_start"] = [window_start(w) for w in units.window]
    units["window_end"] = [window_start(w) + timedelta(days=R.WINDOW_DAYS - 1) for w in units.window]
    units["window_month"] = [(s + timedelta(days=3)).month for s in units.window_start]    # month of the window's middle day
    return units


def attach_facility_distances(units: pd.DataFrame, facilities_geojson: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Nearest authoritative facility (<= 1 km rule) and nearest facility of any source (3 km exclusion rule)."""
    data = json.loads(Path(facilities_geojson).read_text(encoding="utf-8"))
    rows = []
    for f in data["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        srcs = str(p.get("sources", "")).split(",")
        rows.append({"facility_id": p["facility_id"], "name": p.get("name"), "kind": p.get("kind"), "lat": lat, "lon": lon,
                     "authoritative": any(s in R.AUTHORITATIVE_SOURCES for s in srcs), "sources": p.get("sources")})
    fac = pd.DataFrame(rows)
    pts = np.radians(units[["lat", "lon"]].to_numpy(dtype=float))

    def nearest(frame: pd.DataFrame):
        tree = BallTree(np.radians(frame[["lat", "lon"]].to_numpy(dtype=float)), metric="haversine")
        dist, idx = tree.query(pts, k=1)
        return dist[:, 0] * EARTH_R, idx[:, 0]

    d_any, _ = nearest(fac)
    auth = fac[fac.authoritative].reset_index(drop=True)
    d_auth, i_auth = nearest(auth)
    out = units.copy()
    out["distance_to_any_facility_m"] = d_any
    out["distance_to_authoritative_facility_m"] = d_auth
    out["authoritative_facility_id"] = auth.facility_id.to_numpy()[i_auth]
    out["authoritative_facility_name"] = auth.name.to_numpy()[i_auth]
    out["authoritative_facility_kind"] = auth.kind.to_numpy()[i_auth]
    out["authoritative_facility_sources"] = auth.sources.to_numpy()[i_auth]
    return out, fac


def attach_states(units: pd.DataFrame, cache_dir: Path) -> pd.Series:
    import geopandas as gpd
    from src.thermal.pipeline import load_boundary
    states = load_boundary("ADM1", cache_dir).to_crs(4326)[["shapeName", "geometry"]]
    cells = units.drop_duplicates("cell_key")[["cell_key", "lat", "lon"]]
    pts = gpd.GeoDataFrame(cells, geometry=gpd.points_from_xy(cells.lon, cells.lat), crs=4326)
    j = gpd.sjoin(pts, states, how="left", predicate="within").drop_duplicates("cell_key").set_index("cell_key")["shapeName"]
    return units.cell_key.map(j)


# --------------------------------------------------------------------------- rules
def _ascii(series: pd.Series) -> pd.Series:
    """geoBoundaries spells states with diacritics ('Haryana' arrives as 'Haryāna'); compare without them."""
    import unicodedata
    return series.fillna("").map(lambda v: "".join(c for c in unicodedata.normalize("NFKD", v) if not unicodedata.combining(c)))


def in_agri_region(state: pd.Series, lon: pd.Series) -> pd.Series:
    s = _ascii(state)
    is_pun_har = s.str.contains("Punjab|Haryana", case=False, regex=True)
    is_west_up = s.str.contains("Uttar Pradesh", case=False) & (lon <= R.WEST_UP_MAX_LON)
    return is_pun_har | is_west_up


def apply_rules(u: pd.DataFrame) -> pd.DataFrame:
    """Add boolean columns, one per class, from the (already attached) unit features."""
    out = u.copy()
    out["is_industrial_persistent"] = (
        (out.distance_to_authoritative_facility_m <= R.PERSISTENT_MAX_DISTANCE_M)
        & (out.facility_baseline_active_fraction >= R.PERSISTENT_MIN_ACTIVE_FRACTION))
    far = out.distance_to_any_facility_m > R.NO_FACILITY_RADIUS_M
    episodic = out.cell_baseline_active_fraction < R.EPISODIC_MAX_ACTIVE_FRACTION
    out["is_agricultural_burning"] = (
        (out.lc_crop >= R.CROP_MIN) & in_agri_region(out.state, out.lon) & out.window_month.isin(R.AGRI_MONTHS) & far & episodic)
    out["is_vegetation_fire"] = (out.lc_woody >= R.WOODY_MIN) & far & episodic
    return out


def assign_split(u: pd.DataFrame, held_out: list[str]) -> pd.Series:
    """train: other regions AND before TEST_FROM; test: held-out regions AND from TEST_FROM; everything else is
    'unused' (so no region or period appears on both sides)."""
    in_held = u.state.isin(held_out)
    late = pd.to_datetime(u.window_start) >= pd.Timestamp(R.TEST_FROM)
    split = np.where(~in_held & ~late, "train", np.where(in_held & late, "test", "unused"))
    return pd.Series(split, index=u.index)


SOURCE_URL = {"wri_gppd": "https://datasets.wri.org/dataset/globalpowerplantdatabase",
              "gem": "https://globalenergymonitor.org/",
              "ggfr": "https://www.worldbank.org/en/programs/gasflaringreduction/global-flaring-data",
              "vnf": "https://eogdata.mines.edu/products/vnf/"}


def evidence_url(label: str, facility_sources: str | None) -> str:
    """Where a reader can check the label's main input."""
    if label == "industrial_persistent":
        for s in str(facility_sources or "").split(","):
            if s in SOURCE_URL:
                return SOURCE_URL[s]
    return "https://esa-worldcover.org/en/data-access"        # land-cover input of the ag / vegetation rules


# --------------------------------------------------------------------------- main build
def build(archive_dirs: list[Path], facilities_geojson: Path, incidents_csv: Path | None, out_dir: Path,
          cache_dir: Path = Path("data/thermal"), max_per_class: int = 1500, max_per_facility: int = 30,
          landcover_candidates: int = 4000, seed: int = 20261015, use_landcover: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    det = bl.load_archives(archive_dirs)
    det = det[det.confidence != "low"].copy()
    sites = SiteIndex.from_geojson(facilities_geojson)
    geo = bl.assign_geo(det, sites)
    baseline = bl.BaselineIndex.from_detections(det, sites)       # 365-day window from the same archive
    logger.info("baseline: %d entities, %d covered days", len(baseline), baseline.covered_days)

    units = build_units(geo)
    logger.info("units: %d (cell, window) with detections", len(units))
    units, fac = attach_facility_distances(units, facilities_geojson)
    units["state"] = attach_states(units, cache_dir)
    b_fac = {k: v for (s, k), v in baseline._rows.items() if s == "facility"}
    b_cell = {k: v for (s, k), v in baseline._rows.items() if s == "cell"}
    units["facility_baseline_active_fraction"] = units.authoritative_facility_id.map(
        lambda k: float(b_fac[k].active_fraction) if k in b_fac else 0.0)
    units["cell_baseline_active_fraction"] = units.cell_key.map(
        lambda k: float(b_cell[k].active_fraction) if k in b_cell else 0.0)
    units["lc_crop"] = np.nan
    units["lc_woody"] = np.nan

    # ---- land cover only for candidates (network reads), seeded sample per rule family
    if use_landcover:
        far = units.distance_to_any_facility_m > R.NO_FACILITY_RADIUS_M
        episodic = units.cell_baseline_active_fraction < R.EPISODIC_MAX_ACTIVE_FRACTION
        agri_pool = units[far & episodic & in_agri_region(units.state, units.lon) & units.window_month.isin(R.AGRI_MONTHS)]
        veg_pool = units[far & episodic & ~units.index.isin(agri_pool.index)]
        picked = []
        for pool in (agri_pool, veg_pool):
            cells = pool.drop_duplicates("cell_key")
            n = min(landcover_candidates, len(cells))
            picked.append(cells.iloc[rng.choice(len(cells), size=n, replace=False)] if n else cells.iloc[:0])
        cells = pd.concat(picked).drop_duplicates("cell_key")[["cell_key", "lat", "lon"]].reset_index(drop=True)
        lc = landcover.sample_many(cells[["lat", "lon"]], out_dir / "landcover_cache_labels.json")
        cells["lc_crop"], cells["lc_woody"] = lc.lc_crop.to_numpy(), lc.lc_woody.to_numpy()
        m = cells.set_index("cell_key")
        units["lc_crop"] = units.cell_key.map(m.lc_crop)
        units["lc_woody"] = units.cell_key.map(m.lc_woody)

    units = apply_rules(units)
    held = choose_held_out(units, max_per_facility=max_per_facility)
    units["split"] = assign_split(units, held)

    # ---- one label per unit; a unit satisfying two rules is ambiguous and dropped (never guessed)
    flags = ["is_industrial_persistent", "is_agricultural_burning", "is_vegetation_fire"]
    n_true = units[flags].sum(axis=1)
    ambiguous = int((n_true > 1).sum())
    cand = units[n_true == 1].copy()
    cand["label"] = cand[flags].idxmax(axis=1).str.replace("is_", "", regex=False)

    picks = []
    for cls, g in cand.groupby("label"):
        g = g.sample(frac=1.0, random_state=seed)
        if cls == "industrial_persistent":
            g = g.groupby("authoritative_facility_id", group_keys=False).head(max_per_facility)
        # 'unused' units (held-out region before the cutoff, or held-in region after it) are not part of the set
        parts = [g[g.split == s].head(max_per_class if s == "train" else max(max_per_class // 2, 1)) for s in ("train", "test")]
        picks.append(pd.concat(parts))
    lab = pd.concat(picks, ignore_index=True)

    lab["unit_type"] = "cell_window"
    lab["source_id"] = None
    lab["label_source"] = RULE_TEXT = lab.label.map(lambda c: f"{R.RULES[c]['label_source']}@{R.RULE_VERSION}")
    lab["labelled_by"] = f"rules:{R.RULE_VERSION}"
    lab["rule_version"] = R.RULE_VERSION
    lab["evidence_url"] = [evidence_url(r.label, r.authoritative_facility_sources) for r in lab.itertuples()]
    lab["region_group"] = np.where(lab.state.isin(held), "held_out", "train_regions")
    lab["label_id"] = ["lbl-" + hashlib.sha1(f"{r.cell_key}|{r.window_start}|{r.label}|{R.RULE_VERSION}".encode()).hexdigest()[:16]
                       for r in lab.itertuples()]
    lab["detail"] = [json.dumps({k: (None if (isinstance(v, float) and np.isnan(v)) else (round(v, 4) if isinstance(v, float) else v))
                                 for k, v in {
        "distance_to_authoritative_facility_m": r.distance_to_authoritative_facility_m,
        "distance_to_any_facility_m": r.distance_to_any_facility_m,
        "facility": r.authoritative_facility_name if r.label == "industrial_persistent" else None,
        "facility_kind": r.authoritative_facility_kind if r.label == "industrial_persistent" else None,
        "facility_sources": r.authoritative_facility_sources if r.label == "industrial_persistent" else None,
        "facility_baseline_active_fraction": r.facility_baseline_active_fraction,
        "cell_baseline_active_fraction": r.cell_baseline_active_fraction,
        "lc_crop": r.lc_crop, "lc_woody": r.lc_woody, "window_month": r.window_month,
        "confirmation": ("none (MCD64A1 not available)" if r.label == "vegetation_fire" else None)}.items()})
        for r in lab.itertuples()]
    lab = lab[LABEL_COLUMNS[:0] + [c for c in LABEL_COLUMNS if c in lab.columns]]

    # ---- curated incidents (test-only: far too few to train on, and independent of the rule inputs)
    inc_rows = []
    if incidents_csv and Path(incidents_csv).exists():
        inc = pd.read_csv(incidents_csv)
        inc = inc[inc.verified.astype(str).str.lower().isin(["true", "1", "yes"])]
        for r in inc.itertuples():
            d0 = pd.Timestamp(r.event_date).date()
            inc_rows.append({
                "label_id": f"inc-{r.incident_id}", "unit_type": "incident", "cell_key": None, "source_id": r.incident_id,
                "lat": r.lat, "lon": r.lon, "state": r.state, "window_start": d0 - timedelta(days=R.INCIDENT_DAYS_BEFORE),
                "window_end": d0 + timedelta(days=R.INCIDENT_DAYS_AFTER), "label": "industrial_fire",
                "label_source": R.RULES["industrial_fire"]["label_source"], "evidence_url": r.source_url,
                "labelled_by": "curated:faizdevx (source text checked by script)", "rule_version": R.RULE_VERSION,
                "split": "incident_test", "region_group": "held_out" if r.state in held else "train_regions",
                "n_detections": None, "n_days": None, "frp_max": None, "night_fraction": None,
                "detail": json.dumps({"incident_id": r.incident_id, "facility": r.facility, "location_basis": r.location_basis,
                                      "radius_km": r.radius_km, "osm_ref": r.osm_ref, "evidence_publisher": r.source_publisher,
                                      "firms_status": getattr(r, "firms_status", None),
                                      "firms_event_detections": None if pd.isna(getattr(r, "firms_event_detections", np.nan)) else int(r.firms_event_detections),
                                      "firms_event_max_frp": None if pd.isna(getattr(r, "firms_event_max_frp", np.nan)) else round(float(r.firms_event_max_frp), 2)})})
    lab = pd.concat([lab, pd.DataFrame(inc_rows, columns=LABEL_COLUMNS)], ignore_index=True) if inc_rows else lab

    for c in ("window_start", "window_end"):
        lab[c] = pd.to_datetime(lab[c]).dt.date.astype(str)
    lab = lab[LABEL_COLUMNS].sort_values(["label", "split", "label_id"]).reset_index(drop=True)

    parquet = out_dir / f"labels_{R.RULE_VERSION.split('-')[-1]}.parquet"
    lab.to_parquet(parquet, index=False)
    csv_gz = parquet.with_suffix(".csv.gz")
    lab.to_csv(csv_gz, index=False, compression="gzip")

    counts = lab.groupby(["label", "split"]).size().unstack(fill_value=0)
    manifest = {
        "rule_version": R.RULE_VERSION, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(), "seed": seed,
        "unit": f"1 km EPSG:6933 cell x {R.WINDOW_DAYS}-day window with >= 1 non-low-confidence VIIRS detection; windows start "
                f"{R.FIRST_WINDOW} (both satellites in the archive)",
        "total_labels": int(len(lab)), "counts": {k: {s: int(v) for s, v in row.items()} for k, row in counts.iterrows()},
        "class_totals": lab.label.value_counts().to_dict(),
        "ambiguous_units_dropped": ambiguous,
        "split": {"policy": "train = held-in regions AND window before test_from; test = held-out regions AND window from test_from; "
                            "incidents are test-only", "test_from": str(R.TEST_FROM), "held_out_states": held},
        "rules": {c: {"rule": v["rule"], "inputs": v["inputs"]} for c, v in R.RULES.items()},
        "parameters": {"window_days": R.WINDOW_DAYS, "max_per_class": max_per_class, "max_per_facility": max_per_facility,
                       "landcover_candidates_per_family": landcover_candidates, "baseline_covered_days": baseline.covered_days,
                       "baseline_window": [str(baseline.window_start), str(baseline.window_end)]},
        "known_deviations": [
            "vegetation_fire is not confirmed by MODIS MCD64A1 burned area (needs an Earthdata login)",
            "industrial_persistent uses WRI GPPD and World Bank/VIIRS Nightfire flare sites; Global Energy Monitor trackers are not loaded",
            "agricultural_burning / vegetation_fire land cover comes from a seeded sample of candidate cells, not every cell",
        ],
        "inputs": {
            "archive_files": sum(len(list(Path(d).glob("*.parquet"))) for d in archive_dirs),
            "archive_detections": int(len(det)),
            "facilities_geojson_sha256": sha256_of(Path(facilities_geojson)),
            "incidents_csv_sha256": sha256_of(Path(incidents_csv)) if incidents_csv and Path(incidents_csv).exists() else None,
        },
        "outputs": {parquet.name: {"sha256": sha256_of(parquet), "bytes": parquet.stat().st_size},
                    csv_gz.name: {"sha256": sha256_of(csv_gz), "bytes": csv_gz.stat().st_size}},
    }
    (out_dir / f"manifest_{R.RULE_VERSION.split('-')[-1]}.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def persist_postgis(dsn: str, labels_parquet: Path) -> int:
    import psycopg2
    from psycopg2.extras import execute_values
    lab = pd.read_parquet(labels_parquet)
    sql = (Path(__file__).resolve().parents[2] / "sql" / "migrations" / "007_labels.sql").read_text(encoding="utf-8")
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("DELETE FROM labels WHERE rule_version = %s", (R.RULE_VERSION,))

            def nn(v):
                return None if v is None or (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)
            rows = [(r.label_id, r.unit_type, nn(r.cell_key), nn(r.source_id), float(r.lat), float(r.lon), nn(r.state),
                     r.window_start, r.window_end, r.label, r.label_source, r.evidence_url, r.labelled_by, r.rule_version,
                     r.split, r.region_group, nn(r.n_detections), nn(r.n_days), nn(r.frp_max), nn(r.night_fraction), r.detail)
                    for r in lab.itertuples()]
            execute_values(cur, """INSERT INTO labels (label_id, unit_type, cell_key, source_id, geometry, state, window_start,
                                   window_end, label, label_source, evidence_url, labelled_by, rule_version, split, region_group,
                                   n_detections, n_days, frp_max, night_fraction, detail) VALUES %s""",
                           [(a, b, c, d, f"SRID=4326;POINT({lo} {la})", g, h, i, j, k, l, m, n, o, p, q, r_, s_, t_, u_)
                            for (a, b, c, d, la, lo, g, h, i, j, k, l, m, n, o, p, q, r_, s_, t_, u_) in rows],
                           template="(%s,%s,%s,%s,ST_GeomFromEWKT(%s),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", page_size=2000)
    finally:
        conn.close()
    return len(lab)
