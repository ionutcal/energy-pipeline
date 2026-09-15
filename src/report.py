"""Analiza datelor stocate + generare de grafice.

Rulare:  python -m src.report
Iesire:  output/*.png si un rezumat in consola.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # fara interfata grafica, ruleaza si in container
import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import text

from .db import get_engine

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")


def load_frame(metric: str, country: str = "RO") -> pd.DataFrame:
    """Citeste o metrica din baza de date intr-un DataFrame indexat pe timp."""
    # Parametri legati prin SQLAlchemy (:nume), nu prin placeholder-ul unui
    # driver anume — asa merge si pe PostgreSQL, si pe SQLite in teste.
    query = text(
        """
        SELECT ts, value, psr_type
        FROM observations
        WHERE country = :country AND metric = :metric
        ORDER BY ts
        """
    )
    with get_engine().connect() as conn:
        df = pd.read_sql(query, conn, params={"country": country, "metric": metric})

    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def hourly_profile(df: pd.DataFrame) -> pd.Series:
    """Profil mediu pe ora din zi — arata varfurile de consum."""
    return df.groupby(df.index.hour)["value"].mean()


def weekday_vs_weekend(df: pd.DataFrame) -> pd.Series:
    """Media in zilele lucratoare fata de weekend."""
    is_weekend = df.index.dayofweek >= 5
    return df.groupby(is_weekend)["value"].mean().rename({False: "lucratoare", True: "weekend"})


def daily_series(df: pd.DataFrame) -> pd.Series:
    """Media zilnica — util pentru trend."""
    return df["value"].resample("D").mean()


def plot_series(series: pd.Series, title: str, ylabel: str, filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename

    fig, ax = plt.subplots(figsize=(10, 4))
    series.plot(ax=ax)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)

    logger.info("Grafic salvat: %s", path)
    return path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    for metric, label, unit in [
        ("load_actual", "Consum de energie", "MW"),
        ("price_day_ahead", "Pret day-ahead", "EUR/MWh"),
    ]:
        df = load_frame(metric)
        if df.empty:
            logger.warning("Nu exista date pentru %s — ruleaza intai pipeline-ul", metric)
            continue

        print(f"\n=== {label} ===")
        print(f"Interval: {df.index.min()} -> {df.index.max()}  ({len(df)} observatii)")
        print(f"Medie: {df['value'].mean():.1f} {unit}")
        print("\nProfil pe ora din zi:")
        print(hourly_profile(df).round(1).to_string())
        print("\nZile lucratoare vs weekend:")
        print(weekday_vs_weekend(df).round(1).to_string())

        plot_series(daily_series(df), f"{label} — medie zilnica", unit, f"{metric}_zilnic.png")
        plot_series(hourly_profile(df), f"{label} — profil orar", unit, f"{metric}_orar.png")


if __name__ == "__main__":
    main()
