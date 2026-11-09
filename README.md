<p align="center"><img src="ballager/logo.png" alt="BALLager – Unser Vorrat. Gut sortiert." width="420"></p>

# BALLager – Home-Assistant-Add-on

Zentrales Vorratslager für zu Hause: Barcode scannen, Ablaufdatum eintippen, fertig.
Erinnerungen per Home Assistant, E-Mail oder WhatsApp, bevor etwas abläuft.

## Installation

### Variante A – lokales Add-on (am schnellsten)

1. In Home Assistant das Add-on **Samba share** oder **Advanced SSH & Web Terminal** installieren.
2. Den Ordner `ballager/` aus diesem Projekt nach **`/addons/ballager/`** auf den NUC kopieren
   (bei Samba: Freigabe `addons`).
3. **Einstellungen → Add-ons → Add-on Store → ⋮ → Nach Updates suchen**, dann die Seite neu laden.
4. Unter **Lokale Add-ons** erscheint **BALLager** → *Installieren* (der erste Build dauert ein paar Minuten).
5. *Starten* und **In Seitenleiste anzeigen** aktivieren.

### Variante B – eigenes GitHub-Repository (bequeme Updates)

1. Dieses Projekt als (privates oder öffentliches) GitHub-Repository hochladen und in `repository.yaml` die URL eintragen.
2. **Add-on Store → ⋮ → Repositories** → Repository-URL hinzufügen.
3. BALLager installieren und starten. Für Updates `version` in `ballager/config.yaml` erhöhen.

Details zur Bedienung: [ballager/DOCS.md](ballager/DOCS.md)

## Aufbau

```
repository.yaml            Add-on-Repository
ballager/
  config.yaml              Add-on-Konfiguration (Ingress, Seitenleiste, HA-API, /share)
  Dockerfile, build.yaml   Image auf Basis von home-assistant/base-python (amd64 + aarch64)
  run.sh                   Startskript
  app/ballager/            Python-Backend (Flask + Waitress, SQLite)
    db.py                  Schema, Migrationen, Einstellungen
    inventory.py           Einlagern, Entnehmen (FEFO), Auswertungen
    notify.py              Erinnerungen (HA, SMTP, WhatsApp/CallMeBot)
    scheduler.py           tägliche Prüfung, Sicherung, HA-Sensoren
    backup.py              Online-Sicherung mit Aufbewahrung
    lookup.py              Open-Food/Beauty/Products-Facts-Abfrage
    web.py                 REST-API
  app/static/              Weboberfläche (ohne Build-Schritt), Logo, Schrift, Scanner-Bibliothek
```

### Datenmodell

| Tabelle | Zweck |
|---|---|
| `products` | Stammdaten: Barcode (eindeutig), Name, Marke, Kategorie, Einheit, Mindestbestand |
| `stock` | Chargen: Produkt + Lagerort + Ablaufdatum + Menge (> 0) |
| `movements` | lückenloses Protokoll aller Bewegungen |
| `categories`, `locations` | frei anpassbar |
| `notification_log` | welche Erinnerung pro Charge schon verschickt wurde |
| `settings` | Einstellungen als JSON |

Schemaänderungen laufen über `PRAGMA user_version` und neue Einträge in `MIGRATIONS` (db.py) – vorhandene Daten bleiben erhalten.

## Lokal entwickeln

```bash
cd ballager/app && pip install -r requirements.txt && BALLAGER_DATA=./devdata python -m ballager
```

Dann <http://localhost:8099> öffnen.

## Tests – vor jedem Update ausführen

```bash
python3 -m unittest discover -s tests -v      # 57 Tests: alle Funktionen, Vorsorge, E-Mail, Sicherung, Migrationen, Parallelität
python3 tests/update_test.py <alte-git-version> # echter Update-Test: alte Version erfasst, neue liest weiter
python3 tests/crash_test.py 10                  # 10 harte Abstürze mitten im Schreiben
```

**Regeln für Datenbank-Änderungen:** in `db.py` nur neue Einträge an `MIGRATIONS` anhängen, nie bestehende ändern;
nur additive Änderungen (neue Spalten mit Standardwert, neue Tabellen/Indizes). Danach `update_test.py` mit der
zuletzt veröffentlichten Version laufen lassen.

## Lizenzen von Fremdbestandteilen

- [html5-qrcode](https://github.com/mebjas/html5-qrcode) 2.3.8 – Apache-2.0
- [Nunito](https://fonts.google.com/specimen/Nunito) – SIL Open Font License
- Produktdaten: [Open Food Facts](https://openfoodfacts.org) – ODbL
