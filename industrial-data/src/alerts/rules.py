"""When does a thermal source become an alert, and how severe is it. Pure functions, no I/O.

Rule (either one is enough):

* **p_fire** - the classifier's probability of ``industrial_fire_candidate`` is at least ``P_FIRE_THRESHOLD``. This is the
  rule engine's softmax score: the trained model has no fire class (verified incidents are test-only, see
  docs/EVALUATION.md), so it cannot supply this number.
* **frp_z** - the source is at a known facility (inside its match radius, not merely nearest to it) and its radiative power is at least ``FRP_Z_THRESHOLD`` standard
  deviations above that facility's own 365-day baseline.

Both also need a detection in the last ``MAX_AGE_HOURS`` so an old source is never announced as news.

**Field-burning veto** (added after the Punjab replay, see docs/REPLAY.md): a source whose surroundings are at least 60 %
cropland (ESA WorldCover) and which was seen almost only by day (night share < 20 %) has the signature of crop-residue
burning, whatever mapped facility - landfill, brick kiln, quarry - happens to sit in the field. It is never alerted.
Without the veto a peak burning day produced 58 alerts at facilities embedded in farmland.
Severity: radiative power sets the base level (watch < 40 MW <= high < 100 MW <= critical); facilities where a fire
spreads or is hazardous (refinery, LNG, chemical, oil and gas storage) are raised one level.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

LEVELS = ["watch", "high", "critical"]
HAZARDOUS_KINDS = ("refinery", "lng", "chemical", "petrochemical", "oil", "gas", "fuel", "explosive", "fertili")
DEDUPE_HOURS = 6


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def settings() -> dict:
    return {"p_fire": _env_float("ALERT_P_FIRE_THRESHOLD", 0.6), "frp_z": _env_float("ALERT_FRP_Z_THRESHOLD", 3.0),
            "max_age_hours": _env_float("ALERT_MAX_AGE_HOURS", 24.0), "min_frp": _env_float("ALERT_MIN_FRP", 5.0),
            "dedupe_hours": _env_float("ALERT_DEDUPE_HOURS", DEDUPE_HOURS),
            "veto_crop": _env_float("ALERT_VETO_CROP_FRACTION", 0.6), "veto_night": _env_float("ALERT_VETO_NIGHT_FRACTION", 0.2)}


def is_hazardous(kind) -> bool:
    k = str(kind or "").lower()
    return any(h in k for h in HAZARDOUS_KINDS)


def severity_for(frp: float, kind) -> str:
    frp = 0.0 if frp is None or (isinstance(frp, float) and math.isnan(frp)) else float(frp)
    base = 2 if frp >= 100 else 1 if frp >= 40 else 0
    return LEVELS[min(base + (1 if is_hazardous(kind) else 0), 2)]


def _f(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


@dataclass
class Candidate:
    source_id: str
    facility_key: str
    trigger: str
    severity: str
    p_fire: float | None
    frp_max: float | None
    frp_z: float | None
    row: dict
    summary: str


def _text(v) -> str | None:
    """A string, or None for missing values (pandas NaN, None, empty)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = str(v).strip()
    return v or None


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def display_name(row: dict) -> str | None:
    """The facility's name; ids that merely look like names (flare sites carry a UUID) are not shown."""
    for key in ("site_name", "site_ref"):
        v = _text(row.get(key))
        if v and not _UUID.match(v) and not re.match(r"^(osm|wri|gem|nightfire|viirs)[:_-]", v, re.I):
            return v
    return None


def at_facility(row: dict) -> bool:
    """On a known facility: matched to one AND inside its radius. A nearest facility 30 km away is not "at" it."""
    return _text(row.get("site_ref")) is not None and row.get("site_within") is True


def facility_key(row: dict) -> str:
    return _text(row.get("site_ref")) if at_facility(row) else f"src:{row['source_id']}"


def p_fire(row: dict) -> float | None:
    s = row.get("class_scores")
    return _f(s.get("industrial_fire_candidate")) if isinstance(s, dict) else None


def summary_text(c: Candidate) -> str:
    r = c.row
    kind = str((_text(r.get("site_kind")) if at_facility(r) else None) or "facility").replace("_", " ")
    where = (display_name(r) or f"an unnamed {kind}") if at_facility(r) else "an unmapped location"
    bits = []
    if c.p_fire is not None and c.trigger in ("p_fire", "both"):
        bits.append(f"P(industrial fire) {c.p_fire:.0%}")
    if c.frp_z is not None and c.trigger in ("frp_z", "both"):
        bits.append(f"power {c.frp_z:+.1f} sd above this facility's own baseline")
    peak = f"peak {c.frp_max:.0f} MW" if c.frp_max is not None else "peak power unknown"
    return f"{c.severity.upper()}: possible industrial fire at {where} ({kind}), {_text(r.get('state')) or 'India'} - {peak}; " + "; ".join(bits)


def looks_like_field_burning(row: dict, cfg: dict) -> bool:
    crop, night = _f(row.get("lc_crop")), _f(row.get("night_fraction"))
    return crop is not None and night is not None and crop >= cfg["veto_crop"] and night < cfg["veto_night"]


def evaluate(sources: pd.DataFrame, now: datetime | None = None, cfg: dict | None = None,
             vetoed: list | None = None) -> list[Candidate]:
    """Candidates (one per facility, the strongest source) among the current sources; ``vetoed`` collects source ids
    that passed a threshold but look like field burning."""
    cfg = cfg or settings()
    now = now or datetime.now(timezone.utc)
    best: dict[str, Candidate] = {}
    for row in sources.to_dict("records"):
        last = row.get("last_seen")
        if last is None or pd.isna(last):
            continue
        if (now - pd.Timestamp(last).to_pydatetime()).total_seconds() > cfg["max_age_hours"] * 3600:
            continue
        frp = _f(row.get("frp_max"))
        if frp is None or frp < cfg["min_frp"]:
            continue
        pf, z = p_fire(row), _f(row.get("frp_z"))
        by_p = pf is not None and pf >= cfg["p_fire"]
        by_z = at_facility(row) and z is not None and z >= cfg["frp_z"]
        if not (by_p or by_z):
            continue
        if looks_like_field_burning(row, cfg):
            if vetoed is not None:
                vetoed.append(str(row["source_id"]))
            continue
        c = Candidate(str(row["source_id"]), facility_key(row), "both" if by_p and by_z else "p_fire" if by_p else "frp_z",
                      severity_for(frp, row.get("site_kind")), pf, frp, z, row, "")
        c.summary = summary_text(c)
        cur = best.get(c.facility_key)
        if cur is None or (LEVELS.index(c.severity), c.frp_max or 0) > (LEVELS.index(cur.severity), cur.frp_max or 0):
            best[c.facility_key] = c
    return sorted(best.values(), key=lambda c: (-LEVELS.index(c.severity), -(c.frp_max or 0)))
