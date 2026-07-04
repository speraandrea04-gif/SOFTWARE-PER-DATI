"""Interfaccia web (Fase 1): upload dei due file e tabella dei risultati.

Avvio:
    uvicorn riconciliazione.web:app --reload
poi aprire http://127.0.0.1:8000

Nessuna persistenza: i risultati vivono in memoria (ultimi RISULTATI_MASSIMI)
solo per consentire il download dell'Excel dopo l'elaborazione.
"""

from __future__ import annotations

import io
import os
import tempfile
import uuid
from argparse import ArgumentTypeError
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from .cli import _parse_mappa_colonne
from .export import esporta_excel
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


async def _carica_upload(upload: UploadFile, mappa_testo: str) -> FileNormalizzato:
    nome = upload.filename or ""
    if not nome:
        raise ErroreParsing("Nessun file selezionato.")
    try:
        mappa = _parse_mappa_colonne(mappa_testo or None)
    except ArgumentTypeError as errore:
        raise ErroreParsing(f"Mappatura colonne per '{nome}' non valida: {errore}")

    suffisso = Path(nome).suffix.lower() or ".csv"
    contenuto = await upload.read()
    if not contenuto:
        raise ErroreParsing(f"Il file '{nome}' è vuoto.")

    descrittore, percorso_temp = tempfile.mkstemp(suffix=suffisso)
    try:
        with os.fdopen(descrittore, "wb") as temporaneo:
            temporaneo.write(contenuto)
        file = carica_file(percorso_temp, mappa)
    finally:
        os.unlink(percorso_temp)
    file.percorso = nome  # per riepiloghi ed export si mostra il nome originale
    return file


def _movimento_json(movimento) -> dict | None:
    if movimento is None:
        return None
    return {
        "riga": movimento.indice,
        "data": movimento.data.strftime("%d/%m/%Y") if movimento.data else None,
        "importo": float(movimento.importo),
        "descrizione": movimento.descrizione,
    }


def _riga_json(abbinamento: Abbinamento) -> dict:
    con_match = abbinamento.movimento_a is not None and abbinamento.movimento_b is not None
    return {
        "categoria": abbinamento.categoria.value,
        "a": _movimento_json(abbinamento.movimento_a),
        "b": _movimento_json(abbinamento.movimento_b),
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
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    tolleranza_importo: str = Form("0.01"),
    tolleranza_giorni: int = Form(3),
    valore_assoluto: bool = Form(False),
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
        norm_a = await _carica_upload(file_a, colonne_a)
        norm_b = await _carica_upload(file_b, colonne_b)
    except ErroreParsing as errore:
        raise HTTPException(400, str(errore))

    config = ConfigMatching(
        tolleranza_importo=tolleranza,
        tolleranza_giorni=tolleranza_giorni,
        valore_assoluto=valore_assoluto,
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


@app.get("/api/risultati/{identificativo}/excel")
def api_excel(identificativo: str) -> StreamingResponse:
    risultato = _risultati.get(identificativo)
    if risultato is None:
        raise HTTPException(404, "Risultato non trovato o scaduto: ripetere la riconciliazione.")
    buffer = io.BytesIO()
    esporta_excel(risultato, buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="riconciliazione.xlsx"'},
    )
