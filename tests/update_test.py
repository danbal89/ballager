"""Update-Test: Daten mit einer ALTEN Version erfassen, dann mit der aktuellen Version weiterarbeiten.

    python3 tests/update_test.py <git-revision-der-alten-version>   (z.B. 1577313)

Startet beide Versionen als echte Server-Prozesse auf derselben Datenbank, genau wie bei
einem Add-on-Update in Home Assistant, und vergleicht den kompletten Datenstand.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8197


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def start(app_dir, data):
    env = dict(os.environ, BALLAGER_DATA=data, BALLAGER_SHARE=os.path.join(data, "noshare"), BALLAGER_PORT=str(PORT))
    p = subprocess.Popen([sys.executable, "-m", "ballager"], cwd=app_dir, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            call("GET", "meta")
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise RuntimeError(p.stdout.read().decode())


def dump(db_path):
    conn = sqlite3.connect(db_path)
    out = {t: [list(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
           for t in ("products", "stock", "movements", "categories", "locations", "notification_log")}
    out["settings"] = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM settings") if k != "state"}
    out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return out


def main(old_rev):
    tmp = tempfile.mkdtemp(prefix="ballager-update-")
    old_dir = os.path.join(tmp, "alt")
    os.makedirs(old_dir)
    archive = subprocess.run(["git", "archive", old_rev, "ballager/app"], cwd=ROOT, capture_output=True, check=True).stdout
    tar_path = os.path.join(tmp, "alt.tar")
    with open(tar_path, "wb") as fh:
        fh.write(archive)
    with tarfile.open(tar_path) as tf:
        tf.extractall(old_dir)
    data = os.path.join(tmp, "data")

    # ---- 1) Alte Version: Daten erfassen ----
    p = start(os.path.join(old_dir, "ballager", "app"), data)
    try:
        meta = call("GET", "meta")
        cats = {c["name"]: c["id"] for c in meta["categories"]}
        locs = {l["name"]: l["id"] for l in meta["locations"]}
        call("POST", "categories", {"name": "Selbstgemacht", "icon": "🍯"})
        call("DELETE", f"categories/{cats['Tierbedarf']}")
        call("PUT", f"locations/{locs['Keller']}", {"name": "Vorratskeller"})
        for i in range(40):
            call("POST", "products", {"name": f"Produkt {i}", "brand": "Marke" if i % 2 else "", "barcode": f"9{i:012d}",
                                      "category_id": list(cats.values())[i % 10], "min_stock": i % 3, "unit": "Stk",
                                      "stock": {"quantity": i % 5 + 1, "location_id": list(locs.values())[i % 4],
                                                "expiry_date": f"2027-{i % 12 + 1:02d}-15" if i % 4 else None}})
        for pid in range(1, 15):
            call("POST", f"products/{pid}/consume", {"amount": 1})
        call("DELETE", "products/20")
        call("PUT", "settings", {"notify": {"enabled": True, "thresholds": [60, 10], "time": "06:45",
                                            "notify_expired": True, "include_low_stock": True, "ha_services": [],
                                            "email": {"enabled": True, "host": "smtp.x", "password": "geheim",
                                                      "recipients": "a@b"}, "whatsapp": {"enabled": False, "recipients": []}},
                                 "ui": {"soon_days": 21}})
    finally:
        p.terminate()
        p.wait(10)
    before = dump(os.path.join(data, "ballager.db"))

    # ---- 2) Neue Version auf derselben Datenbank ----
    p = start(os.path.join(ROOT, "ballager", "app"), data)
    try:
        inv = call("GET", "inventory")["items"]
        call("POST", "products", {"name": "Nach Update", "stock": {"quantity": 2}})
        call("POST", "products/3/consume", {"amount": 1})  # Produkt 3 hat noch Restbestand
    finally:
        p.terminate()
        p.wait(10)
    after = dump(os.path.join(data, "ballager.db"))

    # ---- 3) Vergleich ----
    problems = []
    if after["integrity"] != "ok":
        problems.append("Integrität: " + after["integrity"])
    for t in ("categories", "locations"):
        if before[t] != after[t]:
            problems.append(f"Tabelle {t} hat sich verändert")
    old_products = {r[0]: r for r in before["products"]}
    for pid, row in old_products.items():
        new = next((r for r in after["products"] if r[0] == pid), None)
        if new is None:
            problems.append(f"Produkt {pid} verloren!")
        elif new[:12] != row[:12]:
            problems.append(f"Produkt {pid} verändert")
    if after["movements"][:len(before["movements"])] != before["movements"]:
        problems.append("Verlauf verändert")
    if after["settings"]["notify"]["email"]["password"] != "geheim" or after["settings"]["notify"]["time"] != "06:45":
        problems.append("Einstellungen verloren")
    old_stock = sum(r[3] for r in before["stock"])
    new_stock = sum(r[3] for r in after["stock"])
    if new_stock != old_stock + 2 - 1:
        problems.append(f"Bestand passt nicht: vorher {old_stock}, nachher {new_stock}")

    print(f"Alte Version {old_rev}: {len(before['products'])} Produkte, {old_stock} Stück, "
          f"{len(before['movements'])} Bewegungen, {len(before['categories'])} Kategorien")
    print(f"Neue Version: {len(after['products'])} Produkte, {new_stock} Stück, {len(after['movements'])} Bewegungen; "
          f"in der Oberfläche sichtbar: {len(inv)} Produkte")
    print("Ergebnis:", "ALLES ERHALTEN ✅" if not problems else "PROBLEME ❌\n  " + "\n  ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "HEAD"))
