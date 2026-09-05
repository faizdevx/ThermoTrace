from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from shapely.geometry import Point, box, mapping

INDIA_BOUNDS = {
    "min_lon": 68.0,
    "min_lat": 6.0,
    "max_lon": 98.0,
    "max_lat": 38.0,
}

SITE_ROWS: list[dict[str, Any]] = [
    {
        "site_id": "site-001",
        "name": "Mumbai Port Logistics Hub",
        "state": "Maharashtra",
        "district": "Mumbai",
        "industry_type": "logistics",
        "status": "operational",
        "address": "Mumbai Port Trust Area, Mumbai, Maharashtra",
        "latitude": 18.9433,
        "longitude": 72.8420,
        "osm_ids": [1001],
        "government_ids": ["MH-GOV-001"],
        "match_score": 0.97,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-03",
    },
    {
        "site_id": "site-002",
        "name": "Pune Auto Cluster",
        "state": "Maharashtra",
        "district": "Pune",
        "industry_type": "automotive",
        "status": "operational",
        "address": "Pimpri-Chinchwad, Pune, Maharashtra",
        "latitude": 18.6298,
        "longitude": 73.7997,
        "osm_ids": [1002],
        "government_ids": ["MH-GOV-002"],
        "match_score": 0.95,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-03",
    },
    {
        "site_id": "site-003",
        "name": "Nagpur Engineering Park",
        "state": "Maharashtra",
        "district": "Nagpur",
        "industry_type": "engineering",
        "status": "under_construction",
        "address": "Butibori, Nagpur, Maharashtra",
        "latitude": 21.1458,
        "longitude": 79.0882,
        "osm_ids": [1003],
        "government_ids": ["MH-GOV-003"],
        "match_score": 0.88,
        "match_confidence": "medium",
        "match_method": "name+industry",
        "review_required": True,
        "last_verified": "2026-08-24",
    },
    {
        "site_id": "site-004",
        "name": "Ahmedabad Textile Zone",
        "state": "Gujarat",
        "district": "Ahmedabad",
        "industry_type": "textiles",
        "status": "operational",
        "address": "Naroda GIDC, Ahmedabad, Gujarat",
        "latitude": 23.0225,
        "longitude": 72.5714,
        "osm_ids": [2001],
        "government_ids": ["GJ-GOV-001"],
        "match_score": 0.96,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-01",
    },
    {
        "site_id": "site-005",
        "name": "Surat Diamond Processing Cluster",
        "state": "Gujarat",
        "district": "Surat",
        "industry_type": "manufacturing",
        "status": "operational",
        "address": "Sachin GIDC, Surat, Gujarat",
        "latitude": 21.1702,
        "longitude": 72.8311,
        "osm_ids": [2002],
        "government_ids": ["GJ-GOV-002"],
        "match_score": 0.93,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-01",
    },
    {
        "site_id": "site-006",
        "name": "Vadodara Petrochemicals Estate",
        "state": "Gujarat",
        "district": "Vadodara",
        "industry_type": "petrochemicals",
        "status": "operational",
        "address": "Makarpura GIDC, Vadodara, Gujarat",
        "latitude": 22.3072,
        "longitude": 73.1812,
        "osm_ids": [2003],
        "government_ids": ["GJ-GOV-003"],
        "match_score": 0.91,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-08-28",
    },
    {
        "site_id": "site-007",
        "name": "Bengaluru Electronics Campus",
        "state": "Karnataka",
        "district": "Bengaluru Urban",
        "industry_type": "electronics",
        "status": "operational",
        "address": "Peenya Industrial Area, Bengaluru, Karnataka",
        "latitude": 12.9716,
        "longitude": 77.5946,
        "osm_ids": [3001],
        "government_ids": ["KA-GOV-001"],
        "match_score": 0.98,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-02",
    },
    {
        "site_id": "site-008",
        "name": "Mangaluru Refinery Belt",
        "state": "Karnataka",
        "district": "Dakshina Kannada",
        "industry_type": "energy",
        "status": "operational",
        "address": "Mangaluru Special Economic Zone, Karnataka",
        "latitude": 12.9141,
        "longitude": 74.8560,
        "osm_ids": [3002],
        "government_ids": ["KA-GOV-002"],
        "match_score": 0.89,
        "match_confidence": "medium",
        "match_method": "name+industry",
        "review_required": True,
        "last_verified": "2026-08-22",
    },
    {
        "site_id": "site-009",
        "name": "Chennai Heavy Engineering Node",
        "state": "Tamil Nadu",
        "district": "Chennai",
        "industry_type": "heavy_engineering",
        "status": "operational",
        "address": "Ambattur Industrial Estate, Chennai, Tamil Nadu",
        "latitude": 13.0827,
        "longitude": 80.2707,
        "osm_ids": [4001],
        "government_ids": ["TN-GOV-001"],
        "match_score": 0.97,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-01",
    },
    {
        "site_id": "site-010",
        "name": "Coimbatore Foundry Belt",
        "state": "Tamil Nadu",
        "district": "Coimbatore",
        "industry_type": "foundry",
        "status": "operational",
        "address": "Sidco Industrial Estate, Coimbatore, Tamil Nadu",
        "latitude": 11.0168,
        "longitude": 76.9558,
        "osm_ids": [4002],
        "government_ids": ["TN-GOV-002"],
        "match_score": 0.92,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-08-30",
    },
    {
        "site_id": "site-011",
        "name": "Hyderabad Pharma Valley",
        "state": "Telangana",
        "district": "Hyderabad",
        "industry_type": "pharmaceuticals",
        "status": "operational",
        "address": "Genome Valley, Hyderabad, Telangana",
        "latitude": 17.3850,
        "longitude": 78.4867,
        "osm_ids": [5001],
        "government_ids": ["TG-GOV-001"],
        "match_score": 0.98,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-02",
    },
    {
        "site_id": "site-012",
        "name": "Warangal Fabrication Hub",
        "state": "Telangana",
        "district": "Warangal",
        "industry_type": "fabrication",
        "status": "under_construction",
        "address": "Kazipet Industrial Area, Warangal, Telangana",
        "latitude": 17.9689,
        "longitude": 79.5941,
        "osm_ids": [5002],
        "government_ids": ["TG-GOV-002"],
        "match_score": 0.84,
        "match_confidence": "medium",
        "match_method": "name+industry",
        "review_required": True,
        "last_verified": "2026-08-19",
    },
    {
        "site_id": "site-013",
        "name": "Delhi Industrial Estate",
        "state": "Delhi",
        "district": "New Delhi",
        "industry_type": "mixed_manufacturing",
        "status": "operational",
        "address": "Mayapuri Industrial Area, Delhi",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "osm_ids": [6001],
        "government_ids": ["DL-GOV-001"],
        "match_score": 0.9,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-02",
    },
    {
        "site_id": "site-014",
        "name": "Gurugram Auto Suppliers Park",
        "state": "Haryana",
        "district": "Gurugram",
        "industry_type": "automotive",
        "status": "operational",
        "address": "IMT Manesar, Gurugram, Haryana",
        "latitude": 28.4595,
        "longitude": 77.0266,
        "osm_ids": [7001],
        "government_ids": ["HR-GOV-001"],
        "match_score": 0.95,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-03",
    },
    {
        "site_id": "site-015",
        "name": "Noida Electronics Corridor",
        "state": "Uttar Pradesh",
        "district": "Gautam Buddha Nagar",
        "industry_type": "electronics",
        "status": "operational",
        "address": "Sector 62, Noida, Uttar Pradesh",
        "latitude": 28.5355,
        "longitude": 77.3910,
        "osm_ids": [8001],
        "government_ids": ["UP-GOV-001"],
        "match_score": 0.93,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-03",
    },
    {
        "site_id": "site-016",
        "name": "Kolkata Chemicals Estate",
        "state": "West Bengal",
        "district": "Kolkata",
        "industry_type": "chemicals",
        "status": "operational",
        "address": "Bantala Industrial Belt, Kolkata, West Bengal",
        "latitude": 22.5726,
        "longitude": 88.3639,
        "osm_ids": [9001],
        "government_ids": ["WB-GOV-001"],
        "match_score": 0.88,
        "match_confidence": "medium",
        "match_method": "industry+state",
        "review_required": True,
        "last_verified": "2026-08-29",
    },
    {
        "site_id": "site-017",
        "name": "Howrah Fabrication Cluster",
        "state": "West Bengal",
        "district": "Howrah",
        "industry_type": "fabrication",
        "status": "operational",
        "address": "Uluberia Industrial Area, Howrah, West Bengal",
        "latitude": 22.5958,
        "longitude": 88.2636,
        "osm_ids": [9002],
        "government_ids": ["WB-GOV-002"],
        "match_score": 0.9,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-08-29",
    },
    {
        "site_id": "site-018",
        "name": "Bhubaneswar Industrial Park",
        "state": "Odisha",
        "district": "Khordha",
        "industry_type": "mixed_manufacturing",
        "status": "operational",
        "address": "Mancheswar Industrial Estate, Bhubaneswar, Odisha",
        "latitude": 20.2961,
        "longitude": 85.8245,
        "osm_ids": [10001],
        "government_ids": ["OD-GOV-001"],
        "match_score": 0.94,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-03",
    },
    {
        "site_id": "site-019",
        "name": "Rourkela Steel Belt",
        "state": "Odisha",
        "district": "Sundargarh",
        "industry_type": "steel",
        "status": "operational",
        "address": "Sector 7, Rourkela, Odisha",
        "latitude": 22.2604,
        "longitude": 84.8536,
        "osm_ids": [10002],
        "government_ids": ["OD-GOV-002"],
        "match_score": 0.96,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-03",
    },
    {
        "site_id": "site-020",
        "name": "Jaipur Auto Components",
        "state": "Rajasthan",
        "district": "Jaipur",
        "industry_type": "automotive",
        "status": "operational",
        "address": "Sitapura Industrial Area, Jaipur, Rajasthan",
        "latitude": 26.9124,
        "longitude": 75.7873,
        "osm_ids": [11001],
        "government_ids": ["RJ-GOV-001"],
        "match_score": 0.9,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-08-31",
    },
    {
        "site_id": "site-021",
        "name": "Indore Food Processing Zone",
        "state": "Madhya Pradesh",
        "district": "Indore",
        "industry_type": "food_processing",
        "status": "operational",
        "address": "Pithampur Industrial Area, Indore, Madhya Pradesh",
        "latitude": 22.7196,
        "longitude": 75.8577,
        "osm_ids": [12001],
        "government_ids": ["MP-GOV-001"],
        "match_score": 0.89,
        "match_confidence": "medium",
        "match_method": "name+industry",
        "review_required": True,
        "last_verified": "2026-08-27",
    },
    {
        "site_id": "site-022",
        "name": "Ludhiana Cycle Manufacturing Hub",
        "state": "Punjab",
        "district": "Ludhiana",
        "industry_type": "manufacturing",
        "status": "operational",
        "address": "Focal Point, Ludhiana, Punjab",
        "latitude": 30.9010,
        "longitude": 75.8573,
        "osm_ids": [13001],
        "government_ids": ["PB-GOV-001"],
        "match_score": 0.91,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-08-31",
    },
    {
        "site_id": "site-023",
        "name": "Kochi Marine Equipment Park",
        "state": "Kerala",
        "district": "Ernakulam",
        "industry_type": "marine",
        "status": "operational",
        "address": "Kakkanad, Kochi, Kerala",
        "latitude": 9.9312,
        "longitude": 76.2673,
        "osm_ids": [14001],
        "government_ids": ["KL-GOV-001"],
        "match_score": 0.92,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-02",
    },
    {
        "site_id": "site-024",
        "name": "Guwahati Agro Processing Unit",
        "state": "Assam",
        "district": "Kamrup Metropolitan",
        "industry_type": "food_processing",
        "status": "operational",
        "address": "Amingaon, Guwahati, Assam",
        "latitude": 26.1445,
        "longitude": 91.7362,
        "osm_ids": [15001],
        "government_ids": ["AS-GOV-001"],
        "match_score": 0.87,
        "match_confidence": "medium",
        "match_method": "industry+state",
        "review_required": True,
        "last_verified": "2026-08-25",
    },
    {
        "site_id": "site-025",
        "name": "Visakhapatnam Port Industrial Zone",
        "state": "Andhra Pradesh",
        "district": "Visakhapatnam",
        "industry_type": "logistics",
        "status": "operational",
        "address": "Gangavaram Port Area, Visakhapatnam, Andhra Pradesh",
        "latitude": 17.6868,
        "longitude": 83.2185,
        "osm_ids": [16001],
        "government_ids": ["AP-GOV-001"],
        "match_score": 0.94,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-02",
    },
    {
        "site_id": "site-026",
        "name": "Raipur Metal Works Estate",
        "state": "Chhattisgarh",
        "district": "Raipur",
        "industry_type": "metalworks",
        "status": "operational",
        "address": "Urla Industrial Area, Raipur, Chhattisgarh",
        "latitude": 21.2514,
        "longitude": 81.6296,
        "osm_ids": [17001],
        "government_ids": ["CG-GOV-001"],
        "match_score": 0.9,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-08-26",
    },
    {
        "site_id": "site-027",
        "name": "Ranchi Engineering Park",
        "state": "Jharkhand",
        "district": "Ranchi",
        "industry_type": "engineering",
        "status": "temporarily_closed",
        "address": "Tatisilwai Industrial Area, Ranchi, Jharkhand",
        "latitude": 23.3441,
        "longitude": 85.3096,
        "osm_ids": [18001],
        "government_ids": ["JH-GOV-001"],
        "match_score": 0.8,
        "match_confidence": "low",
        "match_method": "industry+state",
        "review_required": True,
        "last_verified": "2026-08-20",
    },
    {
        "site_id": "site-028",
        "name": "Patna Food Park",
        "state": "Bihar",
        "district": "Patna",
        "industry_type": "food_processing",
        "status": "operational",
        "address": "Bihta Industrial Area, Patna, Bihar",
        "latitude": 25.5941,
        "longitude": 85.1376,
        "osm_ids": [19001],
        "government_ids": ["BR-GOV-001"],
        "match_score": 0.85,
        "match_confidence": "medium",
        "match_method": "name+industry",
        "review_required": True,
        "last_verified": "2026-08-23",
    },
    {
        "site_id": "site-029",
        "name": "Panaji Renewable Works",
        "state": "Goa",
        "district": "North Goa",
        "industry_type": "renewables",
        "status": "operational",
        "address": "Verna Industrial Estate, Goa",
        "latitude": 15.4909,
        "longitude": 73.8278,
        "osm_ids": [20001],
        "government_ids": ["GA-GOV-001"],
        "match_score": 0.9,
        "match_confidence": "high",
        "match_method": "spatial+name",
        "review_required": False,
        "last_verified": "2026-09-01",
    },
    {
        "site_id": "site-030",
        "name": "New Delhi Cold Storage Belt",
        "state": "Delhi",
        "district": "New Delhi",
        "industry_type": "logistics",
        "status": "operational",
        "address": "Azadpur Industrial Area, Delhi",
        "latitude": 28.7041,
        "longitude": 77.1025,
        "osm_ids": [6002],
        "government_ids": ["DL-GOV-002"],
        "match_score": 0.86,
        "match_confidence": "medium",
        "match_method": "industry+state",
        "review_required": True,
        "last_verified": "2026-09-03",
    },
]


@dataclass(frozen=True)
class CatalogFilters:
    bbox: tuple[float, float, float, float] | None = None
    state: str | None = None
    district: str | None = None
    industry_type: str | None = None
    status: str | None = None
    confidence: str | None = None
    query: str | None = None


FILTERABLE_FIELDS = ("state", "district", "industry_type", "status", "match_confidence")


def _normalize(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _site_data_source(site: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    if site.get("osm_ids"):
        sources.append("openstreetmap")
    if site.get("government_ids"):
        sources.append("government")
    return sources


def _site_geometry(site: dict[str, Any]) -> dict[str, Any]:
    return mapping(Point(float(site["longitude"]), float(site["latitude"])))


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _site_properties(site: dict[str, Any]) -> dict[str, Any]:
    source_ids = [f"osm:{osm_id}" for osm_id in site.get("osm_ids", [])]
    source_ids.extend(f"government:{government_id}" for government_id in site.get("government_ids", []))
    return {
        **site,
        "normalized_name": _normalize(site.get("name")),
        "normalized_industry_type": _normalize(site.get("industry_type")),
        "source_ids": source_ids,
        "data_sources": _site_data_source(site),
        "source_count": len(_site_data_source(site)),
        "latitude": float(site["latitude"]),
        "longitude": float(site["longitude"]),
    }


def get_sites() -> list[dict[str, Any]]:
    return [{**row, **_site_properties(row)} for row in SITE_ROWS]


def _site_matches_bbox(site: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= float(site["longitude"]) <= max_lon and min_lat <= float(site["latitude"]) <= max_lat


def _site_matches_filters(site: dict[str, Any], filters: CatalogFilters) -> bool:
    if filters.state and _normalize(site.get("state")) != _normalize(filters.state):
        return False
    if filters.district and _normalize(site.get("district")) != _normalize(filters.district):
        return False
    if filters.industry_type and _normalize(site.get("industry_type")) != _normalize(filters.industry_type):
        return False
    if filters.status and _normalize(site.get("status")) != _normalize(filters.status):
        return False
    if filters.confidence and _normalize(site.get("match_confidence")) != _normalize(filters.confidence):
        return False
    if filters.query:
        haystack = " ".join(
            [
                _normalize(site.get("name")),
                _normalize(site.get("normalized_name")),
                _normalize(site.get("industry_type")),
                _normalize(site.get("state")),
                _normalize(site.get("district")),
                _normalize(site.get("address")),
            ]
        )
        if _normalize(filters.query) not in haystack:
            return False
    if filters.bbox and not _site_matches_bbox(site, filters.bbox):
        return False
    return True


def _to_bbox_tuple(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        west, south, east, north = [float(value) for value in bbox.split(",")]
    except (ValueError, TypeError):
        return None
    return (west, south, east, north)


def filter_sites(raw_filters: dict[str, str | None]) -> list[dict[str, Any]]:
    from backend.app.config import get_settings  # noqa: PLC0415
    if get_settings().data_mode == "postgis":
        from backend.app.services import postgis_repo  # noqa: PLC0415
        return postgis_repo.filter_sites(raw_filters)
    filters = CatalogFilters(
        bbox=_to_bbox_tuple(raw_filters.get("bbox")),
        state=raw_filters.get("state"),
        district=raw_filters.get("district"),
        industry_type=raw_filters.get("industry_type"),
        status=raw_filters.get("status"),
        confidence=raw_filters.get("confidence"),
        query=raw_filters.get("q"),
    )
    return [site for site in get_sites() if _site_matches_filters(site, filters)]


def search_sites(query: str, raw_filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    from backend.app.config import get_settings  # noqa: PLC0415
    if get_settings().data_mode == "postgis":
        from backend.app.services import postgis_repo  # noqa: PLC0415
        return postgis_repo.search_sites(query, raw_filters)
    filters = dict(raw_filters or {})
    filters["q"] = query
    return filter_sites(filters)


def get_site_by_id(site_id: str) -> dict[str, Any] | None:
    from backend.app.config import get_settings  # noqa: PLC0415
    if get_settings().data_mode == "postgis":
        from backend.app.services import postgis_repo  # noqa: PLC0415
        return postgis_repo.get_site_by_id(site_id)
    for site in get_sites():
        if site["site_id"] == site_id:
            return site
    return None


def _site_bounds(sites: list[dict[str, Any]], padding: float = 0.75) -> tuple[float, float, float, float]:
    min_lon = min(float(site["longitude"]) for site in sites) - padding
    min_lat = min(float(site["latitude"]) for site in sites) - padding
    max_lon = max(float(site["longitude"]) for site in sites) + padding
    max_lat = max(float(site["latitude"]) for site in sites) + padding
    return (min_lon, min_lat, max_lon, max_lat)


def _boundary_feature(level: str, name: str, identifier: str, bounds: tuple[float, float, float, float]) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bounds
    geometry = mapping(box(min_lon, min_lat, max_lon, max_lat))
    properties = {
        "boundary_level": level,
        "name": name,
        "source_id": identifier,
        "source": "synthetic",
    }
    return _feature(geometry, properties)


def get_boundary_collection(level: str) -> dict[str, Any]:
    sites = get_sites()
    if level == "india":
        feature = _boundary_feature("india", "India", "INDIA-SYNTHETIC", (67.5, 6.0, 98.5, 37.8))
        return {"type": "FeatureCollection", "features": [feature]}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for site in sites:
        key = site["state"] if level == "states" else f'{site["state"]}::{site["district"]}'
        grouped.setdefault(key, []).append(site)

    features: list[dict[str, Any]] = []
    for key, group in grouped.items():
        if level == "states":
            name = group[0]["state"]
            identifier = f"state:{_normalize(name).replace(' ', '_')}"
            bounds = _site_bounds(group, padding=1.0)
        else:
            name = group[0]["district"]
            identifier = f"district:{_normalize(group[0]['state']).replace(' ', '_')}::{_normalize(name).replace(' ', '_')}"
            bounds = _site_bounds(group, padding=0.45)
        features.append(_boundary_feature(level, name, identifier, bounds))
    return {"type": "FeatureCollection", "features": features}


def to_feature_collection(sites: list[dict[str, Any]]) -> dict[str, Any]:
    features = [_feature(_site_geometry(site), _site_properties(site)) for site in sites]
    return {"type": "FeatureCollection", "features": features}


def get_statistics() -> dict[str, Any]:
    from backend.app.config import get_settings  # noqa: PLC0415
    if get_settings().data_mode == "postgis":
        from backend.app.services import postgis_repo  # noqa: PLC0415
        return postgis_repo.get_statistics()
    sites = get_sites()
    total_sites = len(sites)
    by_state = Counter(site["state"] for site in sites)
    by_industry_type = Counter(site["industry_type"] for site in sites)
    by_status = Counter(site["status"] for site in sites)
    by_confidence = Counter(site["match_confidence"] for site in sites)

    osm_records = sum(1 for site in sites if site.get("osm_ids"))
    government_records = sum(1 for site in sites if site.get("government_ids"))
    matched_records = sum(1 for site in sites if site.get("source_count", 0) > 1)
    unmatched_records = total_sites - matched_records
    high_confidence_matches = sum(1 for site in sites if site.get("match_confidence") == "high")

    return {
        "total_sites": total_sites,
        "by_state": dict(sorted(by_state.items())),
        "by_industry_type": dict(sorted(by_industry_type.items())),
        "by_status": dict(sorted(by_status.items())),
        "osm_records": osm_records,
        "government_records": government_records,
        "matched_records": matched_records,
        "unmatched_records": unmatched_records,
        "high_confidence_matches": high_confidence_matches,
        "confidence_distribution": dict(sorted(by_confidence.items())),
    }


def get_filter_options() -> dict[str, list[str]]:
    from backend.app.config import get_settings  # noqa: PLC0415
    if get_settings().data_mode == "postgis":
        from backend.app.services import postgis_repo  # noqa: PLC0415
        return postgis_repo.get_filter_options()
    sites = get_sites()
    return {
        "states": sorted({site["state"] for site in sites}),
        "districts": sorted({site["district"] for site in sites}),
        "industry_types": sorted({site["industry_type"] for site in sites}),
        "statuses": sorted({site["status"] for site in sites}),
        "confidence_levels": sorted({site["match_confidence"] for site in sites}),
    }


def get_site_feature(site_id: str) -> dict[str, Any] | None:
    site = get_site_by_id(site_id)
    if site is None:
        return None
    return _feature(_site_geometry(site), _site_properties(site))
