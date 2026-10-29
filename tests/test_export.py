"""Tests for the Parquet extract read by the dashboard."""

import datetime as dt
import json

import pandas as pd

from tests.conftest import SEED_START, seed_rows


def _extract(extract_dir):
    return pd.read_parquet(extract_dir / "observations.parquet")


def test_export_round_trips_every_row(extract_dir):
    seeded = seed_rows()
    extract = _extract(extract_dir)
    assert len(extract) == len(seeded)
    assert extract["value"].sum() == sum(r["value"] for r in seeded)


def test_export_keeps_utc_timestamps(extract_dir):
    extract = _extract(extract_dir)
    assert str(extract["ts"].dt.tz) == "UTC"
    ro_load = extract[(extract["country"] == "RO") & (extract["metric"] == "load_actual")]
    assert ro_load["ts"].min() == pd.Timestamp(SEED_START)


def test_export_metadata(extract_dir):
    meta = json.loads((extract_dir / "metadata.json").read_text())
    assert meta["source"] == "ENTSO-E Transparency Platform"
    assert meta["rows"] == len(seed_rows())
    assert meta["resolution"] == "15min"
    assert meta["countries"] == ["HU", "RO"]
    ro = meta["coverage"]["RO"]
    last_price = dt.datetime.fromisoformat(ro["price_day_ahead"]["last"])
    last_generation = dt.datetime.fromisoformat(ro["generation_actual"]["last"])
    assert last_price - last_generation == dt.timedelta(days=1)
    assert dt.datetime.fromisoformat(meta["exported_at"]).tzinfo is not None
