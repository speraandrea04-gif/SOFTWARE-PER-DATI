# Tool di Riconciliazione Dati — MVP (Fase 1)

Confronta due file (es. estratto conto bancario e registro fatture/contabilità) e classifica ogni riga in tre categorie:

- ✅ **Riconciliato** — importo e data entro tolleranza
- ⚠️ **Discrepanza** — match credibile ma con importo o data fuori tolleranza (con dettaglio della differenza)
- ❌ **Non trovato** — nessuna corrispondenza (righe presenti solo in uno dei due file)

## Stato del progetto

Fase 1 completa: parsing dei file, motore di matching, esportazione Excel, CLI, dati di test e interfaccia web.

## Installazione

```bash
pip install -r requirements.txt
```

Richiede Python 3.10+.

## Uso — interfaccia web (consigliato)

```bash
uvicorn riconciliazione.web:app
```

Poi aprire **http://127.0.0.1:8000** nel browser:

1. trascina (o seleziona) i due file — estratto conto e registro fatture, CSV o `.xlsx`;
2. regola se serve le tolleranze (default ±0.01 € e ±3 giorni);
3. premi **Riconcilia**: compaiono i conteggi delle tre categorie e la tabella riga per riga, filtrabile cliccando sui riquadri dei conteggi;
4. **Scarica Excel** esporta il risultato (riepilogo + un foglio per categoria).

Nelle opzioni avanzate si possono mappare manualmente le colonne se il riconoscimento automatico sbaglia (stessa sintassi della CLI). Nessun dato viene salvato: tutto resta in memoria.

## Uso da riga di comando

```bash
# Caso base (tolleranze di default: ±0.01 € e ±3 giorni)
python -m riconciliazione.cli dati_test/estratto_conto.csv dati_test/registro_fatture.csv

# Con tolleranze personalizzate ed esportazione Excel
python -m riconciliazione.cli A.csv B.xlsx \
    --tolleranza-importo 0.05 --tolleranza-giorni 5 --excel risultato.xlsx

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
4. **Classificazione**: importo e data entrambi entro tolleranza → riconciliato; match credibile ma un criterio fuori tolleranza → discrepanza con dettaglio; nessun match credibile → non trovato.

## Assunzioni fatte (da validare)

- **Matching 1:1**: una riga di A si abbina al massimo a una riga di B (niente raggruppamenti tipo "un bonifico salda tre fatture" — eventualmente in Fase 2).
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
  matching.py   # motore di confronto e classificazione
  export.py     # esportazione risultati in Excel
  cli.py        # interfaccia a riga di comando
  web.py        # API FastAPI (upload, risultati, download Excel)
  static/       # pagina web (HTML/CSS/JS vanilla)
dati_test/      # file fittizi per la validazione
tests/          # suite pytest (unit + end-to-end, API incluse)
```
