"""Tests for the fetch window and for loading into the database.

By default they run on in-memory SQLite, so they need neither PostgreSQL nor
an API token. Set TEST_DATABASE_URL to run the same tests against another
database — CI does this for PostgreSQL, the production target, because the
upsert has dialect-specific code:

    TEST_DATABASE_URL=postgresql+psycopg2://postgres@localhost:5432/energy_test pytest
"""

import datetime as dt
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src import pipeline
from src.db import Base, Observation, upsert_observations
from src.pipeline import LOOKBACK, resolve_window

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite://")


@pytest.fixture
def engine():
    engine = create_engine(TEST_DATABASE_URL, future=True)
    # Start from a clean schema: a real database keeps tables between tests.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _row(ts, **kw):
    return dict(
        country="RO",
        metric="load_actual",
        psr_type="",
        ts=ts,
        value=1.0,
        unit="MW",
    ) | kw


def test_backfill_when_table_is_empty(session):
    start, end = resolve_window(session, "RO", "load_actual")
    assert (end - start).days >= 1


def test_incremental_run_re_requests_the_last_day(session):
    watermark = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    upsert_observations(session, [_row(watermark)])
    start, _ = resolve_window(session, "RO", "load_actual")
    assert abs((start - (watermark - LOOKBACK)).total_seconds()) < 1


def test_day_ahead_price_is_not_blocked_by_a_future_watermark(session):
    # Prices published for tomorrow push the watermark ahead of `now`.
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)
    upsert_observations(session, [_row(future, metric="price_day_ahead")])

    start, end = resolve_window(session, "RO", "price_day_ahead")
    assert start < end, "the window must never be negative"
    assert end - start >= LOOKBACK, "otherwise new publications are missed"
    assert end > dt.datetime.now(dt.timezone.utc), "tomorrow must be requested too"


def test_upsert_does_not_duplicate_on_second_run(session):
    ts = dt.datetime(2026, 9, 10, 14, tzinfo=dt.timezone.utc)
    assert upsert_observations(session, [_row(ts)]) == 1
    assert upsert_observations(session, [_row(ts)]) == 0
    assert session.query(Observation).count() == 1


def test_upsert_corrects_a_revised_value(session):
    ts = dt.datetime(2026, 9, 10, 14, tzinfo=dt.timezone.utc)
    upsert_observations(session, [_row(ts, value=100.0)])
    assert upsert_observations(session, [_row(ts, value=130.0)]) == 1
    session.expire_all()
    assert session.query(Observation).one().value == 130.0


def test_large_backfill_is_inserted_in_chunks(session):
    # Above PostgreSQL's limit of 65535 bound parameters if sent as a
    # single statement (10,000 x 7 = 70,000).
    base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    rows = [_row(base + dt.timedelta(minutes=15 * i)) for i in range(10_000)]
    assert upsert_observations(session, rows) == 10_000
    assert upsert_observations(session, rows) == 0


def test_long_resource_name_fits(session):
    ts = dt.datetime(2026, 9, 10, 14, tzinfo=dt.timezone.utc)
    long_name = "Hydro Run-of-river and poundage"
    upsert_observations(session, [_row(ts, metric="generation_actual", psr_type=long_name)])
    assert session.query(Observation).one().psr_type == long_name


# --- end to end: resolve_window -> fetch -> normalize -> upsert ---


class FakeApi:
    """Stands in for ENTSO-E: returns compressed (A03) frames like the real API.

    Each response has a few consecutive quarter hours, like real responses
    where load or wind change almost every interval: that is what lets
    transform.infer_step recover the 15-minute step.
    """

    def __init__(self, gas_value=1000.0):
        self.gas_value = gas_value
        self.calls = []

    def fetch_metric(self, client, metric, country, start, end):
        self.calls.append((metric, start, end))
        day = pd.Timestamp(end).floor("D") - pd.Timedelta(days=1)
        at = lambda position: day + pd.Timedelta(minutes=15 * (position - 1))
        if metric == "generation_actual":
            return pd.DataFrame(
                {
                    "timestamp": [at(1), at(40), at(41), at(42), at(1), at(96)],
                    "psr_type": ["Solar"] * 4 + ["Fossil Gas"] * 2,
                    "value": [0.0, 300.0, 600.0, 900.0, self.gas_value, self.gas_value],
                    "quantity_unit": "MAW",
                }
            )
        if metric == "price_day_ahead":
            return pd.DataFrame(
                {
                    "timestamp": [at(1), at(2), at(3), at(96)],
                    "value": [120.0, 118.0, 110.0, 95.0],
                    "currency": "EUR",
                    "price_unit": "MWH",
                }
            )
        return pd.DataFrame(
            {
                "timestamp": [at(1), at(2), at(3), at(96)],
                "value": [6000.0, 5900.0, 5800.0, 5500.0],
                "quantity_unit": "MAW",
            }
        )


@pytest.fixture
def run_pipeline(engine, monkeypatch):
    def run(api):
        monkeypatch.setattr(pipeline, "get_engine", lambda: engine)
        monkeypatch.setattr(pipeline, "get_client", lambda: object())
        monkeypatch.setattr(pipeline, "fetch_metric", api.fetch_metric)
        return pipeline.run()

    return run


def _count(engine, **filters):
    with Session(engine) as s:
        return s.query(Observation).filter_by(**filters).count()


def test_full_run_restores_curves_and_stores_every_metric(engine, run_pipeline):
    assert run_pipeline(FakeApi()) == 0

    # 96 quarter hours per series once the A03 curve is restored.
    assert _count(engine, metric="load_actual") == 96
    assert _count(engine, metric="price_day_ahead") == 96
    assert _count(engine, metric="generation_actual", psr_type="Solar") == 96
    assert _count(engine, metric="generation_actual", psr_type="Fossil Gas") == 96

    with Session(engine) as s:
        units = {o.metric: o.unit for o in s.query(Observation)}
    assert units == {"load_actual": "MW", "price_day_ahead": "EUR/MWh", "generation_actual": "MW"}


def test_second_run_writes_nothing_and_a_revision_is_applied(engine, run_pipeline):
    run_pipeline(FakeApi())
    second = FakeApi()
    assert run_pipeline(second) == 0
    assert all(start < end for _, start, end in second.calls), "incremental window must be valid"

    run_pipeline(FakeApi(gas_value=1200.0))
    with Session(engine) as s:
        gas = {o.value for o in s.query(Observation).filter_by(psr_type="Fossil Gas")}
    assert gas == {1200.0}
    assert _count(engine, metric="generation_actual") == 192, "revisions must not add rows"


def test_one_failing_metric_does_not_stop_the_others(engine, run_pipeline):
    api = FakeApi()
    real_fetch = api.fetch_metric

    def flaky(client, metric, country, start, end):
        if metric == "price_day_ahead":
            raise RuntimeError("API down")
        return real_fetch(client, metric, country, start, end)

    api.fetch_metric = flaky
    assert run_pipeline(api) == 1
    assert _count(engine, metric="price_day_ahead") == 0
    assert _count(engine, metric="load_actual") == 96
