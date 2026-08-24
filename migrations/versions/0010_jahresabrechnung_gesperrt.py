"""jahresabrechnung: Sperr-Flag gegen unabsichtliche Aenderungen

Revision ID: 0010_jahresabrechnung_gesperrt
Revises: 0009_objekt_gesamtanteile
Create Date: 2026-08-24

Neues Feld "gesperrt" (Boolean, Default false) auf Jahresabrechnung: wenn
gesetzt, blockieren alle aendernden Routen (Positionen, Vorauszahlungen,
Berechnung, Loeschen) und verlangen zuerst ein explizites Entsperren ueber
die UI. Schuetzt bereits geprueft/fertig abgeschlossene Abrechnungen vor
versehentlichen Aenderungen.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010_jahresabrechnung_gesperrt"
down_revision = "0009_objekt_gesamtanteile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "jahresabrechnung" not in inspector.get_table_names():
        return

    vorhandene_spalten = {s["name"] for s in inspector.get_columns("jahresabrechnung")}
    if "gesperrt" in vorhandene_spalten:
        return

    with op.batch_alter_table("jahresabrechnung") as batch:
        batch.add_column(
            sa.Column("gesperrt", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("jahresabrechnung") as batch:
        batch.drop_column("gesperrt")
