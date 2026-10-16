from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import DBAPIError, OperationalError

from backend.app.config import get_settings
from backend.app.security import audit as audit_log, deps as sec
from backend.app.db import check_db_connection
from backend.app.services import catalog, events, facilities_repo, imagery, sync_status, thermal_repo
from backend.app.services import postgis_repo as repo
from backend.app.services.postgis_repo import InvalidFilter, parse_bbox

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
LEVELS = {"india", "states", "districts"}
FILTER_KEYS = ("bbox", "state", "district", "industry_type", "status", "confidence")


def _live() -> bool:
    return get_settings().data_mode == "postgis"


def _audit_export(request: Request | None, action: str, detail: dict[str, Any]) -> None:
    """Every download is written to the audit log before the first byte is sent."""
    if request is None:
        return
    if get_settings().auth_mode == "off" and not _live():
        return
    try:
        sec.audit_event(request, action, None, detail)
    except Exception:  # noqa: BLE001
        if get_settings().auth_mode != "off":
            raise HTTPException(status_code=503, detail="Could not write the audit log; the export was refused.") from None
        logger.warning("export not audited (no audit log)")


def _filters(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v not in (None, "")}


# ── tiny revision-keyed cache (cache invalidated by every committed data change) ──
_cache: dict[str, tuple[int, float, Any]] = {}
_CACHE_TTL = 30.0


def _cached(key: str, builder):
    """Cache by (key, data revision). The revision lookup is one indexed 1-row read."""
    revision = repo.get_revision()["revision"]
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] == revision and now - hit[1] < _CACHE_TTL:
        return hit[2]
    value = builder()
    if len(_cache) > 200:
        _cache.clear()
    _cache[key] = (revision, now, value)
    return value


# ── Health ─────────────────────────────────────────────────────────────────

@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness + readiness facts. Always 200 while the API process is up:
    database/dataset problems are REPORTED in the body, not hidden behind a 5xx."""
    settings = get_settings()
    body: dict[str, Any] = {
        "status": "ok",                       # API process is alive
        "data_mode": settings.data_mode,
        "synthetic": settings.data_mode == "synthetic",
        "time": datetime.now(timezone.utc).isoformat(),
    }
    if settings.data_mode == "synthetic":
        body["database"] = {"status": "not_used"}
        body["dataset"] = {"active_sites": len(catalog.get_sites()), "label": "SYNTHETIC DEMO DATA"}
        body["ready"] = True
        return body
    db_ok = check_db_connection()
    body["database"] = {"status": "ok" if db_ok else "unavailable"}
    if db_ok:
        try:
            summary = _cached("dataset_summary", repo.dataset_summary)
            body["dataset"] = summary
            body["revision"] = summary["revision"]
            body["ready"] = summary["active_sites"] > 0
        except Exception:  # noqa: BLE001 - schema missing etc.
            logger.exception("health: dataset summary failed")
            body["database"] = {"status": "error"}
            body["ready"] = False
    else:
        body["ready"] = False
    body["event_stream"] = {"listening": events.hub.connected}
    return body


# ── Boundaries ─────────────────────────────────────────────────────────────

@router.get("/boundaries/{level}")
def boundaries(
    level: str,
    response: Response,
    state: str | None = Query(default=None, description="restrict districts/states to this state"),
    bbox: str | None = Query(default=None, description="west,south,east,north"),
    simplify: float | None = Query(default=None, ge=0, le=0.5, description="simplification tolerance in degrees"),
) -> dict[str, Any]:
    if level not in LEVELS:
        raise HTTPException(status_code=404, detail="Boundary level not found")
    if not _live():
        return catalog.get_boundary_collection(level)
    parse_bbox(bbox)  # 422 on malformed
    key = f"boundaries:{level}:{state}:{bbox}:{simplify}"
    data = _cached(key, lambda: repo.get_boundaries(level, state, bbox, simplify))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return data


# ── Filter options ─────────────────────────────────────────────────────────

@router.get("/filters/options")
def filter_options(state: str | None = None) -> dict[str, list[str]]:
    if not _live():
        return catalog.get_filter_options()
    return _cached(f"options:{(state or '').lower()}", lambda: repo.get_filter_options(state))


# ── Industries ─────────────────────────────────────────────────────────────

def _collection(features: list[dict[str, Any]], total: int, limit: int, offset: int, revision: int) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "total": total, "returned": len(features), "limit": limit, "offset": offset,
            "truncated": offset + len(features) < total, "revision": revision,
        },
    }


def _list(filters: dict[str, Any], limit: int, offset: int, geometry: str) -> dict[str, Any]:
    if _live():
        features, total, revision = repo.list_sites(filters, limit, offset, geometry)
        return _collection(features, total, limit, offset, revision)
    parse_bbox(filters.get("bbox"))
    sites = catalog.filter_sites(filters)
    page = sites[offset: offset + limit]
    collection = catalog.to_feature_collection(page)
    collection["meta"] = {"total": len(sites), "returned": len(page), "limit": limit, "offset": offset,
                          "truncated": offset + len(page) < len(sites), "revision": 0, "synthetic": True}
    return collection


@router.get("/industries/export")
def export_industries(
    format: str = Query(default="csv", pattern="^(csv|geojson)$"),
    q: str | None = None,
    bbox: str | None = None,
    state: str | None = None,
    district: str | None = None,
    industry_type: str | None = None,
    status: str | None = None,
    confidence: str | None = None,
    request: Request = None,
    _user=Depends(sec.require("analyst")),
):
    """Stream EVERY site matching the filters (no pagination, no silent truncation). Audited."""
    filters = _filters(q=q, bbox=bbox, state=state, district=district,
                       industry_type=industry_type, status=status, confidence=confidence)
    parse_bbox(bbox)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if _live():
        total = repo.count_matching(filters)
        revision = repo.get_revision()["revision"]
        stream: Iterator[str] = repo.export_csv(filters) if format == "csv" else repo.export_geojson(filters)
    else:
        sites = catalog.filter_sites(filters)
        total, revision = len(sites), 0
        stream = iter(_synthetic_export(sites, format))
    media = "text/csv; charset=utf-8" if format == "csv" else "application/geo+json"
    _audit_export(request, "export.sites", {"format": format, "filters": filters, "rows": total})
    return StreamingResponse(
        stream, media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="thermotrace-sites-{stamp}.{ "csv" if format == "csv" else "geojson"}"',
            "X-Total-Count": str(total),
            "X-Data-Revision": str(revision),
            "X-Attribution": "Data (c) OpenStreetMap contributors, ODbL 1.0",
            "Cache-Control": "no-store",
        },
    )


def _synthetic_export(sites: list[dict[str, Any]], fmt: str) -> list[str]:
    if fmt == "geojson":
        return [json.dumps(catalog.to_feature_collection(sites))]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(repo.CSV_COLUMNS)
    for site in sites:
        writer.writerow([site.get(c, "") for c in repo.CSV_COLUMNS])
    return [buffer.getvalue()]


@router.get("/industries")
def industries(
    bbox: str | None = Query(default=None, description="west,south,east,north"),
    state: str | None = None,
    district: str | None = None,
    industry_type: str | None = None,
    status: str | None = None,
    confidence: str | None = Query(default=None, description="evidence level: high|medium|low"),
    q: str | None = None,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    geometry: str = Query(default="point", pattern="^(point|full)$"),
) -> dict[str, Any]:
    filters = _filters(bbox=bbox, state=state, district=district, industry_type=industry_type,
                       status=status, confidence=confidence, q=q)
    return _list(filters, limit, offset, geometry)


@router.get("/industries/search")
def search_industries(
    q: str = Query(min_length=1, max_length=200),
    bbox: str | None = None,
    state: str | None = None,
    district: str | None = None,
    industry_type: str | None = None,
    status: str | None = None,
    confidence: str | None = None,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filters = _filters(q=q, bbox=bbox, state=state, district=district, industry_type=industry_type,
                       status=status, confidence=confidence)
    return _list(filters, limit, offset, "point")


@router.get("/industries/{site_id}")
def industry_detail(site_id: str) -> dict[str, Any]:
    feature = repo.get_site(site_id) if _live() else catalog.get_site_feature(site_id)
    if feature is None:
        raise HTTPException(status_code=404, detail="Industrial site not found (it may have been removed from the source)")
    return feature


# ── Statistics ─────────────────────────────────────────────────────────────

@router.get("/statistics")
def statistics(
    bbox: str | None = None,
    state: str | None = None,
    district: str | None = None,
    industry_type: str | None = None,
    status: str | None = None,
    confidence: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """Counts over ACTIVE sites matching the filters; with no filter, the whole dataset."""
    filters = _filters(bbox=bbox, state=state, district=district, industry_type=industry_type,
                       status=status, confidence=confidence, q=q)
    if not _live():
        return catalog.get_statistics()
    parse_bbox(bbox)
    if bbox:  # viewport-dependent: do not cache
        return repo.get_statistics(filters)
    key = "stats:" + json.dumps(filters, sort_keys=True)
    return _cached(key, lambda: repo.get_statistics(filters))


# ── Sync status / history / events / admin ────────────────────────────────

@router.get("/sync/status")
def sync_status_endpoint() -> dict[str, Any]:
    """Liveness, database readiness, per-source freshness and data revision - reported separately."""
    now = datetime.now(timezone.utc)
    if not _live():
        return {"data_mode": "synthetic", "overall": {"state": "not_configured",
                "reason": "Synthetic demo data: no synchronization."}, "sources": [], "revision": 0,
                "api": {"alive": True}, "database": {"status": "not_used"}, "time": now.isoformat()}
    db_ok = check_db_connection()
    if not db_ok:
        return {"data_mode": "postgis", "overall": {"state": "offline", "reason": "Database is unavailable."},
                "sources": [], "revision": None, "api": {"alive": True},
                "database": {"status": "unavailable"}, "time": now.isoformat()}
    rows = repo.get_sync_rows()
    sources = [sync_status.derive_source(r, now) for r in rows]
    firms_source = _firms_sync_source(now)
    if firms_source:
        sources.append(firms_source)
    rev = repo.get_revision()
    return {
        "data_mode": "postgis",
        "api": {"alive": True, "event_stream_listening": events.hub.connected},
        "database": {"status": "ok"},
        "overall": sync_status.overall(sources),
        "sources": sources,
        "revision": rev["revision"],
        "revision_changed_at": rev["changed_at"],
        "time": now.isoformat(),
    }


def _firms_sync_source(now: datetime) -> dict[str, Any] | None:
    """Express the thermal worker's status in the same shape as the other sync sources."""
    fb = thermal_repo._file_singleton()
    backend = fb if fb.available() else thermal_repo.get_backend()
    if not backend.available():
        return None
    st = backend.run_info().get("status") or {}
    return sync_status.derive_firms(st, now)


@router.get("/sync/history")
def sync_history(source: str | None = None, limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    if not _live():
        return {"runs": []}
    return {"runs": repo.get_sync_history(source, limit)}


@router.post("/admin/sync/trigger")
def admin_trigger(
    request: Request,
    payload: dict[str, Any] | None = None,
    x_admin_token: str | None = Header(default=None),
    _user=Depends(sec.require("admin")),
) -> dict[str, Any]:
    """Ask the worker to run a source now. AUTH_MODE=off: needs ADMIN_TOKEN; otherwise the admin role. The API never runs jobs itself."""
    if get_settings().auth_mode == "off":
        token = get_settings().admin_token
        if not token:
            raise HTTPException(status_code=503, detail="Admin trigger is disabled (ADMIN_TOKEN not set).")
        if not x_admin_token or not secrets.compare_digest(x_admin_token, token):
            raise HTTPException(status_code=401, detail="Invalid admin token.")
    source = (payload or {}).get("source", "openstreetmap")
    if not _live() or not repo.request_manual_sync(str(source)):
        raise HTTPException(status_code=404, detail="Unknown or disabled source.")
    if get_settings().auth_mode != "off":
        sec.audit_event(request, "admin.sync_trigger", str(source))
    return {"requested": True, "source": source}


@router.get("/events")
async def event_stream(request: Request):
    """Server-Sent Events: ``hello`` (current revision), ``revision`` (new data committed),
    ``sync`` (job status change). Payloads are small counters, never records."""
    async def generator():
        queue = events.hub.subscribe()
        try:
            current: dict[str, Any] = {"revision": 0}
            if _live():
                try:
                    current = await asyncio.to_thread(repo.get_revision)
                except Exception:  # noqa: BLE001
                    current = {"revision": None}
            yield "retry: 3000\n"
            yield events.format_sse("hello", {"revision": current.get("revision"),
                                              "server_time": time.time()}, current.get("revision"))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                data = message["data"]
                yield events.format_sse(message["event"], data, data.get("revision"))
        finally:
            events.hub.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Thermal sources (NASA FIRMS classified) ──────────────────────────────────

def _thermal_filters(thermal_class, bbox, state, min_frp, alerts_only, hours, site_kind):
    return thermal_repo.ThermalFilters(classes=thermal_class, bbox=bbox, state=state, min_frp=min_frp,
                                       alerts_only=alerts_only, hours=hours, kind=site_kind)


def _thermal_backend():
    backend = thermal_repo.get_backend()
    if not backend.available():
        raise HTTPException(status_code=503,
                            detail="Thermal data not available yet (no snapshot / database run). "
                                   "Run: python -m src.thermal.cli refresh")
    return backend


@router.get("/facilities")
def facilities(bbox: str | None = None, kind: str | None = None, source: str | None = None,
               limit: int = Query(default=5000, ge=1, le=facilities_repo.MAX_LIMIT)) -> dict[str, Any]:
    """Merged industrial facilities (OSM + WRI + GEM + VNF) with per-source licence and attribution."""
    try:
        return facilities_repo.query(bbox=bbox, kind=kind, source=source, limit=limit)
    except InvalidFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/facilities/{facility_id}")
def facility_detail(facility_id: str) -> dict[str, Any]:
    """One facility with its footprint polygon (where OSM has one) and every merged source record."""
    found = facilities_repo.detail(facility_id)
    if found is None:
        raise HTTPException(status_code=404, detail="facility not found")
    return found


@router.get("/thermal/model")
def thermal_model() -> dict[str, Any]:
    """The trained model's identity and its held-out evaluation (rules vs model, blind ablation, incident check)."""
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    try:
        from sqlalchemy import text
        from backend.app.db import get_engine
        with get_engine().connect() as c:
            row = c.execute(text("SELECT model_version, metrics, registered_at FROM thermal_model "
                                 "ORDER BY registered_at DESC LIMIT 1")).mappings().first()
        if row:
            return {"available": True, "source": "database", "model_version": row["model_version"],
                    "registered_at": row["registered_at"].isoformat(), "metrics": row["metrics"]}
    except Exception:  # noqa: BLE001 - no database / table: fall back to the file next to the model
        pass
    default = _Path(__file__).resolve().parents[4] / "industrial-data" / "models" / "metrics.json"
    path = _Path(_os.getenv("THERMAL_MODEL_METRICS", str(default)))
    if path.exists():
        m = _json.loads(path.read_text(encoding="utf-8"))
        return {"available": True, "source": "file", "model_version": m.get("model_version"), "metrics": m}
    return {"available": False, "reason": "No trained model registered. Run: python industrial-data/scripts/evaluate.py"}


@router.get("/system/offline")
def system_offline() -> dict[str, Any]:
    """Offline mode: whether it is on, which hosts may be reached, and what this process tried to reach and was refused."""
    import os
    try:
        from src import netguard
    except ImportError:
        return {"offline": False}
    pm = os.getenv("PMTILES_FILE", "/basemap/india.pmtiles")
    return {**netguard.status(), "worker": thermal_repo.read_worker_offline(), "basemap": {"pmtiles_file": pm, "present": os.path.exists(pm)}}


@router.get("/dashboard")
def dashboard() -> dict[str, Any]:
    """Alerts today, class mix over time, top states and the model card in one response (the dashboard tab)."""
    from backend.app.services import dashboard as dash
    metrics = None
    m = thermal_model()
    if m.get("available"):
        metrics = m.get("metrics")
    if _live():
        try:
            from backend.app.db import get_engine
            conn = get_engine().raw_connection()
            try:
                return dash.from_postgis(conn, metrics)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - fall back to the snapshot rather than a blank dashboard
            logger.warning("dashboard from PostGIS failed (%s); using the snapshot", exc)
    backend = _thermal_backend()
    feats, _ = backend.list_sources(_thermal_filters(None, None, None, None, False, None, None), thermal_repo.MAX_LIMIT, 0)
    return dash.from_features(feats, metrics)


# ---------------------------------------------------------------- alerts (analyst inbox)
@router.get("/alerts")
def list_alerts(status: str | None = Query(default=None, pattern="^(open|confirmed|rejected|unsure)$"),
                limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    """Alerts, open ones first. Needs PostGIS (alerts are written by the thermal worker); empty in synthetic mode."""
    if not _live():
        return {"available": False, "alerts": [], "decisions_enabled": False, "channels": [], "demo_readonly": get_settings().demo_readonly}
    from backend.app.services import alerts_repo
    rows = alerts_repo.list_alerts(status, limit)
    return {"available": True, "alerts": rows, "channels": alerts_repo.channels(rows),
            "decisions_enabled": bool(get_settings().analyst_token) and not get_settings().demo_readonly,
            "demo_readonly": get_settings().demo_readonly}


@router.get("/alerts/{alert_id}")
def alert_detail(alert_id: int) -> dict[str, Any]:
    if not _live():
        raise HTTPException(status_code=404, detail="Alerts need the PostGIS stack.")
    from backend.app.services import alerts_repo
    a = alerts_repo.get_alert(alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Unknown alert.")
    return a


@router.get("/alerts/{alert_id}/report.pdf")
def alert_report(alert_id: int, request: Request, imagery: bool = True, _user=Depends(sec.require("analyst"))) -> Response:
    """Incident report for the alert: maps, imagery, reasons, timeline, decisions. Audited."""
    if not _live():
        raise HTTPException(status_code=404, detail="Alerts need the PostGIS stack.")
    from backend.app.services import alerts_repo, report
    a = alerts_repo.get_alert(alert_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Unknown alert.")
    img = None
    if imagery and a.get("last_seen"):
        try:   # the cached analysis when the worker made one; computed on demand otherwise (20-30 s the first time)
            from backend.app.services import imagery as imagery_service
            img = imagery_service.chip_for(a["source_id"], a["lon"], a["lat"], a["last_seen"], a.get("first_seen"))
        except Exception as exc:  # noqa: BLE001 - a report without imagery beats no report
            logger.warning("report imagery for alert %s unavailable: %s", alert_id, exc)
    pdf = report.build_pdf(a, img)
    _audit_export(request, "export.report", {"alert_id": alert_id, "bytes": len(pdf)})
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="thermotrace-alert-{alert_id}.pdf"'})


@router.post("/alerts/{alert_id}/decision")
def alert_decision(alert_id: int, payload: dict[str, Any], request: Request, x_analyst_token: str | None = Header(default=None),
                   user=Depends(sec.require("analyst"))) -> dict[str, Any]:
    """Confirm / reject / unsure. Writes the decision, a row in ``labels`` (analyst-v1) and an audit entry, in one transaction.
    AUTH_MODE=off: needs ANALYST_TOKEN and the name comes from the request; otherwise the analyst role and the signed-in user."""
    s = get_settings()
    if s.auth_mode == "off":
        token = s.analyst_token
        if not token:
            raise HTTPException(status_code=503, detail="Analyst decisions are disabled (ANALYST_TOKEN not set).")
        if not x_analyst_token or not secrets.compare_digest(x_analyst_token, token):
            raise HTTPException(status_code=401, detail="Invalid analyst token.")
        analyst = payload.get("analyst")
    else:
        analyst = user["username"]
    if not _live():
        raise HTTPException(status_code=404, detail="Alerts need the PostGIS stack.")
    from backend.app.services import alerts_repo
    decision = str(payload.get("decision", ""))
    if decision not in alerts_repo.DECISIONS:
        raise HTTPException(status_code=422, detail=f"decision must be one of {list(alerts_repo.DECISIONS)}")
    try:
        actor, role = sec.who(request)
        ip = sec.ip_of(request)
        out = alerts_repo.decide(alert_id, decision, payload.get("note"), analyst, audit=lambda cur, o: audit_log.record_cur(
            cur, actor if role else (analyst or "anonymous"), role, "alert.decision", str(alert_id),
            {"decision": decision, "note": (payload.get("note") or "")[:2000] or None, "label_id": o["label_id"], "label": o["label"]}, ip))
    except LookupError:
        raise HTTPException(status_code=404, detail="Unknown alert.") from None
    return out


@router.get("/thermal/classes")
def thermal_classes() -> dict[str, Any]:
    """Class metadata for legends: label, colour and what each class means."""
    return {"classes": [{"id": k, **v} for k, v in thermal_repo.CLASS_INFO.items()]}


@router.get("/thermal/status")
def thermal_status() -> dict[str, Any]:
    backend = thermal_repo.get_backend()
    if not backend.available():
        return {"backend": backend.name, "available": False,
                "reason": "No thermal snapshot yet. Run: python -m src.thermal.cli refresh"}
    info = backend.run_info()
    now = datetime.now(timezone.utc)
    st = info.get("status") or {}

    def age(key):
        value = st.get(key)
        return (now - datetime.fromisoformat(value)).total_seconds() if value else None

    refresh_age, detect_age = age("last_success_at"), age("latest_detection_at")
    state = "live" if refresh_age is not None and refresh_age < 3 * 3600 and (detect_age or 0) < 12 * 3600 else "delayed"
    replay = st.get("replay") or thermal_repo.read_replay()
    if replay:     # archived data on a virtual clock: "age" is meaningless, say what this is
        state = "replay"
    return {"available": True, **info, **({"replay": replay} if replay else {}), "freshness": {
        "state": state, "last_refresh_age_seconds": refresh_age, "latest_detection_age_seconds": detect_age,
        "note": "FIRMS VIIRS near-real-time data lag the satellite overpass by roughly 3 hours; "
                "live means refreshed within 3 h and holding detections from the last 12 h."}}


@router.get("/thermal/sources")
def thermal_sources(
    thermal_class: str | None = Query(default=None, description="comma-separated class ids"),
    bbox: str | None = None, state: str | None = None, site_kind: str | None = None,
    min_frp: float | None = Query(default=None, ge=0), alerts_only: bool = False,
    hours: int | None = Query(default=None, ge=1, le=24 * 30,
                              description="only sources active within N h of the newest detection"),
    limit: int = Query(default=2000, ge=1, le=thermal_repo.MAX_LIMIT), offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    backend = _thermal_backend()
    flt = _thermal_filters(thermal_class, bbox, state, min_frp, alerts_only, hours, site_kind)
    features, total = backend.list_sources(flt, limit, offset)
    return {"type": "FeatureCollection", "features": features,
            "meta": {"total": total, "returned": len(features), "limit": limit, "offset": offset,
                     "truncated": offset + len(features) < total, "backend": backend.name}}


@router.get("/thermal/sources/{source_id}")
def thermal_source(source_id: str) -> dict[str, Any]:
    backend = thermal_repo.get_backend()
    feature = backend.get_source(source_id) if backend.available() else None
    if feature is None:
        raise HTTPException(status_code=404, detail="Thermal source not found (it may have aged out of the window).")
    return feature


@router.get("/thermal/sources/{source_id}/imagery")
def thermal_source_imagery(source_id: str) -> dict[str, Any]:
    """Sentinel-2 chips (after + before, true colour and SWIR), NBR / dNBR, SWIR hot pixels and a Landsat thermal
    check for the source's location, or an honest 'not available'."""
    backend = thermal_repo.get_backend()
    feature = backend.get_source(source_id) if backend.available() else None
    if feature is None:
        raise HTTPException(status_code=404, detail="Thermal source not found (it may have aged out of the window).")
    lon, lat = feature["geometry"]["coordinates"]
    try:
        return imagery.chip_for(source_id, lon, lat, feature["properties"]["last_seen"], feature["properties"].get("first_seen"))
    except Exception as exc:  # noqa: BLE001 - STAC / COG network failures
        logger.warning("imagery failed for %s: %s", source_id, exc)
        raise HTTPException(status_code=502, detail="Satellite imagery service unavailable. Try again later.") from exc


@router.get("/thermal/stats")
def thermal_stats(
    thermal_class: str | None = None, bbox: str | None = None, state: str | None = None,
    site_kind: str | None = None, min_frp: float | None = Query(default=None, ge=0),
    alerts_only: bool = False, hours: int | None = Query(default=None, ge=1, le=24 * 30),
) -> dict[str, Any]:
    backend = _thermal_backend()
    flt = _thermal_filters(thermal_class, bbox, state, min_frp, alerts_only, hours, site_kind)
    return {**backend.stats(flt), "backend": backend.name,
            "scope": {"description": "Current thermal sources matching the filters.", "filters": flt.describe()}}


@router.get("/thermal/export")
def thermal_export(
    format: str = Query(default="csv", pattern="^(csv|geojson)$"),
    thermal_class: str | None = None, bbox: str | None = None, state: str | None = None,
    site_kind: str | None = None, min_frp: float | None = Query(default=None, ge=0),
    alerts_only: bool = False, hours: int | None = Query(default=None, ge=1, le=24 * 30),
    request: Request = None, _user=Depends(sec.require("analyst")),
):
    """Download the thermal sources matching the filters. Audited."""
    backend = _thermal_backend()
    flt = _thermal_filters(thermal_class, bbox, state, min_frp, alerts_only, hours, site_kind)
    features, total = backend.list_sources(flt, 1_000_000, 0)
    used = dict(thermal_class=thermal_class, bbox=bbox, state=state, site_kind=site_kind, min_frp=min_frp, alerts_only=alerts_only, hours=hours)
    _audit_export(request, "export.thermal", {"format": format, "filters": {k: v for k, v in used.items() if v not in (None, False)}, "rows": total})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        thermal_repo.export_rows(features, format),
        media_type="text/csv; charset=utf-8" if format == "csv" else "application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="thermotrace-thermal-{stamp}.{format}"',
                 "X-Total-Count": str(total),
                 "X-Attribution": "NASA FIRMS; OpenStreetMap contributors (ODbL); ESA WorldCover (CC BY 4.0)"})


def register_error_handlers(app) -> None:
    from fastapi.responses import JSONResponse

    @app.exception_handler(InvalidFilter)
    async def _invalid(_: Request, exc: InvalidFilter):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(OperationalError)
    async def _db_down(_: Request, exc: OperationalError):
        logger.error("database operational error: %s", exc.__class__.__name__)
        return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable."})

    @app.exception_handler(DBAPIError)
    async def _db_err(_: Request, exc: DBAPIError):
        logger.error("database error: %s", exc.__class__.__name__)
        return JSONResponse(status_code=503, content={"detail": "Database error."})
