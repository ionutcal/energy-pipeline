"""Analysis of the stored data + chart generation.

Run:     python -m src.report
Output:  output/*.png and a summary in the console.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no GUI, so it also runs inside a container
import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import text

from .config import config
from .db import get_engine

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")

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

# Group order is also the color order and the stacking order in charts.
# The colors are the reference categorical palette, in its order validated
# for color vision deficiency on adjacent pairs.
GROUP_ORDER = ("Hydro", "Gas", "Wind", "Solar", "Nuclear", "Other", "Coal")
GROUP_COLORS = dict(
    zip(GROUP_ORDER, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"))
)
COLOR_MAIN = "#2a78d6"
COLOR_SURPLUS, COLOR_DEFICIT = "#2a78d6", "#e34948"
INK_MUTED, GRID = "#898781", "#e1e0d9"
# One color per country in the comparison chart, in config order. Beyond this
# many countries the lines stop being distinguishable, so the chart is capped.
COUNTRY_COLORS = ("#2a78d6", "#eb6834", "#1baf7a")


def local_tz(country: str) -> str:
    """The country's time zone; UTC when it isn't in COUNTRY_TZ."""
    return COUNTRY_TZ.get(country, "UTC")


def load_frame(metric: str, country: str = "RO") -> pd.DataFrame:
    """Read a metric from the database into a DataFrame indexed by local time."""
    # Parameters are bound through SQLAlchemy (:name), not a specific driver's
    # placeholder — so this works on both PostgreSQL and SQLite.
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
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(local_tz(country))
    return df.set_index("ts")


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


# --- charts -----------------------------------------------------------------


def _style(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, loc="left")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_MUTED)


def _save(fig, filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Chart saved: %s", path)
    return path


def plot_series(series: pd.Series, title: str, ylabel: str, filename: str) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4))
    series.plot(ax=ax, color=COLOR_MAIN, linewidth=2)
    ax.set_xlabel("")
    _style(ax, title, ylabel)
    return _save(fig, filename)


def plot_mix_profile(by_group: pd.DataFrame, filename: str = "generation_mix_hourly.png") -> Path:
    """Average profile by hour of day, stacked by resource group."""
    profile = by_group.groupby(by_group.index.hour).mean()
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.stackplot(
        profile.index,
        [profile[g] for g in profile.columns],
        labels=list(profile.columns),
        colors=[GROUP_COLORS[g] for g in profile.columns],
        edgecolor="white",
        linewidth=1,
    )
    ax.set_xlim(0, 23)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("hour (local time)")
    _style(ax, "Generation by source — average hourly profile", "MW")
    # Legend in top-to-bottom layer order, so it matches the chart visually.
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    return _save(fig, filename)


def plot_price_by_share(table: pd.DataFrame, filename: str = "price_vs_renewables.png") -> Path:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(table.index.astype(str), table["mean_price"], color=COLOR_MAIN, width=0.6)
    ax.set_xlabel("share of renewables in generation")
    _style(ax, "Average day-ahead price by share of renewables", "EUR/MWh")
    return _save(fig, filename)


def plot_balance_profile(balance: pd.Series, filename: str = "generation_minus_load_hourly.png") -> Path:
    profile = balance.groupby(balance.index.hour).mean()
    colors = [COLOR_SURPLUS if v >= 0 else COLOR_DEFICIT for v in profile]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(profile.index, profile.values, color=colors, width=0.7)
    ax.axhline(0, color=INK_MUTED, linewidth=1)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("hour (local time)")
    _style(ax, "Generation minus load — hourly average (below zero: deficit covered by imports)", "MW")
    return _save(fig, filename)


def plot_price_by_country(daily: pd.DataFrame, filename: str = "price_by_country_daily.png") -> Path:
    fig, ax = plt.subplots(figsize=(10, 4))
    for country, color in zip(daily.columns, COUNTRY_COLORS):
        ax.plot(daily.index, daily[country], color=color, linewidth=2, label=country)
    ax.legend(frameon=False, loc="upper left", ncol=len(daily.columns))
    _style(ax, "Day-ahead price by country — daily average", "EUR/MWh")
    fig.autofmt_xdate()
    return _save(fig, filename)


# --- run --------------------------------------------------------------------


def _report_load_and_price(country: str) -> None:
    for metric, label, unit in [
        ("load_actual", "Electricity load", "MW"),
        ("price_day_ahead", "Day-ahead price", "EUR/MWh"),
    ]:
        df = load_frame(metric, country)
        if df.empty:
            logger.warning("No data for %s/%s — run the pipeline first", country, metric)
            continue

        print(f"\n=== {label} ({country}) ===")
        print(f"Interval: {df.index.min()} -> {df.index.max()}  ({len(df)} observations)")
        print(f"Mean: {df['value'].mean():.1f} {unit}")
        print(f"\nProfile by hour of day ({local_tz(country)}):")
        print(hourly_profile(df).round(1).to_string())
        print("\nWeekdays vs weekend:")
        print(weekday_vs_weekend(df).round(1).to_string())

        plot_series(daily_series(df), f"{label} — daily average", unit, f"{metric}_daily.png")
        plot_series(hourly_profile(df), f"{label} — hourly profile", unit, f"{metric}_hourly.png")


def _report_generation(country: str) -> None:
    gen = load_frame("generation_actual", country)
    if gen.empty:
        logger.warning("No generation data for %s — run the pipeline first", country)
        return

    print(f"\n=== Generation mix ({country}) ===")
    print("Share of energy generated (%):")
    print((energy_mix(gen) * 100).round(1).sort_values(ascending=False).to_string())

    daily_share = daily_renewable_share(gen)
    print(
        f"\nRenewables: {daily_share.mean() * 100:.1f}% per day on average "
        f"(min {daily_share.min() * 100:.1f}% on {daily_share.idxmin():%Y-%m-%d}, "
        f"max {daily_share.max() * 100:.1f}% on {daily_share.idxmax():%Y-%m-%d})"
    )
    plot_mix_profile(generation_by_group(gen))
    plot_series(daily_share * 100, "Share of renewables — daily", "%", "renewables_daily.png")

    price = load_frame("price_day_ahead", country)
    if not price.empty:
        table, corr = price_by_renewable_share(price, renewable_share(gen))
        print("\nAverage price by share of renewables:")
        print(table.round(1).to_string())
        print(f"Correlation between price and renewable share: {corr:.2f}")
        plot_price_by_share(table)

    load = load_frame("load_actual", country)
    if not load.empty:
        balance = net_balance(load, gen)
        deficit = (balance < 0).mean() * 100
        print(
            f"\nGeneration minus load: {balance.mean():.0f} MW on average; "
            f"deficit (imports) {deficit:.0f}% of the time"
        )
        plot_balance_profile(balance)


def _report_price_comparison(countries: list[str]) -> None:
    if len(countries) > len(COUNTRY_COLORS):
        logger.warning(
            "Comparing only the first %d countries: %s", len(COUNTRY_COLORS), countries[: len(COUNTRY_COLORS)]
        )
        countries = countries[: len(COUNTRY_COLORS)]

    prices = {c: load_frame("price_day_ahead", c) for c in countries}
    missing = [c for c, df in prices.items() if df.empty]
    if missing:
        logger.warning("No price data for %s — skipping the country comparison", missing)
        return

    reference = countries[0]
    table = price_comparison(prices, reference)
    print(f"\n=== Day-ahead price by country (reference: {reference}) ===")
    print(
        table.assign(same_price_share=table["same_price_share"] * 100)
        .rename(columns={"same_price_share": "same_price_%"})
        .round(1)
        .to_string()
    )
    plot_price_by_country(daily_price_by_country(prices, local_tz(reference)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    countries = config.countries or ["RO"]
    # The detailed report covers the first country; the others are compared on price.
    primary = countries[0]
    _report_load_and_price(primary)
    _report_generation(primary)
    if len(countries) > 1:
        _report_price_comparison(countries)


if __name__ == "__main__":
    main()
