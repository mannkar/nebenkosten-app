"""objekt: Gesamtanteile (Nenner des Miteigentumsanteils)

Revision ID: 0009_objekt_gesamtanteile
Revises: 0008_objekt_ew_afa
Create Date: 2026-08-14

Neues optionales Feld "gesamtanteile" (Decimal/Float) auf Ebene Wohneinheit
(Objekt): die Anzahl der Gesamtanteile, auf die sich der Miteigentumsanteil
bezieht - bei kleineren WEGs meist 1000, bei sehr grossen Anlagen aber z.B.
auch 1000000. Bisher war dieser Nenner nur implizit Teil des freien
Textfelds "miteigentumsanteil" (z.B. "45,3/1000") - jetzt als eigenes,
separat auswertbares Feld.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_objekt_gesamtanteile"
down_revision = "0008_objekt_ew_afa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "objekt" not in inspector.get_table_names():
        return

    vorhandene_spalten = {s["name"] for s in inspector.get_columns("objekt")}
    if "gesamtanteile" in vorhandene_spalten:
        return

    with op.batch_alter_table("objekt") as batch:
        batch.add_column(sa.Column("gesamtanteile", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("objekt") as batch:
        batch.drop_column("gesamtanteile")
