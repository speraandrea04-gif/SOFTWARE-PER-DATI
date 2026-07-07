"""Test del match 1-a-molti (pagamenti cumulativi)."""

from datetime import date
from decimal import Decimal

from riconciliazione.matching import Categoria, ConfigMatching, riconcilia
from riconciliazione.parsing import FileNormalizzato, Movimento


def mov(indice, giorno, importo, descrizione="", mese=3):
    return Movimento(indice=indice, data=date(2026, mese, giorno) if giorno else None,
                     importo=Decimal(importo), descrizione=descrizione)


def file_norm(movimenti, nome="test.csv"):
    return FileNormalizzato(percorso=nome, movimenti=movimenti,
                            mappa_colonne={"data": "d", "importo": "i", "descrizione": ["t"]})


def esegui(mov_a, mov_b, config=None):
    return riconcilia(file_norm(mov_a, "a.csv"), file_norm(mov_b, "b.csv"), config)


def test_bonifico_salda_tre_fatture():
    risultato = esegui(
        [mov(1, 20, "1750.00", "bonifico rossi srl saldo ft 101 102 103")],
        [mov(1, 1, "1000.00", "ft 101 rossi srl"),
         mov(2, 5, "500.00", "ft 102 rossi srl"),
         mov(3, 10, "250.00", "ft 103 rossi srl")],
    )
    assert risultato.conteggi == {"riconciliato": 1, "discrepanza": 0, "non_trovato": 0}
    ab = risultato.per_categoria(Categoria.RICONCILIATO)[0]
    assert len(ab.movimenti_b) == 3
    assert any("cumulativo" in d for d in ab.dettagli)


def test_fattura_pagata_a_rate():
    # Direzione opposta: più movimenti banca saldano una fattura.
    risultato = esegui(
        [mov(1, 5, "600.00", "acconto ft 200 verdi"),
         mov(2, 25, "400.00", "saldo ft 200 verdi")],
        [mov(1, 1, "1000.00", "ft 200 verdi spa")],
    )
    assert risultato.conteggi["riconciliato"] == 1
    ab = risultato.per_categoria(Categoria.RICONCILIATO)[0]
    assert len(ab.movimenti_a) == 2 and len(ab.movimenti_b) == 1


def test_somma_fuori_tolleranza_non_raggruppa():
    risultato = esegui(
        [mov(1, 20, "1750.00", "bonifico rossi")],
        [mov(1, 1, "1000.00", "ft 101 rossi"), mov(2, 5, "500.00", "ft 102 rossi")],
    )  # 1500 ≠ 1750
    assert risultato.conteggi["riconciliato"] == 0
    assert risultato.conteggi["non_trovato"] == 3


def test_gruppi_disattivabili():
    config = ConfigMatching(cerca_gruppi=False)
    risultato = esegui(
        [mov(1, 20, "1500.00", "bonifico rossi ft 101 102")],
        [mov(1, 1, "1000.00", "ft 101 rossi"), mov(2, 5, "500.00", "ft 102 rossi")],
        config,
    )
    assert risultato.conteggi["riconciliato"] == 0
    assert risultato.conteggi["non_trovato"] == 3


def test_gruppo_non_ruba_match_uno_a_uno():
    # La riga B3 combacia 1:1 con A2: il gruppo per A1 deve usare B1+B2.
    risultato = esegui(
        [mov(1, 20, "1500.00", "bonifico rossi ft 101 102"),
         mov(2, 10, "500.00", "bonifico verdi ft 103")],
        [mov(1, 1, "1000.00", "ft 101 rossi"),
         mov(2, 5, "500.00", "ft 102 rossi"),
         mov(3, 9, "500.00", "ft 103 verdi")],
    )
    assert risultato.conteggi["riconciliato"] == 2
    per_a = {ab.movimenti_a[0].indice: ab for ab in risultato.abbinamenti}
    assert {m.indice for m in per_a[1].movimenti_b} == {1, 2}
    assert {m.indice for m in per_a[2].movimenti_b} == {3}


def test_dimensione_massima_gruppo():
    config = ConfigMatching(dimensione_massima_gruppo=2)
    risultato = esegui(
        [mov(1, 20, "300.00", "pagamento ft 1 2 3 alfa")],
        [mov(1, 1, "100.00", "ft 1 alfa"), mov(2, 2, "100.00", "ft 2 alfa"),
         mov(3, 3, "100.00", "ft 3 alfa")],
        config,
    )
    assert risultato.conteggi["riconciliato"] == 0


def test_finestra_giorni_gruppo():
    config = ConfigMatching(finestra_gruppo_giorni=10)
    risultato = esegui(
        [mov(1, 20, "300.00", "pagamento ft 1 2 alfa", mese=6)],
        [mov(1, 1, "100.00", "ft 1 alfa"), mov(2, 2, "200.00", "ft 2 alfa")],
        config,
    )  # fatture di marzo, pagamento a giugno: fuori finestra
    assert risultato.conteggi["riconciliato"] == 0


def test_testo_incoerente_non_raggruppa():
    # Somma perfetta ma descrizioni del tutto scollegate → niente gruppo.
    config = ConfigMatching(soglia_testo_gruppo=0.4)
    risultato = esegui(
        [mov(1, 20, "300.00", "bonifico rossi srl")],
        [mov(1, 1, "100.00", "canone zeta telecomunicazioni"),
         mov(2, 2, "200.00", "utenza acquedotto kappa")],
        config,
    )
    assert risultato.conteggi["riconciliato"] == 0


def test_valore_assoluto_nei_gruppi():
    config = ConfigMatching(valore_assoluto=True)
    risultato = esegui(
        [mov(1, 20, "-1500.00", "pagamento fornitore beta ft 7 8")],
        [mov(1, 1, "1000.00", "ft 7 beta"), mov(2, 5, "500.00", "ft 8 beta")],
        config,
    )
    assert risultato.conteggi["riconciliato"] == 1
