"""
Verteilungslogik fuer die Nebenkostenabrechnung.

Umgesetzt:
- zeitanteilige Verteilung nach Kalendertagen (Standardfall, Kostenarten mit
  Verteilmethode "zeitanteilig").
- Gradtagszahl-Verteilung nach der Tabelle aus par. 9b HeizkV fuer
  Kostenarten mit Verteilmethode "gradtagszahl" (typischerweise Heizung).
- Verbrauchsbasierte Verteilung anhand echter Zaehlerstaende fuer Kostenarten
  mit Verteilmethode "zwischenablesung_wasser" (Kalt-/Warmwasser, Abwasser).
  Mehrere Zapfstellen (z.B. Bad, Kueche, Waschmaschine) werden je Typ
  summiert; Zaehlertausch wird ueber Ein-/Ausbaudatum automatisch verkettet.
  Abwasser hat i.d.R. keinen eigenen Zaehler und wird ueblicherweise nach dem
  gesamten Frischwasserverbrauch (Kalt- + Warmwasser) umgelegt. Fehlen fuer
  einen Zeitraum verlaessliche Ablesungen, wird fuer genau diesen Zeitraum
  zeitanteilig genaehert - deutlich sichtbar gekennzeichnet, statt es wie
  eine echte zeitanteilige Kostenart aussehen zu lassen.
- Leerstandszeitraeume innerhalb des Abrechnungszeitraums werden erkannt
  (Luecken zwischen/vor/nach Mietverhaeltnissen) und erhalten ebenfalls eine
  eigene Abrechnung, damit sichtbar ist, welcher Kostenanteil beim
  Eigentuemer verbleibt.
- Personenzahl-basierte Verteilung fuer Kostenarten mit Verteilmethode
  "personentage" (z.B. Muellabfuhr): jedes Mietverhaeltnis fuehrt einen
  Verlauf "ab Datum X gelten Y Personen", da sich die Anzahl waehrend eines
  Mietverhaeltnisses aendern kann. Verteilt wird nach Personentage (Anzahl
  Personen * Tage) statt reiner Tage; Leerstand traegt 0 Personentage bei.
  Fehlen fuer ein relevantes Mietverhaeltnis Personenzahl-Eintraege, wird
  wie bei Wasser zeitanteilig genaehert statt es zu verschleiern.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from . import models


def format_de(zahl: float, nachkommastellen: int = 2) -> str:
    """Formatiert eine Zahl mit Komma als Dezimaltrennzeichen (deutsches
    Format), ohne Tausendertrennzeichen, z.B. 133.63 -> "133,63"."""
    return f"{zahl:.{nachkommastellen}f}".replace(".", ",")


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


# ------------------------------------------------------- Wasser/Zaehlerstaende

def _wasser_typen(kostenart_bezeichnung: str) -> list:
    """Ordnet einer Kostenart-Bezeichnung die relevanten Zaehler-Typen zu.
    Abwasser hat i.d.R. keinen eigenen Zaehler und wird ueblicherweise nach
    dem gesamten Frischwasserverbrauch (Kalt- + Warmwasser) umgelegt."""
    name = kostenart_bezeichnung.lower()
    if "abwasser" in name:
        return ["Kaltwasser", "Warmwasser"]
    if "warmwasser" in name:
        return ["Warmwasser"]
    if "kaltwasser" in name:
        return ["Kaltwasser"]
    return []


def _stand_am(ablesungen: list, datum: date):
    """Zaehlerstand an einem bestimmten Datum: exakter Treffer wenn
    vorhanden, sonst lineare Interpolation zwischen den beiden umgebenden
    Ablesungen. Liegt das Datum ausserhalb des abgedeckten Zeitraums (keine
    Ablesung davor oder danach), wird None zurueckgegeben (keine
    Extrapolation - lieber transparent als Naeherung kennzeichnen)."""
    davor = None
    danach = None
    for d, stand in ablesungen:
        if d == datum:
            return stand
        if d < datum and (davor is None or d > davor[0]):
            davor = (d, stand)
        if d > datum and (danach is None or d < danach[0]):
            danach = (d, stand)
    if davor is None or danach is None:
        return None
    anteil = (datum - davor[0]).days / (danach[0] - davor[0]).days
    return davor[1] + (danach[1] - davor[1]) * anteil


def _zaehler_konsum(zaehler: models.Zaehler, von: date, bis: date):
    """Verbrauch eines einzelnen Zaehlers im Zeitraum [von, bis], begrenzt
    auf seine eigene Einbau-/Ausbauzeit (fuer Zaehlertausch: der alte und der
    neue Zaehler tragen je ihren eigenen Abschnitt bei, die Summe ueber beide
    ergibt automatisch den durchgehenden Verbrauch). Gibt (verbrauch,
    vollstaendig) zurueck - vollstaendig=False, wenn fuer einen Teil des
    angefragten, aktiven Zeitraums keine verlaesslichen Ablesungen
    vorliegen."""
    aktiv_von = max(von, zaehler.einbaudatum) if zaehler.einbaudatum else von
    aktiv_bis = min(bis, zaehler.ausbaudatum) if zaehler.ausbaudatum else bis
    if aktiv_von > aktiv_bis:
        return 0.0, True  # Zaehler war in diesem Zeitraum nicht aktiv

    ablesungen = [(a.datum, a.stand) for a in zaehler.ablesungen]
    stand_start = _stand_am(ablesungen, aktiv_von)
    stand_ende = _stand_am(ablesungen, aktiv_bis)
    if stand_start is None or stand_ende is None:
        return 0.0, False
    diff = stand_ende - stand_start
    if diff < 0:
        return 0.0, False  # unplausibel (z.B. Erfassungsfehler) - nicht verwenden
    return diff, True


def _wasser_konsum_gesamt(objekt: models.Objekt, typen: list, von: date, bis: date):
    """Summiert den Verbrauch aller Zaehler eines Objekts mit passendem Typ
    (mehrere Zapfstellen desselben Typs werden addiert) im Zeitraum
    [von, bis]. Gibt (summe, vollstaendig) zurueck."""
    summe = 0.0
    vollstaendig = True
    gefunden = False
    for z in objekt.zaehler:
        if z.typ not in typen:
            continue
        gefunden = True
        konsum, ok = _zaehler_konsum(z, von, bis)
        summe += konsum
        vollstaendig = vollstaendig and ok
    if not gefunden:
        return 0.0, False
    return summe, vollstaendig


def _personentage_periode(mv: models.Mietverhaeltnis, von: date, bis: date):
    """Personentage (Anzahl Personen * Tage) eines Mietverhaeltnisses im
    Zeitraum [von, bis]. Zwischen zwei erfassten Eintraegen gilt die zuletzt
    gesetzte Anzahl bis zum naechsten Eintrag. Gibt (personentage,
    vollstaendig) zurueck - vollstaendig=False, wenn vor dem angefragten
    Zeitraum kein Eintrag vorliegt (keine Annahme/Extrapolation, lieber
    transparent als Naeherung kennzeichnen)."""
    eintraege = sorted(mv.personenzahlen, key=lambda p: p.ab_datum)
    if not eintraege or eintraege[0].ab_datum > von:
        return 0.0, False

    summe = 0.0
    for i, eintrag in enumerate(eintraege):
        start = max(eintrag.ab_datum, von)
        ende_naechster = (
            eintraege[i + 1].ab_datum - timedelta(days=1)
            if i + 1 < len(eintraege) else bis
        )
        ende = min(ende_naechster, bis)
        if start <= ende:
            tage = (ende - start).days + 1
            summe += eintrag.anzahl_personen * tage
    return summe, True


def _personentage_gesamt(relevante_mv_perioden: list):
    """Summiert die Personentage aller im Abrechnungszeitraum ueberlappenden
    Mietverhaeltnisse (Leerstand traegt 0 Personentage bei und wird hier
    nicht mit aufgefuehrt). Gibt (summe, vollstaendig) zurueck."""
    summe = 0.0
    vollstaendig = True
    for mv, periode in relevante_mv_perioden:
        pt, ok = _personentage_periode(mv, *periode)
        summe += pt
        vollstaendig = vollstaendig and ok
    return summe, vollstaendig


def _relevante_mv_perioden(jahresabrechnung: models.Jahresabrechnung) -> list:
    """Liste aus (Mietverhaeltnis, Ueberlappungsperiode) fuer alle im
    Abrechnungszeitraum ueberlappenden Mietverhaeltnisse - Hilfsfunktion fuer
    die Personentage-Gesamtsumme."""
    zeitraum_von = jahresabrechnung.zeitraum_von
    zeitraum_bis = jahresabrechnung.zeitraum_bis
    ergebnis = []
    for mv in jahresabrechnung.objekt.mietverhaeltnisse:
        periode = _overlap_periode(zeitraum_von, zeitraum_bis, mv.einzug, mv.auszug)
        if periode:
            ergebnis.append((mv, periode))
    return ergebnis


def _verteile_positionen(db: Session, mieterabrechnung: models.Mieterabrechnung,
                          positionen: list, tage: int, gesamttage: int,
                          periode, gradtagszahl_periode: float, gradtagszahl_gesamt: float,
                          objekt: models.Objekt, wasser_gesamt_cache: dict,
                          mv: models.Mietverhaeltnis | None,
                          personentage_gesamt: float, personentage_gesamt_ok: bool) -> float:
    """Verteilt die uebergebenen umlagefaehigen Positionen auf eine
    Mieter- oder Leerstands-Abrechnung und legt die Detailzeilen an. Gibt die
    Gesamtsumme zurueck."""
    summe = 0.0
    for pos in positionen:
        methode_kostenart = pos.kostenart.verteilmethode

        if methode_kostenart == "gradtagszahl" and gradtagszahl_gesamt > 0:
            anteil_faktor = gradtagszahl_periode / gradtagszahl_gesamt
            methode = "gradtagszahl"
            detail = (
                f"{format_de(gradtagszahl_periode, 1)}/{format_de(gradtagszahl_gesamt, 1)} "
                f"Promille (par. 9b HeizkV)"
            )
        elif methode_kostenart == "zwischenablesung_wasser":
            anteil_faktor = None
            typen = _wasser_typen(pos.kostenart.bezeichnung)
            if typen and periode:
                cache_key = tuple(sorted(typen))
                gesamt_konsum, gesamt_ok = wasser_gesamt_cache.get(cache_key, (0.0, False))
                if gesamt_ok and gesamt_konsum > 0:
                    periode_konsum, periode_ok = _wasser_konsum_gesamt(objekt, typen, *periode)
                    if periode_ok:
                        anteil_faktor = periode_konsum / gesamt_konsum
                        methode = "zwischenablesung"
                        detail = (
                            f"{format_de(periode_konsum, 3)}/{format_de(gesamt_konsum, 3)} "
                            f"Einheiten (Zählerstände)"
                        )
            if anteil_faktor is None:
                anteil_faktor = tage / gesamttage
                if tage == gesamttage:
                    # Diese eine Abrechnung deckt den kompletten Zeitraum
                    # allein ab (kein Mieterwechsel, kein Leerstand) - der
                    # volle Betrag geht ohnehin zu 100% hierher, unabhaengig
                    # von der Verteilmethode. Zaehlerstaende sind in diesem
                    # Fall nicht erforderlich, daher keine Naeherung/Warnung.
                    methode = "zeitanteilig"
                    detail = f"{tage}/{gesamttage} Tage – einziger Zeitraum, keine Aufteilung nötig"
                else:
                    # Keine oder unvollstaendige Zaehlerdaten fuer diesen
                    # Zeitraum - zeitanteilig naehern, aber sichtbar als
                    # Naeherung kennzeichnen statt es wie "zeitanteilig"
                    # aussehen zu lassen.
                    methode = "zeitanteilig (Näherung)"
                    detail = (
                        f"{tage}/{gesamttage} Tage – keine ausreichenden "
                        f"Zählerstände, zeitanteilig genähert statt Zwischenablesung"
                    )
        elif methode_kostenart == "personentage":
            anteil_faktor = None
            if personentage_gesamt_ok and personentage_gesamt > 0:
                if mv is None:
                    periode_pt, periode_ok = 0.0, True  # Leerstand: 0 Personen
                elif periode:
                    periode_pt, periode_ok = _personentage_periode(mv, periode[0], periode[1])
                else:
                    periode_pt, periode_ok = 0.0, False
                if periode_ok:
                    anteil_faktor = periode_pt / personentage_gesamt
                    methode = "personentage"
                    detail = (
                        f"{format_de(periode_pt, 1)}/{format_de(personentage_gesamt, 1)} "
                        f"Personentage"
                    )
            if anteil_faktor is None:
                anteil_faktor = tage / gesamttage
                if tage == gesamttage:
                    # Siehe Kommentar bei "zwischenablesung_wasser" oben:
                    # einziger Zeitraum der Abrechnung -> 100% ohnehin
                    # unabhaengig von der Verteilmethode, keine
                    # Personenzahl-Eintraege noetig.
                    methode = "zeitanteilig"
                    detail = f"{tage}/{gesamttage} Tage – einziger Zeitraum, keine Aufteilung nötig"
                else:
                    # Keine oder unvollstaendige Personenzahl-Eintraege -
                    # zeitanteilig naehern, aber sichtbar als Naeherung
                    # kennzeichnen.
                    methode = "zeitanteilig (Näherung)"
                    detail = (
                        f"{tage}/{gesamttage} Tage – keine ausreichenden "
                        f"Personenzahl-Einträge, zeitanteilig genähert statt nach Personenzahl"
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


def _kein_verteilungsbedarf(jahresabrechnung: models.Jahresabrechnung) -> bool:
    """True, wenn ueber den gesamten Abrechnungszeitraum durchgehend nur ein
    einziges Mietverhaeltnis bestand (kein Mieterwechsel, kein Leerstand) -
    dann geht jede umlagefaehige Position ohnehin zu 100% an dieses eine
    Mietverhaeltnis, unabhaengig von der Verteilmethode. Zaehlerstaende bzw.
    Personenzahl-Eintraege sind in diesem Fall nicht erforderlich."""
    beleg = analysiere_belegung(jahresabrechnung)
    return len(beleg["relevante_mv"]) == 1 and beleg["leerstandstage"] == 0


def wasser_warnungen(jahresabrechnung: models.Jahresabrechnung) -> list:
    """Liefert die Bezeichnungen der umlagefaehigen Kostenarten mit
    Verteilmethode "zwischenablesung_wasser", fuer die ueber den gesamten
    Abrechnungszeitraum keine ausreichenden Zaehlerstaende vorliegen (wuerde
    also zeitanteilig genaehert statt nach echtem Verbrauch verteilt). Dient
    fuer einen proaktiven Hinweis, bevor/unabhaengig davon berechnet wird.
    Kein Hinweis, wenn ohnehin nur ein Mietverhaeltnis den gesamten Zeitraum
    belegt (siehe _kein_verteilungsbedarf)."""
    if _kein_verteilungsbedarf(jahresabrechnung):
        return []

    objekt = jahresabrechnung.objekt
    zeitraum_von = jahresabrechnung.zeitraum_von
    zeitraum_bis = jahresabrechnung.zeitraum_bis

    warnungen = []
    for pos in jahresabrechnung.positionen:
        if not pos.umlagefaehig or pos.kostenart.verteilmethode != "zwischenablesung_wasser":
            continue
        typen = _wasser_typen(pos.kostenart.bezeichnung)
        gesamt_konsum, gesamt_ok = (
            _wasser_konsum_gesamt(objekt, typen, zeitraum_von, zeitraum_bis)
            if typen else (0.0, False)
        )
        if not (gesamt_ok and gesamt_konsum > 0):
            warnungen.append(pos.kostenart.bezeichnung)
    return warnungen


def personentage_warnungen(jahresabrechnung: models.Jahresabrechnung) -> list:
    """Liefert die Bezeichnungen der umlagefaehigen Kostenarten mit
    Verteilmethode "personentage", fuer die nicht fuer alle im Zeitraum
    ueberlappenden Mietverhaeltnisse ausreichende Personenzahl-Eintraege
    vorliegen (wuerde also zeitanteilig genaehert statt nach Personenzahl
    verteilt). Kein Hinweis, wenn ohnehin nur ein Mietverhaeltnis den
    gesamten Zeitraum belegt (siehe _kein_verteilungsbedarf)."""
    hat_personentage_kostenart = any(
        pos.umlagefaehig and pos.kostenart.verteilmethode == "personentage"
        for pos in jahresabrechnung.positionen
    )
    if not hat_personentage_kostenart:
        return []
    if _kein_verteilungsbedarf(jahresabrechnung):
        return []

    _, gesamt_ok = _personentage_gesamt(_relevante_mv_perioden(jahresabrechnung))
    if gesamt_ok:
        return []
    return [
        pos.kostenart.bezeichnung for pos in jahresabrechnung.positionen
        if pos.umlagefaehig and pos.kostenart.verteilmethode == "personentage"
    ]


def pruefe_summen(jahresabrechnung: models.Jahresabrechnung):
    """Vergleicht den vom Hausverwalter abgerechneten Gesamtbetrag
    (hv_gesamtbetrag) mit (1) der Summe aller erfassten Abrechnungspositionen
    und (2) der Summe der verteilten Betraege (Mieter + Leerstand) zzgl. der
    nicht umlagefaehigen Positionen. Positionen, die nicht ueber die
    Hausverwaltung abgerechnet werden (z.B. Grundsteuer), fliessen in keine
    der beiden Pruefungen ein. Gibt None zurueck, wenn kein HV-Gesamtbetrag
    hinterlegt ist."""
    if jahresabrechnung.hv_gesamtbetrag is None:
        return None

    hv_betrag = jahresabrechnung.hv_gesamtbetrag

    hv_positionen = [p for p in jahresabrechnung.positionen if p.ist_hv_abgerechnet]
    hv_kostenart_ids = {p.kostenart_id for p in hv_positionen}

    positionen_summe = sum(p.betrag for p in hv_positionen)
    nicht_umlagefaehig_summe = sum(p.betrag for p in hv_positionen if not p.umlagefaehig)
    verteilt_summe = sum(
        mp.anteil_betrag
        for ma in jahresabrechnung.mieterabrechnungen
        for mp in ma.positionen
        if mp.kostenart_id in hv_kostenart_ids
    )

    def _check(ist: float) -> dict:
        differenz = round(ist - hv_betrag, 2)
        return {
            "ist": round(ist, 2),
            "soll": round(hv_betrag, 2),
            "differenz": differenz,
            "stimmt": abs(differenz) < 0.01,
        }

    ausgeschlossen = [
        p.kostenart.bezeichnung for p in jahresabrechnung.positionen if not p.ist_hv_abgerechnet
    ]

    return {
        "positionen": _check(positionen_summe),
        "verteilung": _check(verteilt_summe + nicht_umlagefaehig_summe),
        "ausgeschlossen": ausgeschlossen,
    }


def berechne_jahresabrechnung(db: Session, jahresabrechnung: models.Jahresabrechnung) -> dict:
    """Berechnet die Verteilung aller umlagefaehigen Positionen auf die im
    Zeitraum ueberlappenden Mietverhaeltnisse sowie - chronologisch mit
    einsortiert - auf etwaige Leerstandszeitraeume. Positionen mit
    Verteilmethode "gradtagszahl" werden nach der Gradtagszahlentabelle,
    "zwischenablesung_wasser" nach echten Zaehlerstaenden (falls vorhanden)
    verteilt, alle anderen zeitanteilig nach Tagen. Vorhandene Ergebnisse
    werden geloescht und neu berechnet (idempotent)."""

    beleg = analysiere_belegung(jahresabrechnung)
    objekt = jahresabrechnung.objekt
    zeitraum_von = jahresabrechnung.zeitraum_von
    zeitraum_bis = jahresabrechnung.zeitraum_bis
    gesamttage = beleg["gesamttage"]
    relevante_mv = beleg["relevante_mv"]
    leerstand_perioden = beleg["leerstand_perioden"]

    gradtagszahl_gesamt = _gradtagszahl_summe(zeitraum_von, zeitraum_bis)

    # Fuer jede in dieser Jahresabrechnung vorkommende Zaehler-Typ-Kombination
    # (z.B. nur Kaltwasser, nur Warmwasser, oder Kalt+Warm fuer Abwasser)
    # einmalig den Gesamtverbrauch ueber den kompletten Abrechnungszeitraum
    # ermitteln. Wird als Nenner fuer die Verhaeltnisrechnung je Periode
    # verwendet (analog zur Gradtagszahl-Gesamtsumme).
    umlagefaehige_positionen = [p for p in jahresabrechnung.positionen if p.umlagefaehig]
    wasser_gesamt_cache: dict = {}
    for pos in umlagefaehige_positionen:
        if pos.kostenart.verteilmethode != "zwischenablesung_wasser":
            continue
        typen = _wasser_typen(pos.kostenart.bezeichnung)
        if not typen:
            continue
        cache_key = tuple(sorted(typen))
        if cache_key not in wasser_gesamt_cache:
            wasser_gesamt_cache[cache_key] = _wasser_konsum_gesamt(
                objekt, typen, zeitraum_von, zeitraum_bis
            )

    # Gesamtsumme der Personentage ueber alle im Zeitraum ueberlappenden
    # Mietverhaeltnisse (Nenner fuer die Verhaeltnisrechnung je Periode,
    # analog zur Gradtagszahl-Gesamtsumme). Leerstand traegt immer 0 bei.
    relevante_mv_perioden = [
        (mv, _overlap_periode(zeitraum_von, zeitraum_bis, mv.einzug, mv.auszug))
        for mv, _ in relevante_mv
    ]
    personentage_gesamt, personentage_gesamt_ok = _personentage_gesamt(relevante_mv_perioden)

    # Alte Ergebnisse entfernen (Neuberechnung)
    for alt in list(jahresabrechnung.mieterabrechnungen):
        db.delete(alt)
    db.flush()

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
            periode, gradtagszahl_mv, gradtagszahl_gesamt, objekt, wasser_gesamt_cache,
            mv, personentage_gesamt, personentage_gesamt_ok,
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

        # Fuer die Zaehlerstand-Interpolation den Zeitraum um je einen Tag
        # nach aussen erweitern: der Leerstand-Zeitraum ist als Tage-Luecke
        # zwischen zwei Mietverhaeltnissen definiert (beginnt einen Tag nach
        # dem vorherigen Auszug, endet einen Tag vor dem naechsten Einzug),
        # waehrend die tatsaechliche Zwischenablesung genau am Auszugs- bzw.
        # Einzugstag protokolliert wird. Ohne diese Erweiterung wuerde ein
        # Bruchteil des Verbrauchs am jeweiligen Uebergangstag weder dem
        # Mieter- noch dem Leerstand-Zeitraum zugerechnet.
        wasser_periode = (von - timedelta(days=1), bis + timedelta(days=1))
        summe = _verteile_positionen(
            db, leerstandsabrechnung, umlagefaehige_positionen, tage, gesamttage,
            wasser_periode, gradtagszahl_leerstand, gradtagszahl_gesamt, objekt, wasser_gesamt_cache,
            None, personentage_gesamt, personentage_gesamt_ok,
        )
        leerstandsabrechnung.summe_umlagefaehig = round(summe, 2)
        ergebnisse.append(leerstandsabrechnung)

    jahresabrechnung.status = "berechnet"
    jahresabrechnung.berechnet_am = datetime.now()
    db.commit()

    return {**beleg, "anzahl_mieterabrechnungen": len(ergebnisse)}
