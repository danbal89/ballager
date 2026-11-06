"""Absturz-Test: Server mitten in parallelen Schreibzugriffen hart beenden (kill -9).

    python3 tests/crash_test.py [runden]

Prüft nach jedem Neustart:
  * Datenbank-Integrität (PRAGMA integrity_check, foreign_key_check)
  * Jede vom Server BESTÄTIGTE Eingabe ist noch vorhanden (keine verlorenen Produkte)
  * Bestand je Produkt == Summe der protokollierten Bewegungen (keine halben Buchungen)
"""
from __future__ import annotations

import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "ballager", "app")
PORT = 8198


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"null")


def start(data):
    env = dict(os.environ, BALLAGER_DATA=data, BALLAGER_SHARE=os.path.join(data, "noshare"),
               BALLAGER_PORT=str(PORT), BALLAGER_LOGLEVEL="WARNING")
    p = subprocess.Popen([sys.executable, "-m", "ballager"], cwd=APP, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(60):
        try:
            call("GET", "health")
            return p
        except Exception:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("Server startet nicht: " + p.stderr.read().decode()[-2000:])


def check(db_path, confirmed):
    conn = sqlite3.connect(db_path)
    problems = []
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        problems.append("integrity: " + ok)
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("verwaiste Fremdschlüssel")
    names = {r[0] for r in conn.execute("SELECT name FROM products")}
    lost = confirmed - names
    if lost:
        problems.append(f"{len(lost)} bestätigte Produkte verloren: {sorted(lost)[:5]}")
    bad = conn.execute(
        """SELECT p.id FROM products p
            WHERE COALESCE((SELECT SUM(quantity) FROM stock s WHERE s.product_id=p.id),0)
               != COALESCE((SELECT SUM(delta) FROM movements m WHERE m.product_id=p.id),0)""").fetchall()
    if bad:
        problems.append(f"{len(bad)} Produkte mit halber Buchung")
    counts = conn.execute("SELECT (SELECT COUNT(*) FROM products), (SELECT COALESCE(SUM(quantity),0) FROM stock), "
                          "(SELECT COUNT(*) FROM movements)").fetchone()
    conn.close()
    return problems, counts


def main(rounds=10):
    data = tempfile.mkdtemp(prefix="ballager-crash-")
    db_path = os.path.join(data, "ballager.db")
    confirmed: set[str] = set()
    lock = threading.Lock()
    total_problems = 0
    for rnd in range(1, rounds + 1):
        p = start(data)
        stop = threading.Event()
        seq = [0]

        def worker(wid):
            while not stop.is_set():
                with lock:
                    seq[0] += 1
                    n = seq[0]
                name = f"R{rnd}-W{wid}-{n}"
                try:
                    op = random.random()
                    if op < 0.5:
                        call("POST", "products", {"name": name, "stock": {"quantity": random.randint(1, 9),
                                                                            "expiry_date": "2027-06-30"}})
                        with lock:
                            confirmed.add(name)  # erst NACH Bestätigung des Servers merken
                    elif op < 0.8:
                        items = call("GET", "inventory")["items"]
                        full = [i for i in items if i["total"] > 0]
                        if full:
                            call("POST", f"products/{random.choice(full)['id']}/consume", {"amount": random.randint(1, 3)})
                    else:
                        items = call("GET", "inventory")["items"]
                        if items:
                            it = random.choice(items)
                            call("POST", "stock", {"product_id": it["id"], "quantity": 1, "expiry_date": "2028-01-31"})
                except Exception:
                    pass  # Verbindung bricht beim Kill ab – erwartet

        threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(6)]
        for t in threads:
            t.start()
        time.sleep(random.uniform(0.6, 1.8))
        os.kill(p.pid, signal.SIGKILL)  # harter Absturz, keine Aufräumarbeiten
        p.wait()
        stop.set()
        for t in threads:
            t.join(2)
        problems, counts = check(db_path, confirmed)
        total_problems += len(problems)
        status = "OK ✅" if not problems else "FEHLER ❌ " + "; ".join(problems)
        print(f"Runde {rnd:2d}: kill -9 → {counts[0]:4d} Produkte, {counts[1]:5d} Stück, {counts[2]:5d} Bewegungen, "
              f"{len(confirmed):4d} bestätigt – {status}")
    # zum Schluss: sauberer Start mit der App-eigenen Prüfung
    p = start(data)
    st = call("GET", "backups")["status"]
    p.terminate()
    p.wait(10)
    print(f"Abschluss-Start: Integrität laut App = {st['integrity']}")
    print("Ergebnis:", "KEIN DATENVERLUST ✅" if total_problems == 0 and st["integrity"] == "ok" else "PROBLEME ❌")
    return 0 if total_problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
