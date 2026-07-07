import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from riconciliazione.fatturapa import carica_fatture_xml, carica_origine
from riconciliazione.parsing import ErroreParsing

RADICE = Path(__file__).parent.parent
CARTELLA_XML = RADICE / "dati_test" / "fatture_xml"


def xml_fattura(numero="42", data="2026-03-01", importo="122.00",
                cedente="Mia Azienda Srl", cessionario="Rossi Srl",
                con_totale=True):
    totale = f"<ImportoTotaleDocumento>{importo}</ImportoTotaleDocumento>" if con_totale else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<p:FatturaElettronica versione="FPR12"
    xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2">
  <FatturaElettronicaHeader>
    <CedentePrestatore><DatiAnagrafici>
      <Anagrafica><Denominazione>{cedente}</Denominazione></Anagrafica>
    </DatiAnagrafici></CedentePrestatore>
    <CessionarioCommittente><DatiAnagrafici>
      <Anagrafica><Denominazione>{cessionario}</Denominazione></Anagrafica>
    </DatiAnagrafici></CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali><DatiGeneraliDocumento>
      <TipoDocumento>TD01</TipoDocumento>
      <Data>{data}</Data>
      <Numero>{numero}</Numero>
      {totale}
    </DatiGeneraliDocumento></DatiGenerali>
    <DatiBeniServizi><DatiRiepilogo>
      <ImponibileImporto>100.00</ImponibileImporto>
      <Imposta>22.00</Imposta>
    </DatiRiepilogo></DatiBeniServizi>
  </FatturaElettronicaBody>
</p:FatturaElettronica>
"""


def test_singolo_xml(tmp_path):
    percorso = tmp_path / "fattura.xml"
    percorso.write_text(xml_fattura(), encoding="utf-8")
    file = carica_fatture_xml(percorso)
    assert len(file.movimenti) == 1
    movimento = file.movimenti[0]
    assert movimento.importo == Decimal("122.00")
    assert movimento.data == date(2026, 3, 1)
    assert "42" in movimento.descrizione


def test_cartella_e_euristica_fatture_attive(tmp_path):
    # Stesso cedente, cessionari diversi → fatture attive: la controparte
    # è il cessionario e il cedente NON compare nella descrizione.
    for n, cliente in [("1", "Rossi Srl"), ("2", "Verdi Spa"), ("3", "Bianchi Snc")]:
        (tmp_path / f"f{n}.xml").write_text(
            xml_fattura(numero=n, cessionario=cliente), encoding="utf-8")
    file = carica_fatture_xml(tmp_path)
    assert len(file.movimenti) == 3
    descrizioni = " | ".join(m.descrizione for m in file.movimenti)
    assert "Rossi" in descrizioni and "Verdi" in descrizioni
    assert "Mia Azienda" not in descrizioni


def test_euristica_fatture_passive(tmp_path):
    for n, fornitore in [("1", "Fornitore Uno"), ("2", "Fornitore Due")]:
        (tmp_path / f"f{n}.xml").write_text(
            xml_fattura(numero=n, cedente=fornitore, cessionario="Mia Azienda Srl"),
            encoding="utf-8")
    file = carica_fatture_xml(tmp_path)
    descrizioni = " | ".join(m.descrizione for m in file.movimenti)
    assert "Fornitore Uno" in descrizioni
    assert "Mia Azienda" not in descrizioni


def test_importo_da_riepilogo_quando_manca_totale(tmp_path):
    (tmp_path / "f.xml").write_text(xml_fattura(con_totale=False), encoding="utf-8")
    file = carica_fatture_xml(tmp_path)
    assert file.movimenti[0].importo == Decimal("122.00")


def test_zip(tmp_path):
    archivio = tmp_path / "fatture.zip"
    with zipfile.ZipFile(archivio, "w") as z:
        z.writestr("f1.xml", xml_fattura(numero="1", cessionario="Rossi Srl"))
        z.writestr("f2.xml", xml_fattura(numero="2", cessionario="Verdi Spa"))
    file = carica_fatture_xml(archivio)
    assert len(file.movimenti) == 2
    assert file.percorso == str(archivio)


def test_zip_con_file_di_servizio_macos(tmp_path):
    # Gli zip creati su Mac contengono __MACOSX/._*.xml: vanno ignorati
    # senza generare avvisi confusi.
    archivio = tmp_path / "fatture.zip"
    with zipfile.ZipFile(archivio, "w") as z:
        z.writestr("f1.xml", xml_fattura(numero="1"))
        z.writestr("__MACOSX/._f1.xml", b"\x00\x05\x16\x07spazzatura binaria")
    file = carica_fatture_xml(archivio)
    assert len(file.movimenti) == 1
    assert file.avvisi == []


def test_p7m_best_effort(tmp_path):
    # Simula la busta CAdES: XML contiguo con spazzatura binaria attorno.
    xml = xml_fattura().encode()
    (tmp_path / "f.xml.p7m").write_bytes(b"\x30\x82\x99\x01FIRMA" + xml + b"\x00\x01FINE")
    file = carica_fatture_xml(tmp_path / "f.xml.p7m")
    assert file.movimenti[0].importo == Decimal("122.00")


def test_xml_non_fattura_ignorato_con_avviso(tmp_path):
    (tmp_path / "buona.xml").write_text(xml_fattura(), encoding="utf-8")
    (tmp_path / "altro.xml").write_text("<radice><dato>1</dato></radice>", encoding="utf-8")
    file = carica_fatture_xml(tmp_path)
    assert len(file.movimenti) == 1
    assert any("altro.xml" in avviso for avviso in file.avvisi)


def test_nessuna_fattura_valida(tmp_path):
    (tmp_path / "rotto.xml").write_text("<non chiuso", encoding="utf-8")
    with pytest.raises(ErroreParsing, match="Nessuna fattura"):
        carica_fatture_xml(tmp_path)


def test_cartella_vuota(tmp_path):
    with pytest.raises(ErroreParsing, match="Nessun file"):
        carica_fatture_xml(tmp_path)


def test_fixture_repository():
    file = carica_fatture_xml(CARTELLA_XML)
    assert len(file.movimenti) == 10
    per_numero = {m.riga_originale["Numero"]: m for m in file.movimenti}
    assert per_numero["2026/101"].importo == Decimal("1220.00")
    assert "Rossi" in per_numero["2026/101"].descrizione


def test_carica_origine_instrada():
    tabellare = carica_origine(RADICE / "dati_test" / "registro_fatture.csv")
    xml = carica_origine(CARTELLA_XML)
    assert len(tabellare.movimenti) == len(xml.movimenti) == 10


def test_carica_origine_rifiuta_mappa_su_xml():
    with pytest.raises(ErroreParsing, match="mappatura"):
        carica_origine(CARTELLA_XML, {"importo": "X"})
