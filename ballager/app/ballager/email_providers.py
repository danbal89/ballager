"""E-Mail-Anbieter: Servereinstellungen automatisch aus der E-Mail-Adresse ableiten.

Jeder Anbieter hat eine Liste von Server-Kandidaten. Klappt die Verbindung zum ersten nicht
(Port gesperrt, Server umbenannt …), wird automatisch der nächste probiert.
Anmeldefehler werden NICHT mit anderen Servern wiederholt, sondern verständlich gemeldet.
"""
from __future__ import annotations

from typing import Optional

# id: name, domains, servers[(host, port, security)], help (App-Passwort), url, supported, note
PROVIDERS: dict[str, dict] = {
    "gmail": {
        "app_password": True,
        "name": "Gmail",
        "domains": ["gmail.com", "googlemail.com"],
        "servers": [("smtp.gmail.com", 587, "starttls"), ("smtp.gmail.com", 465, "ssl")],
        "help": "Google verlangt ein App-Passwort: Google-Konto → Sicherheit → Bestätigung in zwei Schritten "
                "einschalten → „App-Passwörter“ → Name „BALLager“ → das 16-stellige Passwort hier einfügen.",
        "url": "https://myaccount.google.com/apppasswords",
    },
    "a1": {
        "name": "A1",
        "domains": ["a1.net", "aon.at"],
        "servers": [("smtp.a1.net", 587, "starttls"), ("smtp.a1.net", 465, "ssl"), ("securemail.a1.net", 587, "starttls")],
        "help": "Euer normales A1-Mail-Passwort verwenden. Bitte einmal mit „Testnachricht“ prüfen.",
        "url": "https://www.a1.net/hilfe-support",
    },
    "gmx": {
        "name": "GMX",
        "domains": ["gmx.at", "gmx.net", "gmx.de", "gmx.ch", "gmx.com", "gmx.eu"],
        "servers": [("mail.gmx.net", 587, "starttls"), ("mail.gmx.net", 465, "ssl")],
        "help": "Bei GMX einmalig erlauben: E-Mail → Einstellungen → POP3 & IMAP → „Zugriff über externe Programme“ "
                "aktivieren. Mit Zwei-Faktor-Schutz ein App-Passwort anlegen, sonst das normale Passwort.",
        "url": "https://hilfe.gmx.net/pop-imap/einschalten.html",
    },
    "webde": {
        "name": "WEB.DE",
        "domains": ["web.de"],
        "servers": [("smtp.web.de", 587, "starttls"), ("smtp.web.de", 465, "ssl")],
        "help": "Bei WEB.DE einmalig erlauben: E-Mail → Einstellungen → POP3/IMAP Abruf → „Zugriff erlauben“. "
                "Mit Zwei-Faktor-Schutz ein App-Passwort anlegen, sonst das normale Passwort.",
        "url": "https://hilfe.web.de/pop-imap/einschalten.html",
    },
    "icloud": {
        "app_password": True,
        "name": "iCloud",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "servers": [("smtp.mail.me.com", 587, "starttls")],
        "help": "Apple verlangt ein app-spezifisches Passwort: account.apple.com → Anmeldung und Sicherheit → "
                "App-spezifische Passwörter → „BALLager“ → Passwort hier einfügen.",
        "url": "https://account.apple.com",
    },
    "yahoo": {
        "app_password": True,
        "name": "Yahoo",
        "domains": ["yahoo.com", "yahoo.de", "yahoo.at", "ymail.com"],
        "servers": [("smtp.mail.yahoo.com", 465, "ssl"), ("smtp.mail.yahoo.com", 587, "starttls")],
        "help": "Yahoo verlangt ein App-Passwort: Kontoinformationen → Kontosicherheit → „App-Passwort erstellen“.",
        "url": "https://login.yahoo.com/account/security",
    },
    "posteo": {
        "name": "Posteo",
        "domains": ["posteo.de", "posteo.at", "posteo.net", "posteo.eu"],
        "servers": [("posteo.de", 587, "starttls"), ("posteo.de", 465, "ssl")],
        "help": "Normales Posteo-Passwort verwenden.",
        "url": "https://posteo.de/hilfe",
    },
    "mailbox": {
        "name": "mailbox.org",
        "domains": ["mailbox.org"],
        "servers": [("smtp.mailbox.org", 587, "starttls"), ("smtp.mailbox.org", 465, "ssl")],
        "help": "Normales Passwort oder ein Anwendungspasswort aus den mailbox.org-Einstellungen verwenden.",
        "url": "https://mailbox.org",
    },
    "tonline": {
        "name": "T-Online",
        "domains": ["t-online.de", "magenta.de"],
        "servers": [("securesmtp.t-online.de", 587, "starttls"), ("securesmtp.t-online.de", 465, "ssl")],
        "help": "T-Online verlangt ein eigenes „E-Mail-Passwort“ (Kundencenter → E-Mail-Einstellungen), nicht das Login-Passwort.",
        "url": "https://email.t-online.de",
    },
    "outlook": {
        "name": "Outlook / Hotmail",
        "domains": ["outlook.com", "outlook.de", "outlook.at", "hotmail.com", "hotmail.de", "live.com", "live.at",
                    "live.de", "msn.com"],
        "servers": [],
        "supported": False,
        "help": "Microsoft erlaubt bei privaten Outlook-/Hotmail-Konten seit 2024 keinen Versand mit Passwort oder "
                "App-Passwort mehr. Bitte stattdessen Push über die Home-Assistant-App nutzen oder eine andere "
                "Absender-Adresse (z.B. Gmail, A1, GMX) – Empfänger darf trotzdem eine Outlook-Adresse sein.",
        "url": "https://support.microsoft.com/office/f4202ebf-89c6-4a8a-bec3-3d60cf7deaef",
    },
}


def detect(address: str) -> Optional[str]:
    domain = (address or "").strip().lower().rpartition("@")[2]
    for pid, p in PROVIDERS.items():
        if domain in p["domains"]:
            return pid
    return None


def public_list() -> list[dict]:
    """Für die Oberfläche (ohne interne Server-Listen)."""
    return [{"id": pid, "name": p["name"], "domains": p["domains"], "help": p["help"], "url": p["url"],
             "supported": p.get("supported", True), "app_password": p.get("app_password", False)} for pid, p in PROVIDERS.items()]


def is_custom(cfg: dict) -> bool:
    """Eigene Servereinstellungen? (auch für Konfigurationen aus Version 1.0.x ohne Adressfeld)"""
    prov = cfg.get("provider") or "auto"
    if prov == "custom":
        return True
    return prov == "auto" and not cfg.get("address") and bool(cfg.get("host"))


def resolve(cfg: dict) -> dict:
    """Liefert {servers, username, password, sender, recipients, provider} für den Versand."""
    recipients_raw = str(cfg.get("recipients") or "")
    if is_custom(cfg):
        recipients = _split(recipients_raw)
        return {
            "provider": None,
            "servers": [(cfg.get("host") or "", int(cfg.get("port") or 587), cfg.get("security") or "starttls")],
            "username": cfg.get("username") or "",
            "password": cfg.get("password") or "",
            "sender": cfg.get("sender") or cfg.get("username") or "",
            "recipients": recipients,
        }
    address = (cfg.get("address") or "").strip()
    prov = cfg.get("provider") or "auto"
    pid = detect(address) if prov == "auto" else prov
    if not address or "@" not in address:
        raise ValueError("E-Mail: bitte eure E-Mail-Adresse eintragen")
    if pid not in PROVIDERS:
        raise ValueError("E-Mail: Anbieter nicht erkannt – bitte „Andere (eigener Server)“ wählen")
    p = PROVIDERS[pid]
    if not p.get("supported", True):
        raise ValueError(f"E-Mail: {p['help']}")
    return {
        "provider": pid,
        "servers": list(p["servers"]),
        "username": address,
        "password": cfg.get("password") or "",
        "sender": address,
        "recipients": _split(recipients_raw) or [address],
    }


def _split(raw: str) -> list[str]:
    return [r.strip() for r in raw.replace(";", ",").split(",") if r.strip()]
