"""Tests for the analyses in report.py, on synthetic frames (no database)."""

import pandas as pd
import pytest

from src.report import (
    complete_days,
    complete_months,
    daily_price_by_country,
    daily_renewable_share,
    energy_mix,
    generation_by_group,
    net_balance,
    local_tz,
    monthly_mix,
    monthly_summary,
    price_by_renewable_share,
    price_comparison,
    renewable_share,
    season_of,
    seasonal_hourly_mix,
    seasonal_price_link,
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


# --- country comparison ---


def test_price_comparison_against_the_reference_country():
    ro = _series([100.0, 100.0, 50.0, 80.0])
    hu = _series([100.0, 100.0, 70.0, 80.0])
    table = price_comparison({"RO": ro, "HU": hu}, reference="RO")
    assert table.loc["HU", "mean_price"] == pytest.approx(87.5)
    assert table.loc["HU", "mean_abs_spread"] == pytest.approx(5.0)
    assert table.loc["HU", "same_price_share"] == pytest.approx(0.75)
    assert table.loc["RO", "same_price_share"] == 1.0


def test_price_comparison_uses_only_moments_priced_everywhere():
    ro = _series([100.0, 200.0, 300.0])
    bg = _series([100.0, 200.0])  # the third interval is missing
    table = price_comparison({"RO": ro, "BG": bg}, reference="RO")
    assert table.loc["RO", "mean_price"] == pytest.approx(150.0)


def test_same_instant_in_different_time_zones_is_aligned():
    # 10:00 in Bucharest is 09:00 in Budapest: the same moment.
    ro = _series([120.0, 90.0])
    hu = ro.tz_convert("Europe/Budapest")
    table = price_comparison({"RO": ro, "HU": hu}, reference="RO")
    assert table.loc["HU", "same_price_share"] == 1.0


def test_daily_prices_by_country_keep_only_complete_days():
    index_values = [100.0] * 96 + [50.0] * 24  # one full day, then six hours
    daily = daily_price_by_country(
        {"RO": _series(index_values), "HU": _series([v + 10 for v in index_values])},
        tz="Europe/Bucharest",
    )
    assert len(daily) == 1
    assert daily.iloc[0].tolist() == [100.0, 110.0]


def test_unknown_country_falls_back_to_utc():
    assert local_tz("RO") == "Europe/Bucharest"
    assert local_tz("XX") == "UTC"


# --- seasons and months ---


def _hourly_gen(start, days, solar, gas):
    """Hourly generation for `days` days: constant solar and gas output."""
    n = days * 24
    return _gen({"B16": [solar] * n, "B04": [gas] * n}, start=start, freq="h")


def test_seasons_follow_meteorological_months():
    index = pd.DatetimeIndex(pd.to_datetime(["2025-12-01", "2026-02-28", "2026-03-01", "2026-09-30"])).tz_localize(TZ)
    assert list(season_of(index)) == ["Winter", "Winter", "Spring", "Autumn"]


def test_partial_months_are_left_out():
    # 10 days of August, all of September, 5 days of October.
    index = pd.date_range("2026-08-22 00:00", "2026-10-05 23:00", freq="h", tz=TZ)
    months = complete_months(pd.DatetimeIndex(index))
    assert [f"{m:%Y-%m}" for m in months] == ["2026-09"]


def test_monthly_mix_shares_sum_to_one_per_month():
    gen = pd.concat([_hourly_gen("2026-06-01", 30, solar=300.0, gas=700.0),
                     _hourly_gen("2026-07-01", 31, solar=100.0, gas=900.0)])
    mix = monthly_mix(gen)
    assert mix.sum(axis=1).tolist() == pytest.approx([1.0, 1.0])
    assert mix["Solar"].tolist() == pytest.approx([0.3, 0.1])


def test_monthly_summary_weights_renewables_by_energy_and_averages_price():
    gen = _hourly_gen("2026-06-01", 30, solar=250.0, gas=750.0)
    price = _series([100.0] * (15 * 24) + [60.0] * (15 * 24), start="2026-06-01", freq="h")
    summary = monthly_summary(gen, price)
    assert summary["renewable_share"].tolist() == pytest.approx([0.25])
    assert summary["mean_price"].tolist() == pytest.approx([80.0])


def test_seasonal_price_link_is_computed_within_each_season():
    # Summer: price falls as solar rises. Winter: no solar, flat price.
    summer_share = [0.0, 0.25, 0.5, 0.75]
    summer = _gen({"B16": [v * 1000 for v in summer_share], "B04": [(1 - v) * 1000 for v in summer_share]},
                  start="2026-07-01", freq="6h")
    summer_price = _series([200.0, 150.0, 100.0, 50.0], start="2026-07-01", freq="6h")
    winter = _gen({"B16": [0.0] * 4, "B04": [1000.0] * 4}, start="2026-01-10", freq="6h")
    winter_price = _series([180.0, 181.0, 179.0, 180.0], start="2026-01-10", freq="6h")

    link = seasonal_price_link(pd.concat([winter_price, summer_price]), pd.concat([winter, summer]))
    assert list(link.index) == ["Winter", "Summer"]
    assert link.loc["Summer", "correlation"] == pytest.approx(-1.0)
    assert link.loc["Winter", "mean_renewable_share"] == 0.0
    assert pd.isna(link.loc["Winter", "correlation"]), "no variation in share, so no correlation"
    assert link.loc["Winter", "intervals"] == 4


def test_seasonal_hourly_mix_covers_only_seasons_in_the_data():
    gen = _hourly_gen("2026-07-01", 3, solar=100.0, gas=500.0)
    profiles = seasonal_hourly_mix(gen)
    assert list(profiles) == ["Summer"]
    assert len(profiles["Summer"]) == 24
