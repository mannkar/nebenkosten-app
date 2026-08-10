from __future__ import annotations

import csv
import io
import os
from datetime import datetime, date, timedelta
from typing import Optional

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


# --------------------------------------------------------------- Verwaltung
@app.get("/verwaltungen")
def verwaltungen_list(request: Request, db: Session = Depends(get_db)):
    verwaltungen = db.query(models.Verwaltung).order_by(models.Verwaltung.name).all()
    return templates.TemplateResponse(
        "verwaltungen.html", {"request": request, "verwaltungen": verwaltungen}
    )


@app.post("/verwaltungen/neu")
def verwaltung_neu(
    name: str = Form(...),
    ansprechpartner_anrede: str = Form(""),
    ansprechpartner_name: str = Form(""),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    telefon: str = Form(""),
    email: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.Verwaltung(
        name=name, ansprechpartner_anrede=ansprechpartner_anrede,
        ansprechpartner_name=ansprechpartner_name, strasse=strasse, plz=plz,
        ort=ort, telefon=telefon, email=email, notizen=notizen,
    ))
    db.commit()
    return RedirectResponse("/verwaltungen", status_code=303)


@app.get("/verwaltungen/{verwaltung_id}")
def verwaltung_detail(request: Request, verwaltung_id: int, db: Session = Depends(get_db)):
    v = db.get(models.Verwaltung, verwaltung_id)
    historie = (
        db.query(models.AenderungHistorie)
        .filter_by(entity_typ="Verwaltung", entity_id=verwaltung_id)
        .order_by(models.AenderungHistorie.geaendert_am.desc())
        .all()
    )
    return templates.TemplateResponse(
        "verwaltung_detail.html", {"request": request, "v": v, "historie": historie}
    )


@app.post("/verwaltungen/{verwaltung_id}/bearbeiten")
def verwaltung_bearbeiten(
    verwaltung_id: int,
    name: str = Form(...),
    ansprechpartner_anrede: str = Form(""),
    ansprechpartner_name: str = Form(""),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    telefon: str = Form(""),
    email: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    v = db.get(models.Verwaltung, verwaltung_id)
    neu = {
        "name": name, "ansprechpartner_anrede": ansprechpartner_anrede,
        "ansprechpartner_name": ansprechpartner_name, "strasse": strasse,
        "plz": plz, "ort": ort, "telefon": telefon, "email": email,
        "notizen": notizen,
    }
    alt = {feld: getattr(v, feld) for feld in neu}
    log_aenderungen(db, "Verwaltung", verwaltung_id, alt, neu)
    for feld, wert in neu.items():
        setattr(v, feld, wert)
    db.commit()
    return RedirectResponse(f"/verwaltungen/{verwaltung_id}", status_code=303)


@app.post("/verwaltungen/{verwaltung_id}/loeschen")
def verwaltung_loeschen(verwaltung_id: int, db: Session = Depends(get_db)):
    v = db.get(models.Verwaltung, verwaltung_id)
    if v:
        db.delete(v)
        db.commit()
    return RedirectResponse("/verwaltungen", status_code=303)


# -------------------------------------------------------------------- Objekt
@app.get("/objekte")
def objekte_list(request: Request, db: Session = Depends(get_db)):
    objekte = db.query(models.Objekt).order_by(models.Objekt.bezeichnung).all()
    verwaltungen = db.query(models.Verwaltung).order_by(models.Verwaltung.name).all()
    return templates.TemplateResponse(
        "objekte.html",
        {"request": request, "objekte": objekte, "verwaltungen": verwaltungen, "monate": MONATE},
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
    hausgeld_monatlich: str = Form(""),
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
        hausgeld_monatlich=float(hausgeld_monatlich.replace(",", ".")) if hausgeld_monatlich else None,
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
    verwaltungen = db.query(models.Verwaltung).order_by(models.Verwaltung.name).all()
    neu = bool(request.query_params.get("neu"))
    return templates.TemplateResponse(
        "objekt_detail.html",
        {"request": request, "objekt": objekt, "kostenarten": kostenarten,
         "verwaltungen": verwaltungen, "monate": MONATE, "neu": neu},
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
    hausgeld_monatlich: str = Form(""),
    verwaltung_id: str = Form(""),
    abrechnung_start_monat: str = Form("1"),
    notizen: str = Form(""),
    absender_name: str = Form(""),
    absender_strasse: str = Form(""),
    absender_plz: str = Form(""),
    absender_ort: str = Form(""),
    absender_telefon: str = Form(""),
    absender_email: str = Form(""),
    bank_name: str = Form(""),
    bank_iban: str = Form(""),
    bank_bic: str = Form(""),
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
    o.hausgeld_monatlich = float(hausgeld_monatlich.replace(",", ".")) if hausgeld_monatlich else None
    o.verwaltung_id = int(verwaltung_id) if verwaltung_id else None
    o.abrechnung_start_monat = int(abrechnung_start_monat) if abrechnung_start_monat else 1
    o.notizen = notizen
    o.absender_name = absender_name
    o.absender_strasse = absender_strasse
    o.absender_plz = absender_plz
    o.absender_ort = absender_ort
    o.absender_telefon = absender_telefon
    o.absender_email = absender_email
    o.bank_name = bank_name
    o.bank_iban = bank_iban
    o.bank_bic = bank_bic
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
    "anrede", "vorname", "nachname", "email", "telefon",
    "adresse_vor_strasse", "adresse_vor_plz", "adresse_vor_ort",
    "adresse_nach_strasse", "adresse_nach_plz", "adresse_nach_ort",
]


def _personen_aus_form(form) -> list[dict]:
    """Liest die Personen-Felder aus dem rohen Formular (mehrere Werte pro
    Feldname, ein Wert pro Person in DOM-Reihenfolge) und baut daraus eine
    Liste von Personen-Dicts. Zeilen ohne Vor- und Nachname werden
    uebersprungen (z.B. eine per JS hinzugefuegte, aber leer gelassene
    Person-Zeile)."""
    listen = {feld: form.getlist(f"person_{feld}") for feld in PERSON_FELDER}
    anzahl = len(listen["vorname"])
    personen = []
    for i in range(anzahl):
        vorname = listen["vorname"][i].strip()
        nachname = listen["nachname"][i].strip()
        if not vorname and not nachname:
            continue
        person = {
            feld: (listen[feld][i] or "").strip() or None
            for feld in PERSON_FELDER
        }
        # vorname/nachname sind NOT NULL in der DB - leeres Feld als "" statt None
        person["vorname"] = person["vorname"] or ""
        person["nachname"] = person["nachname"] or ""
        personen.append(person)
    return personen


@app.get("/objekte/{objekt_id}/mietverhaeltnisse/neu")
def mietverhaeltnis_neu_form(request: Request, objekt_id: int, db: Session = Depends(get_db)):
    objekt = db.get(models.Objekt, objekt_id)
    laufendes_mv = (
        db.query(models.Mietverhaeltnis)
        .filter(models.Mietverhaeltnis.objekt_id == objekt_id,
                models.Mietverhaeltnis.auszug.is_(None))
        .first()
    )
    return templates.TemplateResponse(
        "mietverhaeltnis_form.html",
        {"request": request, "objekt": objekt, "mv": None, "laufendes_mv": laufendes_mv},
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
        notizen=notizen,
    )
    db.add(mv)
    db.flush()
    for p in _personen_aus_form(form):
        db.add(models.Person(mietverhaeltnis_id=mv.id, **p))

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
    return templates.TemplateResponse(
        "mietverhaeltnis_form.html",
        {"request": request, "objekt": mv.objekt, "mv": mv, "laufendes_mv": None},
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
    mv.notizen = notizen
    for alte_person in list(mv.personen):
        db.delete(alte_person)
    db.flush()
    for p in _personen_aus_form(form):
        db.add(models.Person(mietverhaeltnis_id=mv.id, **p))
    db.commit()
    return RedirectResponse(f"/objekte/{mv.objekt_id}", status_code=303)


@app.post("/mietverhaeltnisse/{mv_id}/loeschen")
def mietverhaeltnis_loeschen(mv_id: int, db: Session = Depends(get_db)):
    mv = db.get(models.Mietverhaeltnis, mv_id)
    objekt_id = mv.objekt_id if mv else None
    if mv:
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


@app.post("/jahresabrechnungen/{ja_id}/loeschen")
def jahresabrechnung_loeschen(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    objekt_id = ja.objekt_id if ja else None
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
        },
    )


@app.post("/jahresabrechnungen/{ja_id}/hv_gesamtbetrag")
def jahresabrechnung_hv_gesamtbetrag(
    ja_id: int, hv_gesamtbetrag: str = Form(""), db: Session = Depends(get_db),
):
    ja = db.get(models.Jahresabrechnung, ja_id)
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
    pos = db.get(models.Abrechnungsposition, pos_id)
    if pos:
        db.delete(pos)
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#abrechnungspositionen", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/berechnen")
def jahresabrechnung_berechnen(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    calc.berechne_jahresabrechnung(db, ja)
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#berechnung", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/leeren")
def jahresabrechnung_leeren(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
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
    vz = db.get(models.Vorauszahlung, vz_id)
    if vz:
        db.delete(vz)
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}#vorauszahlungen", status_code=303)


# ------------------------------------------------------------------ Export
GENERIC_EXPORTS = {
    "verwaltung": models.Verwaltung,
    "objekt": models.Objekt,
    "mietverhaeltnis": models.Mietverhaeltnis,
    "person": models.Person,
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
    "verwaltung": "Verwaltungen",
    "objekt": "Objekte",
    "mietverhaeltnis": "Mietverhältnisse",
    "person": "Personen",
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
