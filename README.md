# Tool di Riconciliazione Dati — MVP

Confronta due fonti (es. estratto conto bancario e registro fatture/contabilità) e classifica ogni riga in tre categorie:

- ✅ **Riconciliato** — importo e data entro tolleranza, oppure **pagamento cumulativo** (un movimento che salda più fatture, o una fattura pagata a rate)
- ⚠️ **Discrepanza** — match credibile ma con importo o data fuori tolleranza (con dettaglio della differenza)
- ❌ **Non trovato** — nessuna corrispondenza (righe presenti solo in una delle due fonti)

Formati supportati per ciascun lato: **CSV**, **Excel (.xlsx)** e **fatture elettroniche FatturaPA** (file .xml dello SDI, anche più file, una cartella o uno .zip; .p7m firmati con estrazione best-effort).

## Stato del progetto

Fase 1 completa (parsing, matching, Excel, CLI, web) + Ondata 1: import fatture elettroniche XML, pagamenti cumulativi 1-a-molti, report PDF per il cliente finale.

## Installazione

```bash
pip install -r requirements.txt
```

Richiede Python 3.10+.

## Uso — interfaccia web (consigliato)

Avvio rapido senza terminale: doppio click su **`AVVIA_WINDOWS.bat`** (Windows) oppure `bash avvia_mac_linux.sh` (Mac/Linux). Lo script installa il necessario, avvia il programma e apre il browser da solo. Nota: l'indirizzo `http://127.0.0.1:8000` funziona solo mentre il programma è in esecuzione sul proprio computer.

Avvio manuale:

```bash
uvicorn riconciliazione.web:app
```

Poi aprire **http://127.0.0.1:8000** nel browser:

1. trascina (o seleziona) i file dei due lati — CSV, `.xlsx`, oppure le fatture elettroniche XML (anche più file insieme o uno .zip);
2. regola se serve le tolleranze (default ±0.01 € e ±3 giorni);
3. premi **Riconcilia**: compaiono i conteggi delle tre categorie e la tabella riga per riga, filtrabile cliccando sui riquadri dei conteggi;
4. **Scarica Excel** esporta il risultato completo; **Report PDF** genera il documento riepilogativo da girare al cliente (non trovati e discrepanze in evidenza).

Nelle opzioni avanzate si possono mappare manualmente le colonne se il riconoscimento automatico sbaglia (stessa sintassi della CLI). Nessun dato viene salvato: tutto resta in memoria.

## Uso da riga di comando

```bash
# Caso base (tolleranze di default: ±0.01 € e ±3 giorni)
python -m riconciliazione.cli dati_test/estratto_conto.csv dati_test/registro_fatture.csv

# Con tolleranze personalizzate, esportazione Excel e report PDF
python -m riconciliazione.cli A.csv B.xlsx \
    --tolleranza-importo 0.05 --tolleranza-giorni 5 \
    --excel risultato.xlsx --pdf report.pdf

# Estratto conto vs cartella (o .zip) di fatture elettroniche XML
python -m riconciliazione.cli estratto.csv fatture_xml/

# Solo il riepilogo dei conteggi
python -m riconciliazione.cli A.csv B.csv --sintesi

# Mappatura manuale delle colonne (se il riconoscimento automatico sbaglia)
python -m riconciliazione.cli A.csv B.csv \
    --colonne-a "data=Data operazione,importo=Importo,descrizione=Causale" \
    --colonne-b "data=Data,importo=Totale,descrizione=Cliente|Numero Fattura"

# Estratto conto con uscite negative vs registro con importi positivi
python -m riconciliazione.cli A.csv B.csv --valore-assoluto
```

Formati supportati: CSV (separatore `,` `;` tab `|`, rilevato automaticamente; codifica UTF-8 o Latin-1) ed Excel `.xlsx`.

## Come funziona il matching

1. **Normalizzazione**: ogni file viene ridotto a tre campi per riga — data, importo, descrizione. Le colonne vengono individuate automaticamente dalle intestazioni (italiano e inglese) con fallback sull'analisi del contenuto; in alternativa si mappano a mano con `--colonne-a/b`. Vengono gestiti formati data comuni (`02/03/2026`, `2026-03-02`, …) e formati importo italiani/internazionali (`1.234,56`, `1234.56`, `€ 100`, negativi contabili `(50,00)`).
2. **Punteggio**: per ogni coppia di righe (A, B) si calcola una confidenza pesata: importo 50%, data 30%, similarità testuale della descrizione 20% (fuzzy matching con rapidfuzz).
3. **Assegnazione 1:1**: le coppie sopra la soglia di credibilità vengono assegnate in ordine di confidenza decrescente; ogni riga può essere abbinata al massimo una volta. La similarità testuale risolve i casi ambigui (stesso importo, date vicine).
4. **Pagamenti cumulativi**: sulle righe rimaste senza match si cercano gruppi in cui un movimento corrisponde alla somma di più righe dell'altro file (e viceversa), entro la tolleranza di importo, in una finestra di 90 giorni e con coerenza testuale minima. Disattivabile (`--no-gruppi` o checkbox nella web UI).
5. **Classificazione**: importo e data entrambi entro tolleranza (o gruppo con somma esatta) → riconciliato; match credibile ma un criterio fuori tolleranza → discrepanza con dettaglio; nessun match credibile → non trovato.

## Assunzioni fatte (da validare)

- **Fatture XML**: la controparte viene dedotta automaticamente (fatture attive → cessionario, passive → cedente) osservando quale denominazione è costante nel lotto; con un solo file compaiono entrambe.
- **Segno degli importi**: di default gli importi si confrontano con il segno; il flag `--valore-assoluto` gestisce il caso estratto conto con uscite negative.
- **Righe senza importo interpretabile**: vengono scartate con un avviso esplicito (non bloccano l'elaborazione).
- **Date ambigue**: si assume la convenzione giorno/mese (formato italiano).
- **Soglia di credibilità del match**: confidenza ≥ 0.55 (sotto questa soglia una coppia non viene proposta nemmeno come discrepanza).

## Test

```bash
python -m pytest tests/ -v
```

I dati fittizi in `dati_test/` coprono: match esatti, differenze di centesimi, date sfasate, righe presenti in un solo file, importi duplicati disambiguati dal testo, righe malformate. `dati_test/genera_xlsx.py` rigenera la variante Excel del registro.

## Struttura

```
riconciliazione/
  parsing.py    # caricamento CSV/Excel, riconoscimento colonne, normalizzazione
  fatturapa.py  # import fatture elettroniche XML (SDI), zip/cartelle/p7m
  matching.py   # motore di confronto, pagamenti cumulativi, classificazione
  export.py     # esportazione risultati in Excel
  report.py     # report PDF per il cliente finale
  cli.py        # interfaccia a riga di comando
  web.py        # API FastAPI (upload, risultati, download Excel/PDF)
  static/       # pagina web (HTML/CSS/JS vanilla)
dati_test/      # file fittizi per la validazione (CSV, xlsx, fatture XML)
tests/          # suite pytest (unit + end-to-end, API incluse)
```
