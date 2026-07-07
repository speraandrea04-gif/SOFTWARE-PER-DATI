"""Interfaccia web (Fase 1): upload dei due file e tabella dei risultati.

Avvio:
    uvicorn riconciliazione.web:app --reload
poi aprire http://127.0.0.1:8000

Nessuna persistenza: i risultati vivono in memoria (ultimi RISULTATI_MASSIMI)
solo per consentire il download dell'Excel dopo l'elaborazione.
"""

from __future__ import annotations

import io
import tempfile
import uuid
from argparse import ArgumentTypeError
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from .cli import _parse_mappa_colonne
from .export import aggrega_lato, esporta_excel
from .fatturapa import ESTENSIONI_FATTURA, carica_fatture_xml
from .report import genera_pdf
from .matching import (
    Abbinamento,
    Categoria,
    ConfigMatching,
    RisultatoRiconciliazione,
    riconcilia,
)
from .parsing import ErroreParsing, FileNormalizzato, carica_file

app = FastAPI(title="Riconciliazione Dati — MVP")

RISULTATI_MASSIMI = 20
_risultati: OrderedDict[str, RisultatoRiconciliazione] = OrderedDict()

_PAGINA = Path(__file__).parent / "static" / "index.html"


@app.get("/", response_class=HTMLResponse)
def pagina_principale() -> str:
    return _PAGINA.read_text(encoding="utf-8")


async def _scrivi_temporaneo(upload: UploadFile, cartella: str) -> Path:
    nome = Path(upload.filename).name  # niente path traversal
    contenuto = await upload.read()
    if not contenuto:
        raise ErroreParsing(f"Il file '{nome}' è vuoto.")
    destinazione = Path(cartella) / nome
    destinazione.write_bytes(contenuto)
    return destinazione


async def _carica_lato(uploads: list[UploadFile], mappa_testo: str) -> FileNormalizzato:
    """Carica un lato della riconciliazione da uno o più file caricati.

    Un solo file: CSV/Excel oppure fatture XML (.xml/.p7m/.zip).
    Più file: solo fatture elettroniche (.xml/.p7m).
    """
    nomi = [u.filename or "" for u in uploads]
    if not uploads or any(not n for n in nomi):
        raise ErroreParsing("Nessun file selezionato.")
    try:
        mappa = _parse_mappa_colonne(mappa_testo or None)
    except ArgumentTypeError as errore:
        raise ErroreParsing(f"Mappatura colonne per '{nomi[0]}' non valida: {errore}")

    suffissi = {Path(n).suffix.lower() for n in nomi}
    xml_o_zip = suffissi <= (ESTENSIONI_FATTURA | {".zip"})
    if len(uploads) > 1 and not xml_o_zip:
        raise ErroreParsing(
            "Si possono caricare più file insieme solo per le fatture "
            "elettroniche (.xml/.p7m). Per CSV ed Excel caricare un file per lato."
        )
    if len(uploads) > 1 and ".zip" in suffissi:
        raise ErroreParsing("Caricare l'archivio .zip da solo, senza altri file.")
    if xml_o_zip and mappa:
        raise ErroreParsing(
            "La mappatura manuale delle colonne non si applica alle fatture XML."
        )

    with tempfile.TemporaryDirectory() as cartella:
        percorsi = [await _scrivi_temporaneo(upload, cartella) for upload in uploads]
        if xml_o_zip:
            origine = percorsi[0] if len(percorsi) == 1 else Path(cartella)
            file = carica_fatture_xml(origine)
            file.percorso = (nomi[0] if len(nomi) == 1
                             else f"{len(nomi)} fatture elettroniche")
        else:
            file = carica_file(percorsi[0], mappa)
            file.percorso = nomi[0]
    return file


def _lato_json(movimenti) -> dict | None:
    if not movimenti:
        return None
    return aggrega_lato(movimenti)


def _riga_json(abbinamento: Abbinamento) -> dict:
    con_match = bool(abbinamento.movimenti_a and abbinamento.movimenti_b)
    return {
        "categoria": abbinamento.categoria.value,
        "a": _lato_json(abbinamento.movimenti_a),
        "b": _lato_json(abbinamento.movimenti_b),
        "differenza_importo": float(abbinamento.differenza_importo)
                              if abbinamento.differenza_importo is not None else None,
        "differenza_giorni": abbinamento.differenza_giorni,
        "confidenza": abbinamento.confidenza if con_match else None,
        "dettagli": abbinamento.dettagli,
    }


def _file_json(file: FileNormalizzato) -> dict:
    return {
        "nome": file.percorso,
        "movimenti": len(file.movimenti),
        "colonne": file.mappa_colonne,
        "avvisi": file.avvisi,
    }


@app.post("/api/riconcilia")
async def api_riconcilia(
    file_a: list[UploadFile] = File(...),
    file_b: list[UploadFile] = File(...),
    tolleranza_importo: str = Form("0.01"),
    tolleranza_giorni: int = Form(3),
    valore_assoluto: bool = Form(False),
    cerca_gruppi: bool = Form(True),
    colonne_a: str = Form(""),
    colonne_b: str = Form(""),
) -> dict:
    try:
        tolleranza = Decimal(tolleranza_importo.replace(",", "."))
        if tolleranza < 0:
            raise InvalidOperation
    except InvalidOperation:
        raise HTTPException(400, "Tolleranza importo non valida: usare un numero ≥ 0.")
    if tolleranza_giorni < 0:
        raise HTTPException(400, "La tolleranza in giorni non può essere negativa.")

    try:
        norm_a = await _carica_lato(file_a, colonne_a)
        norm_b = await _carica_lato(file_b, colonne_b)
    except ErroreParsing as errore:
        raise HTTPException(400, str(errore))

    config = ConfigMatching(
        tolleranza_importo=tolleranza,
        tolleranza_giorni=tolleranza_giorni,
        valore_assoluto=valore_assoluto,
        cerca_gruppi=cerca_gruppi,
    )
    risultato = riconcilia(norm_a, norm_b, config)

    identificativo = uuid.uuid4().hex
    _risultati[identificativo] = risultato
    while len(_risultati) > RISULTATI_MASSIMI:
        _risultati.popitem(last=False)

    non_trovati = risultato.per_categoria(Categoria.NON_TROVATO)
    solo_in_a = sum(1 for ab in non_trovati if ab.lato_mancante == "B")
    return {
        "id": identificativo,
        "file_a": _file_json(norm_a),
        "file_b": _file_json(norm_b),
        "conteggi": risultato.conteggi,
        "solo_in_a": solo_in_a,
        "solo_in_b": len(non_trovati) - solo_in_a,
        "righe": [_riga_json(ab) for ab in risultato.abbinamenti],
    }


def _risultato_o_404(identificativo: str) -> RisultatoRiconciliazione:
    risultato = _risultati.get(identificativo)
    if risultato is None:
        raise HTTPException(404, "Risultato non trovato o scaduto: ripetere la riconciliazione.")
    return risultato


@app.get("/api/risultati/{identificativo}/excel")
def api_excel(identificativo: str) -> StreamingResponse:
    risultato = _risultato_o_404(identificativo)
    buffer = io.BytesIO()
    esporta_excel(risultato, buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="riconciliazione.xlsx"'},
    )


@app.get("/api/risultati/{identificativo}/pdf")
def api_pdf(identificativo: str) -> StreamingResponse:
    risultato = _risultato_o_404(identificativo)
    buffer = io.BytesIO()
    genera_pdf(risultato, buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="report_riconciliazione.pdf"'},
    )
