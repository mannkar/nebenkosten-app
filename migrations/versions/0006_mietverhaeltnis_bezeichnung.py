"""mietverhaeltnis: freie Bezeichnung (unabhaengig von den Mieternamen)

Revision ID: 0006_mietverhaeltnis_bezeichnung
Revises: 0005_fix_objekt_verwaltung_fk
Create Date: 2026-08-13

Neues optionales Feld "bezeichnung" am Mietverhaeltnis: ein frei vergebener
Anzeigename, der Vorrang vor dem automatisch aus den verknuepften Personen
zusammengesetzten Namen hat (z.B. "Schuster/Wilhelm" bei einer WG, deren
Mitglieder unter einem anderen Namen gefuehrt werden sollen).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_mietverhaeltnis_bezeichnung"
down_revision = "0005_fix_objekt_verwaltung_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Absicherung falls das generische Sicherheitsnetz (0002) diese Spalte
    # auf manchen Umgebungen schon vorher als nullable ergaenzt hat: nur
    # hinzufuegen, wenn sie tatsaechlich noch fehlt (sonst "duplicate
    # column"-Fehler).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    vorhandene_spalten = {s["name"] for s in inspector.get_columns("mietverhaeltnis")}
    if "bezeichnung" in vorhandene_spalten:
        return

    with op.batch_alter_table("mietverhaeltnis") as batch:
        batch.add_column(sa.Column("bezeichnung", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mietverhaeltnis") as batch:
        batch.drop_column("bezeichnung")
