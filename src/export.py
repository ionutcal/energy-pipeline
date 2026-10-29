"""Export the stored observations to a Parquet extract for the dashboard.

Run:     python -m src.export [--out dashboard/data]
Output:  observations.parquet and metadata.json

The extract keeps the database's long format and its original 15-minute
resolution, so the dashboard's figures match the report's exactly. It is
small enough to commit (the full year for three countries is a few MB),
which is what lets the dashboard run on a host without database access.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from .analysis import complete_days, local_tz
from .db import get_engine

logger = logging.getLogger(__name__)

SOURCE = "ENTSO-E Transparency Platform"
DEFAULT_OUT = Path("dashboard/data")
CATEGORICAL = ["country", "metric", "psr_type", "unit"]


def _read(engine) -> pd.DataFrame:
    query = text("SELECT country, metric, psr_type, ts, value, unit FROM observations")
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    # Categoricals are written as dictionary-encoded columns: each code once.
    df[CATEGORICAL] = df[CATEGORICAL].astype("category")
    return df.sort_values(["country", "metric", "psr_type", "ts"], ignore_index=True)


def _metadata(df: pd.DataFrame) -> dict:
    coverage: dict[str, dict[str, dict[str, str]]] = {}
    spans = df.groupby(["country", "metric"], observed=True)["ts"].agg(["min", "max"])
    for (country, metric), span in spans.iterrows():
        coverage.setdefault(country, {})[metric] = {
            "first": span["min"].isoformat(),
            "last": span["max"].isoformat(),
        }
    # The dashboard's default period: complete local days of generation only.
    # A download rarely starts or ends at midnight, and a day with a few hours
    # of data gives absurd daily figures (a 100% deficit from one night hour).
    generation = df[df["metric"] == "generation_actual"]
    for country, rows in generation.groupby("country", observed=True):
        local = pd.DatetimeIndex(rows["ts"]).tz_convert(local_tz(country))
        days = complete_days(local)
        if len(days):
            coverage[country]["generation_actual"].update(
                first_complete_day=days.min().date().isoformat(),
                last_complete_day=days.max().date().isoformat(),
            )
    return {
        "source": SOURCE,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rows": len(df),
        "resolution": "15min",
        "countries": sorted(coverage),
        "coverage": coverage,
    }


def export(engine, out_dir: Path) -> dict:
    """Write `observations.parquet` and `metadata.json` to `out_dir`; return the metadata."""
    df = _read(engine)
    meta = _metadata(df)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "observations.parquet", engine="pyarrow", compression="zstd", index=False)
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    args = parser.parse_args()

    meta = export(get_engine(), args.out)
    size_mb = (args.out / "observations.parquet").stat().st_size / 1e6
    print(f"Exported {meta['rows']:,} rows for {', '.join(meta['countries'])} "
          f"to {args.out} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
