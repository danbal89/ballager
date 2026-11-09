# BALLager – Unser Vorrat. Gut sortiert.

BALLager ist das Vorratslager für die ganze Familie: Produkte per Barcode einlagern,
beim Verbrauch mit einem Tipp austragen und rechtzeitig erinnert werden, bevor etwas abläuft.

## Erste Schritte

1. Add-on starten und **„In Seitenleiste anzeigen“** aktivieren.
2. In der Seitenleiste auf **BALLager** tippen.
3. Unter **Mehr → Einstellungen** Lagerorte und Kategorien anpassen (Speis und Technikraum sind schon angelegt).
4. **Scannen → Einlagern** und los geht’s.

## Scannen

| Möglichkeit | Wann |
|---|---|
| 📷 **Live-Kamera** | Braucht eine sichere Verbindung (HTTPS), z.B. über Home Assistant Cloud (Nabu Casa) oder ein eigenes SSL-Zertifikat. |
| 📸 **Foto vom Barcode** | Klappt immer, auch über `http://homeassistant.local:8123`. Das Handy öffnet die Kamera, Foto machen, fertig. |
| ⌨️ **Code eintippen** | Wenn der Barcode beschädigt ist. |
| ✍️ **Ohne Barcode** | Für lose Ware, Selbstgemachtes usw. |

Unbekannte Barcodes werden automatisch bei *Open Food Facts*, *Open Beauty Facts* und *Open Products Facts*
nachgeschlagen. Ab dem zweiten Scan erkennt BALLager das Produkt selbst – auch ohne Internet.

**Ablaufdatum schnell eingeben:** `31.12.27`, `311227`, `12/27` oder `1227` (→ Monatsende) – oder die Schnellknöpfe
*+3 Monate*, *+1 Jahr* usw. Haushaltsartikel bekommen einfach kein Datum.

**Entnehmen:** Im Modus *Entnehmen* scannen, oder in der Vorratsliste auf **−** tippen. BALLager nimmt automatisch
zuerst das, was am frühesten abläuft. Jede Entnahme lässt sich direkt rückgängig machen.

## Erinnerungen

Unter **Mehr → Erinnerungen** einstellen, wie viele Tage vorher erinnert wird (Standard: 30, 7 und 1 Tag) und um welche
Uhrzeit geprüft wird. Pro Posten und Stufe kommt genau **eine** Nachricht – keine Dauerbeschallung.
Alle fälligen Produkte werden in einer Nachricht zusammengefasst.

### Kanäle

- **Home Assistant** – alle `notify.*` Dienste werden angeboten, z.B. `notify.mobile_app_<handy>` für Push-Nachrichten
  über die Companion-App, oder `persistent_notification.create` für eine Meldung in HA.
  Auch WhatsApp/Telegram/Signal-Integrationen, die einen `notify`-Dienst bereitstellen, funktionieren hier.
- **E-Mail** – nur **E-Mail-Adresse und Passwort** eintragen. BALLager erkennt den Anbieter an der Adresse
  und stellt Server, Port und Verschlüsselung selbst ein – mit Anleitung direkt in der App.
  Erkannt werden Gmail, A1 (auch aon.at), GMX, WEB.DE, iCloud, Yahoo, Posteo, mailbox.org und T-Online.
  Klappt eine Verbindung nicht, probiert BALLager automatisch eine Ausweich-Einstellung.
  - **Gmail, iCloud, Yahoo:** verlangen ein **App-Passwort** (nicht das normale Passwort).
  - **GMX / WEB.DE:** einmalig im Webmail „POP3/IMAP-Zugriff“ erlauben.
  - **Outlook / Hotmail:** Microsoft erlaubt das bei privaten Konten seit 2024 nicht mehr – als *Empfänger*
    funktionieren diese Adressen aber problemlos.
  - Andere Anbieter: „Andere (eigener Mail-Server)“ wählen und Server/Port selbst eintragen.
  - Einstellungen aus Version 1.0.x (selbst eingetragener Server) gelten unverändert weiter.
- **WhatsApp** – kostenlos über [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/).
  Jede Person holt sich einmalig einen eigenen API-Key (Anleitung in den Einstellungen).

Mit **Testnachricht** prüft ihr die Kanäle, mit **Vorschau** seht ihr, was heute verschickt würde.

## Krisenvorsorge

Die Seite **Krisenvorsorge** (Kachel auf der Übersicht, oder *Mehr → Krisenvorsorge*) vergleicht euren Vorrat
mit der **Vorratsliste des Österreichischen Zivilschutzverbands** (Mengen pro Person und Woche). Standard sind
**4 Personen und 14 Tage**; der Zivilschutz empfiehlt mindestens 7 Tage, besser 2 Wochen.

- **Automatische Zuordnung:** Beim Scannen nutzt BALLager die Produktkategorien von Open Food Facts
  (z.B. „Reis“, „Nudeln“, „Fischkonserven“) – das klappt auch, wenn der Name nichts verrät.
  Sonst wird am Namen erkannt („Spaghetti“ → Teigwaren, „Basmati“ → Reis).
  Bei Produkten der Kategorie *Konserven* zählt alles Übrige als Konservendose.
- **Bestehende Produkte:** Auf der Vorsorge-Seite „Jetzt genauer zuordnen“ – schlägt noch nicht zugeordnete
  Produkte einmalig über ihren Barcode nach.
- **Mengen:** aus der Packungsgröße („500 g“, „6 x 1,5 l“). Fehlt sie, rechnet BALLager mit einer typischen Größe
  und weist darauf hin („geschätzt“). Genauer wird es, wenn ihr die Packungsgröße eintragt.
- **Ändern:** Produkt bearbeiten → Abschnitt *Krisenvorsorge*: andere Gruppe wählen, „Nicht mitzählen“ oder
  „Inhalt pro Stück“ eintragen.
- **Abgelaufenes** zählt nicht mit und wird getrennt angezeigt.
- **Ausrüstung:** Checkliste für Hygiene, Apotheke, Licht/Radio/Notkochstelle, Bargeld und Dokumente.
  Was im Vorrat erfasst ist (z.B. Klopapier, Batterien), wird automatisch abgehakt.

Die Werte sind Richtwerte für Erwachsene – passt sie an eure Essgewohnheiten an.

## Home-Assistant-Sensoren

BALLager stellt automatisch diese Sensoren bereit (aktualisiert alle 10 Minuten und nach jeder Änderung):

| Sensor | Inhalt |
|---|---|
| `sensor.ballager_bald_ablaufend` | Anzahl Posten, die bald ablaufen (Attribut `artikel` mit Liste) |
| `sensor.ballager_abgelaufen` | Anzahl abgelaufener Posten |
| `sensor.ballager_vorrat` | Stück im Vorrat gesamt |
| `sensor.ballager_einkaufsliste` | Produkte unter Mindestbestand (Attribut `artikel`) |

Beispiel für eine Dashboard-Karte:

```yaml
type: entities
title: BALLager
entities:
  - sensor.ballager_bald_ablaufend
  - sensor.ballager_abgelaufen
  - sensor.ballager_einkaufsliste
```

## Daten & Sicherheit

> ⚠️ **Updates sind sicher – Deinstallieren nicht!** Bei einem Update bleibt die Datenbank vollständig erhalten.
> Wer das Add-on aber **deinstalliert**, dem löscht Home Assistant auch dessen Daten (`/data`).
> Die täglichen Sicherungen in `/share/ballager/backups/` bleiben dabei erhalten und lassen sich über
> `restore.db` (siehe unten) wieder einspielen.

So ist die Datenbank geschützt:

- **Jede bestätigte Eingabe ist sofort dauerhaft gespeichert** (SQLite im WAL-Modus mit `synchronous=FULL`),
  auch bei Absturz oder Stromausfall. Getestet mit 10 harten Abstürzen mitten in parallelen Schreibzugriffen: kein Verlust, keine halben Buchungen.
- **Jede Buchung ist eine Transaktion** – entweder ganz oder gar nicht. Bestand und Verlauf passen immer zusammen.
- **Prüfung bei jedem Start** (Integrität und Verweise); das Ergebnis steht unter *Mehr → Daten & Sicherung*.
- **Vor jedem Update mit Datenbank-Änderung** legt BALLager automatisch eine Sicherheitskopie in `/data` an.
  Schlägt eine Änderung fehl, wird sie vollständig zurückgerollt und die Daten bleiben unverändert.
- **Ältere Version auf neuerer Datenbank** (z.B. nach Zurückspielen eines alten Add-on-Stands): BALLager schaltet auf *nur lesen*, statt etwas zu verändern.

- Die Datenbank (SQLite, WAL-Modus, Fremdschlüssel, versionierte Migrationen) liegt in `/data/ballager.db`
  und ist Teil jeder **Home-Assistant-Sicherung**.
- Zusätzlich sichert BALLager täglich nach `/share/ballager/backups/` (14 Tagesstände + 24 Monatsstände).
  Jede Sicherung wird vor dem Ablegen auf Lesbarkeit geprüft; eine fehlerhafte ersetzt nie eine gute.
  Über Samba/„Share“ kommt ihr direkt an die Dateien.
- **Wiederherstellen:** entweder die Home-Assistant-Sicherung des Add-ons wiederherstellen, oder eine Datei aus
  `/share/ballager/backups/` nach `/share/ballager/restore.db` kopieren und das Add-on neu starten.
  BALLager prüft die Datei, legt vom aktuellen Stand eine Kopie an und übernimmt sie.
- Zugriff ist nur über Home Assistant (mit eurer HA-Anmeldung) möglich. Den Direktzugriff über Port 8099 nur im
  vertrauenswürdigen Heimnetz aktivieren – dort gibt es keine Anmeldung.
- Gespeicherte Passwörter/API-Keys werden in der Oberfläche nie wieder angezeigt.

## Optionen

| Option | Bedeutung |
|---|---|
| `log_level` | Protokoll-Stufe |
| `direct_access` | Zugriff über Port 8099 ohne HA-Anmeldung erlauben (zusätzlich Port unter „Netzwerk“ eintragen) |
