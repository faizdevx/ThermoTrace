"""Attach satellite-imagery evidence to a cycle's sources.

Every source gets whatever is already in the on-disk cache (so a result survives from cycle to cycle). Only a few
candidates per cycle are computed - alerts first, then industrial fire candidates, then industrial sources whose power
is far above their own baseline - inside a time budget, so a slow STAC / COG service never stalls the live loop.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import core

logger = logging.getLogger(__name__)

COLUMNS = ["imagery_available", "imagery_scene_id", "imagery_scene_time", "imagery_cloud_percent", "imagery_before_time",
           "imagery_dnbr_p95", "imagery_burned_fraction", "imagery_swir_hot_pixels", "imagery_landsat_excess_k",
           "imagery_checked_at"]
_ALERT_RANK = {"critical": 0, "high": 1, "watch": 2}


def candidate_order(src: pd.DataFrame, priority_ids=()) -> pd.DataFrame:
    """Sources worth spending imagery requests on, best first. ``priority_ids`` (sources that raise an alert) go first."""
    s = src.copy()
    rank = s["alert_level"].map(_ALERT_RANK).fillna(9)
    rank = rank.where(~s["source_id"].isin(set(priority_ids)), -1)
    fire = (s["thermal_class"] == "industrial_fire_candidate").astype(int)
    z = pd.to_numeric(s.get("frp_z"), errors="coerce").fillna(0)
    industrial_hot = ((s.get("model_class") == "industrial_persistent") & (z >= 3)).astype(int)
    keep = (rank < 9) | (fire == 1) | (industrial_hot == 1)
    s = s[keep].assign(_rank=rank[keep], _fire=fire[keep], _z=z[keep])
    return s.sort_values(["_rank", "_fire", "_z", "frp_max"], ascending=[True, False, False, False])


def _row_values(result: dict | None) -> dict:
    r = result or {}
    sc, bs, hp, idx, ls = r.get("scene") or {}, r.get("before_scene") or {}, r.get("hot_pixels") or {}, r.get("indices") or {}, r.get("landsat") or {}
    return {"imagery_available": bool(r.get("available")) if result else None, "imagery_scene_id": sc.get("id"),
            "imagery_scene_time": sc.get("datetime"), "imagery_cloud_percent": sc.get("cloud_cover_percent"),
            "imagery_before_time": bs.get("datetime"), "imagery_dnbr_p95": idx.get("dnbr_p95"),
            "imagery_burned_fraction": idx.get("burned_fraction"),
            "imagery_swir_hot_pixels": hp.get("count") if r.get("available") else None,
            "imagery_landsat_excess_k": ls.get("st_excess_k") if ls.get("available") else None,
            "imagery_checked_at": r.get("computed_at")}


def enrich_sources(src: pd.DataFrame, cache_dir: str | Path, per_cycle: int = 4, budget_seconds: float = 240.0,
                   analyse=core.analyse_source, priority_ids=()) -> tuple[pd.DataFrame, dict]:
    """Return ``(sources with imagery_* columns and imagery lines appended to reasons, stats)``."""
    out = src.copy().reset_index(drop=True)
    cache = core.DiskCache(cache_dir)
    if core.netguard.enabled():          # offline mode: attach what is cached, compute nothing
        per_cycle = 0
    stats = {"cached": 0, "computed": 0, "failed": 0, "candidates": 0, "budget_seconds": budget_seconds}
    results: dict[int, dict | None] = {}
    for i, r in out.iterrows():
        results[i] = cache.get(str(r.source_id), str(r.last_seen))
        stats["cached"] += results[i] is not None
    cand = candidate_order(out, priority_ids)
    stats["candidates"] = int(len(cand))
    started = time.time()
    done = 0
    for i, r in cand.iterrows():
        if done >= per_cycle or time.time() - started > budget_seconds:
            break
        if results.get(i) is not None:
            continue
        try:
            res = analyse(float(r.lon), float(r.lat), pd.Timestamp(r.last_seen).to_pydatetime(),
                          first_seen=pd.Timestamp(r.first_seen).to_pydatetime(), with_images=True)
            cache.put(str(r.source_id), str(r.last_seen), res)
            results[i] = res
            stats["computed"] += 1
        except Exception as exc:  # noqa: BLE001 - imagery is evidence, never a reason to lose a cycle
            stats["failed"] += 1
            logger.warning("imagery for %s skipped: %s", r.source_id, exc)
        done += 1
    vals = pd.DataFrame([_row_values(results.get(i)) for i in range(len(out))], columns=COLUMNS)
    out = pd.concat([out, vals], axis=1)
    new_reasons = []
    for i, r in out.iterrows():
        base = list(r.reasons) if isinstance(r.reasons, (list, tuple)) else []
        extra = core.imagery_reasons(results.get(i)) if results.get(i) is not None else []
        new_reasons.append(base + [e for e in extra if e not in base])
    out["reasons"] = new_reasons
    try:
        cache.prune(14)
    except OSError:
        pass
    return out, stats
