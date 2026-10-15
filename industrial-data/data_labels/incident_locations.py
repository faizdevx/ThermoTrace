"""Curated, auditable locations for the documented incidents (input: india_industrial_fire_incidents_40.xlsx).

Tiers (the label manifest and the evaluation report keep them apart):
  facility         the incident's own plant as a named OpenStreetMap object (ref kept)
  industrial_area  an OSM industrial estate polygon / named estate place node / the spine road of a named estate
  locality         the neighbourhood, village or town the SOURCE names, as an OSM place node (uncertainty = its extent)

Nothing here was taken from memory: every ``osm_ref`` opens at openstreetmap.org/<ref>, and the same names were
matched in the source text (data_labels/evidence_index.json). A city centroid is never used as a facility point.
Incidents without an entry here are NOT labelled, whatever the source says about the event.
"""
from __future__ import annotations

# incident_id -> (osm_ref, lat, lon, radius_km, tier, basis)
LOCATIONS: dict[str, tuple[str, float, float, float, str, str]] = {
    # --- facility tier -------------------------------------------------------------------------------------------
    "IN-002": ("way/166334648", 20.2487, 86.5995, 3.0, "facility", "IOCL Paradip Refinery tank farm; AVU is inside the complex"),
    "IN-004": ("way/91585872", 22.3368, 69.8666, 5.0, "facility", "Reliance Refinery, Jamnagar; site extent ~10 km"),
    "IN-005": ("way/259618943", 29.4525, 76.8689, 3.5, "facility", "Panipat Refinery complex; the Naphtha Cracker sits inside it"),
    "IN-008": ("way/66665052", 25.4314, 86.0601, 2.5, "facility", "Barauni Oil Refinery"),
    "IN-011": ("way/259618943", 29.4525, 76.8689, 3.5, "facility", "Panipat Refinery"),
    "IN-013": ("way/335591575", 22.0429, 88.1050, 2.5, "facility", "IOCL Haldia Refinery; DYIP is a construction site inside it"),
    "IN-016": ("way/546118631", 27.3811, 77.6862, 2.5, "facility", "Mathura Refinery"),
    "IN-017": ("way/102565505", 13.1741, 80.2749, 2.0, "facility", "CPCL Refinery, Manali"),
    "IN-026": ("way/335591575", 22.0429, 88.1050, 2.5, "facility", "IOCL Haldia Refinery"),
    "IN-031": ("way/69366299", 26.5824, 93.7872, 2.5, "facility", "Numaligarh Refinery"),
    "IN-034": ("way/66665052", 25.4314, 86.0601, 2.5, "facility", "Barauni Oil Refinery; the ETP is inside the complex"),
    # --- industrial-area tier ------------------------------------------------------------------------------------
    "IN-007": ("way/1076391762", 12.9799, 77.9184, 3.0, "industrial_area", "Malur Industrial Area (OSM polygon)"),
    "IN-012": ("relation/3691014", 28.6321, 77.1371, 1.5, "industrial_area", "Naraina Industrial Area I (OSM relation)"),
    "IN-020": ("way/129884960", 30.6808, 76.8335, 2.5, "industrial_area", "Industrial Area Phase 2, Panchkula (OSM polygon)"),
    "IN-024": ("way/321003779", 22.9557, 72.6367, 3.0, "industrial_area", "Vatva GIDC (OSM polygons, several ways)"),
    "IN-019": ("way/143289510", 21.7058, 72.5994, 2.0, "industrial_area",
               "Dahej Special Economic Zone polygon (r 1.1 km) widened to 2 km: the incident is in SEZ-II next to it"),
    "IN-032": ("way/138964954", 21.0927, 72.8581, 2.3, "industrial_area", "GIDC Sachin industrial estate (OSM polygon)"),
    "IN-027": ("node/1634299166", 28.8739, 77.1320, 3.0, "industrial_area", "'Kundli Industrial Area' (OSM place node; no polygon)"),
    "IN-006": ("node/13986672416", 19.7960, 72.7376, 3.0, "industrial_area", "'MIDC Salwad, Boisar' (OSM place node) in the Boisar-Tarapur MIDC"),
    "IN-003": ("way/198716183", 19.2080, 73.1011, 2.5, "industrial_area",
               "Dombivli MIDC: centroid of the OSM 'MIDC Road' ways way/198716183, 1554384210, 213708444, 236060706, 729322234"),
    "IN-036": ("way/198716183", 19.2080, 73.1011, 2.5, "industrial_area", "Dombivli MIDC Phase 2 (same MIDC Road ways as IN-003)"),
    "IN-037": ("way/198716183", 19.2080, 73.1011, 2.5, "industrial_area", "Dombivli MIDC (same MIDC Road ways as IN-003)"),
    # --- locality tier (named in the source; OSM place node, uncertainty = place extent) -----------------------------
    "IN-001": ("node/5595968690", 17.3034, 82.5976, 3.0, "locality", "Rajavaram, Payakaraopeta mandal (village node)"),
    "IN-009": ("node/6658260818", 28.9166, 77.6478, 3.0, "locality", "Partapur, Meerut (suburb node)"),
    "IN-010": ("node/4963731104", 26.4659, 80.2456, 3.0, "locality", "Panki, Kanpur (suburb node; the source names Panki Industrial Area)"),
    "IN-014": ("node/11849811732", 28.6583, 77.2034, 1.0, "locality", "Filmistan, Rani Jhansi Road, Anaj Mandi area (OSM node)"),
    "IN-015": ("node/9544997460", 28.6791, 77.0960, 1.0, "locality", "Peeragarhi Chowk, West Delhi (OSM node)"),
    "IN-021": ("node/1207129648", 21.0956, 72.6466, 3.0, "locality", "Hazira, Surat (village node); ONGC plant itself is not mapped in OSM"),
    "IN-023": ("node/733519845", 12.9557, 77.5402, 1.6, "locality", "Bapuji Nagar, Mysuru Road, Bengaluru (quarter node)"),
    "IN-028": ("node/7284417624", 28.6343, 77.6490, 3.0, "locality", "Dhaulana, Hapur (town node; the source names Dhaulana industrial area)"),
    "IN-029": ("node/3225070526", 19.7724, 73.6629, 3.0, "locality", "Mundegaon, Igatpuri (village node); the source names the Jindal Poly Films plant there"),
    "IN-038": ("node/7944298565", 26.4352, 80.3228, 3.0, "locality", "Rania, Kanpur Dehat (station node)"),
}

# Incidents whose source confirms the event but whose place cannot be resolved to a point with the tools available
# (kept out of the label set; listed so the gap is visible).
UNRESOLVED: dict[str, str] = {
    "IN-018": "Narol-Pirana Road is ~5 km long; no OSM object for Nandan Denim found",
    "IN-022": "PNGRB register entry names no site (IOCL Auto LPG station)",
    "IN-025": "PNGRB register entry names no site (BPCL Auto LPG station)",
    "IN-030": "source says Tirupati district; the plant's village is not named",
    "IN-033": "Jhadmajri (Baddi belt) not found in OSM by name",
    "IN-035": "Pawane MIDC not found in OSM by name",
    "IN-039": "source (Reuters) not retrievable: bot wall",
    "IN-040": "source (Reuters) not retrievable: bot wall",
}

# --- compatibility with scripts/match_incidents.py ---------------------------------------------------------------
CURATED = {k: (ref, lat, lon, rad, tier, why) for k, (ref, lat, lon, rad, tier, why) in LOCATIONS.items()}
REJECT_AUTO = {"IN-004", "IN-008", "IN-010", "IN-013", "IN-015", "IN-017", "IN-021", "IN-026", "IN-032", "IN-034", "IN-037"}
