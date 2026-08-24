"""Zentrales Adressbuch (Kontakt/KontaktAdresse) statt Verwaltung/Person/
Objekt-Absenderfeldern

Revision ID: 0004_kontakt_adressbuch
Revises: 0003_firma_rechnungsadresse
Create Date: 2026-08-17

Grosser Umbau: fuehrt eine gemeinsame Tabelle "kontakt" (Adressbuch) fuer
Eigentuemer, Verwaltung und Mieter ein, mit einer Adresshistorie
"kontakt_adresse" (gueltig_von/gueltig_bis statt fixer "vor/nach"-Felder).

Datenuebernahme (verlustfrei, bestehende Zeilen werden migriert statt
geloescht):
- Jede bestehende "verwaltung"-Zeile wird ein Kontakt (ist_firma=True,
  firma_name=Name der Verwaltung, Ansprechpartner-Anrede/-Name wandern in
  anrede/vorname). objekt.verwaltung_id wird auf die neue kontakt.id
  umgemappt, die Aenderungshistorie (entity_typ="Verwaltung") wird auf
  "Kontakt" umgehaengt.
- Objekte mit ausgefuellten Absender-/Bankfeldern bekommen einen neuen
  Eigentuemer-Kontakt (objekt.eigentuemer_id). Mehrere Objekte mit exakt
  identischen Absenderdaten bekommen bewusst nur EINEN gemeinsamen Kontakt
  (deckt genau den Anwendungsfall "mehrere Objekte, gleicher Eigentuemer" ab).
- Jede bestehende "person"-Zeile wird ein Kontakt (ist_firma=False), ueber
  "mietverhaeltnis_kontakt" mit ihrem Mietverhaeltnis verknuepft. Die
  bisherigen Felder adresse_vor_*/adresse_nach_* werden zu zwei
  kontakt_adresse-Zeilen mit passendem gueltig_von/gueltig_bis (Ende der
  Vor-Adresse = Tag vor Einzug, Beginn der Nach-Adresse = Tag nach Auszug).

Anschliessend werden die alten Tabellen "verwaltung"/"person" sowie die
Absender-/Bankspalten an "objekt" entfernt.

Alle Schritte sind defensiv gegen unterschiedliche Ausgangszustaende
geschrieben (bereits durch 0002 vorab angelegte leere Kontakt-Tabellen,
bereits vorhandene eigentuemer_id-Spalte, etc.) - siehe die einzelnen
Existenz-Pruefungen unten.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_kontakt_adressbuch"
down_revision = "0003_firma_rechnungsadresse"
branch_labels = None
depends_on = None


def _als_datum(wert):
    if wert is None:
        return None
    if isinstance(wert, date):
        return wert
    return date.fromisoformat(str(wert))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tabellen = set(inspector.get_table_names())

    # ------------------------------------------------ neue Tabellen anlegen
    if "kontakt" not in tabellen:
        op.create_table(
            "kontakt",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("ist_firma", sa.Boolean, nullable=False, server_default=sa.text("0")),
            sa.Column("anrede", sa.String),
            sa.Column("vorname", sa.String),
            sa.Column("nachname", sa.String),
            sa.Column("firma_name", sa.String),
            sa.Column("telefon", sa.String),
            sa.Column("email", sa.String),
            sa.Column("bank_name", sa.String),
            sa.Column("bank_iban", sa.String),
            sa.Column("bank_bic", sa.String),
            sa.Column("notizen", sa.Text),
        )
    if "kontakt_adresse" not in tabellen:
        op.create_table(
            "kontakt_adresse",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("kontakt_id", sa.Integer, sa.ForeignKey("kontakt.id"), nullable=False),
            sa.Column("strasse", sa.String),
            sa.Column("plz", sa.String),
            sa.Column("ort", sa.String),
            sa.Column("gueltig_von", sa.Date),
            sa.Column("gueltig_bis", sa.Date),
            sa.Column("notizen", sa.String),
        )
    if "mietverhaeltnis_kontakt" not in tabellen:
        op.create_table(
            "mietverhaeltnis_kontakt",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "mietverhaeltnis_id", sa.Integer, sa.ForeignKey("mietverhaeltnis.id"),
                nullable=False,
            ),
            sa.Column("kontakt_id", sa.Integer, sa.ForeignKey("kontakt.id"), nullable=False),
            sa.UniqueConstraint("mietverhaeltnis_id", "kontakt_id", name="uq_mv_kontakt"),
        )

    kontakt_tbl = sa.Table("kontakt", sa.MetaData(), autoload_with=bind)
    kontakt_adresse_tbl = sa.Table("kontakt_adresse", sa.MetaData(), autoload_with=bind)
    mvk_tbl = sa.Table("mietverhaeltnis_kontakt", sa.MetaData(), autoload_with=bind)

    objekt_spalten = {c["name"] for c in inspector.get_columns("objekt")}
    hat_alte_objekt_felder = "absender_name" in objekt_spalten

    # --------------------------------------------------- Verwaltung -> Kontakt
    if "verwaltung" in tabellen:
        verwaltung_tbl = sa.Table("verwaltung", sa.MetaData(), autoload_with=bind)
        objekt_alt_tbl = sa.Table("objekt", sa.MetaData(), autoload_with=bind)
        verwaltung_zu_kontakt = {}
        for row in bind.execute(sa.select(verwaltung_tbl)).mappings():
            ergebnis = bind.execute(kontakt_tbl.insert().values(
                ist_firma=True,
                firma_name=row["name"],
                anrede=row["ansprechpartner_anrede"],
                vorname=row["ansprechpartner_name"],
                telefon=row["telefon"],
                email=row["email"],
                notizen=row["notizen"],
            ))
            neue_id = ergebnis.inserted_primary_key[0]
            verwaltung_zu_kontakt[row["id"]] = neue_id
            if row["strasse"] or row["plz"] or row["ort"]:
                bind.execute(kontakt_adresse_tbl.insert().values(
                    kontakt_id=neue_id,
                    strasse=row["strasse"], plz=row["plz"], ort=row["ort"],
                ))

        for alte_id, neue_id in verwaltung_zu_kontakt.items():
            bind.execute(
                objekt_alt_tbl.update()
                .where(objekt_alt_tbl.c.verwaltung_id == alte_id)
                .values(verwaltung_id=neue_id)
            )

        if "aenderung_historie" in tabellen and verwaltung_zu_kontakt:
            hist_tbl = sa.Table("aenderung_historie", sa.MetaData(), autoload_with=bind)
            for alte_id, neue_id in verwaltung_zu_kontakt.items():
                bind.execute(
                    hist_tbl.update()
                    .where(hist_tbl.c.entity_typ == "Verwaltung", hist_tbl.c.entity_id == alte_id)
                    .values(entity_typ="Kontakt", entity_id=neue_id)
                )

    # -------------------------------------- Objekt-Absenderdaten -> Kontakt
    objekt_zu_eigentuemer: dict[int, int] = {}
    if hat_alte_objekt_felder:
        objekt_tbl = sa.Table("objekt", sa.MetaData(), autoload_with=bind)
        gruppen = defaultdict(list)
        for row in bind.execute(sa.select(objekt_tbl)).mappings():
            werte = tuple(
                (row[feld] or "").strip() or None
                for feld in [
                    "absender_name", "absender_strasse", "absender_plz", "absender_ort",
                    "absender_telefon", "absender_email", "bank_name", "bank_iban", "bank_bic",
                ]
            )
            if not any(werte):
                continue
            gruppen[werte].append(row["id"])

        for werte, objekt_ids in gruppen.items():
            (name, strasse, plz, ort, telefon, email, bank_name, bank_iban, bank_bic) = werte
            ergebnis = bind.execute(kontakt_tbl.insert().values(
                ist_firma=True,
                firma_name=name,
                telefon=telefon,
                email=email,
                bank_name=bank_name, bank_iban=bank_iban, bank_bic=bank_bic,
            ))
            neue_id = ergebnis.inserted_primary_key[0]
            if strasse or plz or ort:
                bind.execute(kontakt_adresse_tbl.insert().values(
                    kontakt_id=neue_id, strasse=strasse, plz=plz, ort=ort,
                ))
            for oid in objekt_ids:
                objekt_zu_eigentuemer[oid] = neue_id

    # ------------------------------------------------------- Person -> Kontakt
    if "person" in tabellen:
        person_tbl = sa.Table("person", sa.MetaData(), autoload_with=bind)
        mv_tbl = sa.Table("mietverhaeltnis", sa.MetaData(), autoload_with=bind)
        mv_daten = {
            row["id"]: (row["einzug"], row["auszug"])
            for row in bind.execute(
                sa.select(mv_tbl.c.id, mv_tbl.c.einzug, mv_tbl.c.auszug)
            ).mappings()
        }
        for row in bind.execute(sa.select(person_tbl)).mappings():
            ergebnis = bind.execute(kontakt_tbl.insert().values(
                ist_firma=False,
                anrede=row["anrede"], vorname=row["vorname"], nachname=row["nachname"],
                telefon=row["telefon"], email=row["email"],
            ))
            neue_id = ergebnis.inserted_primary_key[0]
            bind.execute(mvk_tbl.insert().values(
                mietverhaeltnis_id=row["mietverhaeltnis_id"], kontakt_id=neue_id,
            ))

            einzug, auszug = mv_daten.get(row["mietverhaeltnis_id"], (None, None))
            if row["adresse_vor_strasse"] or row["adresse_vor_plz"] or row["adresse_vor_ort"]:
                einzug_datum = _als_datum(einzug)
                bis = (einzug_datum - timedelta(days=1)) if einzug_datum else None
                bind.execute(kontakt_adresse_tbl.insert().values(
                    kontakt_id=neue_id,
                    strasse=row["adresse_vor_strasse"], plz=row["adresse_vor_plz"],
                    ort=row["adresse_vor_ort"], gueltig_bis=bis,
                ))
            if row["adresse_nach_strasse"] or row["adresse_nach_plz"] or row["adresse_nach_ort"]:
                auszug_datum = _als_datum(auszug)
                von = (auszug_datum + timedelta(days=1)) if auszug_datum else None
                bind.execute(kontakt_adresse_tbl.insert().values(
                    kontakt_id=neue_id,
                    strasse=row["adresse_nach_strasse"], plz=row["adresse_nach_plz"],
                    ort=row["adresse_nach_ort"], gueltig_von=von,
                ))

    # --------------------------------------------- objekt.eigentuemer_id
    if "eigentuemer_id" not in objekt_spalten:
        # Spalte zunaechst OHNE Inline-ForeignKey anlegen und den Fremdschluessel
        # danach separat MIT explizitem Namen ergaenzen: SQLites Batch-Modus baut
        # die Tabelle bei einer ALTER TABLE-Operation intern neu auf und verlangt
        # dafuer, dass jeder Constraint (auch Fremdschluessel) einen Namen hat -
        # ein per sa.Column(..., sa.ForeignKey(...)) inline erzeugter Constraint
        # ist unbenannt und fuehrt sonst zu "ValueError: Constraint must have a
        # name".
        with op.batch_alter_table("objekt") as batch:
            batch.add_column(sa.Column("eigentuemer_id", sa.Integer, nullable=True))
        with op.batch_alter_table("objekt") as batch:
            batch.create_foreign_key(
                "fk_objekt_eigentuemer_id_kontakt", "kontakt", ["eigentuemer_id"], ["id"]
            )

    for oid, kid in objekt_zu_eigentuemer.items():
        bind.execute(
            sa.text("UPDATE objekt SET eigentuemer_id = :kid WHERE id = :oid"),
            {"kid": kid, "oid": oid},
        )

    # ------------------------------------------ alte Spalten/Tabellen entfernen
    if hat_alte_objekt_felder:
        with op.batch_alter_table("objekt") as batch:
            for spalte in [
                "absender_name", "absender_strasse", "absender_plz", "absender_ort",
                "absender_telefon", "absender_email", "bank_name", "bank_iban", "bank_bic",
            ]:
                batch.drop_column(spalte)

    if "person" in tabellen:
        op.drop_table("person")
    if "verwaltung" in tabellen:
        op.drop_table("verwaltung")


def downgrade() -> None:
    # Bewusst kein Downgrade: die Ruecktransformation (Kontakt/KontaktAdresse
    # zurueck in Verwaltung/Person/Objekt-Absenderfelder inkl. Aufloesung der
    # Adresshistorie in genau zwei "vor/nach"-Zeilen) waere verlustbehaftet
    # und fehleranfaellig. Vor dieser Migration wurde ein Backup empfohlen.
    pass
