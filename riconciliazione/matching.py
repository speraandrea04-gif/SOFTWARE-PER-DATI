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
    # Pagamenti cumulativi: dopo il passaggio 1:1, cerca gruppi in cui un
    # movimento di un file corrisponde alla somma di più righe dell'altro
    # (es. un bonifico che salda tre fatture).
    cerca_gruppi: bool = True
    dimensione_massima_gruppo: int = 4
    # Distanza massima in giorni tra il movimento singolo e ogni riga del
    # gruppo (le fatture pagate cumulativamente precedono anche di molto).
    finestra_gruppo_giorni: int = 90
    # Similarità testuale media minima perché un gruppo sia accettato
    # (protegge da combinazioni di importi casualmente coincidenti).
    soglia_testo_gruppo: float = 0.3


@dataclass
class Abbinamento:
    """Esito del confronto per una riga: match trovato oppure no.

    Per i pagamenti cumulativi (1-a-molti) il lato "molti" è in gruppo_a
    o gruppo_b e il corrispondente movimento_a/movimento_b resta None.
    """

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
    gruppo_a: list[Movimento] | None = None
    gruppo_b: list[Movimento] | None = None

    @property
    def movimenti_a(self) -> list[Movimento]:
        if self.movimento_a is not None:
            return [self.movimento_a]
        return list(self.gruppo_a or [])

    @property
    def movimenti_b(self) -> list[Movimento]:
        if self.movimento_b is not None:
            return [self.movimento_b]
        return list(self.gruppo_b or [])

    @property
    def lato_mancante(self) -> str | None:
        """Per i NON_TROVATO: 'B' se la riga esiste solo in A, 'A' viceversa."""
        if self.categoria is not Categoria.NON_TROVATO:
            return None
        return "B" if not self.movimenti_b else "A"


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
# Pagamenti cumulativi (match 1-a-molti)
# ---------------------------------------------------------------------------

def _valore(movimento: Movimento, config: ConfigMatching) -> Decimal:
    return abs(movimento.importo) if config.valore_assoluto else movimento.importo


def _cerca_gruppo_per(singolo: Movimento, disponibili: list[Movimento],
                      config: ConfigMatching) -> tuple[list[Movimento], float] | None:
    """Cerca il miglior gruppo di `disponibili` la cui somma coincide con
    l'importo di `singolo` entro tolleranza. Restituisce (gruppo, score_testo)."""
    obiettivo = _valore(singolo, config)
    if obiettivo == 0:
        return None

    candidati = []
    for movimento in disponibili:
        valore = _valore(movimento, config)
        if valore == 0 or (valore > 0) != (obiettivo > 0):
            continue
        if abs(valore) > abs(obiettivo) + config.tolleranza_importo:
            continue
        if (singolo.data and movimento.data
                and abs((singolo.data - movimento.data).days) > config.finestra_gruppo_giorni):
            continue
        candidati.append(movimento)
    if len(candidati) < 2:
        return None

    punteggi_testo = {m.indice: punteggio_testo(singolo, m) for m in candidati}
    # Limita la ricerca ai candidati testualmente più pertinenti.
    candidati.sort(key=lambda m: (-punteggi_testo[m.indice], m.indice))
    candidati = candidati[:20]
    # Per la ricerca: importi grandi prima, così la potatura scatta subito.
    candidati.sort(key=lambda m: (-abs(_valore(m, config)), m.indice))

    soluzioni: list[list[Movimento]] = []

    def esplora(indice: int, somma: Decimal, scelti: list[Movimento]) -> None:
        if len(soluzioni) >= 200:
            return
        if len(scelti) >= 2 and abs(somma - obiettivo) <= config.tolleranza_importo:
            soluzioni.append(list(scelti))
            return  # aggiungere altre righe farebbe solo sforare
        if len(scelti) == config.dimensione_massima_gruppo:
            return
        for successivo in range(indice, len(candidati)):
            movimento = candidati[successivo]
            nuova_somma = somma + _valore(movimento, config)
            if abs(nuova_somma) > abs(obiettivo) + config.tolleranza_importo:
                continue
            scelti.append(movimento)
            esplora(successivo + 1, nuova_somma, scelti)
            scelti.pop()

    esplora(0, Decimal("0"), [])
    if not soluzioni:
        return None

    def qualita(gruppo: list[Movimento]) -> tuple:
        testo_medio = sum(punteggi_testo[m.indice] for m in gruppo) / len(gruppo)
        return (testo_medio, -len(gruppo), [-m.indice for m in gruppo])

    migliore = max(soluzioni, key=qualita)
    testo_medio = sum(punteggi_testo[m.indice] for m in migliore) / len(migliore)
    if testo_medio < config.soglia_testo_gruppo:
        return None
    return sorted(migliore, key=lambda m: m.indice), testo_medio


def _abbinamento_cumulativo(singolo: Movimento, gruppo: list[Movimento],
                            score_testo: float, lato_singolo: str,
                            config: ConfigMatching) -> Abbinamento:
    somma = sum((_valore(m, config) for m in gruppo), Decimal("0"))
    differenza = abs(_valore(singolo, config) - somma)

    score_data = 0.0
    con_data = [m for m in gruppo if m.data and singolo.data]
    if con_data:
        punteggi = []
        for movimento in con_data:
            giorni = abs((singolo.data - movimento.data).days)
            punteggi.append(1.0 if giorni <= config.tolleranza_giorni
                            else max(0.0, 1.0 - giorni / config.finestra_gruppo_giorni))
        score_data = sum(punteggi) / len(punteggi)

    confidenza = (config.peso_importo * 1.0
                  + config.peso_data * score_data
                  + config.peso_testo * score_testo)
    scomposizione = " + ".join(str(_valore(m, config)) for m in gruppo)
    dettagli = [f"pagamento cumulativo: 1 movimento ↔ {len(gruppo)} righe "
                f"({scomposizione} = {somma})"]

    return Abbinamento(
        categoria=Categoria.RICONCILIATO,
        movimento_a=singolo if lato_singolo == "A" else None,
        movimento_b=singolo if lato_singolo == "B" else None,
        gruppo_a=gruppo if lato_singolo == "B" else None,
        gruppo_b=gruppo if lato_singolo == "A" else None,
        confidenza=round(confidenza, 3),
        score_importo=1.0,
        score_data=round(score_data, 3),
        score_testo=round(score_testo, 3),
        differenza_importo=differenza,
        dettagli=dettagli,
    )


def _abbinamenti_cumulativi(file_a: FileNormalizzato, file_b: FileNormalizzato,
                            usati_a: set[int], usati_b: set[int],
                            config: ConfigMatching) -> list[Abbinamento]:
    """Cerca i pagamenti cumulativi tra le righe rimaste senza match 1:1.

    Entrambe le direzioni: un movimento di A che salda più righe di B
    (bonifico cumulativo) e viceversa (fattura pagata a rate).
    """
    risultati: list[Abbinamento] = []
    direzioni = [
        ("A", file_a.movimenti, usati_a, file_b.movimenti, usati_b),
        ("B", file_b.movimenti, usati_b, file_a.movimenti, usati_a),
    ]
    for lato, singoli, usati_singoli, molti, usati_molti in direzioni:
        in_attesa = sorted((m for m in singoli if m.indice not in usati_singoli),
                           key=lambda m: (-abs(_valore(m, config)), m.indice))
        for singolo in in_attesa:
            disponibili = [m for m in molti if m.indice not in usati_molti]
            trovato = _cerca_gruppo_per(singolo, disponibili, config)
            if trovato is None:
                continue
            gruppo, score_testo = trovato
            usati_singoli.add(singolo.indice)
            usati_molti.update(m.indice for m in gruppo)
            risultati.append(
                _abbinamento_cumulativo(singolo, gruppo, score_testo, lato, config))
    return risultati


# ---------------------------------------------------------------------------
# Riconciliazione completa
# ---------------------------------------------------------------------------

def riconcilia(file_a: FileNormalizzato, file_b: FileNormalizzato,
               config: ConfigMatching | None = None) -> RisultatoRiconciliazione:
    """Esegue il matching 1:1 tra i movimenti di A e di B."""
    config = config or ConfigMatching()

    # Massima confidenza raggiungibile con testo e data perfetti: se anche
    # così una coppia non arriva alla soglia, il costoso confronto fuzzy è
    # inutile e si salta. La potatura è "ammissibile" (usa un limite
    # superiore), quindi non scarta mai coppie che sarebbero candidate:
    # il risultato è identico a confrontare tutte le coppie.
    contributo_massimo_non_importo = config.peso_data + config.peso_testo

    candidati: list[Abbinamento] = []
    for mov_a in file_a.movimenti:
        for mov_b in file_b.movimenti:
            score_importo, _ = punteggio_importo(mov_a, mov_b, config)
            if (config.peso_importo * score_importo + contributo_massimo_non_importo
                    < config.soglia_candidato):
                continue
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

    if config.cerca_gruppi:
        abbinamenti.extend(
            _abbinamenti_cumulativi(file_a, file_b, usati_a, usati_b, config))

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
        ab.movimenti_a[0].indice if ab.movimenti_a else 10 ** 9,
        ab.movimenti_b[0].indice if ab.movimenti_b else 10 ** 9,
    ))

    return RisultatoRiconciliazione(
        abbinamenti=abbinamenti, config=config, file_a=file_a, file_b=file_b,
    )
