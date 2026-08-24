"""Fix: objekt.verwaltung_id zeigt noch auf die geloeschte Tabelle "verwaltung"

Revision ID: 0005_fix_objekt_verwaltung_fk
Revises: 0004_kontakt_adressbuch
Create Date: 2026-08-13

In 0004 wurde die "objekt"-Tabelle am Ende per batch_alter_table umgebaut, um
die alten Absender-/Bankspalten zu entfernen. SQLites Batch-Modus rekonstruiert
eine Tabelle dabei aus der REFLEKTIERTEN (tatsaechlich vorhandenen) Struktur
plus den angeforderten Operationen - nicht aus dem aktuellen Python-Modell.
Da die Fremdschluessel-Definition von "verwaltung_id" dabei nicht explizit
angefasst wurde, blieb die ALTE (zum Zeitpunkt der urspruenglichen Tabelle
gueltige) Referenz auf "verwaltung.id" unveraendert erhalten - obwohl die
Werte in der Spalte laengst auf neue "kontakt"-IDs umgestellt wurden und die
Tabelle "verwaltung" direkt im Anschluss gelöscht wird. Ergebnis: ein
dangling Fremdschluessel auf eine nicht mehr existierende Tabelle, der bei
JEDEM Insert/Update auf "objekt" mit "no such table: main.verwaltung"
fehlschlaegt, sobald PRAGMA foreign_keys=ON aktiv ist (siehe
app/database.py - immer der Fall).

Diese Migration baut "objekt" einmalig neu auf (mit explizit korrekter
Zieldefinition statt automatischer Reflektion), damit verwaltung_id wie
eigentuemer_id auf kontakt.id zeigt. Bestehende Daten/Werte bleiben dabei
unveraendert - nur die Fremdschluessel-Definition wird korrigiert.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_fix_objekt_verwaltung_fk"
down_revision = "0004_kontakt_adressbuch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "objekt" not in inspector.get_table_names():
        return

    # Idempotent: nur ausfuehren, wenn die kaputte Referenz tatsaechlich noch
    # da ist (z.B. falls diese Migration auf einer bereits sauberen DB
    # laeuft, etwa bei einer brandneuen Installation ab 0004+0005 in einem
    # Rutsch).
    kaputte_fk_vorhanden = any(
        fk.get("referred_table") == "verwaltung"
        for fk in inspector.get_foreign_keys("objekt")
    )
    if not kaputte_fk_vorhanden:
        return

    ziel_tabelle = sa.Table(
        "objekt", sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("bezeichnung", sa.String, nullable=False),
        sa.Column("strasse", sa.String),
        sa.Column("plz", sa.String),
        sa.Column("ort", sa.String),
        sa.Column("flurstueck", sa.String),
        sa.Column("wohnflaeche_qm", sa.Float),
        sa.Column("miteigentumsanteil", sa.String),
        sa.Column("hausgeld_monatlich", sa.Float),
        sa.Column("eigentuemer_id", sa.Integer, sa.ForeignKey("kontakt.id"), nullable=True),
        sa.Column("verwaltung_id", sa.Integer, sa.ForeignKey("kontakt.id"), nullable=True),
        sa.Column("abrechnung_start_monat", sa.Integer, nullable=False),
        sa.Column("notizen", sa.Text),
    )

    with op.batch_alter_table("objekt", copy_from=ziel_tabelle, recreate="always"):
        pass


def downgrade() -> None:
    # Bewusst kein Downgrade - die vorherige (kaputte) FK-Referenz auf die
    # bereits geloeschte Tabelle "verwaltung" wiederherzustellen ergibt
    # keinen Sinn.
    pass
