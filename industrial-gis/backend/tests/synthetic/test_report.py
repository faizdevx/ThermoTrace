"""PDF incident report: builds with and without basemap tiles and imagery, escapes text, and never invents a map."""
from __future__ import annotations

import base64
import io

from PIL import Image

from backend.app.services import report

ALERT = {"alert_id": 7, "severity": "high", "is_test": False, "summary": "HIGH: possible industrial fire at Plant <A> & Co (Gujarāt)",
         "site_name": "Plant <A> & Co", "site_kind": "steel_plant", "state": "Gujarāt", "lat": 22.9, "lon": 72.6, "first_seen": "2026-10-16T01:00:00+00:00",
         "last_seen": "2026-10-16T03:00:00+00:00", "peak_frp": 61.5, "peak_at": "2026-10-16T02:00:00+00:00", "trigger": "both", "p_fire": 0.9, "frp_z": 4.2,
         "status": "confirmed", "reasons": ["on a mapped steel plant", "power <script> far above baseline"],
         "timeline": [{"at": "2026-10-16T01:00:00+00:00", "kind": "first_detection", "text": "First detection of this source"}],
         "decisions": [{"decision": "confirmed", "note": "smoke seen", "analyst": "asha", "decided_at": "2026-10-16T05:00:00+00:00"}]}


def _png(color=(120, 160, 120)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(b, "PNG")
    return b.getvalue()


def _uri() -> str:
    return "data:image/png;base64," + base64.b64encode(_png((90, 60, 40))).decode()


IMAGERY = {"available": True, "scene": {"id": "S2B_x", "datetime": "2026-10-16T05:00:00+00:00", "cloud_cover_percent": 4.0, "chip_cloud_fraction": 0.02},
           "before_scene": {"id": "S2B_y", "datetime": "2026-10-10T05:00:00+00:00"}, "indices": {"dnbr_p95": 0.31, "severity": "moderate-low severity", "burned_fraction": 0.2},
           "hot_pixels": {"count": 12}, "landsat": {"available": True, "scene": {"datetime": "2026-10-15T05:00:00+00:00", "platform": "landsat-9"}, "st_max_k": 320, "st_excess_k": 12},
           "images": {"after": {"true_colour": _uri(), "swir": _uri()}, "before": {"true_colour": _uri(), "swir": _uri()}}}


def test_basemap_stitches_tiles_and_marks_the_point(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "TILE_CACHE", tmp_path)
    img = report.basemap(22.9, 72.6, 14, size=400, fetch=lambda url: _png())
    assert img is not None and img.size == (400, 400)
    assert img.getpixel((200, 200)) == (220, 38, 38)                 # the marker centre is drawn over the map
    assert (tmp_path / "14").exists()                                # tiles are cached for the next report


def test_basemap_is_none_without_tiles(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "TILE_CACHE", tmp_path)

    def down(url):
        raise OSError("no network")
    assert report.basemap(22.9, 72.6, 14, fetch=down) is None


def test_text_helper_strips_diacritics_and_escapes_markup():
    assert report._t("Gujarāt <b> & co") == "Gujarat &lt;b&gt; &amp; co"


def test_report_builds_with_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "TILE_CACHE", tmp_path)
    pdf = report.build_pdf(ALERT, IMAGERY, fetch=lambda url: _png())
    assert pdf.startswith(b"%PDF") and len(pdf) > 5_000 and pdf.rstrip().endswith(b"%%EOF")


def test_report_builds_offline_without_imagery(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "TILE_CACHE", tmp_path)

    def down(url):
        raise OSError("no network")
    pdf = report.build_pdf({**ALERT, "decisions": [], "timeline": [], "peak_frp": None, "peak_at": None},
                           {"available": False, "reason": "No Sentinel-2 scene under 30% cloud"}, fetch=down)
    assert pdf.startswith(b"%PDF")


def _tiny_pmtiles(path, lat, lon):
    """A one-tile vector basemap: a water polygon in the west half and a major road across the middle of the tile around (lat, lon)."""
    import gzip

    import mapbox_vector_tile
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import write

    z = 12
    x, y = (int(v) for v in report._tile_xy(lat, lon, z))
    mvt = mapbox_vector_tile.encode([
        {"name": "water", "features": [{"geometry": "POLYGON((0 0, 1000 0, 1000 4096, 0 4096, 0 0))", "properties": {"kind": "lake"}}]},
        {"name": "roads", "features": [{"geometry": "LINESTRING(0 2048, 4096 2048)", "properties": {"kind": "major_road"}}]},
    ], default_options={"y_coord_down": True})
    with write(str(path)) as w:
        w.write_tile(zxy_to_tileid(z, x, y), gzip.compress(mvt))
        w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "min_zoom": z, "max_zoom": z, "min_lon_e7": 0, "min_lat_e7": 0,
                    "max_lon_e7": 0, "max_lat_e7": 0, "center_zoom": z, "center_lon_e7": 0, "center_lat_e7": 0}, {})


def test_offline_basemap_is_drawn_from_the_local_vector_file_without_any_network(tmp_path, monkeypatch):
    lat, lon = 22.95, 72.63
    f = tmp_path / "tiny.pmtiles"
    _tiny_pmtiles(f, lat, lon)
    monkeypatch.setattr(report, "PMTILES_FILE", str(f))
    monkeypatch.setattr(report, "_http", lambda url: (_ for _ in ()).throw(AssertionError("offline mode must not fetch tiles")))
    monkeypatch.setenv("OFFLINE_MODE", "true")
    img = report.basemap(lat, lon, 12, size=256)
    assert img is not None and img.size == (256, 256)
    # water in the western part of the data tile, background elsewhere, the road colour somewhere along the tile's horizontal midline
    colours = {img.getpixel((x, y)) for x in range(0, 256, 8) for y in range(0, 256, 8)}
    assert (170, 211, 223) in colours or (249, 210, 125) in colours
    pdf = report.build_pdf(ALERT, None)
    assert pdf.startswith(b"%PDF")


def test_offline_basemap_is_none_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "PMTILES_FILE", str(tmp_path / "missing.pmtiles"))
    monkeypatch.setenv("OFFLINE_MODE", "true")
    assert report.basemap(22.9, 72.6, 12) is None
