"""The overview's headline figures, and the sentences written from them.

The sentences are generated from the computed values rather than written by
hand, so they always describe the current selection. Each one appears only
when its numbers exist, and none of them claims a cause.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from src.analysis import (
    deficit_share,
    mean_price,
    negative_price_hours,
    period_renewable_share,
)

from .charts import country_name, fmt_correlation, fmt_hours, fmt_price, fmt_share

NAN = float("nan")


@dataclass(frozen=True)
class Kpis:
    """Headline figures for one country and period; NaN when not computable."""

    mean_price: float
    renewable_share: float
    deficit_share: float
    negative_price_hours: float


def compute_kpis(load: pd.DataFrame, price: pd.DataFrame, gen: pd.DataFrame) -> Kpis:
    has_price, has_gen = not price.empty, not gen.empty
    return Kpis(
        mean_price=mean_price(price) if has_price else NAN,
        renewable_share=period_renewable_share(gen) if has_gen else NAN,
        deficit_share=deficit_share(load, gen) if has_gen and not load.empty else NAN,
        negative_price_hours=negative_price_hours(price) if has_price else NAN,
    )


def _known(value: float) -> bool:
    return not math.isnan(value)


def findings(country: str, kpis: Kpis, mix: pd.Series, link: pd.DataFrame) -> list[str]:
    """Plain sentences describing the selection, from the figures that exist.

    `mix` is each source's share of generation (`analysis.energy_mix`);
    `link` has a `correlation` column per season (`analysis.seasonal_price_link`).
    """
    name = country_name(country)
    sentences = []

    if _known(kpis.renewable_share):
        sentences.append(
            f"Renewables supplied {fmt_share(kpis.renewable_share)} of the electricity "
            f"generated in {name} over this period."
        )
    if not mix.empty:
        sentences.append(f"The largest source was {mix.idxmax()}, at {fmt_share(mix.max())}.")
    if _known(kpis.mean_price):
        negative = (
            f" and was negative for {fmt_hours(kpis.negative_price_hours)}."
            if kpis.negative_price_hours > 0
            else " and never went negative."
        )
        sentences.append(f"The day-ahead price averaged {fmt_price(kpis.mean_price)}{negative}")
    if _known(kpis.deficit_share):
        sentences.append(f"Generation was below load {fmt_share(kpis.deficit_share)} of the time.")

    correlations = link["correlation"].dropna() if "correlation" in link else pd.Series(dtype=float)
    if len(correlations) >= 2:
        low, high = correlations.idxmin(), correlations.idxmax()
        sentences.append(
            "The link between renewables and price varies by season: "
            f"from {fmt_correlation(correlations[low])} in {low.lower()} "
            f"to {fmt_correlation(correlations[high])} in {high.lower()}."
        )
    return sentences


SMALL_BUCKET_SHARE = 0.02


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def bucket_note(table: pd.DataFrame) -> str:
    """The note under the price-by-bucket chart, naming the buckets that are small samples.

    Which buckets are small depends on the country: in Romania the top
    buckets are; in Hungary the lowest bucket is the largest of all. So the
    note is written from the counts rather than fixed.
    """
    note = "Hover a bar for the number of intervals behind it."
    small = [str(b) for b, n in table["intervals"].items() if n < SMALL_BUCKET_SHARE * table["intervals"].sum()]
    if len(small) == 1:
        note += f" The {small[0]} bucket holds under 2% of them."
    elif small:
        note += f" The {_join(small)} buckets each hold under 2% of them."
    return note
