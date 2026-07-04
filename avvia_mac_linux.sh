#!/usr/bin/env bash
# Avvio del tool di riconciliazione (Mac / Linux).
# Da terminale:  bash avvia_mac_linux.sh
# Per fermarlo: Ctrl+C nel terminale.

set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python non è installato. Scaricalo da https://www.python.org/downloads/"
    exit 1
fi

echo "Installazione componenti (solo la prima volta può richiedere qualche minuto)..."
python3 -m pip install --quiet -r requirements.txt

echo "Avvio in corso... tra qualche secondo si apre il browser."
echo "Per FERMARE il programma: premi Ctrl+C in questa finestra."
( sleep 3
  if command -v open >/dev/null 2>&1; then open http://127.0.0.1:8000
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8000
  fi ) &
python3 -m uvicorn riconciliazione.web:app --port 8000
