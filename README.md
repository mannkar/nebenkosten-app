# Nebenkostenabrechnung – Kern-App (Schritt 1)

Kleine selbst gehostete App zur Verwaltung von Eigentumswohnungen, Mietverhältnissen
und Nebenkostenabrechnungen. Läuft als Docker-Container auf der Synology Rackstation,
Daten liegen zentral auf dem NAS, Zugriff von jedem Mac im Netz per Browser.

## Änderung: Verwaltung/Objekt jetzt bearbeitbar, Verwaltungs-Schema geändert

Das Datenmodell der Verwaltung hat sich geändert (Ansprechpartner in Anrede+Name,
Adresse in Straße/PLZ/Ort aufgesplittet) und es gibt eine neue Tabelle für die
Änderungshistorie. **Falls schon eine `data/nebenkosten.db` mit dem alten Schema
existiert, muss sie vor dem Neustart gelöscht werden**, sonst gibt es Fehler beim
Zugriff auf die Verwaltung-Tabelle:

```bash
cd /volume1/docker/nebenkosten-app/nk-app
sudo docker compose down
rm data/nebenkosten.db
sudo DOCKER_BUILDKIT=0 docker compose up -d --build
```

Neu: Objekte und Verwaltungen lassen sich jetzt nachträglich bearbeiten (Formular
auf der jeweiligen Detailseite), und bei Verwaltungen wird jede Änderung mit
Zeitstempel protokolliert (sichtbar unten auf der Verwaltungs-Detailseite).

## Datenbank-Schema ändern (Alembic-Migrationen)

Seit Einführung von Alembic muss bei Änderungen am Datenmodell (`app/models.py`)
**nicht mehr** `data/nebenkosten.db` gelöscht werden. Stattdessen:

1. `app/models.py` wie gewünscht ändern.
2. Lokal eine Migration generieren lassen:
   ```bash
   NK_DATA_DIR=./data alembic revision --autogenerate -m "kurze beschreibung"
   ```
   Das erzeugte Skript unter `alembic/versions/` kurz prüfen (bei SQLite
   übernimmt Alembic automatisch den nötigen Tabellen-Neubau im Hintergrund,
   sichtbar als `with op.batch_alter_table(...)`).
3. Migration lokal testen (App einfach neu starten – siehe unten).
4. Migrationsskript zusammen mit der Modelländerung committen/deployen.

Beim Start der App (lokal wie im Docker-Container) wendet `app/migrate.py`
automatisch alle noch ausstehenden Migrationen an – vorher wird die
SQLite-Datei automatisch als `nebenkosten.db.backup-<Zeitstempel>` gesichert.
Ein manuelles Löschen der Datenbank ist damit auch beim Produktiv-Deployment
auf dem NAS nicht mehr nötig; ein einfacher Neustart des Containers genügt.

## Was Schritt 1 kann

- Stammdaten: Verwaltungen, Objekte (Adresse, Flurstück, Wohnfläche, Miteigentumsanteil,
  Hausgeld), Mietverhältnisse (auch mehrere pro Jahr bei Mieterwechsel).
- Zentrale Kostenarten-Liste mit Umlagefähigkeit.
- Jahresabrechnungen je Objekt mit den von der Hausverwaltung gelieferten Positionen.
- Automatische zeitanteilige Verteilung aller umlagefähigen Positionen auf die im
  Zeitraum wohnenden Mieter (nach Kalendertagen), inkl. Ausweis von Leerstandstagen.
- CSV-Export aller Kerntabellen und der berechneten Mieterergebnisse.

## Was noch fehlt (Schritt 2, bewusst später)

- Heizkosten-Sonderlogik nach der Gradtagszahlentabelle (§ 9b HeizkV) für den
  verbrauchsunabhängigen Anteil bei Mieterwechsel.
- Wasser (Kalt/Warm/Abwasser): Verrechnung über tatsächliche Zwischenablesungen der
  Zähler inkl. Zählertausch-Historie. Die Tabellen `zaehler` und `zaehlerstand`
  sind im Schema bereits angelegt, damit das ohne Datenbank-Migration ergänzt
  werden kann.
- Objekte ohne Hausverwaltung (komplette Umlage inkl. WEG-Ebene selbst abbilden).

Bis Schritt 2 umgesetzt ist, werden auch Heizung/Wasser wie alle anderen Positionen
zeitanteilig verteilt – das ist bei einem einzigen Mieter im Jahr bereits korrekt und
nur bei unterjährigem Wechsel eine Vereinfachung.

## Deployment auf der Synology (Docker + Portainer)

### Schritt A: Dateien auf die NAS kopieren

1. In DSM eine freigegebene Ordnerstruktur anlegen, z. B. `/volume1/docker/nebenkosten-app/`.
2. Diesen kompletten Ordner (`nk-app/` mit `app/`, `Dockerfile`, `docker-compose.yml`,
   `requirements.txt`) per File Station oder SMB dort hineinkopieren.

### Schritt B: Container bauen und starten

Am zuverlässigsten geht das per SSH (DSM: Systemsteuerung → Terminal & SNMP → SSH aktivieren):

```bash
ssh admin@<NAS-IP>
cd /volume1/docker/nebenkosten-app
docker compose up -d --build
```

Der Container erscheint danach automatisch auch in Portainer (gleicher Docker-Daemon),
dort lassen sich Logs, Neustarts etc. bequem verwalten.

**Alternative ganz ohne SSH:** In Portainer unter *Stacks → Add stack* den Namen vergeben
und als Build-Methode *Repository* wählen, falls der Ordner in einem (auch lokalen)
Git-Repository liegt. Ohne Git ist der SSH-Weg oben der einfachste, weil Portainers
Web-Editor keinen Datei-Upload für einen kompletten Build-Kontext samt Unterordnern
unterstützt.

### Schritt C: Aufrufen

Im Browser von jedem Mac im Netz: `http://<NAS-IP>:8420`

Die SQLite-Datenbank liegt unter `/volume1/docker/nebenkosten-app/data/nebenkosten.db`
auf dem NAS-Storage – Backups also einfach über die normale NAS-Datensicherung
(Hyper Backup o. ä.) mit abdecken.

### CSV-Export

Über die Buttons in der App, oder direkt per URL, z. B.:
`http://<NAS-IP>:8420/export/objekt.csv`

## Lokal testen (ohne Docker)

```bash
cd nk-app
pip install -r requirements.txt --break-system-packages
NK_DATA_DIR=./data uvicorn app.main:app --reload
```

Dann `http://localhost:8000` öffnen.
