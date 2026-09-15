"""Normalizare si verificari de calitate a datelor.

Functiile de aici nu ating reteaua si nu ating baza de date, primesc un
DataFrame si returneaza structuri curate. Asta le face usor de testat.

Numele coloanelor de mai jos sunt cele confirmate pe python-entsoe 0.6.1
(vezi check_api.py):
    load_actual        -> timestamp, value, quantity_unit
    price_day_ahead    -> timestamp, value, currency, price_unit
    generation_actual  -> timestamp, psr_type, value, quantity_unit
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

TS_COLUMN = "timestamp"
VALUE_COLUMN = "value"

# API-ul raspunde cu codurile UN/CEFACT (MAW = megawatt, MWH = megawatt-ora).
# Le traducem in notatia uzuala, ca sa apara corect pe grafice si in rapoarte.
UNIT_ALIASES = {"MAW": "MW", "MWH": "MWh"}


def _require_column(df: pd.DataFrame, name: str, kind: str) -> None:
    if name not in df.columns:
        raise ValueError(
            f"Nu am gasit coloana de {kind} ('{name}'). "
            f"Coloane disponibile: {list(df.columns)}"
        )


def _constant(series: pd.Series) -> str | None:
    """Valoarea unica a unei coloane constante, sau None daca nu e constanta."""
    values = series.dropna().unique()
    return str(values[0]) if len(values) == 1 else None


def unit_from_frame(df: pd.DataFrame, fallback: str = "") -> str:
    """Deduce unitatea din raspunsul API, cu revenire la valoarea data.

    Preturile vin ca `currency` + `price_unit` (EUR, MWH), cantitatile ca
    `quantity_unit` (MAW). Preferam ce spune API-ul in locul unei constante
    din cod, ca sa nu stocam o unitate gresita daca se schimba raspunsul.
    """
    if "currency" in df.columns and "price_unit" in df.columns:
        currency, price_unit = _constant(df["currency"]), _constant(df["price_unit"])
        if currency and price_unit:
            return f"{currency}/{UNIT_ALIASES.get(price_unit, price_unit)}"

    if "quantity_unit" in df.columns:
        quantity_unit = _constant(df["quantity_unit"])
        if quantity_unit:
            return UNIT_ALIASES.get(quantity_unit, quantity_unit)

    return fallback


def normalize(
    df: pd.DataFrame,
    *,
    country: str,
    metric: str,
    unit: str = "",
) -> list[dict]:
    """Transforma raspunsul API intr-o lista de randuri gata de inserat.

    `unit` e doar plasa de siguranta: daca raspunsul contine unitatea, pe
    aceea o stocam.
    """
    if df is None or df.empty:
        logger.warning("DataFrame gol pentru %s/%s", country, metric)
        return []

    df = df.reset_index() if df.index.name else df.copy()

    _require_column(df, TS_COLUMN, "timp")
    _require_column(df, VALUE_COLUMN, "valoare")

    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(df[TS_COLUMN], utc=True),
            "value": pd.to_numeric(df[VALUE_COLUMN], errors="coerce"),
        }
    )
    # psr_type apare doar la productie (tipul de resursa). Atentie: pentru
    # codurile pe care python-entsoe nu le are in tabela lui de traducere
    # (ex. B25 = Energy storage) ramane codul brut, deci in aceeasi coloana
    # convietuiesc "Fossil Gas" si "B25".
    if "psr_type" in df.columns:
        out["psr_type"] = df["psr_type"].fillna("").astype(str)
    else:
        out["psr_type"] = ""

    before = len(out)
    out = out.dropna(subset=["value"])
    dropped = before - len(out)
    if dropped:
        logger.warning("%s/%s: %d valori lipsa eliminate", country, metric, dropped)

    before = len(out)
    out = expand_block_curve(out)
    if len(out) > before:
        logger.info(
            "%s/%s: %d puncte refacute din curba comprimata (A03)",
            country, metric, len(out) - before,
        )

    out["country"] = country
    out["metric"] = metric
    out["unit"] = unit_from_frame(df, unit)

    return out.to_dict(orient="records")


def expand_block_curve(out: pd.DataFrame) -> pd.DataFrame:
    """Reface pozitiile omise dintr-o curba ENTSO-E de tip A03.

    La A03 ("variable sized block") API-ul trimite un punct doar cand valoarea
    se schimba; o pozitie lipsa inseamna "aceeasi valoare ca inainte". Toate
    cele trei metrici colectate vin asa. python-entsoe ignora `curveType` si
    returneaza doar punctele primite, asa ca fara pasul acesta o noapte
    intreaga de solar ar aparea ca un singur 0, iar nuclearul ca un singur
    punct pe zi — sumele si mediile de productie ar iesi gresite.

    Fiecare serie (fiecare `psr_type`) se completeaza pana la ultimul moment
    din raspuns. Pachetul nu pastreaza sfarsitul perioadei, deci acesta e cel
    mai bun reper disponibil; o valoare completata la coada poate fi
    corectata la rularea urmatoare, care re-cere ultima zi (vezi
    pipeline.LOOKBACK) si suprascrie (vezi db.upsert_observations).
    """
    step = infer_step(pd.DatetimeIndex(out["ts"]))
    if step is None:
        return out

    end = out["ts"].max()
    parts = []
    for psr_type, series in out.groupby("psr_type", sort=False):
        series = series.drop_duplicates("ts", keep="last").set_index("ts").sort_index()
        grid = pd.date_range(series.index.min(), end, freq=step)
        series = series.reindex(grid.union(series.index)).ffill()
        series["psr_type"] = psr_type
        parts.append(series.rename_axis("ts").reset_index())
    return pd.concat(parts, ignore_index=True)


def infer_step(timestamps: pd.DatetimeIndex) -> pd.Timedelta | None:
    """Pasul de esantionare: cea mai frecventa distanta dintre doua momente.

    ENTSO-E livreaza pentru Romania la 15 minute, dar rezolutia difera de la
    o tara si de la o metrica la alta, asa ca o citim din date in loc sa o
    presupunem.
    """
    unique = pd.DatetimeIndex(timestamps.unique()).sort_values()
    if len(unique) < 3:
        return None
    diffs = pd.Series(unique).diff().dropna()
    if diffs.empty:
        return None
    return diffs.mode().iloc[0]


def quality_report(rows: list[dict], *, expected_freq=None) -> dict:
    """Verificari simple de calitate: goluri in serie, valori negative, extreme.

    `expected_freq` forteaza pasul asteptat; implicit e dedus din date.
    """
    empty = {"n_rows": 0, "gaps": 0, "negatives": 0, "outliers": 0, "step": None}
    if not rows:
        return empty

    df = pd.DataFrame(rows).sort_values("ts")
    ts = pd.DatetimeIndex(pd.to_datetime(df["ts"], utc=True))
    unique_ts = pd.DatetimeIndex(ts.unique()).sort_values()

    step = pd.tseries.frequencies.to_offset(expected_freq) if expected_freq else infer_step(ts)
    if step is None:
        gaps = 0
    else:
        expected = pd.date_range(unique_ts.min(), unique_ts.max(), freq=step)
        gaps = len(expected.difference(unique_ts))

    values = df["value"]
    negatives = int((values < 0).sum())

    report = {
        "n_rows": len(df),
        "gaps": gaps,
        "negatives": negatives,
        "outliers": _count_outliers(df),
        "step": str(step) if step is not None else None,
    }
    logger.info("Raport calitate: %s", report)
    return report


def _count_outliers(df: pd.DataFrame) -> int:
    """Outlieri prin IQR — robust la distributii asimetrice, cum sunt preturile.

    La productie comparam fiecare tip de resursa cu el insusi: solarul si
    gazul au ordine de marime diferite, iar amestecate ar da un prag fara sens.
    """
    if "psr_type" in df.columns and df["psr_type"].nunique() > 1:
        return int(sum(_count_outliers(g) for _, g in df.groupby("psr_type")))

    values = df["value"]
    if len(values) < 4:
        return 0
    q1, q3 = values.quantile([0.25, 0.75])
    iqr = q3 - q1
    return int(((values < q1 - 3 * iqr) | (values > q3 + 3 * iqr)).sum())
