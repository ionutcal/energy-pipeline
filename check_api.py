"""Verifica conexiunea la API si afiseaza structura reala a datelor.

Ruleaza o singura data, dupa ce ai pus cheia in .env:
    python check_api.py

Scopul: sa vezi numele reale ale coloanelor, ca sa poti simplifica
listele TS_CANDIDATES / VALUE_CANDIDATES din src/transform.py.
"""

import datetime as dt

import pandas as pd

from src.config import config
from src.fetch import fetch_metric, get_client

# Interval mic, ca sa nu consumi din limitele de rata ale API-ului
END = pd.Timestamp(dt.datetime.now(dt.timezone.utc))
START = END - pd.Timedelta(days=2)
COUNTRY = config.countries[0] if config.countries else "RO"


def main() -> None:
    print(f"Tara: {COUNTRY}")
    print(f"Interval: {START} -> {END}\n")

    client = get_client()

    for metric in ("load_actual", "price_day_ahead", "generation_actual"):
        print("=" * 70)
        print(f"METRICA: {metric}")
        print("=" * 70)
        try:
            df = fetch_metric(client, metric, COUNTRY, START, END)

            print(f"forma:    {df.shape}")
            print(f"index:    name={df.index.name}, tip={type(df.index).__name__}")
            print(f"coloane:  {list(df.columns)}")
            print(f"\ntipuri:\n{df.dtypes}")
            print(f"\nprimele randuri:\n{df.head(3)}")

            nuls = df.isna().sum()
            if nuls.any():
                print(f"\nvalori lipsa:\n{nuls[nuls > 0]}")
        except Exception as exc:
            print(f"ESUAT: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()