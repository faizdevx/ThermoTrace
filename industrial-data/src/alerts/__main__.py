"""``python -m src.alerts test`` - send a clearly labelled test alert through every configured channel."""
from __future__ import annotations

import json
import os
import sys

from . import notify, store


def main() -> int:
    if (sys.argv[1:] or [""])[0] != "test":
        print("usage: python -m src.alerts test")
        return 2
    import psycopg2
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    ch = notify.configured()
    print("configured channels:", ch or "none (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, ALERT_WEBHOOK_URL or SMTP_*)")
    conn = psycopg2.connect(dsn)
    try:
        a = store.create_test_alert(conn)
    finally:
        conn.close()
    print(json.dumps({"alert_id": a["alert_id"], "delivery": a["delivery"]}, indent=2, default=str))
    return 0 if a["delivery"] and all(v["ok"] for v in a["delivery"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
