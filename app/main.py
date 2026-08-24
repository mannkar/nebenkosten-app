from __future__ import annotations

import csv
import io
import os
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import models, calc, seed, pdf as pdf_export, migrate
from .database import get_db

# Legt beim Start fehlende Tabellen/Spalten per Alembic-Migration an (statt
# wie frueher per create_all()) - bestehende Daten bleiben dabei erhalten.
# Siehe app/migrate.py.
migrate.migrieren()

app = FastAPI(title="Nebenkostenabrechnung")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def static_version(dateiname: str) -> int:
    """Liefert den Aenderungszeitpunkt einer Datei im static-Ordner. Wird als
    Query-Parameter an CSS/JS-Links gehaengt, damit Browser nach jeder
    Aenderung automatisch die neue Version laden statt eine gecachte Version
    anzuzeigen (kein manueller Hard-Refresh noetig)."""
    pfad = os.path.join("app/static", dateiname)
    try:
        return int(os.path.getmtime(pfad))
    except OSError:
        return 0


templates.env.globals["static_version"] = static_version
templates.env.filters["de"] = calc.format_de


@app.on_event("startup")
def startup():
    db = next(get_db())
    seed.seed_kostenarten(db)
    seed.seed_testdaten(db)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_hv_override(value: str) -> Optional[bool]:
    """Wandelt den Override-Select fuer 'HV-abgerechnet' um: '' = Standard der
    Kostenart uebernehmen (None), '1' = ja, '0' = nein."""
    if value == "1":
        return True
    if value == "0":
        return False
    return None


MONATE = [
    (1, "Januar"), (2, "Februar"), (3, "März"), (4, "April"),
    (5, "Mai"), (6, "Juni"), (7, "Juli"), (8, "August"),
    (9, "September"), (10, "Oktober"), (11, "November"), (12, "Dezember"),
]


def berechne_abrechnungszeitraum(objekt: models.Objekt, jahr: int) -> tuple[date, date]:
    """Leitet den Abrechnungszeitraum eines Jahres aus dem am Objekt
    hinterlegten Start-Monat ab (1 = Kalenderjahr 1.1.-31.12., sonst z.B.
    Wirtschaftsjahr 1.7.-30.6. des Folgejahres)."""
    start_monat = objekt.abrechnung_start_monat or 1
    von = date(jahr, start_monat, 1)
    bis = date(jahr + 1, start_monat, 1) - timedelta(days=1)
    return von, bis


def log_aenderungen(db: Session, entity_typ: str, entity_id: int, alt: dict, neu: dict) -> None:
    """Vergleicht alte und neue Feldwerte und legt fuer jedes tatsaechlich
    geaenderte Feld einen Eintrag in der Aenderungshistorie an."""
    for feld, neuer_wert in neu.items():
        alter_wert = alt.get(feld)
        if (alter_wert or "") != (neuer_wert or ""):
            db.add(models.AenderungHistorie(
                entity_typ=entity_typ, entity_id=entity_id, feld=feld,
                alter_wert=str(alter_wert) if alter_wert not in (None, "") else None,
                neuer_wert=str(neuer_wert) if neuer_wert not in (None, "") else None,
            ))


# ---------------------------------------------------------------- Dashboard
@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    objekte = db.query(models.Objekt).order_by(models.Objekt.bezeichnung).all()
    return templates.TemplateResponse(
        "index.html", {"request": request, "objekte": objekte}
    )


# ------------------------------------------------------------------ Kontakt
# Zentrales Adressbuch: ersetzt die frueher getrennten Konzepte Verwaltung
# und Person. Ein Kontakt kann gleichzeitig Eigentuemer und/oder Verwaltung
# mehrerer Objekte sein, und/oder Mieter in einem oder mehreren
# Mietverhaeltnissen - jeweils per Fremdschluessel/Verknuepfungstabelle
# referenziert, ohne die Adressdaten zu duplizieren.
@app.get("/kontakte")
def kontakte_list(request: Request, db: Session = Depends(get_db)):
    kontakte = db.query(models.Kontakt).order_by(models.Kontakt.nachname, models.Kontakt.firma_name).all()
    return templates.TemplateResponse(
        "kontakte.html", {"request": request, "kontakte": kontakte}
    )


@app.post("/kontakte/neu")
def kontakt_neu(
    ist_firma: str = Form(""),
    anrede: str = Form(""),
    vorname: str = Form(""),
    nachname: str = Form(""),
    firma_name: str = Form(""),
    telefon: str = Form(""),
    email: str = Form(""),
    bank_name: str = Form(""),
    bank_iban: str = Form(""),
    bank_bic: str = Form(""),
    notizen: str = Form(""),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    ist_mieter: str = Form(""),
    ist_eigentuemer: str = Form(""),
    ist_verwalter: str = Form(""),
    ist_handwerker: str = Form(""),
    db: Session = Depends(get_db),
):
    k = models.Kontakt(
        ist_firma=bool(ist_firma), anrede=anrede or None, vorname=vorname or None,
        nachname=nachname or None, firma_name=firma_name or None,
        telefon=telefon or None, email=email or None,
        bank_name=bank_name or None, bank_iban=bank_iban or None, bank_bic=bank_bic or None,
        notizen=notizen or None,
        ist_mieter=bool(ist_mieter), ist_eigentuemer=bool(ist_eigentuemer),
        ist_verwalter=bool(ist_verwalter), ist_handwerker=bool(ist_handwerker),
    )
    db.add(k)
    db.flush()
    if strasse or plz or ort:
        db.add(models.KontaktAdresse(kontakt_id=k.id, strasse=strasse, plz=plz, ort=ort))
    db.commit()
    return RedirectResponse(f"/kontakte/{k.id}", status_code=303)


@app.get("/kontakte/{kontakt_id}")
def kontakt_detail(request: Request, kontakt_id: int, db: Session = Depends(get_db)):
    k = db.get(models.Kontakt, kontakt_id)
    historie = (
        db.query(models.AenderungHistorie)
        .filter_by(entity_typ="Kontakt", entity_id=kontakt_id)
        .order_by(models.AenderungHistorie.geaendert_am.desc())
        .all()
    )
    return templates.TemplateResponse(
        "kontakt_detail.html", {"request": request, "k": k, "historie": historie}
    )


@app.post("/kontakte/{kontakt_id}/bearbeiten")
def kontakt_bearbeiten(
    kontakt_id: int,
    ist_firma: str = Form(""),
    anrede: str = Form(""),
    vorname: str = Form(""),
    nachname: str = Form(""),
    firma_name: str = Form(""),
    telefon: str = Form(""),
    email: str = Form(""),
    bank_name: str = Form(""),
    bank_iban: str = Form(""),
    bank_bic: str = Form(""),
    notizen: str = Form(""),
    ist_mieter: str = Form(""),
    ist_eigentuemer: str = Form(""),
    ist_verwalter: str = Form(""),
    ist_handwerker: str = Form(""),
    db: Session = Depends(get_db),
):
    k = db.get(models.Kontakt, kontakt_id)
    neu = {
        "ist_firma": bool(ist_firma), "anrede": anrede or None, "vorname": vorname or None,
        "nachname": nachname or None, "firma_name": firma_name or None,
        "telefon": telefon or None, "email": email or None,
        "bank_name": bank_name or None, "bank_iban": bank_iban or None,
        "bank_bic": bank_bic or None, "notizen": notizen or None,
        "ist_mieter": bool(ist_mieter), "ist_eigentuemer": bool(ist_eigentuemer),
        "ist_verwalter": bool(ist_verwalter), "ist_handwerker": bool(ist_handwerker),
    }
    alt = {feld: getattr(k, feld) for feld in neu}
    log_aenderungen(db, "Kontakt", kontakt_id, alt, neu)
    for feld, wert in neu.items():
        setattr(k, feld, wert)
    db.commit()
    return RedirectResponse(f"/kontakte/{kontakt_id}", status_code=303)


@app.post("/kontakte/{kontakt_id}/loeschen")
def kontakt_loeschen(kontakt_id: int, db: Session = Depends(get_db)):
    k = db.get(models.Kontakt, kontakt_id)
    if k:
        # Referenzen an Objekten loesen (nicht die Objekte selbst loeschen) -
        # Mietverhaeltnis-Verknuepfungen werden ueber die cascade-Konfiguration
        # am Kontakt-Modell automatisch mit entfernt.
        for o in list(k.objekte_als_eigentuemer):
            o.eigentuemer_id = None
        for o in list(k.objekte_als_verwaltung):
            o.verwaltung_id = None
        db.delete(k)
        db.commit()
    return RedirectResponse("/kontakte", status_code=303)


@app.post("/kontakte/{kontakt_id}/adresse/neu")
def kontakt_adresse_neu(
    kontakt_id: int,
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    gueltig_von: str = Form(""),
    gueltig_bis: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.KontaktAdresse(
        kontakt_id=kontakt_id, strasse=strasse, plz=plz, ort=ort,
        gueltig_von=parse_date(gueltig_von), gueltig_bis=parse_date(gueltig_bis),
        notizen=notizen or None,
    ))
    db.commit()
    return RedirectResponse(f"/kontakte/{kontakt_id}#adressen", status_code=303)


@app.post("/kontakt-adresse/{adresse_id}/bearbeiten")
def kontakt_adresse_bearbeiten(
    adresse_id: int,
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    gueltig_von: str = Form(""),
    gueltig_bis: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    a = db.get(models.KontaktAdresse, adresse_id)
    kontakt_id = a.kontakt_id if a else None
    if a:
        a.strasse = strasse
        a.plz = plz
        a.ort = ort
        a.gueltig_von = parse_date(gueltig_von)
        a.gueltig_bis = parse_date(gueltig_bis)
        a.notizen = notizen or None
        db.commit()
    return RedirectResponse(f"/kontakte/{kontakt_id}#adressen", status_code=303)


@app.post("/kontakt-adresse/{adresse_id}/loeschen")
def kontakt_adresse_loeschen(adresse_id: int, db: Session = Depends(get_db)):
    a = db.get(models.KontaktAdresse, adresse_id)
    kontakt_id = a.kontakt_id if a else None
    if a:
        db.delete(a)
        db.commit()
    return RedirectResponse(f"/kontakte/{kontakt_id}#adressen", status_code=303)


# -------------------------------------------------------------------- Objekt
@app.get("/objekte")
def objekte_list(request: Request, db: Session = Depends(get_db)):
    objekte = db.query(models.Objekt).order_by(models.Objekt.bezeichnung).all()
    kontakte = db.query(models.Kontakt).order_by(models.Kontakt.nachname, models.Kontakt.firma_name).all()
    return templates.TemplateResponse(
        "objekte.html",
        {"request": request, "objekte": objekte, "kontakte": kontakte, "monate": MONATE},
    )


@app.post("/objekte/neu")
def objekt_neu(
    bezeichnung: str = Form(...),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    flurstueck: str = Form(""),
    wohnflaeche_qm: str = Form(""),
    miteigentumsanteil: str = Form(""),
    gesamtanteile: str = Form(""),
    hausgeld_monatlich: str = Form(""),
    ew_aktenzeichen: str = Form(""),
    afa_jahresbetrag: str = Form(""),
    eigentuemer_id: str = Form(""),
    verwaltung_id: str = Form(""),
    abrechnung_start_monat: str = Form("1"),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    o = models.Objekt(
        bezeichnung=bezeichnung, strasse=strasse, plz=plz, ort=ort,
        flurstueck=flurstueck,
        wohnflaeche_qm=float(wohnflaeche_qm.replace(",", ".")) if wohnflaeche_qm else None,
        miteigentumsanteil=miteigentumsanteil,
        gesamtanteile=float(gesamtanteile.replace(",", ".")) if gesamtanteile else None,
        hausgeld_monatlich=float(hausgeld_monatlich.replace(",", ".")) if hausgeld_monatlich else None,
        ew_aktenzeichen=ew_aktenzeichen or None,
        afa_jahresbetrag=float(afa_jahresbetrag.replace(",", ".")) if afa_jahresbetrag else None,
        eigentuemer_id=int(eigentuemer_id) if eigentuemer_id else None,
        verwaltung_id=int(verwaltung_id) if verwaltung_id else None,
        abrechnung_start_monat=int(abrechnung_start_monat) if abrechnung_start_monat else 1,
        notizen=notizen,
    )
    db.add(o)
    db.commit()
    return RedirectResponse(f"/objekte/{o.id}?neu=1", status_code=303)


@app.post("/objekte/{objekt_id}/loeschen")
def objekt_loeschen(objekt_id: int, db: Session = Depends(get_db)):
    o = db.get(models.Objekt, objekt_id)
    if o:
        db.delete(o)
        db.commit()
    return RedirectResponse("/objekte", status_code=303)


@app.get("/objekte/{objekt_id}")
def objekt_detail(request: Request, objekt_id: int, db: Session = Depends(get_db)):
    objekt = db.get(models.Objekt, objekt_id)
    kostenarten = db.query(models.Kostenart).order_by(models.Kostenart.bezeichnung).all()
    kontakte = db.query(models.Kontakt).order_by(models.Kontakt.nachname, models.Kontakt.firma_name).all()
    neu = bool(request.query_params.get("neu"))
    return templates.TemplateResponse(
        "objekt_detail.html",
        {"request": request, "objekt": objekt, "kostenarten": kostenarten,
         "kontakte": kontakte, "monate": MONATE, "neu": neu},
    )


@app.post("/objekte/{objekt_id}/bearbeiten")
def objekt_bearbeiten(
    objekt_id: int,
    bezeichnung: str = Form(...),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    flurstueck: str = Form(""),
    wohnflaeche_qm: str = Form(""),
    miteigentumsanteil: str = Form(""),
    gesamtanteile: str = Form(""),
    hausgeld_monatlich: str = Form(""),
    ew_aktenzeichen: str = Form(""),
    afa_jahresbetrag: str = Form(""),
    eigentuemer_id: str = Form(""),
    verwaltung_id: str = Form(""),
    abrechnung_start_monat: str = Form("1"),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    o = db.get(models.Objekt, objekt_id)
    o.bezeichnung = bezeichnung
    o.strasse = strasse
    o.plz = plz
    o.ort = ort
    o.flurstueck = flurstueck
    o.wohnflaeche_qm = float(wohnflaeche_qm.replace(",", ".")) if wohnflaeche_qm else None
    o.miteigentumsanteil = miteigentumsanteil
    o.gesamtanteile = float(gesamtanteile.replace(",", ".")) if gesamtanteile else None
    o.hausgeld_monatlich = float(hausgeld_monatlich.replace(",", ".")) if hausgeld_monatlich else None
    o.ew_aktenzeichen = ew_aktenzeichen or None
    o.afa_jahresbetrag = float(afa_jahresbetrag.replace(",", ".")) if afa_jahresbetrag else None
    o.eigentuemer_id = int(eigentuemer_id) if eigentuemer_id else None
    o.verwaltung_id = int(verwaltung_id) if verwaltung_id else None
    o.abrechnung_start_monat = int(abrechnung_start_monat) if abrechnung_start_monat else 1
    o.notizen = notizen
    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


# ------------------------------------------------------------------- Zaehler
@app.post("/objekte/{objekt_id}/zaehler/neu")
def zaehler_neu(
    objekt_id: int,
    typ: str = Form(...),
    bezeichnung: str = Form(""),
    zaehlernummer: str = Form(""),
    einbaudatum: str = Form(""),
    ausbaudatum: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.Zaehler(
        objekt_id=objekt_id, typ=typ, bezeichnung=bezeichnung,
        zaehlernummer=zaehlernummer,
        einbaudatum=parse_date(einbaudatum), ausbaudatum=parse_date(ausbaudatum),
    ))
    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}#zaehler", status_code=303)


@app.post("/zaehler/{zaehler_id}/bearbeiten")
def zaehler_bearbeiten(
    zaehler_id: int,
    typ: str = Form(...),
    bezeichnung: str = Form(""),
    zaehlernummer: str = Form(""),
    einbaudatum: str = Form(""),
    ausbaudatum: str = Form(""),
    db: Session = Depends(get_db),
):
    z = db.get(models.Zaehler, zaehler_id)
    objekt_id = z.objekt_id if z else None
    if z:
        z.typ = typ
        z.bezeichnung = bezeichnung
        z.zaehlernummer = zaehlernummer
        z.einbaudatum = parse_date(einbaudatum)
        z.ausbaudatum = parse_date(ausbaudatum)
        db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}#zaehler", status_code=303)


@app.post("/zaehler/{zaehler_id}/loeschen")
def zaehler_loeschen(zaehler_id: int, db: Session = Depends(get_db)):
    z = db.get(models.Zaehler, zaehler_id)
    objekt_id = z.objekt_id if z else None
    if z:
        db.delete(z)
        db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}#zaehler", status_code=303)


@app.get("/zaehler/{zaehler_id}")
def zaehler_detail(request: Request, zaehler_id: int, db: Session = Depends(get_db)):
    z = db.get(models.Zaehler, zaehler_id)
    return templates.TemplateResponse(
        "zaehler_detail.html", {"request": request, "zaehler": z}
    )


@app.post("/zaehler/{zaehler_id}/ablesungen/neu")
def zaehlerstand_neu(
    zaehler_id: int,
    datum: str = Form(...),
    stand: str = Form(...),
    anlass: str = Form("Jahresablesung"),
    db: Session = Depends(get_db),
):
    db.add(models.Zaehlerstand(
        zaehler_id=zaehler_id, datum=parse_date(datum),
        stand=float(stand.replace(",", ".")), anlass=anlass,
    ))
    db.commit()
    return RedirectResponse(f"/zaehler/{zaehler_id}", status_code=303)


@app.post("/zaehlerstaende/{zaehlerstand_id}/bearbeiten")
def zaehlerstand_bearbeiten(
    zaehlerstand_id: int,
    datum: str = Form(...),
    stand: str = Form(...),
    anlass: str = Form("Jahresablesung"),
    db: Session = Depends(get_db),
):
    zs = db.get(models.Zaehlerstand, zaehlerstand_id)
    zaehler_id = zs.zaehler_id if zs else None
    if zs:
        zs.datum = parse_date(datum)
        zs.stand = float(stand.replace(",", "."))
        zs.anlass = anlass
        db.commit()
    return RedirectResponse(f"/zaehler/{zaehler_id}", status_code=303)


@app.post("/zaehlerstaende/{zaehlerstand_id}/loeschen")
def zaehlerstand_loeschen(zaehlerstand_id: int, db: Session = Depends(get_db)):
    zs = db.get(models.Zaehlerstand, zaehlerstand_id)
    zaehler_id = zs.zaehler_id if zs else None
    if zs:
        db.delete(zs)
        db.commit()
    return RedirectResponse(f"/zaehler/{zaehler_id}", status_code=303)


# ------------------------------------------------------------ Mietverhaeltnis
@app.get("/mieter")
def mieter_list(request: Request, objekt_id: str = "", db: Session = Depends(get_db)):
    query = db.query(models.Mietverhaeltnis).join(models.Objekt)
    if objekt_id:
        query = query.filter(models.Mietverhaeltnis.objekt_id == int(objekt_id))
    mietverhaeltnisse = query.order_by(
        models.Objekt.bezeichnung, models.Mietverhaeltnis.einzug
    ).all()
    objekte = db.query(models.Objekt).order_by(models.Objekt.bezeichnung).all()
    return templates.TemplateResponse(
        "mieter.html",
        {
            "request": request, "mietverhaeltnisse": mietverhaeltnisse,
            "objekte": objekte, "objekt_filter": objekt_id,
        },
    )


PERSON_FELDER = [
    "kontakt_id", "vorhandener_kontakt", "anrede", "vorname", "nachname",
    "email", "telefon", "adresse_strasse", "adresse_plz", "adresse_ort",
]


def _personen_aus_form(form) -> list[dict]:
    """Liest die Personen-Felder aus dem rohen Formular (mehrere Werte pro
    Feldname, ein Wert pro Person in DOM-Reihenfolge) und baut daraus eine
    Liste von Personen-Dicts. Jede Zeile ist entweder ein bereits
    verknuepfter Kontakt (kontakt_id gesetzt, aus dem Bearbeiten-Formular),
    die Auswahl eines bestehenden Kontakts aus dem Adressbuch
    (vorhandener_kontakt), oder legt einen neuen Kontakt an (Vor-/Nachname
    ausgefuellt). Komplett leere Zeilen (z.B. eine per JS hinzugefuegte,
    aber leer gelassene Zeile) werden uebersprungen.

    Wichtig: die Zuordnung der Werte zu "welcher Person" passiert rein ueber
    die Position (Index) innerhalb jeder gleichnamigen Feldliste - ein per
    JS deaktiviertes ("disabled") Formularfeld wird vom Browser beim
    Absenden komplett ausgelassen und wuerde dadurch alle Listen
    unterschiedlich lang machen und Werte verschieben (siehe
    mietverhaeltnis_form.html, wo deshalb bewusst "readonly" statt
    "disabled" verwendet wird). "anzahl" wird trotzdem defensiv ueber die
    LAENGSTE der Feldlisten bestimmt statt nur ueber "vorname", damit ein
    einzelnes fehlendes Feld nicht dazu fuehrt, dass eine ganze
    Personen-Zeile (z.B. eine ausgewaehlte, aber sonst leere
    Adressbuch-Auswahl) stillschweigend verloren geht."""
    listen = {feld: form.getlist(f"person_{feld}") for feld in PERSON_FELDER}
    anzahl = max((len(werte) for werte in listen.values()), default=0)

    def wert(feld: str, i: int) -> str:
        werte = listen[feld]
        return werte[i] if i < len(werte) else ""

    personen = []
    for i in range(anzahl):
        kontakt_id = wert("kontakt_id", i).strip()
        vorhandener_kontakt = wert("vorhandener_kontakt", i).strip()
        vorname = wert("vorname", i).strip()
        nachname = wert("nachname", i).strip()
        if not kontakt_id and not vorhandener_kontakt and not vorname and not nachname:
            continue
        eintrag = {
            feld: wert(feld, i).strip() or None
            for feld in PERSON_FELDER
        }
        eintrag["kontakt_id"] = int(kontakt_id) if kontakt_id else None
        eintrag["vorhandener_kontakt"] = int(vorhandener_kontakt) if vorhandener_kontakt else None
        personen.append(eintrag)
    return personen


def _mietverhaeltnis_personen_speichern(db: Session, mv_id: int, form) -> None:
    """Legt fuer jede Personen-Zeile des Formulars die passende Verknuepfung
    an: reine Wiederverwendung eines bestehenden Kontakts (Auswahl aus dem
    Adressbuch, keine Aenderung an dessen Daten), Aktualisierung eines schon
    verknuepften Kontakts, oder Neuanlage. Eine ggf. mitgeschickte Adresse
    wird nur als neue Adresshistorien-Zeile angelegt, wenn sie von der
    aktuell hinterlegten Adresse abweicht (verhindert Duplikate bei jedem
    erneuten Speichern des Formulars ohne inhaltliche Aenderung)."""
    for p in _personen_aus_form(form):
        if p["vorhandener_kontakt"] and not p["kontakt_id"]:
            db.add(models.MietverhaeltnisKontakt(
                mietverhaeltnis_id=mv_id, kontakt_id=p["vorhandener_kontakt"],
            ))
            continue

        if p["kontakt_id"]:
            kontakt = db.get(models.Kontakt, p["kontakt_id"])
        else:
            kontakt = models.Kontakt(ist_firma=False)
            db.add(kontakt)

        kontakt.anrede = p["anrede"]
        kontakt.vorname = p["vorname"] or ""
        kontakt.nachname = p["nachname"] or ""
        kontakt.email = p["email"]
        kontakt.telefon = p["telefon"]
        db.flush()

        if p["adresse_strasse"] or p["adresse_plz"] or p["adresse_ort"]:
            aktuelle = kontakt.aktuelle_adresse
            neue_adresse = (p["adresse_strasse"], p["adresse_plz"], p["adresse_ort"])
            alte_adresse = (
                (aktuelle.strasse, aktuelle.plz, aktuelle.ort) if aktuelle else None
            )
            if neue_adresse != alte_adresse:
                db.add(models.KontaktAdresse(
                    kontakt_id=kontakt.id,
                    strasse=p["adresse_strasse"], plz=p["adresse_plz"], ort=p["adresse_ort"],
                ))

        db.add(models.MietverhaeltnisKontakt(mietverhaeltnis_id=mv_id, kontakt_id=kontakt.id))


@app.get("/objekte/{objekt_id}/mietverhaeltnisse/neu")
def mietverhaeltnis_neu_form(request: Request, objekt_id: int, db: Session = Depends(get_db)):
    objekt = db.get(models.Objekt, objekt_id)
    laufendes_mv = (
        db.query(models.Mietverhaeltnis)
        .filter(models.Mietverhaeltnis.objekt_id == objekt_id,
                models.Mietverhaeltnis.auszug.is_(None))
        .first()
    )
    kontakte = db.query(models.Kontakt).order_by(models.Kontakt.nachname, models.Kontakt.firma_name).all()
    return templates.TemplateResponse(
        "mietverhaeltnis_form.html",
        {"request": request, "objekt": objekt, "mv": None, "laufendes_mv": laufendes_mv,
         "kontakte": kontakte},
    )


@app.post("/objekte/{objekt_id}/mietverhaeltnisse/neu")
async def mietverhaeltnis_neu(
    objekt_id: int,
    request: Request,
    einzug: str = Form(...),
    auszug: str = Form(""),
    nk_abschlag_monatlich: str = Form(""),
    umsatzsteuerpflichtig: str = Form(""),
    mwst_satz: str = Form(""),
    bezeichnung: str = Form(""),
    ist_firma: str = Form(""),
    firma_name: str = Form(""),
    nk_rechnungsadresse_abweichend: str = Form(""),
    nk_rechnungsadresse_name: str = Form(""),
    nk_rechnungsadresse_strasse: str = Form(""),
    nk_rechnungsadresse_plz: str = Form(""),
    nk_rechnungsadresse_ort: str = Form(""),
    notizen: str = Form(""),
    altes_mv_id: str = Form(""),
    altes_mv_auszug: str = Form(""),
    db: Session = Depends(get_db),
):
    form = await request.form()
    neue_einzug = parse_date(einzug)

    mv = models.Mietverhaeltnis(
        objekt_id=objekt_id, einzug=neue_einzug, auszug=parse_date(auszug),
        nk_abschlag_monatlich=(
            float(nk_abschlag_monatlich.replace(",", ".")) if nk_abschlag_monatlich else None
        ),
        umsatzsteuerpflichtig=bool(umsatzsteuerpflichtig),
        mwst_satz=float(mwst_satz.replace(",", ".")) if mwst_satz else 19.0,
        bezeichnung=bezeichnung or None,
        ist_firma=bool(ist_firma),
        firma_name=firma_name or None,
        nk_rechnungsadresse_abweichend=bool(nk_rechnungsadresse_abweichend),
        nk_rechnungsadresse_name=nk_rechnungsadresse_name or None,
        nk_rechnungsadresse_strasse=nk_rechnungsadresse_strasse or None,
        nk_rechnungsadresse_plz=nk_rechnungsadresse_plz or None,
        nk_rechnungsadresse_ort=nk_rechnungsadresse_ort or None,
        notizen=notizen,
    )
    db.add(mv)
    db.flush()
    _mietverhaeltnis_personen_speichern(db, mv.id, form)

    # Laufendes Mietverhaeltnis ggf. beenden und Luecke als Leerstand erfassen
    if altes_mv_id and altes_mv_auszug:
        altes_mv = db.get(models.Mietverhaeltnis, int(altes_mv_id))
        neuer_auszug = parse_date(altes_mv_auszug)
        if altes_mv and neuer_auszug:
            altes_mv.auszug = neuer_auszug
            luecke_von = neuer_auszug + timedelta(days=1)
            luecke_bis = neue_einzug - timedelta(days=1)
            if luecke_von <= luecke_bis:
                db.add(models.Leerstand(
                    objekt_id=objekt_id, von=luecke_von, bis=luecke_bis,
                    notizen=(
                        f"Automatisch erfasst: Auszug Vormieter {neuer_auszug}, "
                        f"Einzug Nachmieter {neue_einzug}."
                    ),
                ))

    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


@app.get("/mietverhaeltnisse/{mv_id}/bearbeiten")
def mietverhaeltnis_bearbeiten_form(request: Request, mv_id: int, db: Session = Depends(get_db)):
    mv = db.get(models.Mietverhaeltnis, mv_id)
    kontakte = db.query(models.Kontakt).order_by(models.Kontakt.nachname, models.Kontakt.firma_name).all()
    return templates.TemplateResponse(
        "mietverhaeltnis_form.html",
        {"request": request, "objekt": mv.objekt, "mv": mv, "laufendes_mv": None,
         "kontakte": kontakte, "fehler": request.query_params.get("fehler")},
    )


@app.post("/mietverhaeltnisse/{mv_id}/bearbeiten")
async def mietverhaeltnis_bearbeiten(
    mv_id: int,
    request: Request,
    einzug: str = Form(...),
    auszug: str = Form(""),
    nk_abschlag_monatlich: str = Form(""),
    umsatzsteuerpflichtig: str = Form(""),
    mwst_satz: str = Form(""),
    bezeichnung: str = Form(""),
    ist_firma: str = Form(""),
    firma_name: str = Form(""),
    nk_rechnungsadresse_abweichend: str = Form(""),
    nk_rechnungsadresse_name: str = Form(""),
    nk_rechnungsadresse_strasse: str = Form(""),
    nk_rechnungsadresse_plz: str = Form(""),
    nk_rechnungsadresse_ort: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    form = await request.form()
    mv = db.get(models.Mietverhaeltnis, mv_id)
    mv.einzug = parse_date(einzug)
    mv.auszug = parse_date(auszug)
    mv.nk_abschlag_monatlich = (
        float(nk_abschlag_monatlich.replace(",", ".")) if nk_abschlag_monatlich else None
    )
    mv.umsatzsteuerpflichtig = bool(umsatzsteuerpflichtig)
    mv.mwst_satz = float(mwst_satz.replace(",", ".")) if mwst_satz else 19.0
    mv.bezeichnung = bezeichnung or None
    mv.ist_firma = bool(ist_firma)
    mv.firma_name = firma_name or None
    mv.nk_rechnungsadresse_abweichend = bool(nk_rechnungsadresse_abweichend)
    mv.nk_rechnungsadresse_name = nk_rechnungsadresse_name or None
    mv.nk_rechnungsadresse_strasse = nk_rechnungsadresse_strasse or None
    mv.nk_rechnungsadresse_plz = nk_rechnungsadresse_plz or None
    mv.nk_rechnungsadresse_ort = nk_rechnungsadresse_ort or None
    mv.notizen = notizen
    # Nur die Verknuepfungen loesen (nicht die Kontakte selbst loeschen -
    # die bleiben als wiederverwendbare Adressbuch-Eintraege erhalten, auch
    # wenn sie hier gerade abgewaehlt/ersetzt werden).
    for alter_link in list(mv.kontakt_links):
        db.delete(alter_link)
    db.flush()
    _mietverhaeltnis_personen_speichern(db, mv.id, form)
    db.commit()
    return RedirectResponse(f"/objekte/{mv.objekt_id}", status_code=303)


@app.post("/mietverhaeltnisse/{mv_id}/loeschen")
def mietverhaeltnis_loeschen(mv_id: int, db: Session = Depends(get_db)):
    mv = db.get(models.Mietverhaeltnis, mv_id)
    if not mv:
        return RedirectResponse("/objekte", status_code=303)

    # Zwingende Pruefung: sind fuer dieses Mietverhaeltnis bereits
    # Vorauszahlungen (tatsaechlich erhaltene Zahlungen) erfasst, wird das
    # Loeschen abgelehnt statt die Zahlungsdaten stillschweigend zu
    # entfernen - der Nutzer muss die betreffenden Vorauszahlungen erst
    # bewusst selbst loeschen (in der jeweiligen Jahresabrechnung), bevor
    # das Mietverhaeltnis geloescht werden kann.
    if mv.vorauszahlungen:
        nachricht = (
            f"Mietverhältnis kann nicht gelöscht werden: Es sind bereits "
            f"{len(mv.vorauszahlungen)} Vorauszahlung(en) erfasst. Bitte zuerst die "
            f"betreffenden Vorauszahlungen in der jeweiligen Jahresabrechnung löschen."
        )
        return RedirectResponse(
            f"/mietverhaeltnisse/{mv_id}/bearbeiten?fehler={quote(nachricht)}",
            status_code=303,
        )

    objekt_id = mv.objekt_id
    db.delete(mv)
    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


# -------------------------------------------------------------- Personenzahl
@app.post("/mietverhaeltnisse/{mv_id}/personenzahl/neu")
def personenzahl_neu(
    mv_id: int,
    ab_datum: str = Form(...),
    anzahl_personen: str = Form(...),
    db: Session = Depends(get_db),
):
    db.add(models.Personenzahl(
        mietverhaeltnis_id=mv_id,
        ab_datum=parse_date(ab_datum),
        anzahl_personen=int(anzahl_personen),
    ))
    db.commit()
    return RedirectResponse(f"/mietverhaeltnisse/{mv_id}/bearbeiten#personenzahl", status_code=303)


@app.post("/personenzahl/{pz_id}/bearbeiten")
def personenzahl_bearbeiten(
    pz_id: int,
    ab_datum: str = Form(...),
    anzahl_personen: str = Form(...),
    db: Session = Depends(get_db),
):
    pz = db.get(models.Personenzahl, pz_id)
    if pz:
        pz.ab_datum = parse_date(ab_datum)
        pz.anzahl_personen = int(anzahl_personen)
        db.commit()
    mv_id = pz.mietverhaeltnis_id if pz else 0
    return RedirectResponse(f"/mietverhaeltnisse/{mv_id}/bearbeiten#personenzahl", status_code=303)


@app.post("/personenzahl/{pz_id}/loeschen")
def personenzahl_loeschen(pz_id: int, db: Session = Depends(get_db)):
    pz = db.get(models.Personenzahl, pz_id)
    mv_id = pz.mietverhaeltnis_id if pz else 0
    if pz:
        db.delete(pz)
        db.commit()
    return RedirectResponse(f"/mietverhaeltnisse/{mv_id}/bearbeiten#personenzahl", status_code=303)


# ------------------------------------------------------------------ Kostenart
@app.get("/kostenarten")
def kostenarten_list(request: Request, db: Session = Depends(get_db)):
    kostenarten = db.query(models.Kostenart).order_by(models.Kostenart.bezeichnung).all()
    return templates.TemplateResponse(
        "kostenarten.html", {"request": request, "kostenarten": kostenarten}
    )


@app.post("/kostenarten/neu")
def kostenart_neu(
    bezeichnung: str = Form(...),
    umlagefaehig: str = Form(""),
    verteilmethode: str = Form("zeitanteilig"),
    hv_abgerechnet: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.Kostenart(
        bezeichnung=bezeichnung, umlagefaehig=bool(umlagefaehig),
        verteilmethode=verteilmethode, hv_abgerechnet=bool(hv_abgerechnet),
        notizen=notizen,
    ))
    db.commit()
    return RedirectResponse("/kostenarten", status_code=303)


@app.post("/kostenarten/{kostenart_id}/bearbeiten")
def kostenart_bearbeiten(
    kostenart_id: int,
    bezeichnung: str = Form(...),
    umlagefaehig: str = Form(""),
    verteilmethode: str = Form("zeitanteilig"),
    hv_abgerechnet: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    k = db.get(models.Kostenart, kostenart_id)
    if k:
        k.bezeichnung = bezeichnung
        k.umlagefaehig = bool(umlagefaehig)
        k.verteilmethode = verteilmethode
        k.hv_abgerechnet = bool(hv_abgerechnet)
        k.notizen = notizen
        db.commit()
    return RedirectResponse("/kostenarten#kostenarten-liste", status_code=303)


@app.post("/kostenarten/{kostenart_id}/loeschen")
def kostenart_loeschen(kostenart_id: int, db: Session = Depends(get_db)):
    k = db.get(models.Kostenart, kostenart_id)
    if k:
        db.delete(k)
        db.commit()
    return RedirectResponse("/kostenarten#kostenarten-liste", status_code=303)


# ------------------------------------------------------------- Jahresabr.
@app.get("/jahresabrechnungen")
def jahresabrechnungen_list(
    request: Request, objekt_id: str = "", jahr: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Jahresabrechnung).join(models.Objekt)
    if objekt_id:
        query = query.filter(models.Jahresabrechnung.objekt_id == int(objekt_id))
    alle = query.order_by(models.Objekt.bezeichnung, models.Jahresabrechnung.zeitraum_von).all()

    standard_jahr = date.today().year - 1
    if jahr is None:
        aktives_jahr = standard_jahr
    elif jahr == "":
        aktives_jahr = None
    else:
        aktives_jahr = int(jahr)

    # Datumsbezug ist das Abrechnungsende (zeitraum_bis), nicht das
    # gespeicherte Jahr-Feld - bei Wirtschaftsjahren weichen die voneinander ab.
    if aktives_jahr is not None:
        gefiltert = [ja for ja in alle if ja.zeitraum_bis.year == aktives_jahr]
    else:
        gefiltert = alle

    verfuegbare_jahre = sorted({ja.zeitraum_bis.year for ja in alle} | {standard_jahr}, reverse=True)
    objekte = db.query(models.Objekt).order_by(models.Objekt.bezeichnung).all()

    return templates.TemplateResponse(
        "jahresabrechnungen.html",
        {
            "request": request, "jahresabrechnungen": gefiltert, "objekte": objekte,
            "objekt_filter": objekt_id,
            "jahr_filter": "" if aktives_jahr is None else str(aktives_jahr),
            "verfuegbare_jahre": verfuegbare_jahre,
        },
    )


def _jahresabrechnung_anlegen(
    db: Session, objekt_id: int, jahr: int, erhalten_am: str, vorjahr_uebernehmen: str,
) -> models.Jahresabrechnung:
    """Legt eine neue Jahresabrechnung an und übernimmt optional Abrechnungs-
    positionen + Vorauszahlungen vom letzten Vorjahr als Startwert. Von beiden
    Anlegen-Routen (objektbezogen und von der Übersichtsseite aus) genutzt."""
    objekt = db.get(models.Objekt, objekt_id)
    von, bis = berechne_abrechnungszeitraum(objekt, jahr)
    neue_ja = models.Jahresabrechnung(
        objekt_id=objekt_id, jahr=jahr, zeitraum_von=von, zeitraum_bis=bis,
        erhalten_am=parse_date(erhalten_am),
    )
    db.add(neue_ja)
    db.flush()  # vergibt neue_ja.id, ohne die Transaktion schon abzuschliessen

    if vorjahr_uebernehmen:
        vorjahr_ja = (
            db.query(models.Jahresabrechnung)
            .filter(
                models.Jahresabrechnung.objekt_id == objekt_id,
                models.Jahresabrechnung.jahr < jahr,
            )
            .order_by(models.Jahresabrechnung.jahr.desc())
            .first()
        )
        if vorjahr_ja:
            # Abrechnungspositionen inkl. Beträgen als Startwert übernehmen -
            # der HV-Gesamtbetrag selbst bleibt bewusst leer, der ist pro
            # Jahr individuell und keine sinnvolle Vorbelegung.
            for pos in vorjahr_ja.positionen:
                db.add(models.Abrechnungsposition(
                    jahresabrechnung_id=neue_ja.id,
                    kostenart_id=pos.kostenart_id,
                    betrag=pos.betrag,
                    umlagefaehig=pos.umlagefaehig,
                    hv_abgerechnet=pos.hv_abgerechnet,
                    bemerkung=pos.bemerkung,
                ))
            # Vorauszahlungen übernehmen, aber nur für Mietverhältnisse, die
            # im neuen Abrechnungszeitraum ueberhaupt noch relevant sind
            # (kein Auszug bereits vor Beginn des neuen Zeitraums).
            for v in vorjahr_ja.vorauszahlungen:
                mv = v.mietverhaeltnis
                if mv.auszug is not None and mv.auszug < von:
                    continue
                db.add(models.Vorauszahlung(
                    jahresabrechnung_id=neue_ja.id,
                    mietverhaeltnis_id=v.mietverhaeltnis_id,
                    anzahl_monate=v.anzahl_monate,
                    betrag_pro_monat=v.betrag_pro_monat,
                    bemerkung=v.bemerkung,
                ))

    db.commit()
    return neue_ja


@app.post("/objekte/{objekt_id}/jahresabrechnungen/neu")
def jahresabrechnung_neu(
    objekt_id: int,
    jahr: int = Form(...),
    erhalten_am: str = Form(""),
    vorjahr_uebernehmen: str = Form(""),
    db: Session = Depends(get_db),
):
    _jahresabrechnung_anlegen(db, objekt_id, jahr, erhalten_am, vorjahr_uebernehmen)
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


@app.post("/jahresabrechnungen/neu")
def jahresabrechnung_neu_uebersicht(
    objekt_id: int = Form(...),
    jahr: int = Form(...),
    erhalten_am: str = Form(""),
    vorjahr_uebernehmen: str = Form(""),
    db: Session = Depends(get_db),
):
    """Wie jahresabrechnung_neu, aber für den Button auf der Übersichtsseite
    /jahresabrechnungen, wo das Objekt erst per Auswahlfeld bestimmt wird."""
    neue_ja = _jahresabrechnung_anlegen(db, objekt_id, jahr, erhalten_am, vorjahr_uebernehmen)
    return RedirectResponse(f"/jahresabrechnungen/{neue_ja.id}", status_code=303)


def _ja_gesperrt_redirect(ja, anchor: str = "") -> Optional[RedirectResponse]:
    """Blockiert aendernde Aktionen auf einer gegen unabsichtliche Aenderungen
    gesperrten Jahresabrechnung. Gibt bei Sperre eine RedirectResponse mit
    Fehlermeldung zurueck, sonst None (Aufrufer soll dann normal fortfahren)."""
    if ja is not None and ja.gesperrt:
        nachricht = (
            "Diese Jahresabrechnung ist gegen unabsichtliche Änderungen gesperrt. "
            "Bitte zuerst oben entsperren."
        )
        ziel = f"/jahresabrechnungen/{ja.id}?fehler={quote(nachricht)}"
        if anchor:
            ziel += f"#{anchor}"
        return RedirectResponse(ziel, status_code=303)
    return None


@app.post("/jahresabrechnungen/{ja_id}/sperren")
def jahresabrechnung_sperren(ja_id: int, ziel: str = Form(""), db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    if ja:
        ja.gesperrt = not ja.gesperrt
        db.commit()
        if ziel == "objekt":
            return RedirectResponse(f"/objekte/{ja.objekt_id}#jahresabrechnungen", status_code=303)
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/loeschen")
def jahresabrechnung_loeschen(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    objekt_id = ja.objekt_id if ja else None
    gesperrt_redirect = _ja_gesperrt_redirect(ja)
    if gesperrt_redirect:
        return gesperrt_redirect
    if ja:
        db.delete(ja)
        db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


@app.get("/jahresabrechnungen/{ja_id}")
def jahresabrechnung_detail(request: Request, ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    kostenarten = db.query(models.Kostenart).order_by(models.Kostenart.bezeichnung).all()
    beleg = calc.analysiere_belegung(ja)
    pruefung = calc.pruefe_summen(ja)
    wasser_warnungen = calc.wasser_warnungen(ja)
    personentage_warnungen = calc.personentage_warnungen(ja)
    return templates.TemplateResponse(
        "jahresabrechnung_detail.html",
        {
            "request": request, "ja": ja, "kostenarten": kostenarten, "beleg": beleg,
            "gradtagszahlen": calc.GRADTAGSZAHL_MONAT, "pruefung": pruefung,
            "wasser_warnungen": wasser_warnungen,
            "personentage_warnungen": personentage_warnungen,
            "fehler": request.query_params.get("fehler"),
        },
    )


@app.post("/jahresabrechnungen/{ja_id}/hv_gesamtbetrag")
def jahresabrechnung_hv_gesamtbetrag(
    ja_id: int, hv_gesamtbetrag: str = Form(""), db: Session = Depends(get_db),
):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "pruefung")
    if gesperrt_redirect:
        return gesperrt_redirect
    if ja:
        wert = hv_gesamtbetrag.strip().replace(",", ".")
        ja.hv_gesamtbetrag = float(wert) if wert else None
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#pruefung", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/positionen/neu")
def position_neu(
    ja_id: int,
    kostenart_id: int = Form(...),
    betrag: str = Form(...),
    umlagefaehig: str = Form(""),
    hv_abgerechnet: str = Form(""),
    bemerkung: str = Form(""),
    db: Session = Depends(get_db),
):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "abrechnungspositionen")
    if gesperrt_redirect:
        return gesperrt_redirect
    kostenart = db.get(models.Kostenart, kostenart_id)
    db.add(models.Abrechnungsposition(
        jahresabrechnung_id=ja_id, kostenart_id=kostenart_id,
        betrag=float(betrag.replace(",", ".")),
        umlagefaehig=bool(umlagefaehig) if umlagefaehig else kostenart.umlagefaehig,
        hv_abgerechnet=parse_hv_override(hv_abgerechnet),
        bemerkung=bemerkung,
    ))
    db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#abrechnungspositionen", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/positionen/{pos_id}/bearbeiten")
def position_bearbeiten(
    ja_id: int,
    pos_id: int,
    kostenart_id: int = Form(...),
    betrag: str = Form(...),
    umlagefaehig: str = Form(""),
    hv_abgerechnet: str = Form(""),
    bemerkung: str = Form(""),
    db: Session = Depends(get_db),
):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "abrechnungspositionen")
    if gesperrt_redirect:
        return gesperrt_redirect
    pos = db.get(models.Abrechnungsposition, pos_id)
    if pos:
        kostenart = db.get(models.Kostenart, kostenart_id)
        pos.kostenart_id = kostenart_id
        pos.betrag = float(betrag.replace(",", "."))
        pos.umlagefaehig = bool(umlagefaehig) if umlagefaehig else kostenart.umlagefaehig
        pos.hv_abgerechnet = parse_hv_override(hv_abgerechnet)
        pos.bemerkung = bemerkung
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#abrechnungspositionen", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/positionen/{pos_id}/loeschen")
def position_loeschen(ja_id: int, pos_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "abrechnungspositionen")
    if gesperrt_redirect:
        return gesperrt_redirect
    pos = db.get(models.Abrechnungsposition, pos_id)
    if pos:
        db.delete(pos)
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#abrechnungspositionen", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/berechnen")
def jahresabrechnung_berechnen(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "berechnung")
    if gesperrt_redirect:
        return gesperrt_redirect
    calc.berechne_jahresabrechnung(db, ja)
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#berechnung", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/leeren")
def jahresabrechnung_leeren(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "berechnung")
    if gesperrt_redirect:
        return gesperrt_redirect
    if ja:
        for alt in list(ja.mieterabrechnungen):
            db.delete(alt)
        ja.status = "erfasst"
        ja.berechnet_am = None
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#berechnung", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/vorauszahlungen/neu")
def vorauszahlung_neu(
    ja_id: int,
    mietverhaeltnis_id: int = Form(...),
    anzahl_monate: str = Form(...),
    betrag_pro_monat: str = Form(...),
    bemerkung: str = Form(""),
    db: Session = Depends(get_db),
):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "vorauszahlungen")
    if gesperrt_redirect:
        return gesperrt_redirect
    db.add(models.Vorauszahlung(
        jahresabrechnung_id=ja_id, mietverhaeltnis_id=mietverhaeltnis_id,
        anzahl_monate=int(anzahl_monate),
        betrag_pro_monat=float(betrag_pro_monat.replace(",", ".")),
        bemerkung=bemerkung,
    ))
    db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#vorauszahlungen", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/vorauszahlungen/{vz_id}/bearbeiten")
def vorauszahlung_bearbeiten(
    ja_id: int,
    vz_id: int,
    mietverhaeltnis_id: int = Form(...),
    anzahl_monate: str = Form(...),
    betrag_pro_monat: str = Form(...),
    bemerkung: str = Form(""),
    db: Session = Depends(get_db),
):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "vorauszahlungen")
    if gesperrt_redirect:
        return gesperrt_redirect
    vz = db.get(models.Vorauszahlung, vz_id)
    if vz:
        vz.mietverhaeltnis_id = mietverhaeltnis_id
        vz.anzahl_monate = int(anzahl_monate)
        vz.betrag_pro_monat = float(betrag_pro_monat.replace(",", "."))
        vz.bemerkung = bemerkung
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#vorauszahlungen", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/vorauszahlungen/{vz_id}/loeschen")
def vorauszahlung_loeschen(ja_id: int, vz_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    gesperrt_redirect = _ja_gesperrt_redirect(ja, "vorauszahlungen")
    if gesperrt_redirect:
        return gesperrt_redirect
    vz = db.get(models.Vorauszahlung, vz_id)
    if vz:
        db.delete(vz)
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#vorauszahlungen", status_code=303)


# ------------------------------------------------------------------ Export
GENERIC_EXPORTS = {
    "kontakt": models.Kontakt,
    "kontakt_adresse": models.KontaktAdresse,
    "objekt": models.Objekt,
    "mietverhaeltnis": models.Mietverhaeltnis,
    "mietverhaeltnis_kontakt": models.MietverhaeltnisKontakt,
    "kostenart": models.Kostenart,
    "jahresabrechnung": models.Jahresabrechnung,
    "abrechnungsposition": models.Abrechnungsposition,
    "mieterabrechnung": models.Mieterabrechnung,
    "mieterabrechnungsposition": models.Mieterabrechnungsposition,
    "aenderung_historie": models.AenderungHistorie,
    "leerstand": models.Leerstand,
    "vorauszahlung": models.Vorauszahlung,
    "zaehler": models.Zaehler,
    "zaehlerstand": models.Zaehlerstand,
}

EXPORT_LABELS = {
    "kontakt": "Kontakte (Adressbuch)",
    "kontakt_adresse": "Kontakt-Adressen",
    "objekt": "Objekte",
    "mietverhaeltnis": "Mietverhältnisse",
    "mietverhaeltnis_kontakt": "Mietverhältnis-Kontakt-Verknüpfungen",
    "kostenart": "Kostenarten",
    "jahresabrechnung": "Jahresabrechnungen",
    "abrechnungsposition": "Abrechnungspositionen",
    "mieterabrechnung": "Mieterabrechnungen",
    "mieterabrechnungsposition": "Mieterabrechnungspositionen",
    "aenderung_historie": "Änderungshistorie",
    "leerstand": "Leerstand",
    "vorauszahlung": "Vorauszahlungen",
    "zaehler": "Zähler",
    "zaehlerstand": "Zählerstände",
}


@app.get("/export")
def export_uebersicht(request: Request):
    tabellen = [(key, EXPORT_LABELS.get(key, key)) for key in GENERIC_EXPORTS]
    return templates.TemplateResponse(
        "export.html", {"request": request, "tabellen": tabellen}
    )


@app.get("/export/{tabelle}.csv")
def export_tabelle(tabelle: str, db: Session = Depends(get_db)):
    model = GENERIC_EXPORTS.get(tabelle)
    if not model:
        return RedirectResponse("/", status_code=303)
    rows = db.query(model).all()
    columns = [c.name for c in model.__table__.columns]

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([getattr(row, c) for c in columns])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={tabelle}.csv"},
    )


@app.get("/jahresabrechnungen/{ja_id}/export.csv")
def export_ergebnis(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "Objekt", "Jahr", "Mieter", "Einzug", "Auszug", "Tage",
        "Kostenart", "Anteil_Betrag", "Methode", "Detail",
    ])
    for ma in sorted(ja.mieterabrechnungen, key=lambda m: m.sortier_datum):
        name = ma.titel
        for pos in ma.positionen:
            writer.writerow([
                ja.objekt.bezeichnung, ja.jahr, name,
                ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
                pos.kostenart.bezeichnung, pos.anteil_betrag,
                pos.berechnungsmethode, pos.berechnungsdetail,
            ])
        if ma.ist_leerstand:
            writer.writerow([
                ja.objekt.bezeichnung, ja.jahr, name,
                ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
                "Kosten beim Eigentümer (Leerstand)", ma.summe_umlagefaehig, "", "",
            ])
            continue
        writer.writerow([
            ja.objekt.bezeichnung, ja.jahr, name,
            ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
            "Summe umlagefähige Kosten", ma.summe_umlagefaehig, "", "",
        ])
        if ma.mietverhaeltnis.umsatzsteuerpflichtig:
            writer.writerow([
                ja.objekt.bezeichnung, ja.jahr, name,
                ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
                "MwSt.", ma.mwst_betrag, "", "",
            ])
            writer.writerow([
                ja.objekt.bezeichnung, ja.jahr, name,
                ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
                "Gesamtbetrag", ma.gesamtbetrag, "", "",
            ])
        writer.writerow([
            ja.objekt.bezeichnung, ja.jahr, name,
            ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
            "Summe Vorauszahlungen", ma.summe_vorauszahlung, "", "",
        ])
        writer.writerow([
            ja.objekt.bezeichnung, ja.jahr, name,
            ma.anzeige_von, ma.anzeige_bis or "", ma.tage_im_zeitraum,
            "Nachzahlung" if ma.differenz >= 0 else "Guthaben",
            abs(ma.differenz), "", "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=abrechnung_{ja.objekt.bezeichnung}_{ja.jahr}.csv"
        },
    )


@app.get("/jahresabrechnungen/{ja_id}/mieterabrechnungen/{ma_id}/pdf")
def export_mieter_pdf(ja_id: int, ma_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    ma = db.get(models.Mieterabrechnung, ma_id)
    if not ja or not ma or ma.jahresabrechnung_id != ja_id or ma.ist_leerstand:
        return RedirectResponse(f"/jahresabrechnungen/{ja_id}", status_code=303)
    inhalt = pdf_export.erzeuge_mieter_pdf(ja, ma)
    dateiname = f"NK_{ja.jahr}_{ma.mietverhaeltnis.anzeige_name}.pdf".replace(" ", "_")
    return StreamingResponse(
        iter([inhalt]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={dateiname}"},
    )


@app.get("/jahresabrechnungen/{ja_id}/pdf")
def export_sammel_pdf(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    if not ja:
        return RedirectResponse("/", status_code=303)
    inhalt = pdf_export.erzeuge_sammel_pdf(ja)
    dateiname = f"NK_{ja.objekt.bezeichnung}_{ja.jahr}_alle.pdf".replace(" ", "_")
    return StreamingResponse(
        iter([inhalt]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={dateiname}"},
    )
