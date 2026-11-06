"""Fachlogik: Einlagern, Entnehmen (zuerst ablaufende Ware zuerst), Auswertungen."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from typing import Optional

from . import db

log = logging.getLogger("ballager.inventory")


class ValidationError(ValueError):
    pass


def _log(conn, product_id: int, location_id, delta: int, kind: str, expiry) -> None:
    name = conn.execute("SELECT name FROM products WHERE id=?", (product_id,)).fetchone()
    conn.execute(
        "INSERT INTO movements(product_id, product_name, location_id, delta, kind, expiry_date) VALUES (?,?,?,?,?,?)",
        (product_id, name["name"] if name else "", location_id, delta, kind, expiry),
    )


def normalize_date(value) -> Optional[str]:
    if value in (None, "", "null"):
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        raise ValidationError("Ungültiges Datum – bitte JJJJ-MM-TT verwenden")


def add_stock(conn: sqlite3.Connection, product_id: int, quantity: int, location_id=None,
              expiry_date=None, note: str = "", kind: str = "einlagern") -> int:
    """Lagert ein. Gleiches Produkt + Ort + Ablaufdatum wird zu einer Charge zusammengefasst."""
    quantity = int(quantity)
    if quantity <= 0:
        raise ValidationError("Menge muss mindestens 1 sein")
    expiry = normalize_date(expiry_date)
    location_id = int(location_id) if location_id not in (None, "") else None
    if not conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
        raise ValidationError("Produkt nicht gefunden")
    existing = conn.execute(
        "SELECT id FROM stock WHERE product_id=? AND location_id IS ? AND expiry_date IS ?",
        (product_id, location_id, expiry),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE stock SET quantity = quantity + ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (quantity, existing["id"]),
        )
        stock_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO stock(product_id, location_id, quantity, expiry_date, note) VALUES (?,?,?,?,?)",
            (product_id, location_id, quantity, expiry, note or ""),
        )
        stock_id = cur.lastrowid
    _log(conn, product_id, location_id, quantity, kind, expiry)
    return stock_id


def take_from_stock(conn, stock_id: int, amount: int, kind: str = "verbrauch") -> dict:
    amount = int(amount)
    row = conn.execute("SELECT * FROM stock WHERE id=?", (stock_id,)).fetchone()
    if not row:
        raise ValidationError("Posten nicht gefunden")
    if amount <= 0:
        raise ValidationError("Menge muss mindestens 1 sein")
    taken = min(amount, row["quantity"])
    if taken >= row["quantity"]:
        conn.execute("DELETE FROM stock WHERE id=?", (stock_id,))
    else:
        conn.execute(
            "UPDATE stock SET quantity = quantity - ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (taken, stock_id),
        )
    _log(conn, row["product_id"], row["location_id"], -taken, kind, row["expiry_date"])
    return {"product_id": row["product_id"], "location_id": row["location_id"],
            "expiry_date": row["expiry_date"], "quantity": taken}


def consume_product(conn, product_id: int, amount: int, kind: str = "verbrauch",
                    location_id=None) -> list[dict]:
    """Entnimmt nach FEFO-Prinzip (First Expired, First Out)."""
    amount = int(amount)
    if amount <= 0:
        raise ValidationError("Menge muss mindestens 1 sein")
    q = "SELECT id, quantity FROM stock WHERE product_id=?"
    args: list = [product_id]
    if location_id not in (None, ""):
        q += " AND location_id=?"
        args.append(int(location_id))
    q += " ORDER BY expiry_date IS NULL, expiry_date, added_at"
    batches = conn.execute(q, args).fetchall()
    if not batches:
        raise ValidationError("Davon ist nichts mehr im Vorrat")
    taken = []
    for b in batches:
        if amount <= 0:
            break
        part = min(amount, b["quantity"])
        taken.append(take_from_stock(conn, b["id"], part, kind))
        amount -= part
    return taken


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------
def inventory(conn) -> list[dict]:
    products = db.rows(conn.execute(
        """SELECT p.*, c.name AS category_name, c.icon AS category_icon, c.color AS category_color
             FROM products p LEFT JOIN categories c ON c.id = p.category_id
            ORDER BY p.name COLLATE NOCASE"""
    ))
    batches = db.rows(conn.execute(
        """SELECT s.*, l.name AS location_name, l.icon AS location_icon
             FROM stock s LEFT JOIN locations l ON l.id = s.location_id
            ORDER BY s.expiry_date IS NULL, s.expiry_date, s.added_at"""
    ))
    by_product: dict[int, list] = {}
    for b in batches:
        by_product.setdefault(b["product_id"], []).append(b)
    for p in products:
        p["batches"] = by_product.get(p["id"], [])
        p["total"] = sum(b["quantity"] for b in p["batches"])
        dates = [b["expiry_date"] for b in p["batches"] if b["expiry_date"]]
        p["next_expiry"] = min(dates) if dates else None
    return products


def shopping_list(conn) -> list[dict]:
    return db.rows(conn.execute(
        """SELECT p.id, p.name, p.brand, p.unit, p.min_stock,
                  COALESCE(SUM(s.quantity), 0) AS total,
                  c.icon AS category_icon, c.name AS category_name
             FROM products p
             LEFT JOIN stock s ON s.product_id = p.id
             LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.min_stock > 0
            GROUP BY p.id
           HAVING total < p.min_stock
            ORDER BY c.sort_order, p.name COLLATE NOCASE"""
    ))


def stats(conn, soon_days: int = 30) -> dict:
    today = date.today()
    soon = (today + timedelta(days=soon_days)).isoformat()
    t = today.isoformat()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0  # noqa: E731
    return {
        "products": one("SELECT COUNT(DISTINCT product_id) FROM stock"),
        "units": one("SELECT SUM(quantity) FROM stock"),
        "expired": one("SELECT COUNT(*) FROM stock WHERE expiry_date < ?", t),
        "soon": one("SELECT COUNT(*) FROM stock WHERE expiry_date >= ? AND expiry_date <= ?", t, soon),
        "low": len(shopping_list(conn)),
        "soon_days": soon_days,
        "today": t,
    }


def expiring(conn, soon_days: int = 30, limit: int = 50) -> list[dict]:
    soon = (date.today() + timedelta(days=soon_days)).isoformat()
    return db.rows(conn.execute(
        """SELECT s.id, s.product_id, s.quantity, s.expiry_date, p.name, p.brand, p.unit,
                  c.icon AS category_icon, c.color AS category_color,
                  l.name AS location_name, l.icon AS location_icon
             FROM stock s JOIN products p ON p.id = s.product_id
             LEFT JOIN categories c ON c.id = p.category_id
             LEFT JOIN locations l ON l.id = s.location_id
            WHERE s.expiry_date IS NOT NULL AND s.expiry_date <= ?
            ORDER BY s.expiry_date LIMIT ?""",
        (soon, limit),
    ))
