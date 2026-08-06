from datetime import date

from sqlalchemy.orm import Session
from . import models

# Uebliche Kostenarten einer WEG-Hausgeldabrechnung. verteilmethode ist fuer
# Heizung/Wasser bereits als Marker gesetzt, auch wenn die Sonderberechnung
# erst in Phase 2 umgesetzt wird - in Phase 1 werden alle umlagefaehigen
# Positionen (auch Heizung/Wasser) zeitanteilig verteilt, was fuer laufende
# Mietverhaeltnisse ohne Wechsel bereits korrekt ist.
DEFAULT_KOSTENARTEN = [
    ("Heizung", True, "gradtagszahl"),
    ("Warmwasser", True, "zeitanteilig"),
    ("Kaltwasser", True, "zwischenablesung_wasser"),
    ("Abwasser", True, "zwischenablesung_wasser"),
    ("Hausmeister / Hauswart", True, "zeitanteilig"),
    ("Gartenpflege", True, "zeitanteilig"),
    ("Allgemeinstrom", True, "zeitanteilig"),
    ("Aufzug", True, "zeitanteilig"),
    ("Müllabfuhr", True, "zeitanteilig"),
    ("Schornsteinfeger", True, "zeitanteilig"),
    ("Gebäudeversicherung", True, "zeitanteilig"),
    ("Grundsteuer", True, "zeitanteilig"),
    ("Kabel-/Antennenanschluss", True, "zeitanteilig"),
    ("Verwaltergebühr", False, "manuell"),
    ("Instandhaltungsrücklage", False, "manuell"),
    ("Instandhaltung / Reparaturen", False, "manuell"),
    ("Sonstige Kosten laut Verwalterabrechnung", True, "zeitanteilig"),
]


def seed_kostenarten(db: Session) -> None:
    vorhanden = {k.bezeichnung for k in db.query(models.Kostenart).all()}
    for bezeichnung, umlagefaehig, methode in DEFAULT_KOSTENARTEN:
        if bezeichnung not in vorhanden:
            db.add(models.Kostenart(
                bezeichnung=bezeichnung,
                umlagefaehig=umlagefaehig,
                verteilmethode=methode,
            ))
    db.commit()


def seed_testdaten(db: Session) -> None:
    """Legt bei einer neu angelegten Datenbank einmalig ein Test-Objekt mit
    zwei Mietverhaeltnissen an, um die Anwendung direkt mit Beispieldaten
    ausprobieren zu koennen. Ist bereits ein Objekt "Test" vorhanden, passiert
    nichts (idempotent, kein Duplikat)."""
    objekt = db.query(models.Objekt).filter(models.Objekt.bezeichnung == "Test").first()
    if objekt:
        return

    objekt = models.Objekt(bezeichnung="Test", abrechnung_start_monat=1)
    db.add(objekt)
    db.flush()

    mv_a = models.Mietverhaeltnis(
        objekt_id=objekt.id, einzug=date(2025, 1, 1), auszug=date(2025, 2, 28),
    )
    db.add(mv_a)
    db.flush()
    db.add(models.Person(mietverhaeltnis_id=mv_a.id, vorname="Mieter", nachname="A"))

    mv_b = models.Mietverhaeltnis(
        objekt_id=objekt.id, einzug=date(2025, 4, 1), auszug=None,
    )
    db.add(mv_b)
    db.flush()
    db.add(models.Person(mietverhaeltnis_id=mv_b.id, vorname="Mieter", nachname="B"))

    db.commit()
