"""mietverhaeltnis: Firma-Kennzeichnung + abweichende NK-Rechnungsadresse

Revision ID: 0003_firma_rechnungsadresse
Revises: 0002_spalten_abgleich
Create Date: 2026-08-14

Neue Felder am Mietverhaeltnis:
- ist_firma / firma_name: Kennzeichnung als gewerblicher Mieter (Firma statt
  Privatperson) inkl. Firmenname fuer Anschrift/Anrede im PDF-Anschreiben.
- nk_rechnungsadresse_*: optionale abweichende Rechnungsadresse fuer die
  Nebenkostenabrechnung (z.B. Verwaltung/Buchhaltung statt Mieter selbst).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_firma_rechnungsadresse"
down_revision = "0002_spalten_abgleich"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Absicherung falls das generische Sicherheitsnetz (0002) diese Spalten
    # - inzwischen als nullable ergaenzt durch neuere models.py-Stände - auf
    # manchen Umgebungen schon vorher angelegt hat: nur ergaenzen, was
    # tatsaechlich noch fehlt, statt blind alle Spalten hinzuzufuegen (sonst
    # "duplicate column"-Fehler).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    vorhandene_spalten = {s["name"] for s in inspector.get_columns("mietverhaeltnis")}

    alle_spalten = [
        sa.Column("ist_firma", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("firma_name", sa.String(), nullable=True),
        sa.Column(
            "nk_rechnungsadresse_abweichend", sa.Boolean(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("nk_rechnungsadresse_name", sa.String(), nullable=True),
        sa.Column("nk_rechnungsadresse_strasse", sa.String(), nullable=True),
        sa.Column("nk_rechnungsadresse_plz", sa.String(), nullable=True),
        sa.Column("nk_rechnungsadresse_ort", sa.String(), nullable=True),
    ]
    fehlende_spalten = [c for c in alle_spalten if c.name not in vorhandene_spalten]
    if not fehlende_spalten:
        return

    with op.batch_alter_table("mietverhaeltnis") as batch:
        for spalte in fehlende_spalten:
            batch.add_column(spalte)


def downgrade() -> None:
    with op.batch_alter_table("mietverhaeltnis") as batch:
        batch.drop_column("nk_rechnungsadresse_ort")
        batch.drop_column("nk_rechnungsadresse_plz")
        batch.drop_column("nk_rechnungsadresse_strasse")
        batch.drop_column("nk_rechnungsadresse_name")
        batch.drop_column("nk_rechnungsadresse_abweichend")
        batch.drop_column("firma_name")
        batch.drop_column("ist_firma")
