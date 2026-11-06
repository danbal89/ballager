"""Erinnerungen vor dem Ablaufdatum – über Home Assistant, E-Mail und WhatsApp."""
from __future__ import annotations

import logging
import smtplib
import ssl
import urllib.parse
import urllib.request
from datetime import date
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import db, ha, inventory

log = logging.getLogger("ballager.notify")

TITLE = "BALLager"


# --------------------------------------------------------------------------
# Kanäle
# --------------------------------------------------------------------------
def _send_email(cfg: dict, subject: str, text: str) -> None:
    recipients = [r.strip() for r in str(cfg.get("recipients", "")).replace(";", ",").split(",") if r.strip()]
    if not cfg.get("host") or not recipients:
        raise ValueError("E-Mail: SMTP-Server und Empfänger angeben")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("sender") or cfg.get("username") or "ballager@localhost"
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="ballager.local")
    msg.set_content(text)
    port = int(cfg.get("port") or 587)
    security = cfg.get("security", "starttls")
    ctx = ssl.create_default_context()
    if security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], port, context=ctx, timeout=20)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=20)
    with server:
        if security == "starttls":
            server.starttls(context=ctx)
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password", ""))
        server.send_message(msg)


def _send_whatsapp(cfg: dict, text: str) -> list[str]:
    """WhatsApp über den kostenlosen Dienst CallMeBot (https://www.callmebot.com)."""
    errors = []
    for r in cfg.get("recipients") or []:
        phone = str(r.get("phone", "")).replace(" ", "")
        apikey = str(r.get("apikey", "")).strip()
        if not phone or not apikey:
            continue
        url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
            {"phone": phone, "text": text, "apikey": apikey}
        )
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "BALLager"}), timeout=20) as resp:
                body = resp.read().decode("utf-8", "replace")
                if resp.status >= 400 or "ERROR" in body.upper()[:400]:
                    raise RuntimeError(body[:200])
        except Exception as exc:
            errors.append(f"WhatsApp an {r.get('name') or phone}: {exc}")
    return errors


def send(cfg: dict, subject: str, text: str) -> dict:
    """Verschickt eine Nachricht über alle aktivierten Kanäle. Liefert Ergebnis je Kanal."""
    result: dict = {"sent": [], "errors": []}
    for service in cfg.get("ha_services") or []:
        try:
            ha.call_service(service, subject, text)
            result["sent"].append(service)
        except Exception as exc:
            result["errors"].append(f"{service}: {exc}")
    mail = cfg.get("email") or {}
    if mail.get("enabled"):
        try:
            _send_email(mail, subject, text)
            result["sent"].append("E-Mail")
        except Exception as exc:
            result["errors"].append(f"E-Mail: {exc}")
    wa = cfg.get("whatsapp") or {}
    if wa.get("enabled"):
        errs = _send_whatsapp(wa, text)
        result["errors"].extend(errs)
        if not errs:
            result["sent"].append("WhatsApp")
    for e in result["errors"]:
        log.warning("Benachrichtigung fehlgeschlagen – %s", e)
    return result


# --------------------------------------------------------------------------
# Tägliche Prüfung
# --------------------------------------------------------------------------
def _fmt_days(days: int) -> str:
    if days < 0:
        return f"seit {-days} Tag{'en' if days < -1 else ''} abgelaufen"
    if days == 0:
        return "läuft HEUTE ab"
    if days == 1:
        return "läuft morgen ab"
    return f"läuft in {days} Tagen ab"


def build_digest(conn, cfg: dict, mark: bool = True) -> tuple[str, list[tuple[int, int]]]:
    """Sucht fällige Erinnerungen. Gibt (Text, [(stock_id, threshold)]) zurück."""
    thresholds = sorted({int(t) for t in cfg.get("thresholds") or [] if int(t) >= 0}, reverse=True)
    today = date.today()
    items = db.rows(conn.execute(
        """SELECT s.id, s.quantity, s.expiry_date, p.name, p.brand, p.unit,
                  l.name AS location, l.icon AS location_icon
             FROM stock s JOIN products p ON p.id = s.product_id
             LEFT JOIN locations l ON l.id = s.location_id
            WHERE s.expiry_date IS NOT NULL
            ORDER BY s.expiry_date"""
    ))
    sent = {(r["stock_id"], r["threshold"]) for r in db.rows(conn.execute("SELECT stock_id, threshold FROM notification_log"))}
    due_lines: list[tuple[int, str]] = []
    marks: list[tuple[int, int]] = []
    for it in items:
        days = (date.fromisoformat(it["expiry_date"]) - today).days
        applicable = [t for t in thresholds if days <= t]
        key_threshold = None
        if days < 0 and cfg.get("notify_expired", True):
            key_threshold = -1
            applicable = applicable + [-1]
        elif applicable:
            key_threshold = min(applicable)
        if key_threshold is None or (it["id"], key_threshold) in sent:
            continue
        # alle größeren Stufen gleich mit abhaken -> keine Mehrfach-Nachrichten
        for t in applicable:
            marks.append((it["id"], t))
        name = it["name"] + (f" ({it['brand']})" if it["brand"] else "")
        loc = f" · {it['location']}" if it["location"] else ""
        exp = date.fromisoformat(it["expiry_date"]).strftime("%d.%m.%Y")
        due_lines.append((days, f"• {it['quantity']} {it['unit']} {name}{loc} – {_fmt_days(days)} ({exp})"))

    parts = []
    expired = [l for d, l in due_lines if d < 0]
    soon = [l for d, l in due_lines if d >= 0]
    if expired:
        parts.append("⛔ Abgelaufen:\n" + "\n".join(expired))
    if soon:
        parts.append("⏰ Bald verbrauchen:\n" + "\n".join(soon))
    if parts and cfg.get("include_low_stock"):
        low = inventory.shopping_list(conn)
        if low:
            parts.append("🛒 Nachkaufen:\n" + "\n".join(
                f"• {p['name']} (noch {p['total']}, Ziel {p['min_stock']})" for p in low[:15]))
    return ("\n\n".join(parts), marks)


def run_check(force: bool = False) -> dict:
    with db.session() as conn:
        cfg = db.get_setting(conn, "notify")
        if not cfg.get("enabled") and not force:
            return {"skipped": "deaktiviert"}
        text, marks = build_digest(conn, cfg)
        if not text:
            return {"sent": [], "errors": [], "message": "Nichts fällig 🎉"}
        result = send(cfg, TITLE + " – Vorrat prüfen", text)
        if result["sent"]:
            with db.tx(conn):
                conn.executemany(
                    "INSERT OR IGNORE INTO notification_log(stock_id, threshold) VALUES (?,?)", marks)
        elif not result["errors"]:
            result["errors"].append("Kein Benachrichtigungskanal eingerichtet")
        result["message"] = text
        return result
