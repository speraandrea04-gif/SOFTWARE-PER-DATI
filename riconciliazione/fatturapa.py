"""Import di fatture elettroniche in formato FatturaPA (XML dello SDI).

Accetta un singolo file .xml, un file .p7m (firma CAdES, estrazione
best-effort dell'XML), una cartella o un archivio .zip contenente più
fatture. Ogni corpo fattura diventa un `Movimento` (un XML può contenere
un lotto con più fatture).

La controparte da usare nella descrizione viene scelta con un'euristica:
in un lotto di fatture della stessa azienda, l'azienda compare in tutte
(come cedente per le fatture attive, come cessionario per le passive)
mentre la controparte varia. Se l'euristica non è conclusiva si usano
entrambe le denominazioni.
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from .parsing import (
    ErroreParsing,
    FileNormalizzato,
    Movimento,
    carica_file,
    parse_data,
    parse_importo,
)

ESTENSIONI_FATTURA = {".xml", ".p7m"}


# ---------------------------------------------------------------------------
# Navigazione XML indipendente dal namespace
# ---------------------------------------------------------------------------

def _nome_locale(tag: str) -> str:
    return tag.rpartition("}")[2]


def _trova_tutti(elemento, nome: str) -> list:
    return [e for e in elemento.iter() if _nome_locale(e.tag) == nome]


def _trova(elemento, nome: str):
    trovati = _trova_tutti(elemento, nome)
    return trovati[0] if trovati else None


def _testo(elemento, nome: str) -> str:
    trovato = _trova(elemento, nome)
    if trovato is None or trovato.text is None:
        return ""
    return trovato.text.strip()


# ---------------------------------------------------------------------------
# Estrazione dell'XML da un .p7m (firma CAdES)
# ---------------------------------------------------------------------------

def _estrai_xml_da_p7m(dati: bytes) -> bytes | None:
    """Estrae il documento XML incapsulato in una busta CAdES (DER).

    Il contenuto firmato è contiguo all'interno della busta: si cerca
    l'inizio del documento e l'ultima chiusura di FatturaElettronica.
    """
    inizio = dati.find(b"<?xml")
    if inizio == -1:
        inizio = dati.find(b"FatturaElettronica")
        if inizio == -1:
            return None
        # Risale all'apertura del tag (che può avere un prefisso di namespace).
        apertura = dati.rfind(b"<", 0, inizio)
        if apertura == -1:
            return None
        inizio = apertura
    marcatore_fine = b"FatturaElettronica>"
    fine = dati.rfind(marcatore_fine)
    if fine == -1 or fine < inizio:
        return None
    return dati[inizio:fine + len(marcatore_fine)]


# ---------------------------------------------------------------------------
# Parsing di un singolo file fattura
# ---------------------------------------------------------------------------

@dataclass
class _Fattura:
    file: str
    numero: str
    data: date | None
    importo: Decimal | None
    cedente: str
    cessionario: str


def _denominazione(anagrafica_contenitore) -> str:
    """Denominazione (o Nome + Cognome) di CedentePrestatore/CessionarioCommittente."""
    if anagrafica_contenitore is None:
        return ""
    denominazione = _testo(anagrafica_contenitore, "Denominazione")
    if denominazione:
        return denominazione
    nome = _testo(anagrafica_contenitore, "Nome")
    cognome = _testo(anagrafica_contenitore, "Cognome")
    return " ".join(p for p in (nome, cognome) if p)


def _importo_da_riepilogo(corpo) -> Decimal | None:
    """Fallback quando manca ImportoTotaleDocumento: somma dei DatiRiepilogo."""
    totale = Decimal("0")
    trovato = False
    for riepilogo in _trova_tutti(corpo, "DatiRiepilogo"):
        imponibile = parse_importo(_testo(riepilogo, "ImponibileImporto"))
        imposta = parse_importo(_testo(riepilogo, "Imposta"))
        if imponibile is not None:
            totale += imponibile
            trovato = True
        if imposta is not None:
            totale += imposta
    return totale if trovato else None


def _leggi_fatture_da_xml(contenuto: bytes, nome_file: str) -> list[_Fattura]:
    radice = ElementTree.fromstring(contenuto)
    if _trova(radice, "FatturaElettronicaBody") is None:
        raise ErroreParsing(f"'{nome_file}' non sembra una fattura elettronica FatturaPA.")

    testata = _trova(radice, "FatturaElettronicaHeader")
    cedente = _denominazione(_trova(testata, "CedentePrestatore")) if testata is not None else ""
    cessionario = (_denominazione(_trova(testata, "CessionarioCommittente"))
                   if testata is not None else "")

    fatture = []
    for corpo in _trova_tutti(radice, "FatturaElettronicaBody"):
        documento = _trova(corpo, "DatiGeneraliDocumento")
        if documento is None:
            continue
        importo = parse_importo(_testo(documento, "ImportoTotaleDocumento"))
        if importo is None:
            importo = _importo_da_riepilogo(corpo)
        fatture.append(_Fattura(
            file=nome_file,
            numero=_testo(documento, "Numero"),
            data=parse_data(_testo(documento, "Data")),
            importo=importo,
            cedente=cedente,
            cessionario=cessionario,
        ))
    return fatture


# ---------------------------------------------------------------------------
# Raccolta dei file e caricamento
# ---------------------------------------------------------------------------

def _raccogli_file_fattura(percorso: Path) -> list[Path]:
    if percorso.is_dir():
        trovati = [p for p in sorted(percorso.rglob("*"))
                   if p.is_file() and p.suffix.lower() in ESTENSIONI_FATTURA]
        if not trovati:
            raise ErroreParsing(
                f"Nessun file .xml o .p7m trovato nella cartella '{percorso.name}'."
            )
        return trovati
    return [percorso]


def carica_fatture_xml(percorso: str | Path) -> FileNormalizzato:
    """Carica fatture FatturaPA da un file .xml/.p7m, una cartella o uno .zip."""
    percorso = Path(percorso)
    if not percorso.exists():
        raise ErroreParsing(f"File non trovato: {percorso}")

    if percorso.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as estratto:
            try:
                with zipfile.ZipFile(percorso) as archivio:
                    archivio.extractall(estratto)
            except zipfile.BadZipFile:
                raise ErroreParsing(f"'{percorso.name}' non è un archivio zip valido.")
            file = carica_fatture_xml(estratto)
        file.percorso = str(percorso)
        return file

    avvisi: list[str] = []
    fatture: list[_Fattura] = []
    for file_fattura in _raccogli_file_fattura(percorso):
        contenuto = file_fattura.read_bytes()
        if file_fattura.suffix.lower() == ".p7m":
            estratto = _estrai_xml_da_p7m(contenuto)
            if estratto is None:
                avvisi.append(f"'{file_fattura.name}': impossibile estrarre l'XML "
                              "dalla firma p7m, file ignorato.")
                continue
            contenuto = estratto
        try:
            fatture.extend(_leggi_fatture_da_xml(contenuto, file_fattura.name))
        except (ElementTree.ParseError, ErroreParsing) as errore:
            avvisi.append(f"'{file_fattura.name}': non leggibile come FatturaPA "
                          f"({errore}), file ignorato.")

    if not fatture:
        raise ErroreParsing(
            f"Nessuna fattura elettronica leggibile in '{percorso.name}'."
            + (f" Dettagli: {' '.join(avvisi)}" if avvisi else "")
        )

    # Euristica attive/passive: la parte costante è l'azienda, quella
    # variabile è la controparte.
    cedenti = {f.cedente for f in fatture if f.cedente}
    cessionari = {f.cessionario for f in fatture if f.cessionario}
    if len(cedenti) == 1 and len(cessionari) > 1:
        controparte_di = "cessionario"       # fatture attive (emesse)
    elif len(cessionari) == 1 and len(cedenti) > 1:
        controparte_di = "cedente"           # fatture passive (ricevute)
    else:
        controparte_di = "entrambi"
        if len(fatture) > 1:
            avvisi.append("Non è stato possibile determinare se le fatture sono "
                          "attive o passive: nella descrizione compaiono entrambe "
                          "le denominazioni.")

    movimenti: list[Movimento] = []
    righe_scartate: list[tuple[int, str]] = []
    for posizione, fattura in enumerate(fatture, start=1):
        if fattura.importo is None:
            righe_scartate.append((posizione, f"'{fattura.file}': importo assente"))
            continue
        if controparte_di == "cessionario":
            controparte = fattura.cessionario
        elif controparte_di == "cedente":
            controparte = fattura.cedente
        else:
            controparte = " ".join(p for p in (fattura.cedente, fattura.cessionario) if p)
        descrizione = " ".join(p for p in (f"FT {fattura.numero}".strip(), controparte) if p)
        movimenti.append(Movimento(
            indice=posizione,
            data=fattura.data,
            importo=fattura.importo,
            descrizione=descrizione,
            riga_originale={
                "File": fattura.file,
                "Numero": fattura.numero,
                "Data": fattura.data.isoformat() if fattura.data else "",
                "Importo": str(fattura.importo),
                "Cedente": fattura.cedente,
                "Cessionario": fattura.cessionario,
            },
        ))

    if not movimenti:
        raise ErroreParsing(f"Nessuna fattura con importo valido in '{percorso.name}'.")
    for posizione, motivo in righe_scartate:
        avvisi.append(f"Fattura {posizione} scartata: {motivo}.")

    return FileNormalizzato(
        percorso=str(percorso),
        movimenti=movimenti,
        mappa_colonne={"data": "Data documento", "importo": "ImportoTotaleDocumento",
                       "descrizione": ["Numero", "Controparte"]},
        avvisi=avvisi,
        righe_scartate=righe_scartate,
    )


def carica_origine(percorso: str | Path, mappa_colonne: dict | None = None) -> FileNormalizzato:
    """Punto d'ingresso unico: instrada verso il parser giusto.

    - cartella, .zip, .xml, .p7m → fatture elettroniche FatturaPA
    - .csv, .txt, .xlsx, .xls   → file tabellare (parsing.carica_file)
    """
    percorso = Path(percorso)
    suffisso = percorso.suffix.lower()
    if percorso.is_dir() or suffisso in (".zip", *ESTENSIONI_FATTURA):
        if mappa_colonne:
            raise ErroreParsing(
                "La mappatura manuale delle colonne non si applica alle "
                "fatture elettroniche XML."
            )
        return carica_fatture_xml(percorso)
    return carica_file(percorso, mappa_colonne)
