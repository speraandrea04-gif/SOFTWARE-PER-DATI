@echo off
setlocal
title Riconciliazione Dati - avvio
cd /d "%~dp0"

echo ==================================================
echo    TOOL DI RICONCILIAZIONE - AVVIO
echo ==================================================
echo.

echo [Passo 1 di 3] Controllo di Python...
python -c "print('ok')" >nul 2>nul
if errorlevel 1 (
    echo.
    echo   Python non risulta installato su questo computer.
    echo.
    echo   1. Vai su  https://www.python.org/downloads/  e scarica Python
    echo   2. Durante l'installazione SPUNTA la casella "Add Python to PATH"
    echo   3. Chiudi questa finestra e rifai doppio click su AVVIA_WINDOWS.bat
    echo.
    pause
    exit /b 1
)
echo   Python trovato.
echo.

echo [Passo 2 di 3] Installazione componenti.
echo   La PRIMA volta servono alcuni minuti e vedrai scorrere del testo:
echo   e' normale, NON chiudere la finestra.
echo.
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   Errore durante l'installazione dei componenti.
    echo   Fai uno screenshot di questa finestra e inviamelo in chat.
    echo.
    pause
    exit /b 1
)
echo.

echo [Passo 3 di 3] Avvio del programma...
echo   Tra qualche secondo il browser si apre da solo.
echo   Se non succede, apri tu il browser e vai su:  http://127.0.0.1:8000
echo.
echo   Per FERMARE il programma: chiudi questa finestra.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"
python -m uvicorn riconciliazione.web:app --port 8000
echo.
echo Il programma si e' fermato. Puoi chiudere questa finestra.
pause
