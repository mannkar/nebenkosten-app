"""objekt: EW-Aktenzeichen + AfA-Jahresbetrag

Revision ID: 0008_objekt_ew_afa
Revises: 0007_kontakt_rollen
Create Date: 2026-08-14

Neue optionale Felder auf Ebene Wohneinheit (Objekt):
- ew_aktenzeichen: Aktenzeichen des Finanzamts fuer den Einheitswert
  (alphanumerisch, z.B. "12/345/67890").
- afa_jahresbetrag: jaehrlicher AfA-Betrag (Absetzung fuer Abnutzung,
  § 7 EStG) als fester Euro-Betrag pro Jahr.
Rein informativ fuer die steuerliche Zuordnung, ohne Einfluss auf die
Nebenkostenabrechnung selbst.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_objekt_ew_afa"
down_revision = "0007_kontakt_rollen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Absicherung falls das generische Sicherheitsnetz (0002) diese Spalten
    # auf manchen Umgebungen schon vorher als nullable ergaenzt hat: nur
    # hinzufuegen, was tatsaechlich noch fehlt (sonst "duplicate
    # column"-Fehler).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "objekt" not in inspector.get_table_names():
        return

    vorhandene_spalten = {s["name"] for s in inspector.get_columns("objekt")}
    alle_spalten = [
        sa.Column("ew_aktenzeichen", sa.String(), nullable=True),
        sa.Column("afa_jahresbetrag", sa.Float(), nullable=True),
    ]
    fehlende_spalten = [c for c in alle_spalten if c.name not in vorhandene_spalten]
    if not fehlende_spalten:
        return

    with op.batch_alter_table("objekt") as batch:
        for spalte in fehlende_spalten:
            batch.add_column(spalte)


def downgrade() -> None:
    with op.batch_alter_table("objekt") as batch:
        batch.drop_column("afa_jahresbetrag")
        batch.drop_column("ew_aktenzeichen")
