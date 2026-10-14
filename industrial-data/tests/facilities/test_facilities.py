"""Facility merge: provenance kept, 500 m rule, kind compatibility, capacity rules, footprints, loaders."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from src.facilities.merge import merge
from src.facilities.sources import COLUMNS, load_gem, load_osm, load_vnf, load_wri_gppd
from src.facilities.store import footprints_geojson, to_geojson, write_outputs

SQUARE_1KM = "POLYGON ((83 21, 83.0096 21, 83.0096 21.009, 83 21.009, 83 21))"   # ~1 km x 1 km


def _rec(source, sid, kind, lat, lon, name=None, cap=None, footprint=None, radius=1500.0):
    return {"source": source, "source_id": sid, "name": name, "kind": kind, "lat": lat, "lon": lon,
            "radius_m": radius, "capacity_mw": cap, "status": None, "fuel": None, "url": None, "footprint": footprint}


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
    assert row["name"] == "Jindal Power Ltd"
    assert row.capacity_mw == 1200.0  # largest source total, never summed across sources
    assert set(links.source) == {"osm", "wri_gppd", "gem"}
    assert links.licence.notna().all() and links.attribution.notna().all()
    assert links.set_index("source").loc["gem", "distance_m"] == 0.0


def test_capacity_is_summed_within_one_source():
    fac, _ = merge([_df(
        _rec("wri_gppd", "W1", "power_thermal", 21.0, 83.0, cap=600.0),
        _rec("wri_gppd", "W2", "power_thermal", 21.0, 83.001, cap=500.0),   # second listing ~100 m away
        _rec("gem", "coal_plant:G1", "power_thermal", 21.0, 83.0005, cap=1000.0),
    )])
    assert len(fac) == 1 and fac.iloc[0].capacity_mw == 1100.0


def test_beyond_500m_or_different_kind_stays_separate():
    fac, _ = merge([_df(
        _rec("osm", "n/1", "power_thermal", 21.0, 83.0),
        _rec("wri_gppd", "W1", "power_thermal", 21.0, 83.0060),      # ~620 m east
        _rec("osm", "n/2", "refinery_petrochem", 21.0, 83.0),        # same place, incompatible kind
    )])
    assert len(fac) == 3


def test_generic_factory_is_absorbed_by_specific_kind_and_does_not_chain():
    fac, _ = merge([_df(
        _rec("osm", "n/1", "factory_generic", 20.0, 80.0, "Works"),
        _rec("gem", "steel:S1", "steel_metal", 20.0, 80.0030, "Steel Plant"),
        _rec("osm", "n/3", "factory_generic", 20.0, 80.0100),        # ~730 m from the steel plant: not absorbed
    )])
    assert len(fac) == 2
    steel = fac[fac.kind == "steel_metal"].iloc[0]
    assert steel.source_count == 2 and steel["name"] == "Steel Plant"


def test_merge_is_deterministic():
    rows = [_rec("osm", f"n/{i}", "cement", 20 + i * 0.001, 80.0) for i in range(5)]
    a, _ = merge([_df(*rows)])
    b, _ = merge([_df(*rows).sample(frac=1, random_state=3)])
    assert sorted(a.facility_id) == sorted(b.facility_id)


def test_footprint_is_kept_and_largest_wins():
    small = "POLYGON ((83 21, 83.001 21, 83.001 21.001, 83 21.001, 83 21))"
    fac, _ = merge([_df(
        _rec("wri_gppd", "W1", "power_thermal", 21.004, 83.004),
        _rec("osm", "way/1", "power_thermal", 21.004, 83.004, footprint=SQUARE_1KM, radius=2500.0),
        _rec("osm", "way/2", "power_thermal", 21.0045, 83.0045, footprint=small),
    )])
    assert len(fac) == 1
    row = fac.iloc[0]
    assert row.footprint == SQUARE_1KM and row.radius_m == 2500.0
    assert len(footprints_geojson(fac)["features"]) == 1


def test_empty_input_returns_typed_empty_tables():
    fac, links = merge([])
    assert fac.empty and "facility_id" in fac.columns and "licence" in links.columns


def test_load_wri_keeps_indian_thermal_only_and_puts_fuel_in_fuel(tmp_path):
    p = tmp_path / "gppd.csv"
    pd.DataFrame({
        "country_long": ["India", "India", "India", "Nepal"], "gppd_idnr": ["I1", "I2", "I3", "N1"],
        "name": ["Coal A", "Solar B", "Gas C", "Coal D"], "capacity_mw": [500, 50, 300, 100],
        "latitude": [21.0, 22.0, 23.0, 28.0], "longitude": [83.0, 77.0, 72.0, 84.0],
        "primary_fuel": ["Coal", "Solar", "Gas", "Coal"],
    }).to_csv(p, index=False)
    out = load_wri_gppd(p).set_index("source_id")
    assert sorted(out.index) == ["I1", "I3"] and set(out.kind) == {"power_thermal"}
    assert out.loc["I1", "fuel"] == "Coal" and out.status.isna().all()


def test_load_gem_keeps_built_units_and_sums_unit_capacity(tmp_path):
    p = tmp_path / "gem.csv"
    pd.DataFrame({
        "Plant name": ["A", "A", "A", "B", "C", "D", "E"],
        "Country/Area": ["India", "India", "India", "India", "India", "China", "India"],
        "Status": ["operating", "operating", "construction", "cancelled", "announced", "operating", "mothballed"],
        "Latitude": [21.0, 21.0, 21.0, 22.0, 23.0, 30.0, 24.0], "Longitude": [83.0, 83.0, 83.0, 77.0, 72.0, 110.0, 81.0],
        "Capacity (MW)": [660, 660, 800, 800, 500, 600, 250],
        "GEM location ID": ["L1", "L1", "L1", "L2", "L3", "L4", "L5"],
    }).to_csv(p, index=False)
    out = load_gem(p, "coal_plant").set_index("source_id")
    assert sorted(out.index) == ["coal_plant:L1", "coal_plant:L5"]   # cancelled, announced, China dropped
    assert out.loc["coal_plant:L1", "capacity_mw"] == 2120             # 3 units of one plant summed
    assert out.loc["coal_plant:L1", "status"] == "operating"          # most advanced unit
    assert out.loc["coal_plant:L5", "status"] == "mothballed"


def test_load_gem_rejects_unknown_tracker(tmp_path):
    with pytest.raises(ValueError):
        load_gem(tmp_path / "x.csv", "nuclear")


def test_load_vnf_needs_persistent_hot_nights(tmp_path):
    rows = [{"Lat_GMTCO": 21.1, "Lon_GMTCO": 84.1, "Temp_BB": 1700, "Date_Mscan": f"2025-01-{d:02d}"} for d in range(1, 8)]
    rows += [{"Lat_GMTCO": 22.5, "Lon_GMTCO": 80.5, "Temp_BB": 1700, "Date_Mscan": "2025-01-01"}]   # one night only
    rows += [{"Lat_GMTCO": 23.5, "Lon_GMTCO": 81.5, "Temp_BB": 900, "Date_Mscan": f"2025-01-{d:02d}"} for d in range(1, 8)]
    p = tmp_path / "vnf.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    out = load_vnf(p, min_days=5)
    assert len(out) == 1 and out.kind.iloc[0] == "gas_flare" and abs(out.lat.iloc[0] - 21.1) < 0.01


def _osm_cache(tmp_path) -> Path:
    src = tmp_path / "sites.geojson"
    square = {"type": "Polygon", "coordinates": [[[83, 21], [83.05, 21], [83.05, 21.045], [83, 21.045], [83, 21]]]}
    src.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [83.025, 21.0225]},
         "properties": {"source_ref": "way/9", "name": "Big Power Station", "tags": {"power": "plant", "plant:source": "coal"},
                        "footprint": square}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [85.0, 23.0]},
         "properties": {"source_ref": "way/10", "name": "Quarry", "tags": {"landuse": "quarry"},
                        "bounds": {"minlat": 22.98, "minlon": 84.98, "maxlat": 23.02, "maxlon": 85.02}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [83.4, 21.2]},
         "properties": {"source_ref": "node/11", "name": "Cement Works", "tags": {"industrial": "cement"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.0, 12.0]},
         "properties": {"source_ref": "node/1", "name": "Bakery", "tags": {"shop": "bakery"}}}]}))
    return src


def test_load_osm_uses_footprint_then_bounds_then_kind_radius(tmp_path):
    osm = load_osm(_osm_cache(tmp_path)).set_index("source_id")
    assert sorted(osm.index) == ["node/11", "way/10", "way/9"]               # bakery is not thermal
    assert osm.loc["way/9", "footprint"].startswith("POLYGON")
    assert 2700 < osm.loc["way/9", "radius_m"] < 3000                         # ~5.2 x 5.0 km -> r ~ 2.9 km
    assert 2300 < osm.loc["way/10", "radius_m"] < 2600                        # bounds ~4.1 x 4.4 km
    assert osm.loc["node/11", "radius_m"] == 1200                             # cement default
    assert osm.loc["way/9", "url"] == "https://www.openstreetmap.org/way/9"


def test_outputs_roundtrip_feed_site_index(tmp_path):
    fac, links = merge([load_osm(_osm_cache(tmp_path))])
    paths = write_outputs(tmp_path / "out", fac, links)
    gj = json.loads(paths["geojson"].read_text())
    props = {f["properties"]["name"]: f["properties"] for f in gj["features"]}
    assert props["Big Power Station"]["has_footprint"] is True and "footprint" not in props["Big Power Station"]
    assert len(json.loads(paths["footprints"].read_text())["features"]) == 1
    assert len(pd.read_csv(paths["sources"])) == 3
    from src.thermal.sites import SiteIndex
    idx = SiteIndex.from_geojson(paths["geojson"])
    assert len(idx) == 3 and idx.sites.radius_m.max() > 2700


def test_to_geojson_is_strict_json():
    fac, _ = merge([_df(_rec("osm", "n/1", "cement", 20.0, 80.0))])
    json.dumps(to_geojson(fac), allow_nan=False)   # NaN capacity must become null


def _fetch_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "fetch_thermal_sites.py"
    spec = importlib.util.spec_from_file_location("fetch_thermal_sites", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_overpass_way_and_relation_become_polygons():
    mod = _fetch_module()
    ring = [{"lat": 21.0, "lon": 83.0}, {"lat": 21.0, "lon": 83.01}, {"lat": 21.01, "lon": 83.01},
            {"lat": 21.01, "lon": 83.0}, {"lat": 21.0, "lon": 83.0}]
    way = {"type": "way", "id": 1, "geometry": ring}
    rel = {"type": "relation", "id": 2, "members": [
        {"type": "way", "role": "outer", "geometry": ring[:3]},
        {"type": "way", "role": "outer", "geometry": ring[2:]}]}
    open_way = {"type": "way", "id": 3, "geometry": ring[:3]}
    polys = mod.footprints({"elements": [way, rel, open_way]})
    assert set(polys) == {"way/1", "relation/2"} and polys["way/1"]["type"] == "Polygon"
    gj = mod.to_geojson({"elements": [{"type": "way", "id": 1, "center": {"lat": 21.005, "lon": 83.005},
                                       "bounds": {"minlat": 21, "minlon": 83, "maxlat": 21.01, "maxlon": 83.01},
                                       "tags": {"power": "plant"}}]}, polys)
    p = gj["features"][0]["properties"]
    assert "footprint" in p and "bounds" in p and gj["metadata"]["footprints"] == 1


def test_gem_coordinates_column_and_gas_plant_kind(tmp_path):
    p = tmp_path / "steel.csv"
    pd.DataFrame({"Plant name (English)": ["S1", "S2"], "Country/Area": ["India", "India"],
                  "Status": ["operating", "construction"], "Coordinates": ["21.12, 83.45", "22.5, 86.1"],
                  "GEM Plant ID": ["P1", "P2"]}).to_csv(p, index=False)
    out = load_gem(p, "steel").set_index("source_id")
    assert sorted(out.index) == ["steel:P1", "steel:P2"] and out.loc["steel:P1", "lat"] == 21.12
    assert out.loc["steel:P1", "name"] == "S1" and set(out.kind) == {"steel_metal"}
    from src.facilities.sources import GEM_TRACKERS
    assert GEM_TRACKERS["gas_plant"] == "power_thermal"   # GOGPT lists power plants, not refineries


def test_discover_raw_recognises_source_files(tmp_path):
    from src.facilities.sources import discover_raw
    for name in ("wri/global_power_plant_database.csv", "Global-Coal-Plant-Tracker-July-2026.xlsx",
                 "Global-Iron-and-Steel-Tracker-2026.xlsx", "Global-Oil-and-Gas-Plant-Tracker-GOGPT.xlsx",
                 "Global-Coal-Mine-Tracker.xlsx", "Global-Cement-and-Concrete-Tracker.xlsx",
                 "vnf/VNF_npp_2025_global.csv", "notes.csv", "readme.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x")
    found = discover_raw(tmp_path)
    assert found["wri"].name == "global_power_plant_database.csv"
    assert sorted(t for _, t in found["gem"]) == ["cement", "coal_mine", "coal_plant", "gas_plant", "steel"]
    assert [p.name for p in found["vnf"]] == ["VNF_npp_2025_global.csv"]
    assert [p.name for p in found["unrecognised"]] == ["notes.csv"]


def test_merged_radius_covers_every_member_circle():
    fac, links = merge([_df(
        _rec("wri_gppd", "W1", "power_thermal", 21.0, 83.0),
        _rec("osm", "way/1", "power_thermal", 21.0, 83.004, radius=2000.0),   # ~415 m east, larger outline
    )])
    d = links.set_index("source").loc["osm", "distance_m"]
    assert len(fac) == 1 and abs(fac.iloc[0].radius_m - (2000.0 + d)) < 1e-6
