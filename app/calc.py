"""
Verteilungslogik fuer die Nebenkostenabrechnung.

Phase 1 (aktuell umgesetzt): zeitanteilige Verteilung nach Kalendertagen.
Das deckt alle Kostenarten ausser Heizung/Wasser korrekt ab.

Phase 2 (noch nicht umgesetzt): Sonderlogik fuer Heizkosten
(Gradtagszahlentabelle nach par. 9b HeizkV fuer den verbrauchsunabhaengigen
Anteil, wenn keine Zwischenablesung moeglich ist) und fuer Wasser
(Zwischenablesung der Zaehlerstaende bei Mieterwechsel, Zaehlertausch
beachten). Die Tabellen Zaehler/Zaehlerstand sind im Schema bereits
angelegt, damit Phase 2 ohne Migration ergaenzt werden kann.
"""

from __future__ import annotations

from datetime import date
from sqlalchemy.orm import Session

from . import models


def _overlap_days(zeitraum_von: date, zeitraum_bis: date,
                   einzug: date, auszug: date | None) -> int:
    """Anzahl der Tage, die ein Mietverhaeltnis innerhalb des
    Abrechnungszeitraums liegt (inklusive Grenzen)."""
    start = max(zeitraum_von, einzug)
    ende = min(zeitraum_bis, auszug) if auszug else zeitraum_bis
    if start > ende:
        return 0
    return (ende - start).days + 1


def analysiere_belegung(jahresabrechnung: models.Jahresabrechnung) -> dict:
    """Ermittelt fuer den Abrechnungszeitraum, welche Mietverhaeltnisse mit
    wie vielen Tagen ueberlappen - ohne etwas in die DB zu schreiben. Wird
    sowohl fuer die Anzeige (auch vor dem Berechnen) als auch fuer die
    eigentliche Berechnung genutzt."""
    zeitraum_von = jahresabrechnung.zeitraum_von
    zeitraum_bis = jahresabrechnung.zeitraum_bis
    gesamttage = (zeitraum_bis - zeitraum_von).days + 1

    relevante_mv = []
    belegte_tage = 0
    for mv in jahresabrechnung.objekt.mietverhaeltnisse:
        tage = _overlap_days(zeitraum_von, zeitraum_bis, mv.einzug, mv.auszug)
        if tage > 0:
            relevante_mv.append((mv, tage))
            belegte_tage += tage

    leerstandstage = gesamttage - belegte_tage  # kann negativ sein bei Ueberlappung

    return {
        "gesamttage": gesamttage,
        "relevante_mv": relevante_mv,
        "belegte_tage": belegte_tage,
        "leerstandstage": max(leerstandstage, 0),
        "ueberlappungs_warnung": leerstandstage < 0,
    }


def berechne_jahresabrechnung(db: Session, jahresabrechnung: models.Jahresabrechnung) -> dict:
    """Berechnet die zeitanteilige Verteilung aller umlagefaehigen Positionen
    auf die im Zeitraum ueberlappenden Mietverhaeltnisse. Vorhandene
    Ergebnisse werden geloescht und neu berechnet (idempotent)."""

    beleg = analysiere_belegung(jahresabrechnung)
    gesamttage = beleg["gesamttage"]
    relevante_mv = beleg["relevante_mv"]

    # Alte Ergebnisse entfernen (Neuberechnung)
    for alt in list(jahresabrechnung.mieterabrechnungen):
        db.delete(alt)
    db.flush()

    umlagefaehige_positionen = [p for p in jahresabrechnung.positionen if p.umlagefaehig]

    ergebnisse = []
    for mv, tage in relevante_mv:
        mieterabrechnung = models.Mieterabrechnung(
            jahresabrechnung_id=jahresabrechnung.id,
            mietverhaeltnis_id=mv.id,
            tage_im_zeitraum=tage,
            summe_umlagefaehig=0.0,
        )
        db.add(mieterabrechnung)
        db.flush()

        summe = 0.0
        for pos in umlagefaehige_positionen:
            anteil = pos.betrag * tage / gesamttage
            summe += anteil
            db.add(models.Mieterabrechnungsposition(
                mieterabrechnung_id=mieterabrechnung.id,
                kostenart_id=pos.kostenart_id,
                anteil_betrag=round(anteil, 2),
                berechnungsmethode="zeitanteilig",
                berechnungsdetail=f"{tage}/{gesamttage} Tage",
            ))
        mieterabrechnung.summe_umlagefaehig = round(summe, 2)
        ergebnisse.append(mieterabrechnung)

    jahresabrechnung.status = "berechnet"
    db.commit()

    return {**beleg, "anzahl_mieterabrechnungen": len(ergebnisse)}
