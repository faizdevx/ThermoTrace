"""Alert delivery: webhook, Telegram bot, SMTP email. Credentials come from the environment only.

    ALERT_WEBHOOK_URL, ALERT_WEBHOOK_SECRET (optional: HMAC-SHA256 of the body in ``X-ThermoTrace-Signature``)
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID           (TELEGRAM_API_BASE overrides https://api.telegram.org for tests)
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_SECURITY (starttls | ssl | none), ALERT_EMAIL_FROM, ALERT_EMAIL_TO
    ALERT_BASE_URL                                 (dashboard address put in the message, optional)

A channel is *configured* only when all of its required variables are set; unconfigured channels are skipped, never an
error. ``send`` never raises: each channel reports ``{ok, at, error}`` so one failing channel cannot stop the others.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

TIMEOUT = float(os.getenv("ALERT_HTTP_TIMEOUT_SECONDS", "10"))


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def configured() -> list[str]:
    ch = []
    if _env("ALERT_WEBHOOK_URL"):
        ch.append("webhook")
    if _env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"):
        ch.append("telegram")
    if _env("SMTP_HOST") and _env("ALERT_EMAIL_FROM") and _env("ALERT_EMAIL_TO"):
        ch.append("email")
    return ch


def message_text(alert: dict) -> str:
    link = _env("ALERT_BASE_URL")
    lines = [("[TEST] " if alert.get("is_test") else "") + str(alert["summary"])]
    if alert.get("first_seen"):
        lines.append(f"First detection {alert['first_seen']}; last {alert.get('last_seen')}")
    if alert.get("lat") is not None:
        lines.append(f"Location {float(alert['lat']):.4f}, {float(alert['lon']):.4f}")
    for r in (alert.get("reasons") or [])[:3]:
        lines.append(f"- {r}")
    lines.append("A heat signature is evidence, not a confirmed fire: check imagery and decide in the alert inbox.")
    if link:
        lines.append(f"{link.rstrip('/')}/#alert-{alert['alert_id']}")
    return "\n".join(lines)


def _post(url: str, body: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:       # noqa: S310 - operator-configured URL
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")
        data = resp.read()
    return data


def send_webhook(alert: dict) -> None:
    body = json.dumps({"event": "thermotrace.alert", "alert": alert, "text": message_text(alert)}, default=str).encode()
    headers = {}
    secret = _env("ALERT_WEBHOOK_SECRET")
    if secret:
        headers["X-ThermoTrace-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    _post(_env("ALERT_WEBHOOK_URL"), body, headers)


def send_telegram(alert: dict) -> None:
    base = (_env("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")
    body = json.dumps({"chat_id": _env("TELEGRAM_CHAT_ID"), "text": message_text(alert), "disable_web_page_preview": True}).encode()
    try:
        data = _post(f"{base}/bot{_env('TELEGRAM_BOT_TOKEN')}/sendMessage", body, {})
    except urllib.error.HTTPError as exc:                              # Telegram explains itself in the body
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail}".replace(_env("TELEGRAM_BOT_TOKEN"), "***")) from None
    if not json.loads(data or b"{}").get("ok", False):
        raise RuntimeError("Telegram answered ok=false")


def send_email(alert: dict) -> None:
    msg = EmailMessage()
    msg["Subject"] = ("[TEST] " if alert.get("is_test") else "") + f"ThermoTrace {str(alert['severity']).upper()} alert: " + \
        str(alert.get("site_name") or alert.get("site_ref") or alert.get("source_id"))
    msg["From"], msg["To"] = _env("ALERT_EMAIL_FROM"), _env("ALERT_EMAIL_TO")
    msg.set_content(message_text(alert))
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT") or "587")
    security = (_env("SMTP_SECURITY") or "starttls").lower()
    cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
    with cls(host, port, timeout=TIMEOUT) as smtp:
        if security == "starttls":
            smtp.starttls()
        if _env("SMTP_USER"):
            smtp.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
        smtp.send_message(msg)


SENDERS = {"webhook": send_webhook, "telegram": send_telegram, "email": send_email}


def send(alert: dict, channels: list[str] | None = None) -> dict[str, dict]:
    """Deliver to ``channels`` (default: all configured). Returns {channel: {ok, at, error}}; never raises."""
    out: dict[str, dict] = {}
    for name in channels if channels is not None else configured():
        at = datetime.now(timezone.utc).isoformat()
        try:
            SENDERS[name](alert)
            out[name] = {"ok": True, "at": at, "error": None}
        except Exception as exc:  # noqa: BLE001 - delivery problems are recorded, not raised
            out[name] = {"ok": False, "at": at, "error": str(exc)[:300]}
    return out
