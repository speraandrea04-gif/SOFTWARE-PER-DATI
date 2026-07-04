"""Caricamento e normalizzazione dei file di input (CSV o Excel).

Ogni file viene trasformato in una lista di `Movimento` con tre campi
normalizzati: data, importo, descrizione. Le colonne rilevanti vengono
individuate automaticamente dalle intestazioni (con fallback sull'analisi
del contenuto) oppure possono essere indicate esplicitamente dall'utente.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

ESTENSIONI_CSV = {".csv", ".txt"}
ESTENSIONI_EXCEL = {".xlsx", ".xls"}

# Parole chiave (già normalizzate: minuscole, senza accenti) usate per
# riconoscere le colonne dalle intestazioni. L'ordine conta: la prima
# corrispondenza vince.
CHIAVI_DATA = [
    "data operazione", "data contabile", "data valuta", "data documento",
    "data fattura", "data emissione", "data", "date",
]
CHIAVI_IMPORTO = [
    "importo", "amount", "totale", "valore", "ammontare", "movimento",
]
CHIAVI_DESCRIZIONE = [
    "descrizione", "causale", "riferimento", "description", "dettaglio",
    "oggetto", "note", "controparte", "beneficiario", "cliente", "fornitore",
    "numero fattura", "n. fattura", "nr fattura", "numero documento", "numero",
]


class ErroreParsing(Exception):
    """Errore bloccante durante la lettura o normalizzazione di un file."""


@dataclass
class Movimento:
    """Una riga normalizzata di uno dei due file."""

    indice: int                      # numero riga nel file originale (1-based, esclusa intestazione)
    data: date | None
    importo: Decimal | None
    descrizione: str
    riga_originale: dict = field(default_factory=dict)


@dataclass
class FileNormalizzato:
    percorso: str
    movimenti: list[Movimento]
    mappa_colonne: dict              # {"data": str|None, "importo": str, "descrizione": [str, ...]}
    avvisi: list[str] = field(default_factory=list)
    righe_scartate: list[tuple[int, str]] = field(default_factory=list)  # (indice, motivo)


# ---------------------------------------------------------------------------
# Normalizzazione di testi, importi e date
# ---------------------------------------------------------------------------

def normalizza_testo(testo: str) -> str:
    """Minuscole, senza accenti, spazi compattati. Usato per confronti."""
    testo = unicodedata.normalize("NFKD", str(testo))
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", testo).strip().lower()


_RE_VALUTA = re.compile(r"(€|eur|euro|\$|usd|£|gbp)", re.IGNORECASE)


def parse_importo(valore) -> Decimal | None:
    """Interpreta un importo in formati comuni italiani e internazionali.

    Gestisce: "1.234,56", "1234.56", "1,234.56", "€ 100", "100,00 EUR",
    negativi con segno, parentesi contabili "(100,00)" e trailing minus.
    Restituisce None se il valore non è interpretabile come numero.
    """
    if valore is None:
        return None
    if isinstance(valore, bool):
        return None
    if isinstance(valore, (int, float, Decimal)):
        if pd.isna(valore):
            return None
        return Decimal(str(valore)).quantize(Decimal("0.01"))

    s = str(valore).strip()
    if not s:
        return None

    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo = True
        s = s[1:-1]
    s = _RE_VALUTA.sub("", s).strip()
    if s.endswith("-"):
        negativo = True
        s = s[:-1]
    if s.startswith("-"):
        negativo = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    s = s.replace(" ", "").replace("'", "")
    if not s:
        return None

    if "," in s and "." in s:
        # Il separatore più a destra è quello decimale.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Una sola virgola con 1-2 decimali → separatore decimale;
        # altrimenti separatore delle migliaia.
        parti = s.split(",")
        if len(parti) == 2 and len(parti[1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s:
        # Un punto seguito da esattamente 3 cifre e nessun altro indizio è
        # ambiguo ("1.234"): lo trattiamo come migliaia (convenzione italiana).
        parti = s.split(".")
        if len(parti) > 2 or (len(parti) == 2 and len(parti[1]) == 3):
            s = s.replace(".", "")

    try:
        importo = Decimal(s)
    except InvalidOperation:
        return None
    if negativo:
        importo = -importo
    return importo.quantize(Decimal("0.01"))


_FORMATI_DATA = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%y", "%d-%m-%y",
]


def parse_data(valore) -> date | None:
    """Interpreta una data nei formati più comuni (convenzione giorno/mese)."""
    if valore is None:
        return None
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    if isinstance(valore, pd.Timestamp):
        return valore.date()

    s = str(valore).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    # Rimuove un'eventuale parte oraria ("01/03/2026 10:30").
    s = s.split(" ")[0].split("T")[0]
    for fmt in _FORMATI_DATA:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(s, dayfirst=True, errors="coerce")
    except (ValueError, TypeError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.date()


# ---------------------------------------------------------------------------
# Lettura file
# ---------------------------------------------------------------------------

def _leggi_csv(percorso: Path) -> pd.DataFrame:
    ultimo_errore: Exception | None = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            testo = percorso.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            ultimo_errore = exc
            continue
        campione = testo[:4096]
        try:
            dialetto = csv.Sniffer().sniff(campione, delimiters=",;\t|")
            separatore = dialetto.delimiter
        except csv.Error:
            separatore = ";" if campione.count(";") > campione.count(",") else ","
        try:
            return pd.read_csv(percorso, sep=separatore, dtype=str,
                               encoding=encoding, skip_blank_lines=True)
        except Exception as exc:  # pandas solleva vari tipi di errore
            ultimo_errore = exc
    raise ErroreParsing(f"Impossibile leggere il CSV '{percorso.name}': {ultimo_errore}")


def _leggi_excel(percorso: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(percorso, dtype=object)
    except Exception as exc:
        raise ErroreParsing(f"Impossibile leggere il file Excel '{percorso.name}': {exc}")


def _leggi_dataframe(percorso: Path) -> pd.DataFrame:
    if not percorso.exists():
        raise ErroreParsing(f"File non trovato: {percorso}")
    estensione = percorso.suffix.lower()
    if estensione in ESTENSIONI_CSV:
        df = _leggi_csv(percorso)
    elif estensione in ESTENSIONI_EXCEL:
        df = _leggi_excel(percorso)
    else:
        raise ErroreParsing(
            f"Formato non supportato: '{estensione}'. Sono accettati CSV e Excel (.xlsx)."
        )
    if df.empty:
        raise ErroreParsing(f"Il file '{percorso.name}' non contiene righe di dati.")
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# Individuazione delle colonne
# ---------------------------------------------------------------------------

def _trova_per_intestazione(colonne: list[str], chiavi: list[str]) -> str | None:
    normalizzate = {col: normalizza_testo(col) for col in colonne}
    for chiave in chiavi:
        for col, norm in normalizzate.items():
            if norm == chiave:
                return col
    for chiave in chiavi:
        for col, norm in normalizzate.items():
            if chiave in norm:
                return col
    return None


def _quota_interpretabile(serie: pd.Series, parser) -> float:
    valori = serie.dropna().head(50)
    if len(valori) == 0:
        return 0.0
    ok = sum(1 for v in valori if parser(v) is not None)
    return ok / len(valori)


def individua_colonne(df: pd.DataFrame) -> tuple[dict, list[str]]:
    """Individua le colonne di data, importo e descrizione.

    Restituisce (mappa_colonne, avvisi). La descrizione può essere composta
    da più colonne testuali (es. numero fattura + causale), concatenate
    in fase di normalizzazione per migliorare il matching testuale.
    """
    avvisi: list[str] = []
    colonne = list(df.columns)

    col_data = _trova_per_intestazione(colonne, CHIAVI_DATA)
    col_importo = _trova_per_intestazione(colonne, CHIAVI_IMPORTO)

    # Fallback: analisi del contenuto.
    if col_data is None:
        candidate = [(c, _quota_interpretabile(df[c], parse_data)) for c in colonne
                     if c != col_importo]
        candidate = [(c, q) for c, q in candidate if q >= 0.8]
        if candidate:
            col_data = max(candidate, key=lambda x: x[1])[0]
            avvisi.append(f"Colonna data individuata dal contenuto: '{col_data}'")
    if col_importo is None:
        candidate = []
        for c in colonne:
            if c == col_data:
                continue
            quota = _quota_interpretabile(df[c], parse_importo)
            if quota >= 0.8 and _quota_interpretabile(df[c], parse_data) < 0.5:
                candidate.append((c, quota))
        if candidate:
            col_importo = candidate[0][0]
            avvisi.append(f"Colonna importo individuata dal contenuto: '{col_importo}'")

    if col_importo is None:
        raise ErroreParsing(
            "Impossibile individuare la colonna dell'importo. "
            f"Colonne disponibili: {colonne}. "
            "Specificarla manualmente (opzione --colonne)."
        )
    if col_data is None:
        avvisi.append("Nessuna colonna data individuata: il confronto sulle date sarà ignorato.")

    colonne_descrizione = []
    for chiave in CHIAVI_DESCRIZIONE:
        for col in colonne:
            if col in (col_data, col_importo) or col in colonne_descrizione:
                continue
            if chiave in normalizza_testo(col):
                colonne_descrizione.append(col)
    if not colonne_descrizione:
        # Fallback: tutte le colonne testuali rimanenti.
        residue = [c for c in colonne if c not in (col_data, col_importo)]
        residue = [c for c in residue
                   if _quota_interpretabile(df[c], parse_importo) < 0.5
                   and _quota_interpretabile(df[c], parse_data) < 0.5]
        colonne_descrizione = residue
        if residue:
            avvisi.append(
                f"Colonne descrizione individuate per esclusione: {residue}"
            )
        else:
            avvisi.append("Nessuna colonna descrizione individuata: "
                          "il confronto testuale sarà ignorato.")

    mappa = {"data": col_data, "importo": col_importo, "descrizione": colonne_descrizione}
    return mappa, avvisi


# ---------------------------------------------------------------------------
# Funzione principale
# ---------------------------------------------------------------------------

def carica_file(percorso: str | Path, mappa_colonne: dict | None = None) -> FileNormalizzato:
    """Carica un file CSV/Excel e lo normalizza in una lista di Movimento.

    `mappa_colonne` opzionale: {"data": "...", "importo": "...", "descrizione": ["...", ...]}
    per forzare le colonne invece dell'individuazione automatica.
    """
    percorso = Path(percorso)
    df = _leggi_dataframe(percorso)

    if mappa_colonne:
        mappa = {
            "data": mappa_colonne.get("data"),
            "importo": mappa_colonne.get("importo"),
            "descrizione": list(mappa_colonne.get("descrizione") or []),
        }
        avvisi = []
        for nome, col in [("data", mappa["data"]), ("importo", mappa["importo"]),
                          *[("descrizione", c) for c in mappa["descrizione"]]]:
            if col is not None and col not in df.columns:
                raise ErroreParsing(
                    f"La colonna '{col}' (campo {nome}) non esiste in '{percorso.name}'. "
                    f"Colonne disponibili: {list(df.columns)}"
                )
        if mappa["importo"] is None:
            raise ErroreParsing("La mappatura manuale deve indicare almeno la colonna importo.")
    else:
        mappa, avvisi = individua_colonne(df)

    movimenti: list[Movimento] = []
    righe_scartate: list[tuple[int, str]] = []
    for posizione, (_, riga) in enumerate(df.iterrows(), start=1):
        grezzo = {col: ("" if pd.isna(riga[col]) else riga[col]) for col in df.columns}

        importo = parse_importo(riga[mappa["importo"]])
        if importo is None:
            valore = riga[mappa["importo"]]
            if pd.isna(valore) or str(valore).strip() == "":
                motivo = "importo mancante"
            else:
                motivo = f"importo non interpretabile: '{valore}'"
            righe_scartate.append((posizione, motivo))
            continue

        data_mov = parse_data(riga[mappa["data"]]) if mappa["data"] else None
        if mappa["data"] and data_mov is None:
            avvisi.append(f"Riga {posizione}: data non interpretabile "
                          f"('{riga[mappa['data']]}'), riga confrontata senza data.")

        pezzi = []
        for col in mappa["descrizione"]:
            valore = riga[col]
            if not pd.isna(valore) and str(valore).strip():
                pezzi.append(str(valore).strip())
        descrizione = " ".join(pezzi)

        movimenti.append(Movimento(
            indice=posizione,
            data=data_mov,
            importo=importo,
            descrizione=descrizione,
            riga_originale=grezzo,
        ))

    if not movimenti:
        raise ErroreParsing(
            f"Nessuna riga valida in '{percorso.name}': "
            "controllare che la colonna importo sia corretta."
        )
    for posizione, motivo in righe_scartate:
        avvisi.append(f"Riga {posizione} scartata: {motivo}.")

    return FileNormalizzato(
        percorso=str(percorso),
        movimenti=movimenti,
        mappa_colonne=mappa,
        avvisi=avvisi,
        righe_scartate=righe_scartate,
    )
