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
    """Eigenständige Einzeldatei ohne -wal/-shm, leicht zu kopieren und wiederherzustellen."""
    db.safety_copy(target)


def verify(path: str) -> None:
    """Prüft, dass eine Sicherung vollständig lesbar ist – sonst Ausnahme."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RuntimeError(f"Sicherung beschädigt: {ok}")
        conn.execute("SELECT COUNT(*) FROM products").fetchone()
        conn.execute("SELECT COUNT(*) FROM stock").fetchone()
    finally:
        conn.close()


def run_daily() -> str:
    d = backup_dir()
    today = date.today()
    target = os.path.join(d, f"ballager-{today.isoformat()}.db")
    tmp = target + ".tmp"
    snapshot(tmp)
    try:
        verify(tmp)
    except Exception:
        os.remove(tmp)
        raise  # alte Sicherung bleibt unangetastet
    os.replace(tmp, target)
    if today.day == 1:
        monthly = os.path.join(d, f"ballager-monat-{today.strftime('%Y-%m')}.db")
        if not os.path.exists(monthly):
            snapshot(monthly)
    _prune(d)
    _prune_safety_copies()
    try:
        with db.session() as conn:
            conn.execute("PRAGMA optimize")
    except Exception:  # nur Optimierung, nie kritisch
        pass
    log.info("Sicherung erstellt und geprüft: %s", target)
    return target


def _prune_safety_copies(keep: int = 10) -> None:
    """Sicherheitskopien vor Updates/Wiederherstellungen: die letzten 10 je Art behalten."""
    try:
        files = sorted(os.listdir(db.DATA_DIR))
    except OSError:
        return
    for prefix in ("vor-update-", "vor-wiederherstellung-"):
        old = [f for f in files if f.startswith(prefix) and f.endswith(".db")]
        for f in old[:-keep]:
            try:
                os.remove(os.path.join(db.DATA_DIR, f))
            except OSError:
                pass


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
