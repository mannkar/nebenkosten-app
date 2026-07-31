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


class Mietverhaeltnis(Base):
    __tablename__ = "mietverhaeltnis"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    mietername = Column(String, nullable=False)
    einzug = Column(Date, nullable=False)
    auszug = Column(Date, nullable=True)  # NULL = aktuell noch Mieter
    kontakt = Column(String)
    notizen = Column(Text)

    objekt = relationship("Objekt", back_populates="mietverhaeltnisse")


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
    mietverhaeltnis_id = Column(Integer, ForeignKey("mietverhaeltnis.id"), nullable=False)
    tage_im_zeitraum = Column(Integer, nullable=False)
    summe_umlagefaehig = Column(Float, nullable=False)

    jahresabrechnung = relationship("Jahresabrechnung", back_populates="mieterabrechnungen")
    mietverhaeltnis = relationship("Mietverhaeltnis")
    positionen = relationship(
        "Mieterabrechnungsposition", back_populates="mieterabrechnung",
        cascade="all, delete-orphan"
    )


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
