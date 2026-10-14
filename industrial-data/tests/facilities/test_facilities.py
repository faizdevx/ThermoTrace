"""Facility merge: provenance kept, 500 m rule, kind compatibility, loaders on small fixtures."""
from __future__ import annotations

import json

import pandas as pd

from src.facilities.merge import merge
from src.facilities.sources import COLUMNS, load_gem, load_osm, load_vnf, load_wri_gppd
from src.facilities.store import to_geojson


def _rec(source, sid, kind, lat, lon, name=None, cap=None):
    return {"source": source, "source_id": sid, "name": name, "kind": kind, "lat": lat, "lon": lon,
            "radius_m": 1500.0, "capacity_mw": cap, "status": None, "url": None}


def _df(*rows):
    return pd.DataFrame(list(rows), columns=COLUMNS)


def test_same_plant_in_three_sources_becomes_one_facility_with_all_ids():
    fac, links = merge([
        _df(_rec("osm", "way/1", "power_thermal", 21.2000, 83.4000, "Jindal TPS")),
        _df(_rec("wri_gppd", "IND0000123", "power_thermal", 21.2020, 83.4010, "Jindal Power", 1000.0)),
        _df(_rec("gem", "coal_plant:G1", "power_thermal", 21.2010, 83.4005, "Jindal Power Ltd", 1200.0)),
    ])
    assert len(fac) == 1 and len(links) == 3
    row = fac.iloc[0]
    assert row.source_count == 3 and row.primary_source == "gem"  # authoritative source wins
    assert row["name"] == "Jindal Power Ltd" and row.capacity_mw == 1200.0
    assert set(links.source) == {"osm", "wri_gppd", "gem"}
    assert links.licence.notna().all() and links.attribution.notna().all()


def test_beyond_500m_or_different_kind_stays_separate():
    fac, _ = merge([_df(
        _rec("osm", "n/1", "power_thermal", 21.0, 83.0),
        _rec("wri_gppd", "W1", "power_thermal", 21.0, 83.0060),      # ~620 m east
        _rec("osm", "n/2", "refinery_petrochem", 21.0, 83.0),        # same place, incompatible kind
    )])
    assert len(fac) == 3


def test_generic_factory_is_absorbed_by_specific_kind_and_does_not_chain():
    fac, links = merge([_df(
        _rec("osm", "n/1", "factory_generic", 20.0, 80.0, "Works"),
        _rec("gem", "steel:S1", "steel_metal", 20.0, 80.0030, "Steel Plant"),
        _rec("osm", "n/3", "factory_generic", 20.0, 80.0100),        # ~730 m from the steel plant: not absorbed
    )])
    assert len(fac) == 2
    steel = fac[fac.kind == "steel_metal"].iloc[0]
    assert steel.source_count == 2 and steel["name"] == "Steel Plant"


def test_merge_is_deterministic():
    frames = [_df(*[_rec("osm", f"n/{i}", "cement", 20 + i * 0.001, 80.0) for i in range(5)])]
    a, _ = merge(frames)
    b, _ = merge([frames[0].sample(frac=1, random_state=3)])
    assert sorted(a.facility_id) == sorted(b.facility_id)


def test_load_wri_keeps_indian_thermal_only(tmp_path):
    p = tmp_path / "gppd.csv"
    pd.DataFrame({
        "country_long": ["India", "India", "India", "Nepal"], "gppd_idnr": ["I1", "I2", "I3", "N1"],
        "name": ["Coal A", "Solar B", "Gas C", "Coal D"], "capacity_mw": [500, 50, 300, 100],
        "latitude": [21.0, 22.0, 23.0, 28.0], "longitude": [83.0, 77.0, 72.0, 84.0],
        "primary_fuel": ["Coal", "Solar", "Gas", "Coal"],
    }).to_csv(p, index=False)
    out = load_wri_gppd(p)
    assert sorted(out.source_id) == ["I1", "I3"] and set(out.kind) == {"power_thermal"}


def test_load_gem_filters_country_and_cancelled(tmp_path):
    p = tmp_path / "gem.csv"
    pd.DataFrame({
        "Plant name": ["A", "B", "C", "D"], "Country": ["India", "India", "India", "China"],
        "Status": ["operating", "cancelled", "construction", "operating"],
        "Latitude": [21.0, 22.0, 23.0, 30.0], "Longitude": [83.0, 77.0, 72.0, 110.0],
        "Capacity (MW)": [660, 800, 500, 600], "GEM unit ID": ["U1", "U2", "U3", "U4"],
    }).to_csv(p, index=False)
    out = load_gem(p, "coal_plant")
    assert sorted(out.source_id) == ["coal_plant:U1", "coal_plant:U3"]
    assert out.capacity_mw.max() == 660


def test_load_vnf_needs_persistent_hot_nights(tmp_path):
    rows = [{"Lat_GMTCO": 21.1, "Lon_GMTCO": 84.1, "Temp_BB": 1700, "Date_Mscan": f"2025-01-{d:02d}"} for d in range(1, 8)]
    rows += [{"Lat_GMTCO": 22.5, "Lon_GMTCO": 80.5, "Temp_BB": 1700, "Date_Mscan": "2025-01-01"}]   # one night only
    rows += [{"Lat_GMTCO": 23.5, "Lon_GMTCO": 81.5, "Temp_BB": 900, "Date_Mscan": f"2025-01-{d:02d}"} for d in range(1, 8)]  # cool
    p = tmp_path / "vnf.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    out = load_vnf(p, min_days=5)
    assert len(out) == 1 and out.kind.iloc[0] == "gas_flare" and abs(out.lat.iloc[0] - 21.1) < 0.01


def test_load_osm_and_geojson_roundtrip_feeds_site_index(tmp_path):
    src = tmp_path / "sites.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [83.4, 21.2]},
         "properties": {"source_ref": "way/9", "name": "Cement Works", "tags": {"industrial": "cement"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.0, 12.0]},
         "properties": {"source_ref": "node/1", "name": "Bakery", "tags": {"shop": "bakery"}}}]}))
    osm = load_osm(src)
    assert list(osm.source_id) == ["way/9"] and osm.kind.iloc[0] == "cement"
    fac, _ = merge([osm])
    out = tmp_path / "facilities.geojson"
    out.write_text(json.dumps(to_geojson(fac)))
    from src.thermal.sites import SiteIndex
    idx = SiteIndex.from_geojson(out)
    assert len(idx) == 1 and idx.sites.kind.iloc[0] == "cement"
