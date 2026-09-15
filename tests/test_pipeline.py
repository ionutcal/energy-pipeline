"""Teste pentru fereastra de descarcare si pentru incarcarea in baza de date.

Ruleaza pe SQLite in memorie, deci nu cer nici PostgreSQL, nici token de API.
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


def test_backfill_cand_tabela_e_goala(session):
    start, end = resolve_window(session, "RO", "load_actual")
    assert (end - start).days >= 1


def test_rulare_incrementala_re_cere_ultima_zi(session):
    watermark = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    upsert_observations(session, [_row(watermark)])
    start, _ = resolve_window(session, "RO", "load_actual")
    assert abs((start - (watermark - LOOKBACK)).total_seconds()) < 1


def test_pretul_day_ahead_nu_se_blocheaza_pe_un_watermark_din_viitor(session):
    # Preturile publicate pentru maine duc watermark-ul inaintea lui `now`.
    viitor = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)
    upsert_observations(session, [_row(viitor, metric="price_day_ahead")])

    start, end = resolve_window(session, "RO", "price_day_ahead")
    assert start < end, "fereastra nu are voie sa iasa negativa"
    assert end - start >= LOOKBACK, "altfel nu mai prinde publicarile noi"
    assert end > dt.datetime.now(dt.timezone.utc), "trebuie ceruta si ziua urmatoare"


def test_upsertul_nu_duplica_la_a_doua_rulare(session):
    ts = dt.datetime(2026, 9, 10, 14, tzinfo=dt.timezone.utc)
    assert upsert_observations(session, [_row(ts)]) == 1
    assert upsert_observations(session, [_row(ts)]) == 0
    assert session.query(Observation).count() == 1


def test_upsertul_corecteaza_o_valoare_revizuita(session):
    ts = dt.datetime(2026, 9, 10, 14, tzinfo=dt.timezone.utc)
    upsert_observations(session, [_row(ts, value=100.0)])
    assert upsert_observations(session, [_row(ts, value=130.0)]) == 1
    session.expire_all()
    assert session.query(Observation).one().value == 130.0


def test_backfillul_mare_se_insereaza_pe_transe(session):
    # Peste limita de 65535 de parametri legati a PostgreSQL daca s-ar
    # trimite intr-un singur statement (10.000 x 7 = 70.000).
    base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    rows = [_row(base + dt.timedelta(minutes=15 * i)) for i in range(10_000)]
    assert upsert_observations(session, rows) == 10_000
    assert upsert_observations(session, rows) == 0


def test_denumirea_lunga_de_resursa_incape(session):
    ts = dt.datetime(2026, 9, 10, 14, tzinfo=dt.timezone.utc)
    lung = "Hydro Run-of-river and poundage"
    upsert_observations(session, [_row(ts, metric="generation_actual", psr_type=lung)])
    assert session.query(Observation).one().psr_type == lung
