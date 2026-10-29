"""Tests for the dashboard's cache: bounded, and keyed on what is actually read."""

import datetime as dt

import pandas as pd
import pytest

from dashboard import cache, data

DAY = dt.date(2026, 5, 4)


@pytest.fixture
def reads(monkeypatch, tmp_path):
    """Count the extract reads that miss the cache."""
    calls = []

    def fake_load_metric(data_dir, metric, country, start, end, tz=None):
        calls.append((metric, country, start, end, tz))
        return pd.DataFrame(columns=["value", "psr_type"])

    monkeypatch.setattr(data, "load_metric", fake_load_metric)
    monkeypatch.setenv("DASHBOARD_DATA_DIR", str(tmp_path))
    cache._frame.clear()
    yield calls
    cache._frame.clear()


def test_the_country_time_zone_and_the_default_share_one_entry(reads):
    cache.frame("price_day_ahead", "RO", DAY, DAY)
    cache.frame("price_day_ahead", "RO", DAY, DAY, tz="Europe/Bucharest")
    assert len(reads) == 1


def test_the_cache_is_bounded(reads):
    # Every visitor's selection adds an entry; on a 1 GB host the cache must evict.
    for offset in range(cache.MAX_ENTRIES + 1):
        cache.frame("load_actual", "RO", DAY, DAY + dt.timedelta(days=offset))
    cache.frame("load_actual", "RO", DAY, DAY)  # the oldest entry was evicted
    assert len(reads) == cache.MAX_ENTRIES + 2
