

def test_neutralizza_excel_injection(tmp_path):
    """Le descrizioni che iniziano con =,+,-,@ non devono diventare formule."""
    import io
    import pandas as pd
    from decimal import Decimal
    from datetime import date
    from riconciliazione.export import esporta_excel
    from riconciliazione.matching import riconcilia
    from riconciliazione.parsing import FileNormalizzato, Movimento

    def mov(i, desc):
        return Movimento(indice=i, data=date(2026, 3, 1),
                         importo=Decimal("100.00"), descrizione=desc)

    pericolose = ["=1+1", "@SUM(A1:A9)", "+cmd|'/c calc'", "-2+3"]
    a = [mov(i + 1, d) for i, d in enumerate(pericolose)]
    # Importo diverso: nessun match, così restano tutte righe "non trovato".
    b = [Movimento(1, date(2026, 3, 1), Decimal("999.00"), "riga normale")]
    risultato = riconcilia(FileNormalizzato("a", a, {}), FileNormalizzato("b", b, {}))
    buffer = io.BytesIO()
    esporta_excel(risultato, buffer)
    buffer.seek(0)
    df = pd.read_excel(buffer, sheet_name="Non trovati")
    esportate = [v for v in df["Descrizione A"].dropna()]
    # Tutte e quattro presenti (nessuna interpretata/persa come formula) e
    # ciascuna neutralizzata con l'apostrofo iniziale.
    assert len(esportate) == 4
    for valore in esportate:
        assert valore.startswith("'")
