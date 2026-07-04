"""Interfaccia a riga di comando per la riconciliazione.

Esempio:
    python -m riconciliazione.cli dati_test/estratto_conto.csv dati_test/registro_fatture.csv
    python -m riconciliazione.cli A.csv B.xlsx --tolleranza-giorni 5 --excel risultato.xlsx
    python -m riconciliazione.cli A.csv B.csv --colonne-a "importo=Importo,data=Data op."
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

from .export import ETICHETTE, esporta_excel
from .matching import Abbinamento, Categoria, ConfigMatching, riconcilia
from .parsing import ErroreParsing, carica_file


def _parse_mappa_colonne(testo: str | None) -> dict | None:
    """Converte "data=Data op.,importo=Importo,descrizione=Causale|Numero"
    in una mappa colonne. La descrizione ammette più colonne separate da '|'."""
    if not testo:
        return None
    mappa: dict = {}
    for pezzo in testo.split(","):
        if "=" not in pezzo:
            raise argparse.ArgumentTypeError(
                f"Mappatura non valida: '{pezzo}'. Formato atteso campo=colonna"
            )
        campo, colonna = pezzo.split("=", 1)
        campo = campo.strip().lower()
        if campo not in ("data", "importo", "descrizione"):
            raise argparse.ArgumentTypeError(
                f"Campo sconosciuto '{campo}': usare data, importo o descrizione"
            )
        if campo == "descrizione":
            mappa[campo] = [c.strip() for c in colonna.split("|") if c.strip()]
        else:
            mappa[campo] = colonna.strip()
    return mappa


def _parse_decimale(testo: str) -> Decimal:
    try:
        valore = Decimal(testo.replace(",", "."))
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"Numero non valido: '{testo}'")
    if valore < 0:
        raise argparse.ArgumentTypeError("La tolleranza non può essere negativa")
    return valore


def _descrivi_movimento(abbinamento: Abbinamento, lato: str) -> str:
    mov = abbinamento.movimento_a if lato == "A" else abbinamento.movimento_b
    if mov is None:
        return "—"
    data = mov.data.strftime("%d/%m/%Y") if mov.data else "??/??/????"
    descrizione = mov.descrizione[:45] + ("…" if len(mov.descrizione) > 45 else "")
    return f"[{lato}{mov.indice:>3}] {data}  {mov.importo:>12}  {descrizione}"


def _stampa_risultato(risultato, dettaglio: bool) -> None:
    file_a, file_b = risultato.file_a, risultato.file_b
    print("=" * 78)
    print("RICONCILIAZIONE")
    print(f"  File A: {file_a.percorso}  ({len(file_a.movimenti)} movimenti)")
    print(f"  File B: {file_b.percorso}  ({len(file_b.movimenti)} movimenti)")
    for nome, file in (("A", file_a), ("B", file_b)):
        mappa = file.mappa_colonne
        print(f"  Colonne {nome}: data={mappa['data']!r}, importo={mappa['importo']!r}, "
              f"descrizione={mappa['descrizione']!r}")
    avvisi = file_a.avvisi + file_b.avvisi
    if avvisi:
        print("  Avvisi:")
        for avviso in avvisi:
            print(f"    - {avviso}")
    print("-" * 78)

    conteggi = risultato.conteggi
    non_trovati = risultato.per_categoria(Categoria.NON_TROVATO)
    solo_a = sum(1 for ab in non_trovati if ab.lato_mancante == "B")
    solo_b = len(non_trovati) - solo_a
    print(f"  {ETICHETTE[Categoria.RICONCILIATO]}: {conteggi['riconciliato']}")
    print(f"  {ETICHETTE[Categoria.DISCREPANZA]}: {conteggi['discrepanza']}")
    print(f"  {ETICHETTE[Categoria.NON_TROVATO]}: {conteggi['non_trovato']}"
          f"  (solo nel file A: {solo_a}, solo nel file B: {solo_b})")
    print("=" * 78)

    if not dettaglio:
        return

    for categoria in Categoria:
        abbinamenti = risultato.per_categoria(categoria)
        if not abbinamenti:
            continue
        print(f"\n{ETICHETTE[categoria]} ({len(abbinamenti)})")
        for ab in abbinamenti:
            print(f"  {_descrivi_movimento(ab, 'A')}")
            print(f"  {_descrivi_movimento(ab, 'B')}")
            if ab.movimento_a and ab.movimento_b:
                print(f"        confidenza {ab.confidenza:.2f} "
                      f"(importo {ab.score_importo:.2f}, data {ab.score_data:.2f}, "
                      f"testo {ab.score_testo:.2f})")
            for dettaglio_riga in ab.dettagli:
                print(f"        → {dettaglio_riga}")
            print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="riconciliazione",
        description="Confronta due file (estratto conto e registro fatture) "
                    "e classifica le righe in riconciliate, discrepanze e non trovate.",
    )
    parser.add_argument("file_a", help="Primo file (es. estratto conto), CSV o .xlsx")
    parser.add_argument("file_b", help="Secondo file (es. registro fatture), CSV o .xlsx")
    parser.add_argument("--tolleranza-importo", type=_parse_decimale, default=Decimal("0.01"),
                        metavar="EURO", help="Tolleranza sull'importo in euro (default: 0.01)")
    parser.add_argument("--tolleranza-giorni", type=int, default=3, metavar="N",
                        help="Tolleranza sulla data in giorni (default: 3)")
    parser.add_argument("--colonne-a", type=_parse_mappa_colonne, default=None,
                        metavar="MAPPA",
                        help="Mappatura manuale colonne file A, es. "
                             "\"data=Data operazione,importo=Importo,descrizione=Causale|Numero\"")
    parser.add_argument("--colonne-b", type=_parse_mappa_colonne, default=None,
                        metavar="MAPPA", help="Mappatura manuale colonne file B")
    parser.add_argument("--valore-assoluto", action="store_true",
                        help="Confronta gli importi in valore assoluto "
                             "(es. estratto conto con uscite negative)")
    parser.add_argument("--excel", metavar="FILE.xlsx", default=None,
                        help="Esporta i risultati in un file Excel")
    parser.add_argument("--sintesi", action="store_true",
                        help="Stampa solo i conteggi, senza il dettaglio riga per riga")
    args = parser.parse_args(argv)

    if args.tolleranza_giorni < 0:
        parser.error("--tolleranza-giorni non può essere negativa")

    config = ConfigMatching(
        tolleranza_importo=args.tolleranza_importo,
        tolleranza_giorni=args.tolleranza_giorni,
        valore_assoluto=args.valore_assoluto,
    )

    try:
        file_a = carica_file(args.file_a, args.colonne_a)
        file_b = carica_file(args.file_b, args.colonne_b)
    except ErroreParsing as errore:
        print(f"Errore: {errore}", file=sys.stderr)
        return 1

    risultato = riconcilia(file_a, file_b, config)
    _stampa_risultato(risultato, dettaglio=not args.sintesi)

    if args.excel:
        percorso = esporta_excel(risultato, args.excel)
        print(f"\nRisultati esportati in: {percorso}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
