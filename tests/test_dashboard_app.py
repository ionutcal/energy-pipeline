"""End-to-end tests of the Streamlit app, run with Streamlit's own AppTest."""

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from src.db import Base, upsert_observations
from src.export import export

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
HOUR = dt.timedelta(hours=1)
# Midnight on 1 May 2026 in Bucharest (UTC+3).
MAY_1 = dt.datetime(2026, 4, 30, 21, 0, tzinfo=dt.timezone.utc)


def _hourly(country, metric, unit, value_at, days, psr_type=""):
    return [
        dict(country=country, metric=metric, psr_type=psr_type,
             ts=MAY_1 + i * HOUR, value=float(value_at(i)), unit=unit)
        for i in range(days * 24)
    ]


def _country(country, days):
    solar = lambda i: max(0.0, 2000 - abs(i % 24 - 13) * 300)  # noqa: E731 — midday peak
    return (
        _hourly(country, "load_actual", "MW", lambda i: 5500 + (i % 24) * 20, days)
        + _hourly(country, "price_day_ahead", "EUR/MWh", lambda i: 150 - solar(i) / 20 - (i % 7), days)
        + _hourly(country, "generation_actual", "MW", solar, days, "B16")
        + _hourly(country, "generation_actual", "MW", lambda i: 3000 + (i % 5) * 10, days, "B04")
    )


@pytest.fixture
def long_extract_dir(tmp_path):
    """RO for May and June 2026 (two complete months, spring and summer); HU for May only."""
    engine = create_engine(f"sqlite:///{tmp_path / 'long.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        upsert_observations(session, _country("RO", 61) + _country("HU", 31))
    out = tmp_path / "data"
    export(engine, out)
    engine.dispose()
    return out


def _run(monkeypatch, data_dir) -> AppTest:
    monkeypatch.setenv("DASHBOARD_DATA_DIR", str(data_dir))
    return AppTest.from_file(APP, default_timeout=60).run()


def test_app_renders_every_tab_without_errors(monkeypatch, long_extract_dir):
    at = _run(monkeypatch, long_extract_dir)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Overview", "Generation mix", "Prices and renewables", "Cross-border"]
    assert len(at.metric) == 4


def test_missing_extract_shows_how_to_create_it(monkeypatch, tmp_path):
    at = _run(monkeypatch, tmp_path / "empty")
    assert not at.exception
    assert "python -m src.export" in at.error[0].value


def test_short_period_replaces_monthly_charts_with_a_note(monkeypatch, long_extract_dir):
    at = _run(monkeypatch, long_extract_dir)
    at.date_input[0].set_value((dt.date(2026, 5, 4), dt.date(2026, 5, 10))).run()
    assert not at.exception
    assert any("at least one complete month" in i.value for i in at.info)
    assert len(at.metric) == 4


def test_empty_period_explains_instead_of_failing(monkeypatch, long_extract_dir):
    at = _run(monkeypatch, long_extract_dir)
    at.selectbox[0].set_value("HU").run()
    at.date_input[0].set_value((dt.date(2026, 6, 8), dt.date(2026, 6, 14))).run()
    assert not at.exception
    assert any("No data for Hungary" in i.value for i in at.info)


def test_season_selector_offers_only_present_seasons(monkeypatch, long_extract_dir):
    at = _run(monkeypatch, long_extract_dir)
    assert list(at.radio[0].options) == ["Spring", "Summer"]


def test_headline_figures_carry_units_in_their_labels(monkeypatch, long_extract_dir):
    # Units in the value made the price too wide for its tile and it was cut off.
    at = _run(monkeypatch, long_extract_dir)
    labels = [m.label for m in at.metric]
    assert labels[0] == "Average price (EUR/MWh)"
    assert "EUR" not in at.metric[0].value
