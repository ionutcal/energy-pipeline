"""Tests for the dashboard's data layer: reading the Parquet extract with DuckDB."""

import datetime as dt

import pandas as pd
import pytest

from dashboard.data import ExtractMissing, default_period, load_metadata, load_metric

DAY_1 = dt.date(2026, 5, 4)  # first seeded local day (see tests/conftest.py)


def test_load_metric_filters_country_metric_and_period(extract_dir):
    load = load_metric(extract_dir, "load_actual", "RO", DAY_1, DAY_1)
    assert len(load) == 96
    assert set(load.index.date) == {DAY_1}


def test_load_metric_returns_analysis_shape(extract_dir):
    gen = load_metric(extract_dir, "generation_actual", "RO", DAY_1, DAY_1)
    assert str(gen.index.tz) == "Europe/Bucharest"
    assert list(gen.columns) == ["value", "psr_type"]
    assert set(gen["psr_type"]) == {"B04", "B16"}


def test_period_on_dst_day_covers_the_whole_local_day(tmp_path):
    # Clocks went back on 26 October 2025: that local day has 25 hours.
    ts = pd.date_range("2025-10-25 00:00", "2025-10-27 23:45", freq="15min", tz="Europe/Bucharest")
    pd.DataFrame(
        {"country": "RO", "metric": "price_day_ahead", "psr_type": "",
         "ts": ts.tz_convert("UTC"), "value": 100.0, "unit": "EUR/MWh"}
    ).to_parquet(tmp_path / "observations.parquet", index=False)

    day = dt.date(2025, 10, 26)
    prices = load_metric(tmp_path, "price_day_ahead", "RO", day, day)
    assert len(prices) == 100
    assert set(prices.index.date) == {day}


def test_default_period_stops_at_last_generation_day(extract_dir):
    # RO prices run one day further than generation; the default must not follow them.
    meta = load_metadata(extract_dir)
    assert default_period(meta, "RO") == (DAY_1, DAY_1 + dt.timedelta(days=1))


def test_missing_extract_raises_with_instructions(tmp_path):
    with pytest.raises(ExtractMissing, match="python -m src.export"):
        load_metadata(tmp_path / "nope")


def test_unknown_country_returns_empty_frame(extract_dir):
    frame = load_metric(extract_dir, "load_actual", "XX", DAY_1, DAY_1)
    assert frame.empty
    assert list(frame.columns) == ["value", "psr_type"]
