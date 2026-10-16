"""Downloadable incident report (PDF) for one alert: where, what the satellite saw, why, and what happened since.

Everything on the page comes from the stored alert and the imagery analysis (the same cached result the dashboard shows);
nothing is invented. The basemap is OpenStreetMap tiles fetched when the report is made (cached on disk); without network
the map panels say so instead of drawing a fake one.
"""
from __future__ import annotations

import base64
import io
import math
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TILE_URL = os.getenv("REPORT_TILE_URL") or "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_CACHE = Path(os.getenv("REPORT_TILE_CACHE", Path(tempfile.gettempdir()) / "thermotrace-tiles"))
UA = "ThermoTrace-report/1.0 (hackathon prototype; contact via repository)"
SEVERITY = {"critical": colors.HexColor("#dc2626"), "high": colors.HexColor("#ea580c"), "watch": colors.HexColor("#d97706")}


# ------------------------------------------------------------------------------------------------ basemap
def _tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def _tile(z: int, x: int, y: int, fetch=None) -> Image.Image:
    path = TILE_CACHE / str(z) / str(x) / f"{y}.png"
    if path.exists():
        return Image.open(path).convert("RGB")
    data = (fetch or _http)(TILE_URL.format(z=z, x=x, y=y))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return Image.open(io.BytesIO(data)).convert("RGB")


def _http(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=8) as r:  # noqa: S310
        return r.read()


def basemap(lat: float, lon: float, zoom: int, size: int = 640, fetch=None) -> Image.Image | None:
    """A ``size`` px square OSM map centred on the point with a marker; None when the tiles cannot be fetched."""
    cx, cy = _tile_xy(lat, lon, zoom)
    n = math.ceil(size / 256) + 1
    x0, y0 = int(cx - n / 2), int(cy - n / 2)
    canvas = Image.new("RGB", (n * 256, n * 256), (226, 232, 240))
    got = 0
    for i in range(n):
        for j in range(n):
            try:
                canvas.paste(_tile(zoom, x0 + i, y0 + j, fetch), (i * 256, j * 256))
                got += 1
            except Exception:  # noqa: BLE001 - one missing tile leaves a grey square
                continue
    if got == 0:
        return None
    px, py = (cx - x0) * 256, (cy - y0) * 256
    img = canvas.crop((int(px - size / 2), int(py - size / 2), int(px + size / 2), int(py + size / 2)))
    d = ImageDraw.Draw(img)
    c = size // 2
    d.ellipse([c - 16, c - 16, c + 16, c + 16], outline=(220, 38, 38), width=4)
    d.ellipse([c - 4, c - 4, c + 4, c + 4], fill=(220, 38, 38))
    return img


def _png(img: Image.Image) -> io.BytesIO:
    b = io.BytesIO()
    img.save(b, "PNG")
    b.seek(0)
    return b


def _data_uri_image(uri: str | None) -> io.BytesIO | None:
    if not uri or "," not in uri:
        return None
    try:
        return io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------ document
def _t(v: Any) -> str:
    """Text safe for the built-in PDF fonts (Latin-1): diacritics stripped (Gujarat for Gujarāt), markup escaped."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode("ascii")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _when(v: Any) -> str:
    if not v:
        return "n/a"
    try:
        return datetime.fromisoformat(str(v)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(v)


def build_pdf(alert: dict[str, Any], imagery: dict[str, Any] | None, fetch=None, now: datetime | None = None) -> bytes:
    """The report as PDF bytes. ``alert`` is ``alerts_repo.get_alert`` output, ``imagery`` the imagery analysis (or None)."""
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.2, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7.6, leading=9.5, textColor=colors.HexColor("#64748b"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=3, textColor=colors.HexColor("#0f172a"))
    title = ParagraphStyle("t", parent=ss["Title"], fontSize=17, leading=20, alignment=0, textColor=colors.white)
    sev = str(alert["severity"])
    place = alert.get("site_name") or (f"unnamed {alert['site_kind'].replace('_', ' ')}" if alert.get("site_kind") else "unmapped location")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
                            title=f"ThermoTrace incident report - alert {alert['alert_id']}", author="ThermoTrace")
    story: list = []

    head = Table([[Paragraph(f"Incident report - alert #{alert['alert_id']}" + (" (TEST)" if alert.get("is_test") else ""), title),
                   Paragraph(f"<b>{sev.upper()}</b>", ParagraphStyle("sv", parent=title, alignment=2, fontSize=15))]], colWidths=[130 * mm, 52 * mm])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SEVERITY.get(sev, colors.HexColor("#334155"))), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 7),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [head, Spacer(1, 5), Paragraph(_t(alert["summary"]), body)]

    facts = [["Place", _t(f"{place} ({str(alert.get('site_kind') or '-').replace('_', ' ')}), {alert.get('state') or 'India'}")],
             ["Location", f"{alert['lat']:.5f} N, {alert['lon']:.5f} E"],
             ["First detection", _when(alert.get("first_seen"))], ["Latest detection", _when(alert.get("last_seen"))],
             ["Peak power", f"{alert['peak_frp']:.1f} MW" + (f" at {_when(alert.get('peak_at'))}" if alert.get("peak_at") else "") if alert.get("peak_frp") is not None else "n/a"],
             ["Why it alerted", {"p_fire": "P(industrial fire) above threshold", "frp_z": "power far above this facility's own baseline",
                                 "both": "both rules", "test": "delivery test"}.get(alert.get("trigger"), str(alert.get("trigger")))
              + (f" - P(fire) {alert['p_fire']:.0%}" if alert.get("p_fire") is not None else "")
              + (f", {alert['frp_z']:+.1f} sd vs own baseline" if alert.get("frp_z") is not None else "")],
             ["Status", str(alert["status"]).capitalize() + (f" (decided by {alert['decisions'][-1].get('analyst') or 'analyst'})" if alert.get("decisions") else "")]]
    t = Table([[Paragraph(f"<b>{k}</b>", body), Paragraph(v, body)] for k, v in facts], colWidths=[34 * mm, 148 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [Spacer(1, 4), t]

    # maps: the neighbourhood and the region
    story.append(Paragraph("Where", h2))
    local, region = basemap(alert["lat"], alert["lon"], 14, fetch=fetch), basemap(alert["lat"], alert["lon"], 9, fetch=fetch)
    cells = []
    for img, cap in ((local, "Neighbourhood (zoom 14)"), (region, "Region (zoom 9)")):
        cells.append([RLImage(_png(img), width=84 * mm, height=84 * mm) if img else Paragraph("Basemap unavailable: the report was made without network access to the tile server.", small),
                      Paragraph(cap + " - red ring = detection", small)])
    mt = Table([[cells[0][0], cells[1][0]], [cells[0][1], cells[1][1]]], colWidths=[91 * mm, 91 * mm])
    mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Paragraph("Map data (c) OpenStreetMap contributors (ODbL). Fire data: NASA FIRMS VIIRS, 375 m pixels.", small)]

    # imagery
    story.append(Paragraph("What the satellite saw", h2))
    if imagery and imagery.get("available") and imagery.get("scene"):
        sc, bf, idx, hp = imagery["scene"], imagery.get("before_scene"), imagery.get("indices") or {}, imagery.get("hot_pixels") or {}
        story.append(Paragraph(f"Sentinel-2 L2A <b>{_t(sc['id'])}</b>, {_when(sc['datetime'])}, {sc['cloud_cover_percent']:.0f}% scene cloud"
                               + (f", {sc['chip_cloud_fraction']:.0%} over the chip" if sc.get("chip_cloud_fraction") is not None else "")
                               + (f"; before: {bf['id']} ({_when(bf['datetime'])})" if bf else "; no before scene") + ".", body))
        imgs = imagery.get("images") or {}
        chips = [(_data_uri_image((imgs.get(k) or {}).get(b)), cap) for k, b, cap in (
            ("after", "true_colour", "After - true colour"), ("after", "swir", "After - SWIR (flames glow orange)"),
            ("before", "true_colour", "Before - true colour"), ("before", "swir", "Before - SWIR"))]
        if any(c[0] for c in chips):
            it = Table([[RLImage(c[0], width=43 * mm, height=43 * mm) if c[0] else "" for c in chips], [Paragraph(c[1], small) for c in chips]],
                       colWidths=[45.5 * mm] * 4)
            it.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1)]))
            story.append(it)
        lines = []
        if idx.get("dnbr_p95") is not None:
            lines.append(f"dNBR (95th percentile) {idx['dnbr_p95']:+.2f} - {idx.get('severity', '')}; {idx.get('burned_fraction', 0):.0%} of the 3 km chip changed like a burn scar. "
                         "A fire inside buildings leaves no scar at 10-20 m, so a low value does not exclude one.")
        if hp:
            lines.append(f"{hp.get('count', 0)} SWIR hot pixel(s) in the chip (B12 above B11: flames or very hot surfaces; furnaces and flares also qualify).")
        ls = imagery.get("landsat") or {}
        if ls.get("available") and ls.get("scene"):
            lines.append(f"Landsat {ls['scene'].get('platform') or '8/9'} {_when(ls['scene']['datetime'])}: surface temperature up to {ls.get('st_max_k', 0):.0f} K, "
                         f"{ls.get('st_excess_k', 0):.0f} K above the chip median.")
        story += [Paragraph(x, body) for x in lines]
    else:
        story.append(Paragraph((imagery or {}).get("reason") or "No Sentinel-2 scene under 30% cloud within 5 days of the detection: imagery could neither confirm nor exclude a fire.", body))

    story.append(Paragraph("Reasons", h2))
    for r in (alert.get("reasons") or ["no reasons recorded"])[:8]:
        story.append(Paragraph("&bull; " + _t(r), body))

    story.append(Paragraph("Timeline", h2))
    tl = [[Paragraph(_when(e["at"]), small), Paragraph(_t(e["text"]), body)] for e in alert.get("timeline") or []]
    if tl:
        tt = Table(tl, colWidths=[38 * mm, 144 * mm])
        tt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(tt)
    for d in alert.get("decisions") or []:
        story.append(Paragraph(f"<b>Analyst decision</b> ({_when(d['decided_at'])}): {d['decision']}" + (_t(f" - {d['note']}") if d.get("note") else ""), body))

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    story += [Spacer(1, 8), KeepTogether([Paragraph(
        f"Generated {stamp} by ThermoTrace. A thermal detection is evidence that a place was hot at that time, not a confirmed fire; the class and the alert come from "
        "heuristic rules and a model trained on rule-derived labels (see docs/EVALUATION.md). Verify with imagery and ground reports before acting. "
        "Data: NASA FIRMS (LANCE), OpenStreetMap contributors (ODbL), ESA WorldCover 2021, Copernicus Sentinel-2 / USGS Landsat.", small)])]
    doc.build(story)
    return buf.getvalue()
