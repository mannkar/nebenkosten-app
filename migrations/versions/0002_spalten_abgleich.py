"""Sicherheitsnetz: fehlende Spalten an bereits bestehenden Tabellen ergaenzen

Revision ID: 0002_spalten_abgleich
Revises: 0001_baseline
Create Date: 2026-08-14

Die Baseline-Migration (0001) nutzt Base.metadata.create_all(), das legt nur
komplett fehlende TABELLEN an - fehlende SPALTEN an einer bereits
bestehenden Tabelle werden dabei stillschweigend ignoriert. Genau das ist
beim ersten Produktiv-Deployment auf der NAS passiert: die dortige
Datenbank hatte noch den Tabellenstand von vor mehreren Modelländerungen
(Personenzahl-Historie, MwSt-Felder, Absenderdaten ...), wodurch der
Container beim ersten Zugriff auf eine der neuen Spalten abgestuerzt ist.

Diese Migration vergleicht bei jedem Start per SQLAlchemy-Introspektion die
tatsaechlich vorhandenen Spalten jeder Tabelle mit dem aktuellen Modell und
ergaenzt fehlende Spalten automatisch per ALTER TABLE (ueber Alembics
Batch-Modus, der das fuer SQLite intern korrekt handhabt). Fehlende
Tabellen werden ebenfalls angelegt, falls es doch mal eine geben sollte.
Bestehende Daten bleiben dabei erhalten - kein Loeschen der DB mehr noetig.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_spalten_abgleich"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    from app import models  # noqa: F401  (registriert alle Modelle an Base.metadata)

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    bestehende_tabellen = set(inspector.get_table_names())

    for tabelle in Base.metadata.sorted_tables:
        if tabelle.name not in bestehende_tabellen:
            # Komplette Tabelle fehlt noch (z.B. neu hinzugekommenes Modell) -
            # normal anlegen.
            tabelle.create(bind=bind)
            continue

        vorhandene_spalten = {s["name"] for s in inspector.get_columns(tabelle.name)}
        fehlende_spalten = [c for c in tabelle.columns if c.name not in vorhandene_spalten]
        if not fehlende_spalten:
            continue

        with op.batch_alter_table(tabelle.name) as batch:
            for spalte in fehlende_spalten:
                neue_spalte = spalte.copy()
                # SQLite verlangt fuer eine per ALTER TABLE ergaenzte NOT
                # NULL-Spalte immer einen DEFAULT-Wert - das Modell selbst
                # setzt i.d.R. nur einen clientseitigen ORM-default (kein
                # server_default), was sonst zu "Cannot add a NOT NULL
                # column with default value NULL" fuehrt. Da diese Migration
                # als generisches Sicherheitsnetz fuer beliebige zukuenftige
                # Modelländerungen dient (nicht nur die zum Zeitpunkt ihrer
                # Erstellung bekannten), wird hier bewusst immer nullable
                # ergaenzt statt modellspezifische Defaults zu erraten. Neue
                # echte Pflichtfelder gehoeren ohnehin in eine eigene,
                # bewusste Migration mit explizitem server_default (siehe
                # z.B. 0003) - die läuft vor dieser hier ggf. bereits, dann
                # ist die Spalte hier schon vorhanden und wird uebersprungen.
                neue_spalte.nullable = True
                batch.add_column(neue_spalte)


def downgrade() -> None:
    # Bewusst kein Downgrade: welche Spalten dabei urspruenglich "fehlend"
    # waren, ist danach nicht mehr rekonstruierbar - und es besteht die
    # Gefahr, versehentlich Spalten mit echten Daten zu loeschen.
    pass
