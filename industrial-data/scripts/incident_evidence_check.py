"""Step 2: does each saved source text name the event, the place and the date? (run from the repo root)

A heuristic: place tokens from locality/state, an event word (fire/blast/explosion) and the event year or month. The
matched tokens are kept in data_labels/evidence_index.json so a reader can audit every decision.
"""
import json, re
from pathlib import Path
import pandas as pd
df = pd.read_excel(r"D:\india_industrial_fire_incidents_40.xlsx", sheet_name="India Incidents")
GENERIC = {"district", "area", "industrial", "industries", "estate", "phase", "near", "road", "mandal", "tehsil", "india", "see", "linked",
           "report", "name", "reported", "source", "private", "limited", "company", "factory", "unit", "units", "plant", "chemical",
           "chemicals", "manufacturing", "facility", "nagar", "midc", "gidc", "east", "west", "north", "south", "pvt", "ltd", "the", "and", "tank", "farm"}
def tokens(*fields):
    out = []
    for f in fields:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", str(f)):
            if w.lower() not in GENERIC and w.lower() not in out:
                out.append(w.lower())
    return out
rows = []
for _, r in df.iterrows():
    iid = r["Incident ID"]
    p = Path(f"data/labels/evidence/{iid}.txt")
    d = pd.Timestamp(r["Event Date"])
    res = {"incident_id": iid, "fetched": p.exists(), "place_hits": [], "facility_hits": [], "event_word": False, "date_ok": False}
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore").lower()
        res["text_chars"] = len(t)
        loc = tokens(r["Locality"], r["State / UT"])
        fac = tokens(r["Facility / Company"])
        res["place_hits"] = [w for w in loc if w in t]
        res["facility_hits"] = [w for w in fac if w in t]
        res["event_word"] = bool(re.search(r"\b(fire|blaze|blast|explosion|flash fire|inferno)\b", t))
        months = d.strftime("%B").lower()
        res["date_ok"] = (str(d.year) in t) or (months in t)
    if res.get("text_chars", 0) < 150:
        res["place_hits"], res["facility_hits"], res["event_word"], res["date_ok"] = [], [], False, False
    rows.append(res)
    print(iid, "fetched" if res["fetched"] else "NOT-FETCHED", "place:", res["place_hits"][:4], "fac:", res["facility_hits"][:3], "event:", res["event_word"], "date:", res["date_ok"])
Path("data/labels/evidence_check.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
