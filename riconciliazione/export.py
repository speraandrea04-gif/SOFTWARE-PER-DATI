"""Esportazione dei risultati della riconciliazione in un file Excel."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .matching import Abbinamento, Categoria, RisultatoRiconciliazione

ETICHETTE = {
    Categoria.RICONCILIATO: "✅ Riconciliato",
    Categoria.DISCREPANZA: "⚠️ Discrepanza",
    Categoria.NON_TROVATO: "❌ Non trovato",
}

COLONNE_EXPORT = [
    "Esito", "Riga A", "Data A", "Importo A", "Descrizione A",
    "Riga B", "Data B", "Importo B", "Descrizione B",
    "Δ Importo", "Δ Giorni", "Confidenza", "Dettagli",
]


def _riga_export(abbinamento: Abbinamento) -> dict:
    mov_a, mov_b = abbinamento.movimento_a, abbinamento.movimento_b
    return {
        "Esito": ETICHETTE[abbinamento.categoria],
        "Riga A": mov_a.indice if mov_a else None,
        "Data A": mov_a.data.strftime("%d/%m/%Y") if mov_a and mov_a.data else None,
        "Importo A": float(mov_a.importo) if mov_a else None,
        "Descrizione A": mov_a.descrizione if mov_a else None,
        "Riga B": mov_b.indice if mov_b else None,
        "Data B": mov_b.data.strftime("%d/%m/%Y") if mov_b and mov_b.data else None,
        "Importo B": float(mov_b.importo) if mov_b else None,
        "Descrizione B": mov_b.descrizione if mov_b else None,
        "Δ Importo": float(abbinamento.differenza_importo)
                     if abbinamento.differenza_importo is not None else None,
        "Δ Giorni": abbinamento.differenza_giorni,
        "Confidenza": abbinamento.confidenza if abbinamento.movimento_a and abbinamento.movimento_b else None,
        "Dettagli": "; ".join(abbinamento.dettagli),
    }


def esporta_excel(risultato: RisultatoRiconciliazione, percorso: str | Path) -> Path:
    """Scrive un file .xlsx con un foglio di riepilogo e un foglio per categoria."""
    percorso = Path(percorso)

    conteggi = risultato.conteggi
    riepilogo = pd.DataFrame([
        {"Voce": "File A", "Valore": risultato.file_a.percorso},
        {"Voce": "File B", "Valore": risultato.file_b.percorso},
        {"Voce": "Movimenti file A", "Valore": len(risultato.file_a.movimenti)},
        {"Voce": "Movimenti file B", "Valore": len(risultato.file_b.movimenti)},
        {"Voce": ETICHETTE[Categoria.RICONCILIATO], "Valore": conteggi["riconciliato"]},
        {"Voce": ETICHETTE[Categoria.DISCREPANZA], "Valore": conteggi["discrepanza"]},
        {"Voce": ETICHETTE[Categoria.NON_TROVATO], "Valore": conteggi["non_trovato"]},
        {"Voce": "Tolleranza importo (€)", "Valore": float(risultato.config.tolleranza_importo)},
        {"Voce": "Tolleranza data (giorni)", "Valore": risultato.config.tolleranza_giorni},
    ])

    fogli = {
        "Riconciliati": risultato.per_categoria(Categoria.RICONCILIATO),
        "Discrepanze": risultato.per_categoria(Categoria.DISCREPANZA),
        "Non trovati": risultato.per_categoria(Categoria.NON_TROVATO),
    }

    with pd.ExcelWriter(percorso, engine="openpyxl") as writer:
        riepilogo.to_excel(writer, sheet_name="Riepilogo", index=False)
        for nome, abbinamenti in fogli.items():
            righe = [_riga_export(ab) for ab in abbinamenti]
            df = pd.DataFrame(righe, columns=COLONNE_EXPORT)
            df.to_excel(writer, sheet_name=nome, index=False)

        # Larghezza colonne leggibile.
        for foglio in writer.sheets.values():
            for colonna in foglio.columns:
                larghezza = max((len(str(c.value)) for c in colonna if c.value is not None),
                                default=8)
                lettera = colonna[0].column_letter
                foglio.column_dimensions[lettera].width = min(max(larghezza + 2, 10), 60)

    return percorso
