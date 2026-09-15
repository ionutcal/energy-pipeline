import pandas as pd
import pytest

from src.transform import normalize, quality_report


def _frame(n=24, value=100.0):
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "value": [value] * n,
        }
    )


def test_normalize_produce_randuri_corecte():
    rows = normalize(_frame(3), country="RO", metric="load_actual", unit="MW")
    assert len(rows) == 3
    assert rows[0]["country"] == "RO"
    assert rows[0]["metric"] == "load_actual"
    assert rows[0]["unit"] == "MW"
    assert rows[0]["psr_type"] == ""


def test_normalize_elimina_valorile_lipsa():
    # O valoare lipsa la inceputul seriei nu are de unde fi completata.
    df = _frame(4)
    df.loc[0, "value"] = None
    rows = normalize(df, country="RO", metric="load_actual")
    assert len(rows) == 3


def test_normalize_dataframe_gol():
    assert normalize(pd.DataFrame(), country="RO", metric="load_actual") == []


def test_normalize_fara_coloana_de_timp_da_eroare_clara():
    df = pd.DataFrame({"altceva": [1, 2], "value": [10, 20]})
    with pytest.raises(ValueError, match="coloana de timp"):
        normalize(df, country="RO", metric="load_actual")


def test_normalize_pastreaza_psr_type():
    df = _frame(2)
    df["psr_type"] = "B19"
    rows = normalize(df, country="RO", metric="generation_actual")
    assert all(r["psr_type"] == "B19" for r in rows)


def test_quality_report_detecteaza_goluri():
    rows = normalize(_frame(5), country="RO", metric="load_actual")
    del rows[2]  # scoatem o ora din mijloc
    report = quality_report(rows)
    assert report["gaps"] == 1
    assert report["n_rows"] == 4


def test_quality_report_detecteaza_negative():
    df = _frame(4)
    df.loc[0, "value"] = -50
    rows = normalize(df, country="RO", metric="price_day_ahead")
    assert quality_report(rows)["negatives"] == 1


def test_quality_report_pe_lista_goala():
    assert quality_report([])["n_rows"] == 0


# --- forme reale de raspuns, asa cum le-a returnat API-ul (check_api.py) ---


def _load_frame(n=8):
    """load_actual: timestamp, value, quantity_unit — la 15 minute."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-09-10 14:00", periods=n, freq="15min", tz="UTC"),
            "value": [6248.0 + i for i in range(n)],
            "quantity_unit": "MAW",
        }
    )


def _price_frame(n=8):
    """price_day_ahead: timestamp, value, currency, price_unit."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-09-09 22:00", periods=n, freq="15min", tz="UTC"),
            "value": [184.84 - i for i in range(n)],
            "currency": "EUR",
            "price_unit": "MWH",
        }
    )


def test_unitatea_de_cantitate_vine_din_api():
    rows = normalize(_load_frame(), country="RO", metric="load_actual", unit="MW")
    assert rows[0]["unit"] == "MW"  # MAW tradus in notatia uzuala


def test_unitatea_de_pret_se_compune_din_moneda_si_unitate():
    rows = normalize(_price_frame(), country="RO", metric="price_day_ahead", unit="")
    assert rows[0]["unit"] == "EUR/MWh"


def test_unitatea_cade_pe_valoarea_implicita_daca_lipseste_din_raspuns():
    df = _load_frame().drop(columns=["quantity_unit"])
    rows = normalize(df, country="RO", metric="load_actual", unit="MW")
    assert rows[0]["unit"] == "MW"


def test_golurile_se_detecteaza_la_rezolutia_reala_de_15_minute():
    rows = normalize(_load_frame(20), country="RO", metric="load_actual")
    del rows[5:9]  # patru sferturi de ora consecutive lipsa
    report = quality_report(rows)
    assert report["gaps"] == 4
    assert report["step"] == "0 days 00:15:00"


def test_outlierii_se_calculeaza_separat_pe_tip_de_resursa():
    # Solar mic, gaz mare: amestecate, pragul IQR global nu ar gasi nimic.
    df = pd.concat(
        [
            _load_frame(12).assign(psr_type="Solar", value=50.0),
            _load_frame(12).assign(psr_type="Fossil Gas", value=1500.0),
        ],
        ignore_index=True,
    )
    df.loc[0, "value"] = 5000.0  # extrem doar raportat la Solar
    rows = normalize(df, country="RO", metric="generation_actual")
    assert quality_report(rows)["outliers"] == 1


def test_psr_type_pastreaza_si_codurile_netraduse():
    # python-entsoe nu are B25 (Energy storage) in tabela lui de traducere.
    df = pd.concat(
        [_load_frame(4).assign(psr_type=p) for p in ("B25", "Fossil Gas", "Biomass")],
        ignore_index=True,
    )
    rows = normalize(df, country="RO", metric="generation_actual")
    assert {r["psr_type"] for r in rows} == {"B25", "Fossil Gas", "Biomass"}


# --- curbe comprimate A03: un punct doar cand valoarea se schimba ---


def _a03(points, psr_type="Solar"):
    """Construieste o serie comprimata din {pozitie: valoare}, la 15 minute."""
    start = pd.Timestamp("2026-09-14 00:00", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": [start + pd.Timedelta(minutes=15 * (p - 1)) for p in points],
            "psr_type": psr_type,
            "value": list(points.values()),
            "quantity_unit": "MAW",
        }
    )


def test_pozitiile_omise_repeta_valoarea_anterioara():
    # Cum vine solarul real: 0 toata noaptea trimis o singura data.
    rows = normalize(_a03({1: 0.0, 5: 6.0, 6: 50.0}), country="RO", metric="generation_actual")
    assert [r["value"] for r in rows] == [0.0, 0.0, 0.0, 0.0, 6.0, 50.0]


def test_seria_constanta_se_intinde_pana_la_ultimul_moment_din_raspuns():
    # Nuclearul real vine ca un singur punct pe zi.
    df = pd.concat(
        [_a03({1: 1400.0}, "Nuclear"), _a03({1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0}, "Wind Onshore")],
        ignore_index=True,
    )
    rows = normalize(df, country="RO", metric="generation_actual")
    nuclear = [r["value"] for r in rows if r["psr_type"] == "Nuclear"]
    assert nuclear == [1400.0] * 4


def test_suma_productiei_e_completa_dupa_refacere():
    df = pd.concat(
        [_a03({1: 100.0, 4: 200.0}, "Fossil Gas"), _a03({1: 0.0, 2: 5.0, 3: 9.0, 4: 12.0}, "Solar")],
        ignore_index=True,
    )
    rows = pd.DataFrame(normalize(df, country="RO", metric="generation_actual"))
    total = rows.groupby("ts")["value"].sum().tolist()
    assert total == [100.0, 105.0, 109.0, 212.0]
