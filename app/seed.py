from datetime import date

from sqlalchemy.orm import Session
from . import models, calc

# Uebliche Kostenarten einer WEG-Hausgeldabrechnung. verteilmethode ist fuer
# Heizung/Wasser bereits als Marker gesetzt, auch wenn die Sonderberechnung
# erst in Phase 2 umgesetzt wird - in Phase 1 werden alle umlagefaehigen
# Positionen (auch Heizung/Wasser) zeitanteilig verteilt, was fuer laufende
# Mietverhaeltnisse ohne Wechsel bereits korrekt ist.
# hv_abgerechnet: Standardwert, ob diese Kostenart ueblicherweise Teil der
# Hausverwalter-Abrechnung ist (und damit in die Pruefsummen gegen den
# HV-Gesamtbetrag einfliesst). Grundsteuer laeuft haeufig ausserhalb der
# HV-Abrechnung direkt beim Eigentuemer, daher hier standardmaessig False -
# pro Position individuell umschaltbar.
DEFAULT_KOSTENARTEN = [
    ("Heizung", True, "gradtagszahl", True),
    ("Warmwasser", True, "zeitanteilig", True),
    ("Kaltwasser", True, "zwischenablesung_wasser", True),
    ("Abwasser", True, "zwischenablesung_wasser", True),
    ("Hausmeister / Hauswart", True, "zeitanteilig", True),
    ("Gartenpflege", True, "zeitanteilig", True),
    ("Allgemeinstrom", True, "zeitanteilig", True),
    ("Aufzug", True, "zeitanteilig", True),
    ("Müllabfuhr", True, "zeitanteilig", True),
    ("Schornsteinfeger", True, "zeitanteilig", True),
    ("Gebäudeversicherung", True, "zeitanteilig", True),
    ("Grundsteuer", True, "zeitanteilig", False),
    ("Kabel-/Antennenanschluss", True, "zeitanteilig", True),
    ("Verwaltergebühr", False, "manuell", True),
    ("Instandhaltungsrücklage", False, "manuell", True),
    ("Instandhaltung / Reparaturen", False, "manuell", True),
    ("Sonstige Kosten laut Verwalterabrechnung", True, "zeitanteilig", True),
    ("Sonstige nicht umlagefähige Kosten laut Verwalterabrechnung", False, "manuell", True),
]


def seed_kostenarten(db: Session) -> None:
    vorhanden = {k.bezeichnung for k in db.query(models.Kostenart).all()}
    for bezeichnung, umlagefaehig, methode, hv_abgerechnet in DEFAULT_KOSTENARTEN:
        if bezeichnung not in vorhanden:
            db.add(models.Kostenart(
                bezeichnung=bezeichnung,
                umlagefaehig=umlagefaehig,
                verteilmethode=methode,
                hv_abgerechnet=hv_abgerechnet,
            ))
    db.commit()


def seed_testdaten(db: Session) -> None:
    """Legt bei einer neu angelegten Datenbank einmalig ein Test-Objekt mit
    zwei Mietverhaeltnissen, einer Jahresabrechnung 2025 (inkl.
    Abrechnungspositionen, Vorauszahlungen und HV-Gesamtbetrag) an und
    berechnet die Verteilung - als Abbild des Standes, mit dem in dieser
    Anwendung getestet wurde. Ist bereits ein Objekt "Test" vorhanden,
    passiert nichts (idempotent, kein Duplikat)."""
    objekt = db.query(models.Objekt).filter(models.Objekt.bezeichnung == "Test").first()
    if objekt:
        return

    objekt = models.Objekt(bezeichnung="Test", abrechnung_start_monat=1)
    db.add(objekt)
    db.flush()

    mv1 = models.Mietverhaeltnis(
        objekt_id=objekt.id, einzug=date(2025, 1, 1), auszug=date(2025, 2, 28),
    )
    db.add(mv1)
    db.flush()
    db.add(models.Person(mietverhaeltnis_id=mv1.id, vorname="Max", nachname="Mustermann"))

    mv2 = models.Mietverhaeltnis(
        objekt_id=objekt.id, einzug=date(2025, 4, 1), auszug=None,
    )
    db.add(mv2)
    db.flush()
    db.add(models.Person(
        mietverhaeltnis_id=mv2.id, anrede="Herr", vorname="Peter", nachname="Parker",
    ))

    ja = models.Jahresabrechnung(
        objekt_id=objekt.id, jahr=2025,
        zeitraum_von=date(2025, 1, 1), zeitraum_bis=date(2025, 12, 31),
        hv_gesamtbetrag=3039.66,
    )
    db.add(ja)
    db.flush()

    kostenart_map = {k.bezeichnung: k for k in db.query(models.Kostenart).all()}
    positionen = [
        ("Heizung", 879.52, True),
        ("Sonstige Kosten laut Verwalterabrechnung", 1165.50, True),
        ("Kaltwasser", 133.63, True),
        ("Sonstige nicht umlagefähige Kosten laut Verwalterabrechnung", 861.01, False),
        ("Grundsteuer", 180.00, True),
    ]
    for bezeichnung, betrag, umlagefaehig in positionen:
        kostenart = kostenart_map.get(bezeichnung)
        if kostenart:
            db.add(models.Abrechnungsposition(
                jahresabrechnung_id=ja.id, kostenart_id=kostenart.id,
                betrag=betrag, umlagefaehig=umlagefaehig,
            ))

    db.add(models.Vorauszahlung(
        jahresabrechnung_id=ja.id, mietverhaeltnis_id=mv1.id,
        anzahl_monate=2, betrag_pro_monat=150.0, bemerkung="Jan/Feb",
    ))
    db.add(models.Vorauszahlung(
        jahresabrechnung_id=ja.id, mietverhaeltnis_id=mv2.id,
        anzahl_monate=9, betrag_pro_monat=1700.0, bemerkung="Apr.-Dez.",
    ))
    db.commit()

    # Verteilung berechnen, damit die Jahresabrechnung wie im Original mit
    # Status "berechnet" und vollstaendigen Ergebnissen vorliegt.
    calc.berechne_jahresabrechnung(db, ja)
