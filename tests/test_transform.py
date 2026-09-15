import pandas as pd
import pytest

from src.transform import normalize, quality_report


def _frame(n=24, value=100.0):
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "value": [value] * n,
        }
    )


def test_normalize_produces_correct_rows():
    rows = normalize(_frame(3), country="RO", metric="load_actual", unit="MW")
    assert len(rows) == 3
    assert rows[0]["country"] == "RO"
    assert rows[0]["metric"] == "load_actual"
    assert rows[0]["unit"] == "MW"
    assert rows[0]["psr_type"] == ""


def test_normalize_drops_missing_values():
    # A missing value at the start of the series has nothing to be filled from.
    df = _frame(4)
    df.loc[0, "value"] = None
    rows = normalize(df, country="RO", metric="load_actual")
    assert len(rows) == 3


def test_normalize_empty_dataframe():
    assert normalize(pd.DataFrame(), country="RO", metric="load_actual") == []


def test_normalize_without_time_column_raises_clear_error():
    df = pd.DataFrame({"something_else": [1, 2], "value": [10, 20]})
    with pytest.raises(ValueError, match="time column"):
        normalize(df, country="RO", metric="load_actual")


def test_normalize_keeps_psr_type():
    df = _frame(2)
    df["psr_type"] = "B19"
    rows = normalize(df, country="RO", metric="generation_actual")
    assert all(r["psr_type"] == "B19" for r in rows)


def test_quality_report_detects_gaps():
    rows = normalize(_frame(5), country="RO", metric="load_actual")
    del rows[2]  # remove one hour from the middle
    report = quality_report(rows)
    assert report["gaps"] == 1
    assert report["n_rows"] == 4


def test_quality_report_detects_negatives():
    df = _frame(4)
    df.loc[0, "value"] = -50
    rows = normalize(df, country="RO", metric="price_day_ahead")
    assert quality_report(rows)["negatives"] == 1


def test_quality_report_on_empty_list():
    assert quality_report([])["n_rows"] == 0


# --- real response shapes, as returned by the API (check_api.py) ---


def _load_frame(n=8):
    """load_actual: timestamp, value, quantity_unit — every 15 minutes."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-09-10 14:00", periods=n, freq="15min", tz="UTC"),
            "value": [6248.0 + i for i in range(n)],
            "quantity_unit": "MAW",
        }
    )


def _price_frame(n=8):
    """price_day_ahead: timestamp, value, currency, price_unit."""
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-09-09 22:00", periods=n, freq="15min", tz="UTC"),
            "value": [184.84 - i for i in range(n)],
            "currency": "EUR",
            "price_unit": "MWH",
        }
    )


def test_quantity_unit_comes_from_the_api():
    rows = normalize(_load_frame(), country="RO", metric="load_actual", unit="MW")
    assert rows[0]["unit"] == "MW"  # MAW mapped to the usual notation


def test_price_unit_combines_currency_and_unit():
    rows = normalize(_price_frame(), country="RO", metric="price_day_ahead", unit="")
    assert rows[0]["unit"] == "EUR/MWh"


def test_unit_falls_back_to_default_when_missing_from_response():
    df = _load_frame().drop(columns=["quantity_unit"])
    rows = normalize(df, country="RO", metric="load_actual", unit="MW")
    assert rows[0]["unit"] == "MW"


def test_gaps_are_detected_at_the_real_15_minute_resolution():
    rows = normalize(_load_frame(20), country="RO", metric="load_actual")
    del rows[5:9]  # four consecutive quarter hours missing
    report = quality_report(rows)
    assert report["gaps"] == 4
    assert report["step"] == "0 days 00:15:00"


def test_outliers_are_computed_per_resource_type():
    # Small solar, large gas: mixed together, a global IQR threshold finds nothing.
    df = pd.concat(
        [
            _load_frame(12).assign(psr_type="Solar", value=50.0),
            _load_frame(12).assign(psr_type="Fossil Gas", value=1500.0),
        ],
        ignore_index=True,
    )
    df.loc[0, "value"] = 5000.0  # extreme only relative to Solar
    rows = normalize(df, country="RO", metric="generation_actual")
    assert quality_report(rows)["outliers"] == 1


def test_psr_type_is_always_stored_as_a_code():
    # python-entsoe returns names for B01–B20 and the raw code for B25.
    df = pd.concat(
        [_load_frame(4).assign(psr_type=p) for p in ("B25", "Fossil Gas", "Biomass")],
        ignore_index=True,
    )
    rows = normalize(df, country="RO", metric="generation_actual")
    assert {r["psr_type"] for r in rows} == {"B25", "B04", "B01"}


def test_unknown_resource_type_is_kept_as_is():
    df = _load_frame(4).assign(psr_type="Tidal kite")
    rows = normalize(df, country="RO", metric="generation_actual")
    assert {r["psr_type"] for r in rows} == {"Tidal kite"}


# --- compressed A03 curves: a point only when the value changes ---


def _a03(points, psr_type="Solar"):
    """Build a compressed series from {position: value}, every 15 minutes."""
    start = pd.Timestamp("2026-09-14 00:00", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": [start + pd.Timedelta(minutes=15 * (p - 1)) for p in points],
            "psr_type": psr_type,
            "value": list(points.values()),
            "quantity_unit": "MAW",
        }
    )


def test_omitted_positions_repeat_the_previous_value():
    # How real solar arrives: a 0 for the whole night, sent once.
    rows = normalize(_a03({1: 0.0, 5: 6.0, 6: 50.0}), country="RO", metric="generation_actual")
    assert [r["value"] for r in rows] == [0.0, 0.0, 0.0, 0.0, 6.0, 50.0]


def test_constant_series_extends_to_the_last_timestamp_in_the_response():
    # Real nuclear arrives as a single point per day.
    df = pd.concat(
        [_a03({1: 1400.0}, "Nuclear"), _a03({1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0}, "Wind Onshore")],
        ignore_index=True,
    )
    rows = normalize(df, country="RO", metric="generation_actual")
    nuclear = [r["value"] for r in rows if r["psr_type"] == "B14"]
    assert nuclear == [1400.0] * 4


def test_generation_total_is_complete_after_restoring():
    df = pd.concat(
        [_a03({1: 100.0, 4: 200.0}, "Fossil Gas"), _a03({1: 0.0, 2: 5.0, 3: 9.0, 4: 12.0}, "Solar")],
        ignore_index=True,
    )
    rows = pd.DataFrame(normalize(df, country="RO", metric="generation_actual"))
    total = rows.groupby("ts")["value"].sum().tolist()
    assert total == [100.0, 105.0, 109.0, 212.0]
