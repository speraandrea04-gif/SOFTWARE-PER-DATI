"""Test smoke del report PDF."""

import io
from pathlib import Path

from riconciliazione.fatturapa import carica_origine
from riconciliazione.matching import riconcilia
from riconciliazione.report import genera_pdf

RADICE = Path(__file__).parent.parent


def _risultato():
    return riconcilia(
        carica_origine(RADICE / "dati_test" / "estratto_conto.csv"),
        carica_origine(RADICE / "dati_test" / "registro_fatture.csv"),
    )


def test_pdf_su_file(tmp_path):
    percorso = tmp_path / "report.pdf"
    genera_pdf(_risultato(), percorso)
    contenuto = percorso.read_bytes()
    assert contenuto.startswith(b"%PDF")
    assert len(contenuto) > 2000


def test_pdf_su_buffer():
    buffer = io.BytesIO()
    genera_pdf(_risultato(), buffer)
    assert buffer.getvalue().startswith(b"%PDF")


def test_pdf_con_descrizioni_con_caratteri_speciali(tmp_path):
    # "Verdi & C." nei dati di test: l'& non deve rompere i Paragraph.
    percorso = tmp_path / "report.pdf"
    genera_pdf(_risultato(), percorso)
    assert percorso.exists()
