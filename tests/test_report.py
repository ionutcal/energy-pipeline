"""Teste pentru analizele din report.py, pe cadre sintetice (fara baza de date)."""

import pandas as pd
import pytest

from src.report import (
    complete_days,
    daily_renewable_share,
    energy_mix,
    generation_by_group,
    net_balance,
    price_by_renewable_share,
    renewable_share,
)

TZ = "Europe/Bucharest"


def _gen(values_by_psr: dict[str, list[float]], start="2026-09-14 00:00", freq="15min"):
    """Productie in formatul lui load_frame: index ts, coloane value si psr_type."""
    n = len(next(iter(values_by_psr.values())))
    ts = pd.date_range(start, periods=n, freq=freq, tz=TZ)
    frames = [
        pd.DataFrame({"ts": ts, "value": values, "psr_type": psr})
        for psr, values in values_by_psr.items()
    ]
    return pd.concat(frames).set_index("ts")


def _series(values, start="2026-09-14 00:00", freq="15min"):
    ts = pd.date_range(start, periods=len(values), freq=freq, tz=TZ)
    return pd.DataFrame({"value": values}, index=ts)


def test_grupele_aduna_resursele_inrudite():
    gen = _gen(
        {
            "Hydro Run-of-river and poundage": [100.0, 100.0],
            "Hydro Water Reservoir": [50.0, 70.0],
            "Fossil Gas": [300.0, 300.0],
        }
    )
    wide = generation_by_group(gen)
    assert list(wide.columns) == ["Hidro", "Gaz"]
    assert wide["Hidro"].tolist() == [150.0, 170.0]


def test_codul_necunoscut_ajunge_la_altele():
    gen = _gen({"B99": [10.0], "Solar": [30.0]})
    assert generation_by_group(gen)["Altele"].tolist() == [10.0]


def test_ponderea_regenerabilelor_la_fiecare_moment():
    gen = _gen({"Solar": [0.0, 300.0], "Fossil Gas": [400.0, 100.0]})
    assert renewable_share(gen).tolist() == [0.0, 0.75]


def test_ponderea_zilnica_e_ponderata_cu_energia():
    # Noaptea: 10 MW solar din 100; ziua: 900 MW solar din 1000.
    # Media ponderilor ar da 50%; energia arata 910 / 1100.
    gen = _gen({"Solar": [10.0, 900.0], "Fossil Gas": [90.0, 100.0]}, freq="12h")
    assert daily_renewable_share(gen).iloc[0] == pytest.approx(910 / 1100)


def test_mixul_energetic_insumeaza_unu():
    gen = _gen({"Solar": [100.0, 300.0], "Fossil Gas": [600.0, 0.0]})
    mix = energy_mix(gen)
    assert mix.sum() == pytest.approx(1.0)
    assert mix["Gaz"] == pytest.approx(0.6)


def test_zilele_partiale_sunt_excluse():
    # O zi completa (96 de sferturi) si o a doua zi de doar 6 ore.
    index = pd.date_range("2026-09-14 00:00", periods=96 + 24, freq="15min", tz=TZ)
    days = complete_days(pd.DatetimeIndex(index))
    assert [d.day for d in days] == [14]


def test_pretul_pe_transe_si_corelatia_negativa():
    gen = _gen({"Solar": [0.0, 100.0, 700.0, 950.0], "Fossil Gas": [1000.0, 900.0, 300.0, 50.0]})
    price = _series([200.0, 190.0, 80.0, 50.0])
    table, corr = price_by_renewable_share(price, renewable_share(gen))
    assert table["momente"].sum() == 4
    assert table.loc["90-100%", "pret_mediu"] == 50.0
    assert corr < -0.9


def test_pretul_fara_momente_comune_nu_crapa():
    gen = _gen({"Solar": [100.0]}, start="2026-09-14 00:00")
    price = _series([80.0], start="2026-01-01 00:00")
    table, corr = price_by_renewable_share(price, renewable_share(gen))
    assert table.empty and pd.isna(corr)


def test_soldul_e_productie_minus_consum():
    gen = _gen({"Solar": [500.0, 0.0], "Fossil Gas": [1000.0, 1000.0]})
    load = _series([1200.0, 1600.0])
    assert net_balance(load, gen).tolist() == [300.0, -600.0]
