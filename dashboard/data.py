"""Read the Parquet extract for the dashboard.

DuckDB runs SQL directly on the Parquet file and reads only the rows for the
selected country, metric and period. The result has the shape the analysis
functions expect — indexed by local time, with `value` and `psr_type` — the
same shape `src.report.load_frame` returns from the database.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import pandas as pd

from src.analysis import local_tz

DEFAULT_DATA_DIR = Path(__file__).parent / "data"
COLUMNS = ["value", "psr_type"]


class ExtractMissing(FileNotFoundError):
    """The Parquet extract hasn't been created yet."""


def _require(path: Path, data_dir: Path) -> Path:
    if not path.exists():
        raise ExtractMissing(
            f"No data extract found in {data_dir}. "
            f"Create it from the database with: python -m src.export --out {data_dir}"
        )
    return path


def load_metadata(data_dir: Path) -> dict:
    """The extract's metadata: source, export time, countries and coverage."""
    _require(data_dir / "observations.parquet", data_dir)
    return json.loads(_require(data_dir / "metadata.json", data_dir).read_text())


def default_period(meta: dict, country: str) -> tuple[dt.date, dt.date]:
    """First and last complete local day of generation data for the country.

    Day-ahead prices run one day further, into tomorrow, and a download rarely
    starts or ends at midnight; using complete generation days keeps every
    figure on the same, whole days. Extracts written before complete days were
    recorded fall back to the first and last timestamp, and a country without
    generation data to the span of its other metrics.
    """
    coverage = meta["coverage"][country]
    span = coverage.get("generation_actual")
    if span and "first_complete_day" in span:
        return (dt.date.fromisoformat(span["first_complete_day"]),
                dt.date.fromisoformat(span["last_complete_day"]))
    spans = [span] if span else list(coverage.values())
    tz = local_tz(country)
    first = min(pd.Timestamp(s["first"]) for s in spans).tz_convert(tz).date()
    last = max(pd.Timestamp(s["last"]) for s in spans).tz_convert(tz).date()
    return first, last


def _utc_bounds(start: dt.date, end: dt.date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inclusive local days -> [start 00:00, end + 1 day 00:00) as UTC instants."""
    lower = pd.Timestamp(start).tz_localize(tz)
    upper = pd.Timestamp(end + dt.timedelta(days=1)).tz_localize(tz)
    return lower.tz_convert("UTC"), upper.tz_convert("UTC")


def load_metric(
    data_dir: Path,
    metric: str,
    country: str,
    start: dt.date,
    end: dt.date,
    tz: str | None = None,
) -> pd.DataFrame:
    """One metric for one country over inclusive local days, indexed by local time.

    `tz` sets the time zone the days are read in; it defaults to the country's.
    """
    tz = tz or local_tz(country)
    lower, upper = _utc_bounds(start, end, tz)
    path = _require(data_dir / "observations.parquet", data_dir)
    source = str(path).replace("'", "''")
    query = f"""
        SELECT ts, value, psr_type
        FROM read_parquet('{source}')
        WHERE country = ? AND metric = ? AND ts >= ? AND ts < ?
        ORDER BY ts, psr_type
    """
    params = [country, metric, lower.to_pydatetime(), upper.to_pydatetime()]
    with duckdb.connect() as conn:
        df = conn.sql(query, params=params).to_arrow_table().to_pandas()

    index = pd.DatetimeIndex(pd.to_datetime(df["ts"], utc=True), name="ts").tz_convert(tz)
    return pd.DataFrame(
        {"value": df["value"].to_numpy(dtype=float), "psr_type": df["psr_type"].astype(str).to_numpy()},
        index=index,
        columns=COLUMNS,
    )
