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

# Orele si zilele se citesc in ora Romaniei: in UTC, varful de seara de la
# 20:00 ar aparea la 17:00, iar zilele s-ar taia la 03:00 dimineata.
LOCAL_TZ = "Europe/Bucharest"

# psr_type (denumirea din python-entsoe sau codul brut) -> (grup afisat, regenerabil).
# Grupele raman sub 8 ca fiecare sa aiba o culoare distincta si pe grafic.
PSR_GROUPS = {
    "Hydro Run-of-river and poundage": ("Hidro", True),
    "Hydro Water Reservoir": ("Hidro", True),
    "Hydro Pumped Storage": ("Altele", False),
    "Fossil Gas": ("Gaz", False),
    "Wind Onshore": ("Eolian", True),
    "Wind Offshore": ("Eolian", True),
    "Solar": ("Solar", True),
    "Nuclear": ("Nuclear", False),
    "Fossil Brown coal/Lignite": ("Carbune", False),
    "Fossil Hard coal": ("Carbune", False),
    "Biomass": ("Altele", True),
    "B25": ("Altele", False),  # Energy storage, netradus de python-entsoe
}
UNKNOWN_GROUP = ("Altele", False)

# Ordinea grupelor este si ordinea culorilor si a straturilor pe grafic.
# Culorile sunt paleta categorica de referinta, in ordinea ei validata
# pentru daltonism pe perechi alaturate.
GROUP_ORDER = ("Hidro", "Gaz", "Eolian", "Solar", "Nuclear", "Altele", "Carbune")
GROUP_COLORS = dict(
    zip(GROUP_ORDER, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"))
)
COLOR_MAIN = "#2a78d6"
COLOR_SURPLUS, COLOR_DEFICIT = "#2a78d6", "#e34948"
INK_MUTED, GRID = "#898781", "#e1e0d9"


def load_frame(metric: str, country: str = "RO") -> pd.DataFrame:
    """Citeste o metrica din baza de date intr-un DataFrame indexat pe timp local."""
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
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(LOCAL_TZ)
    return df.set_index("ts")


# --- consum si pret ---------------------------------------------------------


def hourly_profile(df: pd.DataFrame) -> pd.Series:
    """Profil mediu pe ora din zi — arata varfurile de consum."""
    return df.groupby(df.index.hour)["value"].mean()


def weekday_vs_weekend(df: pd.DataFrame) -> pd.Series:
    """Media in zilele lucratoare fata de weekend."""
    is_weekend = df.index.dayofweek >= 5
    return df.groupby(is_weekend)["value"].mean().rename({False: "lucratoare", True: "weekend"})


def complete_days(index: pd.DatetimeIndex, min_coverage: float = 0.9) -> pd.DatetimeIndex:
    """Zilele (locale) cu cel putin `min_coverage` din momentele unei zile tipice.

    Prima si ultima zi dintr-o descarcare sunt de obicei partiale; o zi care
    incepe la 18:00 nu are solar si ar trage artificial in jos orice medie
    sau pondere zilnica. Comparam cu mediana, ca zilele cu schimbare de ora
    (23 sau 25 de ore) sa nu fie eliminate.
    """
    counts = pd.Series(1, index=index.unique()).resample("D").sum()
    return counts.index[counts >= counts.median() * min_coverage]


def daily_series(df: pd.DataFrame) -> pd.Series:
    """Media zilnica — util pentru trend. Doar zilele complete."""
    daily = df["value"].resample("D").mean()
    return daily.reindex(complete_days(df.index))


# --- mixul de productie -----------------------------------------------------


def _psr_info(psr_type: str) -> tuple[str, bool]:
    return PSR_GROUPS.get(psr_type, UNKNOWN_GROUP)


def generation_by_group(gen: pd.DataFrame) -> pd.DataFrame:
    """Productia pe grupe: un rand pe moment, o coloana pe grupa (MW)."""
    frame = gen.reset_index()
    unknown = set(frame["psr_type"]) - set(PSR_GROUPS)
    if unknown:
        logger.warning("Tipuri de resursa necunoscute, puse la Altele: %s", sorted(unknown))
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
    """Ponderea regenerabilelor in productie, la fiecare moment (0..1)."""
    renew, total = _renewable_and_total(gen)
    return (renew / total).where(total > 0)


def daily_renewable_share(gen: pd.DataFrame) -> pd.Series:
    """Ponderea zilnica a regenerabilelor, ponderata cu energia.

    Nu media ponderilor de la fiecare moment: un sfert de ora de noapte cu
    productie mica ar cantari cat unul de la pranz.
    """
    renew, total = _renewable_and_total(gen)
    daily_total = total.resample("D").sum()
    share = (renew.resample("D").sum() / daily_total).where(daily_total > 0)
    return share.reindex(complete_days(total.index))


def energy_mix(gen: pd.DataFrame) -> pd.Series:
    """Ponderea fiecarei grupe in energia produsa pe toata perioada (0..1)."""
    totals = generation_by_group(gen).sum()
    return totals / totals.sum()


def price_by_renewable_share(
    price: pd.DataFrame, share: pd.Series
) -> tuple[pd.DataFrame, float]:
    """Pretul mediu pe transe de pondere a regenerabilelor + corelatia lor.

    Se folosesc doar momentele pentru care exista si pret, si productie.
    """
    joined = pd.concat({"price": price["value"], "share": share}, axis=1, join="inner").dropna()
    if joined.empty:
        return pd.DataFrame(columns=["pret_mediu", "momente"]), float("nan")

    edges = [i / 10 for i in range(11)]
    labels = [f"{int(a * 100)}-{int(b * 100)}%" for a, b in zip(edges, edges[1:])]
    buckets = pd.cut(joined["share"], edges, labels=labels, include_lowest=True)
    table = (
        joined.groupby(buckets, observed=True)["price"]
        .agg(pret_mediu="mean", momente="count")
    )
    return table, float(joined["price"].corr(joined["share"]))


def net_balance(load: pd.DataFrame, gen: pd.DataFrame) -> pd.Series:
    """Productie minus consum (MW): pozitiv = excedent, negativ = deficit.

    Este o aproximare a soldului de import/export: consumul raportat si
    productia raportata nu acopera exact aceleasi instalatii (pierderi,
    pompaj, autoconsum).
    """
    total = gen.groupby(level=0)["value"].sum()
    return (total - load["value"]).dropna()


# --- grafice ----------------------------------------------------------------


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
    logger.info("Grafic salvat: %s", path)
    return path


def plot_series(series: pd.Series, title: str, ylabel: str, filename: str) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4))
    series.plot(ax=ax, color=COLOR_MAIN, linewidth=2)
    ax.set_xlabel("")
    _style(ax, title, ylabel)
    return _save(fig, filename)


def plot_mix_profile(by_group: pd.DataFrame, filename: str = "productie_mix_orar.png") -> Path:
    """Profilul mediu pe ora din zi, stivuit pe grupe de resurse."""
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
    ax.set_xlabel("ora (ora Romaniei)")
    _style(ax, "Productie pe surse — profil mediu pe ora", "MW")
    # Legenda in ordinea straturilor de sus in jos, ca sa corespunda vizual.
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    return _save(fig, filename)


def plot_price_by_share(table: pd.DataFrame, filename: str = "pret_vs_regenerabile.png") -> Path:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(table.index.astype(str), table["pret_mediu"], color=COLOR_MAIN, width=0.6)
    ax.set_xlabel("ponderea regenerabilelor in productie")
    _style(ax, "Pretul day-ahead mediu, dupa ponderea regenerabilelor", "EUR/MWh")
    return _save(fig, filename)


def plot_balance_profile(balance: pd.Series, filename: str = "sold_productie_consum_orar.png") -> Path:
    profile = balance.groupby(balance.index.hour).mean()
    colors = [COLOR_SURPLUS if v >= 0 else COLOR_DEFICIT for v in profile]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(profile.index, profile.values, color=colors, width=0.7)
    ax.axhline(0, color=INK_MUTED, linewidth=1)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("ora (ora Romaniei)")
    _style(ax, "Productie minus consum — medie pe ora (sub zero: deficit acoperit din import)", "MW")
    return _save(fig, filename)


# --- rulare -----------------------------------------------------------------


def _report_load_and_price() -> None:
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
        print("\nProfil pe ora din zi (ora Romaniei):")
        print(hourly_profile(df).round(1).to_string())
        print("\nZile lucratoare vs weekend:")
        print(weekday_vs_weekend(df).round(1).to_string())

        plot_series(daily_series(df), f"{label} — medie zilnica", unit, f"{metric}_zilnic.png")
        plot_series(hourly_profile(df), f"{label} — profil orar", unit, f"{metric}_orar.png")


def _report_generation() -> None:
    gen = load_frame("generation_actual")
    if gen.empty:
        logger.warning("Nu exista date de productie — ruleaza intai pipeline-ul")
        return

    print("\n=== Mixul de productie ===")
    print("Ponderea in energia produsa:")
    print((energy_mix(gen) * 100).round(1).sort_values(ascending=False).to_string())

    daily_share = daily_renewable_share(gen)
    print(
        f"\nRegenerabile: {daily_share.mean() * 100:.1f}% in medie pe zi "
        f"(minim {daily_share.min() * 100:.1f}% pe {daily_share.idxmin():%d.%m}, "
        f"maxim {daily_share.max() * 100:.1f}% pe {daily_share.idxmax():%d.%m})"
    )
    plot_mix_profile(generation_by_group(gen))
    plot_series(daily_share * 100, "Ponderea regenerabilelor — pe zi", "%", "regenerabile_zilnic.png")

    price = load_frame("price_day_ahead")
    if not price.empty:
        table, corr = price_by_renewable_share(price, renewable_share(gen))
        print("\nPretul mediu dupa ponderea regenerabilelor:")
        print(table.round(1).to_string())
        print(f"Corelatie pret–pondere regenerabile: {corr:.2f}")
        plot_price_by_share(table)

    load = load_frame("load_actual")
    if not load.empty:
        balance = net_balance(load, gen)
        deficit = (balance < 0).mean() * 100
        print(
            f"\nProductie minus consum: {balance.mean():.0f} MW in medie; "
            f"deficit (import) in {deficit:.0f}% din timp"
        )
        plot_balance_profile(balance)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    _report_load_and_price()
    _report_generation()


if __name__ == "__main__":
    main()
