"""Console report and PNG charts from the stored data.

Run:     python -m src.report
Output:  output/*.png and a summary in the console.

The figures are computed in src/analysis.py, shared with the dashboard; this
module reads the database, prints the summary and draws the charts.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no GUI, so it also runs inside a container
import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import text

from .analysis import (
    SEASONS,
    complete_months,
    daily_price_by_country,
    daily_renewable_share,
    daily_series,
    energy_mix,
    generation_by_group,
    hourly_profile,
    local_tz,
    monthly_mix,
    monthly_summary,
    net_balance,
    price_by_renewable_share,
    price_comparison,
    renewable_share,
    seasonal_hourly_mix,
    seasonal_price_link,
    weekday_vs_weekend,
)
from .config import config
from .db import get_engine
from .style import (
    COLOR_DEFICIT,
    COLOR_MAIN,
    COLOR_SURPLUS,
    COUNTRY_COLORS,
    GRID,
    GROUP_COLORS,
    INK_MUTED,
)

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")


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


def plot_seasonal_mix(profiles: dict[str, pd.DataFrame], filename: str = "generation_mix_by_season.png") -> Path:
    """Hourly generation profile per season, as small multiples on one scale."""
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True, sharey=True)
    top = max(p.sum(axis=1).max() for p in profiles.values())
    for ax, season in zip(axes.flat, SEASONS):
        if season not in profiles:
            ax.set_visible(False)
            continue
        profile = profiles[season]
        ax.stackplot(
            profile.index,
            [profile[g] for g in profile.columns],
            labels=list(profile.columns),
            colors=[GROUP_COLORS[g] for g in profile.columns],
            edgecolor="white",
            linewidth=0.8,
        )
        ax.set_xlim(0, 23)
        ax.set_ylim(0, top * 1.05)
        ax.set_xticks(range(0, 24, 4))
        _style(ax, season, "MW")
    for ax in axes[1]:
        ax.set_xlabel("hour (local time)")
    # One legend for all panels, in top-to-bottom layer order.
    first = next(ax for ax in axes.flat if ax.get_visible())
    handles, labels = first.get_legend_handles_labels()
    fig.legend(handles[::-1], labels[::-1], loc="center right", frameon=False)
    fig.suptitle("Generation by source — average hourly profile per season", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 0.88, 1))
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Chart saved: %s", path)
    return path


def plot_monthly_mix(mix: pd.DataFrame, filename: str = "generation_mix_monthly.png") -> Path:
    """Share of each source per month, as 100% stacked bars."""
    fig, ax = plt.subplots(figsize=(11, 4.5))
    labels = [f"{m:%b %Y}" for m in mix.index]
    bottom = pd.Series(0.0, index=mix.index)
    for group in mix.columns:
        ax.bar(labels, mix[group] * 100, bottom=bottom * 100, color=GROUP_COLORS[group],
               label=group, width=0.75, edgecolor="white", linewidth=0.8)
        bottom += mix[group]
    ax.set_ylim(0, 100)
    _style(ax, "Generation mix by month — share of energy generated", "%")
    handles, names = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], names[::-1], loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    fig.autofmt_xdate()
    return _save(fig, filename)


def plot_monthly_summary(summary: pd.DataFrame, filename: str = "renewables_and_price_monthly.png") -> Path:
    """Renewable share and mean price per month, as two panels sharing the months.

    Two panels rather than two y-axes on one plot: the scales are unrelated,
    and aligning them would suggest a relationship the chart can't show.
    """
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    labels = [f"{m:%b %Y}" for m in summary.index]
    top.plot(labels, summary["renewable_share"] * 100, color=GROUP_COLORS["Wind"], linewidth=2, marker="o")
    _style(top, "Share of renewables in generation — monthly", "%")
    if "mean_price" in summary:
        bottom.plot(labels, summary["mean_price"], color=COLOR_MAIN, linewidth=2, marker="o")
    _style(bottom, "Day-ahead price — monthly average", "EUR/MWh")
    fig.autofmt_xdate()
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

    _report_seasons(gen, price)


def _report_seasons(gen: pd.DataFrame, price: pd.DataFrame) -> None:
    """Monthly and seasonal views — only once the data spans several months."""
    months = complete_months(pd.DatetimeIndex(gen.index.unique()))
    if len(months) < 3:
        logger.info("Seasonal analysis skipped: needs at least 3 complete months, found %d", len(months))
        return

    print(f"\n=== Seasons and months ({len(months)} complete months) ===")
    summary = monthly_summary(gen, price)
    print("Per month:")
    print(
        summary.assign(renewable_share=summary["renewable_share"] * 100)
        .rename(columns={"renewable_share": "renewables_%"})
        .rename(index=lambda m: f"{m:%Y-%m}")
        .round(1)
        .to_string()
    )
    plot_monthly_summary(summary)
    plot_monthly_mix(monthly_mix(gen))
    plot_seasonal_mix(seasonal_hourly_mix(gen))

    if not price.empty:
        link = seasonal_price_link(price, gen)
        print("\nPrice and renewables by season:")
        print(
            link.assign(mean_renewable_share=link["mean_renewable_share"] * 100)
            .rename(columns={"mean_renewable_share": "renewables_%"})
            .round(2)
            .to_string()
        )


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
