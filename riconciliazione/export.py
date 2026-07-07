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

# Caratteri che, come primo carattere di una cella, farebbero interpretare
# il testo come formula quando il file viene aperto in Excel/LibreOffice
# (CSV/Excel injection). Le descrizioni provengono da file esterni, quindi
# vanno neutralizzate anteponendo un apostrofo (marcatore di testo).
_PREFISSI_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _neutralizza_formula(valore):
    """Antepone un apostrofo alle stringhe che Excel leggerebbe come formula."""
    if isinstance(valore, str) and valore[:1] in _PREFISSI_FORMULA:
        return "'" + valore
    return valore


def aggrega_lato(movimenti) -> dict:
    """Riduce uno o più movimenti dello stesso lato a valori mostrabili.

    Con più movimenti (pagamento cumulativo) le righe e le date vengono
    concatenate, gli importi sommati, le descrizioni unite.
    """
    if not movimenti:
        return {"riga": None, "data": None, "importo": None, "descrizione": None}
    return {
        "riga": "+".join(str(m.indice) for m in movimenti),
        "data": ", ".join(m.data.strftime("%d/%m/%Y") for m in movimenti if m.data) or None,
        "importo": float(sum(m.importo for m in movimenti)),
        "descrizione": " + ".join(m.descrizione for m in movimenti if m.descrizione) or None,
    }


def _riga_export_sicura(abbinamento: Abbinamento) -> dict:
    """Come _riga_export ma con le celle testuali neutralizzate per Excel."""
    riga = _riga_export(abbinamento)
    for colonna in ("Descrizione A", "Descrizione B", "Dettagli", "Esito"):
        riga[colonna] = _neutralizza_formula(riga[colonna])
    return riga


def _riga_export(abbinamento: Abbinamento) -> dict:
    lato_a = aggrega_lato(abbinamento.movimenti_a)
    lato_b = aggrega_lato(abbinamento.movimenti_b)
    con_match = bool(abbinamento.movimenti_a and abbinamento.movimenti_b)
    return {
        "Esito": ETICHETTE[abbinamento.categoria],
        "Riga A": lato_a["riga"],
        "Data A": lato_a["data"],
        "Importo A": lato_a["importo"],
        "Descrizione A": lato_a["descrizione"],
        "Riga B": lato_b["riga"],
        "Data B": lato_b["data"],
        "Importo B": lato_b["importo"],
        "Descrizione B": lato_b["descrizione"],
        "Δ Importo": float(abbinamento.differenza_importo)
                     if abbinamento.differenza_importo is not None else None,
        "Δ Giorni": abbinamento.differenza_giorni,
        "Confidenza": abbinamento.confidenza if con_match else None,
        "Dettagli": "; ".join(abbinamento.dettagli),
    }


def esporta_excel(risultato: RisultatoRiconciliazione, percorso) -> object:
    """Scrive un file .xlsx con un foglio di riepilogo e un foglio per categoria.

    `percorso` può essere un path oppure un buffer binario (es. io.BytesIO).
    """
    if isinstance(percorso, (str, Path)):
        percorso = Path(percorso)

    conteggi = risultato.conteggi
    riepilogo = pd.DataFrame([
        {"Voce": "File A", "Valore": _neutralizza_formula(risultato.file_a.percorso)},
        {"Voce": "File B", "Valore": _neutralizza_formula(risultato.file_b.percorso)},
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
            righe = [_riga_export_sicura(ab) for ab in abbinamenti]
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
