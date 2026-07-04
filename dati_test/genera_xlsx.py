"""Genera la variante Excel (.xlsx) del registro fatture di test.

Eseguire dalla radice del progetto:
    python dati_test/genera_xlsx.py
"""

from pathlib import Path

import pandas as pd

CARTELLA = Path(__file__).parent

df = pd.read_csv(CARTELLA / "registro_fatture.csv")
df["Data"] = pd.to_datetime(df["Data"])
df.to_excel(CARTELLA / "registro_fatture.xlsx", index=False)
print(f"Creato {CARTELLA / 'registro_fatture.xlsx'}")
