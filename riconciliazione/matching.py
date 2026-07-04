"""Motore di matching tra i movimenti di due file normalizzati.

Per ogni coppia (riga A, riga B) viene calcolata una confidenza pesata su:
- importo (peso alto)
- data (peso medio)
- similarità testuale della descrizione/causale (peso basso)

Le coppie vengono poi assegnate 1:1 in ordine di confidenza decrescente
(assegnazione greedy) e classificate in tre categorie:
- RICONCILIATO: importo e data entro tolleranza, confidenza sopra soglia
- DISCREPANZA: match credibile ma con importo o data fuori tolleranza
- NON_TROVATO: nessun match credibile (righe presenti solo in A o solo in B)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from rapidfuzz import fuzz

from .parsing import FileNormalizzato, Movimento, normalizza_testo


class Categoria(Enum):
    RICONCILIATO = "riconciliato"
    DISCREPANZA = "discrepanza"
    NON_TROVATO = "non_trovato"


@dataclass
class ConfigMatching:
    tolleranza_importo: Decimal = Decimal("0.01")   # in euro
    tolleranza_giorni: int = 3
    # Sotto questa confidenza una coppia non è considerata un match credibile.
    soglia_candidato: float = 0.55
    peso_importo: float = 0.5
    peso_data: float = 0.3
    peso_testo: float = 0.2
    # Confronta i valori assoluti degli importi (utile quando un file usa
    # segni negativi per le uscite e l'altro importi sempre positivi).
    valore_assoluto: bool = False


@dataclass
class Abbinamento:
    """Esito del confronto per una riga: match trovato oppure no."""

    categoria: Categoria
    movimento_a: Movimento | None
    movimento_b: Movimento | None
    confidenza: float = 0.0
    score_importo: float = 0.0
    score_data: float = 0.0
    score_testo: float = 0.0
    differenza_importo: Decimal | None = None
    differenza_giorni: int | None = None
    dettagli: list[str] = field(default_factory=list)

    @property
    def lato_mancante(self) -> str | None:
        """Per i NON_TROVATO: 'B' se la riga esiste solo in A, 'A' viceversa."""
        if self.categoria is not Categoria.NON_TROVATO:
            return None
        return "B" if self.movimento_b is None else "A"


@dataclass
class RisultatoRiconciliazione:
    abbinamenti: list[Abbinamento]
    config: ConfigMatching
    file_a: FileNormalizzato
    file_b: FileNormalizzato

    def per_categoria(self, categoria: Categoria) -> list[Abbinamento]:
        return [a for a in self.abbinamenti if a.categoria is categoria]

    @property
    def conteggi(self) -> dict[str, int]:
        return {cat.value: len(self.per_categoria(cat)) for cat in Categoria}


# ---------------------------------------------------------------------------
# Punteggi sui singoli criteri
# ---------------------------------------------------------------------------

def _importi(a: Movimento, b: Movimento, config: ConfigMatching) -> tuple[Decimal, Decimal]:
    importo_a, importo_b = a.importo, b.importo
    if config.valore_assoluto:
        importo_a, importo_b = abs(importo_a), abs(importo_b)
    return importo_a, importo_b


def punteggio_importo(a: Movimento, b: Movimento, config: ConfigMatching) -> tuple[float, Decimal]:
    """Restituisce (score 0..1, differenza assoluta)."""
    importo_a, importo_b = _importi(a, b, config)
    differenza = abs(importo_a - importo_b)
    if differenza <= config.tolleranza_importo:
        return 1.0, differenza
    riferimento = max(abs(importo_a), abs(importo_b), Decimal("1"))
    relativa = float(differenza / riferimento)
    # 1% di scostamento → 0.9; 10% → 0. Piccole differenze assolute
    # (pochi centesimi/decine di centesimi) restano comunque candidati forti.
    score = max(0.0, 1.0 - relativa * 10.0)
    if differenza <= Decimal("0.50"):
        score = max(score, 0.85)
    return score, differenza


def punteggio_data(a: Movimento, b: Movimento, config: ConfigMatching) -> tuple[float, int | None]:
    """Restituisce (score 0..1, differenza in giorni o None se manca una data)."""
    if a.data is None or b.data is None:
        return 0.0, None
    giorni = abs((a.data - b.data).days)
    if giorni <= config.tolleranza_giorni:
        return 1.0, giorni
    # Decadimento lineare: a 30 giorni oltre la tolleranza lo score è 0.
    score = max(0.0, 1.0 - (giorni - config.tolleranza_giorni) / 30.0)
    return score, giorni


def punteggio_testo(a: Movimento, b: Movimento) -> float:
    testo_a = normalizza_testo(a.descrizione)
    testo_b = normalizza_testo(b.descrizione)
    if not testo_a or not testo_b:
        # Nessun testo da confrontare: punteggio neutro, non penalizzante.
        return 0.5
    return fuzz.token_set_ratio(testo_a, testo_b) / 100.0


def confronta(a: Movimento, b: Movimento, config: ConfigMatching) -> Abbinamento:
    """Confronta due movimenti e costruisce l'abbinamento (senza categoria definitiva)."""
    score_importo, diff_importo = punteggio_importo(a, b, config)
    score_data, diff_giorni = punteggio_data(a, b, config)
    score_testo = punteggio_testo(a, b)

    confidenza = (config.peso_importo * score_importo
                  + config.peso_data * score_data
                  + config.peso_testo * score_testo)

    importo_ok = diff_importo <= config.tolleranza_importo
    data_ok = diff_giorni is not None and diff_giorni <= config.tolleranza_giorni

    dettagli: list[str] = []
    if importo_ok and data_ok:
        categoria = Categoria.RICONCILIATO
    else:
        categoria = Categoria.DISCREPANZA
        importo_a, importo_b = _importi(a, b, config)
        if not importo_ok:
            dettagli.append(
                f"importo diverso: {importo_a} vs {importo_b} (Δ {diff_importo})"
            )
        if diff_giorni is None:
            dettagli.append("data mancante in una delle due righe")
        elif not data_ok:
            dettagli.append(
                f"data diversa: {a.data:%d/%m/%Y} vs {b.data:%d/%m/%Y} (Δ {diff_giorni} giorni)"
            )

    return Abbinamento(
        categoria=categoria,
        movimento_a=a,
        movimento_b=b,
        confidenza=round(confidenza, 3),
        score_importo=round(score_importo, 3),
        score_data=round(score_data, 3),
        score_testo=round(score_testo, 3),
        differenza_importo=diff_importo,
        differenza_giorni=diff_giorni,
        dettagli=dettagli,
    )


# ---------------------------------------------------------------------------
# Riconciliazione completa
# ---------------------------------------------------------------------------

def riconcilia(file_a: FileNormalizzato, file_b: FileNormalizzato,
               config: ConfigMatching | None = None) -> RisultatoRiconciliazione:
    """Esegue il matching 1:1 tra i movimenti di A e di B."""
    config = config or ConfigMatching()

    candidati: list[Abbinamento] = []
    for mov_a in file_a.movimenti:
        for mov_b in file_b.movimenti:
            abbinamento = confronta(mov_a, mov_b, config)
            if abbinamento.confidenza >= config.soglia_candidato:
                candidati.append(abbinamento)

    # Assegnazione greedy: prima le coppie con confidenza più alta.
    # Ordinamento deterministico a parità di confidenza.
    candidati.sort(key=lambda ab: (-ab.confidenza,
                                   ab.movimento_a.indice, ab.movimento_b.indice))
    usati_a: set[int] = set()
    usati_b: set[int] = set()
    abbinamenti: list[Abbinamento] = []
    for abbinamento in candidati:
        if abbinamento.movimento_a.indice in usati_a:
            continue
        if abbinamento.movimento_b.indice in usati_b:
            continue
        usati_a.add(abbinamento.movimento_a.indice)
        usati_b.add(abbinamento.movimento_b.indice)
        abbinamenti.append(abbinamento)

    for mov_a in file_a.movimenti:
        if mov_a.indice not in usati_a:
            abbinamenti.append(Abbinamento(
                categoria=Categoria.NON_TROVATO,
                movimento_a=mov_a, movimento_b=None,
                dettagli=["nessuna corrispondenza nel file B"],
            ))
    for mov_b in file_b.movimenti:
        if mov_b.indice not in usati_b:
            abbinamenti.append(Abbinamento(
                categoria=Categoria.NON_TROVATO,
                movimento_a=None, movimento_b=mov_b,
                dettagli=["nessuna corrispondenza nel file A"],
            ))

    ordine = {Categoria.RICONCILIATO: 0, Categoria.DISCREPANZA: 1, Categoria.NON_TROVATO: 2}
    abbinamenti.sort(key=lambda ab: (
        ordine[ab.categoria],
        ab.movimento_a.indice if ab.movimento_a else 10 ** 9,
        ab.movimento_b.indice if ab.movimento_b else 10 ** 9,
    ))

    return RisultatoRiconciliazione(
        abbinamenti=abbinamenti, config=config, file_a=file_a, file_b=file_b,
    )
