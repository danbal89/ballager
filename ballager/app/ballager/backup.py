"""Automatische Datenbank-Sicherungen (konsistent im laufenden Betrieb)."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date

from . import db

log = logging.getLogger("ballager.backup")

KEEP_DAILY = 14
KEEP_MONTHLY = 24


def backup_dir() -> str:
    share = os.environ.get("BALLAGER_SHARE", "/share")
    if os.path.isdir(share) and os.access(share, os.W_OK):
        path = os.path.join(share, "ballager", "backups")
    else:
        path = os.path.join(db.DATA_DIR, "backups")
    os.makedirs(path, exist_ok=True)
    return path


def snapshot(target: str) -> None:
    src = db.connect()
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        # eigenständige Einzeldatei ohne -wal/-shm, leicht zu kopieren und wiederherzustellen
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()
        cleanup_sidecars(target)


def cleanup_sidecars(path: str) -> None:
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def run_daily() -> str:
    d = backup_dir()
    today = date.today()
    target = os.path.join(d, f"ballager-{today.isoformat()}.db")
    tmp = target + ".tmp"
    snapshot(tmp)
    os.replace(tmp, target)
    if today.day == 1:
        monthly = os.path.join(d, f"ballager-monat-{today.strftime('%Y-%m')}.db")
        snapshot(monthly)
    _prune(d)
    log.info("Sicherung erstellt: %s", target)
    return target


def _prune(d: str) -> None:
    files = sorted(os.listdir(d))
    daily = [f for f in files if f.startswith("ballager-") and not f.startswith("ballager-monat-") and f.endswith(".db")]
    monthly = [f for f in files if f.startswith("ballager-monat-") and f.endswith(".db")]
    for f in daily[:-KEEP_DAILY] + monthly[:-KEEP_MONTHLY]:
        try:
            os.remove(os.path.join(d, f))
        except OSError as exc:
            log.warning("Konnte alte Sicherung %s nicht löschen: %s", f, exc)


def list_backups() -> list[dict]:
    d = backup_dir()
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if f.endswith(".db"):
            st = os.stat(os.path.join(d, f))
            out.append({"name": f, "size": st.st_size})
    return out
