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
