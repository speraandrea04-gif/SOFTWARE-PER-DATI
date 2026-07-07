"""Report PDF della riconciliazione, pensato per essere girato al cliente.

Struttura: intestazione con file e parametri, riepilogo dei conteggi,
poi le sezioni in ordine di urgenza per chi legge: righe non trovate,
discrepanze, righe riconciliate (in forma compatta).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .export import aggrega_lato
from .matching import Categoria, RisultatoRiconciliazione

# Colori coerenti con l'interfaccia web.
_VERDE = colors.HexColor("#1a7f4b")
_GIALLO = colors.HexColor("#9a6700")
_ROSSO = colors.HexColor("#b3261e")
_GRIGIO_RIGA = colors.HexColor("#f1f3f4")
_BORDO = colors.HexColor("#dadce0")

_TITOLI_SEZIONE = {
    Categoria.NON_TROVATO: ("Righe non trovate", _ROSSO),
    Categoria.DISCREPANZA: ("Discrepanze", _GIALLO),
    Categoria.RICONCILIATO: ("Righe riconciliate", _VERDE),
}


def _stili():
    base = getSampleStyleSheet()
    return {
        "titolo": ParagraphStyle("titolo", parent=base["Title"], fontSize=18,
                                 spaceAfter=2 * mm),
        "sottotitolo": ParagraphStyle("sottotitolo", parent=base["Normal"],
                                      textColor=colors.grey, spaceAfter=6 * mm),
        "sezione": ParagraphStyle("sezione", parent=base["Heading2"], fontSize=13,
                                  spaceBefore=8 * mm, spaceAfter=2 * mm),
        "cella": ParagraphStyle("cella", parent=base["Normal"], fontSize=8, leading=10),
        "cella_grigia": ParagraphStyle("cella_grigia", parent=base["Normal"],
                                       fontSize=8, leading=10, textColor=colors.grey),
    }


def _euro(valore) -> str:
    if valore is None:
        return "—"
    testo = f"{valore:,.2f}"
    testo = testo.replace(",", "@").replace(".", ",").replace("@", ".")
    return f"{testo} €"


def _tabella(intestazioni: list[str], righe: list[list], larghezze: list[float],
             stili) -> Table:
    dati = [[Paragraph(f"<b>{escape(voce)}</b>", stili["cella"]) for voce in intestazioni]]
    for riga in righe:
        dati.append([voce if isinstance(voce, Paragraph)
                     else Paragraph(escape(str(voce)) if voce not in (None, "") else "—",
                                    stili["cella"])
                     for voce in riga])
    tabella = Table(dati, colWidths=[l * mm for l in larghezze], repeatRows=1)
    tabella.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _GRIGIO_RIGA),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tabella


def _sezione_non_trovati(risultato, elementi, stili) -> None:
    abbinamenti = risultato.per_categoria(Categoria.NON_TROVATO)
    if not abbinamenti:
        return
    titolo, colore = _TITOLI_SEZIONE[Categoria.NON_TROVATO]
    for lato, etichetta in (("B", f"Presenti solo in «{escape(risultato.file_a.percorso)}»"),
                            ("A", f"Presenti solo in «{escape(risultato.file_b.percorso)}»")):
        gruppo = [ab for ab in abbinamenti if ab.lato_mancante == lato]
        if not gruppo:
            continue
        stile_sezione = ParagraphStyle("s", parent=stili["sezione"], textColor=colore)
        elementi.append(Paragraph(f"{titolo} — {etichetta} ({len(gruppo)})", stile_sezione))
        righe = []
        for ab in gruppo:
            dati = aggrega_lato(ab.movimenti_a or ab.movimenti_b)
            righe.append([dati["data"], _euro(dati["importo"]), dati["descrizione"]])
        elementi.append(_tabella(["Data", "Importo", "Descrizione"],
                                 righe, [22, 26, 122], stili))


def _sezione_discrepanze(risultato, elementi, stili) -> None:
    abbinamenti = risultato.per_categoria(Categoria.DISCREPANZA)
    if not abbinamenti:
        return
    titolo, colore = _TITOLI_SEZIONE[Categoria.DISCREPANZA]
    stile_sezione = ParagraphStyle("s", parent=stili["sezione"], textColor=colore)
    elementi.append(Paragraph(f"{titolo} ({len(abbinamenti)})", stile_sezione))
    righe = []
    for ab in abbinamenti:
        lato_a = aggrega_lato(ab.movimenti_a)
        lato_b = aggrega_lato(ab.movimenti_b)
        righe.append([
            lato_a["data"], _euro(lato_a["importo"]), lato_a["descrizione"],
            lato_b["data"], _euro(lato_b["importo"]), lato_b["descrizione"],
            Paragraph(escape("; ".join(ab.dettagli)), stili["cella_grigia"]),
        ])
    elementi.append(_tabella(
        ["Data A", "Importo A", "Descrizione A", "Data B", "Importo B",
         "Descrizione B", "Differenza"],
        righe, [18, 20, 33, 18, 20, 33, 28], stili))


def _sezione_riconciliati(risultato, elementi, stili) -> None:
    abbinamenti = risultato.per_categoria(Categoria.RICONCILIATO)
    if not abbinamenti:
        return
    titolo, colore = _TITOLI_SEZIONE[Categoria.RICONCILIATO]
    stile_sezione = ParagraphStyle("s", parent=stili["sezione"], textColor=colore)
    elementi.append(Paragraph(f"{titolo} ({len(abbinamenti)})", stile_sezione))
    righe = []
    for ab in abbinamenti:
        lato_a = aggrega_lato(ab.movimenti_a)
        lato_b = aggrega_lato(ab.movimenti_b)
        nota = "; ".join(ab.dettagli) if ab.dettagli else ""
        righe.append([
            lato_a["data"], _euro(lato_a["importo"]), lato_a["descrizione"],
            Paragraph(escape(lato_b["descrizione"] or "—")
                      + (f"<br/><font color='grey'>{escape(nota)}</font>" if nota else ""),
                      stili["cella"]),
        ])
    elementi.append(_tabella(
        ["Data", "Importo", "Movimento", "Corrispondenza trovata"],
        righe, [20, 24, 58, 68], stili))


def genera_pdf(risultato: RisultatoRiconciliazione, destinazione) -> object:
    """Scrive il report PDF. `destinazione` può essere un path o un buffer."""
    if isinstance(destinazione, (str, Path)):
        destinazione = str(destinazione)
    documento = SimpleDocTemplate(destinazione, pagesize=A4,
                                  leftMargin=15 * mm, rightMargin=15 * mm,
                                  topMargin=15 * mm, bottomMargin=15 * mm,
                                  title="Report di riconciliazione")
    stili = _stili()
    conteggi = risultato.conteggi
    totale = len(risultato.abbinamenti)

    elementi = [
        Paragraph("Report di riconciliazione", stili["titolo"]),
        Paragraph(
            f"Generato il {date.today():%d/%m/%Y} — "
            f"File A: {escape(risultato.file_a.percorso)} "
            f"({len(risultato.file_a.movimenti)} movimenti) · "
            f"File B: {escape(risultato.file_b.percorso)} "
            f"({len(risultato.file_b.movimenti)} movimenti) · "
            f"Tolleranze: ±{risultato.config.tolleranza_importo} € / "
            f"±{risultato.config.tolleranza_giorni} giorni",
            stili["sottotitolo"]),
    ]

    riepilogo = Table(
        [[Paragraph(f"<b>{conteggi['riconciliato']}</b> riconciliate", stili["cella"]),
          Paragraph(f"<b>{conteggi['discrepanza']}</b> discrepanze", stili["cella"]),
          Paragraph(f"<b>{conteggi['non_trovato']}</b> non trovate", stili["cella"]),
          Paragraph(f"<b>{totale}</b> righe totali", stili["cella"])]],
        colWidths=[45 * mm, 45 * mm, 45 * mm, 45 * mm])
    riepilogo.setStyle(TableStyle([
        ("TEXTCOLOR", (0, 0), (0, 0), _VERDE),
        ("TEXTCOLOR", (1, 0), (1, 0), _GIALLO),
        ("TEXTCOLOR", (2, 0), (2, 0), _ROSSO),
        ("BOX", (0, 0), (-1, -1), 0.4, _BORDO),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, _BORDO),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elementi.append(riepilogo)
    elementi.append(Spacer(1, 4 * mm))

    _sezione_non_trovati(risultato, elementi, stili)
    _sezione_discrepanze(risultato, elementi, stili)
    _sezione_riconciliati(risultato, elementi, stili)

    documento.build(elementi)
    return destinazione
