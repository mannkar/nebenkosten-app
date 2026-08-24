from datetime import date, datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text,
    UniqueConstraint
)
from sqlalchemy.orm import relationship
from .database import Base


class Kontakt(Base):
    """Zentrales Adressbuch: eine Person oder Firma, die in einer oder
    mehreren Rollen auftreten kann - als Eigentuemer und/oder Verwaltung
    eines oder mehrerer Objekte, oder als Mieter (ueber
    MietverhaeltnisKontakt). Ein Kontakt muss nur einmal angelegt werden und
    laesst sich beliebig oft wiederverwenden, statt Adressdaten mehrfach
    einzutippen."""
    __tablename__ = "kontakt"

    id = Column(Integer, primary_key=True)
    ist_firma = Column(Boolean, default=False, nullable=False)
    anrede = Column(String)
    vorname = Column(String)
    nachname = Column(String)
    firma_name = Column(String)
    telefon = Column(String)
    email = Column(String)
    # Bankdaten - i.d.R. nur relevant, wenn der Kontakt als Eigentuemer
    # (Empfaenger von Nachzahlungen/Absender im PDF-Anschreiben) verwendet wird.
    bank_name = Column(String)
    bank_iban = Column(String)
    bank_bic = Column(String)
    notizen = Column(Text)
    # Rollen-Flags: rein informativ/zur Vorfilterung der Auswahllisten in
    # den jeweiligen Formularen gedacht (z.B. zeigt die Eigentuemer-Auswahl
    # am Objekt nur Kontakte mit ist_eigentuemer=True) - schliessen sich
    # NICHT gegenseitig aus, ein Kontakt kann mehrere Rollen gleichzeitig
    # haben (z.B. Verwaltung UND Handwerker).
    ist_mieter = Column(Boolean, default=False, nullable=False)
    ist_eigentuemer = Column(Boolean, default=False, nullable=False)
    ist_verwalter = Column(Boolean, default=False, nullable=False)
    ist_handwerker = Column(Boolean, default=False, nullable=False)

    adressen = relationship(
        "KontaktAdresse", back_populates="kontakt", cascade="all, delete-orphan",
        order_by="KontaktAdresse.gueltig_von",
    )
    objekte_als_eigentuemer = relationship(
        "Objekt", back_populates="eigentuemer", foreign_keys="Objekt.eigentuemer_id",
    )
    objekte_als_verwaltung = relationship(
        "Objekt", back_populates="verwaltung", foreign_keys="Objekt.verwaltung_id",
    )
    mietverhaeltnis_links = relationship(
        "MietverhaeltnisKontakt", back_populates="kontakt", cascade="all, delete-orphan",
    )

    @property
    def anzeige_name(self) -> str:
        if self.ist_firma and self.firma_name:
            return self.firma_name
        name = f"{self.vorname or ''} {self.nachname or ''}".strip()
        return name or self.firma_name or "(ohne Namen)"

    @property
    def aktuelle_adresse(self):
        """Die aktuell gueltige Adresse (gueltig_von in der Vergangenheit/
        heute, gueltig_bis leer oder in der Zukunft). Gibt es keine aktuell
        laufende, wird die zuletzt gueltige zurueckgegeben (z.B. eine
        Vor-Adresse, die zwar "beendet" ist, aber noch die einzige bekannte
        Anschrift darstellt). None, wenn ueberhaupt keine Adresse erfasst ist."""
        if not self.adressen:
            return None
        heute = date.today()
        laufende = [
            a for a in self.adressen
            if (a.gueltig_von is None or a.gueltig_von <= heute)
            and (a.gueltig_bis is None or a.gueltig_bis >= heute)
        ]
        kandidaten = laufende or list(self.adressen)
        return sorted(kandidaten, key=lambda a: a.gueltig_von or date.min)[-1]


class KontaktAdresse(Base):
    """Eine Adresse eines Kontakts mit Gueltigkeitszeitraum - ein Kontakt
    kann im Lauf der Zeit mehrere Adressen haben (z.B. ein Mieter: Adresse
    vor Einzug, dann nach Auszug eine neue). gueltig_von/gueltig_bis = NULL
    bedeutet "schon immer" bzw. "bis auf Weiteres/aktuell"."""
    __tablename__ = "kontakt_adresse"

    id = Column(Integer, primary_key=True)
    kontakt_id = Column(Integer, ForeignKey("kontakt.id"), nullable=False)
    strasse = Column(String)
    plz = Column(String)
    ort = Column(String)
    gueltig_von = Column(Date, nullable=True)
    gueltig_bis = Column(Date, nullable=True)
    notizen = Column(String)

    kontakt = relationship("Kontakt", back_populates="adressen")


class MietverhaeltnisKontakt(Base):
    """Verknuepft ein Mietverhaeltnis mit einem oder mehreren Kontakten
    (Mietern, z.B. bei einer WG) - als eigene n:m-Tabelle, damit derselbe
    Kontakt theoretisch auch in mehreren Mietverhaeltnissen auftauchen kann
    (z.B. jemand mietet nach Auszug spaeter eine andere Wohnung im selben
    Portfolio)."""
    __tablename__ = "mietverhaeltnis_kontakt"

    id = Column(Integer, primary_key=True)
    mietverhaeltnis_id = Column(Integer, ForeignKey("mietverhaeltnis.id"), nullable=False)
    kontakt_id = Column(Integer, ForeignKey("kontakt.id"), nullable=False)

    mietverhaeltnis = relationship("Mietverhaeltnis", back_populates="kontakt_links")
    kontakt = relationship("Kontakt", back_populates="mietverhaeltnis_links")

    __table_args__ = (
        UniqueConstraint("mietverhaeltnis_id", "kontakt_id", name="uq_mv_kontakt"),
    )


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
    # Anzahl der Gesamtanteile (Nenner), auf die der Miteigentumsanteil sich
    # bezieht - bei kleineren WEGs meist 1000, bei sehr grossen Anlagen aber
    # z.B. auch 1000000. Bewusst als eigenes Feld statt fest angenommen,
    # da sich der Nenner von Objekt zu Objekt unterscheiden kann.
    gesamtanteile = Column(Float, nullable=True)
    hausgeld_monatlich = Column(Float)
    # Aktenzeichen des Finanzamts fuer den Einheitswert dieser Wohneinheit
    # (alphanumerisch, z.B. "12/345/67890"), sowie der jaehrliche AfA-Betrag
    # (Absetzung fuer Abnutzung, § 7 EStG) in Euro - beides rein informativ
    # fuer die steuerliche Zuordnung, ohne Einfluss auf die
    # Nebenkostenabrechnung selbst.
    ew_aktenzeichen = Column(String, nullable=True)
    afa_jahresbetrag = Column(Float, nullable=True)
    # Eigentuemer: Absender/Bankdaten fuer die PDF-Nebenkostenabrechnung
    # kommen jetzt aus dem verknuepften Kontakt (Adressbuch), statt pro
    # Objekt einzeln eingetippt zu werden - mehrere Objekte desselben
    # Eigentuemers koennen so denselben Kontakt referenzieren.
    eigentuemer_id = Column(Integer, ForeignKey("kontakt.id"), nullable=True)
    verwaltung_id = Column(Integer, ForeignKey("kontakt.id"), nullable=True)
    # Monat (1-12), in dem das Abrechnungsjahr beginnt. 1 = Kalenderjahr
    # (1.1.-31.12.), z.B. 7 = Wirtschaftsjahr 1.7.-30.6. des Folgejahres.
    abrechnung_start_monat = Column(Integer, nullable=False, default=1)
    notizen = Column(Text)

    eigentuemer = relationship(
        "Kontakt", back_populates="objekte_als_eigentuemer", foreign_keys=[eigentuemer_id],
    )
    verwaltung = relationship(
        "Kontakt", back_populates="objekte_als_verwaltung", foreign_keys=[verwaltung_id],
    )
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

    @property
    def aktuelles_mietverhaeltnis(self):
        """Das gerade laufende Mietverhaeltnis (auszug ist leer), oder None
        bei Leerstand/wenn keins erfasst ist. Bei (regulaer nicht
        vorkommenden) mehreren gleichzeitig laufenden Mietverhaeltnissen
        wird das mit dem juengsten Einzug zurueckgegeben."""
        laufende = [mv for mv in self.mietverhaeltnisse if mv.auszug is None]
        if not laufende:
            return None
        return sorted(laufende, key=lambda mv: mv.einzug)[-1]


class Mietverhaeltnis(Base):
    __tablename__ = "mietverhaeltnis"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    einzug = Column(Date, nullable=False)
    auszug = Column(Date, nullable=True)  # NULL = aktuell noch Mieter
    nk_abschlag_monatlich = Column(Float, nullable=True)  # zuletzt bezahlter monatl. NK-Abschlag
    # Bei gewerblicher Vermietung mit Umsatzsteueroption (par. 9 UStG) wird auf
    # die Nebenkosten Mehrwertsteuer erhoben (die Vorauszahlung wird dann
    # ebenfalls brutto/inkl. MwSt. geleistet) - bei privater Wohnraummiete
    # i.d.R. nicht der Fall (Standardwert False).
    umsatzsteuerpflichtig = Column(Boolean, default=False, nullable=False)
    mwst_satz = Column(Float, nullable=True, default=19.0)  # Prozent
    # Gewerbliche Vermietung an eine Firma statt an Privatpersonen - betrifft
    # Anschrift/Anrede im PDF-Anschreiben (Firmenname statt Personenname,
    # ggf. "z. Hd." mit Ansprechpartner aus den Personen).
    ist_firma = Column(Boolean, default=False, nullable=False)
    firma_name = Column(String, nullable=True)
    # Optionaler, von den Mieternamen unabhaengiger Anzeigename fuer das
    # Mietverhaeltnis (z.B. "Schuster/Wilhelm" bei einer WG mit anderem
    # Wunschnamen, oder ein Kuerzel). Ist er gesetzt, hat er in anzeige_name
    # Vorrang vor den automatisch aus den verknuepften Personen
    # zusammengesetzten Namen (bzw. dem Firmennamen).
    bezeichnung = Column(String, nullable=True)
    # Abweichende Rechnungsadresse fuer die Nebenkostenabrechnung, z.B. wenn
    # sie an eine Verwaltung/Buchhaltung statt an den Mieter selbst gehen
    # soll - hat im PDF-Anschreiben Vorrang vor der sonst automatisch
    # ermittelten Anschrift (Objektadresse bzw. Nachsendeadresse nach Auszug).
    nk_rechnungsadresse_abweichend = Column(Boolean, default=False, nullable=False)
    nk_rechnungsadresse_name = Column(String, nullable=True)
    nk_rechnungsadresse_strasse = Column(String, nullable=True)
    nk_rechnungsadresse_plz = Column(String, nullable=True)
    nk_rechnungsadresse_ort = Column(String, nullable=True)
    notizen = Column(Text)

    objekt = relationship("Objekt", back_populates="mietverhaeltnisse")
    kontakt_links = relationship(
        "MietverhaeltnisKontakt", back_populates="mietverhaeltnis", cascade="all, delete-orphan",
        order_by="MietverhaeltnisKontakt.id",
    )
    personenzahlen = relationship(
        "Personenzahl", back_populates="mietverhaeltnis", cascade="all, delete-orphan",
        order_by="Personenzahl.ab_datum",
    )
    # Vorauszahlungen sind tatsaechlich erhaltene Zahlungen - die duerfen
    # NIEMALS stillschweigend mitgeloescht werden (siehe Loeschen-Route in
    # main.py: dort wird VOR jedem Loeschversuch explizit geprueft, ob
    # bereits Vorauszahlungen erfasst sind, und in dem Fall abgebrochen).
    # Bewusst OHNE cascade="delete-orphan" - bleibt trotzdem eine Relation
    # ohne Cascade (nicht "cascade='all'"), damit ein direktes db.delete(mv)
    # ohne vorherige Pruefung an der Fremdschluessel-Pruefung scheitert
    # (PRAGMA foreign_keys=ON, siehe app/database.py) statt Zahlungsdaten
    # zu verlieren - ein bewusstes zweites Sicherheitsnetz.
    vorauszahlungen = relationship("Vorauszahlung", back_populates="mietverhaeltnis")
    # Mieterabrechnungen sind reine Berechnungsergebnisse (jederzeit per
    # "Jahresabrechnung neu berechnen" reproduzierbar), daher unbedenklich
    # mit cascade mitzuloeschen.
    mieterabrechnungen = relationship(
        "Mieterabrechnung", back_populates="mietverhaeltnis", cascade="all, delete-orphan",
    )

    @property
    def aktuelle_personenzahl(self):
        """Zuletzt erfasste Anzahl Personen (neuester Eintrag), oder None
        wenn noch keine Eintraege vorhanden sind."""
        if not self.personenzahlen:
            return None
        return sorted(self.personenzahlen, key=lambda p: p.ab_datum)[-1].anzahl_personen

    @property
    def personen(self) -> list:
        """Die Mieter-Kontakte dieses Mietverhaeltnisses (z.B. bei einer WG
        mehrere), in der Reihenfolge, in der sie verknuepft wurden. Liefert
        Kontakt-Objekte (nicht mehr ein eigenes Person-Modell) - die Felder
        anrede/vorname/nachname/email/telefon sind dieselben, daher
        funktioniert bestehender Code, der z.B. "for p in mv.personen"
        macht, unveraendert weiter."""
        return [link.kontakt for link in self.kontakt_links]

    @property
    def anzeige_name(self) -> str:
        """Anzeigename des Mietverhaeltnisses. Prioritaet: 1. die frei
        vergebene "bezeichnung" (z.B. "Schuster/Wilhelm", falls von den
        eigentlichen Mieternamen abweichend gewuenscht), 2. bei
        gewerblicher Vermietung der Firmenname, 3. sonst der automatisch
        aus den verknuepften Personen zusammengesetzte Name, z.B. bei einer
        WG: 'Max Mustermann / Erika Musterfrau'."""
        if self.bezeichnung:
            return self.bezeichnung
        if self.ist_firma and self.firma_name:
            return self.firma_name
        namen = [f"{p.vorname or ''} {p.nachname or ''}".strip() for p in self.personen]
        return " / ".join(namen) if namen else "(keine Person erfasst)"


class Personenzahl(Base):
    """Anzahl Personen im Haushalt eines Mietverhaeltnisses, mit Datum ab dem
    sie gilt - ein Verlauf statt eines einzelnen Werts, da sich die Anzahl
    waehrend eines Mietverhaeltnisses aendern kann (z.B. Kind oder
    Mitbewohner kommt dazu). Wird fuer Kostenarten mit Verteilmethode
    "personentage" (Anzahl Personen * Tage) benoetigt."""
    __tablename__ = "personenzahl"

    id = Column(Integer, primary_key=True)
    mietverhaeltnis_id = Column(Integer, ForeignKey("mietverhaeltnis.id"), nullable=False)
    ab_datum = Column(Date, nullable=False)
    anzahl_personen = Column(Integer, nullable=False)

    mietverhaeltnis = relationship("Mietverhaeltnis", back_populates="personenzahlen")


class Kostenart(Base):
    __tablename__ = "kostenart"

    id = Column(Integer, primary_key=True)
    bezeichnung = Column(String, nullable=False, unique=True)
    umlagefaehig = Column(Boolean, default=True, nullable=False)
    # zeitanteilig | gradtagszahl | zwischenablesung_wasser | manuell
    verteilmethode = Column(String, default="zeitanteilig", nullable=False)
    # Standardwert: wird diese Kostenart ueblicherweise ueber die
    # Hausverwalter-Abrechnung abgerechnet? Manche Kosten (z.B. Grundsteuer)
    # laufen ausserhalb der HV-Abrechnung und duerfen daher nicht in die
    # Pruefsummen gegen den HV-Gesamtbetrag einfliessen. Pro Abrechnungsposition
    # individuell ueberschreibbar.
    hv_abgerechnet = Column(Boolean, default=True, nullable=False)
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
    # Schutz gegen unabsichtliche Änderungen: wenn gesetzt, blockieren alle
    # aendernden Routen (Positionen, Vorauszahlungen, Berechnung, Löschen) und
    # verlangen zuerst ein explizites Entsperren.
    gesperrt = Column(Boolean, default=False, nullable=False)

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
    # NULL = Standardwert der Kostenart (Kostenart.hv_abgerechnet) uebernehmen,
    # True/False = fuer diese Position/Periode individuell ueberschrieben.
    hv_abgerechnet = Column(Boolean, nullable=True)
    bemerkung = Column(String)

    jahresabrechnung = relationship("Jahresabrechnung", back_populates="positionen")
    kostenart = relationship("Kostenart")

    @property
    def ist_hv_abgerechnet(self) -> bool:
        if self.hv_abgerechnet is not None:
            return self.hv_abgerechnet
        return self.kostenart.hv_abgerechnet


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
    mietverhaeltnis = relationship("Mietverhaeltnis", back_populates="vorauszahlungen")

    @property
    def summe(self) -> float:
        return self.anzahl_monate * self.betrag_pro_monat


class Zaehler(Base):
    __tablename__ = "zaehler"

    id = Column(Integer, primary_key=True)
    objekt_id = Column(Integer, ForeignKey("objekt.id"), nullable=False)
    typ = Column(String, nullable=False)  # Kaltwasser | Warmwasser | Waerme | Strom
    # Zapfstelle/Standort, z.B. "Bad", "Küche", "Waschmaschine" - unterscheidet
    # mehrere Zaehler desselben Typs an einem Objekt voneinander.
    bezeichnung = Column(String)
    zaehlernummer = Column(String)
    einbaudatum = Column(Date)
    ausbaudatum = Column(Date, nullable=True)  # NULL = aktuell verbaut

    objekt = relationship("Objekt", back_populates="zaehler")
    ablesungen = relationship(
        "Zaehlerstand", back_populates="zaehler", cascade="all, delete-orphan",
        order_by="Zaehlerstand.datum",
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
    mietverhaeltnis = relationship("Mietverhaeltnis", back_populates="mieterabrechnungen")
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
    def mwst_betrag(self) -> float:
        """MwSt.-Betrag auf die Nebenkosten, falls das Mietverhaeltnis
        umsatzsteuerpflichtig ist (par. 9 UStG bei gewerblicher Vermietung),
        sonst 0. Einzige Quelle fuer diese Berechnung - sowohl die
        App-Ergebnisanzeige als auch das PDF-Anschreiben nutzen diese
        Property, damit beide immer denselben Betrag zeigen."""
        if self.ist_leerstand or not self.mietverhaeltnis.umsatzsteuerpflichtig:
            return 0.0
        satz = self.mietverhaeltnis.mwst_satz if self.mietverhaeltnis.mwst_satz is not None else 19.0
        return round(self.summe_umlagefaehig * satz / 100, 2)

    @property
    def gesamtbetrag(self) -> float:
        """Nebenkosten inkl. MwSt. (falls umsatzsteuerpflichtig), sonst
        identisch zu summe_umlagefaehig."""
        return round(self.summe_umlagefaehig + self.mwst_betrag, 2)

    @property
    def differenz(self) -> float:
        """Positiv = Nachzahlung des Mieters, negativ = Guthaben des Mieters.
        Beruecksichtigt MwSt. falls umsatzsteuerpflichtig - die Vorauszahlung
        gilt in dem Fall als bereits brutto (inkl. MwSt.) geleistet. Bei
        Leerstand nicht relevant (keine Vorauszahlung vorhanden)."""
        return round(self.gesamtbetrag - self.summe_vorauszahlung, 2)

    @property
    def laeuft_ueber_jahresende_hinaus(self) -> bool:
        """True, wenn dieses Mietverhaeltnis am Ende des Abrechnungszeitraums
        noch nicht beendet war (Auszug leer oder nach zeitraum_bis). Relevant
        fuer die Frage, ob ein neuer monatlicher NK-Abschlag festgelegt
        werden sollte."""
        if self.ist_leerstand:
            return False
        auszug = self.mietverhaeltnis.auszug
        bis = self.jahresabrechnung.zeitraum_bis
        return auszug is None or (bis is not None and auszug > bis)


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
