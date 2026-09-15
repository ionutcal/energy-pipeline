"""Tests for the fetch window and for loading into the database.

They run on in-memory SQLite, so they need neither PostgreSQL nor an API token.
"""

import datetime as dt
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db import Base, Observation, upsert_observations
from src.pipeline import LOOKBACK, resolve_window


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
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
