"""Checks the API connection and prints the real shape of the data.

Run it after putting the key in .env:
    python check_api.py

Purpose: see the real column names, units and resolution returned by the
API, so you can confirm the assumptions in src/transform.py still hold
(for example after upgrading python-entsoe).
"""

import datetime as dt

import pandas as pd

from src.config import config
from src.fetch import fetch_metric, get_client

# A small interval, so we don't eat into the API's rate limits
END = pd.Timestamp(dt.datetime.now(dt.timezone.utc))
START = END - pd.Timedelta(days=2)
COUNTRY = config.countries[0] if config.countries else "RO"


def main() -> None:
    print(f"Country: {COUNTRY}")
    print(f"Interval: {START} -> {END}\n")

    client = get_client()

    for metric in ("load_actual", "price_day_ahead", "generation_actual"):
        print("=" * 70)
        print(f"METRIC: {metric}")
        print("=" * 70)
        try:
            df = fetch_metric(client, metric, COUNTRY, START, END)

            print(f"shape:    {df.shape}")
            print(f"index:    name={df.index.name}, type={type(df.index).__name__}")
            print(f"columns:  {list(df.columns)}")
            print(f"\ndtypes:\n{df.dtypes}")
            print(f"\nfirst rows:\n{df.head(3)}")

            nulls = df.isna().sum()
            if nulls.any():
                print(f"\nmissing values:\n{nulls[nulls > 0]}")
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()