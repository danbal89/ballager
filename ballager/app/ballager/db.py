"""SQLite-Datenbank für BALLager.

Designentscheidungen für langfristigen, stabilen Betrieb:
* SQLite im WAL-Modus (robust gegen Stromausfall, parallele Leser).
* Fremdschlüssel und CHECK-Constraints erzwingen saubere Daten.
* Schema-Versionierung über ``PRAGMA user_version`` mit
  vorwärts gerichteten Migrationen – bestehende Daten bleiben erhalten.
* Jede Bestandsänderung wird in ``movements`` protokolliert.
* Tägliche Online-Sicherung über die SQLite-Backup-API (siehe backup.py).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("ballager.db")

DATA_DIR = os.environ.get("BALLAGER_DATA", "/data")
DB_PATH = os.path.join(DATA_DIR, "ballager.db")

_init_lock = threading.Lock()

# --------------------------------------------------------------------------
# Migrationen: Index + 1 = Schema-Version. Niemals bestehende Einträge ändern,
# immer nur neue anhängen!
# --------------------------------------------------------------------------
MIGRATIONS: list[str] = [
    # ---- Version 1 -------------------------------------------------------
    """
    CREATE TABLE categories (
        id          INTEGER PRIMARY KEY,
        name        TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
        icon        TEXT    NOT NULL DEFAULT '📦',
        color       TEXT    NOT NULL DEFAULT '#5AA9E6',
        sort_order  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE locations (
        id          INTEGER PRIMARY KEY,
        name        TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
        icon        TEXT    NOT NULL DEFAULT '🏠',
        description TEXT    NOT NULL DEFAULT '',
        sort_order  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE products (
        id                  INTEGER PRIMARY KEY,
        barcode             TEXT    UNIQUE,
        name                TEXT    NOT NULL CHECK (length(trim(name)) > 0),
        brand               TEXT    NOT NULL DEFAULT '',
        category_id         INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        default_location_id INTEGER REFERENCES locations(id)  ON DELETE SET NULL,
        unit                TEXT    NOT NULL DEFAULT 'Stk',
        package_size        TEXT    NOT NULL DEFAULT '',
        min_stock           INTEGER NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
        notes               TEXT    NOT NULL DEFAULT '',
        image_url           TEXT    NOT NULL DEFAULT '',
        created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
    CREATE INDEX idx_products_category ON products(category_id);
    CREATE INDEX idx_products_name     ON products(name COLLATE NOCASE);

    -- Ein Bestandsposten ("Charge") = gleiches Produkt, gleicher Ort, gleiches Ablaufdatum
    CREATE TABLE stock (
        id          INTEGER PRIMARY KEY,
        product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
        quantity    INTEGER NOT NULL CHECK (quantity > 0),
        expiry_date TEXT CHECK (expiry_date IS NULL OR
                     expiry_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
        note        TEXT    NOT NULL DEFAULT '',
        added_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
    CREATE INDEX idx_stock_product  ON stock(product_id);
    CREATE INDEX idx_stock_expiry   ON stock(expiry_date);
    CREATE INDEX idx_stock_location ON stock(location_id);

    -- Lückenloses Protokoll aller Bewegungen
    CREATE TABLE movements (
        id           INTEGER PRIMARY KEY,
        product_id   INTEGER REFERENCES products(id) ON DELETE CASCADE,
        product_name TEXT    NOT NULL DEFAULT '',
        location_id  INTEGER REFERENCES locations(id) ON DELETE SET NULL,
        delta        INTEGER NOT NULL,
        kind         TEXT    NOT NULL CHECK (kind IN
                       ('einlagern','verbrauch','entsorgt','korrektur','umlagern')),
        expiry_date  TEXT,
        created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
    CREATE INDEX idx_movements_created ON movements(created_at);
    CREATE INDEX idx_movements_product ON movements(product_id);

    -- Merkt sich, welche Erinnerung für welchen Posten schon verschickt wurde
    CREATE TABLE notification_log (
        stock_id   INTEGER NOT NULL REFERENCES stock(id) ON DELETE CASCADE,
        threshold  INTEGER NOT NULL,
        sent_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        PRIMARY KEY (stock_id, threshold)
    );

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]

DEFAULT_CATEGORIES = [
    ("Konserven", "🥫", "#FF6B5B"),
    ("Mehl & Backen", "🌾", "#FFC145"),
    ("Nudeln, Reis & Getreide", "🍝", "#F4A259"),
    ("Gewürze, Öle & Saucen", "🧂", "#E07A5F"),
    ("Süßes & Snacks", "🍫", "#B5679E"),
    ("Getränke", "🧃", "#5AA9E6"),
    ("Milchprodukte", "🧀", "#F2CC8F"),
    ("Tiefkühl", "❄️", "#7FC8F8"),
    ("Haushalt", "🧻", "#2EC4A5"),
    ("Putzmittel", "🧽", "#43AA8B"),
    ("Hygiene & Drogerie", "🧴", "#9B8DE0"),
    ("Medikamente", "💊", "#EF476F"),
    ("Technik & Batterien", "🔋", "#6C7A99"),
    ("Tierbedarf", "🐾", "#A47551"),
    ("Sonstiges", "📦", "#8D99AE"),
]

DEFAULT_LOCATIONS = [
    ("Speis", "🚪", "Vorratsraum"),
    ("Technikraum", "🔧", ""),
    ("Küche", "🍳", ""),
    ("Kühlschrank", "🧊", ""),
    ("Tiefkühltruhe", "❄️", ""),
    ("Keller", "🏚️", ""),
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "notify": {
        "enabled": True,
        "thresholds": [30, 7, 1],
        "time": "08:00",
        "notify_expired": True,
        "include_low_stock": True,
        "ha_services": [],
        "email": {
            "enabled": False,
            "host": "",
            "port": 587,
            "security": "starttls",
            "username": "",
            "password": "",
            "sender": "",
            "recipients": "",
        },
        "whatsapp": {
            "enabled": False,
            "recipients": [],  # [{"name":..., "phone":..., "apikey":...}]
        },
    },
    "ui": {"soon_days": 30},
    "state": {"last_notify_date": "", "last_backup_date": ""},
}


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    # FULL: jede bestätigte Änderung ist auch nach Stromausfall auf der Platte.
    conn.execute("PRAGMA synchronous = FULL")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    _configure(conn)
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Verbindung für einen Request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explizite Schreib-Transaktion (BEGIN IMMEDIATE vermeidet Deadlocks)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _restore_if_requested() -> None:
    """Liegt /share/ballager/restore.db bereit, wird sie beim Start als Datenbank übernommen."""
    share = os.environ.get("BALLAGER_SHARE", "/share")
    src = os.path.join(share, "ballager", "restore.db")
    if not os.path.isfile(src):
        return
    try:
        test = sqlite3.connect(src)
        ok = test.execute("PRAGMA integrity_check").fetchone()[0]
        test.execute("SELECT COUNT(*) FROM products").fetchone()
        test.execute("PRAGMA journal_mode = DELETE")
        test.close()
        for suffix in ("-wal", "-shm"):
            if os.path.exists(src + suffix):
                os.remove(src + suffix)
        if ok != "ok":
            raise ValueError(ok)
    except Exception as exc:
        log.error("restore.db ist keine gültige BALLager-Datenbank (%s) – nichts geändert.", exc)
        return
    import shutil
    from datetime import datetime
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if os.path.exists(DB_PATH):
        # konsistente Kopie inkl. noch nicht eingearbeiteter WAL-Daten
        safety_copy(os.path.join(DATA_DIR, f"vor-wiederherstellung-{stamp}.db"))
    for suffix in ("-wal", "-shm"):
        if os.path.exists(DB_PATH + suffix):
            os.remove(DB_PATH + suffix)
    shutil.copy2(src, DB_PATH)
    os.replace(src, os.path.join(share, "ballager", f"restore-erledigt-{stamp}.db"))
    log.warning("Datenbank aus restore.db wiederhergestellt.")


def safety_copy(target: str) -> None:
    """Konsistente Einzeldatei-Kopie der laufenden Datenbank (SQLite-Backup-API)."""
    src = connect()
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(target + suffix)
        except OSError:
            pass


# Ergebnis der Prüfung beim Start (für Anzeige in den Einstellungen)
STATUS: dict = {"integrity": "unbekannt", "checked_at": "", "schema": 0, "schema_expected": len(MIGRATIONS)}


def init() -> None:
    with _init_lock:
        from datetime import datetime
        os.makedirs(DATA_DIR, exist_ok=True)
        _restore_if_requested()
        conn = connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            STATUS["integrity"] = ok if not fk else f"{ok}; {len(fk)} verwaiste Verweise"
            STATUS["checked_at"] = datetime.now().isoformat(timespec="seconds")
            if ok != "ok" or fk:
                log.error("Integritätsprüfung meldet Probleme: %s – bitte Sicherung prüfen!", STATUS["integrity"])
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            STATUS["schema"] = version
            if version > len(MIGRATIONS):
                log.error("Die Datenbank (Schema %d) stammt von einer NEUEREN BALLager-Version (kennt nur %d). "
                          "Bitte BALLager aktualisieren – die Daten werden nicht verändert.", version, len(MIGRATIONS))
                STATUS["readonly"] = True
                return
            if 0 < version < len(MIGRATIONS):
                # Vor jeder Schemaänderung durch ein Update: Sicherheitskopie
                target = os.path.join(DATA_DIR, f"vor-update-schema{version}-{datetime.now():%Y-%m-%d_%H%M%S}.db")
                conn.close()
                safety_copy(target)
                conn = connect()
                log.info("Sicherheitskopie vor Update angelegt: %s", target)
            for idx in range(version, len(MIGRATIONS)):
                log.info("Migriere Datenbank auf Version %d", idx + 1)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _split_sql(MIGRATIONS[idx]):
                        conn.execute(stmt)
                    conn.execute(f"PRAGMA user_version = {idx + 1}")
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                STATUS["schema"] = idx + 1
            if version == 0:
                _seed(conn)
            _ensure_settings(conn)
        finally:
            conn.close()


def _split_sql(script: str) -> list[str]:
    """Zerlegt ein Migrationsskript in Einzelbefehle.

    Trennt an jedem ';', an dem SQLite den Befehl für vollständig hält – also auch mehrere
    Befehle pro Zeile und ';' innerhalb von Texten ('a;b') werden korrekt behandelt.
    """
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    parts, buf = [], ""
    for ch in "\n".join(lines):
        buf += ch
        if ch == ";" and sqlite3.complete_statement(buf):
            if buf.strip().strip(";").strip():
                parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())  # unvollständig -> SQLite meldet beim Ausführen den Fehler
    return parts


def _seed(conn: sqlite3.Connection) -> None:
    with tx(conn):
        for i, (name, icon, color) in enumerate(DEFAULT_CATEGORIES):
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, icon, color, sort_order) VALUES (?,?,?,?)",
                (name, icon, color, i),
            )
        for i, (name, icon, desc) in enumerate(DEFAULT_LOCATIONS):
            conn.execute(
                "INSERT OR IGNORE INTO locations(name, icon, description, sort_order) VALUES (?,?,?,?)",
                (name, icon, desc, i),
            )


def _merge(defaults: Any, current: Any) -> Any:
    if isinstance(defaults, dict) and isinstance(current, dict):
        out = dict(defaults)
        for k, v in current.items():
            out[k] = _merge(defaults.get(k), v) if k in defaults else v
        return out
    return defaults if current is None else current


def _ensure_settings(conn: sqlite3.Connection) -> None:
    with tx(conn):
        for key, default in DEFAULT_SETTINGS.items():
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            current = json.loads(row["value"]) if row else None
            merged = _merge(default, current)
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(merged, ensure_ascii=False)),
            )


def get_setting(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return json.loads(json.dumps(DEFAULT_SETTINGS.get(key)))
    return _merge(DEFAULT_SETTINGS.get(key), json.loads(row["value"]))


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
