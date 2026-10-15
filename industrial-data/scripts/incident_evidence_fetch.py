"""Step 1 of incident verification: fetch each incident's source page once (run from the repo root).

Writes data/labels/evidence/<id>.txt|.pdf and evidence_fetch.json. Pages behind a bot wall or a JavaScript check were
read in a normal browser instead and their text saved by hand under the same names (see docs/LABELS.md).
"""
import json, re, sys, time, hashlib
from pathlib import Path
import pandas as pd, requests
from html import unescape

OUT = Path("data/labels/evidence"); OUT.mkdir(parents=True, exist_ok=True)
df = pd.read_excel(r"D:\india_industrial_fire_incidents_40.xlsx", sheet_name="India Incidents")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "en"}
def text_of(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", html)))
rows = []
for _, r in df.iterrows():
    iid, url = r["Incident ID"], str(r["Source URL"])
    rec = {"incident_id": iid, "url": url, "status": None, "bytes": 0, "title": None, "error": None}
    try:
        resp = requests.get(url, headers=UA, timeout=45, allow_redirects=True)
        rec["status"] = resp.status_code
        ctype = resp.headers.get("content-type", "")
        rec["bytes"] = len(resp.content)
        rec["content_type"] = ctype.split(";")[0]
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            (OUT / f"{iid}.pdf").write_bytes(resp.content)
            rec["pdf"] = True
        else:
            t = text_of(resp.text)
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", resp.text)
            rec["title"] = unescape(m.group(1)).strip()[:200] if m else None
            (OUT / f"{iid}.txt").write_text(t[:200000], encoding="utf-8")
            rec["text_chars"] = len(t)
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    rows.append(rec)
    print(iid, rec["status"], rec.get("content_type"), rec["bytes"], (rec["title"] or rec["error"] or "")[:80], flush=True)
    time.sleep(1.0)
Path("data/labels/evidence_fetch.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
