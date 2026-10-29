"""Shared fixtures: a small seeded database and its Parquet extract."""

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db import Base, upsert_observations
from src.export import export

# Midnight on 4 May 2026 in Bucharest (UTC+3), so local days start on the hour.
SEED_START = dt.datetime(2026, 5, 3, 21, 0, tzinfo=dt.timezone.utc)
QUARTER = dt.timedelta(minutes=15)
DAY = 96  # quarter hours


def _rows(country, metric, values, unit, psr_type="", start=SEED_START):
    return [
        dict(country=country, metric=metric, psr_type=psr_type,
             ts=start + i * QUARTER, value=float(v), unit=unit)
        for i, v in enumerate(values)
    ]


def seed_rows() -> list[dict]:
    """Two days for RO and HU, plus RO prices for the following day."""
    rows = []
    for country in ("RO", "HU"):
        rows += _rows(country, "load_actual", [6000 + i for i in range(2 * DAY)], "MW")
        prices = [120.0] * (2 * DAY)
        prices[50], prices[51] = -4.5, -0.5  # two negative quarter hours on day 1
        rows += _rows(country, "price_day_ahead", prices, "EUR/MWh")
        rows += _rows(country, "generation_actual", [i % 96 * 10 for i in range(2 * DAY)], "MW", "B16")
        rows += _rows(country, "generation_actual", [5000.0] * (2 * DAY), "MW", "B04")
    rows += _rows("RO", "price_day_ahead", [90.0] * DAY, "EUR/MWh", start=SEED_START + 2 * DAY * QUARTER)
    return rows


@pytest.fixture
def seeded_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        upsert_observations(session, seed_rows())
    yield engine
    engine.dispose()


@pytest.fixture
def extract_dir(tmp_path, seeded_engine):
    out = tmp_path / "data"
    export(seeded_engine, out)
    return out
