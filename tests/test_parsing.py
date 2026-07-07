from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from riconciliazione.parsing import (
    ErroreParsing,
    carica_file,
    normalizza_testo,
    parse_data,
    parse_importo,
)

RADICE = Path(__file__).parent.parent
ESTRATTO = RADICE / "dati_test" / "estratto_conto.csv"
REGISTRO = RADICE / "dati_test" / "registro_fatture.csv"
REGISTRO_XLSX = RADICE / "dati_test" / "registro_fatture.xlsx"


# --------------------------------------------------------------------------
# parse_importo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valore, atteso", [
    ("1.234,56", Decimal("1234.56")),      # formato italiano
    ("1234.56", Decimal("1234.56")),       # formato internazionale
    ("1,234.56", Decimal("1234.56")),      # formato anglosassone
    ("€ 100", Decimal("100.00")),
    ("100,00 EUR", Decimal("100.00")),
    ("(50,00)", Decimal("-50.00")),        # negativo contabile
    ("100-", Decimal("-100.00")),          # trailing minus
    ("-12.5", Decimal("-12.50")),
    ("+30", Decimal("30.00")),
    ("1.234", Decimal("1234.00")),         # punto come migliaia (convenzione it.)
    ("12,5", Decimal("12.50")),
    ("1.234.567,89", Decimal("1234567.89")),
    (850.5, Decimal("850.50")),            # già numerico (es. da Excel)
    (100, Decimal("100.00")),
])
def test_parse_importo_validi(valore, atteso):
    assert parse_importo(valore) == atteso


@pytest.mark.parametrize("valore", [
    "", "   ", "abc", "12/03/2026", None, True, "€",
    # Valori fuori scala o non finiti: scartati, mai eccezioni.
    "1E+30", "1e999", float("inf"), float("-inf"), 10 ** 40, Decimal("NaN"),
])
def test_parse_importo_non_validi(valore):
    assert parse_importo(valore) is None


# --------------------------------------------------------------------------
# parse_data
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valore, attesa", [
    ("02/03/2026", date(2026, 3, 2)),
    ("2026-03-02", date(2026, 3, 2)),
    ("02-03-2026", date(2026, 3, 2)),
    ("02.03.2026", date(2026, 3, 2)),
    ("02/03/26", date(2026, 3, 2)),
    ("02/03/2026 10:30", date(2026, 3, 2)),
    (datetime(2026, 3, 2, 12, 0), date(2026, 3, 2)),
    (date(2026, 3, 2), date(2026, 3, 2)),
])
def test_parse_data_valide(valore, attesa):
    assert parse_data(valore) == attesa


@pytest.mark.parametrize("valore", ["", "non è una data", None, "99/99/2026"])
def test_parse_data_non_valide(valore):
    assert parse_data(valore) is None


def test_normalizza_testo():
    assert normalizza_testo("  Società  ÀLFA   ") == "societa alfa"


# --------------------------------------------------------------------------
# carica_file
# --------------------------------------------------------------------------

def test_carica_estratto_conto():
    file = carica_file(ESTRATTO)
    assert file.mappa_colonne["data"] == "Data operazione"
    assert file.mappa_colonne["importo"] == "Importo"
    assert file.mappa_colonne["descrizione"] == ["Descrizione operazione"]
    # 11 righe nel CSV, 1 scartata (importo mancante)
    assert len(file.movimenti) == 10
    assert len(file.righe_scartate) == 1
    assert file.righe_scartate[0][0] == 11

    primo = file.movimenti[0]
    assert primo.data == date(2026, 3, 2)
    assert primo.importo == Decimal("1220.00")
    assert "ROSSI" in primo.descrizione


def test_carica_registro_fatture():
    file = carica_file(REGISTRO)
    assert file.mappa_colonne["data"] == "Data"
    assert file.mappa_colonne["importo"] == "Importo (€)"
    assert set(file.mappa_colonne["descrizione"]) == {"Numero Fattura", "Cliente"}
    assert len(file.movimenti) == 10
    primo = file.movimenti[0]
    assert primo.data == date(2026, 3, 1)
    assert primo.importo == Decimal("1220.00")
    assert "FT 2026/101" in primo.descrizione and "Rossi" in primo.descrizione


def test_carica_excel():
    file = carica_file(REGISTRO_XLSX)
    assert len(file.movimenti) == 10
    assert file.movimenti[0].importo == Decimal("1220.00")
    assert file.movimenti[0].data == date(2026, 3, 1)


def test_mappatura_manuale():
    file = carica_file(REGISTRO, {"data": "Data", "importo": "Importo (€)",
                                  "descrizione": ["Cliente"]})
    assert file.mappa_colonne["descrizione"] == ["Cliente"]
    assert "FT" not in file.movimenti[0].descrizione


def test_mappatura_manuale_colonna_inesistente():
    with pytest.raises(ErroreParsing, match="non esiste"):
        carica_file(REGISTRO, {"importo": "Colonna Fantasma"})


def test_file_inesistente():
    with pytest.raises(ErroreParsing, match="non trovato"):
        carica_file("non_esiste.csv")


def test_estensione_non_supportata(tmp_path):
    percorso = tmp_path / "dati.pdf"
    percorso.write_text("finto pdf")
    with pytest.raises(ErroreParsing, match="Formato non supportato"):
        carica_file(percorso)


def test_file_senza_colonna_importo(tmp_path):
    percorso = tmp_path / "strano.csv"
    percorso.write_text("Colonna1,Colonna2\nciao,mondo\n")
    with pytest.raises(ErroreParsing, match="importo"):
        carica_file(percorso)


def test_csv_delimitatore_virgola_e_intestazioni_inglesi(tmp_path):
    percorso = tmp_path / "inglese.csv"
    percorso.write_text(
        "Date,Description,Amount\n"
        "2026-01-15,Invoice 42,99.90\n"
    )
    file = carica_file(percorso)
    assert file.movimenti[0].importo == Decimal("99.90")
    assert file.movimenti[0].data == date(2026, 1, 15)
