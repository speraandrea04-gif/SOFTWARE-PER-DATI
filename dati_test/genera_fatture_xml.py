"""Genera le fatture elettroniche XML di test in dati_test/fatture_xml/.

Sono le stesse 10 fatture di registro_fatture.csv in formato FatturaPA,
emesse da "Mia Azienda Srl" verso i vari clienti (fatture attive).

Eseguire dalla radice del progetto:
    python dati_test/genera_fatture_xml.py
"""

from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

CARTELLA = Path(__file__).parent
DESTINAZIONE = CARTELLA / "fatture_xml"

MODELLO = """<?xml version="1.0" encoding="UTF-8"?>
<p:FatturaElettronica versione="FPR12"
    xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">
  <FatturaElettronicaHeader>
    <CedentePrestatore>
      <DatiAnagrafici>
        <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>01234567890</IdCodice></IdFiscaleIVA>
        <Anagrafica><Denominazione>Mia Azienda Srl</Denominazione></Anagrafica>
        <RegimeFiscale>RF01</RegimeFiscale>
      </DatiAnagrafici>
    </CedentePrestatore>
    <CessionarioCommittente>
      <DatiAnagrafici>
        <Anagrafica><Denominazione>{cliente}</Denominazione></Anagrafica>
      </DatiAnagrafici>
    </CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <TipoDocumento>TD01</TipoDocumento>
        <Divisa>EUR</Divisa>
        <Data>{data}</Data>
        <Numero>{numero}</Numero>
        <ImportoTotaleDocumento>{importo}</ImportoTotaleDocumento>
      </DatiGeneraliDocumento>
    </DatiGenerali>
    <DatiBeniServizi>
      <DatiRiepilogo>
        <AliquotaIVA>22.00</AliquotaIVA>
        <ImponibileImporto>{imponibile}</ImponibileImporto>
        <Imposta>{imposta}</Imposta>
      </DatiRiepilogo>
    </DatiBeniServizi>
  </FatturaElettronicaBody>
</p:FatturaElettronica>
"""


def main() -> None:
    DESTINAZIONE.mkdir(exist_ok=True)
    registro = pd.read_csv(CARTELLA / "registro_fatture.csv")
    for indice, riga in registro.iterrows():
        importo = float(riga["Importo (€)"])
        imponibile = round(importo / 1.22, 2)
        numero = riga["Numero Fattura"].replace("FT ", "")
        xml = MODELLO.format(
            cliente=escape(riga["Cliente"]),
            data=riga["Data"],
            numero=numero,
            importo=f"{importo:.2f}",
            imponibile=f"{imponibile:.2f}",
            imposta=f"{importo - imponibile:.2f}",
        )
        nome = f"IT01234567890_{indice + 1:05d}.xml"
        (DESTINAZIONE / nome).write_text(xml, encoding="utf-8")
        print(f"Creata {DESTINAZIONE / nome}")


if __name__ == "__main__":
    main()
