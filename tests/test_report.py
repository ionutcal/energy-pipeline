"""Tests for the analyses in report.py, on synthetic frames (no database)."""

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
    """Generation in load_frame's format: ts index, value and psr_type columns."""
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


def test_groups_sum_related_resources():
    gen = _gen(
        {
            "B11": [100.0, 100.0],  # Hydro Run-of-river
            "B12": [50.0, 70.0],  # Hydro Water Reservoir
            "B04": [300.0, 300.0],
        }
    )
    wide = generation_by_group(gen)
    assert list(wide.columns) == ["Hydro", "Gas"]
    assert wide["Hydro"].tolist() == [150.0, 170.0]


def test_unknown_code_goes_to_other():
    gen = _gen({"B99": [10.0], "B16": [30.0]})
    assert generation_by_group(gen)["Other"].tolist() == [10.0]


def test_renewable_share_at_each_timestamp():
    gen = _gen({"B16": [0.0, 300.0], "B04": [400.0, 100.0]})
    assert renewable_share(gen).tolist() == [0.0, 0.75]


def test_daily_share_is_weighted_by_energy():
    # Night: 10 MW solar out of 100; day: 900 MW solar out of 1000.
    # The mean of the shares would give 50%; energy says 910 / 1100.
    gen = _gen({"B16": [10.0, 900.0], "B04": [90.0, 100.0]}, freq="12h")
    assert daily_renewable_share(gen).iloc[0] == pytest.approx(910 / 1100)


def test_energy_mix_sums_to_one():
    gen = _gen({"B16": [100.0, 300.0], "B04": [600.0, 0.0]})
    mix = energy_mix(gen)
    assert mix.sum() == pytest.approx(1.0)
    assert mix["Gas"] == pytest.approx(0.6)


def test_partial_days_are_excluded():
    # One complete day (96 quarter hours) and a second day of only 6 hours.
    index = pd.date_range("2026-09-14 00:00", periods=96 + 24, freq="15min", tz=TZ)
    days = complete_days(pd.DatetimeIndex(index))
    assert [d.day for d in days] == [14]


def test_price_by_bucket_and_negative_correlation():
    gen = _gen({"B16": [0.0, 100.0, 700.0, 950.0], "B04": [1000.0, 900.0, 300.0, 50.0]})
    price = _series([200.0, 190.0, 80.0, 50.0])
    table, corr = price_by_renewable_share(price, renewable_share(gen))
    assert table["intervals"].sum() == 4
    assert table.loc["90-100%", "mean_price"] == 50.0
    assert corr < -0.9


def test_price_without_common_timestamps_does_not_crash():
    gen = _gen({"B16": [100.0]}, start="2026-09-14 00:00")
    price = _series([80.0], start="2026-01-01 00:00")
    table, corr = price_by_renewable_share(price, renewable_share(gen))
    assert table.empty and pd.isna(corr)


def test_balance_is_generation_minus_load():
    gen = _gen({"B16": [500.0, 0.0], "B04": [1000.0, 1000.0]})
    load = _series([1200.0, 1600.0])
    assert net_balance(load, gen).tolist() == [300.0, -600.0]
