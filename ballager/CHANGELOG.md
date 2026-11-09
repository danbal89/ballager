# Änderungen

## 1.0.2

- E-Mail-Einrichtung mit zwei Eingaben: Adresse + Passwort, Anbieter wird automatisch erkannt
  (Gmail, A1, GMX, WEB.DE, iCloud, Yahoo, Posteo, mailbox.org, T-Online) – mit Anleitung zum App-Passwort
- Automatische Ausweich-Server bei Verbindungsproblemen (z.B. A1: 587 → 465)
- Verständliche Meldungen bei falschem Passwort, abgelehnten Empfängern und Outlook/Hotmail
- Empfänger leer = Mail geht an die eigene Adresse
- Bestehende E-Mail-Einstellungen aus 1.0.x bleiben unverändert gültig

## 1.0.1

- Behoben: Kategorie ließ sich beim Anlegen/Bearbeiten ab dem zweiten Produkt nicht mehr auswählen und wurde nicht gespeichert
- Scanner: Kamera mit Full-HD-Auflösung und Dauer-Autofokus – Barcodes werden aus größerem Abstand erkannt
- Scanner: Zoom-Regler und Taschenlampe (sofern das Handy es unterstützt, meist Android)
- Hinweis zum idealen Abstand unter dem Scan-Rahmen
- Datenbank: `synchronous=FULL` – bestätigte Eingaben überstehen auch einen Stromausfall
- Datenbank: automatische Sicherheitskopie vor jedem Schema-Update; fehlgeschlagene Updates werden vollständig zurückgerollt
- Datenbank: vollständige Integritätsprüfung beim Start, Status unter „Daten & Sicherung“
- Datenbank: Schreibschutz, falls eine ältere BALLager-Version eine neuere Datenbank öffnet
- Sicherungen werden vor dem Ablegen geprüft; Sicherheitskopie vor Wiederherstellung enthält jetzt auch noch nicht eingearbeitete Änderungen
- Migrationen: robuste Zerlegung in Einzelbefehle
- Ungültige Eingaben liefern eine verständliche Meldung statt eines Serverfehlers
- Neu: automatische Tests (`tests/`) inkl. Update- und Absturztest

## 1.0.0

- Erste Version: Barcode-Scan (Live-Kamera und Foto), Produkterkennung über Open Food/Beauty/Products Facts
- Vorrat mit Chargen (Menge, Lagerort, Ablaufdatum), Entnahme nach dem Prinzip „zuerst Ablaufendes zuerst“
- Liste mit Suche, Filtern (Kategorie, Ort, Status) und Sortierung
- Einkaufsliste über Mindestbestand
- Tägliche Erinnerungen über Home Assistant, E-Mail (SMTP) und WhatsApp (CallMeBot)
- Home-Assistant-Sensoren, tägliche Datenbank-Sicherung, CSV-Export
