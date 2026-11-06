"""REST-API und Auslieferung der Weboberfläche."""
from __future__ import annotations

import copy
import csv
import io
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from . import __version__, backup, db, ha, inventory, lookup, notify, scheduler
from .inventory import ValidationError

log = logging.getLogger("ballager.web")

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
SECRET = "••••••"

app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------
def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _int_or_none(v):
    return int(v) if v not in (None, "", "null") else None


def _str(v, maxlen=500) -> str:
    return str(v or "").strip()[:maxlen]


@app.errorhandler(ValidationError)
def _validation(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(sqlite3.IntegrityError)
def _integrity(e):
    msg = str(e)
    if "UNIQUE" in msg and "barcode" in msg:
        msg = "Dieser Barcode ist bereits einem anderen Produkt zugeordnet."
    elif "UNIQUE" in msg:
        msg = "Diesen Namen gibt es schon."
    elif "CHECK" in msg:
        msg = "Ungültige Eingabe."
    return jsonify(error=msg), 400


INGRESS_IP = "172.30.32.2"
_GUARD = ha.available() and os.environ.get("BALLAGER_DIRECT_ACCESS") != "1"


@app.before_request
def _only_ingress():
    """Im Add-on nur Zugriffe über Home Assistant (Ingress, mit HA-Anmeldung) zulassen."""
    if _GUARD and request.path != "/api/health" and request.remote_addr not in (INGRESS_IP, "127.0.0.1"):
        return jsonify(error="Bitte BALLager über Home Assistant öffnen"), 403
    return None


@app.get("/api/health")
def health():
    with db.session() as conn:
        conn.execute("SELECT 1").fetchone()
    return jsonify(ok=True, version=__version__)


@app.after_request
def _no_cache(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
        if request.method != "GET" and resp.status_code < 400:
            scheduler.trigger_sensor_update()
    return resp


# --------------------------------------------------------------------------
# Oberfläche
# --------------------------------------------------------------------------
@app.get("/")
def index():
    resp = send_from_directory(STATIC, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/static/<path:name>")
def static_files(name):
    resp = send_from_directory(STATIC, name)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# --------------------------------------------------------------------------
# Stammdaten / Übersicht
# --------------------------------------------------------------------------
@app.get("/api/meta")
def meta():
    with db.session() as conn:
        return jsonify(
            version=__version__,
            ha=ha.available(),
            categories=db.rows(conn.execute("SELECT * FROM categories ORDER BY sort_order, name")),
            locations=db.rows(conn.execute("SELECT * FROM locations ORDER BY sort_order, name")),
            ui=db.get_setting(conn, "ui"),
        )


@app.get("/api/dashboard")
def dashboard():
    with db.session() as conn:
        soon_days = int(db.get_setting(conn, "ui").get("soon_days", 30))
        recent = db.rows(conn.execute(
            """SELECT m.*, l.name AS location_name FROM movements m
                 LEFT JOIN locations l ON l.id = m.location_id
                ORDER BY m.id DESC LIMIT 8"""))
        return jsonify(
            stats=inventory.stats(conn, soon_days),
            expiring=inventory.expiring(conn, soon_days, 12),
            shopping=inventory.shopping_list(conn)[:8],
            recent=recent,
        )


@app.get("/api/inventory")
def get_inventory():
    with db.session() as conn:
        return jsonify(items=inventory.inventory(conn))


@app.get("/api/shopping")
def shopping():
    with db.session() as conn:
        return jsonify(items=inventory.shopping_list(conn))


@app.get("/api/history")
def history():
    limit = min(int(request.args.get("limit", 200)), 2000)
    with db.session() as conn:
        return jsonify(items=db.rows(conn.execute(
            """SELECT m.*, l.name AS location_name FROM movements m
                 LEFT JOIN locations l ON l.id = m.location_id
                ORDER BY m.id DESC LIMIT ?""", (limit,))))


# --------------------------------------------------------------------------
# Produkte
# --------------------------------------------------------------------------
PRODUCT_FIELDS = ("barcode", "name", "brand", "category_id", "default_location_id", "unit",
                  "package_size", "min_stock", "notes", "image_url")


def _product_values(d: dict) -> dict:
    out = {}
    for f in PRODUCT_FIELDS:
        if f not in d:
            continue
        v = d[f]
        if f in ("category_id", "default_location_id"):
            out[f] = _int_or_none(v)
        elif f == "min_stock":
            out[f] = max(0, int(v or 0))
        elif f == "barcode":
            out[f] = "".join(ch for ch in str(v or "") if ch.isalnum())[:64] or None
        elif f == "unit":
            out[f] = _str(v, 20) or "Stk"
        else:
            out[f] = _str(v, 1000)
    return out


def _get_product(conn, pid: int) -> dict:
    items = [p for p in inventory.inventory(conn) if p["id"] == pid]
    if not items:
        abort(404)
    return items[0]


@app.get("/api/barcode/<code>")
def by_barcode(code):
    code = "".join(ch for ch in code if ch.isalnum())
    with db.session() as conn:
        row = conn.execute("SELECT id FROM products WHERE barcode=?", (code,)).fetchone()
        if row:
            return jsonify(found=True, product=_get_product(conn, row["id"]))
        suggestion = lookup.lookup(code)
        if suggestion and suggestion.get("category_name"):
            cat = conn.execute("SELECT id FROM categories WHERE name=?", (suggestion["category_name"],)).fetchone()
            suggestion["category_id"] = cat["id"] if cat else None
        return jsonify(found=False, barcode=code, suggestion=suggestion)


@app.post("/api/products")
def create_product():
    d = _product_values(body())
    if not d.get("name"):
        raise ValidationError("Bitte einen Namen eingeben")
    initial = body().get("stock")
    with db.session() as conn:
        with db.tx(conn):
            cols = ", ".join(d.keys())
            cur = conn.execute(f"INSERT INTO products({cols}) VALUES ({', '.join('?' * len(d))})", list(d.values()))
            pid = cur.lastrowid
            if isinstance(initial, dict) and int(initial.get("quantity") or 0) > 0:
                inventory.add_stock(conn, pid, initial["quantity"], initial.get("location_id"),
                                    initial.get("expiry_date"), _str(initial.get("note")))
        return jsonify(product=_get_product(conn, pid)), 201


@app.put("/api/products/<int:pid>")
def update_product(pid):
    d = _product_values(body())
    if "name" in d and not d["name"]:
        raise ValidationError("Bitte einen Namen eingeben")
    with db.session() as conn:
        with db.tx(conn):
            if d:
                sets = ", ".join(f"{k}=?" for k in d)
                conn.execute(f"UPDATE products SET {sets}, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                             [*d.values(), pid])
        return jsonify(product=_get_product(conn, pid))


@app.delete("/api/products/<int:pid>")
def delete_product(pid):
    with db.session() as conn:
        with db.tx(conn):
            conn.execute("UPDATE movements SET product_id=NULL WHERE product_id=?", (pid,))
            conn.execute("DELETE FROM products WHERE id=?", (pid,))
    return jsonify(ok=True)


@app.post("/api/products/<int:pid>/consume")
def consume(pid):
    d = body()
    kind = d.get("kind") if d.get("kind") in ("verbrauch", "entsorgt") else "verbrauch"
    with db.session() as conn:
        with db.tx(conn):
            taken = inventory.consume_product(conn, pid, int(d.get("amount") or 1), kind, d.get("location_id"))
        return jsonify(taken=taken, product=_get_product(conn, pid))


# --------------------------------------------------------------------------
# Bestand (Chargen)
# --------------------------------------------------------------------------
@app.post("/api/stock")
def add_stock():
    d = body()
    kind = d.get("kind") if d.get("kind") in ("einlagern", "korrektur") else "einlagern"
    with db.session() as conn:
        with db.tx(conn):
            sid = inventory.add_stock(conn, int(d.get("product_id") or 0), int(d.get("quantity") or 0),
                                      d.get("location_id"), d.get("expiry_date"), _str(d.get("note")), kind)
        return jsonify(stock_id=sid, product=_get_product(conn, int(d["product_id"]))), 201


@app.post("/api/stock/undo")
def undo():
    """Macht eine Entnahme rückgängig (Liste aus 'taken')."""
    items = body().get("items") or []
    with db.session() as conn:
        with db.tx(conn):
            for it in items:
                inventory.add_stock(conn, int(it["product_id"]), int(it["quantity"]), it.get("location_id"),
                                    it.get("expiry_date"), kind="korrektur")
    return jsonify(ok=True)


@app.put("/api/stock/<int:sid>")
def update_stock(sid):
    d = body()
    with db.session() as conn:
        with db.tx(conn):
            row = conn.execute("SELECT * FROM stock WHERE id=?", (sid,)).fetchone()
            if not row:
                abort(404)
            qty = int(d.get("quantity", row["quantity"]))
            loc = _int_or_none(d["location_id"]) if "location_id" in d else row["location_id"]
            exp = inventory.normalize_date(d["expiry_date"]) if "expiry_date" in d else row["expiry_date"]
            note = _str(d.get("note", row["note"]))
            if qty <= 0:
                inventory.take_from_stock(conn, sid, row["quantity"], "korrektur")
            else:
                conn.execute(
                    "UPDATE stock SET quantity=?, location_id=?, expiry_date=?, note=?, "
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (qty, loc, exp, note, sid))
                if qty != row["quantity"]:
                    inventory._log(conn, row["product_id"], loc, qty - row["quantity"], "korrektur", exp)
                if loc != row["location_id"]:
                    inventory._log(conn, row["product_id"], loc, 0, "umlagern", exp)
                if exp != row["expiry_date"]:
                    conn.execute("DELETE FROM notification_log WHERE stock_id=?", (sid,))
                # ggf. mit gleicher Charge zusammenführen
                twin = conn.execute(
                    "SELECT id FROM stock WHERE product_id=? AND location_id IS ? AND expiry_date IS ? AND id<>?",
                    (row["product_id"], loc, exp, sid)).fetchone()
                if twin:
                    conn.execute("UPDATE stock SET quantity=quantity+? WHERE id=?", (qty, twin["id"]))
                    conn.execute("DELETE FROM stock WHERE id=?", (sid,))
        return jsonify(product=_get_product(conn, row["product_id"]))


@app.post("/api/stock/<int:sid>/take")
def take(sid):
    d = body()
    kind = d.get("kind") if d.get("kind") in ("verbrauch", "entsorgt", "korrektur") else "verbrauch"
    with db.session() as conn:
        with db.tx(conn):
            t = inventory.take_from_stock(conn, sid, int(d.get("amount") or 1), kind)
        return jsonify(taken=[t], product=_get_product(conn, t["product_id"]))


# --------------------------------------------------------------------------
# Kategorien & Lagerorte
# --------------------------------------------------------------------------
def _crud(table: str, fields: tuple):
    def create():
        d = body()
        vals = {f: _str(d.get(f), 200) for f in fields if f in d}
        if not vals.get("name"):
            raise ValidationError("Bitte einen Namen eingeben")
        with db.session() as conn:
            with db.tx(conn):
                nxt = conn.execute(f"SELECT COALESCE(MAX(sort_order),0)+1 FROM {table}").fetchone()[0]
                vals["sort_order"] = nxt
                conn.execute(f"INSERT INTO {table}({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                             list(vals.values()))
        return jsonify(ok=True), 201

    def update(oid):
        d = body()
        vals = {f: _str(d.get(f), 200) for f in fields if f in d}
        if "sort_order" in d:
            vals["sort_order"] = int(d["sort_order"])
        if "name" in vals and not vals["name"]:
            raise ValidationError("Bitte einen Namen eingeben")
        with db.session() as conn:
            with db.tx(conn):
                if vals:
                    conn.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in vals)} WHERE id=?",
                                 [*vals.values(), oid])
        return jsonify(ok=True)

    def delete(oid):
        with db.session() as conn:
            with db.tx(conn):
                conn.execute(f"DELETE FROM {table} WHERE id=?", (oid,))
        return jsonify(ok=True)

    app.add_url_rule(f"/api/{table}", f"{table}_create", create, methods=["POST"])
    app.add_url_rule(f"/api/{table}/<int:oid>", f"{table}_update", update, methods=["PUT"])
    app.add_url_rule(f"/api/{table}/<int:oid>", f"{table}_delete", delete, methods=["DELETE"])


_crud("categories", ("name", "icon", "color"))
_crud("locations", ("name", "icon", "description"))


# --------------------------------------------------------------------------
# Einstellungen & Benachrichtigungen
# --------------------------------------------------------------------------
def _mask(cfg: dict) -> dict:
    c = copy.deepcopy(cfg)
    if c["email"].get("password"):
        c["email"]["password"] = SECRET
    for r in c["whatsapp"].get("recipients") or []:
        if r.get("apikey"):
            r["apikey"] = SECRET
    return c


@app.get("/api/settings")
def get_settings():
    with db.session() as conn:
        return jsonify(notify=_mask(db.get_setting(conn, "notify")), ui=db.get_setting(conn, "ui"), ha=ha.available())


@app.put("/api/settings")
def put_settings():
    d = body()
    with db.session() as conn:
        with db.tx(conn):
            if isinstance(d.get("notify"), dict):
                old = db.get_setting(conn, "notify")
                new = d["notify"]
                n = copy.deepcopy(old)
                n["enabled"] = bool(new.get("enabled"))
                n["thresholds"] = sorted({max(0, min(3650, int(t))) for t in new.get("thresholds", []) if str(t).strip() != ""},
                                         reverse=True)
                hhmm = _str(new.get("time"), 5) or "08:00"
                datetime.strptime(hhmm, "%H:%M")
                n["time"] = hhmm
                n["notify_expired"] = bool(new.get("notify_expired"))
                n["include_low_stock"] = bool(new.get("include_low_stock"))
                n["ha_services"] = [_str(s, 120) for s in new.get("ha_services", []) if "." in str(s)]
                em = new.get("email") or {}
                for k in ("host", "username", "sender", "recipients", "security"):
                    n["email"][k] = _str(em.get(k), 500)
                n["email"]["enabled"] = bool(em.get("enabled"))
                n["email"]["port"] = int(em.get("port") or 587)
                if em.get("password") != SECRET:
                    n["email"]["password"] = str(em.get("password") or "")
                wa = new.get("whatsapp") or {}
                old_keys = {r.get("phone"): r.get("apikey") for r in old["whatsapp"].get("recipients", [])}
                recips = []
                for r in wa.get("recipients", []):
                    phone = _str(r.get("phone"), 30)
                    if not phone:
                        continue
                    key = r.get("apikey")
                    recips.append({"name": _str(r.get("name"), 60), "phone": phone,
                                   "apikey": old_keys.get(phone, "") if key == SECRET else _str(key, 100)})
                n["whatsapp"] = {"enabled": bool(wa.get("enabled")), "recipients": recips}
                db.set_setting(conn, "notify", n)
            if isinstance(d.get("ui"), dict):
                ui = db.get_setting(conn, "ui")
                ui["soon_days"] = max(1, min(365, int(d["ui"].get("soon_days", ui["soon_days"]))))
                db.set_setting(conn, "ui", ui)
    return get_settings()


@app.get("/api/ha/services")
def ha_services():
    if not ha.available():
        return jsonify(services=[], error="Nur im Home-Assistant-Add-on verfügbar")
    try:
        return jsonify(services=ha.notify_services())
    except Exception as exc:
        return jsonify(services=[], error=str(exc))


@app.post("/api/notify/test")
def notify_test():
    with db.session() as conn:
        cfg = db.get_setting(conn, "notify")
    res = notify.send(cfg, notify.TITLE + " – Test",
                      "Hallo! 👋 Das ist eine Testnachricht von BALLager. Benachrichtigungen funktionieren.")
    if not res["sent"] and not res["errors"]:
        res["errors"].append("Kein Benachrichtigungskanal eingerichtet")
    return jsonify(res)


@app.post("/api/notify/run")
def notify_run():
    return jsonify(notify.run_check(force=True))


@app.get("/api/notify/preview")
def notify_preview():
    with db.session() as conn:
        cfg = db.get_setting(conn, "notify")
        text, _ = notify.build_digest(conn, cfg)
    return jsonify(message=text)


# --------------------------------------------------------------------------
# Daten & Sicherung
# --------------------------------------------------------------------------
@app.get("/api/export.csv")
def export_csv():
    with db.session() as conn:
        items = inventory.inventory(conn)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Produkt", "Marke", "Barcode", "Kategorie", "Lagerort", "Menge", "Einheit", "Ablaufdatum", "Mindestbestand", "Notiz"])
    for p in items:
        for b in p["batches"] or [None]:
            w.writerow([p["name"], p["brand"], p["barcode"] or "", p["category_name"] or "",
                        (b or {}).get("location_name") or "", (b or {}).get("quantity", 0), p["unit"],
                        (b or {}).get("expiry_date") or "", p["min_stock"], p["notes"]])
    data = "﻿" + buf.getvalue()  # BOM für Excel
    fn = f"ballager-vorrat-{datetime.now():%Y-%m-%d}.csv"
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={fn}"})


@app.get("/api/backup/download")
def backup_download():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    backup.snapshot(tmp.name)
    fn = f"ballager-{datetime.now():%Y-%m-%d_%H%M}.db"
    resp = send_file(tmp.name, as_attachment=True, download_name=fn, mimetype="application/octet-stream")
    resp.call_on_close(lambda: os.unlink(tmp.name))
    return resp


@app.get("/api/backups")
def backups():
    return jsonify(items=backup.list_backups(), path=backup.backup_dir())


@app.post("/api/backups")
def backup_now():
    path = backup.run_daily()
    return jsonify(ok=True, path=path)
