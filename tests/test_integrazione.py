"""Test end-to-end sui dati fittizi di dati_test/."""

from pathlib import Path

import pandas as pd
import pytest

from riconciliazione.cli import main
from riconciliazione.export import esporta_excel
from riconciliazione.matching import Categoria, riconcilia
from riconciliazione.parsing import carica_file

RADICE = Path(__file__).parent.parent
ESTRATTO = RADICE / "dati_test" / "estratto_conto.csv"
REGISTRO = RADICE / "dati_test" / "registro_fatture.csv"
REGISTRO_XLSX = RADICE / "dati_test" / "registro_fatture.xlsx"


@pytest.fixture(scope="module")
def risultato():
    return riconcilia(carica_file(ESTRATTO), carica_file(REGISTRO))


def test_conteggi_attesi(risultato):
    # Casi costruiti nei dati di test:
    # 5 match pieni, 3 discrepanze (importo, data, importo),
    # 2 righe solo nell'estratto + 2 solo nel registro.
    assert risultato.conteggi == {"riconciliato": 5, "discrepanza": 3, "non_trovato": 4}


def test_ambiguita_gamma_delta(risultato):
    # Stesso importo (1100), date vicine: il testo deve abbinare
    # GAMMA↔FT 2026/106 e DELTA↔FT 2026/107.
    riconciliati = risultato.per_categoria(Categoria.RICONCILIATO)
    per_descrizione = {ab.movimento_a.descrizione: ab.movimento_b.descrizione
                       for ab in riconciliati}
    gamma = next(v for k, v in per_descrizione.items() if "GAMMA" in k)
    delta = next(v for k, v in per_descrizione.items() if "DELTA" in k)
    assert "2026/106" in gamma and "Gamma" in gamma
    assert "2026/107" in delta and "Delta" in delta


def test_dettaglio_discrepanze(risultato):
    discrepanze = risultato.per_categoria(Categoria.DISCREPANZA)
    dettagli = " | ".join(d for ab in discrepanze for d in ab.dettagli)
    assert "importo diverso" in dettagli
    assert "data diversa" in dettagli


def test_non_trovati(risultato):
    non_trovati = risultato.per_categoria(Categoria.NON_TROVATO)
    solo_a = [ab for ab in non_trovati if ab.lato_mancante == "B"]
    solo_b = [ab for ab in non_trovati if ab.lato_mancante == "A"]
    assert len(solo_a) == 2 and len(solo_b) == 2
    descrizioni_a = " ".join(ab.movimento_a.descrizione for ab in solo_a)
    assert "CONTANTI" in descrizioni_a and "POS" in descrizioni_a


def test_excel_come_input(risultato):
    """Il registro in formato .xlsx deve dare lo stesso risultato del CSV."""
    da_excel = riconcilia(carica_file(ESTRATTO), carica_file(REGISTRO_XLSX))
    assert da_excel.conteggi == risultato.conteggi


def test_export_excel(risultato, tmp_path):
    percorso = esporta_excel(risultato, tmp_path / "risultato.xlsx")
    fogli = pd.read_excel(percorso, sheet_name=None)
    assert set(fogli) == {"Riepilogo", "Riconciliati", "Discrepanze", "Non trovati"}
    assert len(fogli["Riconciliati"]) == 5
    assert len(fogli["Discrepanze"]) == 3
    assert len(fogli["Non trovati"]) == 4
    assert "Dettagli" in fogli["Discrepanze"].columns


def test_cli_end_to_end(tmp_path, capsys):
    excel_out = tmp_path / "out.xlsx"
    codice = main([str(ESTRATTO), str(REGISTRO), "--excel", str(excel_out)])
    output = capsys.readouterr().out
    assert codice == 0
    assert excel_out.exists()
    assert "Riconciliato: 5" in output
    assert "Discrepanza: 3" in output
    assert "Non trovato: 4" in output


def test_cli_file_inesistente(capsys):
    codice = main(["manca.csv", str(REGISTRO)])
    assert codice == 1
    assert "Errore" in capsys.readouterr().err
