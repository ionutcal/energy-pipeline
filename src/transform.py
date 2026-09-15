"""Data normalization and quality checks.

Nothing here touches the network or the database: functions take a
DataFrame and return clean structures, which makes them easy to test.

The column names below are the ones confirmed on python-entsoe 0.6.1
(see check_api.py):
    load_actual        -> timestamp, value, quantity_unit
    price_day_ahead    -> timestamp, value, currency, price_unit
    generation_actual  -> timestamp, psr_type, value, quantity_unit
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

TS_COLUMN = "timestamp"
VALUE_COLUMN = "value"

# The API responds with UN/CEFACT codes (MAW = megawatt, MWH = megawatt-hour).
# We map them to the usual notation so they read correctly in charts and reports.
UNIT_ALIASES = {"MAW": "MW", "MWH": "MWh"}


def _require_column(df: pd.DataFrame, name: str, kind: str) -> None:
    if name not in df.columns:
        raise ValueError(
            f"Could not find the {kind} column ('{name}'). "
            f"Available columns: {list(df.columns)}"
        )


def _constant(series: pd.Series) -> str | None:
    """The single value of a constant column, or None if it isn't constant."""
    values = series.dropna().unique()
    return str(values[0]) if len(values) == 1 else None


def unit_from_frame(df: pd.DataFrame, fallback: str = "") -> str:
    """Infer the unit from the API response, falling back to the given value.

    Prices come as `currency` + `price_unit` (EUR, MWH), quantities as
    `quantity_unit` (MAW). We prefer what the API says over a constant in the
    code, so we don't store a wrong unit if the response ever changes.
    """
    if "currency" in df.columns and "price_unit" in df.columns:
        currency, price_unit = _constant(df["currency"]), _constant(df["price_unit"])
        if currency and price_unit:
            return f"{currency}/{UNIT_ALIASES.get(price_unit, price_unit)}"

    if "quantity_unit" in df.columns:
        quantity_unit = _constant(df["quantity_unit"])
        if quantity_unit:
            return UNIT_ALIASES.get(quantity_unit, quantity_unit)

    return fallback


def normalize(
    df: pd.DataFrame,
    *,
    country: str,
    metric: str,
    unit: str = "",
) -> list[dict]:
    """Turn an API response into a list of rows ready to insert.

    `unit` is only a fallback: if the response carries a unit, that is the
    one we store.
    """
    if df is None or df.empty:
        logger.warning("Empty DataFrame for %s/%s", country, metric)
        return []

    df = df.reset_index() if df.index.name else df.copy()

    _require_column(df, TS_COLUMN, "time")
    _require_column(df, VALUE_COLUMN, "value")

    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(df[TS_COLUMN], utc=True),
            "value": pd.to_numeric(df[VALUE_COLUMN], errors="coerce"),
        }
    )
    # psr_type only appears for generation (the resource type). Note: codes
    # missing from python-entsoe's translation table (e.g. B25 = Energy
    # storage) stay raw, so "Fossil Gas" and "B25" share the same column.
    if "psr_type" in df.columns:
        out["psr_type"] = df["psr_type"].fillna("").astype(str)
    else:
        out["psr_type"] = ""

    before = len(out)
    out = out.dropna(subset=["value"])
    dropped = before - len(out)
    if dropped:
        logger.warning("%s/%s: dropped %d missing values", country, metric, dropped)

    before = len(out)
    out = expand_block_curve(out)
    if len(out) > before:
        logger.info(
            "%s/%s: restored %d points from the compressed (A03) curve",
            country, metric, len(out) - before,
        )

    out["country"] = country
    out["metric"] = metric
    out["unit"] = unit_from_frame(df, unit)

    return out.to_dict(orient="records")


def expand_block_curve(out: pd.DataFrame) -> pd.DataFrame:
    """Restore the positions omitted from an ENTSO-E A03 curve.

    With A03 ("variable sized block") the API only sends a point when the
    value changes; a missing position means "same value as before". All three
    collected metrics arrive this way. python-entsoe ignores `curveType` and
    returns only the points it received, so without this step a whole night
    of solar would show up as a single 0 and nuclear as one point per day —
    generation sums and averages would be wrong.

    Each series (each `psr_type`) is filled up to the last timestamp in the
    response. The package doesn't keep the period end, so that is the best
    reference available; a value filled in at the tail can be corrected on
    the next run, which re-requests the last day (see pipeline.LOOKBACK) and
    overwrites (see db.upsert_observations).
    """
    step = infer_step(pd.DatetimeIndex(out["ts"]))
    if step is None:
        return out

    end = out["ts"].max()
    parts = []
    for psr_type, series in out.groupby("psr_type", sort=False):
        series = series.drop_duplicates("ts", keep="last").set_index("ts").sort_index()
        grid = pd.date_range(series.index.min(), end, freq=step)
        series = series.reindex(grid.union(series.index)).ffill()
        series["psr_type"] = psr_type
        parts.append(series.rename_axis("ts").reset_index())
    return pd.concat(parts, ignore_index=True)


def infer_step(timestamps: pd.DatetimeIndex) -> pd.Timedelta | None:
    """The sampling step: the most common distance between two timestamps.

    ENTSO-E delivers Romanian data every 15 minutes, but resolution varies by
    country and metric, so we read it from the data instead of assuming it.
    """
    unique = pd.DatetimeIndex(timestamps.unique()).sort_values()
    if len(unique) < 3:
        return None
    diffs = pd.Series(unique).diff().dropna()
    if diffs.empty:
        return None
    return diffs.mode().iloc[0]


def quality_report(rows: list[dict], *, expected_freq=None) -> dict:
    """Simple quality checks: gaps in the series, negative values, outliers.

    `expected_freq` forces the expected step; by default it is inferred.
    """
    empty = {"n_rows": 0, "gaps": 0, "negatives": 0, "outliers": 0, "step": None}
    if not rows:
        return empty

    df = pd.DataFrame(rows).sort_values("ts")
    ts = pd.DatetimeIndex(pd.to_datetime(df["ts"], utc=True))
    unique_ts = pd.DatetimeIndex(ts.unique()).sort_values()

    step = pd.tseries.frequencies.to_offset(expected_freq) if expected_freq else infer_step(ts)
    if step is None:
        gaps = 0
    else:
        expected = pd.date_range(unique_ts.min(), unique_ts.max(), freq=step)
        gaps = len(expected.difference(unique_ts))

    values = df["value"]
    negatives = int((values < 0).sum())

    report = {
        "n_rows": len(df),
        "gaps": gaps,
        "negatives": negatives,
        "outliers": _count_outliers(df),
        "step": str(step) if step is not None else None,
    }
    logger.info("Quality report: %s", report)
    return report


def _count_outliers(df: pd.DataFrame) -> int:
    """IQR outliers — robust to skewed distributions, such as prices.

    For generation, each resource type is compared only with itself: solar
    and gas differ by orders of magnitude, and mixed together they would
    produce a meaningless threshold.
    """
    if "psr_type" in df.columns and df["psr_type"].nunique() > 1:
        return int(sum(_count_outliers(g) for _, g in df.groupby("psr_type")))

    values = df["value"]
    if len(values) < 4:
        return 0
    q1, q3 = values.quantile([0.25, 0.75])
    iqr = q3 - q1
    return int(((values < q1 - 3 * iqr) | (values > q3 + 3 * iqr)).sum())
