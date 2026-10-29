"""Calculations on the stored observations, shared by the report and the dashboard.

Every function takes DataFrames indexed by local time, with a `value` column
(and `psr_type` for generation), as returned by `report.load_frame` and
`dashboard.data.load_metric`. Nothing here touches the database, the network
or a plotting library, so the same numbers reach the console report, the PNG
charts and the dashboard.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Hours and days are read in each country's local time: in UTC, Romania's
# 20:00 evening peak would show up at 17:00, and days would be cut at 03:00.
COUNTRY_TZ = {
    "RO": "Europe/Bucharest",
    "BG": "Europe/Sofia",
    "HU": "Europe/Budapest",
    "AT": "Europe/Vienna",
    "CZ": "Europe/Prague",
    "SK": "Europe/Bratislava",
    "PL": "Europe/Warsaw",
    "RS": "Europe/Belgrade",
    "HR": "Europe/Zagreb",
    "SI": "Europe/Ljubljana",
    "GR": "Europe/Athens",
    "DE": "Europe/Berlin",
}

# psr_type code (see src/psr.py) -> (display group, renewable).
# Groups stay under 8 so each gets a distinct color in the charts.
PSR_GROUPS = {
    "B01": ("Other", True),  # Biomass
    "B02": ("Coal", False),  # Fossil Brown coal/Lignite
    "B03": ("Coal", False),  # Fossil Coal-derived gas
    "B04": ("Gas", False),  # Fossil Gas
    "B05": ("Coal", False),  # Fossil Hard coal
    "B06": ("Other", False),  # Fossil Oil
    "B07": ("Other", False),  # Fossil Oil shale
    "B08": ("Other", False),  # Fossil Peat
    "B09": ("Other", True),  # Geothermal
    "B10": ("Other", False),  # Hydro Pumped Storage: stored energy, not a primary source
    "B11": ("Hydro", True),  # Hydro Run-of-river and poundage
    "B12": ("Hydro", True),  # Hydro Water Reservoir
    "B13": ("Other", True),  # Marine
    "B14": ("Nuclear", False),  # Nuclear
    "B15": ("Other", True),  # Other renewable
    "B16": ("Solar", True),  # Solar
    "B17": ("Other", False),  # Waste
    "B18": ("Wind", True),  # Wind Offshore
    "B19": ("Wind", True),  # Wind Onshore
    "B20": ("Other", False),  # Other
    "B25": ("Other", False),  # Energy storage
}
UNKNOWN_GROUP = ("Other", False)

# Group order is also the stacking order in charts and the order of their
# colours (see src/style.py).
GROUP_ORDER = ("Hydro", "Gas", "Wind", "Solar", "Nuclear", "Other", "Coal")


def local_tz(country: str) -> str:
    """The country's time zone; UTC when it isn't in COUNTRY_TZ."""
    return COUNTRY_TZ.get(country, "UTC")


# --- load and price ---------------------------------------------------------


def hourly_profile(df: pd.DataFrame) -> pd.Series:
    """Average profile by hour of day — shows the load peaks."""
    return df.groupby(df.index.hour)["value"].mean()


def weekday_vs_weekend(df: pd.DataFrame) -> pd.Series:
    """Average on weekdays versus weekends."""
    is_weekend = df.index.dayofweek >= 5
    return df.groupby(is_weekend)["value"].mean().rename({False: "weekday", True: "weekend"})


def complete_days(index: pd.DatetimeIndex, min_coverage: float = 0.9) -> pd.DatetimeIndex:
    """Local days with at least `min_coverage` of a typical day's timestamps.

    The first and last day of a download are usually partial; a day that
    starts at 18:00 has no solar and would artificially drag down any daily
    average or share. We compare against the median so days with a clock
    change (23 or 25 hours) aren't dropped.
    """
    counts = pd.Series(1, index=index.unique()).resample("D").sum()
    return counts.index[counts >= counts.median() * min_coverage]


def daily_series(df: pd.DataFrame) -> pd.Series:
    """Daily average — useful for trends. Complete days only."""
    daily = df["value"].resample("D").mean()
    return daily.reindex(complete_days(df.index))


# --- generation mix ---------------------------------------------------------


def _psr_info(psr_type: str) -> tuple[str, bool]:
    return PSR_GROUPS.get(psr_type, UNKNOWN_GROUP)


def generation_by_group(gen: pd.DataFrame) -> pd.DataFrame:
    """Generation by group: one row per timestamp, one column per group (MW)."""
    frame = gen.reset_index()
    unknown = set(frame["psr_type"]) - set(PSR_GROUPS)
    if unknown:
        logger.warning("Unknown resource types, grouped as Other: %s", sorted(unknown))
    frame["group"] = frame["psr_type"].map(lambda p: _psr_info(p)[0])
    wide = frame.pivot_table(
        index="ts", columns="group", values="value", aggfunc="sum", fill_value=0.0
    )
    return wide.reindex(columns=[g for g in GROUP_ORDER if g in wide.columns])


def _renewable_and_total(gen: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    frame = gen.reset_index()
    renewable = frame["psr_type"].map(lambda p: _psr_info(p)[1])
    total = frame.groupby("ts")["value"].sum()
    renew = frame[renewable.values].groupby("ts")["value"].sum().reindex(total.index, fill_value=0.0)
    return renew, total


def renewable_share(gen: pd.DataFrame) -> pd.Series:
    """Share of renewables in generation at each timestamp (0..1)."""
    renew, total = _renewable_and_total(gen)
    return (renew / total).where(total > 0)


def daily_renewable_share(gen: pd.DataFrame) -> pd.Series:
    """Daily share of renewables, weighted by energy.

    Not the mean of per-timestamp shares: a low-output quarter hour at night
    would weigh as much as one at noon.
    """
    renew, total = _renewable_and_total(gen)
    daily_total = total.resample("D").sum()
    share = (renew.resample("D").sum() / daily_total).where(daily_total > 0)
    return share.reindex(complete_days(total.index))


def energy_mix(gen: pd.DataFrame) -> pd.Series:
    """Each group's share of the energy generated over the whole period (0..1)."""
    totals = generation_by_group(gen).sum()
    return totals / totals.sum()


def price_by_renewable_share(
    price: pd.DataFrame, share: pd.Series
) -> tuple[pd.DataFrame, float]:
    """Average price per renewable-share bucket + their correlation.

    Only timestamps that have both a price and generation are used.
    """
    joined = pd.concat({"price": price["value"], "share": share}, axis=1, join="inner").dropna()
    if joined.empty:
        return pd.DataFrame(columns=["mean_price", "intervals"]), float("nan")

    edges = [i / 10 for i in range(11)]
    labels = [f"{int(a * 100)}-{int(b * 100)}%" for a, b in zip(edges, edges[1:])]
    buckets = pd.cut(joined["share"], edges, labels=labels, include_lowest=True)
    table = (
        joined.groupby(buckets, observed=True)["price"]
        .agg(mean_price="mean", intervals="count")
    )
    return table, float(joined["price"].corr(joined["share"]))


def net_balance(load: pd.DataFrame, gen: pd.DataFrame) -> pd.Series:
    """Generation minus load (MW): positive = surplus, negative = deficit.

    This approximates the import/export balance: reported load and reported
    generation don't cover exactly the same installations (losses, pumping,
    self-consumption).
    """
    total = gen.groupby(level=0)["value"].sum()
    return (total - load["value"]).dropna()


# --- period-level figures ---------------------------------------------------


def period_renewable_share(gen: pd.DataFrame) -> float:
    """Renewables' share of all the energy generated in the frame (0..1).

    Weighted by energy, like `daily_renewable_share`; NaN without generation.
    """
    renew, total = _renewable_and_total(gen)
    total_energy = total.sum()
    return float(renew.sum() / total_energy) if total_energy > 0 else float("nan")


def mean_price(price: pd.DataFrame) -> float:
    """Average of the day-ahead prices in the frame (EUR/MWh)."""
    return float(price["value"].mean())


def negative_price_hours(price: pd.DataFrame) -> float:
    """Hours with a negative day-ahead price: quarter hours below zero, over four."""
    return float((price["value"] < 0).sum() / 4)


def negative_price_hours_by_month(price: pd.DataFrame) -> pd.Series:
    """Negative-price hours per local month, including partial months and months with none."""
    negative = (price["value"] < 0).astype(float)
    return negative.resample("MS").sum() / 4


def deficit_share(load: pd.DataFrame, gen: pd.DataFrame) -> float:
    """Share of intervals where generation was below load (0..1); NaN without overlap."""
    balance = net_balance(load, gen)
    return float((balance < 0).mean()) if not balance.empty else float("nan")


# --- seasons and months -----------------------------------------------------

SEASONS = ("Winter", "Spring", "Summer", "Autumn")
_SEASON_BY_MONTH = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Autumn", 10: "Autumn", 11: "Autumn",
}

# A month counts only if most of its days are complete; otherwise the partial
# first and last months of a download would skew monthly figures.
MIN_DAYS_PER_MONTH = 20


def season_of(index: pd.DatetimeIndex) -> pd.Index:
    """Meteorological season of each timestamp (December belongs to winter)."""
    return pd.Index([_SEASON_BY_MONTH[m] for m in index.month], name="season")


def _on_complete_days(frame: pd.DataFrame | pd.Series):
    """Keep only rows that fall on complete local days."""
    days = complete_days(pd.DatetimeIndex(frame.index))
    return frame[frame.index.normalize().isin(days)]


def complete_months(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Month starts with at least MIN_DAYS_PER_MONTH complete days."""
    days = complete_days(pd.DatetimeIndex(index))
    per_month = pd.Series(1, index=days).resample("MS").sum()
    return per_month.index[per_month >= MIN_DAYS_PER_MONTH]


def monthly_mix(gen: pd.DataFrame) -> pd.DataFrame:
    """Each group's share of the energy generated, per complete month (0..1)."""
    by_group = _on_complete_days(generation_by_group(gen))
    monthly = by_group.resample("MS").sum()
    monthly = monthly.reindex(complete_months(by_group.index))
    return monthly.div(monthly.sum(axis=1), axis=0)


def monthly_summary(gen: pd.DataFrame, price: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per complete month: renewable share (energy-weighted) and mean price."""
    renew, total = _renewable_and_total(gen)
    renew, total = _on_complete_days(renew), _on_complete_days(total)
    months = complete_months(total.index)
    summary = pd.DataFrame(
        {"renewable_share": (renew.resample("MS").sum() / total.resample("MS").sum())}
    ).reindex(months)
    if price is not None and not price.empty:
        summary["mean_price"] = _on_complete_days(price["value"]).resample("MS").mean().reindex(months)
    return summary


def seasonal_price_link(price: pd.DataFrame, gen: pd.DataFrame) -> pd.DataFrame:
    """Per season: mean price, renewable share and their correlation.

    The correlation is computed within each season, so it isn't just the
    summer-versus-winter difference showing up as a link.
    """
    share = renewable_share(gen)
    joined = pd.concat({"price": price["value"], "share": share}, axis=1, join="inner").dropna()
    if joined.empty:
        return pd.DataFrame(columns=["mean_price", "mean_renewable_share", "correlation", "intervals"])
    joined["season"] = season_of(pd.DatetimeIndex(joined.index)).values
    grouped = joined.groupby("season")
    table = pd.DataFrame(
        {
            "mean_price": grouped["price"].mean(),
            "mean_renewable_share": grouped["share"].mean(),
            "correlation": grouped.apply(_correlation, include_groups=False),
            "intervals": grouped.size(),
        }
    )
    return table.reindex([s for s in SEASONS if s in table.index])


def _correlation(group: pd.DataFrame) -> float:
    """Price–share correlation, or NaN when either side doesn't vary at all."""
    if group["price"].nunique() < 2 or group["share"].nunique() < 2:
        return float("nan")
    return float(group["price"].corr(group["share"]))


def seasonal_hourly_mix(gen: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Average hourly generation profile by group, for each season in the data."""
    by_group = generation_by_group(gen)
    seasons = season_of(pd.DatetimeIndex(by_group.index))
    profiles = {}
    for season in SEASONS:
        part = by_group[seasons == season]
        if not part.empty:
            profiles[season] = part.groupby(part.index.hour).mean()
    return profiles


# --- country comparison -----------------------------------------------------


def price_comparison(prices: dict[str, pd.DataFrame], reference: str) -> pd.DataFrame:
    """Compare day-ahead prices across countries, against a reference country.

    Only moments priced in every country are used, so each figure covers the
    same intervals. `same_price_share` is how often a country's price equals
    the reference to the cent: coupled markets clear at the same price unless
    the interconnectors between them are full.
    """
    joined = pd.concat({c: df["value"] for c, df in prices.items()}, axis=1, join="inner").dropna()
    if joined.empty:
        return pd.DataFrame(columns=["mean_price", "mean_abs_spread", "same_price_share"])

    ref = joined[reference]
    spread = joined.sub(ref, axis=0)
    return pd.DataFrame(
        {
            "mean_price": joined.mean(),
            "mean_abs_spread": spread.abs().mean(),
            "same_price_share": (spread.abs() < 0.005).mean(),
        }
    )


def daily_price_by_country(prices: dict[str, pd.DataFrame], tz: str) -> pd.DataFrame:
    """Daily average price per country, with days cut in one shared time zone.

    Complete days only, judged by the moments every country has in common.
    """
    joined = pd.concat({c: df["value"] for c, df in prices.items()}, axis=1, join="inner").dropna()
    if joined.empty:
        return joined
    joined.index = joined.index.tz_convert(tz)
    daily = joined.resample("D").mean()
    return daily.reindex(complete_days(joined.index))
