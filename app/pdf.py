"""
PDF-Generierung der Nebenkostenabrechnung je Mietverhaeltnis, als klassischer
Geschaeftsbrief (angelehnt an das hochgeladene Muster NBK_2025_Muster.pdf).

Ein Brief wird pro Mieterabrechnung (= ein Mietverhaeltnis) erzeugt - fuer
Leerstandszeitraeume gibt es keinen Empfaenger, die werden hier nicht
beruecksichtigt (Aufrufer filtert das). Fuer eine Jahresabrechnung koennen
mehrere Mieter-Briefe zu einem Sammel-PDF hintereinander (je eigene Seite(n))
zusammengefasst werden.

Vereinfachungen ggue. dem Muster:
- Die "Ihre Anteil"-Spaltenueberschrift zeigt die grob gerundete Anzahl
  Monate (proportional zu den tatsaechlichen Tagen der Mieterabrechnung im
  Zeitraum), keine exakte Monatsrechnung.
- "Nebenkosten" und "Gesamtbetrag" sind identisch (= Summe umlagefaehige
  Kosten des Mieters), AUSSER das Mietverhaeltnis ist als
  umsatzsteuerpflichtig markiert - dann kommt eine zusaetzliche MwSt.-Zeile
  dazwischen und "Gesamtbetrag" ist der Bruttobetrag (Nebenkosten + MwSt.).
  Die Vorauszahlung gilt in diesem Fall als bereits brutto (inkl. MwSt.)
  geleistet.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from functools import partial

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from . import models

# Linksbuendiges Briefformat: alle Textelemente (Absender, Anschrift,
# Betreff, Anrede, Flieasstext, Schlussformel) beginnen an derselben linken
# Kante - einzige Ausnahme ist Ort/Datum zwischen Anschrift und Betreff,
# das klassisch rechtsbuendig steht.
_STYLES = getSampleStyleSheet()
_NORMAL = ParagraphStyle("nk_normal", parent=_STYLES["Normal"], fontSize=10, leading=13)
_NORMAL_RIGHT = ParagraphStyle("nk_normal_right", parent=_NORMAL, alignment=TA_RIGHT)
_SMALL = ParagraphStyle("nk_small", parent=_STYLES["Normal"], fontSize=8, leading=11, textColor=colors.grey)
_SMALL_UNDERLINE = ParagraphStyle("nk_small_ul", parent=_SMALL, fontSize=8, textColor=colors.black)
_TITLE = ParagraphStyle("nk_title", parent=_NORMAL, fontName="Helvetica-Bold", fontSize=11, spaceAfter=6)

# Zellen-Styles fuer die Tabellen (Kostentabelle + Zusammenfassung). Es wird
# durchgaengig mit Paragraph-Flowables statt reinen Strings gearbeitet, damit
# laengere Inhalte (z.B. lange Kostenart-Bezeichnungen oder "zeitanteilig
# (Näherung)") bei schmalen Spalten sauber umbrechen statt optisch
# ueberzulaufen.
_CELL = ParagraphStyle("nk_cell", parent=_NORMAL, fontSize=9, leading=11)
_CELL_RIGHT = ParagraphStyle("nk_cell_right", parent=_CELL, alignment=TA_RIGHT)
_CELL_BOLD = ParagraphStyle("nk_cell_bold", parent=_CELL, fontName="Helvetica-Bold")
_CELL_BOLD_RIGHT = ParagraphStyle("nk_cell_bold_right", parent=_CELL_BOLD, alignment=TA_RIGHT)

# Gemeinsame Spaltenbreiten fuer Kostentabelle und Zusammenfassung, damit
# beide Tabellen exakt dieselbe rechte Spalte (Betraege) verwenden und die
# Zusammenfassung optisch nahtlos unter der Kostentabelle weiterlaeuft.
_SPALTE_KOSTENART = 65 * mm
_SPALTE_ABRECHNUNGSART = 32 * mm
_SPALTE_BETRAG = 24 * mm
_SPALTE_ANTEIL = 30 * mm


def format_de(zahl: float, nachkommastellen: int = 2) -> str:
    return f"{zahl:.{nachkommastellen}f}".replace(".", ",")


def _abrechnungsart_label(berechnungsmethode: str) -> str:
    """Fuer die Tabellenspalte 'Abrechnungsart': die tatsaechlich
    angewendete Berechnungsmethode dieser Position (nicht die nominelle
    Verteilmethode der Kostenart - bei fehlenden Zaehlerstaenden/
    Personenzahl-Eintraegen wird fuer die betroffene Position abweichend
    zeitanteilig genaehert, siehe calc.py), unveraendert wie im Ergebnis der
    Jahresabrechnung selbst angezeigt (z.B. 'gradtagszahl',
    'zwischenablesung', 'personentage', 'zeitanteilig',
    'zeitanteilig (Näherung)')."""
    return berechnungsmethode


def _empfaenger_namen_zeilen(mv: models.Mietverhaeltnis) -> list[str]:
    """Ein oder zwei Zeilen mit dem Namen des Empfaengers. Bei Privatpersonen
    alle Namen des Mietverhaeltnisses (WG etc.) in einer Zeile, z.B. 'Frau A
    und Herr B'. Bei einer Firma steht der Firmenname in Zeile 1, darunter
    als zweite Zeile 'z. Hd. ...' mit der hinterlegten Ansprechperson,
    sofern genau eine Person erfasst ist."""
    if mv.ist_firma:
        zeilen = [mv.firma_name or "Firma"]
        if len(mv.personen) == 1:
            p = mv.personen[0]
            praefix = f"{p.anrede} " if p.anrede else ""
            zeilen.append(f"z. Hd. {praefix}{p.vorname} {p.nachname}".strip())
        return zeilen

    namen = []
    for p in mv.personen:
        praefix = f"{p.anrede} " if p.anrede else ""
        namen.append(f"{praefix}{p.vorname} {p.nachname}".strip())
    if not namen:
        return ["Mieter"]
    if len(namen) == 1:
        return [namen[0]]
    return [", ".join(namen[:-1]) + " und " + namen[-1]]


def _anrede_zeile(mv: models.Mietverhaeltnis) -> str:
    """Persoenliche Briefanrede, z.B. 'Sehr geehrter Herr Mustermann,' bzw.
    fuer mehrere Personen 'Sehr geehrte Frau A und Herr B,'. Ohne erfasste
    Anrede wird eine neutrale Form verwendet. Bei einer Firma nur dann
    persoenlich (an die im Adressfeld als 'z. Hd.' genannte Ansprechperson),
    wenn genau eine Person hinterlegt ist - sonst die uebliche geschaeftliche
    Form."""
    personen = mv.personen
    if not personen or (mv.ist_firma and len(personen) != 1):
        return "Sehr geehrte Damen und Herren,"
    if len(personen) == 1:
        p = personen[0]
        if p.anrede == "Herr":
            return f"Sehr geehrter Herr {p.nachname},"
        if p.anrede == "Frau":
            return f"Sehr geehrte Frau {p.nachname},"
        return f"Guten Tag {p.vorname} {p.nachname},"
    namen = []
    for p in personen:
        if p.anrede == "Herr":
            namen.append(f"Herr {p.nachname}")
        elif p.anrede == "Frau":
            namen.append(f"Frau {p.nachname}")
        else:
            namen.append(f"{p.vorname} {p.nachname}")
    return "Sehr geehrte " + " und ".join(namen) + ","


def _mietverhaeltnis_zeitraum(ja: models.Jahresabrechnung, mv: models.Mietverhaeltnis):
    """Der tatsaechliche Zeitraum des Mietverhaeltnisses innerhalb dieser
    Jahresabrechnung (Ueberlappung von Mietverhaeltnis- und
    Abrechnungszeitraum), z.B. fuer einen Mieter der unterjaehrig ausgezogen
    ist der 1.1. bis zum tatsaechlichen Auszugsdatum statt bis zum
    Jahresende."""
    von = max(ja.zeitraum_von, mv.einzug)
    bis = ja.zeitraum_bis
    if mv.auszug:
        bis = min(bis, mv.auszug)
    return von, bis


def _empfaenger_zeilen(mv: models.Mietverhaeltnis, objekt: models.Objekt, heute: date) -> list[str]:
    """Alle Zeilen des Anschriftenfelds im Brief (Name(n) gefolgt von Straße
    und PLZ/Ort). Prioritaet der Adresse:
    1. Abweichende Rechnungsadresse fuer die Nebenkostenabrechnung, falls am
       Mietverhaeltnis hinterlegt (z.B. Verwaltung/Buchhaltung) - ist dabei
       auch ein eigener Name hinterlegt, ersetzt der die sonstige
       Namenszeile (Firma/Personen) komplett.
    2. Solange das Mietverhaeltnis noch laeuft (kein Auszug oder Auszug in
       der Zukunft): die Adresse der Wohnung selbst (Objekt).
    3. Nach dem Auszug: die hinterlegte Nachsendeadresse der ersten Person
       (mit Fallback auf die Objektadresse, falls keine erfasst ist)."""
    rechnungsadresse_aktiv = mv.nk_rechnungsadresse_abweichend and (
        mv.nk_rechnungsadresse_strasse or mv.nk_rechnungsadresse_plz
        or mv.nk_rechnungsadresse_ort or mv.nk_rechnungsadresse_name
    )

    if rechnungsadresse_aktiv and mv.nk_rechnungsadresse_name:
        namen_zeilen = [mv.nk_rechnungsadresse_name]
    else:
        namen_zeilen = _empfaenger_namen_zeilen(mv)

    if rechnungsadresse_aktiv:
        adresse = {
            "strasse": mv.nk_rechnungsadresse_strasse or "",
            "plz": mv.nk_rechnungsadresse_plz or "",
            "ort": mv.nk_rechnungsadresse_ort or "",
        }
    else:
        adresse = None
        ausgezogen = mv.auszug is not None and mv.auszug <= heute
        if ausgezogen and mv.personen:
            p = mv.personen[0]
            if p.adresse_nach_strasse or p.adresse_nach_plz or p.adresse_nach_ort:
                adresse = {
                    "strasse": p.adresse_nach_strasse or "",
                    "plz": p.adresse_nach_plz or "",
                    "ort": p.adresse_nach_ort or "",
                }
        if adresse is None:
            adresse = {
                "strasse": objekt.strasse or "",
                "plz": objekt.plz or "",
                "ort": objekt.ort or "",
            }

    zeilen = list(namen_zeilen)
    if adresse["strasse"]:
        zeilen.append(adresse["strasse"])
    plz_ort = f"{adresse['plz']} {adresse['ort']}".strip()
    if plz_ort:
        zeilen.append(plz_ort)
    return zeilen


def _footer(canvas, doc, objekt: models.Objekt):
    bankdaten = " - ".join(
        teil for teil in [
            objekt.bank_name,
            f"IBAN: {objekt.bank_iban}" if objekt.bank_iban else None,
            f"BIC: {objekt.bank_bic}" if objekt.bank_bic else None,
        ] if teil
    )
    if not bankdaten:
        return
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawCentredString(A4[0] / 2, 12 * mm, f"Bankverbindung: {bankdaten}")
    canvas.restoreState()


def _brief_story(ja: models.Jahresabrechnung, ma: models.Mieterabrechnung, heute: date) -> list:
    """Baut die Flowables fuer den Brief eines einzelnen Mieters (ohne
    aeusseres Dokument), damit mehrere Briefe zu einem Sammel-PDF
    aneinandergereiht werden koennen."""
    if ma.ist_leerstand:
        raise ValueError("Fuer Leerstand kann kein Anschreiben erzeugt werden.")

    mv = ma.mietverhaeltnis
    objekt = ja.objekt
    story = []

    # kleiner Briefkopf oben (fuer Blankopapier-Druck)
    kopf_zeile1 = objekt.absender_name or ""
    kopf_teile = [t for t in [objekt.absender_strasse, f"{objekt.absender_plz or ''} {objekt.absender_ort or ''}".strip()] if t]
    kopf_zeile1 = ", ".join([t for t in [kopf_zeile1] + kopf_teile if t])
    kontakt_teile = [t for t in [
        f"Tel. {objekt.absender_telefon}" if objekt.absender_telefon else None,
        objekt.absender_email,
    ] if t]
    if kopf_zeile1:
        story.append(Paragraph(kopf_zeile1, _SMALL))
    if kontakt_teile:
        story.append(Paragraph(", ".join(kontakt_teile), _SMALL))
    story.append(Spacer(1, 18 * mm))

    # Ruecksendeangabe (kleine Zeile ueber der Empfaengeradresse)
    ruecksende_teile = [t for t in [
        objekt.absender_name,
        objekt.absender_strasse,
        f"{objekt.absender_plz or ''} {objekt.absender_ort or ''}".strip(),
    ] if t]
    if ruecksende_teile:
        story.append(Paragraph(", ".join(ruecksende_teile), _SMALL_UNDERLINE))
        story.append(Spacer(1, 3 * mm))

    for zeile in _empfaenger_zeilen(mv, objekt, heute):
        story.append(Paragraph(zeile, _NORMAL))
    story.append(Spacer(1, 6 * mm))

    ort_datum = f"{objekt.absender_ort or objekt.ort or ''}, {heute.strftime('%d.%m.%Y')}"
    story.append(Paragraph(ort_datum, _NORMAL_RIGHT))
    story.append(Spacer(1, 10 * mm))

    von_str = ja.zeitraum_von.strftime("%d.%m.%Y")
    bis_str = ja.zeitraum_bis.strftime("%d.%m.%Y")
    objekt_adresse = f"{objekt.strasse or ''}, {objekt.plz or ''} {objekt.ort or ''}".strip(", ")
    story.append(Paragraph(
        f"Abrechnung über die Mietnebenkosten für den Zeitraum {von_str} – {bis_str}<br/>"
        f"für die Verwaltungseinheit {objekt_adresse}",
        _TITLE,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(_anrede_zeile(mv), _NORMAL))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "auf der Grundlage der für die Wohnanlage erteilten Verwalterabrechnung ergibt sich für "
        "Sie nachstehende Abrechnung über die Mietnebenkosten:",
        _NORMAL,
    ))
    story.append(Spacer(1, 4 * mm))

    # Kostentabelle - Spaltenbreiten mit der Zusammenfassung darunter
    # abgestimmt (_SPALTE_*), damit beide Tabellen exakt dieselbe rechte
    # Spalte fuer Betraege verwenden und optisch nahtlos ineinander uebergehen.
    positionen_je_kostenart = {p.kostenart_id: p for p in ja.positionen}
    gesamttage = (ja.zeitraum_bis - ja.zeitraum_von).days + 1

    kopfzeile = [
        Paragraph("Kostenart", _CELL_BOLD),
        Paragraph("Abrechnungsart", _CELL_BOLD),
        Paragraph("Betrag", _CELL_BOLD_RIGHT),
        Paragraph(f"{ma.tage_im_zeitraum}/{gesamttage} Tagen", _CELL_BOLD_RIGHT),
    ]
    tabellen_rows = [kopfzeile]
    for mp in ma.positionen:
        pos = positionen_je_kostenart.get(mp.kostenart_id)
        betrag_gesamt = pos.betrag if pos else mp.anteil_betrag
        tabellen_rows.append([
            Paragraph(mp.kostenart.bezeichnung, _CELL),
            Paragraph(_abrechnungsart_label(mp.berechnungsmethode), _CELL),
            Paragraph(f"{format_de(betrag_gesamt)} €", _CELL_RIGHT),
            Paragraph(f"{format_de(mp.anteil_betrag)} €", _CELL_RIGHT),
        ])

    nebenkosten_netto = round(ma.summe_umlagefaehig, 2)
    tabellen_rows.append([
        Paragraph("Für den Abrechnungszeitraum betragen die Gesamtkosten:", _CELL),
        "", "",
        Paragraph(f"{format_de(nebenkosten_netto)} €", _CELL_RIGHT),
    ])

    kosten_spalten = [_SPALTE_KOSTENART, _SPALTE_ABRECHNUNGSART, _SPALTE_BETRAG, _SPALTE_ANTEIL]
    kosten_tabelle = Table(tabellen_rows, colWidths=kosten_spalten, hAlign="LEFT")
    letzte_zeile = len(tabellen_rows) - 1
    kosten_tabelle.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
        ("LINEABOVE", (0, letzte_zeile), (-1, letzte_zeile), 0.5, colors.black),
        ("SPAN", (0, letzte_zeile), (2, letzte_zeile)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(kosten_tabelle)
    story.append(Spacer(1, 6 * mm))

    # Netto/MwSt./Brutto + Vorauszahlung + Kontostand - Spalte 1 entspricht in
    # der Breite genau Kostenart+Abrechnungsart+Betrag der Kostentabelle,
    # Spalte 2 der Anteil-Spalte, damit die Betraege exakt darunter stehen.
    mv_von, mv_bis = _mietverhaeltnis_zeitraum(ja, mv)
    zusammenfassung_rows = [[
        Paragraph(f"Nebenkosten {mv_von.strftime('%d.%m.')} - {mv_bis.strftime('%d.%m.%Y')}", _CELL),
        Paragraph(f"{format_de(nebenkosten_netto)} €", _CELL_RIGHT),
    ]]

    # mwst_betrag/gesamtbetrag kommen bewusst aus der Mieterabrechnung-Model-
    # Property (nicht hier neu berechnet), damit PDF und App-Ergebnisanzeige
    # niemals auseinanderlaufen koennen.
    if mv.umsatzsteuerpflichtig:
        satz = mv.mwst_satz if mv.mwst_satz is not None else 19.0
        zusammenfassung_rows.append([
            Paragraph(f"zzgl. {format_de(satz, 1)}% MwSt.", _CELL),
            Paragraph(f"{format_de(ma.mwst_betrag)} €", _CELL_RIGHT),
        ])
    gesamtbetrag = ma.gesamtbetrag

    zusammenfassung_rows.append([
        Paragraph("Gesamtbetrag", _CELL_BOLD),
        Paragraph(f"{format_de(gesamtbetrag)} €", _CELL_BOLD_RIGHT),
    ])
    zusammenfassung_rows.append(["", ""])
    zusammenfassung_rows.append([Paragraph("Hierauf haben Sie vorausgezahlt", _CELL), ""])

    vorauszahlung = round(ma.summe_vorauszahlung, 2)
    kontostand = round(vorauszahlung - gesamtbetrag, 2)

    zusammenfassung_rows.append([
        Paragraph("Vorauszahlung gesamt", _CELL),
        Paragraph(f"{format_de(vorauszahlung)} €", _CELL_RIGHT),
    ])
    zusammenfassung_rows.append([
        Paragraph("Kontostand", _CELL),
        Paragraph(f"{format_de(kontostand)} €", _CELL_RIGHT),
    ])

    zusammenfassung_spalten = [
        _SPALTE_KOSTENART + _SPALTE_ABRECHNUNGSART + _SPALTE_BETRAG, _SPALTE_ANTEIL,
    ]
    zusammenfassung = Table(zusammenfassung_rows, colWidths=zusammenfassung_spalten, hAlign="LEFT")
    n_letzte = len(zusammenfassung_rows) - 1
    n_voraus = len(zusammenfassung_rows) - 2
    zusammenfassung.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEABOVE", (0, n_voraus), (-1, n_voraus), 0.5, colors.black),
        ("LINEBELOW", (0, n_letzte), (-1, n_letzte), 0.5, colors.black),
    ]))
    story.append(zusammenfassung)
    story.append(Spacer(1, 6 * mm))

    if kontostand < -0.005:
        story.append(Paragraph(
            f"Bitte überweisen Sie den Restbetrag in Höhe von {format_de(abs(kontostand))} € innerhalb "
            f"von 14 Tagen auf unten stehendes Konto.",
            _NORMAL,
        ))
    elif kontostand > 0.005:
        story.append(Paragraph(
            f"Das Guthaben in Höhe von {format_de(kontostand)} € überweisen wir Ihnen in den nächsten "
            f"Tagen auf das uns bekannte Konto zurück.",
            _NORMAL,
        ))
    else:
        story.append(Paragraph("Es ergibt sich weder eine Nachzahlung noch ein Guthaben.", _NORMAL))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("mit freundlichen Grüßen,", _NORMAL))
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph(objekt.absender_name or "", _NORMAL))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Anlage", _NORMAL))

    return story


def erzeuge_mieter_pdf(ja: models.Jahresabrechnung, ma: models.Mieterabrechnung) -> bytes:
    """Erzeugt das PDF-Anschreiben fuer einen einzelnen Mieter (eine
    Mieterabrechnung, kein Leerstand)."""
    puffer = io.BytesIO()
    doc = SimpleDocTemplate(
        puffer, pagesize=A4,
        leftMargin=25 * mm, rightMargin=20 * mm, topMargin=15 * mm, bottomMargin=20 * mm,
    )
    story = _brief_story(ja, ma, date.today())
    footer = partial(_footer, objekt=ja.objekt)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return puffer.getvalue()


def erzeuge_sammel_pdf(ja: models.Jahresabrechnung) -> bytes:
    """Erzeugt ein Sammel-PDF mit den Anschreiben aller Mieter (nicht
    Leerstand) einer Jahresabrechnung, je Mieter beginnend auf einer neuen
    Seite."""
    puffer = io.BytesIO()
    doc = SimpleDocTemplate(
        puffer, pagesize=A4,
        leftMargin=25 * mm, rightMargin=20 * mm, topMargin=15 * mm, bottomMargin=20 * mm,
    )
    heute = date.today()
    mieter_abrechnungen = [ma for ma in ja.mieterabrechnungen if not ma.ist_leerstand]
    mieter_abrechnungen.sort(key=lambda ma: ma.sortier_datum)

    story = []
    for i, ma in enumerate(mieter_abrechnungen):
        if i > 0:
            story.append(PageBreak())
        story.extend(_brief_story(ja, ma, heute))

    footer = partial(_footer, objekt=ja.objekt)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return puffer.getvalue()
