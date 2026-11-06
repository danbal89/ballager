"""Zugriff auf Home Assistant über die Supervisor-API (nur im Add-on verfügbar)."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Optional

log = logging.getLogger("ballager.ha")

TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
BASE = os.environ.get("BALLAGER_HA_URL", "http://supervisor/core/api")


def available() -> bool:
    return bool(TOKEN)


def _call(method: str, path: str, payload: Optional[dict] = None, timeout: int = 10) -> Any:
    if not TOKEN:
        raise RuntimeError("Home Assistant API nicht verfügbar (läuft BALLager als Add-on?)")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def notify_services() -> list[str]:
    """Liefert alle notify.* Dienste plus persistent_notification.create."""
    out = ["persistent_notification.create"]
    for domain in _call("GET", "/services") or []:
        if domain.get("domain") == "notify":
            for name in sorted((domain.get("services") or {}).keys()):
                if name not in ("send_message",):
                    out.append(f"notify.{name}")
    return out


def call_service(full_name: str, title: str, message: str) -> None:
    domain, _, service = full_name.partition(".")
    if not domain or not service:
        raise ValueError(f"Ungültiger Dienst: {full_name}")
    payload: dict[str, Any] = {"title": title, "message": message}
    if domain == "persistent_notification":
        payload["notification_id"] = "ballager"
    _call("POST", f"/services/{domain}/{service}", payload)


def set_state(entity_id: str, state: Any, attributes: dict) -> None:
    _call("POST", f"/states/{entity_id}", {"state": state, "attributes": attributes})
