"""Build the 6-slide deck (problem, data, model, accuracy, live demo, impact) as PPTX and PDF from one slide spec.

    python docs/deck/build_deck.py

Every number comes from the repository's own results (README, docs/EVALUATION.md, docs/REPLAY.md); the accuracy slide says what
the headline number is and is not.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "docs" / "images"
OUT = Path(__file__).resolve().parent
W, H = 13.333, 7.5
NAVY, SLATE, RED, AMBER, GREY = "#0f172a", "#475569", "#dc2626", "#ea580c", "#94a3b8"

SLIDES = [
    {"title": "Which heat is an industrial fire?",
     "sub": "Problem",
     "bullets": ["Satellites flag thousands of heat sources every day. In one 3-hour snapshot of India: 3,073 sources, 2,165 of them crop burning and 427 forest fires.",
                 "Only about 1 in 10 is industrial (291), and almost all of those are routine: coal seams, flares, kilns, furnaces.",
                 "An analyst needs the few that are abnormal, a reason for each, and proof before anyone is sent."],
     "numbers": [("3,073", "thermal sources, one snapshot"), ("9 %", "industrial heat"), ("34", "documented industrial fires, 2016-2025")],
     "images": [("national_view.png", 6.9, 3.15, 6.0, 3.55)],
     "foot": "NASA FIRMS VIIRS, 2026-10-16. A detection is evidence that a place was hot, not a confirmed fire."},
    {"title": "Public data, joined and kept honest",
     "sub": "Data",
     "bullets": ["Heat: NASA FIRMS VIIRS (S-NPP, NOAA-20/21), 375 m, about 3 h behind the satellite, plus a 365-day archive of 1.9 M detections.",
                 "Place: 12,762 facilities merged from OpenStreetMap, WRI power plants and VIIRS flare sites, each with its source and licence; ESA WorldCover land cover.",
                 "Normal: a 365-day baseline per facility and per 1 km cell, so a source is compared with its own history.",
                 "Proof: Sentinel-2 and Landsat scenes for the day of the detection (burn index, SWIR hot pixels, surface temperature).",
                 "Labels: 3,247 rule-derived units and 34 verified incidents (source text confirms event, place and date)."],
     "images": [("facility_provenance.png", 7.0, 1.7, 5.8, 3.5), ("source_baseline.png", 7.0, 5.0, 5.8, 2.0)],
     "foot": "Rule-derived labels are weak labels; the verified incidents are the independent check."},
    {"title": "A model that explains itself, and the rules as a fallback",
     "sub": "Model",
     "bullets": ["Gradient-boosted trees on 41 features: heat behaviour, facility distance and kind, land cover, the baseline z-score, season.",
                 "Calibrated probabilities; the top reasons per source come from sampling-Shapley values, in words.",
                 "The rule classifier stays as baseline and fallback, and as the source of the fire-candidate score the alerts use.",
                 "Alert = P(industrial fire) high, or a source at a known facility 3 sd above its own baseline; one alert per facility per 6 h; a cropland-by-day veto.",
                 "Analysts confirm or reject in an inbox; every decision lands in the labels table for the next model."],
     "images": [("alert_inbox.png", 6.7, 1.5, 6.2, 3.72), ("eval_confusion.png", 6.7, 5.25, 6.2, 1.9)],
     "foot": "Rules in docs/EVALUATION.md and the README; model card in the dashboard tab."},
    {"title": "What the numbers say, and what they do not",
     "sub": "Accuracy",
     "numbers": [("0.993", "model macro-F1, held-out regions and months (95% CI 0.987-0.998)"), ("0.809", "rule classifier (0.763-0.848)"),
                 ("0.633", "model that sees only the heat itself")],
     "bullets": ["The labels are rule-derived, so 0.993 is agreement with the labelling rules, not real-world accuracy. The 0.633 is the part the satellite signal earns alone.",
                 "Independent check: VIIRS saw 20 of 34 documented industrial fires; at 0.25 false alarms per day the model flags all 20, the rules 16.",
                 "Replaying every verified incident through the live pipeline: 22 have a detection and only 5 raise an alert (15% of all 34).",
                 "Misses are mostly refineries that are always hot, and a 54 MW event with no mapped facility."],
     "images": [("eval_incidents.png", 7.6, 3.3, 5.3, 3.6)],
     "foot": "docs/EVALUATION.md, docs/REPLAY.md. Small samples; single digits per location tier."},
    {"title": "Live demo",
     "sub": "Demo",
     "bullets": ["National view: heat-density grid for crop and forest fires, points for industrial sources; facilities are a separate toggle.",
                 "Dashboard tab: alerts today, class mix over time, top states, model card.",
                 "Alert inbox: timeline, imagery, Confirm / Reject / Unsure; one click to a PDF incident report.",
                 "Replay: Vatva GIDC 2020-12-09 through the same pipeline on a virtual clock; the analyst's decision lands in labels."],
     "images": [("dashboard_tab.png", 0.5, 4.45, 6.0, 2.6), ("report_page1.png", 6.8, 1.35, 3.9, 5.5), ("mobile_map.png", 10.9, 1.35, 2.2, 4.7)],
     "foot": "Video: docs/demo/thermotrace_overview.mp4 (2 min) and docs/demo/thermotrace_replay_demo.mp4 (4 min)."},
    {"title": "Impact, and what is still hard",
     "sub": "Impact",
     "bullets": ["For a control room: a short list of abnormal industrial heat with reasons, imagery and a report, instead of thousands of dots.",
                 "Every claim carries its limits: weak labels, 375 m pixels, one cycle every 3 h, a refinery fire looks like its flare.",
                 "Honest hit rate: 5 of 22 detected incidents alert today. Next: a rule for large new sources with no mapped facility, more verified incidents, and Sentinel-2 evidence at alert time for every alert.",
                 "Open items: public hosting (scripts and checks are ready, no VM yet), real Telegram credentials, Global Energy Monitor trackers."],
     "numbers": [("5 / 22", "detected incidents that alert"), ("0", "industrial alerts on the Punjab burning peak (6,746 sources)")],
     "images": [("source_imagery.png", 7.2, 3.6, 5.6, 3.35)],
     "foot": "github.com/faizdevx/ThermoTrace"},
]


def pdf_image_box(c, name, x, y, w, h):
    p = IMG / name
    if not p.exists():
        return
    iw, ih = Image.open(p).size
    s = min(w / iw, h / ih)
    dw, dh = iw * s, ih * s
    c.drawImage(ImageReader(str(p)), x + (w - dw) / 2, H - y - h + (h - dh) / 2 + 0, dw, dh, mask="auto")


def wrap(c, text, font, size, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if c.stringWidth(t, font, size) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    return lines + ([cur] if cur else [])


def build_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(W * 72, H * 72))
    c.setTitle("ThermoTrace - 6-slide deck")
    for i, s in enumerate(SLIDES, 1):
        c.saveState()
        c.scale(72, 72)
        c.setFillColor(HexColor("#f8fafc"))
        c.rect(0, 0, W, H, stroke=0, fill=1)
        c.setFillColor(HexColor(NAVY))
        c.rect(0, H - 1.15, W, 1.15, stroke=0, fill=1)
        c.restoreState()
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 30)
        c.drawString(0.5 * 72, (H - 0.72) * 72, s["title"])
        c.setFont("Helvetica", 12)
        c.setFillColor(HexColor("#7dd3fc"))
        c.drawRightString((W - 0.5) * 72, (H - 0.45) * 72, f"{i} / 6 - {s['sub'].upper()}")
        y = 1.55
        for big, small in s.get("numbers", []):
            c.setFillColor(HexColor(RED if "0.993" not in big else NAVY))
            c.setFont("Helvetica-Bold", 30)
            c.drawString(0.5 * 72, (H - y - 0.4) * 72, big)
            c.setFillColor(HexColor(SLATE))
            c.setFont("Helvetica", 11)
            for k, ln in enumerate(wrap(c, small, "Helvetica", 11, 4.2 * 72)):
                c.drawString(2.0 * 72, (H - y - 0.22 - 0.2 * k) * 72, ln)
            y += 0.75
        y += 0.1
        has_img = bool(s.get("images"))
        width = (6.0 if has_img else 12.0) * 72
        c.setFillColor(HexColor(NAVY))
        for b in s["bullets"]:
            lines = wrap(c, b, "Helvetica", 13.5, width)
            c.setFont("Helvetica-Bold", 13.5)
            c.drawString(0.5 * 72, (H - y - 0.2) * 72, "-")
            c.setFont("Helvetica", 13.5)
            for k, ln in enumerate(lines):
                c.drawString(0.75 * 72, (H - y - 0.2 - 0.23 * k) * 72, ln)
            y += 0.23 * len(lines) + 0.17
        for name, x, yy, w, h in s.get("images", []):
            pdf_image_box_pts(c, name, x, yy, w, h)
        c.setFillColor(HexColor(GREY))
        c.setFont("Helvetica", 9.5)
        c.drawString(0.5 * 72, 0.3 * 72, s["foot"])
        c.showPage()
    c.save()


def pdf_image_box_pts(c, name, x, y, w, h):
    p = IMG / name
    if not p.exists():
        return
    iw, ih = Image.open(p).size
    s = min(w / iw, h / ih)
    dw, dh = iw * s, ih * s
    c.drawImage(ImageReader(str(p)), (x + (w - dw) / 2) * 72, (H - y - h + (h - dh) / 2) * 72, dw * 72, dh * 72, mask="auto")


def rgb(hex_):
    h = hex_.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def build_pptx(path: Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    blank = prs.slide_layouts[6]
    for i, s in enumerate(SLIDES, 1):
        sl = prs.slides.add_slide(blank)
        bg = sl.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.15))
        bg.fill.solid()
        bg.fill.fore_color.rgb = rgb(NAVY)
        bg.line.fill.background()

        def text(x, y, w, h, t, size, bold=False, color=NAVY, align=None):
            tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = t
            p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, rgb(color)
            if align:
                p.alignment = align
            return tf

        text(0.5, 0.25, 10.5, 0.7, s["title"], 30, True, "#ffffff")
        from pptx.enum.text import PP_ALIGN
        text(9.3, 0.15, 3.6, 0.4, f"{i} / 6 - {s['sub'].upper()}", 12, False, "#7dd3fc", PP_ALIGN.RIGHT)
        y = 1.35
        for big, small in s.get("numbers", []):
            text(0.5, y, 1.6, 0.6, big, 28, True, RED if "0.993" not in big else NAVY)
            text(2.0, y + 0.05, 4.4, 0.6, small, 11, False, SLATE)
            y += 0.75
        y += 0.1
        has_img = bool(s.get("images"))
        tb = sl.shapes.add_textbox(Inches(0.5), Inches(y), Inches(6.1 if has_img else 12.3), Inches(H - y - 0.6))
        tf = tb.text_frame
        tf.word_wrap = True
        for k, b in enumerate(s["bullets"]):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            p.text = "- " + b
            p.font.size, p.font.color.rgb = Pt(13.5), rgb(NAVY)
            p.space_after = Pt(8)
        for name, x, yy, w, h in s.get("images", []):
            pth = IMG / name
            if pth.exists():
                iw, ih = Image.open(pth).size
                sc = min(w / iw, h / ih)
                dw, dh = iw * sc, ih * sc
                sl.shapes.add_picture(str(pth), Inches(x + (w - dw) / 2), Inches(yy + (h - dh) / 2), Inches(dw), Inches(dh))
        text(0.5, H - 0.5, 12, 0.3, s["foot"], 9.5, False, GREY)
    prs.save(str(path))


if __name__ == "__main__":
    build_pdf(OUT / "ThermoTrace_deck.pdf")
    build_pptx(OUT / "ThermoTrace_deck.pptx")
    print("wrote", OUT / "ThermoTrace_deck.pdf", OUT / "ThermoTrace_deck.pptx")
