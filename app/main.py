import csv
import io
from datetime import datetime, date

from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import models, calc, seed
from .database import Base, engine, get_db

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Nebenkostenabrechnung")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def startup():
    db = next(get_db())
    seed.seed_kostenarten(db)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


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
        {"request": request, "objekte": objekte, "verwaltungen": verwaltungen},
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
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.Objekt(
        bezeichnung=bezeichnung, strasse=strasse, plz=plz, ort=ort,
        flurstueck=flurstueck,
        wohnflaeche_qm=float(wohnflaeche_qm) if wohnflaeche_qm else None,
        miteigentumsanteil=miteigentumsanteil,
        hausgeld_monatlich=float(hausgeld_monatlich) if hausgeld_monatlich else None,
        verwaltung_id=int(verwaltung_id) if verwaltung_id else None,
        notizen=notizen,
    ))
    db.commit()
    return RedirectResponse("/objekte", status_code=303)


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
    return templates.TemplateResponse(
        "objekt_detail.html",
        {"request": request, "objekt": objekt, "kostenarten": kostenarten,
         "verwaltungen": verwaltungen},
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
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    o = db.get(models.Objekt, objekt_id)
    o.bezeichnung = bezeichnung
    o.strasse = strasse
    o.plz = plz
    o.ort = ort
    o.flurstueck = flurstueck
    o.wohnflaeche_qm = float(wohnflaeche_qm) if wohnflaeche_qm else None
    o.miteigentumsanteil = miteigentumsanteil
    o.hausgeld_monatlich = float(hausgeld_monatlich) if hausgeld_monatlich else None
    o.verwaltung_id = int(verwaltung_id) if verwaltung_id else None
    o.notizen = notizen
    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


# ------------------------------------------------------------ Mietverhaeltnis
@app.post("/objekte/{objekt_id}/mietverhaeltnisse/neu")
def mietverhaeltnis_neu(
    objekt_id: int,
    mietername: str = Form(...),
    einzug: str = Form(...),
    auszug: str = Form(""),
    kontakt: str = Form(""),
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.Mietverhaeltnis(
        objekt_id=objekt_id, mietername=mietername,
        einzug=parse_date(einzug), auszug=parse_date(auszug),
        kontakt=kontakt, notizen=notizen,
    ))
    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


@app.post("/mietverhaeltnisse/{mv_id}/loeschen")
def mietverhaeltnis_loeschen(mv_id: int, db: Session = Depends(get_db)):
    mv = db.get(models.Mietverhaeltnis, mv_id)
    objekt_id = mv.objekt_id if mv else None
    if mv:
        db.delete(mv)
        db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


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
    notizen: str = Form(""),
    db: Session = Depends(get_db),
):
    db.add(models.Kostenart(
        bezeichnung=bezeichnung, umlagefaehig=bool(umlagefaehig),
        verteilmethode=verteilmethode, notizen=notizen,
    ))
    db.commit()
    return RedirectResponse("/kostenarten", status_code=303)


@app.post("/kostenarten/{kostenart_id}/loeschen")
def kostenart_loeschen(kostenart_id: int, db: Session = Depends(get_db)):
    k = db.get(models.Kostenart, kostenart_id)
    if k:
        db.delete(k)
        db.commit()
    return RedirectResponse("/kostenarten", status_code=303)


# ------------------------------------------------------------- Jahresabr.
@app.post("/objekte/{objekt_id}/jahresabrechnungen/neu")
def jahresabrechnung_neu(
    objekt_id: int,
    jahr: int = Form(...),
    zeitraum_von: str = Form(""),
    zeitraum_bis: str = Form(""),
    erhalten_am: str = Form(""),
    db: Session = Depends(get_db),
):
    von = parse_date(zeitraum_von) or date(jahr, 1, 1)
    bis = parse_date(zeitraum_bis) or date(jahr, 12, 31)
    db.add(models.Jahresabrechnung(
        objekt_id=objekt_id, jahr=jahr, zeitraum_von=von, zeitraum_bis=bis,
        erhalten_am=parse_date(erhalten_am),
    ))
    db.commit()
    return RedirectResponse(f"/objekte/{objekt_id}", status_code=303)


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
    return templates.TemplateResponse(
        "jahresabrechnung_detail.html",
        {"request": request, "ja": ja, "kostenarten": kostenarten, "beleg": beleg},
    )


@app.post("/jahresabrechnungen/{ja_id}/positionen/neu")
def position_neu(
    ja_id: int,
    kostenart_id: int = Form(...),
    betrag: str = Form(...),
    umlagefaehig: str = Form(""),
    bemerkung: str = Form(""),
    db: Session = Depends(get_db),
):
    kostenart = db.get(models.Kostenart, kostenart_id)
    db.add(models.Abrechnungsposition(
        jahresabrechnung_id=ja_id, kostenart_id=kostenart_id,
        betrag=float(betrag.replace(",", ".")),
        umlagefaehig=bool(umlagefaehig) if umlagefaehig else kostenart.umlagefaehig,
        bemerkung=bemerkung,
    ))
    db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/positionen/{pos_id}/loeschen")
def position_loeschen(ja_id: int, pos_id: int, db: Session = Depends(get_db)):
    pos = db.get(models.Abrechnungsposition, pos_id)
    if pos:
        db.delete(pos)
        db.commit()
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}", status_code=303)


@app.post("/jahresabrechnungen/{ja_id}/berechnen")
def jahresabrechnung_berechnen(ja_id: int, db: Session = Depends(get_db)):
    ja = db.get(models.Jahresabrechnung, ja_id)
    calc.berechne_jahresabrechnung(db, ja)
    return RedirectResponse(f"/jahresabrechnungen/{ja_id}", status_code=303)


# ------------------------------------------------------------------ Export
GENERIC_EXPORTS = {
    "verwaltung": models.Verwaltung,
    "objekt": models.Objekt,
    "mietverhaeltnis": models.Mietverhaeltnis,
    "kostenart": models.Kostenart,
    "jahresabrechnung": models.Jahresabrechnung,
    "abrechnungsposition": models.Abrechnungsposition,
    "mieterabrechnung": models.Mieterabrechnung,
    "mieterabrechnungsposition": models.Mieterabrechnungsposition,
    "aenderung_historie": models.AenderungHistorie,
}


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
    for ma in ja.mieterabrechnungen:
        mv = ma.mietverhaeltnis
        for pos in ma.positionen:
            writer.writerow([
                ja.objekt.bezeichnung, ja.jahr, mv.mietername,
                mv.einzug, mv.auszug or "", ma.tage_im_zeitraum,
                pos.kostenart.bezeichnung, pos.anteil_betrag,
                pos.berechnungsmethode, pos.berechnungsdetail,
            ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=abrechnung_{ja.objekt.bezeichnung}_{ja.jahr}.csv"
        },
    )
