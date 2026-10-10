"""Curated locations for the documented incidents (input: india_industrial_fire_incidents_40.xlsx).

Rule: a point is only called ``facility`` when it is a NAMED OpenStreetMap object of the incident's own
facility (refinery / plant polygon, ref kept for audit). Industrial estates are ``industrial_area``
(centroid + radius = estate extent, never a facility point). Anything else stays ``locality``/``unlocated``.
Nothing here was taken from memory: every ``osm_ref`` can be opened at openstreetmap.org/<ref>.
Radii are the generous extent of the named complex, e.g. Jamnagar is a ~10 km wide site.
"""
from __future__ import annotations

# incident_id -> (osm_ref, lat, lon, radius_km, quality, rationale)
CURATED: dict[str, tuple[str, float, float, float, str, str]] = {
    "IN-002": ("way/166334648", 20.2487, 86.5995, 3.0, "facility", "IOCL Paradip Refinery tank farm (named OSM refinery object); AVU is inside the complex"),
    "IN-004": ("way/91585872", 22.3368, 69.8666, 5.0, "facility", "Reliance Refinery, Jamnagar (named OSM refinery object); site extent ~10 km"),
    "IN-005": ("way/259618943", 29.4525, 76.8689, 3.5, "facility", "Panipat Refinery complex (named OSM refinery objects); the Naphtha Cracker sits inside the same complex"),
    "IN-008": ("way/66665052", 25.4314, 86.0601, 2.5, "facility", "Barauni Oil Refinery (named OSM refinery object)"),
    "IN-011": ("way/259618943", 29.4525, 76.8689, 3.5, "facility", "Panipat Refinery (named OSM refinery objects)"),
    "IN-013": ("way/335591575", 22.0429, 88.1050, 2.5, "facility", "IOCL Haldia Refinery (named OSM refinery object); DYIP is a construction site inside the refinery area"),
    "IN-016": ("way/546118631", 27.3811, 77.6862, 2.5, "facility", "Mathura Refinery (named OSM refinery object)"),
    "IN-017": ("way/102565505", 13.1741, 80.2749, 2.0, "facility", "CPCL Refinery, Manali (named OSM refinery object)"),
    "IN-026": ("way/335591575", 22.0429, 88.1050, 2.5, "facility", "IOCL Haldia Refinery (named OSM refinery object)"),
    "IN-031": ("way/69366299", 26.5824, 93.7872, 2.5, "facility", "Numaligarh Refinery (named OSM refinery object)"),
    "IN-034": ("way/66665052", 25.4314, 86.0601, 2.5, "facility", "Barauni Oil Refinery (named OSM refinery object); ETP is inside the complex"),
    # industrial estates (Nominatim result is the estate itself)
    "IN-007": ("auto", None, None, 3.0, "industrial_area", "Malur Industrial Area"),
    "IN-012": ("auto", None, None, 1.5, "industrial_area", "Naraina Industrial Area"),
    "IN-020": ("auto", None, None, 2.5, "industrial_area", "Panchkula Industrial Area Phase 2"),
    "IN-024": ("auto", None, None, 3.0, "industrial_area", "Vatva GIDC"),
}

# Nominatim picked unrelated objects (port, hospital, other plant) for these: never use the auto point.
REJECT_AUTO = {"IN-004", "IN-008", "IN-010", "IN-013", "IN-015", "IN-017", "IN-021", "IN-026", "IN-032", "IN-034", "IN-037"}
