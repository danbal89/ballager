"""Hintergrund-Aufgaben: tägliche Erinnerung, tägliche Sicherung, HA-Sensoren."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

from . import backup, db, ha, inventory, notify, prep

log = logging.getLogger("ballager.scheduler")

_wake = threading.Event()


def trigger_sensor_update() -> None:
    _wake.set()


def update_sensors() -> None:
    if not ha.available():
        return
    with db.session() as conn:
        soon_days = int(db.get_setting(conn, "ui").get("soon_days", 30))
        s = inventory.stats(conn, soon_days)
        exp = inventory.expiring(conn, soon_days, 20)
        low = inventory.shopping_list(conn)
    items = [f"{e['quantity']}× {e['name']} ({e['expiry_date']})" for e in exp]
    ha.set_state("sensor.ballager_bald_ablaufend", s["soon"], {
        "friendly_name": "BALLager bald ablaufend", "icon": "mdi:clock-alert-outline",
        "unit_of_measurement": "Posten", "tage": soon_days, "artikel": items})
    ha.set_state("sensor.ballager_abgelaufen", s["expired"], {
        "friendly_name": "BALLager abgelaufen", "icon": "mdi:alert-circle-outline", "unit_of_measurement": "Posten"})
    ha.set_state("sensor.ballager_vorrat", s["units"], {
        "friendly_name": "BALLager Vorrat", "icon": "mdi:archive-outline", "unit_of_measurement": "Stk",
        "produkte": s["products"]})
    ha.set_state("sensor.ballager_einkaufsliste", len(low), {
        "friendly_name": "BALLager Einkaufsliste", "icon": "mdi:cart-outline", "unit_of_measurement": "Produkte",
        "artikel": [p["name"] for p in low]})


def _tick() -> None:
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    with db.session() as conn:
        cfg = db.get_setting(conn, "notify")
        state = db.get_setting(conn, "state")
    if state.get("last_backup_date") != today:
        try:
            backup.run_daily()
        except Exception:
            log.exception("Sicherung fehlgeschlagen")
        _save_state("last_backup_date", today)
    if cfg.get("enabled") and now >= (cfg.get("time") or "08:00") and state.get("last_notify_date") != today:
        try:
            res = notify.run_check()
            log.info("Tägliche Prüfung: %s", {k: v for k, v in res.items() if k != "message"})
        except Exception:
            log.exception("Tägliche Prüfung fehlgeschlagen")
        _save_state("last_notify_date", today)


def _save_state(key: str, value: str) -> None:
    with db.session() as conn:
        with db.tx(conn):
            st = db.get_setting(conn, "state")
            st[key] = value
            db.set_setting(conn, "state", st)


# Vorsorge-Abgleich im Hintergrund: höchstens 10 Produkte alle 2 Minuten, 1 s Pause je Anfrage
ABGLEICH_BATCH = 10
_next_abgleich = time.time() + 60


def abgleich_tick() -> dict | None:
    """Ein Schritt des Hintergrund-Abgleichs. Gibt das Ergebnis zurück (oder None, wenn nicht fällig)."""
    global _next_abgleich
    now = time.time()
    if now < _next_abgleich:
        return None
    try:
        res = prep.abgleich(limit=ABGLEICH_BATCH, pause=1.0)
    except Exception:
        log.exception("Vorsorge-Abgleich fehlgeschlagen")
        _next_abgleich = now + 1800
        return None
    if res["offline"]:
        _next_abgleich = now + 1800      # kein Internet: in 30 Minuten wieder
    elif res["remaining"] > 0:
        _next_abgleich = now + 120       # weiter in 2 Minuten
    else:
        _next_abgleich = now + 3600      # alles erledigt: stündlich nach Neuem schauen
    if res["assigned"]:
        trigger_sensor_update()
    return res


def _loop() -> None:
    last_sensor = 0.0
    while True:
        try:
            _tick()
        except Exception:
            log.exception("Fehler im Zeitplaner")
        abgleich_tick()
        if _wake.is_set() or time.time() - last_sensor > 600:
            _wake.clear()
            try:
                update_sensors()
            except Exception as exc:
                log.debug("Sensor-Update fehlgeschlagen: %s", exc)
            last_sensor = time.time()
        _wake.wait(60)


def start() -> None:
    threading.Thread(target=_loop, name="ballager-scheduler", daemon=True).start()
