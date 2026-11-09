"""Strukturierte Tests für BALLager (Backend, Datenbank, Updates, Sicherungen).

Ausführen (aus dem Projektordner):
    python3 -m unittest discover -s tests -v

Benötigt nur Flask + Waitress (siehe ballager/app/requirements.txt).
Jeder Test bekommt eine frische, leere Datenbank in einem Temp-Ordner.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ballager", "app"))

from ballager import backup, db, inventory, lookup, notify  # noqa: E402
from ballager.web import app  # noqa: E402

TODAY = date.today()


def d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ballager-test-")
        self.share = os.path.join(self.tmp, "share")
        os.makedirs(self.share)
        self._env = mock.patch.dict(os.environ, {"BALLAGER_SHARE": self.share})
        self._env.start()
        db.DATA_DIR = os.path.join(self.tmp, "data")
        db.DB_PATH = os.path.join(db.DATA_DIR, "ballager.db")
        db.STATUS.clear()
        db.STATUS.update({"integrity": "unbekannt", "checked_at": "", "schema": 0,
                          "schema_expected": len(db.MIGRATIONS)})
        self._migrations = list(db.MIGRATIONS)
        self._lookup = mock.patch.object(lookup, "lookup", return_value=None)
        self.lookup_mock = self._lookup.start()
        db.init()
        self.c = app.test_client()

    def tearDown(self):
        db.MIGRATIONS[:] = self._migrations
        self._lookup.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- Hilfen ------------------------------------------------------------
    def api(self, method, path, body=None, status=None):
        r = self.c.open("/api/" + path, method=method, json=body)
        if status is not None:
            self.assertEqual(r.status_code, status, f"{method} {path}: {r.get_data(as_text=True)}")
        return r.get_json(silent=True)

    def cat_id(self, name):
        return next(c["id"] for c in self.api("GET", "meta")["categories"] if c["name"] == name)

    def loc_id(self, name):
        return next(l["id"] for l in self.api("GET", "meta")["locations"] if l["name"] == name)

    def product(self, name="Mehl", qty=0, **kw):
        body = {"name": name, **kw}
        if qty:
            body["stock"] = {"quantity": qty, "location_id": kw.pop("loc", None) or self.loc_id("Speis"),
                             "expiry_date": kw.pop("exp", None)}
        return self.api("POST", "products", body, 201)["product"]

    def get(self, pid):
        return next(p for p in self.api("GET", "inventory")["items"] if p["id"] == pid)

    def assertConsistent(self):
        """Datenbank intakt + Bestand == Summe aller protokollierten Bewegungen je Produkt."""
        with db.session() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            rows = conn.execute(
                """SELECT p.id,
                          COALESCE((SELECT SUM(quantity) FROM stock s WHERE s.product_id=p.id),0) AS bestand,
                          COALESCE((SELECT SUM(delta) FROM movements m WHERE m.product_id=p.id),0) AS bewegungen
                     FROM products p""").fetchall()
            for r in rows:
                self.assertEqual(r["bestand"], r["bewegungen"], f"Produkt {r['id']} inkonsistent")
            neg = conn.execute("SELECT COUNT(*) FROM stock WHERE quantity <= 0").fetchone()[0]
            self.assertEqual(neg, 0)


# ==========================================================================
class TestProdukte(Base):
    def test_anlegen_ohne_und_mit_bestand(self):
        p = self.product("Reis")
        self.assertEqual(p["total"], 0)
        p2 = self.product("Mehl", qty=3, exp=d(100), category_id=self.cat_id("Mehl & Backen"))
        self.assertEqual(p2["total"], 3)
        self.assertEqual(p2["category_name"], "Mehl & Backen")
        self.assertEqual(p2["batches"][0]["expiry_date"], d(100))
        self.assertConsistent()

    def test_pflichtfeld_name(self):
        self.api("POST", "products", {"name": ""}, 400)
        self.api("POST", "products", {"name": "   "}, 400)
        self.api("POST", "products", {}, 400)

    def test_barcode_eindeutig_und_bereinigt(self):
        p = self.product("Nudeln", barcode="90 01-234 5")
        self.assertEqual(p["barcode"], "90012345")
        err = self.api("POST", "products", {"name": "Andere", "barcode": "90012345"}, 400)
        self.assertIn("Barcode", err["error"])
        # mehrere Produkte ohne Barcode sind erlaubt
        self.product("Ohne 1", barcode="")
        self.product("Ohne 2")

    def test_bearbeiten(self):
        p = self.product("Mehl", qty=2)
        r = self.api("PUT", f"products/{p['id']}", {
            "name": "Weizenmehl glatt", "brand": "Fini", "category_id": self.cat_id("Mehl & Backen"),
            "min_stock": 4, "unit": "Packung", "default_location_id": self.loc_id("Keller"),
            "notes": "Bio", "package_size": "1 kg"}, 200)["product"]
        self.assertEqual((r["name"], r["brand"], r["min_stock"], r["unit"], r["notes"]),
                         ("Weizenmehl glatt", "Fini", 4, "Packung", "Bio"))
        self.assertEqual(r["total"], 2, "Bestand darf beim Bearbeiten nicht verloren gehen")
        self.api("PUT", f"products/{p['id']}", {"name": ""}, 400)
        self.api("PUT", f"products/{p['id']}", {"min_stock": "abc"}, 400)
        self.assertEqual(self.get(p["id"])["name"], "Weizenmehl glatt")

    def test_kategorie_entfernen_und_wechseln(self):
        p = self.product("X", category_id=self.cat_id("Konserven"))
        r = self.api("PUT", f"products/{p['id']}", {"category_id": ""}, 200)["product"]
        self.assertIsNone(r["category_id"])
        r = self.api("PUT", f"products/{p['id']}", {"category_id": self.cat_id("Haushalt")}, 200)["product"]
        self.assertEqual(r["category_name"], "Haushalt")

    def test_barcode_auf_vorhandenen_aendern_wird_abgelehnt(self):
        self.product("A", barcode="111111")
        b = self.product("B", barcode="222222")
        self.api("PUT", f"products/{b['id']}", {"barcode": "111111"}, 400)
        self.assertEqual(self.get(b["id"])["barcode"], "222222")

    def test_loeschen_behaelt_verlauf(self):
        p = self.product("Kaffee", qty=5)
        self.api("POST", f"products/{p['id']}/consume", {"amount": 2}, 200)
        self.api("DELETE", f"products/{p['id']}", None, 200)
        self.assertFalse(any(x["id"] == p["id"] for x in self.api("GET", "inventory")["items"]))
        hist = self.api("GET", "history")["items"]
        self.assertEqual(len([h for h in hist if h["product_name"] == "Kaffee"]), 2)
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0], 0)
        self.assertConsistent()


class TestBarcodeSuche(Base):
    def test_bekannt_unbekannt_vorschlag(self):
        self.product("Nutella", barcode="3017620422003")
        r = self.api("GET", "barcode/3017620422003", None, 200)
        self.assertTrue(r["found"])
        self.lookup_mock.return_value = {"barcode": "4000000000000", "name": "Linsen", "brand": "X",
                                         "category_name": "Konserven", "source": "Test"}
        r = self.api("GET", "barcode/4000000000000", None, 200)
        self.assertFalse(r["found"])
        self.assertEqual(r["suggestion"]["category_id"], self.cat_id("Konserven"))
        self.lookup_mock.return_value = None
        r = self.api("GET", "barcode/5000000000000", None, 200)
        self.assertIsNone(r["suggestion"])

    def test_kategorie_regeln(self):
        self.assertEqual(lookup.guess_category(["en:canned-foods"]), "Konserven")
        self.assertEqual(lookup.guess_category(["en:wheat-flours"]), "Mehl & Backen")
        self.assertIsNone(lookup.guess_category(["en:unknown"]))


class TestBestand(Base):
    def test_zusammenfassen_gleicher_chargen(self):
        p = self.product("Dosen")
        speis = self.loc_id("Speis")
        for _ in range(3):
            self.api("POST", "stock", {"product_id": p["id"], "quantity": 2, "location_id": speis, "expiry_date": d(50)}, 201)
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 1, "location_id": speis, "expiry_date": d(80)}, 201)
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 1, "location_id": speis}, 201)
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 1, "location_id": speis, "expiry_date": ""}, 201)
        x = self.get(p["id"])
        self.assertEqual(x["total"], 9)
        self.assertEqual(sorted(b["quantity"] for b in x["batches"]), [1, 2, 6])
        self.assertConsistent()

    def test_ungueltige_eingaben(self):
        p = self.product("Dosen")
        for body in ({"product_id": p["id"], "quantity": 0},
                     {"product_id": p["id"], "quantity": -3},
                     {"product_id": p["id"], "quantity": "viel"},
                     {"product_id": p["id"], "quantity": 1, "expiry_date": "31.02.2027"},
                     {"product_id": p["id"], "quantity": 1, "expiry_date": "morgen"},
                     {"product_id": 99999, "quantity": 1}):
            self.api("POST", "stock", body, 400)
        self.assertEqual(self.get(p["id"])["total"], 0)
        self.assertConsistent()

    def test_entnahme_fefo(self):
        p = self.product("Milch")
        speis = self.loc_id("Speis")
        for qty, exp in ((2, d(30)), (2, d(5)), (2, None), (2, d(-3))):
            self.api("POST", "stock", {"product_id": p["id"], "quantity": qty, "location_id": speis, "expiry_date": exp}, 201)
        r = self.api("POST", f"products/{p['id']}/consume", {"amount": 3}, 200)
        self.assertEqual([(t["expiry_date"], t["quantity"]) for t in r["taken"]], [(d(-3), 2), (d(5), 1)])
        r = self.api("POST", f"products/{p['id']}/consume", {"amount": 100}, 200)
        self.assertEqual(sum(t["quantity"] for t in r["taken"]), 5)
        self.assertEqual(r["taken"][-1]["expiry_date"], None, "ohne Datum kommt zuletzt")
        self.api("POST", f"products/{p['id']}/consume", {"amount": 1}, 400)
        self.assertConsistent()

    def test_entnahme_nach_ort_und_entsorgt(self):
        p = self.product("Saft")
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 3, "location_id": self.loc_id("Speis")}, 201)
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 3, "location_id": self.loc_id("Keller")}, 201)
        self.api("POST", f"products/{p['id']}/consume", {"amount": 2, "kind": "entsorgt", "location_id": self.loc_id("Keller")}, 200)
        x = self.get(p["id"])
        self.assertEqual({b["location_name"]: b["quantity"] for b in x["batches"]}, {"Speis": 3, "Keller": 1})
        self.assertIn("entsorgt", [h["kind"] for h in self.api("GET", "history")["items"]])
        self.assertConsistent()

    def test_rueckgaengig(self):
        p = self.product("Tomaten")
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 1, "location_id": self.loc_id("Speis"), "expiry_date": d(10)}, 201)
        self.api("POST", "stock", {"product_id": p["id"], "quantity": 2, "location_id": self.loc_id("Speis"), "expiry_date": d(20)}, 201)
        vorher = sorted((b["expiry_date"], b["quantity"]) for b in self.get(p["id"])["batches"])
        r = self.api("POST", f"products/{p['id']}/consume", {"amount": 3}, 200)
        self.assertEqual(self.get(p["id"])["total"], 0)
        self.api("POST", "stock/undo", {"items": r["taken"]}, 200)
        nachher = sorted((b["expiry_date"], b["quantity"]) for b in self.get(p["id"])["batches"])
        self.assertEqual(vorher, nachher)
        self.assertConsistent()

    def test_charge_bearbeiten(self):
        p = self.product("Bohnen")
        speis, keller = self.loc_id("Speis"), self.loc_id("Keller")
        a = self.api("POST", "stock", {"product_id": p["id"], "quantity": 4, "location_id": speis, "expiry_date": d(40)}, 201)["stock_id"]
        b = self.api("POST", "stock", {"product_id": p["id"], "quantity": 1, "location_id": keller, "expiry_date": d(90)}, 201)["stock_id"]
        self.api("PUT", f"stock/{a}", {"quantity": 6}, 200)                      # Menge korrigieren
        self.api("PUT", f"stock/{a}", {"location_id": keller}, 200)               # umlagern
        self.api("PUT", f"stock/{a}", {"expiry_date": d(90)}, 200)                # -> verschmilzt mit b
        x = self.get(p["id"])
        self.assertEqual([(bb["id"], bb["quantity"], bb["location_name"]) for bb in x["batches"]], [(b, 7, "Keller")])
        self.api("PUT", f"stock/{b}", {"expiry_date": "kaputt"}, 400)
        self.api("PUT", f"stock/{b}", {"quantity": 0}, 200)                       # 0 = aufgebraucht
        self.assertEqual(self.get(p["id"])["total"], 0)
        self.api("PUT", "stock/99999", {"quantity": 1}, 404)
        self.assertConsistent()

    def test_charge_teilweise_und_ganz_entnehmen(self):
        p = self.product("Erbsen", qty=5)
        sid = p["batches"][0]["id"]
        self.api("POST", f"stock/{sid}/take", {"amount": 2}, 200)
        self.assertEqual(self.get(p["id"])["total"], 3)
        self.api("POST", f"stock/{sid}/take", {"amount": 3, "kind": "entsorgt"}, 200)
        self.assertEqual(self.get(p["id"])["batches"], [])
        self.api("POST", f"stock/{sid}/take", {"amount": 1}, 400)
        self.assertConsistent()


class TestStammdaten(Base):
    def test_kategorien(self):
        self.api("POST", "categories", {"name": "Vorräte Oma", "icon": "👵", "color": "#123456"}, 201)
        self.api("POST", "categories", {"name": "vorräte oma"}, 400)   # gleicher Name, andere Schreibung
        self.api("POST", "categories", {"name": ""}, 400)
        cid = self.cat_id("Vorräte Oma")
        self.api("PUT", f"categories/{cid}", {"name": "Oma-Vorrat", "icon": "🍯"}, 200)
        p = self.product("Marmelade", qty=2, category_id=cid)
        self.api("DELETE", f"categories/{cid}", None, 200)
        x = self.get(p["id"])
        self.assertIsNone(x["category_id"], "Produkt bleibt, nur Zuordnung entfällt")
        self.assertEqual(x["total"], 2)
        self.assertConsistent()

    def test_lagerorte(self):
        self.api("POST", "locations", {"name": "Garage", "icon": "🚗"}, 201)
        lid = self.loc_id("Garage")
        p = self.product("Öl", qty=3, loc=lid)
        self.api("PUT", f"locations/{lid}", {"name": "Carport"}, 200)
        self.assertEqual(self.get(p["id"])["batches"][0]["location_name"], "Carport")
        self.api("DELETE", f"locations/{lid}", None, 200)
        x = self.get(p["id"])
        self.assertEqual(x["total"], 3, "Bestand bleibt beim Löschen des Lagerorts erhalten")
        self.assertIsNone(x["batches"][0]["location_id"])
        self.assertConsistent()


class TestAuswertungen(Base):
    def test_einkaufsliste_und_kennzahlen(self):
        a = self.product("Klopapier", qty=2, min_stock=8)
        self.product("Salz", qty=5, min_stock=2)
        self.product("Zucker", min_stock=1)
        names = [x["name"] for x in self.api("GET", "shopping")["items"]]
        self.assertEqual(sorted(names), ["Klopapier", "Zucker"])
        self.product("Alt", qty=1, exp=d(-1))
        self.product("Bald", qty=1, exp=d(3))
        s = self.api("GET", "dashboard")["stats"]
        self.assertEqual((s["expired"], s["soon"], s["low"], s["units"]), (1, 1, 2, 9))
        self.api("PUT", f"products/{a['id']}", {"min_stock": 0}, 200)
        self.assertEqual([x["name"] for x in self.api("GET", "shopping")["items"]], ["Zucker"])

    def test_export_und_verlauf(self):
        self.product("Linsen", qty=2, exp=d(200), barcode="123456789")
        r = self.c.get("/api/export.csv")
        text = r.get_data(as_text=True)
        self.assertIn("Linsen", text)
        self.assertIn("123456789", text)
        self.assertGreaterEqual(len(self.api("GET", "history")["items"]), 1)


class TestEinstellungen(Base):
    FULL = {"notify": {"enabled": True, "thresholds": [30, "7", 7, -5, ""], "time": "07:15",
                       "notify_expired": True, "include_low_stock": False, "ha_services": ["notify.handy", "kaputt"],
                       "email": {"enabled": True, "host": "smtp.test", "port": 587, "security": "starttls",
                                 "username": "u", "password": "geheim", "sender": "", "recipients": "a@b.at"},
                       "whatsapp": {"enabled": True, "recipients": [{"name": "M", "phone": "+43664", "apikey": "k1"}]}},
            "ui": {"soon_days": 14}}

    def test_speichern_maskieren_bereinigen(self):
        r = self.api("PUT", "settings", self.FULL, 200)
        n = r["notify"]
        self.assertEqual(n["thresholds"], [30, 7, 0])
        self.assertEqual(n["ha_services"], ["notify.handy"])
        self.assertEqual(n["email"]["password"], "••••••")
        self.assertEqual(n["whatsapp"]["recipients"][0]["apikey"], "••••••")
        # erneut speichern mit maskierten Werten -> Geheimnisse bleiben
        self.api("PUT", "settings", {"notify": n, "ui": r["ui"]}, 200)
        with db.session() as conn:
            stored = db.get_setting(conn, "notify")
        self.assertEqual(stored["email"]["password"], "geheim")
        self.assertEqual(stored["whatsapp"]["recipients"][0]["apikey"], "k1")
        self.assertEqual(r["ui"]["soon_days"], 14)

    def test_ungueltige_uhrzeit(self):
        bad = json.loads(json.dumps(self.FULL))
        bad["notify"]["time"] = "25:99"
        self.api("PUT", "settings", bad, 400)


class TestErinnerungen(Base):
    def setUp(self):
        super().setUp()
        self.sent = []
        self._send = mock.patch.object(notify, "send", side_effect=lambda cfg, s, t: (self.sent.append(t), {"sent": ["test"], "errors": []})[1])
        self._send.start()

    def tearDown(self):
        self._send.stop()
        super().tearDown()

    def test_je_stufe_nur_einmal(self):
        self.product("Joghurt", qty=1, exp=d(5))     # Stufe 7
        self.product("Abgelaufen", qty=1, exp=d(-2))
        self.product("Lange gut", qty=1, exp=d(400))
        notify.run_check()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Joghurt", self.sent[0])
        self.assertIn("Abgelaufen", self.sent[0])
        self.assertNotIn("Lange gut", self.sent[0])
        notify.run_check()
        self.assertEqual(len(self.sent), 1, "kein zweites Mal für dieselbe Stufe")

    def test_neues_datum_setzt_erinnerung_zurueck(self):
        p = self.product("Käse", qty=1, exp=d(5))
        notify.run_check()
        sid = self.get(p["id"])["batches"][0]["id"]
        self.api("PUT", f"stock/{sid}", {"expiry_date": d(6)}, 200)
        notify.run_check()
        self.assertEqual(len(self.sent), 2)

    def test_ohne_kanal_fehlermeldung(self):
        self._send.stop()
        self.product("Joghurt", qty=1, exp=d(1))
        r = notify.run_check(force=True)
        self.assertTrue(r["errors"])
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0], 0,
                             "ohne erfolgreichen Versand wird nichts als erledigt markiert")
        self._send.start()


class TestSicherung(Base):
    def test_taegliche_sicherung_geprueft_und_vollstaendig(self):
        for i in range(20):
            self.product(f"P{i}", qty=i + 1)
        path = backup.run_daily()
        self.assertTrue(path.startswith(self.share))
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 20)
        self.assertEqual(conn.execute("SELECT SUM(quantity) FROM stock").fetchone()[0], 210)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        conn.close()
        self.assertFalse(os.path.exists(path + "-wal"))

    def test_aufbewahrung(self):
        d_ = backup.backup_dir()
        for i in range(30):
            open(os.path.join(d_, f"ballager-2020-01-{i + 1:02d}.db"), "w").close()
        backup.run_daily()
        daily = [f for f in os.listdir(d_) if not f.startswith("ballager-monat")]
        self.assertEqual(len(daily), backup.KEEP_DAILY)
        self.assertIn(f"ballager-{TODAY.isoformat()}.db", daily, "neueste bleibt immer")

    def test_download_ist_gueltige_datenbank(self):
        self.product("Reis", qty=4)
        r = self.c.get("/api/backup/download")
        f = os.path.join(self.tmp, "dl.db")
        with open(f, "wb") as fh:
            fh.write(r.data)
        conn = sqlite3.connect(f)
        self.assertEqual(conn.execute("SELECT name FROM products").fetchone()[0], "Reis")
        conn.close()

    def test_wiederherstellung(self):
        self.product("Alter Stand", qty=1)
        backup.run_daily()
        snap = os.path.join(backup.backup_dir(), f"ballager-{TODAY.isoformat()}.db")
        self.product("Neuer Stand", qty=1)
        shutil.copy(snap, os.path.join(self.share, "ballager", "restore.db"))
        db.init()
        names = [p["name"] for p in self.api("GET", "inventory")["items"]]
        self.assertEqual(names, ["Alter Stand"])
        kopie = [f for f in os.listdir(db.DATA_DIR) if f.startswith("vor-wiederherstellung-")]
        self.assertEqual(len(kopie), 1)
        conn = sqlite3.connect(os.path.join(db.DATA_DIR, kopie[0]))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 2,
                         "Sicherheitskopie enthält auch die letzten (WAL-)Änderungen")
        conn.close()
        self.assertConsistent()

    def test_kaputte_wiederherstellungsdatei_wird_ignoriert(self):
        self.product("Wichtig", qty=3)
        os.makedirs(os.path.join(self.share, "ballager"), exist_ok=True)
        with open(os.path.join(self.share, "ballager", "restore.db"), "wb") as fh:
            fh.write(b"das ist keine datenbank" * 100)
        db.init()
        self.assertEqual([p["name"] for p in self.api("GET", "inventory")["items"]], ["Wichtig"])


class TestUpdates(Base):
    """Datenerhalt über Neustarts und Schema-Updates hinweg."""

    def _fingerprint(self):
        with db.session() as conn:
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
                    for t in ("products", "stock", "movements", "categories", "locations")}

    def _fill(self):
        self.api("DELETE", f"categories/{self.cat_id('Tierbedarf')}", None, 200)
        self.api("PUT", f"locations/{self.loc_id('Keller')}", {"name": "Vorratskeller"}, 200)
        for i in range(15):
            self.product(f"Produkt {i}", qty=i % 4 + 1, exp=d(i * 10 - 20) if i % 3 else None, barcode=f"40000{i:05d}")
        self.api("POST", "products/1/consume", {"amount": 1}, 200)

    def test_neustart_veraendert_nichts(self):
        self._fill()
        before = self._fingerprint()
        for _ in range(3):
            db.init()
        self.assertEqual(before, self._fingerprint())
        names = [c["name"] for c in self.api("GET", "meta")["categories"]]
        self.assertNotIn("Tierbedarf", names, "gelöschte Standard-Kategorie darf nicht zurückkommen")
        self.assertIn("Vorratskeller", [l["name"] for l in self.api("GET", "meta")["locations"]])

    def test_kuenftige_migration_erhaelt_daten_und_sichert_vorher(self):
        self._fill()
        before = self._fingerprint()
        db.MIGRATIONS.append("ALTER TABLE products ADD COLUMN zukunft TEXT NOT NULL DEFAULT '';"
                             "CREATE INDEX idx_zukunft ON products(zukunft);")
        db.init()
        after = self._fingerprint()
        self.assertEqual(len(after["products"]), len(before["products"]))
        self.assertEqual([r[:13] for r in after["products"]], [r[:13] for r in before["products"]])
        for t in ("stock", "movements", "categories", "locations"):
            self.assertEqual(before[t], after[t])
        self.assertEqual(db.STATUS["schema"], len(self._migrations) + 1)
        copies = [f for f in os.listdir(db.DATA_DIR) if f.startswith(f"vor-update-schema{len(self._migrations)}-")]
        self.assertEqual(len(copies), 1, "Sicherheitskopie vor dem Update fehlt")
        self.assertConsistent()

    def test_fehlerhafte_migration_wird_komplett_zurueckgerollt(self):
        self._fill()
        before = self._fingerprint()
        db.MIGRATIONS.append("ALTER TABLE products ADD COLUMN neu TEXT; SELECT * FROM gibt_es_nicht;")
        with self.assertRaises(sqlite3.OperationalError):
            db.init()
        with db.session() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(self._migrations))
            cols = [r[1] for r in conn.execute("PRAGMA table_info(products)")]
        self.assertNotIn("neu", cols, "halbe Migration darf nicht übrig bleiben")
        self.assertEqual(before, self._fingerprint())

    def test_aeltere_app_mit_neuerer_datenbank_nur_lesen(self):
        self._fill()
        with db.session() as conn:
            conn.execute("PRAGMA user_version = 99")
        before = self._fingerprint()
        db.init()
        self.assertTrue(db.STATUS.get("readonly"))
        self.api("POST", "products", {"name": "Neu"}, 409)
        self.api("POST", "products/1/consume", {"amount": 1}, 409)
        self.assertEqual(len(self.api("GET", "inventory")["items"]), 15)
        self.assertEqual(before, self._fingerprint())

    def test_neue_standard_einstellungen_behalten_eigene_werte(self):
        self.api("PUT", "settings", TestEinstellungen.FULL, 200)
        db.DEFAULT_SETTINGS["notify"]["neue_option"] = 42
        try:
            db.init()
            with db.session() as conn:
                n = db.get_setting(conn, "notify")
            self.assertEqual(n["neue_option"], 42)
            self.assertEqual(n["time"], "07:15")
            self.assertEqual(n["email"]["password"], "geheim")
        finally:
            del db.DEFAULT_SETTINGS["notify"]["neue_option"]


class _FakeSMTP(threading.Thread):
    """Minimaler SMTP-Server (ohne TLS/Anmeldung) zum Prüfen des echten Versands."""

    def __init__(self):
        import socket
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.mail = {"rcpt": [], "data": ""}

    def run(self):
        conn, _ = self.sock.accept()
        f = conn.makefile("rwb")

        def say(s):
            f.write((s + "\r\n").encode())
            f.flush()

        say("220 test")
        while True:
            line = f.readline().decode().strip()
            cmd = line.upper()
            if cmd.startswith(("EHLO", "HELO")):
                say("250 ok")
            elif cmd.startswith("MAIL"):
                say("250 ok")
            elif cmd.startswith("RCPT"):
                self.mail["rcpt"].append(line.split(":", 1)[1].strip("<> "))
                say("250 ok")
            elif cmd == "DATA":
                say("354 go")
                buf = []
                while True:
                    ln = f.readline().decode("utf-8", "replace")
                    if ln.rstrip("\r\n") == ".":
                        break
                    buf.append(ln)
                self.mail["data"] = "".join(buf)
                say("250 queued")
            elif cmd == "QUIT" or not line:
                say("221 bye")
                break
            else:
                say("250 ok")
        conn.close()
        self.sock.close()


class TestEmail(Base):
    def test_erkennung(self):
        from ballager import email_providers as ep
        cases = {"familie@gmail.com": "gmail", "Papa@A1.NET": "a1", "x@aon.at": "a1", "y@gmx.at": "gmx",
                 "z@web.de": "webde", "m@icloud.com": "icloud", "o@hotmail.com": "outlook", "q@firma.at": None}
        for addr, want in cases.items():
            self.assertEqual(ep.detect(addr), want, addr)

    def test_aufloesen_auto(self):
        from ballager import email_providers as ep
        r = ep.resolve({"provider": "auto", "address": "familie@gmail.com", "password": "abcd", "recipients": ""})
        self.assertEqual(r["servers"][0], ("smtp.gmail.com", 587, "starttls"))
        self.assertEqual((r["username"], r["sender"], r["recipients"]),
                         ("familie@gmail.com", "familie@gmail.com", ["familie@gmail.com"]))
        r = ep.resolve({"provider": "auto", "address": "a@a1.net", "recipients": "x@y.at; z@y.at"})
        self.assertGreaterEqual(len(r["servers"]), 2, "A1 braucht Ausweich-Server")
        self.assertEqual(r["recipients"], ["x@y.at", "z@y.at"])

    def test_outlook_und_unbekannt_verstaendlich(self):
        from ballager import email_providers as ep
        with self.assertRaisesRegex(ValueError, "Microsoft"):
            ep.resolve({"provider": "auto", "address": "a@outlook.com"})
        with self.assertRaisesRegex(ValueError, "Andere"):
            ep.resolve({"provider": "auto", "address": "a@firma.at"})
        with self.assertRaisesRegex(ValueError, "Adresse"):
            ep.resolve({"provider": "auto", "address": ""})

    def test_einstellungen_aus_1_0_bleiben_gueltig(self):
        """Wer in 1.0.x Server/Port selbst eingetragen hat, verschickt nach dem Update unverändert weiter."""
        from ballager import email_providers as ep
        with db.session() as conn:  # Stand wie in Version 1.0.1 (ohne provider/address)
            with db.tx(conn):
                db.set_setting(conn, "notify", {"enabled": True, "thresholds": [7], "time": "08:00",
                                                "email": {"enabled": True, "host": "mail.firma.at", "port": 465,
                                                          "security": "ssl", "username": "u", "password": "pw",
                                                          "sender": "s@firma.at", "recipients": "a@b.at"},
                                                "whatsapp": {"enabled": False, "recipients": []}})
        db.init()
        with db.session() as conn:
            cfg = db.get_setting(conn, "notify")["email"]
        self.assertEqual((cfg["provider"], cfg["address"], cfg["host"]), ("auto", "", "mail.firma.at"))
        r = ep.resolve(cfg)
        self.assertEqual(r["servers"], [("mail.firma.at", 465, "ssl")])
        self.assertEqual((r["username"], r["password"], r["sender"]), ("u", "pw", "s@firma.at"))

    def test_ausweichserver_und_anmeldefehler(self):
        import smtplib
        calls = []

        def fake(host, port, sec, user, pw, msg):
            calls.append((host, port))
            if len(calls) == 1:
                raise ConnectionRefusedError("Port zu")

        with mock.patch.object(notify, "_smtp_send", side_effect=fake):
            used = notify._send_email({"provider": "auto", "address": "a@a1.net", "password": "x"}, "T", "Text")
        self.assertEqual(calls, [("smtp.a1.net", 587), ("smtp.a1.net", 465)])
        self.assertEqual(used, "smtp.a1.net:465")

        calls.clear()
        def deny(host, port, sec, user, pw, msg):
            calls.append(host)
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        with mock.patch.object(notify, "_smtp_send", side_effect=deny):
            with self.assertRaisesRegex(ValueError, "Anmeldung abgelehnt"):
                notify._send_email({"provider": "auto", "address": "a@gmail.com", "password": "x"}, "T", "Text")
        self.assertEqual(len(calls), 1, "bei falschem Passwort nicht weitere Server probieren")

    def test_echter_versand(self):
        srv = _FakeSMTP()
        srv.start()
        notify._send_email({"provider": "custom", "host": "127.0.0.1", "port": srv.port, "security": "none",
                            "username": "", "sender": "ballager@test.at", "recipients": "mama@test.at, papa@test.at"},
                           "BALLager – Vorrat prüfen", "⏰ Bald verbrauchen:\n• 2 Stk Mehl – läuft morgen ab")
        srv.join(5)
        self.assertEqual(srv.mail["rcpt"], ["mama@test.at", "papa@test.at"])
        import email
        m = email.message_from_string(srv.mail["data"], policy=email.policy.default)
        self.assertIn("Mehl", m.get_content())
        self.assertIn("ballager@test.at", m["From"])

    def test_speichern_ueber_api(self):
        body = json.loads(json.dumps(TestEinstellungen.FULL))
        body["notify"]["email"] = {"enabled": True, "provider": "auto", "address": "familie@gmail.com",
                                   "password": "app-pw", "recipients": ""}
        r = self.api("PUT", "settings", body, 200)
        self.assertEqual((r["notify"]["email"]["address"], r["notify"]["email"]["provider"]), ("familie@gmail.com", "auto"))
        self.assertEqual(r["notify"]["email"]["password"], "••••••")
        self.assertTrue(any(p["id"] == "gmail" for p in r["email_providers"]))
        body["notify"]["email"]["provider"] = "gibtsnicht"
        self.api("PUT", "settings", body, 400)


class TestVorsorge(Base):
    def vs(self):
        return self.api("GET", "vorsorge", None, 200)

    def item(self, v, iid):
        return next(i for g in v["groups"] for i in g["items"] if i["id"] == iid)

    def test_mengen_erkennen(self):
        from ballager import prep
        self.assertEqual(prep.parse_amount("500 g"), (0.5, "kg"))
        self.assertEqual(prep.parse_amount("6 x 1,5 l"), (9.0, "l"))
        self.assertEqual(prep.parse_amount("25 dag"), (0.25, "kg"))
        self.assertIsNone(prep.parse_amount("Packung"))
        for name, want in [("Weizenmehl glatt", "mehl"), ("Thunfisch in Öl", "fisch"), ("Milchschokolade", None),
                           ("Fruchtaufstrich", "marmelade"), ("Butter", "streichfett"), ("Spaghetti", "teigwaren")]:
            self.assertEqual(prep.detect_item(name), want, name)

    def test_ziele_fuer_vier_personen_14_tage(self):
        v = self.vs()
        self.assertEqual((v["persons"], v["days"]), (4, 14))
        self.assertEqual(self.item(v, "wasser")["target"], 112)
        self.assertEqual(self.item(v, "konserven")["target"], 56)
        self.assertEqual(self.item(v, "teigwaren")["target"], 4)
        self.assertEqual(self.item(v, "aufstrich")["target"], 4, "Fleischaufstrich: 1 Dose pro Person, unabhängig von Tagen")
        self.assertEqual(v["score"], 0)

    def test_abgleich_mit_vorrat(self):
        self.product("Weizenmehl glatt", qty=8, package_size="1 kg", exp=d(300))
        self.product("Vöslauer Mineralwasser", qty=10, package_size="6 x 1,5 l")
        self.product("Spaghetti", qty=4)                              # ohne Größe -> geschätzt 0,5 kg
        self.product("Reis alt", qty=5, package_size="1 kg", exp=d(-10))   # abgelaufen -> zählt nicht
        self.product("Kichererbsen", qty=3, category_id=self.cat_id("Konserven"))
        v = self.vs()
        self.assertEqual(self.item(v, "mehl")["have"], 8)
        self.assertEqual(self.item(v, "mehl")["pct"], 1)
        self.assertEqual(self.item(v, "wasser")["have"], 90)
        self.assertAlmostEqual(v["water_days"], 11.2, places=1)
        self.assertEqual(self.item(v, "teigwaren")["have"], 2)
        self.assertEqual([e["name"] for e in v["estimated"]], ["Spaghetti"])
        reis = self.item(v, "reis")
        self.assertEqual((reis["have"], reis["expired"]), (0, 5))
        self.assertEqual(self.item(v, "konserven")["have"], 3)
        self.assertGreater(v["score"], 0)
        self.assertLess(v["score"], 1)

    def test_manuelle_zuordnung_und_inhalt(self):
        p = self.product("Omas Spezial", qty=3)
        v = self.vs()
        self.assertFalse(any(pp["id"] == p["id"] for g in v["groups"] for i in g["items"] for pp in i["products"]))
        self.api("PUT", f"products/{p['id']}", {"prep_group": "oel", "content_amount": "0,75"}, 200)
        self.assertEqual(self.item(self.vs(), "oel")["have"], 2.25)
        q = self.product("Mineralwasser", qty=6, package_size="1,5 l")
        self.api("PUT", f"products/{q['id']}", {"prep_group": "none"}, 200)
        self.assertEqual(self.item(self.vs(), "wasser")["have"], 0)
        self.api("PUT", f"products/{p['id']}", {"prep_group": "gibtsnicht"}, 400)
        self.api("PUT", f"products/{p['id']}", {"content_amount": "-1"}, 400)
        self.api("PUT", f"products/{p['id']}", {"content_amount": "viel"}, 400)
        k = self.product("Kartoffeln lose", qty=5, unit="kg")
        self.assertEqual(self.item(self.vs(), "kartoffeln")["have"], 5)
        self.api("DELETE", f"products/{k['id']}", None, 200)

    def test_einstellungen_und_checkliste(self):
        self.product("Klopapier", qty=12, unit="Rolle")
        v = self.api("PUT", "vorsorge", {"persons": 2, "days": 7, "checklist": {"radio": True, "bargeld": True, "hack": True}}, 200)
        self.assertEqual(self.item(v, "wasser")["target"], 28)
        checks = {c["id"]: c for c in v["checklist"]}
        self.assertTrue(checks["toilettenpapier"]["checked"])
        self.assertTrue(checks["toilettenpapier"]["found"])
        self.assertTrue(checks["radio"]["checked"])
        self.assertFalse(checks["kocher"]["checked"])
        with db.session() as conn:
            self.assertNotIn("hack", db.get_setting(conn, "prep")["checklist"])
        self.api("PUT", "vorsorge", {"persons": 0}, 200)
        self.assertEqual(self.vs()["persons"], 1)

    def test_open_food_facts_kategorien(self):
        from ballager import prep
        cases = [
            (["en:cereals-and-potatoes", "en:cereal-grains", "en:rices", "en:basmati-rices"], "reis"),
            (["en:fats", "en:vegetable-fats", "en:vegetable-oils", "en:sunflower-oils"], "oel"),        # nicht Streichfett
            (["en:cereals-and-potatoes", "en:breads", "en:crispbreads"], "brot"),                      # nicht Kartoffeln
            (["en:canned-foods", "en:fishes", "en:canned-fishes", "en:sardines", "en:sardines-in-sunflower-oil"], "fisch"),
            (["en:cereal-pastas", "en:dry-pastas", "en:spaghetti"], "teigwaren"),
            (["en:spreads", "en:sweet-spreads", "en:hazelnut-spreads"], "marmelade"),
            (["en:canned-foods", "en:canned-legumes", "en:canned-chickpeas"], "konserven"),
            (["en:beverages", "en:waters", "en:mineral-waters"], "wasser"),
            (["en:dairies", "en:milks", "en:uht-milks"], "milch"),
            (["en:spreads", "en:peanut-butters"], None),        # Erdnussbutter ist kein Streichfett
            (["en:coconut-milks"], None),
            (["en:puffed-rices", "en:rice-cakes"], None),       # Reiswaffeln
            (["en:snacks", "en:crisps", "en:potato-crisps"], None),
            ([], None),
        ]
        for tags, want in cases:
            self.assertEqual(prep.detect_from_tags(tags), want, tags)
        self.assertEqual(prep.detect_item("Uncle Ben's Original Langkorn"), "reis")
        self.assertEqual(prep.detect_item("Riso Arborio"), "reis")
        self.assertIsNone(prep.detect_item("Kartoffelchips Paprika"))

    def test_scan_uebernimmt_vorsorge_gruppe(self):
        fake = {"status": 1, "product": {"product_name_de": "Original Langkorn", "brands": "Uncle Ben's",
                                         "quantity": "1 kg", "categories_tags": ["en:cereal-grains", "en:rices", "en:long-grain-rices"]}}

        class Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        self._lookup.stop()
        try:
            with mock.patch("urllib.request.urlopen", return_value=Resp(json.dumps(fake).encode())):
                r = self.api("GET", "barcode/5000000000001", None, 200)
        finally:
            self.lookup_mock = self._lookup.start()
        s = r["suggestion"]
        self.assertEqual((s["prep_group"], s["prep_name"], s["package_size"]), ("reis", "Reis", "1 kg"))
        # so wie die Oberfläche es anlegt
        self.api("POST", "products", {"name": s["name"], "brand": s["brand"], "package_size": s["package_size"],
                                      "barcode": "5000000000001", "prep_group": s["prep_group"],
                                      "stock": {"quantity": 3}}, 201)
        self.assertEqual(self.item(self.vs(), "reis")["have"], 3)

    def test_nachtraeglicher_abgleich(self):
        a = self.product("Uncle Bens Original", qty=2, barcode="5000000000002", package_size="500 g")
        self.product("Klopapier", qty=12, barcode="5000000000003")
        self.product("Spaghetti", qty=1, barcode="5000000000004")      # schon per Name erkannt -> kein Kandidat
        self.assertEqual(self.vs()["unmatched"], 2)
        self.lookup_mock.side_effect = lambda code: (
            {"prep_group": "reis", "prep_name": "Reis"} if code == "5000000000002" else None)
        r = self.api("POST", "vorsorge/abgleich", None, 200)
        self.assertEqual((r["checked"], [x["name"] for x in r["assigned"]]), (2, ["Uncle Bens Original"]))
        v = self.vs()
        self.assertEqual(self.item(v, "reis")["have"], 1)
        self.assertEqual(v["unmatched"], 0, "einmal geprüfte Produkte werden nicht erneut angeboten")
        r = self.api("POST", "vorsorge/abgleich", None, 200)
        self.assertEqual(r["checked"], 0)
        self.assertEqual(self.get(a["id"])["prep_group"], "reis")
        self.lookup_mock.side_effect = None

    def test_migration_auf_schema_2_mit_bestehenden_daten(self):
        """Simuliert den Stand von 1.0.2 (Schema 1) mit Daten und spielt das Update ein."""
        shutil.rmtree(db.DATA_DIR)
        db.MIGRATIONS[:] = self._migrations[:1]
        db.init()
        for i in range(10):
            with db.session() as conn:
                with db.tx(conn):
                    conn.execute("INSERT INTO products(name, package_size) VALUES (?, '1 kg')", (f"Mehl {i}",))
                    inventory.add_stock(conn, i + 1, 2, None, d(100))
        db.MIGRATIONS[:] = self._migrations
        db.init()
        with db.session() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(self._migrations))
            rows = conn.execute("SELECT name, prep_group, content_amount FROM products ORDER BY id").fetchall()
            self.assertEqual(len(rows), 10)
            self.assertTrue(all(r["prep_group"] == "auto" and r["content_amount"] is None for r in rows))
            self.assertEqual(conn.execute("SELECT SUM(quantity) FROM stock").fetchone()[0], 20)
        self.assertTrue(any(f.startswith("vor-update-schema1-") for f in os.listdir(db.DATA_DIR)))
        self.assertEqual(self.item(self.vs(), "mehl")["have"], 20)
        self.assertConsistent()


class TestMigrationsZerleger(unittest.TestCase):
    def test_zerlegen(self):
        s = db._split_sql("-- Kommentar\nCREATE TABLE a(x);CREATE TABLE b(y TEXT DEFAULT 'a;b');\n\nINSERT INTO a VALUES(1);")
        self.assertEqual(s, ["CREATE TABLE a(x);", "CREATE TABLE b(y TEXT DEFAULT 'a;b');", "INSERT INTO a VALUES(1);"])

    def test_schema_1_identisch(self):
        conn = sqlite3.connect(":memory:")
        for stmt in db._split_sql(db.MIGRATIONS[0]):
            conn.execute(stmt)
        tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        self.assertEqual(tables, ["categories", "locations", "movements", "notification_log", "products", "settings", "stock"])


class TestGleichzeitigkeit(Base):
    def test_parallele_zugriffe(self):
        pids = [self.product(f"Parallel {i}", qty=50)["id"] for i in range(4)]
        errors = []

        def worker(n):
            c = app.test_client()
            for i in range(40):
                pid = pids[(n + i) % len(pids)]
                if i % 3 == 0:
                    r = c.post("/api/stock", json={"product_id": pid, "quantity": 1, "location_id": 1, "expiry_date": d(i)})
                elif i % 3 == 1:
                    r = c.post(f"/api/products/{pid}/consume", json={"amount": 1})
                else:
                    r = c.get("/api/inventory")
                if r.status_code >= 500:
                    errors.append(r.get_data(as_text=True))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertConsistent()


if __name__ == "__main__":
    unittest.main(verbosity=2)
