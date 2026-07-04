from datetime import date
from decimal import Decimal

from riconciliazione.matching import (
    Categoria,
    ConfigMatching,
    riconcilia,
)
from riconciliazione.parsing import FileNormalizzato, Movimento


def mov(indice, giorno, importo, descrizione=""):
    return Movimento(indice=indice, data=date(2026, 3, giorno) if giorno else None,
                     importo=Decimal(importo), descrizione=descrizione)


def file_norm(movimenti, nome="test.csv"):
    return FileNormalizzato(percorso=nome, movimenti=movimenti,
                            mappa_colonne={"data": "d", "importo": "i", "descrizione": ["t"]})


def esegui(mov_a, mov_b, config=None):
    return riconcilia(file_norm(mov_a, "a.csv"), file_norm(mov_b, "b.csv"), config)


def test_match_esatto_riconciliato():
    risultato = esegui([mov(1, 10, "100.00", "fattura 42")],
                       [mov(1, 10, "100.00", "FT 42")])
    assert risultato.conteggi == {"riconciliato": 1, "discrepanza": 0, "non_trovato": 0}


def test_data_entro_tolleranza_riconciliato():
    risultato = esegui([mov(1, 10, "100.00", "pagamento rossi")],
                       [mov(1, 12, "100.00", "rossi")])
    assert risultato.conteggi["riconciliato"] == 1


def test_importo_diverso_discrepanza():
    risultato = esegui([mov(1, 10, "100.00", "fattura 42 rossi srl")],
                       [mov(1, 10, "100.05", "FT 42 rossi srl")])
    assert risultato.conteggi["discrepanza"] == 1
    ab = risultato.per_categoria(Categoria.DISCREPANZA)[0]
    assert ab.differenza_importo == Decimal("0.05")
    assert any("importo" in d for d in ab.dettagli)


def test_data_fuori_tolleranza_discrepanza():
    risultato = esegui([mov(1, 20, "100.00", "fattura 42 rossi srl")],
                       [mov(1, 10, "100.00", "FT 42 rossi srl")])
    assert risultato.conteggi["discrepanza"] == 1
    ab = risultato.per_categoria(Categoria.DISCREPANZA)[0]
    assert ab.differenza_giorni == 10
    assert any("data" in d for d in ab.dettagli)


def test_nessun_match_non_trovato():
    risultato = esegui([mov(1, 10, "100.00", "alfa")],
                       [mov(1, 25, "999.99", "beta")])
    assert risultato.conteggi["non_trovato"] == 2
    lati = {ab.lato_mancante for ab in risultato.per_categoria(Categoria.NON_TROVATO)}
    assert lati == {"A", "B"}


def test_ambiguita_risolta_dal_testo():
    # Due movimenti con stesso importo e date vicine: il testo decide.
    risultato = esegui(
        [mov(1, 20, "1100.00", "bonifico gamma srl ft 106"),
         mov(2, 20, "1100.00", "bonifico delta srl ft 107")],
        [mov(1, 19, "1100.00", "ft 107 delta srl"),
         mov(2, 20, "1100.00", "ft 106 gamma srl")],
    )
    assert risultato.conteggi["riconciliato"] == 2
    coppie = {(ab.movimento_a.indice, ab.movimento_b.indice)
              for ab in risultato.per_categoria(Categoria.RICONCILIATO)}
    assert coppie == {(1, 2), (2, 1)}


def test_tolleranza_importo_configurabile():
    config = ConfigMatching(tolleranza_importo=Decimal("0.10"))
    risultato = esegui([mov(1, 10, "100.00", "x")],
                       [mov(1, 10, "100.05", "x")], config)
    assert risultato.conteggi["riconciliato"] == 1


def test_tolleranza_giorni_configurabile():
    config = ConfigMatching(tolleranza_giorni=15)
    risultato = esegui([mov(1, 20, "100.00", "rossi")],
                       [mov(1, 10, "100.00", "rossi")], config)
    assert risultato.conteggi["riconciliato"] == 1


def test_valore_assoluto():
    config = ConfigMatching(valore_assoluto=True)
    risultato = esegui([mov(1, 10, "-100.00", "addebito rossi")],
                       [mov(1, 10, "100.00", "rossi")], config)
    assert risultato.conteggi["riconciliato"] == 1


def test_matching_uno_a_uno():
    # Due righe identiche in A, una sola in B: una riconciliata, una non trovata.
    risultato = esegui(
        [mov(1, 10, "100.00", "rossi"), mov(2, 10, "100.00", "rossi")],
        [mov(1, 10, "100.00", "rossi")],
    )
    assert risultato.conteggi["riconciliato"] == 1
    assert risultato.conteggi["non_trovato"] == 1
    assert risultato.per_categoria(Categoria.NON_TROVATO)[0].lato_mancante == "B"


def test_data_mancante():
    risultato = esegui([mov(1, None, "100.00", "fattura 42 rossi srl")],
                       [mov(1, 10, "100.00", "ft 42 rossi srl")])
    # Importo uguale e testo molto simile, ma data assente → discrepanza.
    assert risultato.conteggi["discrepanza"] == 1
    ab = risultato.per_categoria(Categoria.DISCREPANZA)[0]
    assert any("data mancante" in d for d in ab.dettagli)
