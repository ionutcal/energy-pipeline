"""Tests for the dashboard's data layer: reading the Parquet extract with DuckDB."""

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from dashboard.data import ExtractMissing, default_period, load_metadata, load_metric
from src.db import Base, upsert_observations
from src.export import export
from tests.conftest import QUARTER, SEED_START, _rows

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


@pytest.mark.parametrize(
    "day, quarters",
    [
        (dt.date(2025, 10, 26), 100),  # clocks went back: 25 hours
        (dt.date(2026, 3, 29), 92),  # clocks went forward: 23 hours
    ],
)
def test_period_on_dst_day_covers_the_whole_local_day(tmp_path, day, quarters):
    around = pd.date_range(day - dt.timedelta(days=1), day + dt.timedelta(days=2), freq="15min",
                           tz="Europe/Bucharest", inclusive="left")
    pd.DataFrame(
        {"country": "RO", "metric": "price_day_ahead", "psr_type": "",
         "ts": around.tz_convert("UTC"), "value": 100.0, "unit": "EUR/MWh"}
    ).to_parquet(tmp_path / "observations.parquet", index=False)

    prices = load_metric(tmp_path, "price_day_ahead", "RO", day, day)
    assert len(prices) == quarters
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


def test_default_period_skips_partial_days_at_either_end(tmp_path):
    # Generation from 20:00 on 3 May to 10:00 on 6 May, local time: only the
    # 4th and the 5th are complete. A partial day would give absurd figures.
    start = SEED_START - 4 * 4 * QUARTER
    quarters = 4 * 4 + 2 * 96 + 10 * 4
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        upsert_observations(session, _rows("RO", "generation_actual", [100.0] * quarters, "MW", "B04", start=start))
    export(engine, tmp_path / "data")
    engine.dispose()

    assert default_period(load_metadata(tmp_path / "data"), "RO") == (DAY_1, DAY_1 + dt.timedelta(days=1))


def test_default_period_without_generation_uses_the_other_metrics():
    # A country can have prices and load but no generation data at all.
    meta = {"coverage": {"BG": {"price_day_ahead": {
        "first": "2026-05-03T21:00:00+00:00", "last": "2026-05-05T20:45:00+00:00",
    }}}}
    assert default_period(meta, "BG") == (DAY_1, DAY_1 + dt.timedelta(days=1))
