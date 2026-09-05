from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app.services.catalog import (
    filter_sites,
    get_boundary_collection,
    get_filter_options,
    get_site_feature,
    get_statistics,
    search_sites,
    to_feature_collection,
)

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    from backend.app.config import get_settings  # noqa: PLC0415
    settings = get_settings()
    return {"status": "ok", "data_mode": settings.data_mode}


@router.get("/boundaries/{level}")
def boundaries(level: str) -> dict[str, Any]:
    if level not in {"india", "states", "districts"}:
        raise HTTPException(status_code=404, detail="Boundary level not found")
    return get_boundary_collection(level)


@router.get("/filters/options")
def filter_options() -> dict[str, list[str]]:
    return get_filter_options()


@router.get("/industries")
def industries(
    bbox: str | None = Query(default=None, description="west,south,east,north"),
    state: str | None = None,
    district: str | None = None,
    industry_type: str | None = None,
    status: str | None = None,
    confidence: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
) -> dict[str, Any]:
    sites = filter_sites(
        {
            "bbox": bbox,
            "state": state,
            "district": district,
            "industry_type": industry_type,
            "status": status,
            "confidence": confidence,
        }
    )
    if limit is not None:
        sites = sites[:limit]
    return to_feature_collection(sites)


@router.get("/industries/search")
def search_industries(
    q: str = Query(min_length=1),
    bbox: str | None = Query(default=None, description="west,south,east,north"),
    state: str | None = None,
    district: str | None = None,
    industry_type: str | None = None,
    status: str | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    sites = search_sites(
        q,
        {
            "bbox": bbox,
            "state": state,
            "district": district,
            "industry_type": industry_type,
            "status": status,
            "confidence": confidence,
        },
    )
    return to_feature_collection(sites)


@router.get("/industries/{site_id}")
def industry_detail(site_id: str) -> dict[str, Any]:
    feature = get_site_feature(site_id)
    if feature is None:
        raise HTTPException(status_code=404, detail="Industrial site not found")
    return feature


@router.get("/statistics")
def statistics() -> dict[str, Any]:
    return get_statistics()
