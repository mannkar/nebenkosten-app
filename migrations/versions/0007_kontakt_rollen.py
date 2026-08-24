"""kontakt: Rollen-Flags (Mieter/Eigentuemer/Verwalter/Handwerker)

Revision ID: 0007_kontakt_rollen
Revises: 0006_mietverhaeltnis_bezeichnung
Create Date: 2026-08-13

Neue Boolean-Spalten am Kontakt fuer die Vorfilterung der Auswahllisten in
den jeweiligen Formularen (Eigentuemer-/Verwaltung-Auswahl am Objekt,
Mieter-Auswahl im Mietverhaeltnis). Die Flags schliessen sich nicht
gegenseitig aus.

Backfill fuer bestehende Daten: damit bereits verwendete Kontakte nach
diesem Update nicht ploetzlich aus den (jetzt gefilterten) Auswahllisten
verschwinden, werden die Flags anhand der TATSAECHLICHEN aktuellen Nutzung
gesetzt (ein Objekt referenziert den Kontakt als Eigentuemer/Verwaltung,
bzw. der Kontakt ist ueber mietverhaeltnis_kontakt mit einem Mietverhaeltnis
verknuepft). ist_handwerker wird nirgends automatisch gesetzt, da es dafuer
noch keine bestehende Verknuepfung im Datenmodell gibt.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_kontakt_rollen"
down_revision = "0006_mietverhaeltnis_bezeichnung"
branch_labels = None
depends_on = None

_NEUE_SPALTEN = ["ist_mieter", "ist_eigentuemer", "ist_verwalter", "ist_handwerker"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tabellen = set(inspector.get_table_names())
    if "kontakt" not in tabellen:
        return

    vorhandene_spalten = {s["name"] for s in inspector.get_columns("kontakt")}
    fehlende_spalten = [n for n in _NEUE_SPALTEN if n not in vorhandene_spalten]
    if fehlende_spalten:
        with op.batch_alter_table("kontakt") as batch:
            for name in fehlende_spalten:
                batch.add_column(
                    sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("0"))
                )

    # Backfill anhand bestehender Nutzung - nur fuer Spalten, die diese
    # Migration gerade selbst angelegt hat (bei einem bereits vorher
    # gesetzten Flag, z.B. weil ein Nutzer es schon manuell geaendert hat,
    # nichts ueberschreiben).
    if "ist_eigentuemer" in fehlende_spalten and "objekt" in tabellen:
        objekt_spalten = {c["name"] for c in inspector.get_columns("objekt")}
        if "eigentuemer_id" in objekt_spalten:
            bind.execute(sa.text(
                "UPDATE kontakt SET ist_eigentuemer = 1 WHERE id IN "
                "(SELECT DISTINCT eigentuemer_id FROM objekt WHERE eigentuemer_id IS NOT NULL)"
            ))

    if "ist_verwalter" in fehlende_spalten and "objekt" in tabellen:
        objekt_spalten = {c["name"] for c in inspector.get_columns("objekt")}
        if "verwaltung_id" in objekt_spalten:
            bind.execute(sa.text(
                "UPDATE kontakt SET ist_verwalter = 1 WHERE id IN "
                "(SELECT DISTINCT verwaltung_id FROM objekt WHERE verwaltung_id IS NOT NULL)"
            ))

    if "ist_mieter" in fehlende_spalten and "mietverhaeltnis_kontakt" in tabellen:
        bind.execute(sa.text(
            "UPDATE kontakt SET ist_mieter = 1 WHERE id IN "
            "(SELECT DISTINCT kontakt_id FROM mietverhaeltnis_kontakt)"
        ))


def downgrade() -> None:
    with op.batch_alter_table("kontakt") as batch:
        for name in reversed(_NEUE_SPALTEN):
            batch.drop_column(name)
