@echo off
REM Avvio del tool di riconciliazione (Windows).
REM Doppio click su questo file: installa il necessario, apre il browser
REM e avvia il programma. Per fermarlo: chiudere questa finestra.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python non e' installato.
    echo Scaricalo da https://www.python.org/downloads/
    echo IMPORTANTE: durante l'installazione spunta "Add Python to PATH",
    echo poi rilancia questo file.
    echo.
    pause
    exit /b 1
)

echo Installazione componenti (solo la prima volta puo' richiedere qualche minuto)...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo Errore durante l'installazione dei componenti.
    pause
    exit /b 1
)

echo Avvio in corso... tra qualche secondo si apre il browser.
echo Per FERMARE il programma: chiudi questa finestra.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"
python -m uvicorn riconciliazione.web:app --port 8000
pause
