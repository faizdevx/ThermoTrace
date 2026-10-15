"""Join the incident workbook, the source-text checks and the curated locations into the committed incident list.

Output: industrial-data/data_labels/incidents_verified.csv  (+ evidence_index.json with sha256 of every checked text)
An incident is `verified` only when (1) the source text names the event, the place and the date and (2) its location
is resolved to an auditable OSM object. Page texts are not committed (publisher copyright); their hashes are.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "industrial-data"))
from data_labels.incident_locations import ALTERNATIVE_SOURCES, LOCATIONS, UNRESOLVED  # noqa: E402

XLSX = Path(os.getenv("INCIDENT_WORKBOOK", r"D:\india_industrial_fire_incidents_40.xlsx"))
EVID = ROOT / "data" / "labels" / "evidence"
CHECK = json.loads((ROOT / "data" / "labels" / "evidence_check.json").read_text(encoding="utf-8"))
FETCH = {r["incident_id"]: r for r in json.loads((ROOT / "data" / "labels" / "evidence_fetch.json").read_text(encoding="utf-8"))}
BROWSER = {"IN-005", "IN-008", "IN-011", "IN-013", "IN-026", "IN-012", "IN-015", "IN-021"}
PDF_TEXT = {"IN-019", "IN-034"}
PDF_IMAGE = {"IN-016"}
REGISTER = {"IN-017", "IN-022", "IN-025"}


def method(iid: str) -> str:
    if iid in BROWSER:
        return "browser_read"
    if iid in PDF_TEXT:
        return "pdf_text"
    if iid in PDF_IMAGE:
        return "pdf_page_image+register_row"
    if iid in REGISTER:
        return "register_row"
    return "http_fetch"


def main() -> None:
    df = pd.read_excel(XLSX, sheet_name="India Incidents")
    chk = {c["incident_id"]: c for c in CHECK}
    rows, index = [], []
    for _, r in df.iterrows():
        iid = r["Incident ID"]
        c = chk.get(iid, {})
        confirmed = bool(c.get("place_hits")) and bool(c.get("event_word")) and bool(c.get("date_ok"))
        txt = EVID / f"{iid}.txt"
        sha = hashlib.sha256(txt.read_bytes()).hexdigest() if txt.exists() else None
        loc = LOCATIONS.get(iid)
        rows.append({
            "incident_id": iid, "event_date": pd.Timestamp(r["Event Date"]).date().isoformat(),
            "facility": r["Facility / Company"], "locality": r["Locality"], "state": r["State / UT"], "industry": r["Industry"],
            "incident_type": r["Incident Type"],
            "source_url": ALTERNATIVE_SOURCES.get(iid, (r["Source URL"],))[0],
            "source_publisher": ALTERNATIVE_SOURCES[iid][1] if iid in ALTERNATIVE_SOURCES else r["Source Publisher"],
            "source_url_original": r["Source URL"],
            "evidence_type": r["Evidence Type"], "source_confirms_event_place_date": confirmed,
            "verified": bool(confirmed and loc),
            "lat": loc[1] if loc else None, "lon": loc[2] if loc else None, "radius_km": loc[3] if loc else None,
            "location_basis": loc[4] if loc else None, "osm_ref": loc[0] if loc else None,
            "location_note": loc[5] if loc else UNRESOLVED.get(iid),
        })
        index.append({"incident_id": iid, "url": ALTERNATIVE_SOURCES.get(iid, (r["Source URL"],))[0],
                      "method": ("http_fetch_alternative_source" if iid in ALTERNATIVE_SOURCES else method(iid)) if txt.exists() else "not_retrievable",
                      "http_status": FETCH.get(iid, {}).get("status"), "text_sha256": sha,
                      "matched_place_tokens": c.get("place_hits", [])[:6], "matched_facility_tokens": c.get("facility_hits", [])[:6],
                      "event_word": c.get("event_word", False), "date_ok": c.get("date_ok", False)})
    out = pd.DataFrame(rows)
    match = ROOT / "data" / "incidents" / "incident_firms_match_v2.csv"
    if match.exists():       # NASA FIRMS check (scripts/match_incidents.py); parameters sit next to it
        m = pd.read_csv(match).set_index("incident_id")
        for col, name in (("status", "firms_status"), ("event_detections", "firms_event_detections"),
                          ("event_max_frp", "firms_event_max_frp"), ("baseline_active_fraction", "firms_baseline_active_fraction")):
            out[name] = out.incident_id.map(m[col]) if col in m else None
    dest = ROOT / "industrial-data" / "data_labels" / "incidents_verified.csv"
    out.to_csv(dest, index=False)
    (ROOT / "industrial-data" / "data_labels" / "evidence_index.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": "page texts are not stored in git (publisher copyright); sha256 identifies what was checked",
                    "entries": index}, indent=1), encoding="utf-8")
    v = out[out.verified]
    print("incidents:", len(out), "| source confirms event+place+date:", int(out.source_confirms_event_place_date.sum()),
          "| verified (confirmed + located):", len(v))
    print(v.location_basis.value_counts().to_dict())
    print("not verified:", out[~out.verified][["incident_id", "location_note"]].to_dict("records"))


if __name__ == "__main__":
    main()
