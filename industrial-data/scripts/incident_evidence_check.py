"""Step 2: does each saved source text name the event, the place and the date? (run from the repo root)

A heuristic: place tokens from locality/state, an event word (fire/blast/explosion) and the event year or month. The
matched tokens are kept in data_labels/evidence_index.json so a reader can audit every decision.
"""
import json
import os, re
from pathlib import Path
import pandas as pd
df = pd.read_excel(os.getenv("INCIDENT_WORKBOOK", r"D:\india_industrial_fire_incidents_40.xlsx"), sheet_name="India Incidents")
GENERIC = {"district", "area", "industrial", "industries", "estate", "phase", "near", "road", "mandal", "tehsil", "india", "see", "linked",
           "report", "name", "reported", "source", "private", "limited", "company", "factory", "unit", "units", "plant", "chemical",
           "chemicals", "manufacturing", "facility", "nagar", "midc", "gidc", "east", "west", "north", "south", "pvt", "ltd", "the", "and", "tank", "farm",
           "register", "investigation", "pngrb", "off", "belt", "dehat"}
STATES = {"andhra", "pradesh", "uttar", "madhya", "himachal", "arunachal", "tamil", "nadu", "west", "bengal", "maharashtra", "gujarat",
          "karnataka", "kerala", "odisha", "bihar", "assam", "punjab", "haryana", "delhi", "telangana", "rajasthan", "jharkhand",
          "chhattisgarh", "goa", "uttarakhand"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]


def tokens(field) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", str(field)):
        w = w.lower()
        if w not in GENERIC and w not in STATES and w not in out:
            out.append(w)
    return out


def date_found(text: str, d: pd.Timestamp) -> bool:
    """Event year AND event month must both appear: as words ('January 2020', 'Jan 7, 2020') or as a numeric date
    (07.01.2020, 7/1/2020, 2020-01-07). A bare year is not enough - pages carry many years."""
    y, m = str(d.year), d.month
    month_word = re.search(rf"\b({MONTHS[m - 1]}|{MONTHS[m - 1][:3]})\b", text) is not None
    numeric = re.search(rf"\b0?{d.day}[./-]0?{m}[./-]{y}\b|\b{y}-0?{m}-0?{d.day}\b|\b0?{d.day}[./-]0?{m}[./-]{y[2:]}\b", text) is not None
    return numeric or (month_word and y in text)


rows = []
for _, r in df.iterrows():
    iid = r["Incident ID"]
    p = Path(f"data/labels/evidence/{iid}.txt")
    d = pd.Timestamp(r["Event Date"])
    res = {"incident_id": iid, "fetched": p.exists(), "place_hits": [], "facility_hits": [], "event_word": False, "date_ok": False}
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore").lower()
        res["text_chars"] = len(t)
        res["place_hits"] = [w for w in tokens(r["Locality"]) if w in t]          # locality only - a state name is not a place
        res["facility_hits"] = [w for w in tokens(r["Facility / Company"]) if w in t]
        res["event_word"] = bool(re.search(r"\b(fire|blaze|blast|explosion|flash fire|inferno)\b", t))
        res["date_ok"] = date_found(t, d)
    if res.get("text_chars", 0) < 150:          # error pages that merely echo the URL
        res["place_hits"], res["facility_hits"], res["event_word"], res["date_ok"] = [], [], False, False
    rows.append(res)
    print(iid, "place:", res["place_hits"][:4], "fac:", res["facility_hits"][:3], "event:", res["event_word"], "date:", res["date_ok"])
Path("data/labels/evidence_check.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
