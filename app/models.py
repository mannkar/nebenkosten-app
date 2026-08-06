from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text,
    UniqueConstraint
)
from sqlalchemy.orm import relationship
from .database import Base


class Verwaltung(Base):
    __tablename__ = "verwaltung"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    ansprechpartner_anrede = Column(String)
    ansprechpartner_name = Column(String)
    strasse = Column(String)
    plz = Column(String)
    ort = Column(String)
    telefon = Column(String)
    email = Column(String)
    notizen = Column(Text)

    objekte = relationship("Objekt", back_populates="verwaltung")


class Objekt(Base):
    __tablename__ = "objekt"

    id = Column(Integer, primary_key=True)
    bezeichnung = Column(String, nullable=False)
    strasse = Column(String)
    plz = Column(String)
    ort = Column(String)
    flurstueck = Column(String)
    wohnflaeche_qm = Column(Float)
    miteigentumsanteil = Column(String)  # z.B. "45,3/1000"
    hausgeld_monatlich = Column(Float)
    verwaltung_id = Column(Integer, ForeignKey("verwaltung.id"), nullable=True)
    # Monat (1-12), in dem das Abrechnungsjahr beginnt. 1 = Kalenderjahr
    # (1.1.-31.12.), z.B. 7 = Wirtschaftsjahr 1.7.-30.6. des Folgejahres.
    abrechnung_start_monat = Column(Integer, nullable=False, default=1)
    notizen = Column(Text)

    verwaltung = relationship("Verwaltung", back_populates="objekte")
    mietverhaeltnisse = relationship(
        "Mietverhaeltnis", back_populates="objekt", cascade="all, delete-orphan"
    )
    jahresabrechnungen = relationship(
        "Jahresabrechnung", back_populates="objekt", cascade="all, delete-orphan"
    )
    zaehler = relationship(
        "Zaehler", back_populates="objekt", cascade="all, delete-orphan"
    )
    leerstaende = relationship(
        "Leerstand", back_populates="objekt", cascade="all, delete-orphan"
    )


class Mietverhaeltnis(Base):
    __tablename__ = "mietverhaeltnis"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    einzug = Column(Date, nullable=False)
    auszug = Column(Date, nullable=True)  # NULL = aktuell noch Mieter
    notizen = Column(Text)

    objekt = relationship("Objekt", back_populates="mietverhaeltnisse")
    personen = relationship(
        "Person", back_populates="mietverhaeltnis", cascade="all, delete-orphan",
        order_by="Person.id",
    )

    @property
    def anzeige_name(self) -> str:
        """Zusammengesetzter Name aller Personen des Mietverhaeltnisses,
        z.B. bei einer WG: 'Max Mustermann / Erika Musterfrau'."""
        namen = [f"{p.vorname} {p.nachname}".strip() for p in self.personen]
        return " / ".join(namen) if namen else "(keine Person erfasst)"


class Person(Base):
    """Eine Person innerhalb eines Mietverhaeltnisses. Ein Mietverhaeltnis
    kann beliebig viele Personen haben (z.B. Wohngemeinschaft)."""
    __tablename__ = "person"

    id = Column(Integer, primary_key=True)
    mietverhaeltnis_id = Column(Integer, ForeignKey("mietverhaeltnis.id"), nullable=False)
    anrede = Column(String)
    vorname = Column(String, nullable=False)
    nachname = Column(String, nullable=False)
    email = Column(String)
    telefon = Column(String)
    adresse_vor_strasse = Column(String)
    adresse_vor_plz = Column(String)
    adresse_vor_ort = Column(String)
    adresse_nach_strasse = Column(String)
    adresse_nach_plz = Column(String)
    adresse_nach_ort = Column(String)

    mietverhaeltnis = relationship("Mietverhaeltnis", back_populates="personen")


class Kostenart(Base):
    __tablename__ = "kostenart"

    id = Column(Integer, primary_key=True)
    bezeichnung = Column(String, nullable=False, unique=True)
    umlagefaehig = Column(Boolean, default=True, nullable=False)
    # zeitanteilig | gradtagszahl | zwischenablesung_wasser | manuell
    verteilmethode = Column(String, default="zeitanteilig", nullable=False)
    notizen = Column(Text)


class Jahresabrechnung(Base):
    __tablename__ = "jahresabrechnung"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    jahr = Column(Integer, nullable=False)
    zeitraum_von = Column(Date, nullable=False)
    zeitraum_bis = Column(Date, nullable=False)
    erhalten_am = Column(Date, nullable=True)
    status = Column(String, default="erfasst")  # erfasst | berechnet
    berechnet_am = Column(DateTime, nullable=True)
    # Vom Hausverwalter abgerechneter Gesamtbetrag fuer die Abrechnungsperiode
    # (Summe aller Positionen, umlagefaehig + nicht umlagefaehig). Dient als
    # Kontrollsumme gegen die selbst erfassten Abrechnungspositionen.
    hv_gesamtbetrag = Column(Float, nullable=True)
    notizen = Column(Text)

    __table_args__ = (UniqueConstraint("objekt_id", "jahr", name="uq_objekt_jahr"),)

    objekt = relationship("Objekt", back_populates="jahresabrechnungen")
    positionen = relationship(
        "Abrechnungsposition", back_populates="jahresabrechnung",
        cascade="all, delete-orphan"
    )
    mieterabrechnungen = relationship(
        "Mieterabrechnung", back_populates="jahresabrechnung",
        cascade="all, delete-orphan"
    )
    vorauszahlungen = relationship(
        "Vorauszahlung", back_populates="jahresabrechnung",
        cascade="all, delete-orphan"
    )


class Abrechnungsposition(Base):
    __tablename__ = "abrechnungsposition"

    id = Column(Integer, primary_key=True)
    jahresabrechnung_id = Column(Integer, ForeignKey("jahresabrechnung.id"), nullable=False)
    kostenart_id = Column(Integer, ForeignKey("kostenart.id"), nullable=False)
    betrag = Column(Float, nullable=False)
    umlagefaehig = Column(Boolean, nullable=False, default=True)
    bemerkung = Column(String)

    jahresabrechnung = relationship("Jahresabrechnung", back_populates="positionen")
    kostenart = relationship("Kostenart")


class Vorauszahlung(Base):
    """Vom Mieter geleistete Vorauszahlung fuer einen Abrechnungszeitraum,
    als Anzahl Monate x Betrag - z.B. bei einer unterjaehrigen Anpassung
    5 Monate zu 370 Euro + 7 Monate zu 385 Euro als zwei Eintraege."""
    __tablename__ = "vorauszahlung"

    id = Column(Integer, primary_key=True)
    jahresabrechnung_id = Column(Integer, ForeignKey("jahresabrechnung.id"), nullable=False)
    mietverhaeltnis_id = Column(Integer, ForeignKey("mietverhaeltnis.id"), nullable=False)
    anzahl_monate = Column(Integer, nullable=False)
    betrag_pro_monat = Column(Float, nullable=False)
    bemerkung = Column(String)  # z.B. "Jan-Mai" oder "ab Anpassung Juni"

    jahresabrechnung = relationship("Jahresabrechnung", back_populates="vorauszahlungen")
    mietverhaeltnis = relationship("Mietverhaeltnis")

    @property
    def summe(self) -> float:
        return self.anzahl_monate * self.betrag_pro_monat


class Zaehler(Base):
    __tablename__ = "zaehler"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    typ = Column(String, nullable=False)  # Kaltwasser | Warmwasser | Waerme | Strom
    zaehlernummer = Column(String)
    einbaudatum = Column(Date)
    ausbaudatum = Column(Date, nullable=True)  # NULL = aktuell verbaut

    objekt = relationship("Objekt", back_populates="zaehler")
    ablesungen = relationship(
        "Zaehlerstand", back_populates="zaehler", cascade="all, delete-orphan"
    )


class Zaehlerstand(Base):
    __tablename__ = "zaehlerstand"

    id = Column(Integer, primary_key=True)
    zaehler_id = Column(Integer, ForeignKey("zaehler.id"), nullable=False)
    datum = Column(Date, nullable=False)
    stand = Column(Float, nullable=False)
    # Jahresablesung | Zwischenablesung_Mieterwechsel | Einbau | Ausbau
    anlass = Column(String, nullable=False)

    zaehler = relationship("Zaehler", back_populates="ablesungen")


class Mieterabrechnung(Base):
    __tablename__ = "mieterabrechnung"

    id = Column(Integer, primary_key=True)
    jahresabrechnung_id = Column(Integer, ForeignKey("jahresabrechnung.id"), nullable=False)
    # NULL = Leerstandszeitraum (kein Mietverhaeltnis vorhanden). In dem Fall
    # sind von/bis gesetzt, damit der Zeitraum trotzdem bekannt ist.
    mietverhaeltnis_id = Column(Integer, ForeignKey("mietverhaeltnis.id"), nullable=True)
    von = Column(Date, nullable=True)
    bis = Column(Date, nullable=True)
    tage_im_zeitraum = Column(Integer, nullable=False)
    summe_umlagefaehig = Column(Float, nullable=False)
    summe_vorauszahlung = Column(Float, nullable=False, default=0.0)

    jahresabrechnung = relationship("Jahresabrechnung", back_populates="mieterabrechnungen")
    mietverhaeltnis = relationship("Mietverhaeltnis")
    positionen = relationship(
        "Mieterabrechnungsposition", back_populates="mieterabrechnung",
        cascade="all, delete-orphan"
    )

    @property
    def ist_leerstand(self) -> bool:
        return self.mietverhaeltnis_id is None

    @property
    def titel(self) -> str:
        if self.ist_leerstand:
            return f"Leerstand ({self.von} – {self.bis})"
        return self.mietverhaeltnis.anzeige_name

    @property
    def anzeige_von(self):
        return self.von if self.ist_leerstand else self.mietverhaeltnis.einzug

    @property
    def anzeige_bis(self):
        return self.bis if self.ist_leerstand else self.mietverhaeltnis.auszug

    @property
    def sortier_datum(self):
        """Fuer die chronologische Sortierung von Mieter- und
        Leerstands-Abrechnungen in der Ergebnisliste."""
        return self.anzeige_von

    @property
    def differenz(self) -> float:
        """Positiv = Nachzahlung des Mieters, negativ = Guthaben des Mieters.
        Bei Leerstand nicht relevant (keine Vorauszahlung vorhanden)."""
        return round(self.summe_umlagefaehig - self.summe_vorauszahlung, 2)


class Mieterabrechnungsposition(Base):
    __tablename__ = "mieterabrechnungsposition"

    id = Column(Integer, primary_key=True)
    mieterabrechnung_id = Column(Integer, ForeignKey("mieterabrechnung.id"), nullable=False)
    kostenart_id = Column(Integer, ForeignKey("kostenart.id"), nullable=False)
    anteil_betrag = Column(Float, nullable=False)
    berechnungsmethode = Column(String, nullable=False)
    berechnungsdetail = Column(String)  # kurzer Klartext, z.B. "184/365 Tage"

    mieterabrechnung = relationship("Mieterabrechnung", back_populates="positionen")
    kostenart = relationship("Kostenart")


class AenderungHistorie(Base):
    """Generisches Aenderungsprotokoll: pro geaendertem Feld ein Eintrag.

    entity_typ ist z.B. "Verwaltung", entity_id die ID des Datensatzes. So
    laesst sich die Historie spaeter auch fuer andere Tabellen (Objekt,
    Mietverhaeltnis, ...) ergaenzen, ohne das Schema aendern zu muessen."""
    __tablename__ = "aenderung_historie"

    id = Column(Integer, primary_key=True)
    entity_typ = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    feld = Column(String, nullable=False)
    alter_wert = Column(String)
    neuer_wert = Column(String)
    geaendert_am = Column(DateTime, nullable=False, default=datetime.utcnow)


class Leerstand(Base):
    """Erfasste Leerstandszeitraeume eines Objekts, z.B. die Luecke zwischen
    dem Auszug des einen und dem Einzug des naechsten Mietverhaeltnisses."""
    __tablename__ = "leerstand"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    von = Column(Date, nullable=False)
    bis = Column(Date, nullable=False)
    notizen = Column(Text)

    objekt = relationship("Objekt", back_populates="leerstaende")
