"""Tests for the dashboard's Plotly chart builders and number formats."""

import datetime as dt
import math

import pandas as pd
import pytest

from dashboard.charts import (
    country_name,
    daily_price_by_country_figure,
    fmt_correlation,
    fmt_day,
    fmt_hours,
    fmt_number,
    fmt_price,
    fmt_share,
    hourly_mix_figure,
    monthly_mix_figure,
    monthly_summary_figure,
    negative_hours_figure,
    price_by_share_figure,
)
from src.style import COUNTRY_COLORS, GROUP_COLORS

MONTHS = pd.date_range("2026-01-01", periods=3, freq="MS", tz="Europe/Bucharest")


def test_monthly_summary_has_two_panels_with_units():
    summary = pd.DataFrame({"renewable_share": [0.4, 0.5, 0.6], "mean_price": [150.0, 100.0, 95.0]}, index=MONTHS)
    fig = monthly_summary_figure(summary)
    assert len(fig.data) == 2
    assert list(fig.data[0].y) == pytest.approx([40.0, 50.0, 60.0])  # shown as %
    assert "%" in fig.layout.yaxis.title.text
    assert "EUR/MWh" in fig.layout.yaxis2.title.text
    assert not fig.layout.title.text  # titles belong to the view


def test_monthly_mix_stacks_groups_in_order_with_their_colours():
    mix = pd.DataFrame({"Hydro": [0.3] * 3, "Gas": [0.5] * 3, "Solar": [0.2] * 3}, index=MONTHS)
    fig = monthly_mix_figure(mix)
    assert [t.name for t in fig.data] == ["Hydro", "Gas", "Solar"]
    assert [t.marker.color for t in fig.data] == [GROUP_COLORS[g] for g in ("Hydro", "Gas", "Solar")]
    assert fig.layout.barmode == "stack"
    assert "%" in fig.layout.yaxis.title.text


def test_hourly_mix_is_stacked_by_group_in_mw():
    profile = pd.DataFrame({"Hydro": [1000.0] * 24, "Solar": [0.0] * 6 + [500.0] * 18}, index=range(24))
    fig = hourly_mix_figure(profile)
    assert [t.name for t in fig.data] == ["Hydro", "Solar"]
    assert all(t.stackgroup for t in fig.data)
    assert [t.fillcolor for t in fig.data] == [GROUP_COLORS["Hydro"], GROUP_COLORS["Solar"]]
    assert "MW" in fig.layout.yaxis.title.text
    assert "local time" in fig.layout.xaxis.title.text


def test_price_by_share_carries_the_sample_size_for_hover():
    table = pd.DataFrame(
        {"mean_price": [135.8, 94.0, 54.7], "intervals": [6387, 4659, 292]},
        index=pd.CategoricalIndex(["30-40%", "60-70%", "80-90%"], name="share"),
    )
    fig = price_by_share_figure(table)
    assert list(fig.data[0].x) == ["30-40%", "60-70%", "80-90%"]
    assert [row[0] for row in fig.data[0].customdata] == [6387, 4659, 292]
    assert "intervals" in fig.data[0].hovertemplate
    assert "EUR/MWh" in fig.layout.yaxis.title.text


def test_negative_hours_per_month_in_hours():
    fig = negative_hours_figure(pd.Series([0.0, 12.5, 40.0], index=MONTHS))
    assert list(fig.data[0].y) == [0.0, 12.5, 40.0]
    assert "hours" in fig.layout.yaxis.title.text
    # The hover uses the same hours format as the rest of the dashboard.
    assert [row[0] for row in fig.data[0].customdata] == ["0 h", "12 h", "40 h"]
    assert "%{customdata[0]}" in fig.data[0].hovertemplate


def test_daily_price_by_country_has_one_line_per_country_in_eur():
    days = pd.date_range("2026-05-01", periods=3, freq="D", tz="Europe/Bucharest")
    daily = pd.DataFrame({"RO": [100.0] * 3, "HU": [110.0] * 3, "BG": [90.0] * 3}, index=days)
    fig = daily_price_by_country_figure(daily)
    assert sorted(t.name for t in fig.data) == ["BG", "HU", "RO"]
    assert "EUR/MWh" in fig.layout.yaxis.title.text


def test_number_formats():
    assert fmt_day(dt.date(2026, 5, 4)) == "4 May 2026"
    assert fmt_number(1121.34) == "1,121.3"
    assert fmt_price(121.34) == "121.3 EUR/MWh"
    assert fmt_share(0.5012) == "50.1%"
    assert fmt_hours(173.25) == "173 h"
    assert fmt_hours(0.75) == "0.75 h"
    assert fmt_hours(0.0) == "0 h"


@pytest.mark.parametrize("fmt", [fmt_number, fmt_price, fmt_share, fmt_hours])
def test_missing_numbers_show_a_dash(fmt):
    assert fmt(math.nan) == "—"
    assert fmt(None) == "—"


def test_reference_country_is_drawn_on_top_but_listed_first():
    # Close prices would otherwise hide the reference line under the others.
    days = pd.date_range("2026-05-01", periods=3, freq="D", tz="Europe/Bucharest")
    daily = pd.DataFrame({"RO": [100.0] * 3, "BG": [100.0] * 3, "HU": [101.0] * 3}, index=days)
    fig = daily_price_by_country_figure(daily)
    assert fig.data[-1].name == "RO"
    assert min(fig.data, key=lambda t: t.legendrank).name == "RO"
    assert {t.name: t.line.color for t in fig.data} == dict(zip(["RO", "BG", "HU"], COUNTRY_COLORS))


def test_correlations_carry_a_sign_and_a_true_minus():
    assert fmt_correlation(0.09) == "+0.09"
    assert fmt_correlation(-0.41) == "−0.41"
    assert fmt_correlation(math.nan) == "—"


def test_country_names():
    assert country_name("RO") == "Romania"
    assert country_name("XX") == "XX"  # unknown codes are shown as they are
