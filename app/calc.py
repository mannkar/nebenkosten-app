"""
Verteilungslogik fuer die Nebenkostenabrechnung.

Umgesetzt:
- zeitanteilige Verteilung nach Kalendertagen (Standardfall, Kostenarten mit
  Verteilmethode "zeitanteilig").
- Gradtagszahl-Verteilung nach der Tabelle aus par. 9b HeizkV fuer
  Kostenarten mit Verteilmethode "gradtagszahl" (typischerweise Heizung).
- Leerstandszeitraeume innerhalb des Abrechnungszeitraums werden erkannt
  (Luecken zwischen/vor/nach Mietverhaeltnissen) und erhalten ebenfalls eine
  eigene Abrechnung, damit sichtbar ist, welcher Kostenanteil beim
  Eigentuemer verbleibt.

Noch nicht umgesetzt:
- Verteilmethode "zwischenablesung_wasser" (Wasserkosten anhand
  tatsaechlicher Zaehlerstaende bei Mieterwechsel). Faellt aktuell auf
  zeitanteilig zurueck. Die Tabellen Zaehler/Zaehlerstand sind im Schema
  bereits angelegt, damit das ohne Migration ergaenzt werden kann.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from . import models

# Promille-Anteile je Monat nach der Gradtagszahlentabelle (par. 9b HeizkV /
# VDI-Norm). Juni, Juli und August werden als ein zusammenhaengender
# 92-Tage-Block mit insgesamt 40 Promille behandelt (nicht einzeln pro
# Monat), das entspricht der ueblichen Praxis bei Mieterwechsel-Abrechnungen.
GRADTAGSZAHL_MONAT = {
    1: 170, 2: 150, 3: 130, 4: 80, 5: 40, 6: 40, 7: 40, 8: 40,
    9: 30, 10: 80, 11: 120, 12: 160,
}


def _gradtag_pro_tag(tag: date) -> float:
    monat = tag.month
    if monat in (6, 7, 8):
        return 40 / 92
    tage_im_monat = calendar.monthrange(tag.year, monat)[1]
    return GRADTAGSZAHL_MONAT[monat] / tage_im_monat


def _gradtagszahl_summe(von: date, bis: date) -> float:
    """Summe der Promille-Anteile ueber alle Tage von..bis (inklusive)."""
    summe = 0.0
    tag = von
    while tag <= bis:
        summe += _gradtag_pro_tag(tag)
        tag += timedelta(days=1)
    return summe


def _overlap_periode(zeitraum_von: date, zeitraum_bis: date,
                      einzug: date, auszug: date | None):
    """Start-/Enddatum der Ueberlappung zwischen Mietverhaeltnis und
    Abrechnungszeitraum, oder None wenn keine Ueberlappung besteht."""
    start = max(zeitraum_von, einzug)
    ende = min(zeitraum_bis, auszug) if auszug else zeitraum_bis
    if start > ende:
        return None
    return start, ende


def _leerstand_perioden(zeitraum_von: date, zeitraum_bis: date,
                         mv_perioden: list) -> list:
    """Ermittelt die nicht durch ein Mietverhaeltnis abgedeckten Zeitraeume
    (Luecken) innerhalb des Abrechnungszeitraums, chronologisch sortiert.
    Ueberlappende oder direkt aneinandergrenzende Mietverhaeltnis-Perioden
    werden dabei zusammengefuehrt."""
    if not mv_perioden:
        return [(zeitraum_von, zeitraum_bis)]

    merged = []
    for start, ende in sorted(mv_perioden):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], ende))
        else:
            merged.append((start, ende))

    luecken = []
    cursor = zeitraum_von
    for start, ende in merged:
        if start > cursor:
            luecken.append((cursor, start - timedelta(days=1)))
        cursor = max(cursor, ende + timedelta(days=1))
    if cursor <= zeitraum_bis:
        luecken.append((cursor, zeitraum_bis))
    return luecken


def analysiere_belegung(jahresabrechnung: models.Jahresabrechnung) -> dict:
    """Ermittelt fuer den Abrechnungszeitraum, welche Mietverhaeltnisse mit
    wie vielen Tagen ueberlappen und welche Zeitraeume als Leerstand
    (chronologisch) uebrig bleiben - ohne etwas in die DB zu schreiben. Wird
    sowohl fuer die Anzeige (auch vor dem Berechnen) als auch fuer die
    eigentliche Berechnung genutzt."""
    zeitraum_von = jahresabrechnung.zeitraum_von
    zeitraum_bis = jahresabrechnung.zeitraum_bis
    gesamttage = (zeitraum_bis - zeitraum_von).days + 1

    relevante_mv = []
    mv_perioden = []
    belegte_tage = 0
    for mv in jahresabrechnung.objekt.mietverhaeltnisse:
        periode = _overlap_periode(zeitraum_von, zeitraum_bis, mv.einzug, mv.auszug)
        if periode:
            tage = (periode[1] - periode[0]).days + 1
            relevante_mv.append((mv, tage))
            mv_perioden.append(periode)
            belegte_tage += tage

    # Fuer die Ueberlappungswarnung weiterhin die naive Differenz nutzen
    # (kann bei sich ueberschneidenden Mietverhaeltnissen negativ werden).
    leerstandstage_naiv = gesamttage - belegte_tage

    leerstand_perioden = [
        (von, bis, (bis - von).days + 1)
        for von, bis in _leerstand_perioden(zeitraum_von, zeitraum_bis, mv_perioden)
    ]

    return {
        "gesamttage": gesamttage,
        "relevante_mv": relevante_mv,
        "belegte_tage": belegte_tage,
        "leerstandstage": max(leerstandstage_naiv, 0),
        "leerstand_perioden": leerstand_perioden,
        "ueberlappungs_warnung": leerstandstage_naiv < 0,
    }


def _verteile_positionen(db: Session, mieterabrechnung: models.Mieterabrechnung,
                          positionen: list, tage: int, gesamttage: int,
                          gradtagszahl_periode: float, gradtagszahl_gesamt: float) -> float:
    """Verteilt die uebergebenen umlagefaehigen Positionen auf eine
    Mieter- oder Leerstands-Abrechnung und legt die Detailzeilen an. Gibt die
    Gesamtsumme zurueck."""
    summe = 0.0
    for pos in positionen:
        if pos.kostenart.verteilmethode == "gradtagszahl" and gradtagszahl_gesamt > 0:
            anteil_faktor = gradtagszahl_periode / gradtagszahl_gesamt
            methode = "gradtagszahl"
            detail = (
                f"{round(gradtagszahl_periode, 1)}/{round(gradtagszahl_gesamt, 1)} "
                f"Promille (par. 9b HeizkV)"
            )
        else:
            anteil_faktor = tage / gesamttage
            methode = "zeitanteilig"
            detail = f"{tage}/{gesamttage} Tage"

        anteil = pos.betrag * anteil_faktor
        summe += anteil
        db.add(models.Mieterabrechnungsposition(
            mieterabrechnung_id=mieterabrechnung.id,
            kostenart_id=pos.kostenart_id,
            anteil_betrag=round(anteil, 2),
            berechnungsmethode=methode,
            berechnungsdetail=detail,
        ))
    return summe


def pruefe_summen(jahresabrechnung: models.Jahresabrechnung):
    """Vergleicht den vom Hausverwalter abgerechneten Gesamtbetrag
    (hv_gesamtbetrag) mit (1) der Summe aller erfassten Abrechnungspositionen
    und (2) der Summe der verteilten Betraege (Mieter + Leerstand) zzgl. der
    nicht umlagefaehigen Positionen. Gibt None zurueck, wenn kein
    HV-Gesamtbetrag hinterlegt ist."""
    if jahresabrechnung.hv_gesamtbetrag is None:
        return None

    hv_betrag = jahresabrechnung.hv_gesamtbetrag

    positionen_summe = sum(p.betrag for p in jahresabrechnung.positionen)
    nicht_umlagefaehig_summe = sum(
        p.betrag for p in jahresabrechnung.positionen if not p.umlagefaehig
    )
    verteilt_summe = sum(ma.summe_umlagefaehig for ma in jahresabrechnung.mieterabrechnungen)

    def _check(ist: float) -> dict:
        differenz = round(ist - hv_betrag, 2)
        return {
            "ist": round(ist, 2),
            "soll": round(hv_betrag, 2),
            "differenz": differenz,
            "stimmt": abs(differenz) < 0.01,
        }

    return {
        "positionen": _check(positionen_summe),
        "verteilung": _check(verteilt_summe + nicht_umlagefaehig_summe),
    }


def berechne_jahresabrechnung(db: Session, jahresabrechnung: models.Jahresabrechnung) -> dict:
    """Berechnet die Verteilung aller umlagefaehigen Positionen auf die im
    Zeitraum ueberlappenden Mietverhaeltnisse sowie - chronologisch mit
    einsortiert - auf etwaige Leerstandszeitraeume. Positionen mit
    Verteilmethode "gradtagszahl" werden nach der Gradtagszahlentabelle
    verteilt, alle anderen zeitanteilig nach Tagen. Vorhandene Ergebnisse
    werden geloescht und neu berechnet (idempotent)."""

    beleg = analysiere_belegung(jahresabrechnung)
    zeitraum_von = jahresabrechnung.zeitraum_von
    zeitraum_bis = jahresabrechnung.zeitraum_bis
    gesamttage = beleg["gesamttage"]
    relevante_mv = beleg["relevante_mv"]
    leerstand_perioden = beleg["leerstand_perioden"]

    gradtagszahl_gesamt = _gradtagszahl_summe(zeitraum_von, zeitraum_bis)

    # Alte Ergebnisse entfernen (Neuberechnung)
    for alt in list(jahresabrechnung.mieterabrechnungen):
        db.delete(alt)
    db.flush()

    umlagefaehige_positionen = [p for p in jahresabrechnung.positionen if p.umlagefaehig]

    # Vorauszahlungen je Mietverhaeltnis vorab summieren (werden nicht
    # geloescht, das sind vom Nutzer erfasste Eingabedaten wie die
    # Abrechnungspositionen, keine Berechnungsergebnisse).
    vorauszahlung_je_mv: dict[int, float] = {}
    for vz in jahresabrechnung.vorauszahlungen:
        vorauszahlung_je_mv[vz.mietverhaeltnis_id] = (
            vorauszahlung_je_mv.get(vz.mietverhaeltnis_id, 0.0) + vz.summe
        )

    ergebnisse = []

    for mv, tage in relevante_mv:
        periode = _overlap_periode(zeitraum_von, zeitraum_bis, mv.einzug, mv.auszug)
        gradtagszahl_mv = _gradtagszahl_summe(*periode) if periode else 0.0

        mieterabrechnung = models.Mieterabrechnung(
            jahresabrechnung_id=jahresabrechnung.id,
            mietverhaeltnis_id=mv.id,
            tage_im_zeitraum=tage,
            summe_umlagefaehig=0.0,
            summe_vorauszahlung=round(vorauszahlung_je_mv.get(mv.id, 0.0), 2),
        )
        db.add(mieterabrechnung)
        db.flush()

        summe = _verteile_positionen(
            db, mieterabrechnung, umlagefaehige_positionen, tage, gesamttage,
            gradtagszahl_mv, gradtagszahl_gesamt,
        )
        mieterabrechnung.summe_umlagefaehig = round(summe, 2)
        ergebnisse.append(mieterabrechnung)

    # Leerstandszeitraeume erhalten ebenfalls eine eigene Abrechnung (ohne
    # Mietverhaeltnis, ohne Vorauszahlung), damit sichtbar ist, welcher
    # Kostenanteil in diesen Zeiten beim Eigentuemer verbleibt.
    for von, bis, tage in leerstand_perioden:
        gradtagszahl_leerstand = _gradtagszahl_summe(von, bis)

        leerstandsabrechnung = models.Mieterabrechnung(
            jahresabrechnung_id=jahresabrechnung.id,
            mietverhaeltnis_id=None,
            von=von,
            bis=bis,
            tage_im_zeitraum=tage,
            summe_umlagefaehig=0.0,
            summe_vorauszahlung=0.0,
        )
        db.add(leerstandsabrechnung)
        db.flush()

        summe = _verteile_positionen(
            db, leerstandsabrechnung, umlagefaehige_positionen, tage, gesamttage,
            gradtagszahl_leerstand, gradtagszahl_gesamt,
        )
        leerstandsabrechnung.summe_umlagefaehig = round(summe, 2)
        ergebnisse.append(leerstandsabrechnung)

    jahresabrechnung.status = "berechnet"
    jahresabrechnung.berechnet_am = datetime.now()
    db.commit()

    return {**beleg, "anzahl_mieterabrechnungen": len(ergebnisse)}
