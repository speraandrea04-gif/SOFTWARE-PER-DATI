"""Test dell'API web con il TestClient di FastAPI."""

import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from riconciliazione.web import app

RADICE = Path(__file__).parent.parent
ESTRATTO = RADICE / "dati_test" / "estratto_conto.csv"
REGISTRO = RADICE / "dati_test" / "registro_fatture.csv"
REGISTRO_XLSX = RADICE / "dati_test" / "registro_fatture.xlsx"

client = TestClient(app)


def _upload(percorso_a=ESTRATTO, percorso_b=REGISTRO, **campi):
    files = {
        "file_a": (Path(percorso_a).name, Path(percorso_a).read_bytes()),
        "file_b": (Path(percorso_b).name, Path(percorso_b).read_bytes()),
    }
    return client.post("/api/riconcilia", files=files, data=campi)


def test_pagina_principale():
    risposta = client.get("/")
    assert risposta.status_code == 200
    assert "Riconciliazione" in risposta.text


def test_riconciliazione_base():
    risposta = _upload()
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["conteggi"] == {"riconciliato": 5, "discrepanza": 3, "non_trovato": 4}
    assert corpo["solo_in_a"] == 2 and corpo["solo_in_b"] == 2
    assert corpo["file_a"]["movimenti"] == 10
    assert len(corpo["righe"]) == 12
    riconciliata = next(r for r in corpo["righe"] if r["categoria"] == "riconciliato")
    assert riconciliata["a"]["importo"] and riconciliata["b"]["importo"]
    non_trovata = next(r for r in corpo["righe"] if r["categoria"] == "non_trovato")
    assert (non_trovata["a"] is None) != (non_trovata["b"] is None)


def test_riconciliazione_con_excel_e_tolleranze():
    risposta = _upload(percorso_b=REGISTRO_XLSX,
                       tolleranza_importo="0,05", tolleranza_giorni="10")
    assert risposta.status_code == 200
    corpo = risposta.json()
    # Con tolleranze larghe le discrepanze da 0.02 € e da 9 giorni rientrano.
    assert corpo["conteggi"]["riconciliato"] == 7
    assert corpo["conteggi"]["discrepanza"] == 1


def test_mappatura_manuale_via_api():
    risposta = _upload(colonne_b="data=Data,importo=Importo (€),descrizione=Cliente")
    assert risposta.status_code == 200
    assert risposta.json()["file_b"]["colonne"]["descrizione"] == ["Cliente"]


def test_download_excel():
    identificativo = _upload().json()["id"]
    risposta = client.get(f"/api/risultati/{identificativo}/excel")
    assert risposta.status_code == 200
    assert "spreadsheetml" in risposta.headers["content-type"]
    fogli = pd.read_excel(io.BytesIO(risposta.content), sheet_name=None)
    assert set(fogli) == {"Riepilogo", "Riconciliati", "Discrepanze", "Non trovati"}
    assert len(fogli["Riconciliati"]) == 5


def test_excel_risultato_inesistente():
    risposta = client.get("/api/risultati/inesistente/excel")
    assert risposta.status_code == 404


@pytest.mark.parametrize("campi, frammento", [
    ({"tolleranza_importo": "abc"}, "Tolleranza importo"),
    ({"tolleranza_giorni": "-1"}, "giorni"),
    ({"colonne_a": "campo_strano=X"}, "Mappatura"),
    ({"colonne_a": "importo=Non Esiste"}, "non esiste"),
])
def test_parametri_non_validi(campi, frammento):
    risposta = _upload(**campi)
    assert risposta.status_code == 400
    assert frammento.lower() in risposta.json()["detail"].lower()


def test_file_vuoto():
    risposta = client.post("/api/riconcilia", files={
        "file_a": ("vuoto.csv", b""),
        "file_b": (REGISTRO.name, REGISTRO.read_bytes()),
    })
    assert risposta.status_code == 400
    assert "vuoto" in risposta.json()["detail"]


def test_formato_non_supportato():
    risposta = client.post("/api/riconcilia", files={
        "file_a": ("documento.pdf", b"finto pdf"),
        "file_b": (REGISTRO.name, REGISTRO.read_bytes()),
    })
    assert risposta.status_code == 400
    assert "Formato non supportato" in risposta.json()["detail"]


def test_upload_fatture_xml_multiple():
    xml = sorted((RADICE / "dati_test" / "fatture_xml").glob("*.xml"))
    files = [("file_a", (ESTRATTO.name, ESTRATTO.read_bytes()))]
    files += [("file_b", (p.name, p.read_bytes())) for p in xml]
    risposta = client.post("/api/riconcilia", files=files)
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["conteggi"] == {"riconciliato": 5, "discrepanza": 3, "non_trovato": 4}
    assert corpo["file_b"]["nome"] == "10 fatture elettroniche"


def test_piu_file_tabellari_rifiutati():
    files = [
        ("file_a", (ESTRATTO.name, ESTRATTO.read_bytes())),
        ("file_b", (REGISTRO.name, REGISTRO.read_bytes())),
        ("file_b", ("altro.csv", REGISTRO.read_bytes())),
    ]
    risposta = client.post("/api/riconcilia", files=files)
    assert risposta.status_code == 400
    assert "più file" in risposta.json()["detail"]


def test_pagamenti_cumulativi_via_api():
    cartella = RADICE / "dati_test"
    risposta = client.post("/api/riconcilia", files={
        "file_a": ("estratto.csv", (cartella / "estratto_cumulativo.csv").read_bytes()),
        "file_b": ("registro.csv", (cartella / "registro_cumulativo.csv").read_bytes()),
    })
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["conteggi"]["riconciliato"] == 2
    cumulativa = next(r for r in corpo["righe"] if "+" in str(r["b"] and r["b"]["riga"]))
    assert cumulativa["b"]["importo"] == 1830.0
    assert any("cumulativo" in d for d in cumulativa["dettagli"])


def test_cumulativi_disattivabili_via_api():
    cartella = RADICE / "dati_test"
    risposta = client.post(
        "/api/riconcilia",
        files={
            "file_a": ("estratto.csv", (cartella / "estratto_cumulativo.csv").read_bytes()),
            "file_b": ("registro.csv", (cartella / "registro_cumulativo.csv").read_bytes()),
        },
        data={"cerca_gruppi": "false"},
    )
    assert risposta.json()["conteggi"]["riconciliato"] == 1


def test_download_pdf():
    identificativo = _upload().json()["id"]
    risposta = client.get(f"/api/risultati/{identificativo}/pdf")
    assert risposta.status_code == 200
    assert risposta.headers["content-type"] == "application/pdf"
    assert risposta.content.startswith(b"%PDF")


def test_valore_fuori_scala_non_causa_500():
    csv_anomalo = (b"Data,Causale,Importo\n"
                   b"2026-01-01,riga buona,100.00\n"
                   b"2026-01-02,riga anomala,1E+30\n")
    risposta = client.post("/api/riconcilia", files={
        "file_a": ("anomalo.csv", csv_anomalo),
        "file_b": (REGISTRO.name, REGISTRO.read_bytes()),
    })
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["file_a"]["movimenti"] == 1
    assert any("scartata" in avviso for avviso in corpo["file_a"]["avvisi"])


def test_fatture_omonime_non_si_sovrascrivono():
    xml = sorted((RADICE / "dati_test" / "fatture_xml").glob("*.xml"))[:2]
    files = [("file_a", (ESTRATTO.name, ESTRATTO.read_bytes()))]
    files += [("file_b", ("fattura.xml", p.read_bytes())) for p in xml]
    risposta = client.post("/api/riconcilia", files=files)
    assert risposta.status_code == 200
    assert risposta.json()["file_b"]["movimenti"] == 2


def test_file_senza_estensione_trattato_come_csv():
    risposta = client.post("/api/riconcilia", files={
        "file_a": ("estratto", ESTRATTO.read_bytes()),
        "file_b": (REGISTRO.name, REGISTRO.read_bytes()),
    })
    assert risposta.status_code == 200
    assert risposta.json()["conteggi"]["riconciliato"] == 5
